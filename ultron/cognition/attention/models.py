from __future__ import annotations

from pydantic import Field

from ultron.domain.base import DomainModel


class AttentionItem(DomainModel):
    """
    Represents something competing
    for cognitive attention.
    """

    source: str

    identifier: str

    score: float

    metadata: dict[str, object] = Field(default_factory=dict)
