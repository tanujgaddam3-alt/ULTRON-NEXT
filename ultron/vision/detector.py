from datetime import UTC, datetime


class DetectionResult:
    """
    Represents detected objects.
    """

    def __init__(
        self,
        objects=None,
    ):

        self.objects = objects or []

        self.timestamp = datetime.now(UTC)


class ObjectDetector:
    """
    Detects objects from processed images.
    """

    def __init__(
        self,
        model=None,
    ):

        self.model = model

    def detect(
        self,
        image,
    ):

        if self.model:
            return self.model.predict(image)

        return DetectionResult(objects=[])
