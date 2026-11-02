from datetime import UTC, datetime
from uuid import uuid4


class Reminder:
    """
    Represents a reminder.
    """

    def __init__(
        self,
        message: str,
        trigger_time,
    ):

        self.id = str(uuid4())

        self.message = message

        self.trigger_time = trigger_time

        self.created_at = datetime.now(UTC)

        self.completed = False


class ReminderManager:
    """
    Manages user reminders.
    """

    def __init__(
        self,
    ):

        self.reminders = []

    def create(
        self,
        message,
        trigger_time,
    ):

        reminder = Reminder(
            message,
            trigger_time,
        )

        self.reminders.append(reminder)

        return reminder

    def complete(
        self,
        reminder_id,
    ):

        for reminder in self.reminders:
            if reminder.id == reminder_id:
                reminder.completed = True

                return reminder

        return None

    def get_pending(
        self,
    ):

        return [reminder for reminder in self.reminders if not reminder.completed]
