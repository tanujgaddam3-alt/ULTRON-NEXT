from ultron.knowledge.models import (
    KnowledgeItem,
)


class ValidationResult:
    """
    Result of knowledge validation.
    """

    def __init__(
        self,
        accepted: bool,
        confidence: float,
        reason: str,
    ):

        self.accepted = accepted
        self.confidence = confidence
        self.reason = reason


class KnowledgeValidator:
    """
    Validates knowledge before storage.
    """

    def __init__(
        self,
        minimum_confidence: float = 0.5,
    ):

        self.minimum_confidence = minimum_confidence

    def validate(
        self,
        item: KnowledgeItem,
    ) -> ValidationResult:
        """
        Check knowledge quality.
        """

        if not item.content.strip():
            return ValidationResult(
                False,
                0.0,
                "Empty knowledge content.",
            )

        if item.confidence < self.minimum_confidence:
            return ValidationResult(
                False,
                item.confidence,
                "Confidence below threshold.",
            )

        return ValidationResult(
            True,
            item.confidence,
            "Knowledge accepted.",
        )
