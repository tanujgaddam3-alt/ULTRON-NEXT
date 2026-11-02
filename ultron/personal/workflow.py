from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4


class Workflow:
    """
    Represents an automated workflow.
    """

    def __init__(
        self,
        name: str,
        actions=None,
    ):

        self.id = str(uuid4())

        self.name = name

        self.actions = actions or []

        self.created_at = datetime.now(UTC)


class WorkflowEngine:
    """
    Executes user workflows.
    """

    def __init__(
        self,
    ):

        self.workflows = []

    def create(
        self,
        name,
        actions,
    ):

        workflow = Workflow(
            name,
            actions,
        )

        self.workflows.append(workflow)

        return workflow

    def execute(
        self,
        workflow,
    ):

        results: ClassVar = []

        for action in workflow.actions:
            results.append(
                {
                    "action": action,
                    "status": "completed",
                }
            )

        return results

    def list_workflows(
        self,
    ):

        return self.workflows
