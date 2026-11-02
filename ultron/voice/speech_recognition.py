from datetime import UTC, datetime


class SpeechResult:
    """
    Represents recognized speech.
    """

    def __init__(
        self,
        text: str,
    ):

        self.text = text

        self.timestamp = datetime.now(UTC)


class SpeechRecognizer:
    """
    Converts audio into text.
    """

    def __init__(
        self,
        engine=None,
    ):

        self.engine = engine

    def recognize(
        self,
        audio,
    ):

        if self.engine:
            text = self.engine.transcribe(audio)

        else:
            text = ""

        return SpeechResult(text)
