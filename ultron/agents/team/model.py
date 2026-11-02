from pydantic import BaseModel, Field


class AgentTeam(BaseModel):
    """
    Represents a temporary agent collaboration group.
    """

    name: str

    objective: str

    agents: list[str] = Field(default_factory=list)

    status: str = "created"
