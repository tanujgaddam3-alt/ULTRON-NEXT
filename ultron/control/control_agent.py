from ultron.agents.base import BaseAgent


class ControlAgent(BaseAgent):
    """
    Main ULTRON device control agent.
    """

    name = "control"

    capabilities = [
        "application_control",
        "system_control",
        "automation",
        "hardware_monitoring",
    ]

    def __init__(
        self,
        parser=None,
        permission=None,
        router=None,
    ):

        self.parser = parser

        self.permission = permission

        self.router = router

    def process(
        self,
        text,
    ):

        command = self.parser.parse(text)

        decision = self.permission.check(command)

        if decision != "allow":
            return {
                "status": decision,
                "command": command.action,
            }

        return self.router.route(command)
