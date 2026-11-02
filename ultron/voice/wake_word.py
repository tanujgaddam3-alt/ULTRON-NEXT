from datetime import UTC, datetime


class WakeWordResult:
    """
    Represents wake detection result.
    """

    def __init__(
        self,
        detected: bool,
        word: str = None,
    ):

        self.detected = detected

        self.word = word

        self.timestamp = datetime.now(UTC)


class WakeWordDetector:
    """
    Detects ULTRON activation word.
    """

    def __init__(
        self,
        wake_word="ultron",
    ):

        self.wake_word = wake_word.lower()

    def detect(
        self,
        text,
    ):

        detected = self.wake_word in text.lower()

        return WakeWordResult(
            detected,
            self.wake_word if detected else None,
        )
