from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ThreatKnowledge(BaseModel):
    """
    Stores learned threat information.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    pattern: str

    category: str

    severity: str

    occurrences: int = 1

    last_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ThreatIntelligence:
    """
    Maintains threat knowledge.
    """

    def __init__(self):

        self.knowledge = []

    def learn(
        self,
        pattern: str,
        category: str,
        severity: str,
    ):

        for threat in self.knowledge:
            if threat.pattern == pattern:
                threat.occurrences += 1

                threat.last_seen = datetime.now(UTC)

                return threat

        threat = ThreatKnowledge(
            pattern=pattern,
            category=category,
            severity=severity,
        )

        self.knowledge.append(threat)

        return threat

    def search(
        self,
        pattern: str,
    ):

        return [threat for threat in self.knowledge if threat.pattern == pattern]
