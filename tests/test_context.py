from ultron.domain.context import CognitiveContext


def test_context_creation():

    context = CognitiveContext()

    assert context.perception is None
    assert context.understanding is None
    assert context.decision is None
    assert context.plan is None
    assert context.execution_result is None