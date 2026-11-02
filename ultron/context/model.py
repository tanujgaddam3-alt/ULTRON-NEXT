from pydantic import BaseModel, Field


class ContextState(BaseModel):
    """
    Represents ULTRON's current awareness.
    """

    user_input: str = ""

    current_goal: str = ""

    conversation: list[str] = Field(default_factory=list)

    memories: list[dict] = Field(default_factory=list)

    environment: dict = Field(default_factory=dict)

    metadata: dict = Field(default_factory=dict)
