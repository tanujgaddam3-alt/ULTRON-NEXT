from ultron.domain.perception import Perception

from .classifier import IntentClassifier
from .extractor import EntityExtractor
from .parser import InputParser


class PerceptionEngine:
    def __init__(self):

        self.parser = InputParser()

        self.classifier = IntentClassifier()

        self.extractor = EntityExtractor()

    def perceive(self, text: str) -> Perception:

        perception = self.parser.parse(text)

        intent, confidence = self.classifier.classify(text)

        entities = self.extractor.extract(text)

        return perception.model_copy(
            update={
                "intent": intent,
                "confidence": confidence,
                "entities": entities,
            }
        )
