from ultron.smith.models.metrics import (
    CognitiveMetrics,
)


class CognitiveMetricsCollector:
    """
    Extracts performance metrics
    from cognitive cycles.
    """

    def collect(
        self,
        cycle,
    ) -> CognitiveMetrics:

        reasoning_confidence = 0.0

        if cycle.reasoning:
            reasoning_confidence = (
                cycle.reasoning.confidence
                if hasattr(
                    cycle.reasoning,
                    "confidence",
                )
                else 0.0
            )

        decision_confidence = 0.0

        if cycle.decision:
            decision = (
                cycle.decision.decision
                if hasattr(
                    cycle.decision,
                    "decision",
                )
                else cycle.decision
            )

            if decision:
                decision_confidence = decision.confidence

        execution_success = False

        if cycle.execution:
           execution_success = (
        cycle.execution.record.success
    )
        learning_generated = cycle.learning is not None

        return CognitiveMetrics(
            reasoning_confidence=reasoning_confidence,
            decision_confidence=decision_confidence,
            execution_success=execution_success,
            learning_generated=learning_generated,
        )
