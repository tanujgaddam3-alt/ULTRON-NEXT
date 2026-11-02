from datetime import UTC, datetime
from uuid import uuid4


class AudioFrame:
    """
    Represents captured audio.
    """

    def __init__(
        self,
        data,
    ):

        self.id = str(uuid4())

        self.data = data

        self.timestamp = datetime.now(UTC)


class MicrophoneInterface:
    """
    Handles microphone input.
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

        return {"status": "microphone_active"}

    def capture(
        self,
    ):

        if not self.active:
            raise RuntimeError("Microphone is not active.")

        return AudioFrame(data=None)

    def stop(
        self,
    ):

        self.active = False

        return {"status": "microphone_stopped"}
