from ultron.application.runtime import (
    CognitiveRuntime,
    RuntimeEvent,
)
def __init__(
    self,
    cognitive_service=None,
    perception_engine=None,
    understanding_engine=None,
    context_engine=None,
    smith_service=None,
):

 def test_runtime_creates_cycle():

    runtime = CognitiveRuntime()

    event = RuntimeEvent(
        source="user",
        payload="hello",
    )

    cycle = runtime.run_cycle(event)

    assert cycle.event.payload == "hello"

    assert runtime.state.cycle_count == 1

    assert runtime.state.last_cycle is cycle