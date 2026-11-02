from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class TrustedDevice(BaseModel):
    """
    Represents a trusted device.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    device_name: str

    device_id: str

    owner_id: str

    trusted: bool = True

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeviceRegistry:
    """
    Stores trusted devices.
    """

    def __init__(self):

        self.devices = []

    def register(
        self,
        device: TrustedDevice,
    ):

        self.devices.append(device)

        return device

    def verify(
        self,
        device_id: str,
    ) -> bool:

        for device in self.devices:
            if device.device_id == device_id and device.trusted:
                return True

        return False
