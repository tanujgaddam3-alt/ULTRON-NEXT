from dataclasses import dataclass


@dataclass
class PersonalConfig:
    """
    Configuration for ULTRON personal assistant.
    """

    tasks_enabled: bool = True

    reminders_enabled: bool = True

    notes_enabled: bool = True

    calendar_enabled: bool = True

    file_access_enabled: bool = True

    automation_enabled: bool = True

    max_memory_items: int = 10000

    def disable_automation(
        self,
    ):

        self.automation_enabled = False

    def enable_full_assistant(
        self,
    ):

        self.tasks_enabled = True

        self.reminders_enabled = True

        self.notes_enabled = True

        self.calendar_enabled = True

        self.file_access_enabled = True

        self.automation_enabled = True
