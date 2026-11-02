import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import urlopen

import numpy as np
import pytest

from ultron.voice import server as voice_server
from ultron.voice.tts.xtts_engine import XtTsEngine, _REQUIRED_MODEL_FILES


def _create_model_dir(path):
    path.mkdir()
    for name in _REQUIRED_MODEL_FILES:
        artifact = path / name
        if name == "config.json":
            artifact.write_text("{}", encoding="utf-8")
        else:
            artifact.write_bytes(b"artifact")
    return path


def test_xtts_requires_explicit_local_model_directory():
    engine = XtTsEngine(
        {"voice_engine": {"model_spec": "must-not-download"}}, {}
    )

    with pytest.raises(FileNotFoundError, match="automatic model downloads are disabled"):
        engine._resolve_model_dir()


def test_xtts_rejects_incomplete_local_model_directory(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    engine = XtTsEngine({"voice_engine": {"model_path": str(model_dir)}}, {})

    with pytest.raises(FileNotFoundError, match="model.pth"):
        engine._resolve_model_dir()


def test_xtts_rejects_invalid_local_model_config(tmp_path):
    model_dir = _create_model_dir(tmp_path / "model")
    (model_dir / "config.json").write_text("{invalid", encoding="utf-8")
    engine = XtTsEngine({"voice_engine": {"model_path": str(model_dir)}}, {})

    with pytest.raises(ValueError, match="config.json is unreadable or invalid"):
        engine._resolve_model_dir()


def test_xtts_rejects_missing_reference_before_loading_model(tmp_path):
    model_dir = _create_model_dir(tmp_path / "model")
    engine = XtTsEngine(
        {
            "voice_engine": {
                "model_path": str(model_dir),
                "reference_audio": str(tmp_path / "missing-reference.wav"),
            }
        },
        {},
    )

    with pytest.raises(FileNotFoundError, match="reference audio is absent"):
        engine.prepare()


def test_xtts_forwards_reference_to_speaker_wav(tmp_path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"test fixture")
    calls = {}

    class Model:
        def synthesize(self, **kwargs):
            calls.update(kwargs)
            return {"wav": np.zeros(2400), "sample_rate": 24000}

    engine = XtTsEngine({}, {})
    engine._ready = True
    engine.is_clone = True
    engine._model = Model()
    engine._xtts_config = object()
    engine._reference = str(reference)
    output = tmp_path / "output.wav"

    result = engine.synthesize("Test.", {"rate": 1.0}, str(output))

    assert calls["speaker_wav"] == str(reference)
    assert calls["language"] == "en"
    assert result.is_clone is True
    assert output.is_file()


def test_unready_xtts_does_not_report_clone_capability():
    engine = XtTsEngine({}, {})

    assert engine.ready is False
    assert engine.is_clone is False


def test_required_xtts_failure_does_not_start_edge_fallback(monkeypatch):
    initialized = []

    class Engine:
        def __init__(self, name):
            self.name = name
            self.ready = False
            self.is_clone = name == "xtts"

        def prepare(self):
            raise RuntimeError("test initialization failure")

    class Pipeline:
        def __init__(self, engine_name, config, profile):
            initialized.append(engine_name)
            self.engine = Engine(engine_name)

    monkeypatch.setattr(voice_server, "VoicePipeline", Pipeline)

    with pytest.raises(RuntimeError, match="Required XTTS engine failed"):
        voice_server.build_server(
            engine_name="xtts",
            preload=True,
            port=0,
            model_dir="/local/model",
            require_engine=True,
        )

    assert initialized == ["xtts"]


def test_normal_startup_retains_edge_fallback(monkeypatch):
    initialized = []

    class Engine:
        def __init__(self, name):
            self.name = name
            self.ready = False
            self.is_clone = name == "xtts"

        def prepare(self):
            if self.name == "xtts":
                raise RuntimeError("test XTTS failure")
            self.ready = True

    class Pipeline:
        def __init__(self, engine_name, config, profile):
            initialized.append(engine_name)
            self.engine = Engine(engine_name)

    monkeypatch.setattr(voice_server, "VoicePipeline", Pipeline)
    server = voice_server.build_server(engine_name="xtts", preload=True, port=0)
    try:
        assert initialized == ["xtts", "edge"]
        assert server.pipeline.engine.name == "edge"
        assert server.pipeline.engine.is_clone is False
    finally:
        server.server_close()


def test_health_does_not_claim_clone_when_xtts_is_unready():
    class Pipeline:
        engine = SimpleNamespace(ready=False, name="xtts", is_clone=True)
        profile = {}
        config = {}

    server = ThreadingHTTPServer(("127.0.0.1", 0), voice_server._Handler)
    server.pipeline = Pipeline()
    server.styles = {}
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/v1/health") as response:
            health = json.loads(response.read())
        assert health["engine"] == "xtts"
        assert health["ready"] is False
        assert health["is_clone"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
