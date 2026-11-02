from pathlib import Path

from ultron.projects.model import Project
from ultron.security.permission.rules import deny_when_authorization_unavailable


class WorkspaceManager:
    """
    Creates and manages project workspaces.
    """

    def __init__(
        self,
        root: str = "Projects",
    ):

        self.root = Path(root)

    def create_workspace(
        self,
        project: Project,
    ) -> Path:
        deny_when_authorization_unavailable("build_project")

        project_path = self.root / project.name

        project_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        folders = [
            "Notes",
            "Documents",
            "Assets",
        ]

        for folder in folders:
            (project_path / folder).mkdir(exist_ok=True)

        return project_path
