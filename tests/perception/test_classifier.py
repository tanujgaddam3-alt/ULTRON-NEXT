from ultron.perception.classifier import IntentClassifier


def test_improve_self_intent():
    classifier = IntentClassifier()

    intent, confidence = classifier.classify(
        "Improve yourself"
    )

    assert intent == "improve_self"
    assert confidence >= 0.95


def test_self_analysis_intent():
    classifier = IntentClassifier()

    intent, confidence = classifier.classify(
        "Analyze yourself"
    )

    assert intent == "self_analysis"
    assert confidence >= 0.95
