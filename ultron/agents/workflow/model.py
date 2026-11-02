from uuid import uuid4

from pydantic import BaseModel, Field


class AgentWorkflowStep(BaseModel):
    """
    Single step in an agent workflow.
    """

    order: int

    agent: str

    objective: str

    depends_on: list[int] = Field(default_factory=list)


class AgentWorkflow(BaseModel):
    """
    Complete multi-agent workflow.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    goal: str

    steps: list[AgentWorkflowStep] = Field(default_factory=list)

    status: str = "created"
