from ultron.infrastructure.container import container


def test_singleton_services():

    assert container.memory is container.memory

    assert container.blackboard is container.blackboard

    assert container.event_bus is container.event_bus