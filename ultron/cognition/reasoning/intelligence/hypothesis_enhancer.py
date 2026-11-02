from typing import ClassVar


class HypothesisEnhancer:
    """
    Enhances generated hypotheses using
    knowledge and experience signals.
    """

    def __init__(
        self,
        experience_logger=None,
        knowledge_analyzer=None,
    ):

        self.experience_logger = experience_logger

        self.knowledge_analyzer = knowledge_analyzer

    def enhance(
        self,
        hypotheses,
        context=None,
    ):

        enhanced: ClassVar = []

        for hypothesis in hypotheses:
            score = hypothesis.confidence

            metadata = hypothesis.metadata

            metadata["enhanced"] = True

            if context:
                metadata["context_used"] = True

            hypothesis.confidence = self._adjust_confidence(score)

            enhanced.append(hypothesis)

        return enhanced

    def _adjust_confidence(
        self,
        confidence,
    ):

        confidence = min(
            max(confidence, 0),
            1,
        )

        return confidence
