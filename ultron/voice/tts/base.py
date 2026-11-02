"""TTS engine abstraction for ULTRON voice.

Every engine exposes a uniform ``synthesize(text, style_params, output_path)``
contract returning an :class:`EngineResult` with timing and capability metadata.

Two engines are provided:

* :class:`XtTsEngine`  - local voice cloning via XTTS v2 (coqui/XTTS-v2).
                          Provides genuine reference-conditioned cloning.
* :class:`EdgeEngine`  - cloud (no-key) Microsoft Edge neural voices, used as a
                          fallback when the XTTS model is unavailable. Explicitly
                          NOT a clone; the DSP stage shapes its timbre toward the
                          measured profile.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class EngineResult:
    """Result of a synthesis call."""

    engine: str
    is_clone: bool
    output_path: str
    sample_rate: int
    elapsed_synth_s: float
    text: str
    notes: list[str] = field(default_factory=list)


class BaseEngine:
    """Minimum interface implemented by all ULTRON voice engines."""

    name = "base"
    is_clone = False

    def __init__(self, config: dict, profile: dict):
        self.config = config
        self.profile = profile
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def prepare(self) -> None:
        """Load models / open connections. Called before the first use."""

    def synthesize(
        self, text: str, style_params: dict, output_path: str
    ) -> EngineResult:
        raise NotImplementedError

    def close(self) -> None:  # noqa: B027 - no-op base
        return None