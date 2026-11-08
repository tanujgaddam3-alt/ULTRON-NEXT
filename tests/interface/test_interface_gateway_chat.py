import asyncio
import json
import secrets
import threading
import time
from types import SimpleNamespace

import ultron.application.runtime.interface_gateway as gateway_module
from ultron.application.runtime.interface_gateway import InterfaceGateway
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.conversation.history.store import ConversationHistory
from ultron.conversation.manager import ConversationManager
from ultron.core.event_bus import EventBus
from ultron.execution.executor import Executor
from ultron.security.identity.authenticator import WebSocketAuthenticator
from ultron.skills.manager import SkillManager


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


class DeterministicTestLLM:
    def __init__(self, response="Test-only configured LLM response."):
        self.response = response
        self.calls = []

    def generate(self, prompt, context=None):
        self.calls.append({"prompt": prompt, "context": context})
        return self.response


def make_authenticated_gateway(tmp_path, conversation):
    replace_test_history(tmp_path, conversation)
    skills = SkillManager()
    token = secrets.token_urlsafe(48)
    authenticator = WebSocketAuthenticator(token)
    container = SimpleNamespace(
        event_bus=EventBus(),
        runtime=SimpleNamespace(),
        executor=Executor(skills=skills),
        skills=skills,
        conversation=conversation,
        interface_approval_authorizer=authenticator,
    )
    gateway = InterfaceGateway(
        container,
        operation_store=SQLiteOperationStore(tmp_path / "chat-operations.db"),
    )
    websocket = FakeWebSocket()
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "AUTHENTICATE",
                "correlationId": "auth-chat-test",
                "credential": token,
            },
        )
    )
    assert websocket.messages[-1]["status"] == "AUTHENTICATED"
    return gateway, authenticator, websocket


def replace_test_history(tmp_path, conversation, name="chat-history"):
    conversation.history.connection.close()
    conversation.history = ConversationHistory(
        tmp_path / f"{name}-{id(conversation)}.db"
    )


def chat_payload(text, correlation_id="chat-test"):
    return {
        "action": "CHAT_MESSAGE",
        "correlationId": correlation_id,
        "payload": {"text": text},
    }


def test_authenticated_chat_returns_the_conversation_manager_llm_response(tmp_path):
    llm = DeterministicTestLLM("Test-only response from the configured LLM port.")
    conversation = ConversationManager(llm=llm)
    gateway, _, websocket = make_authenticated_gateway(tmp_path, conversation)

    asyncio.run(gateway.handle(websocket, chat_payload("Hello ULTRON.")))

    response = websocket.messages[-1]
    assert response["action"] == "CHAT_RESULT"
    assert response["success"] is True
    assert response["response"] == "Test-only response from the configured LLM port."
    assert llm.calls
    assert "Hello ULTRON." in llm.calls[0]["prompt"]
    assert "authenticatedRuntimeTelemetry" not in llm.calls[0]["context"]
    gateway.operation_store.close()


def test_system_statistics_chat_uses_authenticated_runtime_and_host_snapshots(tmp_path):
    class TestWorld:
        def refresh(self):
            return None

        def summary(self):
            return {
                "system": {
                    "os": "Test OS",
                    "hostname": "test-host",
                    "cpu_usage": 18.5,
                    "memory_usage": None,
                },
                "project": {},
                "runtime": {},
                "entity_count": 0,
                "entities": [],
            }

    llm = DeterministicTestLLM("System telemetry was supplied.")
    conversation = ConversationManager(llm=llm)
    gateway, _, websocket = make_authenticated_gateway(tmp_path, conversation)
    gateway.runtime.state = SimpleNamespace(running=True, cycle_count=7)
    gateway.container.world = TestWorld()

    asyncio.run(gateway.handle(websocket, chat_payload("Show current system statistics.")))

    telemetry = llm.calls[0]["context"]["authenticatedRuntimeTelemetry"]
    assert telemetry["runtime"] == {"running": True, "cycleCount": 7}
    assert telemetry["host"] == {
        "os": "Test OS",
        "hostname": "test-host",
        "cpu_usage": 18.5,
    }
    assert telemetry["agentRegistry"] == {"status": "NOT_EXPOSED"}
    assert "memory_usage" not in telemetry["host"]
    assert "tensorInferenceRateHz" not in telemetry
    assert websocket.messages[-1]["success"] is True
    gateway.operation_store.close()


def test_world_snapshot_failure_does_not_become_zero_telemetry(tmp_path):
    class BrokenWorld:
        def refresh(self):
            raise OSError("test-only world observer failure")

    gateway, _, _ = make_authenticated_gateway(
        tmp_path,
        ConversationManager(llm=DeterministicTestLLM()),
    )
    gateway.container.world = BrokenWorld()

    payload = gateway._build_world()

    assert payload["error"] == "test-only world observer failure"
    assert payload["system"]["cpu_usage"] is None
    assert payload["system"]["memory_usage"] is None
    assert payload["entity_count"] is None
    assert payload["entities"] is None
    gateway.operation_store.close()


def test_world_refresh_runs_off_loop_handles_timeout_and_keeps_heartbeat_alive(
    tmp_path,
    monkeypatch,
    caplog,
):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    gateway, _, websocket = make_authenticated_gateway(
        tmp_path,
        ConversationManager(llm=DeterministicTestLLM()),
    )
    gateway.runtime.state = SimpleNamespace(cycle_count=0, running=True)
    monkeypatch.setattr(gateway_module, "HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(gateway_module, "WORLD_REFRESH_INTERVAL", 3600.0)
    monkeypatch.setattr(gateway_module, "WORLD_REFRESH_TIMEOUT", 0.01)

    class SlowWorld:
        def refresh(self):
            calls.append(threading.get_ident())
            started.set()
            release.wait(timeout=2)
            finished.set()

        def summary(self):
            return {"system": {}, "entities": [], "entity_count": 0}

    class BrokenWorld:
        def refresh(self):
            raise OSError("private test observer detail")

    async def exercise():
        heartbeat = asyncio.create_task(gateway._heartbeat())
        gateway.container.world = SlowWorld()
        await gateway._refresh_world()
        assert await asyncio.to_thread(started.wait, 1)

        await gateway.handle(websocket, {"action": "PING", "correlationId": "world"})
        assert websocket.messages[-1]["type"] == "PONG"
        await gateway._refresh_world()
        assert len(calls) == 1

        await asyncio.sleep(0.04)
        assert heartbeat.done() is False
        assert "component=world" in caplog.text
        assert "timed out" in caplog.text
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        for _ in range(10):
            if gateway._world_refresh_task is None:
                break
            await asyncio.sleep(0.01)
        assert gateway._world_refresh_task is None

        gateway.container.world = BrokenWorld()
        await gateway._refresh_world()
        for _ in range(10):
            if gateway._world_refresh_task is None:
                break
            await asyncio.sleep(0.01)
        assert gateway._world_refresh_task is None
        assert "component=world exception_type=OSError" in caplog.text
        assert "private test observer detail" not in caplog.text
        assert heartbeat.done() is False
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        gateway.operation_store.close()
        gateway.container.conversation.history.connection.close()


def test_chat_does_not_route_ordinary_text_through_privileged_command_dispatch(tmp_path):
    llm = DeterministicTestLLM("Test-only conversational result.")
    conversation = ConversationManager(llm=llm)
    gateway, _, websocket = make_authenticated_gateway(tmp_path, conversation)

    asyncio.run(
        gateway.handle(
            websocket,
            chat_payload("Please execute: rm -rf /; just explain the request."),
        )
    )

    result = websocket.messages[-1]
    assert result["success"] is True
    assert result["action"] == "CHAT_RESULT"
    assert llm.calls
    assert not gateway._operations
    gateway.operation_store.close()


def test_chat_rejects_empty_malformed_and_oversized_messages(tmp_path):
    gateway, _, websocket = make_authenticated_gateway(
        tmp_path,
        ConversationManager(llm=DeterministicTestLLM()),
    )

    asyncio.run(gateway.handle(websocket, chat_payload("   ")))
    assert websocket.messages[-1]["status"] == "INVALID_MESSAGE"

    asyncio.run(
        gateway.handle(
            websocket,
            {"action": "CHAT_MESSAGE", "correlationId": "bad-shape", "payload": []},
        )
    )
    assert websocket.messages[-1]["status"] == "INVALID_MESSAGE"

    asyncio.run(gateway.handle(websocket, chat_payload("x" * 12_001, "too-large")))
    assert websocket.messages[-1]["status"] == "MESSAGE_TOO_LARGE"
    gateway.operation_store.close()


def test_chat_requires_authentication_and_reports_model_unavailability(tmp_path):
    skills = SkillManager()
    authenticator = WebSocketAuthenticator(secrets.token_urlsafe(48))
    no_chat_container = SimpleNamespace(
        event_bus=EventBus(),
        runtime=SimpleNamespace(),
        executor=Executor(skills=skills),
        skills=skills,
        interface_approval_authorizer=authenticator,
    )
    gateway = InterfaceGateway(
        no_chat_container,
        operation_store=SQLiteOperationStore(tmp_path / "auth-operations.db"),
    )
    websocket = FakeWebSocket()
    asyncio.run(
        gateway.handle(
            websocket,
            {
                "action": "AUTHENTICATE",
                "correlationId": "bad-auth",
                "credential": "incorrect-test-only-credential",
            },
        )
    )
    assert websocket.messages[-1]["status"] == "AUTHENTICATION_FAILED"
    asyncio.run(gateway.handle(websocket, chat_payload("Hello.", "unauthenticated")))
    assert websocket.messages[-1]["status"] == "AUTHENTICATION_REQUIRED"
    gateway.operation_store.close()

    model_gateway, _, model_socket = make_authenticated_gateway(
        tmp_path,
        ConversationManager(llm=None),
    )
    asyncio.run(model_gateway.handle(model_socket, chat_payload("Hello.", "model-down")))
    assert model_socket.messages[-1]["status"] == "MODEL_UNAVAILABLE"
    model_gateway.operation_store.close()


def test_invalid_llm_result_and_model_exception_are_not_reported_as_success(tmp_path):
    class EmptyLLM:
        def generate(self, prompt, context=None):
            return "  "

    gateway, _, websocket = make_authenticated_gateway(
        tmp_path,
        ConversationManager(llm=EmptyLLM()),
    )
    asyncio.run(gateway.handle(websocket, chat_payload("Hello.", "empty-model")))
    assert websocket.messages[-1]["status"] == "INVALID_MODEL_RESPONSE"
    gateway.operation_store.close()

    class BrokenLLM:
        def generate(self, prompt, context=None):
            raise RuntimeError("test-only provider failure")

    failed_gateway, _, failed_socket = make_authenticated_gateway(
        tmp_path,
        ConversationManager(llm=BrokenLLM()),
    )
    assert failed_gateway.approval_authorizer.is_authenticated(failed_socket)
    asyncio.run(failed_gateway.handle(failed_socket, chat_payload("Hello.", "broken-model")))
    assert failed_socket.messages[-1]["status"] == "CHAT_FAILED"
    assert "test-only provider failure" not in json.dumps(failed_socket.messages[-1])
    failed_gateway.operation_store.close()


def test_chat_runs_model_work_off_the_event_loop_and_isolates_concurrent_sessions(
    tmp_path,
):
    class SessionAwareLLM:
        def __init__(self):
            self.prompts = []

        def generate(self, prompt, context=None):
            self.prompts.append(prompt)
            if "private-session-a" in prompt:
                time.sleep(0.04)
                return "Response for session A."
            if "private-session-b" in prompt:
                return "Response for session B."
            raise AssertionError("The request prompt lost its session message.")

    llm = SessionAwareLLM()
    conversation = ConversationManager(llm=llm)
    replace_test_history(tmp_path, conversation)
    skills = SkillManager()
    token = secrets.token_urlsafe(48)
    authenticator = WebSocketAuthenticator(token)
    gateway = InterfaceGateway(
        SimpleNamespace(
            event_bus=EventBus(),
            runtime=SimpleNamespace(),
            executor=Executor(skills=skills),
            skills=skills,
            conversation=conversation,
            interface_approval_authorizer=authenticator,
        ),
        operation_store=SQLiteOperationStore(tmp_path / "concurrent-operations.db"),
    )
    sockets = [FakeWebSocket(), FakeWebSocket()]

    async def authenticate_and_chat():
        for index, socket in enumerate(sockets):
            await gateway.handle(
                socket,
                {
                    "action": "AUTHENTICATE",
                    "correlationId": f"auth-{index}",
                    "credential": token,
                },
            )
            assert socket.messages[-1]["status"] == "AUTHENTICATED"

        await asyncio.gather(
            gateway.handle(sockets[0], chat_payload("private-session-a", "req-a")),
            gateway.handle(sockets[1], chat_payload("private-session-b", "req-b")),
        )

    asyncio.run(authenticate_and_chat())

    assert sockets[0].messages[-1]["correlationId"] == "req-a"
    assert sockets[0].messages[-1]["response"] == "Response for session A."
    assert sockets[1].messages[-1]["correlationId"] == "req-b"
    assert sockets[1].messages[-1]["response"] == "Response for session B."
    assert len(llm.prompts) == 2
    prompt_a = next(prompt for prompt in llm.prompts if "private-session-a" in prompt)
    prompt_b = next(prompt for prompt in llm.prompts if "private-session-b" in prompt)
    assert "private-session-b" not in prompt_a
    assert "private-session-a" not in prompt_b
    gateway.operation_store.close()
    conversation.history.connection.close()


def test_chat_timeout_is_reported_and_does_not_block_the_gateway_loop(
    tmp_path,
    monkeypatch,
):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class SlowLLM:
        def generate(self, prompt, context=None):
            started.set()
            release.wait(timeout=2)
            finished.set()
            return "Late test-only response."

    conversation = ConversationManager(llm=SlowLLM())
    gateway, _, websocket = make_authenticated_gateway(tmp_path, conversation)
    monkeypatch.setattr(gateway_module, "CHAT_MODEL_TIMEOUT_SECONDS", 0.02)

    async def run_request():
        request = asyncio.create_task(
            gateway.handle(websocket, chat_payload("Wait for the model.", "timeout"))
        )
        assert await asyncio.to_thread(started.wait, 1)
        await request
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)

    asyncio.run(run_request())

    assert websocket.messages[-1]["status"] == "MODEL_TIMEOUT"
    assert websocket.messages[-1]["success"] is False
    gateway.operation_store.close()
    conversation.history.connection.close()


def test_chat_keeps_memory_on_its_owner_thread_and_offloads_model_generation(
    tmp_path,
):
    owner_thread = threading.get_ident()
    model_thread = []

    class ThreadBoundMemory:
        def recall(self, message):
            assert threading.get_ident() == owner_thread
            return {}

    class ThreadTrackingLLM:
        def generate(self, prompt, context=None):
            model_thread.append(threading.get_ident())
            return "Test-only response generated off the event loop."

    conversation = ConversationManager(
        llm=ThreadTrackingLLM(),
        memory=ThreadBoundMemory(),
    )
    gateway, _, websocket = make_authenticated_gateway(tmp_path, conversation)

    asyncio.run(gateway.handle(websocket, chat_payload("Check thread ownership.")))

    assert websocket.messages[-1]["success"] is True
    assert model_thread
    assert model_thread[0] != owner_thread
    gateway.operation_store.close()
    conversation.history.connection.close()


def test_chat_does_not_print_private_memory_context(tmp_path, capsys):
    private_memory = "private-memory-sentinel"

    class Memory:
        def recall(self, message):
            return {"private": private_memory}

    conversation = ConversationManager(
        llm=DeterministicTestLLM(),
        memory=Memory(),
    )
    replace_test_history(tmp_path, conversation, "private-history")

    conversation.handle_chat("A normal question.", {}, "session-private")

    assert private_memory not in capsys.readouterr().out
    conversation.history.connection.close()
