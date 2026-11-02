"""ULTRON Voice Pipeline.

    TEXT
      -> normalize.normalize()
      -> engine.synthesize()            (XTTS clone primary / edge fallback)
      -> pipeline sync timing
      -> dsp.process_file()             (profile-based shaping + loudness)
      -> FINAL AUDIO (wav, 24 kHz mono, PCM16)

The pipeline is deliberately engine-agnostic: the caller supplies the engine
via the factory. See ``voice/tts/__init__.py``.

Example
-------
    >>> from voice.pipeline import VoicePipeline
    >>> p = VoicePipeline(engine_name="xtts", config=..., profile=...)
    >>> result = p.speak("Systems are operational.", "normal", "/tmp/out.wav")
"""

from __future__ import annotations

import json
import os
import time

from .normalize import normalize
from .styles import resolve_style
from .tts import create_engine


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_config(config=None) -> dict:
    if config is None:
        default = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
            "ultron_voice.json",
        )
        if os.path.isfile(default):
            config = _load(default)
        else:
            config = {}
    return config


def resolve_profile(profile=None) -> dict:
    if profile is None:
        default = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
            "voice_profile.json",
        )
        if os.path.isfile(default):
            profile = _load(default)
        else:
            profile = {}
    return profile


class Result:
    """Outcome of a pipeline speak() call."""

    def __init__(
        self, path, sample_rate, engine, is_clone, elapsed, text, notes=None,
        timings=None,
    ):
        self.path = path
        self.sample_rate = sample_rate
        self.engine = engine
        self.is_clone = is_clone
        self.elapsed_s = elapsed
        self.text = text
        self.notes = notes or []
        self.style = None
        self.timings = timings or {}

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "sample_rate": self.sample_rate,
            "engine": self.engine,
            "is_clone": self.is_clone,
            "elapsed_s": round(self.elapsed_s, 3),
            "text": self.text,
            "style": self.style,
            "notes": self.notes,
        }


class VoicePipeline:
    """Production entry point for ULTRON speech generation."""

    def __init__(self, engine_name=None, config=None, profile=None):
        self.config = resolve_config(config)
        self.profile = resolve_profile(profile)
        engine_name = engine_name or self.config.get("voice_engine", {}).get(
            "primary", "xtts"
        )
        self.engine = create_engine(engine_name, self.config, self.profile)

    # ------------------------------------------------------------------ #
    def prepare(self) -> None:
        """Ensure the engine is ready (loads the XTTS model on first call)."""
        if not self.engine.ready:
            self.engine.prepare()

    def speak(self, text, style="normal", output_path=None) -> Result:
        """Normalize -> synthesize -> process. Returns :class:`Result`."""
        t0 = time.perf_counter()
        spoken = normalize(text, self.config)
        if not spoken:
            raise ValueError("Nothing to speak after normalization.")

        style_params = resolve_style(style, self.config)
        if not self.engine.ready:
            self.engine.prepare()

        if output_path is None:
            output_path = self._scratch_output(spoken)

        synthesis_started = time.perf_counter()
        engine_result = self.engine.synthesize(spoken, style_params, output_path)
        synthesis_completed = time.perf_counter()
        self._post_process(engine_result.output_path, style_params)
        processing_completed = time.perf_counter()

        res = Result(
            path=engine_result.output_path,
            sample_rate=engine_result.sample_rate,
            engine=engine_result.engine,
            is_clone=engine_result.is_clone,
            elapsed=time.perf_counter() - t0,
            text=spoken,
            notes=engine_result.notes,
            timings={
                "synthesis_start_offset_s": synthesis_started - t0,
                "synthesis_s": synthesis_completed - synthesis_started,
                "processing_s": processing_completed - synthesis_completed,
                "processing_complete_offset_s": processing_completed - t0,
            },
        )
        res.style = style_params["name"]
        return res

    def speak_dialogue(self, text, context="", output_path=None) -> Result:
        """Speak contextual sentence segments through the already-loaded engine."""
        import tempfile

        import numpy as np
        import soundfile as sf

        from .dialogue_expression import ExpressionSegment, express_dialogue

        started = time.perf_counter()
        segments = express_dialogue(text, context)
        if not segments:
            raise ValueError("Nothing to speak after normalization.")
        if not self.engine.ready:
            self.engine.prepare()
        if output_path is None:
            output_path = self._scratch_output(text)

        base_style = resolve_style("normal", self.config)
        audio_segments = []
        grouped_segments: list[ExpressionSegment] = []
        for segment in segments:
            previous = grouped_segments[-1] if grouped_segments else None
            if (
                previous
                and previous.profile is segment.profile
                and previous.rate == segment.rate
                and previous.energy == segment.energy
            ):
                grouped_segments[-1] = ExpressionSegment(
                    text=f"{previous.text} {segment.text}",
                    profile=previous.profile,
                    rate=previous.rate,
                    energy=previous.energy,
                    pause_after_s=segment.pause_after_s,
                )
            else:
                grouped_segments.append(segment)
        sample_rate = int(self.config.get("sample_rate", 24000))
        synthesis_started = None
        synthesis_duration = 0.0
        processing_duration = 0.0
        with tempfile.TemporaryDirectory(prefix="ultron_dialogue_") as work_dir:
            for index, segment in enumerate(grouped_segments):
                spoken = normalize(segment.text, self.config)
                if not spoken:
                    continue
                params = dict(base_style)
                params.update(
                    name=segment.profile.value,
                    rate=segment.rate,
                    energy=segment.energy,
                )
                segment_path = os.path.join(work_dir, f"segment_{index:04d}.wav")
                stage_started = time.perf_counter()
                if synthesis_started is None:
                    synthesis_started = stage_started
                result = self.engine.synthesize(spoken, params, segment_path)
                stage_completed = time.perf_counter()
                synthesis_duration += stage_completed - stage_started
                self._post_process(result.output_path, params)
                audio, actual_rate = sf.read(result.output_path, dtype="float64")
                processing_duration += time.perf_counter() - stage_completed
                if audio.ndim > 1:
                    audio = np.mean(audio, axis=1)
                if actual_rate != sample_rate:
                    raise ValueError(
                        f"Dialogue segment sample rate {actual_rate} does not match "
                        f"configured rate {sample_rate}."
                    )
                audio_segments.append((audio, segment.pause_after_s))

        if not audio_segments:
            raise ValueError("Nothing to speak after normalization.")

        pieces = []
        for index, (audio, pause_after_s) in enumerate(audio_segments):
            pieces.append(audio)
            if index < len(audio_segments) - 1:
                pause_samples = round(
                    sample_rate * max(0.0, min(pause_after_s, 0.3))
                )
                if pause_samples:
                    pieces.append(np.zeros(pause_samples, dtype=np.float64))
        sf.write(
            output_path,
            np.concatenate(pieces),
            sample_rate,
            subtype="PCM_16",
        )
        processing_completed = time.perf_counter()
        normalized_text = normalize(text, self.config)
        result = Result(
            path=output_path,
            sample_rate=sample_rate,
            engine=self.engine.name,
            is_clone=self.engine.is_clone,
            elapsed=time.perf_counter() - started,
            text=normalized_text,
            notes=[
                "Context-aware delivery uses sentence segmentation, bounded pauses, "
                "XTTS/engine speed, and the existing energy/loudness processing.",
                "No emotion-specific model controls or audio pitch/time effects are used.",
            ],
            timings={
                "synthesis_start_offset_s": (
                    synthesis_started - started if synthesis_started else 0.0
                ),
                "synthesis_s": synthesis_duration,
                "processing_s": processing_duration,
                "processing_complete_offset_s": processing_completed - started,
            },
        )
        result.style = "dialogue_expression"
        return result

    # ------------------------------------------------------------------ #
    def _post_process(self, wav_path: str, style_params: dict) -> None:
        from .dsp import process_file

        cfg = dict(self.config.get("processing", {}))
        # per-style energy scaling
        e = style_params.get("energy", 1.0)
        if abs(e - 1.0) > 0.01:
            loud = dict(cfg.get("loudness", {}))
            target = float(loud.get("target_lufs", -22.5))
            loud["target_lufs"] = round(target + 6.0 * (e - 1.0), 2)
            cfg["loudness"] = dict(loud)
        # per-style spectral trim: high-energy delivery tends to darken the
        # vowels; trim the low shelf a touch and lift presence to keep the
        # balance inside the measured profile envelope.
        spec = self.config.get("processing", {}).get("style_spectral", {})
        if spec.get("enabled", True) and e >= float(spec.get("min_energy", 1.1)):
            ls = dict(cfg.get("low_shelf", {}))
            ls["gain_db"] = float(ls.get("gain_db", 2.5)) - float(
                spec.get("low_shelf_trim_db", 1.0)
            )
            cfg["low_shelf"] = dict(ls)
            pp = dict(cfg.get("presence_peak", {}))
            pp["gain_db"] = float(pp.get("gain_db", 1.0)) + float(
                spec.get("presence_boost_db", 1.5)
            )
            cfg["presence_peak"] = dict(pp)
        process_file(
            wav_path,
            wav_path,
            cfg,
            target_sr=int(self.config.get("sample_rate", 24000)),
        )

    def _scratch_output(self, spoken: str) -> str:
        import tempfile
        import time
        import uuid

        stem = "".join(ch for ch in spoken[:24] if ch.isalnum() or ch == "_") or "speech"
        d = os.path.join(tempfile.gettempdir(), "ultron_voice")
        os.makedirs(d, exist_ok=True)
        unique = f"{time.monotonic_ns()}_{uuid.uuid4().hex[:8]}"
        return os.path.join(d, f"ultron_{stem}_{unique}.wav")

    def close(self) -> None:
        self.engine.close()


def available_engines() -> list[str]:
    return ["xtts", "edge"]