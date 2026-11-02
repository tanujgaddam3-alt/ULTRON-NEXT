"""ULTRON Voice service - HTTP API.

A minimal, dependency-light HTTP server exposing the voice pipeline to the
ULTRON frontend and other clients.

Endpoints
---------
* ``GET  /api/v1/health``
      -> ``{status, engine, is_clone, ready, profile_summary, style}``
* ``POST /api/v1/speak``
      JSON ``{"text": str, "style": "normal|information|warning|critical|confirmation|question|error"}``
      -> ``200 audio/wav`` (inline PCM16 WAV, 24 kHz mono)
      -> ``400 `` invalid input
      -> ``503 `` engine unavailable / generation error
* ``GET  /api/v1/engines``
      -> ``{primary, fallback, available: [...], clone_capable: bool}``

Run
---
    python -m ultron.voice.server            (defaults from config/ultron_voice.json)
    python -m ultron.voice.server --port 8765 --no-preload
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .pipeline import VoicePipeline

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class _Handler(BaseHTTPRequestHandler):
    server_version = "ULTRONVoice/1.0"  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    def log_message(self, fmt, *args):  # quiet default logging
        sys.stderr.write("[voice] %s %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_wav(self, path: str):
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Engine", str(self.server.pipeline.engine.name))
        self.send_header(
            "X-Voice-Clone",
            str(
                bool(
                    getattr(self.server.pipeline.engine, "ready", False)
                    and self.server.pipeline.engine.is_clone
                )
            ).lower(),
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------ #
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path.rstrip("/") == "/api/v1/health":
            p = self.server.pipeline
            ready = bool(getattr(p.engine, "ready", False))
            profile = p.profile
            summary = {
                "f0_median_hz": profile.get("pitch", {}).get("f0_median_hz"),
                "voice_class": profile.get("pitch", {}).get("voice_class"),
                "spectral_low_band_pct": profile.get("spectral", {}).get(
                    "low_band_pct"
                ),
                "lufs_approx": profile.get("signal", {}).get("lufs_approximate"),
            }
            self._send_json(
                200,
                {
                    "status": "ready" if ready else "warming",
                    "engine": p.engine.name,
                    "is_clone": bool(ready and p.engine.is_clone),
                    "ready": ready,
                    "profile_summary": summary,
                    "styles": list(self.server.styles),
                    "processing_enabled": bool(
                        p.config.get("processing", {}).get("enabled", True)
                    ),
                },
            )
            return
        if path.rstrip("/") == "/api/v1/engines":
            p = self.server.pipeline
            self._send_json(
                200,
                {
                    "primary": p.engine.name,
                    "fallback": "edge",
                    "available": ["xtts", "edge"],
                    "clone_capable": p.engine.is_clone and p.engine.ready,
                },
            )
            return
        if path.rstrip("/") == "/api/v1/styles":
            self._send_json(200, {"styles": list(self.server.styles)})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        request_started = time.perf_counter()
        path = self.path.split("?", 1)[0]
        if path.rstrip("/") != "/api/v1/speak":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._send_json(400, {"error": "invalid JSON body"})
            return

        text = str(payload.get("text", "")).strip()
        if not text:
            self._send_json(400, {"error": "missing 'text'"})
            return
        if len(text) > int(self.server.max_chars):
            self._send_json(
                400, {"error": f"text exceeds {self.server.max_chars} chars"}
            )
            return
        style = str(payload.get("style", "normal")).strip()
        context = payload.get("context", "")
        if not isinstance(context, str):
            self._send_json(400, {"error": "'context' must be a string"})
            return
        context = context.strip()
        if len(context) > int(self.server.max_chars):
            self._send_json(
                400, {"error": f"context exceeds {self.server.max_chars} chars"}
            )
            return

        p = self.server.pipeline
        tmp_wav = os.path.join(
            tempfile.gettempdir(),
            f"ultron_voice_{os.getpid()}_{time.monotonic_ns()}_{uuid.uuid4().hex[:8]}.wav",
        )
        try:
            try:
                if not getattr(p.engine, "ready", False):
                    try:
                        p.engine.prepare()
                    except Exception as exc:
                        self._send_json(
                            503,
                            {
                                "error": f"engine unavailable: {exc}",
                                "hint": "run with --engine edge",
                            },
                        )
                        return
                pipeline_started = time.perf_counter()
                if context:
                    result = p.speak_dialogue(
                        text,
                        context=context,
                        output_path=tmp_wav,
                    )
                else:
                    result = p.speak(text, style, output_path=tmp_wav)
                pipeline_completed = time.perf_counter()
            except Exception as exc:
                traceback.print_exc()
                self._send_json(503, {"error": f"generation failed: {exc}"})
                return
            self._send_wav(result.path)
            response_completed = time.perf_counter()
            if os.environ.get("ULTRON_VOICE_TIMING") == "1":
                timings = getattr(result, "timings", {})
                synthesis_start = (
                    pipeline_started
                    + float(timings.get("synthesis_start_offset_s", 0.0))
                    - request_started
                )
                processing_complete = (
                    pipeline_started
                    + float(timings.get("processing_complete_offset_s", 0.0))
                    - request_started
                )
                sys.stderr.write(
                    "[voice] timing "
                    f"request_to_pipeline_ms={(pipeline_started - request_started) * 1000:.1f} "
                    f"synthesis_start_after_request_ms={synthesis_start * 1000:.1f} "
                    f"synthesis_ms={float(timings.get('synthesis_s', 0.0)) * 1000:.1f} "
                    f"processing_complete_after_request_ms={processing_complete * 1000:.1f} "
                    f"processing_ms={float(timings.get('processing_s', 0.0)) * 1000:.1f} "
                    f"response_transfer_ms={(response_completed - pipeline_completed) * 1000:.1f} "
                    f"total_ms={(response_completed - request_started) * 1000:.1f}\n"
                )
        finally:
            for generated_path in (tmp_wav, f"{tmp_wav}.mp3"):
                try:
                    os.remove(generated_path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    sys.stderr.write(
                        "[voice] unable to remove generated audio temporary "
                        f"file ({type(exc).__name__})\n"
                    )

    def do_PUT(self):  # noqa: N802 - not supported
        self._send_json(405, {"error": "method not allowed"})


def build_server(
    engine_name: str | None = None,
    preload: bool = True,
    port: int = 8765,
    model_dir: str | None = None,
    require_engine: bool = False,
) -> ThreadingHTTPServer:
    config = load_json(os.path.join(BASE_DIR, "config", "ultron_voice.json"))
    profile = load_json(os.path.join(BASE_DIR, "config", "voice_profile.json"))

    if engine_name is None:
        engine_name = config.get("voice_engine", {}).get("primary", "xtts")
    engine_name = engine_name.lower().strip()
    if model_dir and engine_name != "xtts":
        raise ValueError("--model-dir can only be used with --engine xtts.")
    if require_engine and engine_name != "xtts":
        raise ValueError("--require-engine currently requires --engine xtts.")
    if require_engine and not preload:
        raise ValueError("--require-engine requires model preload at startup.")
    if model_dir:
        config.setdefault("voice_engine", {})["model_path"] = model_dir

    pipeline = VoicePipeline(engine_name=engine_name, config=config, profile=profile)
    if preload:
        initialization_started = time.perf_counter()
        try:
            pipeline.engine.prepare()
        except Exception as exc:
            if os.environ.get("ULTRON_VOICE_TIMING") == "1":
                sys.stderr.write(
                    "[voice] timing "
                    f"engine_initialization_ms="
                    f"{(time.perf_counter() - initialization_started) * 1000:.1f} "
                    f"engine={engine_name} status=failed\n"
                )
            if require_engine:
                raise RuntimeError(
                    "Required XTTS engine failed to initialize; isolated server "
                    "was not started."
                ) from exc
            sys.stderr.write(f"[voice] preload of {engine_name!r} failed: {exc}\n")
            # fall back for this run: hand the server an edge engine that is ready
            pipeline = VoicePipeline(engine_name="edge", config=config, profile=profile)
            try:
                pipeline.engine.prepare()
            except Exception as fallback_exc:
                sys.stderr.write(f"[voice] edge fallback also failed: {fallback_exc}\n")
        else:
            if os.environ.get("ULTRON_VOICE_TIMING") == "1":
                sys.stderr.write(
                    "[voice] timing "
                    f"engine_initialization_ms="
                    f"{(time.perf_counter() - initialization_started) * 1000:.1f} "
                    f"engine={pipeline.engine.name} status=ready\n"
                )

    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    srv.pipeline = pipeline  # type: ignore[attr-defined]
    srv.styles = config.get("delivery", {}).get("styles", {})  # type: ignore[attr-defined]
    srv.max_chars = int(config.get("server", {}).get("max_request_chars", 2000))  # type: ignore[attr-defined]
    return srv


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="ULTRON voice service")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--engine", type=str, default=None, help="xtts (default) | edge")
    ap.add_argument(
        "--model-dir",
        type=str,
        default=None,
        help="explicit local XTTS model directory; no model download is performed",
    )
    ap.add_argument(
        "--require-engine",
        action="store_true",
        help="fail startup rather than fall back if XTTS cannot initialize",
    )
    ap.add_argument("--no-preload", action="store_true", help="do not load model at start")
    args = ap.parse_args(argv)

    cfg = load_json(os.path.join(BASE_DIR, "config", "ultron_voice.json"))
    port = args.port or int(cfg.get("server", {}).get("port", 8765))
    preload = not args.no_preload
    srv = build_server(
        engine_name=args.engine,
        preload=preload,
        port=port,
        model_dir=args.model_dir,
        require_engine=args.require_engine,
    )

    engine = srv.pipeline.engine  # type: ignore[attr-defined]
    sys.stderr.write(
        f"[voice] ULTRON voice service http://127.0.0.1:{port}/api/v1 "
        f"engine={engine.name} clone={engine.is_clone} ready={getattr(engine, 'ready', False)}\n"
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()