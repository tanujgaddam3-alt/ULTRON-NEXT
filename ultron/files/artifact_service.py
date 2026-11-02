from datetime import UTC, datetime

from ultron.projects.model import ProjectArtifact
from ultron.security.permission.rules import deny_when_authorization_unavailable


class ArtifactService:
    """
    Manages ULTRON file artifacts.

    Responsible for:
    - artifact registration
    - metadata
    - persistence
    """

    def __init__(
        self,
        repository,
    ):

        self.repository = repository

    def register(
        self,
        name: str,
        path: str,
        artifact_type: str = "file",
        project: str | None = None,
    ) -> ProjectArtifact:
        """
        Create and persist artifact metadata.
        """
        deny_when_authorization_unavailable("register_artifact")

        artifact = ProjectArtifact(
            name=name,
            artifact_type=artifact_type,
            path=path,
            metadata={
                "project": project,
                "managed_by": "ULTRON",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

        self.repository.save(artifact)

        return artifact

    def get(
        self,
        name: str,
    ):
        deny_when_authorization_unavailable("read_artifact")

        return self.repository.get(name)

    def list(
        self,
    ):
        deny_when_authorization_unavailable("list_artifacts")

        return self.repository.list_all()

    def delete(
        self,
        name: str,
    ):
        deny_when_authorization_unavailable("delete_artifact")

        return self.repository.delete(name)
