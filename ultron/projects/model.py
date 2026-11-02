from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ultron.projects.state import ProjectState


class ProjectArtifact(BaseModel):
    """
    Represents an item created inside a project.
    """

    name: str

    artifact_type: str

    path: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_artifact_name(
        cls,
        value: str,
    ):

        if not value.strip():
            raise ValueError("Artifact name cannot be empty.")

        return value


class Project(BaseModel):
    """
    Universal representation of a project.
    """

    name: str

    description: str = ""

    project_type: str | None = None

    state: ProjectState = ProjectState.CREATED

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    artifacts: list[ProjectArtifact] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)

    def validate_name(self):

        if not self.name.strip():
            raise ValueError("Artifact name cannot be empty.")
