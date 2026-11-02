from ultron.execution.intelligence.action_evaluator import ActionEvaluator
from ultron.execution.intelligence.capability_registry import CapabilityRegistry
from ultron.execution.intelligence.feedback.execution_feedback import ExecutionFeedback
from ultron.execution.intelligence.security.permission_intelligence import (
    PermissionIntelligence,
)


def test_execution_intelligence():

    evaluator = ActionEvaluator()

    registry = CapabilityRegistry()

    permission = PermissionIntelligence()

    feedback = ExecutionFeedback()

    registry.register(
        "test_action",
        "Testing capability",
        risk=0.1,
    )

    action = evaluator.evaluate(
        "test_action",
        risk=0.1,
        confidence=0.9,
    )

    access = permission.evaluate(
        "test_action",
        risk=0.1,
    )

    feedback.record(
        "test_action",
        True,
    )

    assert action["approved"] is True

    assert access["decision"] == "allowed"

    assert feedback.success_rate() == 1.0
