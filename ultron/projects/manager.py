from ultron.projects.events.events import (
    ProjectEvent,
    ProjectEventType,
)
from ultron.projects.model import Project
from ultron.projects.repository.base import ProjectRepository
from ultron.projects.workspace.manager import WorkspaceManager
from ultron.security.permission.rules import deny_when_authorization_unavailable


class ProjectManager:
    """
    Manages ULTRON projects.
    """

    def __init__(
        self,
        repository: ProjectRepository,
        event_bus=None,
    ):

        self.repository = repository

        self.workspace = WorkspaceManager()

        self.event_bus = event_bus

    def create(
        self,
        project: Project,
    ) -> Project:
        deny_when_authorization_unavailable("create_project")

        existing = self.repository.get(project.name)

        if existing:
            raise ValueError("Project already exists.")

        self.repository.save(project)

        self.workspace.create_workspace(project)
        if self.event_bus:
            self.event_bus.publish(
                ProjectEvent(
                    type=ProjectEventType.CREATED,
                    project_name=project.name,
                    data={
                        "description": project.description,
                    },
                )
            )

        return project

    def get(
        self,
        name: str,
    ) -> Project | None:
        deny_when_authorization_unavailable("read_project")

        return self.repository.get(name)

    def list_projects(
        self,
    ):
        deny_when_authorization_unavailable("list_projects")

        return self.repository.list_all()
