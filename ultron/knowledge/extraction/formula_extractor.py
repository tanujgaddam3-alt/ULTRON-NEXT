import re
from typing import ClassVar


class FormulaExtractor:
    """
    Extracts mathematical formulas from
    structured curriculum concept blocks.

    Filters:
    - definitions
    - examples
    - explanations
    - normal sentences
    """

    def extract(
        self,
        text: str,
    ):

        formulas: ClassVar = []

        for line in text.splitlines():
            clean = line.strip()

            if not clean:
                continue

            # Remove markdown bullets
            clean = clean.lstrip("-• ")

            # Remove markdown symbols
            clean = re.sub(r"[*_`]", "", clean).strip()

            lower = clean.lower()

            # Skip explanation/example text
            blocked_words = [
                "example",
                "calculate",
                "find",
                "given",
                "where",
                "means",
                "defined",
                "definition",
                "property",
                "statement",
                "draw",
                "correct",
                "without",
                "object",
                "this",
                "because",
                "shows",
                "represents",
                "graph",
                "slope",
                "area",
                "relationship",
            ]

            if any(word in lower for word in blocked_words):
                continue

            # Must contain equation symbol
            if "=" not in clean:
                continue

            # Remove labels:
            # VELOCITY: v = dx/dt
            if ":" in clean:
                parts = clean.split(":", 1)

                if len(parts) == 2:
                    clean = parts[1].strip()

            # Reject incomplete equations
            if clean.startswith("="):
                continue

            # Too long means probably explanation
            if len(clean.split()) > 12:
                continue

            # Must contain mathematical operators
            if not re.search(r"[+\-*/²³√∫ΔΣ]", clean):
                continue

            # Reject normal sentence starts
            first_word = clean.split()[0].lower()

            bad_starts = [
                "the",
                "a",
                "an",
                "if",
                "when",
                "for",
                "all",
                "initial",
                "final",
                "mass",
                "time",
                "velocity",
                "acceleration",
                "position",
                "force",
            ]

            if first_word in bad_starts:
                continue

            formulas.append(clean)
            # Ignore definitions
            definition_words = [
                "velocity",
                "acceleration",
                "position",
                "slope",
                "area",
                "graph",
                "rate",
                "represents",
            ]

            if any(word in clean.lower() for word in definition_words):
                continue

        return formulas
