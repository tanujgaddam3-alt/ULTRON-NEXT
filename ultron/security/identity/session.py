from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class UserSession(BaseModel):
    """
    Represents an active user session.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    user_id: str

    device_id: str

    active: bool = True

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    last_activity: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionManager:
    """
    Creates and manages user sessions.
    """

    def __init__(self):

        self.sessions = []

    def create(
        self,
        user_id: str,
        device_id: str,
    ):

        session = UserSession(
            user_id=user_id,
            device_id=device_id,
        )

        self.sessions.append(session)

        return session

    def validate(
        self,
        session_id: str,
    ) -> bool:

        for session in self.sessions:
            if session.id == session_id and session.active:
                return True

        return False

    def terminate(
        self,
        session_id: str,
    ):

        for session in self.sessions:
            if session.id == session_id:
                session.active = False

                return session

        return None
