from typing import ClassVar


class DefinitionExtractor:
    """
    Extracts concept definitions.
    """

    def extract(
        self,
        text: str,
    ):

        definitions: ClassVar = []

        keywords = [
            "POSITION:",
            "VELOCITY:",
            "ACCELERATION:",
            "STATEMENT:",
            "DEFINITION:",
        ]

        for line in text.splitlines():
            clean = line.strip()

            if not clean:
                continue

            upper = clean.upper()

            if any(key in upper for key in keywords):
                definitions.append(clean)

        return definitions
