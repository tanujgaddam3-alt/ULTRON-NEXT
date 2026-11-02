"""Conservative context-aware delivery profiles for spoken dialogue."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class DeliveryProfile(str, Enum):
    NEUTRAL = "neutral"
    CALM = "calm"
    CURIOUS = "curious"
    ASSERTIVE = "assertive"
    INTENSE = "intense"
    SOMBER = "somber"


@dataclass(frozen=True)
class ExpressionSegment:
    text: str
    profile: DeliveryProfile
    rate: float
    energy: float
    pause_after_s: float


_PROFILE_TARGETS = {
    DeliveryProfile.NEUTRAL: (1.0, 1.0, 0.18),
    DeliveryProfile.CALM: (0.97, 0.98, 0.24),
    DeliveryProfile.CURIOUS: (0.98, 1.0, 0.22),
    DeliveryProfile.ASSERTIVE: (0.96, 1.04, 0.16),
    DeliveryProfile.INTENSE: (0.92, 1.08, 0.12),
    DeliveryProfile.SOMBER: (0.94, 0.96, 0.28),
}
_MAX_RATE_STEP = 0.04
_MAX_ENERGY_STEP = 0.04
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

_INTENSE = re.compile(
    r"\b(?:urgent|urgently|emergency|immediately|critical|danger|"
    r"under attack|act now|time-sensitive|life-threatening)\b",
    re.IGNORECASE,
)
_SOMBER = re.compile(
    r"\b(?:i am sorry|i'm sorry|regret|tragic|loss|grief|"
    r"unfortunate|died|death|my condolences)\b",
    re.IGNORECASE,
)
_ASSERTIVE = re.compile(
    r"\b(?:must|will not|cannot allow|do not proceed|confirmed|"
    r"i will handle|i have decided|proceed now|this is required)\b",
    re.IGNORECASE,
)
_CURIOUS = re.compile(
    r"\b(?:i wonder|i am curious|i'm curious|could you explain|"
    r"the reason|why this|how this|what caused|let us examine|"
    r"let's examine)\b",
    re.IGNORECASE,
)
_CALM = re.compile(
    r"\b(?:take a moment|there is no need to rush|no need to rush|"
    r"you are safe|i understand|we can work through|"
    r"remain calm|steady breathing)\b",
    re.IGNORECASE,
)
_CONTEXT_DISTRESS = re.compile(
    r"\b(?:worried|afraid|scared|panic|panicking|distressed|"
    r"overwhelmed|frustrated|upset|emergency|urgent)\b",
    re.IGNORECASE,
)
_CONTEXT_QUESTION = re.compile(
    r"\b(?:why|how|what caused|can you explain|could you explain|"
    r"help me understand)\b",
    re.IGNORECASE,
)
_REASSURING = re.compile(
    r"\b(?:i understand|we can|i can help|you are safe|"
    r"take a moment|there is no need to rush)\b",
    re.IGNORECASE,
)
_EXPLANATORY = re.compile(
    r"\b(?:because|the reason|this is due to|to explain|"
    r"what happened is|the cause is)\b",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\b(?:do|stop|evacuate|disconnect|shut down|move|"
    r"follow|check|confirm|secure|act|proceed)\b",
    re.IGNORECASE,
)


def split_dialogue(text: str) -> list[str]:
    """Split at sentence boundaries without removing or rewriting any text."""
    if not text or not text.strip():
        return []
    return [part for part in _SENTENCE_BOUNDARY.split(text) if part.strip()]


def classify_segment(text: str, context: str = "") -> DeliveryProfile:
    """Classify a sentence using its meaning and the user's conversational cue."""
    if not isinstance(text, str):
        return DeliveryProfile.NEUTRAL
    if not isinstance(context, str):
        context = ""
    if _INTENSE.search(text):
        return DeliveryProfile.INTENSE
    if _SOMBER.search(text):
        return DeliveryProfile.SOMBER
    if _ASSERTIVE.search(text):
        return DeliveryProfile.ASSERTIVE
    if _CURIOUS.search(text) or (
        context and _CONTEXT_QUESTION.search(context) and _EXPLANATORY.search(text)
    ):
        return DeliveryProfile.CURIOUS
    if _CALM.search(text) or (
        context and _CONTEXT_DISTRESS.search(context) and _REASSURING.search(text)
    ):
        return DeliveryProfile.CALM
    if context and _CONTEXT_DISTRESS.search(context) and _ACTION.search(text):
        return DeliveryProfile.ASSERTIVE
    return DeliveryProfile.NEUTRAL


def _step_toward(current: float, target: float, maximum_step: float) -> float:
    difference = target - current
    if abs(difference) <= maximum_step:
        return target
    return current + (maximum_step if difference > 0 else -maximum_step)


def express_dialogue(text: str, context: str = "") -> list[ExpressionSegment]:
    """Return sentence segments with restrained, smoothed supported controls."""
    if not isinstance(text, str):
        return []
    sentences = split_dialogue(text)
    if not sentences:
        return []

    segments: list[ExpressionSegment] = []
    current_rate, current_energy, _ = _PROFILE_TARGETS[DeliveryProfile.NEUTRAL]
    for sentence in sentences:
        profile = classify_segment(sentence, context)
        target_rate, target_energy, pause = _PROFILE_TARGETS[profile]
        current_rate = _step_toward(current_rate, target_rate, _MAX_RATE_STEP)
        current_energy = _step_toward(
            current_energy, target_energy, _MAX_ENERGY_STEP
        )
        segments.append(
            ExpressionSegment(
                text=sentence,
                profile=profile,
                rate=current_rate,
                energy=current_energy,
                pause_after_s=pause,
            )
        )
    return segments
