"""XTTS v2 cloning engine (coqui-tts, low-level loader).

Loads a complete, explicitly configured local ``coqui/XTTS-v2`` model and
conditions it on the configured ULTRON reference recording. Model downloads
are deliberately not performed during service startup. Inference is CPU-only
by default; thread count is bounded from config.

The low-level ``Xtts`` API is used instead of ``TTS.api.TTS`` because the
sugar API's config auto-discovery fails across transformers 4.57+; the
low-level loader is explicit and version-stable.
"""

from __future__ import annotations

import json
import os
import time

from .base import BaseEngine, EngineResult


_REQUIRED_MODEL_FILES = (
    "config.json",
    "model.pth",
    "dvae.pth",
    "mel_stats.pth",
    "speakers_xtts.pth",
    "vocab.json",
    "LICENSE.txt",
)


class XtTsEngine(BaseEngine):
    name = "xtts"
    is_clone = True

    def __init__(self, config: dict, profile: dict):
        super().__init__(config, profile)
        self.is_clone = False
        self._model: object | None = None
        self._xtts_config: object | None = None
        self._reference: str | None = None
        self._sample_rate = int(config.get("sample_rate", 24000))
        self._torch_threads = int(
            config.get("voice_engine", {}).get("torch_threads", 4)
        )

    # ------------------------------------------------------------------ #
    def _resolve_model_dir(self) -> str:
        explicit = self.config.get("voice_engine", {}).get("model_path")
        if not explicit:
            raise FileNotFoundError(
                "XTTS requires an explicit local voice_engine.model_path; "
                "automatic model downloads are disabled."
            )
        if not os.path.isdir(explicit):
            raise FileNotFoundError("Configured local XTTS model directory is absent.")
        missing = [
            name
            for name in _REQUIRED_MODEL_FILES
            if not os.path.isfile(os.path.join(explicit, name))
            or os.path.getsize(os.path.join(explicit, name)) == 0
        ]
        if missing:
            raise FileNotFoundError(
                "Local XTTS model directory is incomplete; missing or empty "
                f"artifacts: {', '.join(missing)}."
            )
        config_path = os.path.join(explicit, "config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as config_file:
                model_config = json.load(config_file)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Local XTTS config.json is unreadable or invalid.") from exc
        if not isinstance(model_config, dict):
            raise ValueError("Local XTTS config.json must contain a JSON object.")
        return os.path.abspath(explicit)

    def _resolve_reference(self, rel: str) -> str:
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        candidates = [
            rel,
            os.path.join(root, rel),
            os.path.join(root, "voice", rel),
            os.path.join(os.getcwd(), rel),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return candidates[0]

    # ------------------------------------------------------------------ #
    def _ensure_ffmpeg_dlls_on_path(self) -> None:
        """Ensure FFmpeg libraries are discoverable.

        Linux/WSL uses the system dynamic linker and installed FFmpeg.
        Windows retains support for the configured DLL directory.
        """
        import sys

        if not sys.platform.startswith("win"):
            return

        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        candidates = []

        explicit = self.config.get("voice_engine", {}).get("ffmpeg_dll_dir")
        if explicit:
            candidates.append(explicit)
            candidates.append(os.path.join(root, explicit))

        env = os.environ.get("ULTRON_FFMPEG_DLL_DIR")
        if env:
            candidates.append(env)

        for d in candidates:
            if d and os.path.isdir(d) and any(
                f.lower().endswith(".dll")
                and f.lower().startswith(("avcodec", "avformat"))
                for f in os.listdir(d)
            ):
                self._prepend_dll_dir(d)
                return

    def _prepend_dll_dir(self, d: str) -> None:
        try:
            os.add_dll_directory(d)  # type: ignore[attr-defined]
        except Exception:
            pass
        path = os.environ.get("PATH", "")
        if d not in path.split(os.pathsep):
            os.environ["PATH"] = d + os.pathsep + path

    # ------------------------------------------------------------------ #
    def prepare(self) -> None:
        import os as _os

        model_dir = self._resolve_model_dir()
        ref_rel = self.config.get("voice_engine", {}).get(
            "reference_audio", "assets/reference/ultron_reference.wav"
        )
        reference = self._resolve_reference(ref_rel)
        if not os.path.isfile(reference):
            raise FileNotFoundError("Configured XTTS reference audio is absent.")

        self._ensure_ffmpeg_dlls_on_path()

        _os.environ.setdefault("TORCH_NUM_THREADS", str(self._torch_threads))
        _os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

        import torch  # deferred: only the clone engine needs it

        threads = max(1, min(self._torch_threads, 16))
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(min(threads, 4))

        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts

        config_path = os.path.join(model_dir, "config.json")
        cfg = XttsConfig()
        cfg.load_json(config_path)

        model = Xtts.init_from_config(cfg)
        model.load_checkpoint(cfg, checkpoint_dir=model_dir, eval=True)
        device = self.config.get("voice_engine", {}).get("device", "cpu")
        model = model.to(device)
        model.eval()
        self._model = model
        self._xtts_config = cfg

        self._reference = reference
        self._ready = True
        self.is_clone = True

    # ------------------------------------------------------------------ #
    def synthesize(
        self, text: str, style_params: dict, output_path: str
    ) -> EngineResult:
        if (
            not self._ready
            or self._model is None
            or self._xtts_config is None
        ):
            raise RuntimeError("XTTS engine not prepared; call prepare() first.")
        if self._reference is None or not os.path.isfile(self._reference):
            raise FileNotFoundError("Configured XTTS reference audio is unavailable.")

        import torch  # noqa: PLC0415

        rate = float(style_params.get("rate", 1.0))
        start = time.perf_counter()

        with torch.no_grad():
            out = self._model.synthesize(
                text=text,
                config=self._xtts_config,
                speaker_wav=self._reference,
                language="en",
temperature=0.48,
                length_penalty=1.0,
                repetition_penalty=9.0,
                top_k=18,
                top_p=0.8,
                speed=rate,
            )
        elapsed = time.perf_counter() - start

        wav = out.get("wav")
        sr = int(out.get("sample_rate", self._sample_rate))
        if wav is None:
            raise RuntimeError("XTTS synthesize returned no audio.")

        import torch as _t

        if hasattr(wav, "cpu"):
            wav = wav.cpu().numpy()
        if wav.ndim > 1:
            wav = wav[:, 0]
        wav = wav.astype("float64")

        import soundfile as sf

        sf.write(output_path, wav, min(sr, self._sample_rate), subtype="PCM_16")

        return EngineResult(
            engine=self.name,
            is_clone=self.is_clone,
            output_path=output_path,
            sample_rate=self._sample_rate,
            elapsed_synth_s=round(elapsed, 3),
            text=text,
            notes=[
                "XTTS v2 (coqui/XTTS-v2) conditioned on the 30 s ULTRON reference segment",
                "temperature=0.48, top_k=18, top_p=0.8 (controlled for reproducibility)",
            ],
        )