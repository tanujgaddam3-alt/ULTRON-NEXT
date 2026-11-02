from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentTask(BaseModel):
    """
    Task assigned to an agent.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    goal: str

    required_capabilities: list[str] = Field(default_factory=list)

    priority: int = 1

    constraints: dict = Field(default_factory=dict)

    context: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentResult(BaseModel):
    """
    Result produced by an agent.
    """

    success: bool

    agent: str

    output: str

    capabilities_used: list[str] = Field(default_factory=list)

    metadata: dict = Field(default_factory=dict)
