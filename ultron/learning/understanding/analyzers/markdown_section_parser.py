import re
from typing import ClassVar


class MarkdownSectionParser:
    """
    Parses markdown labelled sections.

    Domain independent.
    """

    def parse(
        self,
        text: str,
    ):

        sections: ClassVar = {}

        current = None

        buffer: ClassVar = []

        for line in text.splitlines():
            match = re.match(r"^\*\*(.+?)\*\*\s*:?", line.strip())

            if match:
                if current:
                    sections[current] = "\n".join(buffer).strip()

                current = match.group(1).strip().lower()

                buffer: ClassVar = []

            else:
                if current:
                    buffer.append(line)

        if current:
            sections[current] = "\n".join(buffer).strip()

        return sections
