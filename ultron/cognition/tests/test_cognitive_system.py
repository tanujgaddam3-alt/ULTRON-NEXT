from ultron.cognition.memory.experience_logger import (
    ExperienceLogger,
)
from ultron.cognition.monitoring.metrics_collector import (
    MetricsCollector,
)
from ultron.cognition.optimization.alignment_monitor import (
    AlignmentMonitor,
)
from ultron.cognition.optimization.optimization_engine import (
    OptimizationEngine,
)
from ultron.cognition.reflection.reflection_engine import (
    ReflectionEngine,
)


def test_cognitive_feedback_loop():

    metrics = MetricsCollector()

    experience = ExperienceLogger()

    reflection = ReflectionEngine()

    optimization = OptimizationEngine()

    alignment = AlignmentMonitor()

    metric = metrics.record(
        "reasoning",
        "problem_solving",
        0.9,
    )

    exp = experience.record(
        system="reasoning",
        action="solve_problem",
        outcome="successful",
        success=True,
        score=0.9,
    )

    lesson = reflection.reflect(exp)

    improvement = optimization.analyze(lesson)

    alignment_result = alignment.evaluate(
        goal="help_user",
        behaviour="accurate_answer",
        score=0.9,
    )

    assert metric.score == 0.9

    assert exp.success is True

    assert lesson is not None

    assert improvement is not None

    assert alignment_result.score == 0.9
