from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ThreatMemory(BaseModel):
    """
    Stores learned security patterns.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    pattern: str

    defense: str

    occurrences: int = 1

    last_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ImmuneMemory:
    """
    Digital immune threat memory.
    """

    def __init__(self):

        self.memory = []

    def remember(
        self,
        pattern: str,
        defense: str,
    ):

        for item in self.memory:
            if item.pattern == pattern:
                item.occurrences += 1

                item.last_seen = datetime.now(UTC)

                return item

        record = ThreatMemory(
            pattern=pattern,
            defense=defense,
        )

        self.memory.append(record)

        return record

    def find(
        self,
        pattern: str,
    ):

        return [item for item in self.memory if item.pattern == pattern]
