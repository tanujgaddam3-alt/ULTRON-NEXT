from ultron.perception.engine import PerceptionEngine


def test_open_application():

    engine = PerceptionEngine()

    perception = engine.perceive(
        "Open VSCode"
    )

    assert perception.intent == "open_application"

    assert perception.entities["application"] == "vscode"


def test_question():

    engine = PerceptionEngine()

    perception = engine.perceive(
        "What is Python?"
    )

    assert perception.intent == "question"