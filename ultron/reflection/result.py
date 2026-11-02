from pydantic import BaseModel, Field


class ReflectionResult(BaseModel):
    """
    Result of reflecting on an execution.
    """

    success: bool

    lesson: str

    confidence: float

    evaluation_score: float = 0.0

    quality: str = "unknown"

    recommendation: str = ""

    metadata: dict = Field(default_factory=dict)
