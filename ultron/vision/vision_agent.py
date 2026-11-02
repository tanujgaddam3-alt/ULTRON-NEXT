from typing import ClassVar

from ultron.agents.base import BaseAgent


class VisionAgent(BaseAgent):
    """
    Agent responsible for visual intelligence.
    """

    name = "vision"

    capabilities = [
        "image_analysis",
        "object_detection",
        "text_extraction",
        "visual_memory",
    ]

    def __init__(
        self,
        processor=None,
        detector=None,
        ocr=None,
        memory=None,
    ):

        self.processor = processor

        self.detector = detector

        self.ocr = ocr

        self.memory = memory

    def analyze(
        self,
        image,
    ):

        processed = None

        if self.processor:
            processed = self.processor.process(image)

        objects: ClassVar = []

        if self.detector:
            objects = self.detector.detect(processed)

        text = None

        if self.ocr:
            text = self.ocr.extract(processed)

        if self.memory:
            self.memory.remember(
                {
                    "objects": objects,
                    "text": text,
                }
            )

        return {
            "objects": objects,
            "text": text,
        }
