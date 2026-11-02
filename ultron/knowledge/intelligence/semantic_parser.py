import re
from typing import ClassVar

from ultron.knowledge.models.knowledge_unit import KnowledgeUnit


class SemanticParser:
    def parse(self, text):

        knowledge: ClassVar = []

        sentences = self.split(text)

        for sentence in sentences:
            result = self.extract(sentence)

            if result:
                knowledge.append(result)

        return knowledge

    def split(self, text):

        return [s.strip() for s in re.split(r"[.!?\n]", text) if len(s.strip()) > 20]

    def extract(self, sentence):

        clean = sentence.strip()

        patterns = [
            (" is ", "describes"),
            (" are ", "describes"),
            (" causes ", "causes"),
            (" produces ", "produces"),
            (" depends on ", "depends on"),
            (" proportional to ", "proportional to"),
            (" equals ", "equals"),
            (" represents ", "represents"),
            (" converts ", "converts"),
            (" measures ", "measures"),
        ]

        lower = clean.lower()

        for pattern, relation in patterns:
            if pattern in lower:
                parts = lower.split(pattern, 1)

                source = self.normalize_concept(parts[0])

                target = self.normalize_concept(parts[1])

                if self.valid(source, target):
                    return KnowledgeUnit(
                        concept=source,
                        relationships=[f"{source} {relation} {target}"],
                        source="semantic_parser",
                    )

        return None

    def clean(self, text):

        text = text.strip()

        text = re.sub(r"[*#:`]", "", text)

        return text.title()

    def valid(self, source, target):
        bad_words = ["these", "this", "where", "both", "why", "if"]

        for word in bad_words:
            if source.lower().startswith(word):
                return False

        if len(source) < 3:
            return False

        if len(target) < 3:
            return False

        if len(source.split()) > 6:
            return False

        if len(target.split()) > 8:
            return False

        return True

    def normalize_concept(self, text):

        replacements = {
            "the net force on an object": "Force",
            "its mass times acceleration": "Mass × Acceleration",
            "mass times acceleration": "Mass × Acceleration",
            "rate of change of velocity": "Acceleration",
            "rate of change": "Derivative",
            "kinetic energy": "Kinetic Energy",
            "average kinetic energy of particles": "Temperature",
        }

        clean = text.lower().strip()

        for key, value in replacements.items():
            if key in clean:
                return value

        return self.clean(text)
