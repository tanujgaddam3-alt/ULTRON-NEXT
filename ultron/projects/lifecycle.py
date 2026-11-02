from datetime import UTC, datetime

from ultron.projects.model import Project
from ultron.projects.state import ProjectState


class ProjectLifecycle:
    """
    Controls project state transitions.
    """

    def activate(
        self,
        project: Project,
    ):

        project.state = ProjectState.ACTIVE

        project.updated_at = datetime.now(UTC)

        return project

    def pause(
        self,
        project: Project,
    ):

        project.state = ProjectState.PAUSED

        project.updated_at = datetime.now(UTC)

        return project

    def complete(
        self,
        project: Project,
    ):

        project.state = ProjectState.COMPLETED

        project.updated_at = datetime.now(UTC)

        return project
