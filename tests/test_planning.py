from ultron.domain.decision import Decision
from ultron.planning.engine import PlanningEngine
from ultron.domain.action_types import ActionType

def test_plan_creation():

    engine = PlanningEngine()

    decision = Decision(
    action=ActionType.OPEN_APPLICATION,
    confidence=0.95,
    reason="Highest confidence",
)

    result = engine.plan(decision)

    assert result.valid
    assert result.plan.goal == ActionType.OPEN_APPLICATION
    assert len(result.plan.steps) == 1
    assert result.plan.steps[0].action == "open_application"
    assert result.plan.steps[0].parameters["application"] == "code"