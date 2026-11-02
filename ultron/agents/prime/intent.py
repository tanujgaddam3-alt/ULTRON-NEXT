from pydantic import BaseModel, Field


class UserIntent(BaseModel):
    """
    Represents interpreted user command.
    """

    command: str

    goal: str

    required_capabilities: list[str] = Field(default_factory=list)

    constraints: dict = Field(default_factory=dict)

    priority: int = 1
