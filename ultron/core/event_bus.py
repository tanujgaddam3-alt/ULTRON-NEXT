from collections import defaultdict
from collections.abc import Callable

from ultron.domain.event import Event

EventHandler = Callable[[Event], None]


class EventBus:
    """
    Simple synchronous event bus.

    Components subscribe to event types and are notified
    whenever an event of that type is published.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type."""
        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a handler."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    def publish(self, event: Event) -> None:
        """Publish an event."""

        handlers = self._subscribers.get(event.type.value, [])

        wildcard = self._subscribers.get("*", [])

        for handler in handlers + wildcard:
            handler(event)


event_bus = EventBus()
