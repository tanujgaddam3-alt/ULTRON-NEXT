from pathlib import Path

import pytest

from ultron.files.artifact_service import ArtifactService
from ultron.files.repository.sqlite import SQLiteArtifactRepository
from ultron.files.service import FileService
from ultron.projects.artifacts.manager import ArtifactManager
from ultron.projects.manager import ProjectManager
from ultron.projects.model import Project, ProjectArtifact
from ultron.projects.repository.sqlite import SQLiteProjectRepository
from ultron.projects.workspace.manager import WorkspaceManager


class RecordingRepository:
    def __init__(self):
        self.calls = []

    def get(self, name):
        self.calls.append(("get", name))
        return None

    def list_all(self):
        self.calls.append(("list_all",))
        return []

    def save(self, value):
        self.calls.append(("save", value))
        return value

    def delete(self, name):
        self.calls.append(("delete", name))
        return True


def test_project_manager_reads_fail_before_repository_access():
    repository = RecordingRepository()
    manager = ProjectManager(repository)

    with pytest.raises(PermissionError):
        manager.get("sentinel")
    with pytest.raises(PermissionError):
        manager.list_projects()

    assert repository.calls == []


@pytest.mark.parametrize(
    "method",
    [
        "save",
        "get",
        "list_all",
        "delete",
    ],
)
def test_project_repository_operations_fail_closed(
    tmp_path: Path,
    method: str,
):
    repository = SQLiteProjectRepository(tmp_path / "projects.sqlite3")

    with pytest.raises(PermissionError):
        if method == "save":
            repository.save(Project(name="sentinel"))
        elif method == "get":
            repository.get("sentinel")
        elif method == "list_all":
            repository.list_all()
        else:
            repository.delete("sentinel")

    assert repository.connection.execute(
        "SELECT COUNT(*) FROM projects"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "method",
    [
        "save",
        "get",
        "list_all",
        "delete",
    ],
)
def test_artifact_repository_operations_fail_closed(
    tmp_path: Path,
    method: str,
):
    repository = SQLiteArtifactRepository(tmp_path / "artifacts.sqlite3")
    artifact = ProjectArtifact(
        name="sentinel",
        artifact_type="document",
        path="sentinel",
    )

    with pytest.raises(PermissionError):
        if method == "save":
            repository.save(artifact)
        elif method == "get":
            repository.get("sentinel")
        elif method == "list_all":
            repository.list_all()
        else:
            repository.delete("sentinel")

    assert repository.connection.execute(
        "SELECT COUNT(*) FROM artifacts"
    ).fetchone()[0] == 0


def test_artifact_service_denies_before_repository_dispatch():
    repository = RecordingRepository()
    service = ArtifactService(repository)

    with pytest.raises(PermissionError):
        service.register("sentinel", "sentinel")
    with pytest.raises(PermissionError):
        service.get("sentinel")
    with pytest.raises(PermissionError):
        service.list()
    with pytest.raises(PermissionError):
        service.delete("sentinel")

    assert repository.calls == []


def test_artifact_manager_does_not_mutate_project_without_authorization():
    project = Project(name="sentinel")
    artifact = ProjectArtifact(
        name="sentinel",
        artifact_type="document",
        path="sentinel",
    )
    manager = ArtifactManager()

    with pytest.raises(PermissionError):
        manager.create(project, artifact)
    with pytest.raises(PermissionError):
        manager.get(project, artifact.name)
    with pytest.raises(PermissionError):
        manager.list(project)
    with pytest.raises(PermissionError):
        manager.remove(project, artifact.name)

    assert project.artifacts == []


def test_workspace_manager_does_not_create_directories_without_authorization(
    tmp_path: Path,
):
    manager = WorkspaceManager(str(tmp_path))

    with pytest.raises(PermissionError):
        manager.create_workspace(Project(name="sentinel"))

    assert not (tmp_path / "sentinel").exists()


def test_file_service_denies_before_project_lookup():
    projects = RecordingRepository()
    service = FileService(
        file_manager=object(),
        project_manager=projects,
        artifact_service=object(),
    )

    with pytest.raises(PermissionError):
        service.create_project_file("sentinel", "sentinel.txt")

    assert projects.calls == []
