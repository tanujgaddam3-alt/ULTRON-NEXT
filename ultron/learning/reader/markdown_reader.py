from typing import ClassVar

from ultron.learning.reader.base import DocumentReader
from ultron.learning.reader.document import Document


class MarkdownReader(DocumentReader):
    """
    Reads Markdown files and converts
    them into ULTRON Document format.
    """

    def read(
        self,
        path: str,
    ) -> Document:

        with open(path, "r", encoding="utf-8") as file:
            content = file.read()

        sections: ClassVar = []

        current_section = None
        current_content: ClassVar = []

        for line in content.splitlines():
            if line.startswith("#"):
                if current_section:
                    sections.append(
                        {
                            "title": current_section,
                            "content": "\n".join(current_content),
                        }
                    )

                current_section = line.replace("#", "").strip()

                current_content: ClassVar = []

            else:
                current_content.append(line)

        if current_section:
            sections.append(
                {"title": current_section, "content": "\n".join(current_content)}
            )

        return Document(
            title=path,
            content=content,
            sections=sections,
            metadata={
                "type": "markdown",
                "path": path,
            },
        )
