from pydantic import BaseModel, Field


class ObserverHealth(BaseModel):
    """
    Health status of world observers.
    """

    observers: dict[str, str] = Field(default_factory=dict)

    errors: list[str] = Field(default_factory=list)

    healthy: bool = True
