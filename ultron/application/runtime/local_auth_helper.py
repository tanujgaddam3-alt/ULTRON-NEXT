from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import stat
import time
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

ENDPOINT_PATH = "/ultron/stream"
FRONTEND_ORIGIN = "http://127.0.0.1:8000"
HELPER_HOST = "127.0.0.1"
HELPER_PORT = 8081
GATEWAY_URL = "ws://127.0.0.1:8080/ultron/stream"
CONFIG_DIRECTORY = Path.home() / ".config" / "ultron"
TOKEN_PATH = CONFIG_DIRECTORY / "interface-token"
VERIFIER_PATH = CONFIG_DIRECTORY / "interface-unlock.json"
THROTTLE_PATH = CONFIG_DIRECTORY / "interface-unlock-throttle.db"
MIN_PASSWORD_LENGTH = 20
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
MAX_AUTH_FAILURES = 5
BASE_LOCKOUT_SECONDS = 30
MAX_LOCKOUT_SECONDS = 900
# Covers the base64 envelope for the 32 MiB source limit, with a bounded cap.
MAX_MESSAGE_SIZE = 48 * 1024 * 1024
# A 16 MiB artifact encodes to 22,369,624 Base64 bytes. The 24 MiB bound
# leaves 2,796,200 bytes for the bounded render JSON envelope and headroom.
# The envelope is limited by 12 sources and 100 audio decisions with IDs
# bounded to 200 characters; this remains below the 48 MiB gateway/browser cap.
MAX_GATEWAY_MESSAGE_SIZE = 24 * 1024 * 1024
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,}$")


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise PermissionError("ULTRON auth directory must be owned by this user and mode 700.")


def _ensure_private_file(path: Path, *, create: bool) -> None:
    if create:
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise PermissionError("ULTRON auth files must be regular, user-owned, and mode 600.")


def _derive_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )


def _normalize_password(password: str) -> str:
    return unicodedata.normalize("NFC", password)


def write_password_verifier(
    password: str,
    path: Path = VERIFIER_PATH,
    *,
    replace: bool = False,
) -> None:
    normalized_password = _normalize_password(password)
    if len(normalized_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Use a passphrase of at least {MIN_PASSWORD_LENGTH} characters."
        )
    _ensure_private_directory(path.parent)
    if path.exists() and not replace:
        raise FileExistsError("Unlock verifier already exists; use change-password to replace it.")
    salt = secrets.token_bytes(16)
    verifier = {
        "version": 2,
        "algorithm": "scrypt",
        "normalization": "NFC",
        "salt": salt.hex(),
        "digest": _derive_password(normalized_password, salt).hex(),
    }
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as verifier_file:
            json.dump(verifier, verifier_file, separators=(",", ":"))
            verifier_file.flush()
            os.fsync(verifier_file.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


class PasswordVerifier:
    def __init__(self, path: Path = VERIFIER_PATH):
        self.path = path

    def _load(self) -> tuple[bytes, bytes, int] | None:
        _ensure_private_file(self.path, create=False)
        try:
            data = json.loads(self.path.read_text(encoding="ascii"))
            if (
                not isinstance(data, dict)
                or data.get("version") not in {1, 2}
                or data.get("algorithm") != "scrypt"
                or (data.get("version") == 2 and data.get("normalization") != "NFC")
            ):
                return None
            salt = bytes.fromhex(data["salt"])
            expected = bytes.fromhex(data["digest"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if len(salt) != 16 or len(expected) != 32:
            return None
        return salt, expected, data["version"]

    def verify(self, password: str) -> bool:
        verifier = self._load()
        if verifier is None:
            return False
        salt, expected, version = verifier
        candidate = _normalize_password(password) if version == 2 else password
        if hmac.compare_digest(_derive_password(candidate, salt), expected):
            return True
        if version == 1:
            for form in {"NFC", "NFD"}:
                normalized = unicodedata.normalize(form, password)
                if hmac.compare_digest(_derive_password(normalized, salt), expected):
                    return True
        return False


def read_backend_token(path: Path = TOKEN_PATH) -> str:
    _ensure_private_file(path, create=False)
    token = path.read_text(encoding="ascii").strip()
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("The configured gateway token is missing or malformed.")
    return token


class PersistentAttemptThrottle:
    def __init__(self, path: Path = THROTTLE_PATH):
        _ensure_private_directory(path.parent)
        _ensure_private_file(path, create=True)
        self.path = path
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "failures INTEGER NOT NULL, "
                "lockouts INTEGER NOT NULL, "
                "blocked_until REAL NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO auth_state VALUES (1, 0, 0, 0)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5, isolation_level=None)

    def retry_after(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT blocked_until FROM auth_state WHERE id = 1"
            ).fetchone()
        return max(0, int(row[0] - time.time() + 0.999))

    def record_failure(self) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            failures, lockouts, blocked_until = connection.execute(
                "SELECT failures, lockouts, blocked_until "
                "FROM auth_state WHERE id = 1"
            ).fetchone()
            now = time.time()
            if blocked_until > now:
                connection.rollback()
                return max(1, int(blocked_until - now + 0.999))
            failures += 1
            retry_after = 0
            if failures >= MAX_AUTH_FAILURES:
                lockouts += 1
                retry_after = min(
                    BASE_LOCKOUT_SECONDS * 2 ** (lockouts - 1),
                    MAX_LOCKOUT_SECONDS,
                )
                failures = 0
                blocked_until = now + retry_after
            connection.execute(
                "UPDATE auth_state SET failures = ?, lockouts = ?, "
                "blocked_until = ? WHERE id = 1",
                (failures, lockouts, blocked_until),
            )
            connection.commit()
        return retry_after

    def record_success(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE auth_state SET failures = 0, lockouts = 0, "
                "blocked_until = 0 WHERE id = 1"
            )
            connection.commit()

    def reset(self) -> None:
        self.record_success()


def _decode_message(raw: str) -> dict[str, Any] | None:
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return message if isinstance(message, dict) else None


def _contains_secret(value: Any, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(
            _contains_secret(key, secret) or _contains_secret(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret(item, secret) for item in value)
    return False


class LocalAuthHelper:
    def __init__(
        self,
        *,
        token_path: Path = TOKEN_PATH,
        verifier_path: Path = VERIFIER_PATH,
        throttle_path: Path = THROTTLE_PATH,
        gateway_url: str = GATEWAY_URL,
        frontend_origin: str = FRONTEND_ORIGIN,
    ):
        self._backend_token = read_backend_token(token_path)
        self._password_verifier = PasswordVerifier(verifier_path)
        if self._password_verifier._load() is None:
            raise ValueError("The local unlock verifier is invalid.")
        self._throttle = PersistentAttemptThrottle(throttle_path)
        self._gateway_url = gateway_url
        self._frontend_origin = frontend_origin
        self._auth_lock = asyncio.Lock()

    async def _send(self, websocket: Any, message: dict[str, Any]) -> None:
        await websocket.send(json.dumps(message, separators=(",", ":")))

    async def _fail_authentication(
        self,
        websocket: Any,
        correlation_id: Any,
        retry_after: int = 0,
    ) -> None:
        locked = retry_after > 0
        response = {
            "action": "AUTHENTICATION_RESULT",
            "correlationId": correlation_id,
            "success": False,
            "status": "AUTHENTICATION_LOCKED" if locked else "AUTHENTICATION_FAILED",
            "error": (
                "Local unlock is temporarily locked."
                if locked
                else "Local unlock passphrase was not accepted."
            ),
        }
        if locked:
            response["retryAfterSeconds"] = retry_after
        await self._send(
            websocket,
            response,
        )
        await websocket.close(code=1008, reason="Authentication failed.")

    async def _authenticate_password(self, password: str) -> tuple[bool, int]:
        async with self._auth_lock:
            retry_after = self._throttle.retry_after()
            if retry_after > 0:
                return False, retry_after
            valid = await asyncio.to_thread(self._password_verifier.verify, password)
            if valid:
                self._throttle.record_success()
                return True, 0
            return False, self._throttle.record_failure()

    async def _relay(self, source: Any, destination: Any, *, from_gateway: bool) -> None:
        try:
            async for raw in source:
                message = _decode_message(raw)
                if message is None:
                    await source.close(code=1008, reason="Invalid protocol message.")
                    return
                if from_gateway:
                    if _contains_secret(message, self._backend_token):
                        await source.close(code=1011, reason="Gateway response rejected.")
                        return
                elif (
                    message.get("action") == "AUTHENTICATE"
                    or _contains_secret(message, self._backend_token)
                ):
                    await source.close(code=1008, reason="Credential forwarding is not allowed.")
                    return
                await destination.send(raw)
        except ConnectionClosed:
            return

    async def handler(self, websocket: Any) -> None:
        request = getattr(websocket, "request", None)
        if getattr(request, "path", None) != ENDPOINT_PATH:
            await websocket.close(code=1008, reason="Invalid endpoint.")
            return

        correlation_id: Any = None
        try:
            await self._send(
                websocket,
                {
                    "type": "connected",
                    "authenticationRequired": True,
                    "authMethod": "local-unlock",
                },
            )
            raw = await asyncio.wait_for(websocket.recv(), timeout=60)
            message = _decode_message(raw)
            if (
                message is None
                or message.get("action") != "AUTHENTICATE"
                or not isinstance(message.get("credential"), str)
                or len(message["credential"]) > 1024
            ):
                await self._fail_authentication(websocket, None)
                return
            correlation_id = message.get("correlationId")
            password = message["credential"]
            message.clear()
            raw = ""
            authenticated, retry_after = await self._authenticate_password(password)
            if not authenticated:
                password = ""
                await self._fail_authentication(
                    websocket,
                    correlation_id,
                    retry_after,
                )
                return
            password = ""

            try:
                async with websockets.connect(
                    self._gateway_url,
                    open_timeout=5,
                    close_timeout=1,
                    max_size=MAX_GATEWAY_MESSAGE_SIZE,
                    proxy=None,
                ) as upstream:
                    greeting = _decode_message(
                        await asyncio.wait_for(upstream.recv(), timeout=5)
                    )
                    if greeting is None:
                        await self._send(
                            websocket,
                            {
                                "action": "AUTHENTICATION_RESULT",
                                "correlationId": correlation_id,
                                "success": False,
                                "status": "AUTHENTICATION_PROTOCOL_ERROR",
                                "error": "The protected gateway returned an invalid authentication greeting.",
                            },
                        )
                        return
                    if greeting.get("authenticationRequired") is not True:
                        await self._send(
                            websocket,
                            {
                                "action": "AUTHENTICATION_RESULT",
                                "correlationId": correlation_id,
                                "success": False,
                                "status": "AUTHENTICATION_PROTOCOL_ERROR",
                                "error": "The protected gateway did not require expected authentication.",
                            },
                        )
                        return
                    await upstream.send(json.dumps({
                        "action": "AUTHENTICATE",
                        "correlationId": "local-helper-" + uuid4().hex,
                        "credential": self._backend_token,
                    }))
                    gateway_auth = _decode_message(
                        await asyncio.wait_for(upstream.recv(), timeout=5)
                    )
                    if gateway_auth is None or gateway_auth.get("action") != "AUTHENTICATION_RESULT":
                        await self._send(
                            websocket,
                            {
                                "action": "AUTHENTICATION_RESULT",
                                "correlationId": correlation_id,
                                "success": False,
                                "status": "AUTHENTICATION_PROTOCOL_ERROR",
                                "error": "The protected gateway returned an invalid authentication response.",
                            },
                        )
                        return
                    if gateway_auth.get("status") != "AUTHENTICATED":
                        await self._send(
                            websocket,
                            {
                                "action": "AUTHENTICATION_RESULT",
                                "correlationId": correlation_id,
                                "success": False,
                                "status": "AUTHENTICATION_UPSTREAM_REJECTED",
                                "error": "The protected gateway rejected its configured credential.",
                            },
                        )
                        return
                    await self._send(
                        websocket,
                        {
                            "action": "AUTHENTICATION_RESULT",
                            "correlationId": correlation_id,
                            "success": True,
                            "status": "AUTHENTICATED",
                            "principal": gateway_auth.get("principal"),
                            "sessionId": gateway_auth.get("sessionId"),
                            "expiresAt": gateway_auth.get("expiresAt"),
                        },
                    )
                    client_to_gateway = asyncio.create_task(
                        self._relay(websocket, upstream, from_gateway=False)
                    )
                    gateway_to_client = asyncio.create_task(
                        self._relay(upstream, websocket, from_gateway=True)
                    )
                    tasks = (client_to_gateway, gateway_to_client)
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except TimeoutError:
                await self._send(
                    websocket,
                    {
                        "action": "AUTHENTICATION_RESULT",
                        "correlationId": correlation_id,
                        "success": False,
                        "status": "AUTHENTICATION_TIMEOUT",
                        "error": "The protected gateway authentication timed out.",
                    },
                )
            except (OSError, InvalidStatus, ConnectionClosed):
                await self._send(
                    websocket,
                    {
                        "action": "AUTHENTICATION_RESULT",
                        "correlationId": correlation_id,
                        "success": False,
                        "status": "AUTHENTICATION_UNAVAILABLE",
                        "error": "The protected gateway is unavailable.",
                    },
                )
            except (ValueError,):
                return
            except Exception:
                await websocket.close(code=1011, reason="Local authentication helper failed.")
        except (ConnectionClosed, TimeoutError, OSError, ValueError):
            return
        except Exception:
            await websocket.close(code=1011, reason="Local authentication helper failed.")

    async def serve(self, host: str = HELPER_HOST, port: int = HELPER_PORT) -> None:
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("The local auth helper must bind to loopback.")
        except ValueError as exc:
            raise ValueError("The local auth helper must bind to loopback.") from exc

        async with websockets.serve(
            self.handler,
            host,
            port,
            origins=[self._frontend_origin],
            max_size=MAX_MESSAGE_SIZE,
            ping_interval=20,
            close_timeout=1,
        ):
            print(f"ULTRON local auth helper listening on ws://{host}:{port}{ENDPOINT_PATH}")
            await asyncio.Future()


def _prompt_password(*, confirm: bool) -> str:
    password = getpass.getpass("New ULTRON local unlock passphrase: ")
    if confirm:
        repeated = getpass.getpass("Repeat passphrase: ")
        if not hmac.compare_digest(password, repeated):
            raise ValueError("Passphrases do not match.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Use a passphrase of at least {MIN_PASSWORD_LENGTH} characters."
        )
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local ULTRON password unlock helper")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-password")
    commands.add_parser("change-password")
    commands.add_parser("reset-throttle")
    commands.add_parser("serve")
    args = parser.parse_args(argv)

    try:
        if args.command in {"setup-password", "change-password"}:
            password = _prompt_password(confirm=True)
            write_password_verifier(
                password,
                replace=args.command == "change-password",
            )
            password = ""
            print("Local unlock verifier saved securely.")
            return 0
        if args.command == "reset-throttle":
            PersistentAttemptThrottle().reset()
            print("Local unlock throttle reset.")
            return 0
        asyncio.run(LocalAuthHelper().serve())
        return 0
    except (OSError, PermissionError, ValueError, FileExistsError) as exc:
        print(f"Local auth helper: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())