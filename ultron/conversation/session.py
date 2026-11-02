from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ConversationSession:

    session_id: str

    user_id: str | None = None

    created_at: datetime = field(
        default_factory=datetime.utcnow
    )

    context: dict = field(
        default_factory=dict
    )

    active: bool = True


    def update_context(
        self,
        key,
        value
    ):

        self.context[key] = value


    def close(self):

        self.active = False
