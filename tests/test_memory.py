from ultron.memory.manager import MemoryManager
from ultron.domain.perception import Perception


def test_working_memory():
    memory = MemoryManager()

    memory.working.add({"goal": "Build ULTRON"})

    state = memory.working.get()

    assert state["goal"] == "Build ULTRON"


def test_sensory_memory():
    memory = MemoryManager()

    perception = Perception(
        source="terminal",
        raw_input="Hello",
        intent="greeting",
    )

    memory.sensory.add(perception)

    assert len(memory.sensory.get()) == 1