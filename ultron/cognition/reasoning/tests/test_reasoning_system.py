from ultron.cognition.reasoning.engine import ReasoningEngine
from ultron.cognition.reasoning.intelligence.reasoning_metrics import ReasoningMetrics


def test_reasoning_pipeline():

    engine = ReasoningEngine()

    metrics = ReasoningMetrics()

    assert engine.pipeline is not None

    metrics.record(
        confidence=0.9,
        success=True,
    )

    assert metrics.accuracy() == 1.0
