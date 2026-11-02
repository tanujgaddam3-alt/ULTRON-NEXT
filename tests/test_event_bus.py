from ultron.core.event_bus import EventBus
from ultron.domain.event import Event, EventType


def test_event_bus_publish():
    bus = EventBus()
    received = []

    def handler(event: Event):
        received.append(event)

    bus.subscribe(EventType.USER_MESSAGE.value, handler)

    event = Event(
        type=EventType.USER_MESSAGE,
        source="test",
        payload={"text": "Hello ULTRON"},
    )

    bus.publish(event)

    assert len(received) == 1
    assert received[0].payload["text"] == "Hello ULTRON"