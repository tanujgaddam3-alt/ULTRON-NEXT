from typing import ClassVar


class QuestionAnalyzer:
    """
    Extracts intent and concepts from questions.
    """

    def analyze(self, question):

        text = question.lower().strip()

        intent = self.detect_intent(text)

        concepts = self.extract_concepts(text)

        return {"intent": intent, "concepts": concepts, "question": question}

    def detect_intent(self, text):

        if "why" in text:
            return "reason"

        if "how" in text:
            return "explain"

        if "what" in text:
            return "definition"

        return "lookup"

    def extract_concepts(self, text):

        stop_words = [
            "what",
            "why",
            "how",
            "does",
            "do",
            "is",
            "are",
            "the",
            "a",
            "an",
            "of",
            "to",
            "and",
            "on",
            "change",
            "affect",
        ]

        words = text.replace("?", "").split()

        concepts: ClassVar = []

        for word in words:
            if word not in stop_words:
                concepts.append(word.capitalize())

        return concepts
