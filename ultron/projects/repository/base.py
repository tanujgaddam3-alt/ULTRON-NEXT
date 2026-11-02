from abc import ABC, abstractmethod

from ultron.projects.model import Project


class ProjectRepository(ABC):
    """
    Interface for project persistence.
    """

    @abstractmethod
    def save(
        self,
        project: Project,
    ) -> Project:
        raise NotImplementedError

    @abstractmethod
    def get(
        self,
        name: str,
    ) -> Project | None:
        raise NotImplementedError

    @abstractmethod
    def list_all(
        self,
    ) -> list[Project]:
        raise NotImplementedError

    @abstractmethod
    def delete(
        self,
        name: str,
    ) -> bool:
        raise NotImplementedError
