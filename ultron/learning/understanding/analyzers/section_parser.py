class InternalSectionParser:
    """
    Parses internal labelled sections inside concepts.
    """

    SECTION_MAP = {
        "definition": [
            "definition",
            "precise definition",
            "statement",
        ],
        "formula": [
            "equations",
            "equation",
            "formula",
            "formal expression",
        ],
        "example": [
            "worked example",
            "worked examples",
            "example:",
            "level ",
        ],
        "misconception": [
            "misconception",
        ],
        "relationship": [
            "geometric meaning",
            "relationship",
            "depends",
            "causes",
        ],
    }

    def parse(self, text):

        result = {
            "definition": [],
            "formula": [],
            "example": [],
            "misconception": [],
            "relationship": [],
        }

        current = None

        for line in text.splitlines():
            clean = line.strip()

            if not clean:
                continue

            category = self.detect_section(clean)

            if category:
                current = category

                continue

            if current:
                result[current].append(clean)

        return result

    def detect_section(self, line):

        lower = line.lower()

        for category, markers in self.SECTION_MAP.items():
            for marker in markers:
                if marker in lower:
                    return category

        return None
