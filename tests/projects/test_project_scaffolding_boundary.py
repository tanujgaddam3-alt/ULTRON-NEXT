from types import SimpleNamespace

import pytest

from ultron.projects.builder.builder import ProjectBuilder


class FakeProjects:
    def __init__(self, name):
        self.project = SimpleNamespace(name=name)

    def get(self, _name):
        return self.project


@pytest.mark.parametrize(
    "directory",
    ["../outside/child", "../demo-sibling/child"],
)
def test_project_scaffolding_rejects_traversal_and_sibling_prefix(
    tmp_path,
    monkeypatch,
    directory,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Projects" / "demo").mkdir(parents=True)
    builder = ProjectBuilder(FakeProjects("demo"), file_service=None)

    with pytest.raises(ValueError, match="Unsafe project directory"):
        builder._create_directory("demo", directory)

    assert not (tmp_path / "Projects" / "outside").exists()
    assert not (tmp_path / "Projects" / "demo-sibling").exists()


def test_project_scaffolding_rejects_project_root_symlink_escape(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    projects_root = tmp_path / "Projects"
    projects_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (projects_root / "demo").symlink_to(outside, target_is_directory=True)
    builder = ProjectBuilder(FakeProjects("demo"), file_service=None)

    with pytest.raises(ValueError, match="Unsafe project directory"):
        builder._create_directory("demo", "generated")

    assert not (outside / "generated").exists()


def test_project_scaffolding_rejects_nested_symlink_escape(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    project_root = tmp_path / "Projects" / "demo"
    project_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_root / "linked").symlink_to(outside, target_is_directory=True)
    builder = ProjectBuilder(FakeProjects("demo"), file_service=None)

    with pytest.raises(ValueError, match="Unsafe project directory"):
        builder._create_directory("demo", "linked/generated")

    assert not (outside / "generated").exists()


def test_project_scaffolding_creates_directory_inside_project_root(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    project_root = tmp_path / "Projects" / "demo"
    project_root.mkdir(parents=True)
    builder = ProjectBuilder(FakeProjects("demo"), file_service=None)

    builder._create_directory("demo", "src/generated")

    assert (project_root / "src" / "generated").is_dir()
