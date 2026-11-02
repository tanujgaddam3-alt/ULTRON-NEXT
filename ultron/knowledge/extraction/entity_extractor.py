from ultron.knowledge.extraction.entity_detector import EntityDetector


class EntityExtractor:
    """
    Pipeline compatible entity extractor.
    """

    def __init__(
        self,
        detector: EntityDetector,
    ):
        self.detector = detector

    def extract(
        self,
        text: str,
    ):

        return self.detector.detect(text)
