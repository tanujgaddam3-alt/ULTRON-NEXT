from ultron.cognition.understanding import UnderstandingEngine
from ultron.perception.engine import PerceptionEngine


def test_command_understanding():

    perception = PerceptionEngine().perceive(
        "Open VSCode"
    )

    understanding = UnderstandingEngine().understand(
        perception
    )

    assert understanding.type.value == "command"

    assert understanding.requires_execution


def test_question_understanding():

    perception = PerceptionEngine().perceive(
        "What is FastAPI?"
    )

    understanding = UnderstandingEngine().understand(
        perception
    )

    assert understanding.requires_reasoning