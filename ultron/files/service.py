from ultron.files.manager import FileManager
from ultron.projects.manager import ProjectManager
from ultron.security.permission.rules import deny_when_authorization_unavailable


class FileService:
    """
    Application service for project-aware files.
    """

    def __init__(
        self,
        file_manager: FileManager,
        project_manager: ProjectManager,
        artifact_service,
    ):

        self.files = file_manager
        self.projects = project_manager
        self.artifacts = artifact_service

    def create_project_file(
        self,
        project_name: str,
        filename: str,
        content: str = "",
    ):
        deny_when_authorization_unavailable("create_file")

        project = self.projects.get(project_name)

        if project is None:
            raise ValueError(f"Project '{project_name}' not found.")

        result = self.files.create_file(
            project=project,
            filename=filename,
            content=content,
        )

        artifact = self.artifacts.register(
            name=filename,
            path=result["file"],
            artifact_type="document",
            project=project_name,
        )

        return {
            "success": True,
            "file": result["file"],
            "artifact": artifact,
        }
