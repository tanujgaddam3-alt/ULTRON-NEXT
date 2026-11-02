from typing import Any

from pydantic import BaseModel, Field

from ultron.domain.execution import ExecutionRecord


class ExecutionResult(BaseModel):
    """
    Result of executing a cognitive plan.
    """

    record: ExecutionRecord

    data: list[dict[str, Any]] = Field(
        default_factory=list
    )
