"""Delivery styles for ULTRON voice.

The personality comes from prosody and timing, not exaggerated processing
(per the delivery directive). Every style maps to a small set of controls:

* ``rate``         - relative speaking rate (edge-tts rate % and XTTS speed)
* ``pitch_target`` - approximate target median F0 in Hz (drives shaping)
* ``pitch_range``  - allowed modulation around the target (Hz)
* ``energy``       - loudness/energy multiplier
* ``emphases``     - human-readable descriptors (documentation only)

Styles are defined in ``config/ultron_voice.json`` (``delivery.styles``).
This module only selects and validates them.
"""

from __future__ import annotations

VALID_STYLES = (
    "normal",
    "information",
    "warning",
    "critical",
    "confirmation",
    "question",
    "error",
)

DEFAULT_STYLE = "normal"


def resolve_style(name: str, config: dict) -> dict:
    """Return the style dict for ``name`` (validated and clamped).

    Unknown style names raise ``ValueError`` so a typo in configuration or a
    caller is caught instead of silently falling back to ``normal``.
    """
    styles = config.get("delivery", {}).get("styles", {})
    if name not in VALID_STYLES:
        raise ValueError(
            f"Unknown delivery style {name!r}. Valid: {', '.join(VALID_STYLES)}"
        )
    base = styles.get(name) or styles.get(DEFAULT_STYLE) or {}
    rate = float(base.get("rate", 1.0))
    rate = max(0.5, min(1.5, rate))
    energy = float(base.get("energy", 1.0))
    energy = max(0.5, min(1.5, energy))
    return {
        "name": name,
        "rate": rate,
        "pitch_target_hz": float(base.get("pitch_target_hz", 92.0)),
        "pitch_range_hz": float(base.get("pitch_range", 30.0)),
        "energy": energy,
        "emphases": base.get("emphases", []),
    }


def describe_available() -> list[str]:
    return list(VALID_STYLES)