from pydantic import BaseModel, Field


class Workflow(BaseModel):
    """
    Represents a multi-agent execution plan.
    """

    goal: str

    agents: list[str] = Field(default_factory=list)

    steps: list[str] = Field(default_factory=list)

    status: str = "created"
