import re
from typing import ClassVar

from ultron.learning.document.structure import (
    Topic,
)


class TopicExtractor:
    """
    Extracts learning topics from chapters.
    """

    def extract(
        self,
        chapter,
    ):

        topics: ClassVar = []

        matches = re.findall(
            r"(?:^|\n)([A-Z][^\n]{3,60})",
            chapter.content,
        )

        for item in matches:
            topics.append(Topic(name=item.strip()))

        return topics
