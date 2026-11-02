from ultron.agents.communication.bus import (
    AgentCommunicationBus,
)
from ultron.agents.communication.intelligence.message_router import (
    MessageRouter,
)
from ultron.agents.intelligence.adaptive_selector import (
    AdaptiveAgentSelector,
)
from ultron.agents.memory.performance_memory import (
    AgentPerformanceMemory,
)


def test_agent_evolution_system():

    memory = AgentPerformanceMemory()

    bus = AgentCommunicationBus()

    router = MessageRouter(bus)

    memory.record(
        "test_agent",
        "test_task",
        True,
    )

    message = router.send(
        "agent_a",
        "agent_b",
        "task completed",
    )

    selector = AdaptiveAgentSelector(memory)

    assert message.sender == "agent_a"

    assert memory.success_rate("test_agent") == 1.0

    assert selector.memory is not None
