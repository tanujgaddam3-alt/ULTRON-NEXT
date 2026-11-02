from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


class CommandLevel(str, Enum):
    INFORMATION = "level_0"

    NORMAL = "level_1"

    SYSTEM = "level_2"

    CRITICAL = "level_3"


class CommandStatus(str, Enum):
    CREATED = "created"

    APPROVED = "approved"

    EXECUTING = "executing"

    COMPLETED = "completed"

    FAILED = "failed"


class Command:
    """
    Represents an ULTRON command.
    """

    def __init__(
        self,
        action: str,
        target: str = None,
        level: CommandLevel = CommandLevel.NORMAL,
        confidence: float = 0.0,
    ):

        self.id = str(uuid4())

        self.action = action

        self.target = target

        self.level = level

        self.confidence = confidence

        self.status = CommandStatus.CREATED

        self.created_at = datetime.now(UTC)
