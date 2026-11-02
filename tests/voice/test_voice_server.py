import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request, urlopen

from ultron.voice import server as voice_server


def test_speak_endpoint_removes_generated_audio_after_serving(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_server.tempfile, "gettempdir", lambda: str(tmp_path))
    audio_bytes = b"RIFF" + b"\x00" * 4096

    class TestPipeline:
        engine = SimpleNamespace(ready=True, name="edge", is_clone=False)

        def speak(self, _text, _style, output_path):
            with open(output_path, "wb") as audio_file:
                audio_file.write(audio_bytes)
            with open(f"{output_path}.mp3", "wb") as audio_file:
                audio_file.write(b"temporary encoded audio")
            return SimpleNamespace(path=output_path)

    http_server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        voice_server._Handler,
    )
    http_server.pipeline = TestPipeline()
    http_server.styles = {}
    http_server.max_chars = 2_000
    server_thread = threading.Thread(target=http_server.serve_forever)
    server_thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{http_server.server_port}/api/v1/speak",
            data=json.dumps({"text": "Temporary cleanup test."}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "audio/wav"
            assert response.read() == audio_bytes
    finally:
        http_server.shutdown()
        http_server.server_close()
        server_thread.join(timeout=5)

    assert list(tmp_path.iterdir()) == []


def test_speak_endpoint_passes_context_only_to_dialogue_pipeline():
    class TestPipeline:
        engine = SimpleNamespace(ready=True, name="xtts", is_clone=True)

        def __init__(self):
            self.dialogue = None

        def speak_dialogue(self, text, context, output_path):
            self.dialogue = (text, context)
            with open(output_path, "wb") as audio_file:
                audio_file.write(b"RIFF" + b"\x00" * 4096)
            return SimpleNamespace(path=output_path)

    pipeline = TestPipeline()
    http_server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        voice_server._Handler,
    )
    http_server.pipeline = pipeline
    http_server.styles = {}
    http_server.max_chars = 2_000
    server_thread = threading.Thread(target=http_server.serve_forever)
    server_thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{http_server.server_port}/api/v1/speak",
            data=json.dumps(
                {"text": "I understand.", "context": "I am worried."}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
            assert response.read().startswith(b"RIFF")
        assert pipeline.dialogue == ("I understand.", "I am worried.")
    finally:
        http_server.shutdown()
        http_server.server_close()
        server_thread.join(timeout=5)
