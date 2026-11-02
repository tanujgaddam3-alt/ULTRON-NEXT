from dataclasses import dataclass


@dataclass
class CognitiveMetrics:
    """
    Performance snapshot of one ULTRON cognitive cycle.
    """

    reasoning_confidence: float = 0.0

    decision_confidence: float = 0.0

    execution_success: bool = False

    reflection_score: float = 0.0

    learning_generated: bool = False

    def intelligence_score(self) -> float:

        execution = 1.0 if self.execution_success else 0.0

        learning = 1.0 if self.learning_generated else 0.0

        return (
            self.reasoning_confidence
            + self.decision_confidence
            + execution
            + self.reflection_score
            + learning
        ) / 5
