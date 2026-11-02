from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class EvidenceRecord(BaseModel):
    """
    Stores collected security evidence.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    event_id: str

    identity: dict = Field(default_factory=dict)

    process_info: dict = Field(default_factory=dict)

    command_info: dict = Field(default_factory=dict)

    system_info: dict = Field(default_factory=dict)

    network_info: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
