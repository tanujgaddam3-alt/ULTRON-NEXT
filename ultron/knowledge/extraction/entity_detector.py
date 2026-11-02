from typing import ClassVar


class EntityDetector:
    """
    Detects important entities from text.
    """

    def __init__(
        self,
        keywords=None,
    ):

        self.keywords = keywords or []

    def add_keyword(
        self,
        keyword: str,
    ):

        self.keywords.append(keyword)

    def detect(
        self,
        text: str,
    ) -> list[str]:

        found: ClassVar = []

        text_lower = text.lower()

        for keyword in self.keywords:
            if keyword.lower() in text_lower:
                found.append(keyword)

        return found
