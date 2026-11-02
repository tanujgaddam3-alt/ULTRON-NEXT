import re
from typing import ClassVar


class KnowledgeChunker:
    """
    Splits documents into hierarchical knowledge chunks.
    """

    def chunk(
        self,
        text: str,
    ):

        chunks: ClassVar = []

        current = {"level": 0, "title": "", "content": []}

        for line in text.splitlines():
            stripped = line.strip()

            if not stripped:
                continue

            heading = self.get_heading(stripped)

            if heading:
                if current["content"]:
                    chunks.append(self.make_chunk(current))

                current = {
                    "level": heading["level"],
                    "title": heading["title"],
                    "content": [],
                }

            else:
                current["content"].append(stripped)

        if current["content"]:
            chunks.append(self.make_chunk(current))

        return chunks

    def get_heading(
        self,
        line,
    ):

        match = re.match(r"^(#+)\s*(.+)", line)

        if not match:
            return None

        return {"level": len(match.group(1)), "title": match.group(2).strip()}

    def make_chunk(
        self,
        data,
    ):

        return {
            "level": data["level"],
            "title": data["title"],
            "content": "\n".join(data["content"]),
            "type": self.detect_type(data),
        }

    def detect_type(
        self,
        chunk,
    ):

        title = chunk["title"].lower()

        if "module" in title:
            return "topic"

        if "chapter" in title:
            return "chapter"

        if "concept" in title:
            return "concept"

        return "section"
