from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ultron.projects.model import (
    Project,
    ProjectArtifact,
)
from ultron.security.permission.rules import deny_when_authorization_unavailable


class FileManager:
    """
    Production file management service.

    Responsibilities:
    - create files
    - validate paths
    - register artifacts
    - return operation results

    Does not:
    - decide what to create
    - reason about user intent
    """

    def __init__(
        self,
        root: str = "Projects",
    ):

        self.root = Path(root)

    def create_file(
        self,
        project: Project,
        filename: str,
        content: str = "",
        artifact_type: str = "document",
    ) -> dict[str, Any]:
        """
        Create a project file and register it.
        """

        deny_when_authorization_unavailable("create_file")
        if not filename.strip():
            raise ValueError("Filename cannot be empty.")

        file_path = self._resolve_project_file_path(project.name, filename)

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with file_path.open("x", encoding="utf-8") as file:
            file.write(content)

        artifact = ProjectArtifact(
            name=filename,
            artifact_type=artifact_type,
            path=str(file_path),
            metadata={
                "created_at": datetime.now(UTC).isoformat(),
                "managed_by": "ULTRON",
            },
        )

        project.artifacts.append(artifact)

        project.updated_at = datetime.now(UTC)

        return {
            "success": True,
            "file": str(file_path),
            "artifact": artifact,
        }

    def read_file(
        self,
        path: str,
    ) -> str:
        deny_when_authorization_unavailable("read_file")

        file = Path(path)

        if not file.exists():
            raise FileNotFoundError(path)

        return file.read_text(encoding="utf-8")

    def exists(
        self,
        path: str,
    ) -> bool:
        deny_when_authorization_unavailable("read_file")

        return Path(path).exists()

    def _resolve_project_file_path(
        self,
        project_name: str,
        filename: str,
    ) -> Path:
        root = self.root.resolve()
        project_path = (root / project_name).resolve()
        file_path = (project_path / filename).resolve()

        try:
            project_path.relative_to(root)
            file_path.relative_to(project_path)
        except ValueError as error:
            raise ValueError("Unsafe file path.") from error

        return file_path
