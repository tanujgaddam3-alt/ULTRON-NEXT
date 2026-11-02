from datetime import UTC, datetime


class ProcessedImage:
    """
    Represents processed visual data.
    """

    def __init__(
        self,
        source,
        features=None,
    ):

        self.source = source

        self.features = features or {}

        self.created_at = datetime.now(UTC)


class VisionProcessor:
    """
    Processes image input before AI analysis.
    """

    def __init__(
        self,
    ):

        self.processed = []

    def process(
        self,
        frame,
    ):

        result = ProcessedImage(
            source=frame,
            features={
                "processed": True,
                "resolution_checked": True,
            },
        )

        self.processed.append(result)

        return result

    def history(
        self,
    ):

        return self.processed
