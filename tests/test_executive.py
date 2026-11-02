from ultron.executive.executive import Executive


def test_executive_pipeline():

    executive = Executive()

    context = executive.process(
        "Open VSCode"
    )

    assert context.perception is not None

    assert context.understanding is not None

    assert (
        context.understanding.requires_execution
    )

    assert executive.state.value == "idle"