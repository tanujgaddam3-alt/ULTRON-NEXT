from enum import Enum

from pydantic import BaseModel


class PermissionLevel(str, Enum):
    ALLOW = "allow"

    CONFIRM = "confirm"

    DENY = "deny"


class ActionRequest(BaseModel):
    """
    Represents an action ULTRON wants to execute.
    """

    action: str

    requester: str

    risk_score: float = 0.0


class PermissionDecision(BaseModel):
    """
    Result of permission evaluation.
    """

    action: str

    permission: PermissionLevel

    reason: str
