from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradingControl:
    """
    Operational control for enabling or pausing trading.

    This is intentionally independent of strategy and market logic.
    """

    _paused: bool = False
    _reason: str | None = None

    def pause(self, reason: str = "Trading manually paused.") -> None:
        self._paused = True
        self._reason = reason

    def resume(self) -> None:
        self._paused = False
        self._reason = None

    def is_paused(self) -> bool:
        return self._paused

    def status(self) -> dict[str, object]:
        return {
            "paused": self._paused,
            "reason": self._reason,
        }

    def validate(self) -> dict[str, object]:
        if self._paused:
            return {
                "valid": False,
                "paused": True,
                "reason": self._reason
                or "Trading is manually paused.",
            }

        return {
            "valid": True,
            "paused": False,
            "reason": "Trading is active.",
        }
