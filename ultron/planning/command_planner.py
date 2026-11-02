from ultron.domain.plan import (
    Plan,
    PlanStep,
)


class CommandPlanner:
    """
    Converts simple user commands
    into executable plans.
    """


    def create_plan(
        self,
        command: str,
    ) -> Plan:

        command = command.lower().strip()


        steps = []


        if command.startswith("open "):

            application = command.replace(
                "open ",
                "",
                1,
            ).strip()


            steps.append(
                PlanStep(
                    order=1,
                    action="open_application",
                    parameters={
                        "application": application
                    },
                )
            )


        elif "system info" in command:

            steps.append(
                PlanStep(
                    order=1,
                    action="get_system_info",
                    parameters={},
                )
            )


        elif "disk usage" in command:

            steps.append(
                PlanStep(
                    order=1,
                    action="get_disk_usage",
                    parameters={},
                )
            )


        else:

            raise ValueError(
                f"Unknown command: {command}"
            )


        return Plan(
            goal=command,
            steps=steps,
        )
