from datetime import UTC, datetime
from uuid import uuid4


class CalendarEvent:
    """
    Represents a calendar event.
    """

    def __init__(
        self,
        title: str,
        start_time,
        end_time=None,
        description="",
    ):

        self.id = str(uuid4())

        self.title = title

        self.start_time = start_time

        self.end_time = end_time

        self.description = description

        self.created_at = datetime.now(UTC)


class CalendarManager:
    """
    Manages user calendar events.
    """

    def __init__(
        self,
    ):

        self.events = []

    def create_event(
        self,
        title,
        start_time,
        end_time=None,
        description="",
    ):

        event = CalendarEvent(
            title,
            start_time,
            end_time,
            description,
        )

        self.events.append(event)

        return event

    def upcoming(
        self,
    ):

        return sorted(
            self.events,
            key=lambda event: event.start_time,
        )
