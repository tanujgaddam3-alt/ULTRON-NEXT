from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class DefenseRule(BaseModel):
    """
    Represents an adaptive security rule.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    threat_pattern: str

    action: str

    enabled: bool = True

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DefenseRuleEngine:
    """
    Stores and applies learned defense rules.
    """

    def __init__(self):

        self.rules = []

    def create_rule(
        self,
        threat_pattern: str,
        action: str,
    ):

        rule = DefenseRule(
            threat_pattern=threat_pattern,
            action=action,
        )

        self.rules.append(rule)

        return rule

    def match(
        self,
        pattern: str,
    ):

        return [
            rule
            for rule in self.rules
            if (rule.threat_pattern == pattern and rule.enabled)
        ]
