from typing import Any

from pydantic import Field

from .action_types import ActionType
from .base import DomainModel


class Action(DomainModel):
    """
    A single executable action.
    """

    type: ActionType

    parameters: dict[str, Any] = Field(default_factory=dict)
