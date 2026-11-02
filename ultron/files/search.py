from typing import ClassVar

from ultron.files.repository.base import (
    FileArtifactRepository,
)


class FileSearchEngine:
    """
    Searches ULTRON artifacts.
    """

    def __init__(
        self,
        repository: FileArtifactRepository,
    ):

        self.repository = repository

    def search(
        self,
        query: str,
    ) -> list:

        if not query:
            return []

        query = query.lower()

        artifacts = self.repository.list_all()

        results: ClassVar = []

        for artifact in artifacts:
            score = 0

            if query in artifact.name.lower():
                score += 3

            if query in artifact.path.lower():
                score += 2

            metadata = str(artifact.metadata).lower()

            if query in metadata:
                score += 1

            if score > 0:
                results.append(
                    {
                        "artifact": artifact,
                        "score": score,
                    }
                )

        results.sort(
            key=lambda item: item["score"],
            reverse=True,
        )

        return results
