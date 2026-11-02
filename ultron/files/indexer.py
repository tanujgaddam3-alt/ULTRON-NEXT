from pathlib import Path
from typing import ClassVar


class FileContentIndexer:
    """
    Indexes file contents for intelligent retrieval.
    """

    def __init__(self):

        self._index = {}

    def index(
        self,
        path: str,
    ) -> dict:

        file = Path(path)

        if not file.exists():
            raise FileNotFoundError(path)

        content = file.read_text(encoding="utf-8")

        self._index[path] = {
            "content": content,
            "words": set(content.lower().split()),
        }

        return {
            "success": True,
            "path": path,
            "characters": len(content),
        }

    def search(
        self,
        query: str,
    ) -> list:

        query = query.lower()

        results: ClassVar = []

        for path, data in self._index.items():
            if query in data["content"].lower():
                results.append(
                    {
                        "path": path,
                        "match": True,
                    }
                )

        return results

    def remove(
        self,
        path: str,
    ):

        self._index.pop(
            path,
            None,
        )
