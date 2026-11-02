from datetime import UTC, datetime


class OCRResult:
    """
    Represents extracted text.
    """

    def __init__(
        self,
        text: str,
    ):

        self.text = text

        self.timestamp = datetime.now(UTC)


class OCRProcessor:
    """
    Extracts text from images.
    """

    def __init__(
        self,
        engine=None,
    ):

        self.engine = engine

    def extract(
        self,
        image,
    ):

        if self.engine:
            text = self.engine.read(image)

        else:
            text = ""

        return OCRResult(text)
