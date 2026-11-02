from abc import ABC, abstractmethod

from ultron.projects.model import ProjectArtifact


class FileArtifactRepository(ABC):
    """
    Persistence interface for file artifacts.

    Implementations can use:
    - SQLite
    - PostgreSQL
    - Cloud storage
    """

    @abstractmethod
    def save(
        self,
        artifact: ProjectArtifact,
    ) -> ProjectArtifact:
        raise NotImplementedError

    @abstractmethod
    def get(
        self,
        name: str,
    ) -> ProjectArtifact | None:
        raise NotImplementedError

    @abstractmethod
    def list_all(
        self,
    ) -> list[ProjectArtifact]:
        raise NotImplementedError

    @abstractmethod
    def delete(
        self,
        name: str,
    ) -> bool:
        raise NotImplementedError
