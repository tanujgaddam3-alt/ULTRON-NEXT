import re
from typing import ClassVar

from ultron.learning.document.structure import (
    Chapter,
)


class ChapterDetector:
    """
    Detects chapters from extracted text.
    """

    def detect(
        self,
        text: str,
    ):

        chapters: ClassVar = []

        matches = re.findall(
            r"(Chapter\s+\d+.*)",
            text,
            re.IGNORECASE,
        )

        for item in matches:
            chapters.append(Chapter(name=item.strip()))

        return chapters
