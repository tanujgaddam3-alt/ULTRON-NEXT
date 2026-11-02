"""Edge TTS engine (Microsoft Edge neural voices, no API key).

Used as the explicit, clearly-labelled fallback when the local XTTS cloning
model is unavailable or fails to load. The synthesized audio is subsequently
shaped toward the measured ULTRON vocal profile by the DSP stage, but this
engine never claims to be a voice clone.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

from .base import BaseEngine, EngineResult


class EdgeEngine(BaseEngine):
    name = "edge"
    is_clone = False

    def __init__(self, config: dict, profile: dict):
        super().__init__(config, profile)
        self._sample_rate = int(config.get("sample_rate", 24000))
        self._voice = (
            config.get("edge_engine", {}).get("voice", "en-US-ChristopherNeural")
        )
        self._rate_default = "+0%"

    def _edge_cmd(self) -> list[str]:
        return [sys.executable, "-m", "edge_tts"]

    def prepare(self) -> None:
        try:
            subprocess.run(
                [sys.executable, "-m", "edge_tts", "--list-voices"],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except Exception:
            # Listing is only a capability probe; synthesis is the authority.
            pass
        self._ready = True

    def synthesize(
        self, text: str, style_params: dict, output_path: str
    ) -> EngineResult:
        rate_pct = int(round((float(style_params.get("rate", 1.0)) - 1.0) * 100))
        rate_arg = f"{'+' if rate_pct >= 0 else ''}{rate_pct}%"
        if rate_pct == 0:
            rate_arg = "+0%"

        tmp = output_path + ".mp3"
        cmd = [
            *self._edge_cmd(),
            f"--voice={self._voice}",
            f"--rate={rate_arg}",
            f"--text={text}",
            f"--write-media={tmp}",
        ]
        start = time.perf_counter()
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"edge-tts failed: {exc.stderr.decode(errors='ignore')[:300]}"
            ) from exc
        elapsed = time.perf_counter() - start

        # Transcode MP3 -> WAV at the configured sample rate (mono, s16).
        from ..dsp import transcode_to_wav

        transcode_to_wav(tmp, output_path, self._sample_rate)
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

        return EngineResult(
            engine=self.name,
            is_clone=False,
            output_path=output_path,
            sample_rate=self._sample_rate,
            elapsed_synth_s=round(elapsed, 3),
            text=text,
            notes=[
                "edge-tts fallback (en-US-ChristopherNeural)",
                "NOT a clone of the reference speaker; DSP shapes timbre only.",
                "Requires outbound HTTPS access to Microsoft TTS endpoints.",
            ],
        )