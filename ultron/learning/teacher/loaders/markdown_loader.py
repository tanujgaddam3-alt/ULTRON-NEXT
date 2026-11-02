from pathlib import Path

from .base import DocumentLoader


class MarkdownLoader(DocumentLoader):
    def load(self, path: str) -> dict:

        content = Path(path).read_text(encoding="utf-8")

        return {
            "type": "markdown",
            "source": path,
            "content": content,
        }
