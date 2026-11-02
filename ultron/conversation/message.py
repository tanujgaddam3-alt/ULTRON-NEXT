from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"



@dataclass
class Message:

    role: MessageRole

    content: str

    session_id: str

    timestamp: datetime = field(
        default_factory=datetime.utcnow
    )

    metadata: dict = field(
        default_factory=dict
    )


    def to_dict(self):

        return {
            "role": self.role.value,
            "content": self.content,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }
