from datetime import UTC, datetime
from uuid import uuid4


class CameraFrame:
    """
    Represents captured visual data.
    """

    def __init__(
        self,
        data,
    ):

        self.id = str(uuid4())

        self.data = data

        self.timestamp = datetime.now(UTC)


class CameraInterface:
    """
    Interface for camera input.
    """

    def __init__(
        self,
        source=None,
    ):

        self.source = source

        self.active = False

    def start(
        self,
    ):

        self.active = True

        return {"status": "camera_active"}

    def capture(
        self,
    ):

        if not self.active:
            raise RuntimeError("Camera is not active.")

        return CameraFrame(data=None)

    def stop(
        self,
    ):

        self.active = False

        return {"status": "camera_stopped"}
