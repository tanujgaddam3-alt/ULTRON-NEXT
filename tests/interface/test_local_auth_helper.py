import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import socket
import unicodedata
from types import SimpleNamespace

import pytest
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ultron.application.content.store import ContentStore
from ultron.application.runtime import interface_gateway
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.application.runtime.local_auth_helper import (
    ENDPOINT_PATH,
    FRONTEND_ORIGIN,
    LocalAuthHelper,
    MAX_AUTH_FAILURES,
    MAX_GATEWAY_MESSAGE_SIZE,
    MAX_MESSAGE_SIZE,
    PersistentAttemptThrottle,
    PasswordVerifier,
    read_backend_token,
    write_password_verifier,
)
from ultron.core.event_bus import EventBus
from ultron.security.identity.authenticator import WebSocketAuthenticator


def write_private(path, value):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as file:
        file.write(value)


async def receive_optional_gateway_message(websocket):
    try:
        return await websocket.recv()
    except ConnectionClosed:
        return None


def test_password_verifier_is_salted_private_and_checks_password(tmp_path):
    password = secrets.token_urlsafe(24)
    verifier_path = tmp_path / "unlock.json"

    write_password_verifier(password, verifier_path)

    verifier = PasswordVerifier(verifier_path)
    assert verifier.verify(password)
    assert not verifier.verify(secrets.token_urlsafe(24))
    assert verifier_path.stat().st_mode & 0o777 == 0o600
    assert password not in verifier_path.read_text(encoding="ascii")
    with pytest.raises(FileExistsError):
        write_password_verifier(password, verifier_path)


def test_password_setup_rejects_short_passphrases(tmp_path):
    with pytest.raises(ValueError, match="at least"):
        write_password_verifier("short", tmp_path / "unlock.json")


def test_new_verifier_accepts_canonically_equivalent_unicode_passphrases(tmp_path):
    verifier_path = tmp_path / "unlock.json"
    composed = "Caf\u00e9 jardin d'hiver avec le chat"
    decomposed = unicodedata.normalize("NFD", composed)

    write_password_verifier(composed, verifier_path)

    assert PasswordVerifier(verifier_path).verify(decomposed)


def test_legacy_v1_verifier_accepts_raw_and_canonical_unicode_forms(tmp_path):
    verifier_path = tmp_path / "legacy-unlock.json"
    legacy_password = "Cafe\u0301 jardin d'hiver avec le chat"
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        legacy_password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    verifier_path.write_text(json.dumps({
        "version": 1,
        "algorithm": "scrypt",
        "salt": salt.hex(),
        "digest": digest.hex(),
    }), encoding="ascii")
    os.chmod(verifier_path, 0o600)

    verifier = PasswordVerifier(verifier_path)
    assert verifier.verify(legacy_password)
    assert verifier.verify(unicodedata.normalize("NFC", legacy_password))


def test_throttle_persists_lockout_across_reconnections_and_restart(tmp_path):
    throttle_path = tmp_path / "throttle.db"
    throttle = PersistentAttemptThrottle(throttle_path)

    for _ in range(MAX_AUTH_FAILURES - 1):
        assert throttle.record_failure() == 0
    assert throttle.retry_after() == 0
    assert throttle.record_failure() > 0
    assert throttle.retry_after() > 0

    restarted_throttle = PersistentAttemptThrottle(throttle_path)
    assert restarted_throttle.retry_after() > 0
    restarted_throttle.reset()
    assert restarted_throttle.retry_after() == 0


def make_helper(tmp_path, gateway_url):
    token = secrets.token_urlsafe(32)
    password = secrets.token_urlsafe(24)
    token_path = tmp_path / "interface-token"
    verifier_path = tmp_path / "interface-unlock.json"
    throttle_path = tmp_path / "throttle.db"
    write_private(token_path, token + "\n")
    write_password_verifier(password, verifier_path)
    helper = LocalAuthHelper(
        token_path=token_path,
        verifier_path=verifier_path,
        throttle_path=throttle_path,
        gateway_url=gateway_url,
    )
    return helper, token, password, token_path, verifier_path, throttle_path


async def start_helper(helper, callback):
    async with websockets.serve(
        helper.handler,
        "127.0.0.1",
        0,
        origins=[FRONTEND_ORIGIN],
        max_size=MAX_MESSAGE_SIZE,
    ) as server:
        port = server.sockets[0].getsockname()[1]
        await callback(f"ws://127.0.0.1:{port}{ENDPOINT_PATH}")


def test_local_password_authenticates_upstream_without_exposing_backend_token(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        upstream_channels = []
        gateway_sessions = 0

        async def gateway_handler(websocket):
            nonlocal gateway_sessions
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            raw_authentication = await receive_optional_gateway_message(websocket)
            if raw_authentication is None:
                return
            gateway_sessions += 1
            authentication = json.loads(raw_authentication)
            assert authentication["action"] == "AUTHENTICATE"
            assert authentication["credential"] == token
            assert authentication["credential"] != password
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "success": True,
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": f"gateway-session-{gateway_sessions}",
                "expiresAt": "later",
            }))
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("action") == "SUBSCRIBE":
                    upstream_channels.extend(message["channels"])
                    await websocket.send(json.dumps({
                        "action": "SUBSCRIBE_ACK",
                        "channels": message["channels"],
                    }))
                    for channel in message["channels"]:
                        await websocket.send(json.dumps({
                            "channel": channel,
                            "payload": {"available": True},
                        }))
                elif message.get("action") == "PING":
                    await websocket.send(json.dumps({"type": "PONG"}))

        async with websockets.serve(gateway_handler, "127.0.0.1", 0) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            throttle_path = tmp_path / "throttle.db"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=throttle_path,
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    proxy=None,
                ) as browser:
                    greeting = json.loads(await browser.recv())
                    assert greeting["authenticationRequired"] is True
                    assert greeting["runtimeId"] == "ultron-default"
                    assert token not in json.dumps(greeting)
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "browser-auth",
                        "credential": password,
                    }))
                    authenticated = json.loads(await browser.recv())
                    assert authenticated["status"] == "AUTHENTICATED"
                    assert authenticated["sessionId"] == "gateway-session-1"
                    assert authenticated["runtimeId"] == "ultron-default"
                    assert token not in json.dumps(authenticated)

                    channels = ["trading", "omega"]
                    await browser.send(json.dumps({
                        "action": "SUBSCRIBE",
                        "channels": channels,
                    }))
                    acknowledgement = json.loads(await browser.recv())
                    snapshots = [
                        json.loads(await browser.recv()),
                        json.loads(await browser.recv()),
                    ]
                    assert acknowledgement["action"] == "SUBSCRIBE_ACK"
                    assert {frame["channel"] for frame in snapshots} == set(channels)
                    assert upstream_channels == channels
                    assert all(token not in json.dumps(frame) for frame in snapshots)
                    await browser.send(json.dumps({"action": "PING"}))
                    assert json.loads(await browser.recv())["type"] == "PONG"

            await start_helper(helper, browser_flow)

            async def reconnect_flow(url):
                async with websockets.connect(url, origin=FRONTEND_ORIGIN, proxy=None) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "browser-reconnect",
                        "credential": password,
                    }))
                    authenticated = json.loads(await browser.recv())
                    assert authenticated["status"] == "AUTHENTICATED"
                    assert authenticated["sessionId"] == "gateway-session-2"

            await start_helper(helper, reconnect_flow)
            assert gateway_sessions == 2

    asyncio.run(run())


def test_gateway_response_secret_rejection_logs_only_safe_action_and_path(
    tmp_path,
    caplog,
):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        gateway_connections = 0

        async def gateway_handler(websocket):
            nonlocal gateway_connections
            gateway_connections += 1
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            raw_authentication = await receive_optional_gateway_message(websocket)
            if raw_authentication is None:
                return
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "success": True,
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": "secret-guard-session",
            }))
            request = json.loads(await websocket.recv())
            assert request["action"] == "SUBSCRIBE"
            await websocket.send(json.dumps({
                "action": "PRIVATE_DIAGNOSTIC_RESULT",
                "payload": {"private": {"field": f"prefix-{token}-suffix"}},
            }))
            await websocket.wait_closed()

        async with websockets.serve(
            gateway_handler, "127.0.0.1", 0
        ) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    proxy=None,
                ) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "secret-guard-auth",
                        "credential": password,
                    }))
                    assert json.loads(await browser.recv())["status"] == "AUTHENTICATED"
                    await browser.send(json.dumps({
                        "action": "SUBSCRIBE",
                        "channels": ["runtimeEvents"],
                    }))
                    with pytest.raises(ConnectionClosed) as closed:
                        await browser.recv()
                    assert closed.value.rcvd.code == 1008
                    assert "credential" not in closed.value.rcvd.reason.lower()
                    assert gateway_connections == 2

            await start_helper(helper, browser_flow)
        return token

    with caplog.at_level("WARNING"):
        token = asyncio.run(run())
    assert "PRIVATE_DIAGNOSTIC_RESULT" in caplog.text
    assert "$.payload.private.field" in caplog.text
    assert token not in caplog.text


def test_gateway_authentication_expiry_close_is_propagated_to_browser(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)

        async def gateway_handler(websocket):
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            raw_authentication = await receive_optional_gateway_message(websocket)
            if raw_authentication is None:
                return
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "success": True,
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": "expiring-session",
            }))
            await websocket.recv()
            await websocket.send(json.dumps({
                "action": "SUBSCRIBE_ACK",
                "channels": ["runtimeEvents"],
            }))
            await websocket.close(code=1008, reason="Authentication expired.")

        async with websockets.serve(
            gateway_handler, "127.0.0.1", 0
        ) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    proxy=None,
                ) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "expiry-auth",
                        "credential": password,
                    }))
                    assert json.loads(await browser.recv())["status"] == "AUTHENTICATED"
                    await browser.send(json.dumps({
                        "action": "SUBSCRIBE",
                        "channels": ["runtimeEvents"],
                    }))
                    assert json.loads(await browser.recv())["action"] == "SUBSCRIBE_ACK"
                    with pytest.raises(ConnectionClosed) as closed:
                        await browser.recv()
                    assert closed.value.rcvd.code == 1008
                    assert closed.value.rcvd.reason == "Authentication expired."

            await start_helper(helper, browser_flow)

    asyncio.run(run())


@pytest.mark.parametrize("runtime_id", [None, "../candidate"])
def test_local_helper_refuses_to_authenticate_without_a_valid_gateway_identity(
    tmp_path,
    runtime_id,
):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        gateway_connections = 0

        async def gateway_handler(websocket):
            nonlocal gateway_connections
            gateway_connections += 1
            greeting = {
                "type": "connected",
                "authenticationRequired": True,
            }
            if runtime_id is not None:
                greeting["runtimeId"] = runtime_id
            await websocket.send(json.dumps(greeting))
            await websocket.wait_closed()

        async with websockets.serve(
            gateway_handler, "127.0.0.1", 0
        ) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    proxy=None,
                ) as browser:
                    greeting = json.loads(await browser.recv())
                    assert greeting["type"] == "connected"
                    assert "runtimeId" not in greeting
                    with pytest.raises(ConnectionClosed):
                        await browser.recv()
                    assert gateway_connections == 1

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_local_helper_binds_authentication_to_the_preflight_gateway_identity(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        gateway_connections = 0

        async def gateway_handler(websocket):
            nonlocal gateway_connections
            gateway_connections += 1
            runtime_id = (
                "ultron-default"
                if gateway_connections == 1
                else "candidate-v10"
            )
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": runtime_id,
            }))
            raw = await receive_optional_gateway_message(websocket)
            if raw is not None:
                raise AssertionError(
                    "The helper must not send its backend token to a changed runtime."
                )

        async with websockets.serve(
            gateway_handler, "127.0.0.1", 0
        ) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    proxy=None,
                ) as browser:
                    greeting = json.loads(await browser.recv())
                    assert greeting["runtimeId"] == "ultron-default"
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "changed-runtime",
                        "credential": password,
                    }))
                    result = json.loads(await browser.recv())
                    assert result["status"] == "RUNTIME_IDENTITY_MISMATCH"
                    assert "runtimeId" not in result
                    assert token not in json.dumps(result)
                    assert gateway_connections == 2

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_local_login_authenticates_through_real_gateway_and_relogin_gets_new_session(
    tmp_path,
    monkeypatch,
):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        operation_database = tmp_path / "operations.db"
        content_database = tmp_path / "content.db"
        content_store = ContentStore(content_database)
        content_store.migrate_publishing_attempt_schema()
        content_store.migrate_private_render_operation_schema()
        content_store.close()

        monkeypatch.setenv(WebSocketAuthenticator.TOKEN_ENV, token)
        monkeypatch.setenv("ULTRON_OPERATION_DB", str(operation_database))
        monkeypatch.setenv("ULTRON_CRE_DATABASE", str(content_database))

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            gateway_port = listener.getsockname()[1]

        container = SimpleNamespace(
            event_bus=EventBus(),
            runtime=SimpleNamespace(
                state=SimpleNamespace(cycle_count=0, running=True),
            ),
            executor=SimpleNamespace(),
            skills=SimpleNamespace(),
            execution_audit_store=SQLiteOperationStore(operation_database),
        )
        gateway_task = asyncio.create_task(
            interface_gateway.serve_interface(
                container,
                host="127.0.0.1",
                port=gateway_port,
            )
        )
        gateway_url = f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}"
        gateway_ready = False
        deadline = asyncio.get_running_loop().time() + 5
        try:
            while not gateway_ready:
                if gateway_task.done():
                    await gateway_task
                    raise AssertionError("Gateway exited before becoming ready.")
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("Gateway did not become ready.")
                try:
                    async with websockets.connect(gateway_url, proxy=None) as probe:
                        greeting = json.loads(await asyncio.wait_for(probe.recv(), timeout=1))
                        assert greeting["authenticationRequired"] is True
                        assert greeting["runtimeId"] == "ultron-default"
                        gateway_ready = True
                except (OSError, TimeoutError):
                    await asyncio.sleep(0.05)

            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=gateway_url,
            )

            session_ids = []

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    proxy=None,
                ) as browser:
                    greeting = json.loads(await browser.recv())
                    assert greeting["authenticationRequired"] is True
                    assert greeting["authMethod"] == "local-unlock"
                    assert greeting["runtimeId"] == "ultron-default"
                    assert token not in json.dumps(greeting)

                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "isolated-login",
                        "credential": password,
                    }))
                    authenticated = json.loads(
                        await asyncio.wait_for(browser.recv(), timeout=2)
                    )
                    assert authenticated["success"] is True
                    assert authenticated["status"] == "AUTHENTICATED"
                    assert authenticated["correlationId"] == "isolated-login"
                    assert authenticated["principal"] == "local-operator"
                    assert authenticated["runtimeId"] == "ultron-default"
                    assert isinstance(authenticated["sessionId"], str)
                    assert isinstance(authenticated["expiresAt"], str)
                    assert token not in json.dumps(authenticated)
                    assert password not in json.dumps(authenticated)
                    session_ids.append(authenticated["sessionId"])

                    await browser.send(json.dumps({
                        "action": "SUBSCRIBE",
                        "correlationId": "isolated-subscribe",
                        "channels": [],
                    }))
                    acknowledgement = json.loads(
                        await asyncio.wait_for(browser.recv(), timeout=2)
                    )
                    assert acknowledgement["action"] == "SUBSCRIBE_ACK"
                    assert acknowledgement["channels"] == []

            await start_helper(helper, browser_flow)
            await start_helper(helper, browser_flow)
            assert len(session_ids) == 2
            assert session_ids[0] != session_ids[1]
        finally:
            gateway_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await gateway_task

    asyncio.run(run())


def test_helper_relays_bounded_scene_preview_response_larger_than_request_limit(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        preview_payload = "x" * (600 * 1024)

        async def gateway_handler(websocket):
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            raw_authentication = await receive_optional_gateway_message(websocket)
            if raw_authentication is None:
                return
            authentication = json.loads(raw_authentication)
            assert authentication["credential"] == token
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": "large-preview-session",
                "expiresAt": "later",
            }))
            request = json.loads(await websocket.recv())
            assert request["action"] == "CRE_SCRIPT_PREVIEW_REQUEST"
            await websocket.send(json.dumps({
                "action": "CRE_SCRIPT_PREVIEW_RESULT",
                "success": True,
                "payload": {"boundedPreview": preview_payload},
            }))

        async with websockets.serve(gateway_handler, "127.0.0.1", 0) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    proxy=None,
                ) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "large-preview-auth",
                        "credential": password,
                    }))
                    assert json.loads(await browser.recv())["status"] == "AUTHENTICATED"
                    await browser.send(json.dumps({
                        "action": "CRE_SCRIPT_PREVIEW_REQUEST",
                        "correlationId": "large-preview-request",
                        "payload": {"script": "synthetic", "references": []},
                    }))
                    response = json.loads(await asyncio.wait_for(browser.recv(), timeout=5))
                    assert response["action"] == "CRE_SCRIPT_PREVIEW_RESULT"
                    assert response["payload"]["boundedPreview"] == preview_payload

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_helper_relays_large_authenticated_video_upload_and_registration_response(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        source_bytes = b"synthetic-video-data" * 80_000
        source_hash = hashlib.sha256(source_bytes).hexdigest()

        async def gateway_handler(websocket):
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            raw_authentication = await receive_optional_gateway_message(websocket)
            if raw_authentication is None:
                return
            authentication = json.loads(raw_authentication)
            assert authentication["credential"] == token
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": "large-upload-session",
                "expiresAt": "later",
            }))
            request = json.loads(await websocket.recv())
            assert request["action"] == "CRE_SOURCE_VIDEO_REQUEST"
            assert request["correlationId"] == "large-upload-request"
            assert base64.b64decode(
                request["payload"]["videoBase64"],
                validate=True,
            ) == source_bytes
            assert request["payload"]["acquisitionRequestId"] == "selected-request-1"
            assert request["payload"]["targetContext"] == "youtube_shorts"
            await websocket.send(json.dumps({
                "action": "CRE_SOURCE_VIDEO_RESULT",
                "correlationId": request["correlationId"],
                "success": True,
                "status": "SOURCE_REGISTERED",
                "payload": {
                    "asset": {
                        "assetId": "registered-source",
                        "sha256": source_hash,
                    },
                },
            }))

        async with websockets.serve(
            gateway_handler,
            "127.0.0.1",
            0,
            max_size=MAX_GATEWAY_MESSAGE_SIZE,
        ) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    max_size=MAX_MESSAGE_SIZE,
                    proxy=None,
                ) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "large-upload-auth",
                        "credential": password,
                    }))
                    assert json.loads(await browser.recv())["status"] == "AUTHENTICATED"
                    await browser.send(json.dumps({
                        "action": "CRE_SOURCE_VIDEO_REQUEST",
                        "correlationId": "large-upload-request",
                        "payload": {
                            "videoBase64": base64.b64encode(source_bytes).decode("ascii"),
                            "fileName": "source.mp4",
                            "mediaType": "video/mp4",
                            "targetContext": "youtube_shorts",
                            "accountId": "local-channel",
                            "territory": "US",
                            "acquisitionRequestId": "selected-request-1",
                        },
                    }))
                    response = json.loads(
                        await asyncio.wait_for(browser.recv(), timeout=5)
                    )
                    assert response["action"] == "CRE_SOURCE_VIDEO_RESULT"
                    assert response["correlationId"] == "large-upload-request"
                    assert response["status"] == "SOURCE_REGISTERED"
                    assert response["payload"]["asset"]["sha256"] == source_hash

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_helper_relays_maximum_private_render_artifact_with_bounded_envelope(
    tmp_path,
):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        artifact = b"\x5a" * (16 * 1024 * 1024)
        artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        source_id = "s" * 200
        response = {
            "action": "CRE_PRIVATE_PROJECT_RENDER_RESULT",
            "correlationId": "maximum-private-render",
            "success": True,
            "status": "PRIVATE_PROJECT_RENDER_READY",
            "error": None,
            "payload": {
                "renderId": "r" * 36,
                "projectId": "p" * 200,
                "artifactId": "clip-" + "r" * 36,
                "sha256": artifact_sha256,
                "sizeBytes": len(artifact),
                "durationSeconds": 60.0,
                "width": 1280,
                "height": 720,
                "mimeType": "video/mp4",
                "sourceAssetIds": [source_id] * 12,
                "privateReviewOnly": True,
                "planRevision": 1,
                "planSha256": "a" * 64,
                "planMode": "structured",
                "planVersion": "cre-creative-plan-v2",
                "orientation": "landscape",
                "rendererSettings": {
                    "videoCodec": "h264",
                    "frameRateFps": 30,
                    "maxWidth": 1280,
                    "maxHeight": 1280,
                    "audioPolicy": "explicit_segment_audio",
                    "transitions": "hard_cut",
                    "audioSampleRateHz": 48000,
                    "peakProtectionPolicy": "sample_peak_limiter_minus_1_5_dbfs",
                },
                "audioTimeline": [
                    {
                        "timelineOrder": index,
                        "mode": "source_audio",
                        "sourceAssetId": source_id,
                        "sourceInSeconds": 0.0,
                        "sourceOutSeconds": 0.6,
                        "gainDb": 0.0,
                        "timingPolicy": "trim_pad_to_video_segment",
                    }
                    for index in range(1, 101)
                ],
                "audioValidation": {
                    "outputPresent": True,
                    "sampleRateHz": 48000,
                    "channels": 2,
                    "durationSeconds": 60.0,
                    "decodedPeakDbfs": -1.5,
                    "peakLimitDbfs": -1.5,
                    "peakValidationToleranceDb": 0.5,
                    "status": "PASSED",
                },
                "base64": base64.b64encode(artifact).decode("ascii"),
            },
            "provenance": "LIVE",
        }
        serialized_response = json.dumps(response, separators=(",", ":"))
        serialized_size = len(serialized_response.encode("utf-8"))
        assert len(response["payload"]["base64"]) == 22_369_624
        assert serialized_size <= MAX_GATEWAY_MESSAGE_SIZE
        assert MAX_GATEWAY_MESSAGE_SIZE == 24 * 1024 * 1024
        assert MAX_GATEWAY_MESSAGE_SIZE < MAX_MESSAGE_SIZE

        async def gateway_handler(websocket):
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            raw_authentication = await receive_optional_gateway_message(websocket)
            if raw_authentication is None:
                return
            authentication = json.loads(raw_authentication)
            assert authentication["credential"] == token
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "success": True,
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": "maximum-render-session",
                "expiresAt": "later",
            }))
            if await receive_optional_gateway_message(websocket) is None:
                return
            await websocket.send(serialized_response)

        async with websockets.serve(
            gateway_handler,
            "127.0.0.1",
            0,
            max_size=MAX_GATEWAY_MESSAGE_SIZE,
        ) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    max_size=MAX_MESSAGE_SIZE,
                    proxy=None,
                ) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "maximum-render-auth",
                        "credential": password,
                    }))
                    assert json.loads(await browser.recv())["status"] == "AUTHENTICATED"
                    await browser.send(json.dumps({
                        "action": "CRE_PRIVATE_PROJECT_RENDER_REQUEST",
                        "correlationId": "maximum-private-render",
                        "payload": {"projectId": "p" * 200, "vertical": False},
                    }))
                    received = json.loads(
                        await asyncio.wait_for(browser.recv(), timeout=20)
                    )
                    assert received["success"] is True
                    assert received["status"] == "PRIVATE_PROJECT_RENDER_READY"
                    encoded = received["payload"]["base64"]
                    decoded = base64.b64decode(encoded, validate=True)
                    assert len(decoded) == 16 * 1024 * 1024
                    assert hashlib.sha256(decoded).hexdigest() == artifact_sha256
                    assert len(encoded) == 22_369_624

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_helper_rejects_gateway_response_over_configured_receive_limit(
    tmp_path,
    monkeypatch,
):
    import ultron.application.runtime.local_auth_helper as helper_module

    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        receive_limit = 1024
        monkeypatch.setattr(
            helper_module,
            "MAX_GATEWAY_MESSAGE_SIZE",
            receive_limit,
        )

        async def gateway_handler(websocket):
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            if await receive_optional_gateway_message(websocket) is None:
                return
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "success": True,
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": "over-limit-session",
                "expiresAt": "later",
            }))
            if await receive_optional_gateway_message(websocket) is None:
                return
            await websocket.send(json.dumps({
                "action": "CRE_PRIVATE_PROJECT_RENDER_RESULT",
                "correlationId": "over-limit-render",
                "success": True,
                "status": "PRIVATE_PROJECT_RENDER_READY",
                "payload": {"base64": "x" * receive_limit},
            }))

        async with websockets.serve(
            gateway_handler,
            "127.0.0.1",
            0,
            max_size=MAX_MESSAGE_SIZE,
        ) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    max_size=MAX_MESSAGE_SIZE,
                    proxy=None,
                ) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "over-limit-auth",
                        "credential": password,
                    }))
                    assert json.loads(await browser.recv())["status"] == "AUTHENTICATED"
                    await browser.send(json.dumps({
                        "action": "CRE_PRIVATE_PROJECT_RENDER_REQUEST",
                        "correlationId": "over-limit-render",
                        "payload": {"projectId": "project", "vertical": False},
                    }))
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(browser.recv(), timeout=5)

            await start_helper(helper, browser_flow)

    asyncio.run(run())


@pytest.mark.parametrize(
    "origin",
    ["http://attacker.invalid", "null", None],
    ids=["untrusted-http-origin", "file-origin", "missing-origin"],
)
def test_helper_rejects_untrusted_origin_before_gateway_connection(tmp_path, origin):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        gateway_connections = 0

        async def gateway_handler(websocket):
            nonlocal gateway_connections
            gateway_connections += 1
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            await websocket.wait_closed()

        async with websockets.serve(gateway_handler, "127.0.0.1", 0) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            throttle_path = tmp_path / "throttle.db"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=throttle_path,
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                with pytest.raises(InvalidStatus):
                    await websockets.connect(
                        url,
                        origin=origin,
                        proxy=None,
                    )
                assert gateway_connections == 0

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_protected_channel_request_is_not_forwarded_before_local_authentication(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        gateway_connections = 0

        async def gateway_handler(websocket):
            nonlocal gateway_connections
            gateway_connections += 1
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            await websocket.wait_closed()

        async with websockets.serve(gateway_handler, "127.0.0.1", 0) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(url, origin=FRONTEND_ORIGIN, proxy=None) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "SUBSCRIBE",
                        "channels": ["trading", "omega"],
                    }))
                    result = json.loads(await browser.recv())
                    assert result["status"] == "AUTHENTICATION_FAILED"
                assert gateway_connections == 1

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_gateway_session_expiry_closes_local_proxy_session(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)

        async def gateway_handler(websocket):
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            if await receive_optional_gateway_message(websocket) is None:
                return
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": "expiring-session",
                "expiresAt": "soon",
            }))
            await asyncio.sleep(0.05)
            await websocket.close(code=1008, reason="Authentication expired.")

        async with websockets.serve(gateway_handler, "127.0.0.1", 0) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(url, origin=FRONTEND_ORIGIN, proxy=None) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "session-expiry",
                        "credential": password,
                    }))
                    assert json.loads(await browser.recv())["status"] == "AUTHENTICATED"
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(browser.recv(), timeout=1)
                    assert browser.close_code == 1008
                    assert browser.close_reason == "Authentication expired."

            await start_helper(helper, browser_flow)

    asyncio.run(run())

def test_client_disconnect_closes_upstream_gateway_connection(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        connections = 0
        upstream_closed = asyncio.Event()

        async def gateway_handler(websocket):
            nonlocal connections
            connections += 1
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            if connections == 1:
                await websocket.wait_closed()
                return
            await websocket.recv()
            await websocket.send(json.dumps({
                "action": "AUTHENTICATION_RESULT",
                "success": True,
                "status": "AUTHENTICATED",
                "principal": "local-operator",
                "sessionId": "disconnect-session",
            }))
            await websocket.recv()
            await websocket.send(json.dumps({
                "action": "SUBSCRIBE_ACK",
                "channels": ["runtimeEvents"],
            }))
            await websocket.wait_closed()
            upstream_closed.set()

        async with websockets.serve(
            gateway_handler, "127.0.0.1", 0
        ) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(
                    url,
                    origin=FRONTEND_ORIGIN,
                    proxy=None,
                ) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "disconnect-auth",
                        "credential": password,
                    }))
                    assert json.loads(await browser.recv())["status"] == "AUTHENTICATED"
                    await browser.send(json.dumps({
                        "action": "SUBSCRIBE",
                        "channels": ["runtimeEvents"],
                    }))
                    assert json.loads(await browser.recv())["action"] == "SUBSCRIBE_ACK"
                await asyncio.wait_for(upstream_closed.wait(), timeout=1)

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_gateway_unavailable_does_not_claim_a_runtime_identity(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        token_path = tmp_path / "interface-token"
        verifier_path = tmp_path / "interface-unlock.json"
        write_private(token_path, token + "\n")
        write_password_verifier(password, verifier_path)

        reserved = await asyncio.get_running_loop().create_server(
            lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader()),
            "127.0.0.1",
            0,
        )
        gateway_port = reserved.sockets[0].getsockname()[1]
        reserved.close()
        await reserved.wait_closed()
        helper = LocalAuthHelper(
            token_path=token_path,
            verifier_path=verifier_path,
            throttle_path=tmp_path / "throttle.db",
            gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
        )

        async def browser_flow(url):
            async with websockets.connect(url, origin=FRONTEND_ORIGIN, proxy=None) as browser:
                with pytest.raises(ConnectionClosed):
                    await browser.recv()
                assert browser.close_code == 1013

        await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_password_guesses_are_throttled_across_reconnects_and_helper_restart(tmp_path):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        gateway_connections = 0

        async def gateway_handler(websocket):
            nonlocal gateway_connections
            gateway_connections += 1
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            await websocket.wait_closed()

        async with websockets.serve(gateway_handler, "127.0.0.1", 0) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            token_path = tmp_path / "interface-token"
            verifier_path = tmp_path / "interface-unlock.json"
            throttle_path = tmp_path / "throttle.db"
            write_private(token_path, token + "\n")
            write_password_verifier(password, verifier_path)
            gateway_url = f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}"
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=throttle_path,
                gateway_url=gateway_url,
            )

            async def failed_attempt(
                url,
                supplied_password,
                expected_status="AUTHENTICATION_FAILED",
            ):
                async with websockets.connect(url, origin=FRONTEND_ORIGIN, proxy=None) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "guess",
                        "credential": supplied_password,
                    }))
                    result = json.loads(await browser.recv())
                    assert result["status"] == expected_status
                    if expected_status == "AUTHENTICATION_LOCKED":
                        assert result["retryAfterSeconds"] > 0

            async def first_run(url):
                for attempt in range(MAX_AUTH_FAILURES):
                    await failed_attempt(
                        url,
                        secrets.token_urlsafe(24),
                        "AUTHENTICATION_LOCKED"
                        if attempt == MAX_AUTH_FAILURES - 1
                        else "AUTHENTICATION_FAILED",
                    )
                assert gateway_connections == MAX_AUTH_FAILURES

            await start_helper(helper, first_run)

            restarted_helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=throttle_path,
                gateway_url=gateway_url,
            )

            async def after_restart(url):
                await failed_attempt(
                    url,
                    password,
                    "AUTHENTICATION_LOCKED",
                )
                assert gateway_connections == MAX_AUTH_FAILURES + 1

            await start_helper(restarted_helper, after_restart)
            assert PersistentAttemptThrottle(throttle_path).retry_after() > 0

    asyncio.run(run())


@pytest.mark.parametrize(
    ("gateway_auth_response", "expected_status"),
    [
        ({"action": "unexpected", "status": "AUTHENTICATED"}, "AUTHENTICATION_PROTOCOL_ERROR"),
        ({"action": "AUTHENTICATION_RESULT", "success": False, "status": "AUTHENTICATION_FAILED"}, "AUTHENTICATION_UPSTREAM_REJECTED"),
    ],
)
def test_helper_distinguishes_gateway_protocol_and_upstream_rejection(
    tmp_path,
    gateway_auth_response,
    expected_status,
):
    async def run():
        token = secrets.token_urlsafe(32)
        password = secrets.token_urlsafe(24)
        token_path = tmp_path / "interface-token"
        verifier_path = tmp_path / "interface-unlock.json"
        write_private(token_path, token + "\n")
        write_password_verifier(password, verifier_path)

        async def gateway_handler(websocket):
            await websocket.send(json.dumps({
                "type": "connected",
                "authenticationRequired": True,
                "runtimeId": "ultron-default",
            }))
            if await receive_optional_gateway_message(websocket) is None:
                return
            await websocket.send(json.dumps(gateway_auth_response))
            await websocket.wait_closed()

        async with websockets.serve(gateway_handler, "127.0.0.1", 0) as gateway:
            gateway_port = gateway.sockets[0].getsockname()[1]
            helper = LocalAuthHelper(
                token_path=token_path,
                verifier_path=verifier_path,
                throttle_path=tmp_path / "throttle.db",
                gateway_url=f"ws://127.0.0.1:{gateway_port}{ENDPOINT_PATH}",
            )

            async def browser_flow(url):
                async with websockets.connect(url, origin=FRONTEND_ORIGIN, proxy=None) as browser:
                    await browser.recv()
                    await browser.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "gateway-response-classification",
                        "credential": password,
                    }))
                    result = json.loads(await browser.recv())
                    assert result["status"] == expected_status

            await start_helper(helper, browser_flow)

    asyncio.run(run())


def test_backend_token_reader_requires_owner_only_strong_token(tmp_path):
    token_path = tmp_path / "token"
    write_private(token_path, secrets.token_urlsafe(32) + "\n")
    assert len(read_backend_token(token_path)) >= 43
    os.chmod(token_path, 0o644)
    with pytest.raises(PermissionError):
        read_backend_token(token_path)


def test_helper_rejects_non_loopback_binding(tmp_path):
    password = secrets.token_urlsafe(24)
    token_path = tmp_path / "token"
    verifier_path = tmp_path / "unlock.json"
    write_private(token_path, secrets.token_urlsafe(32) + "\n")
    write_password_verifier(password, verifier_path)
    helper = LocalAuthHelper(
        token_path=token_path,
        verifier_path=verifier_path,
        throttle_path=tmp_path / "throttle.db",
    )
    with pytest.raises(ValueError, match="loopback"):
        asyncio.run(helper.serve(host="0.0.0.0", port=0))