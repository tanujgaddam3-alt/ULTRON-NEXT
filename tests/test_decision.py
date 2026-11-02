from ultron.decision.engine import DecisionEngine
from ultron.domain.action_types import ActionType

def test_highest_confidence_selected():
    engine = DecisionEngine()

    result = engine.decide(
    [
        {
            "action": ActionType.OPEN_APPLICATION,
            "confidence": 0.95,
        },
        {
            "action": ActionType.RESPOND,
            "confidence": 0.60,
        },
    ]
)


    assert result.decision is not None
    assert result.decision.action == ActionType.OPEN_APPLICATION
    assert result.decision.confidence == 0.95
    assert result.evaluated == 2