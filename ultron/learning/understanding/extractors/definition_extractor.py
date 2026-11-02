import re
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class DefinitionCandidate:
    """
    Represents extracted definition knowledge.
    """

    text: str

    confidence: float

    source_section: str = ""

    metadata: dict = field(default_factory=dict)


class DefinitionExtractor:
    """
    Universal Definition Extractor.

    Works across domains:
    - science
    - programming
    - history
    - mathematics
    - literature
    - technical documents

    It does not learn subjects.
    It detects definition patterns.
    """

    DEFINITION_PATTERNS = [
        # Explicit definitions
        r"\bis defined as\b",
        r"\bis known as\b",
        r"\bis called\b",
        r"\bis referred to as\b",
        r"\brefers to\b",
        # General definitions
        r"\bis a\b",
        r"\bis an\b",
        r"\bis the\b",
        # Meaning patterns
        r"\bmeans\b",
        r"\bdescribes\b",
        r"\brepresents\b",
    ]

    IGNORE_PREFIXES = [
        "example:",
        "examples:",
        "note:",
        "warning:",
        "correct:",
        "wrong:",
        "answer:",
        "solution:",
    ]

    QUESTION_STARTERS = [
        "what",
        "why",
        "how",
        "when",
        "where",
        "which",
    ]

    def extract(
        self,
        document,
    ) -> list[DefinitionCandidate]:

        results: ClassVar = []

        seen = set()

        for section in document.sections:
            title = section.get("title", "")

            content = section.get("content", "")

            sentences = self._split_sentences(content)

            for sentence in sentences:
                clean = sentence.strip()

                if not self._valid_candidate(clean):
                    continue

                confidence = self._calculate_confidence(clean)

                if confidence < 0.5:
                    continue

                key = clean.lower()

                if key in seen:
                    continue

                seen.add(key)

                results.append(
                    DefinitionCandidate(
                        text=clean,
                        confidence=confidence,
                        source_section=title,
                        metadata={"type": "definition"},
                    )
                )

        return results

    def _split_sentences(
        self,
        text: str,
    ):

        return re.split(r"(?<=[.!?])\s+", text)

    def _valid_candidate(
        self,
        text: str,
    ) -> bool:

        if not text:
            return False

        lower = text.lower()

        # Remove questions

        if text.endswith("?"):
            return False

        if any(lower.startswith(word + " ") for word in self.QUESTION_STARTERS):
            return False

        # Remove labels

        if any(lower.startswith(prefix) for prefix in self.IGNORE_PREFIXES):
            return False

        # Too short

        if len(text.split()) < 4:
            return False

        return any(re.search(pattern, lower) for pattern in self.DEFINITION_PATTERNS)

    def _calculate_confidence(
        self,
        text: str,
    ) -> float:

        score = 0.5

        lower = text.lower()

        if "is defined as" in lower:
            score += 0.25

        if "refers to" in lower:
            score += 0.20

        if "means" in lower:
            score += 0.15

        if len(text.split()) > 8:
            score += 0.10

        if ":" in text:
            score += 0.05

        return min(score, 1.0)
