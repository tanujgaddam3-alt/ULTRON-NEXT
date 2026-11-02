from datetime import UTC, datetime

from ultron.projects.model import (
    Project,
    ProjectArtifact,
)
from ultron.security.permission.rules import deny_when_authorization_unavailable


class ArtifactManager:
    """
    Manages project artifacts.
    """

    def create(
        self,
        project: Project,
        artifact: ProjectArtifact,
    ) -> ProjectArtifact:
        """
        Add an artifact to a project.
        """

        deny_when_authorization_unavailable("register_artifact")
        project.artifacts.append(artifact)

        project.updated_at = datetime.now(UTC)

        return artifact

    def get(
        self,
        project: Project,
        name: str,
    ) -> ProjectArtifact | None:
        """
        Find artifact by name.
        """

        deny_when_authorization_unavailable("read_artifact")
        for artifact in project.artifacts:
            if artifact.name == name:
                return artifact

        return None

    def list(
        self,
        project: Project,
    ) -> list[ProjectArtifact]:
        """
        Return all artifacts.
        """

        deny_when_authorization_unavailable("list_artifacts")
        return project.artifacts.copy()

    def remove(
        self,
        project: Project,
        name: str,
    ) -> bool:
        """
        Remove artifact.
        """

        deny_when_authorization_unavailable("delete_artifact")
        artifact = self.get(
            project,
            name,
        )

        if artifact is None:
            return False

        project.artifacts.remove(artifact)

        project.updated_at = datetime.now(UTC)

        return True
