import re
from typing import ClassVar


class ConceptAnalyzer:
    """
    Converts concept chunks into structured knowledge candidates.
    """

    def analyze(
        self,
        chunk: dict,
    ):

        text = chunk.get("content", "")

        return {
            "concept": chunk.get("title", ""),
            "definitions": self.extract_definitions(text),
            "formulas": self.extract_formulas(text),
            "relationships": self.extract_relationships(text),
            "examples": self.extract_examples(text),
        }

    def extract_definitions(
        self,
        text,
    ):

        results: ClassVar = []

        for line in text.splitlines():
            clean = line.strip()

            if not clean:
                continue

            lower = clean.lower()

            # remove headings

            if clean.startswith("**"):
                continue

            if any(
                word in lower
                for word in [
                    "is defined as",
                    "refers to",
                    "is the",
                    "is a measure of",
                    "rate of change",
                ]
            ):
                results.append(clean)

        return results

    def extract_formulas(
        self,
        text,
    ):

        results: ClassVar = []

        for line in text.splitlines():
            clean = line.strip()

            # remove examples

            if any(
                word in clean.lower()
                for word in [
                    "example",
                    "object moves",
                    "find",
                    "initial",
                    "slope",
                    "area",
                ]
            ):
                continue

            # actual mathematical expressions

            if re.search(r"[A-Za-z0-9\)\]]\s*=\s*", clean):
                results.append(clean)

        return results

    def extract_relationships(
        self,
        text,
    ):

        results: ClassVar = []

        patterns = [
            "depends on",
            "proportional to",
            "causes",
            "related to",
            "increases",
            "decreases",
            "changes with",
        ]

        for line in text.splitlines():
            lower = line.lower()

            if any(p in lower for p in patterns):
                results.append(line.strip())

        return results

    def extract_examples(
        self,
        text,
    ):

        results: ClassVar = []

        capture = False

        for line in text.splitlines():
            lower = line.lower()

            if "example" in lower:
                capture = True
                continue

            if capture:
                if line.strip():
                    results.append(line.strip())

        return results
