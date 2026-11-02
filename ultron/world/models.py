from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from ultron.world.entity import WorldEntity


class SystemInfo(BaseModel):
    os: str = ""
    hostname: str = ""
    python_version: str = ""
    cpu_usage: float | None = None
    memory_usage: float | None = None


class ProjectInfo(BaseModel):
    name: str = ""
    directory: str = ""
    git_branch: str = ""


class RuntimeInfo(BaseModel):
    internet_connected: bool = False
    docker_running: bool = False
    active_applications: list[str] = Field(default_factory=list)


class WorldSnapshot(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))

    system: SystemInfo = Field(default_factory=SystemInfo)

    project: ProjectInfo = Field(default_factory=ProjectInfo)

    runtime: RuntimeInfo = Field(default_factory=RuntimeInfo)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entities: list[WorldEntity] = Field(default_factory=list)
