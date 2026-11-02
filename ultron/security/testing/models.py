from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class SecurityTestResult(BaseModel):
    """
    Result of a security test.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    test_name: str

    passed: bool

    message: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
