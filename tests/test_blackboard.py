from ultron.cognition.blackboard import Blackboard
from ultron.domain.perception import Perception


def test_blackboard_update():

    board = Blackboard()

    perception = Perception(
        source="terminal",
        raw_input="Hello",
        intent="conversation",
    )

    board.update(
        perception=perception
    )

    assert board.workspace.perception == perception