from ultron.cognition.reasoning.pipeline import (
    ReasoningPipeline,
)


class DummyStage:

    def run(self, state):
        state["count"] += 1
        return state


def test_pipeline():

    pipeline = ReasoningPipeline()

    pipeline.add_stage(DummyStage())

    pipeline.add_stage(DummyStage())

    result = pipeline.run(
        {"count": 0}
    )

    assert result["count"] == 2