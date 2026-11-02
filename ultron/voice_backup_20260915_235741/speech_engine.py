from datetime import UTC, datetime


class SpeechOutput:
    """
    Represents generated speech.
    """

    def __init__(
        self,
        text,
    ):

        self.text = text

        self.created_at = datetime.now(UTC)


class SpeechEngine:
    """
    Converts text into voice.
    """

    def __init__(
        self,
        engine=None,
    ):

        self.engine = engine

    def speak(
        self,
        text,
    ):

        if self.engine:
            self.engine.generate(text)

        return SpeechOutput(text)
