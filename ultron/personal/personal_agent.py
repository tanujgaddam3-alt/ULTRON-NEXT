from ultron.agents.base import BaseAgent


class PersonalAssistantAgent(BaseAgent):
    """
    Main personal assistant controller.
    """

    name = "personal_assistant"

    capabilities = [
        "task_management",
        "reminders",
        "notes",
        "calendar",
        "file_management",
        "automation",
    ]

    def __init__(
        self,
        tasks=None,
        reminders=None,
        notes=None,
        calendar=None,
        files=None,
        workflows=None,
    ):

        self.tasks = tasks

        self.reminders = reminders

        self.notes = notes

        self.calendar = calendar

        self.files = files

        self.workflows = workflows

    def status(
        self,
    ):

        return {
            "agent": self.name,
            "capabilities": self.capabilities,
            "online": True,
        }
