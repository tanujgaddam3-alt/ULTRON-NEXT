"""Text normalization for the ULTRON voice pipeline.

Converts internal ULTRON strings (which may carry block labels, markdown,
telemetry tokens and literal control prefixes) into clean spoken prose.

Rules (all configurable under ``text_normalization``):

* strip the block label tags ULTRON uses in ticker logs (``[CORE]``, ``[NAV]`` …);
* remove markdown markup (``**bold**``, ``*em*``, backticks, hash headers);
* expand typical telemetry shorthand ("P&L", "LUFS", "BRIEF") only at the
  punctuation level — no invented numbers;
* collapse whitespace and trim sentence fragments;
* clean quotes and dashes for TTS-safe output.
"""

from __future__ import annotations

import json
import os
import re


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize(text: str, cfg: dict | None = None) -> str:
    """Return a clean spoken version of ``text``."""
    if not text:
        return ""
    cfg = cfg or {}
    norm = cfg.get("text_normalization", {})

    if norm.get("strip_markdown", True):
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
        text = re.sub(r"^#{1,6}\s*$", "", text, flags=re.M)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    if norm.get("remove_block_labels", []):
        labels = norm.get("remove_block_labels")
        for lab in labels:
            text = re.sub(rf"\[{re.escape(lab)}\]", " ", text, flags=re.I)
    text = re.sub(r"\[([A-Za-z0-9_.-]{1,12})\]", " ", text)

    # unify dashes/quotes to safe spoken characters
    text = text.replace("—", " - ").replace("–", " - ").replace("…", ".")
    text = text.replace('"', "").replace("'", "'")

    # telemetry tokens -> neutral phrasing (never invent values)
    text = re.sub(r"\bP&L\b", "profit and loss", text)
    text = re.sub(r"\bLUFS\b", "loudness unit", text)
    text = re.sub(r"\bBRIEF\b", "brief", text)

    if norm.get("collapse_whitespace", True):
        text = re.sub(r"\s+", " ", text).strip()

    # ensure terminal punctuation for a declarative TTS read
    if text and text[-1] not in ".!?;":
        text += "."
    return text