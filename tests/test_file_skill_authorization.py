import pytest

from ultron.files.manager import FileManager
from ultron.financial.agent.financial_agent import FinancialAgent
from ultron.projects.model import Project
from ultron.projects.manager import ProjectManager
from ultron.skills.file.skill import FileSkill
from ultron.skills.financial.skill import FinancialSkill
from ultron.skills.improvement.skill import ImprovementSkill
from ultron.skills.project.skill import ProjectSkill
from ultron.skills.system.skill import SystemSkill
from ultron.projects.builder.builder import (
    ProjectBuilder as ProjectWorkspaceBuilder,
)


class RecordingFileService:
    def __init__(self):
        self.calls = []

    def create_project_file(self, **kwargs):
        self.calls.append(("create_project_file", kwargs))


@pytest.mark.parametrize(
    ("action", "invoke"),
    [
        ("create_file", lambda skill, path: skill._create_file(
            project_name="demo",
            filename="example.txt",
            content="data",
        )),
        ("read_file", lambda skill, path: skill._read_file(path=str(path))),
        ("read_file", lambda skill, path: skill._read_path(str(path))),
        ("write_file", lambda skill, path: skill._write_file(
            path=str(path),
            content="data",
        )),
        ("write_file", lambda skill, path: skill.write_file(
            path=str(path),
            content="data",
        )),
        ("list_directory", lambda skill, path: skill.list_directory(str(path))),
    ],
)
def test_direct_file_skill_methods_fail_closed_before_io(
    tmp_path,
    action,
    invoke,
):
    file_service = RecordingFileService()
    skill = FileSkill(file_service=file_service)
    target = tmp_path / "protected.txt"

    with pytest.raises(PermissionError, match="trusted authorization"):
        invoke(skill, target)

    assert not target.exists()
    assert file_service.calls == []


def test_file_manager_mutation_fails_closed_before_creating_files(tmp_path):
    manager = FileManager(root=str(tmp_path / "Projects"))
    project = Project(name="demo")

    with pytest.raises(PermissionError, match="trusted authorization"):
        manager.create_file(project, "example.txt", "data")

    assert not (tmp_path / "Projects").exists()
    assert project.artifacts == []


def test_file_manager_read_and_existence_checks_fail_closed(tmp_path):
    manager = FileManager(root=str(tmp_path / "Projects"))
    target = tmp_path / "private.txt"
    target.write_text("fixture data", encoding="utf-8")

    with pytest.raises(PermissionError, match="trusted authorization"):
        manager.read_file(str(target))
    with pytest.raises(PermissionError, match="trusted authorization"):
        manager.exists(str(target))


def test_file_manager_rejects_traversal_and_symlink_escape(tmp_path):
    root = tmp_path / "Projects"
    project_dir = root / "demo"
    outside = tmp_path / "outside"
    project_dir.mkdir(parents=True)
    outside.mkdir()
    (project_dir / "linked").symlink_to(outside, target_is_directory=True)
    manager = FileManager(root=str(root))

    with pytest.raises(ValueError, match="Unsafe file path"):
        manager._resolve_project_file_path("demo", "../outside.txt")

    with pytest.raises(ValueError, match="Unsafe file path"):
        manager._resolve_project_file_path("demo", "linked/escaped.txt")


def test_direct_project_skill_mutation_methods_fail_closed():
    class RecordingProjectManager:
        def __init__(self):
            self.calls = []

        def create(self, project):
            self.calls.append(project)

        def get(self, name):
            self.calls.append(name)

    manager = RecordingProjectManager()
    skill = ProjectSkill(manager)

    with pytest.raises(PermissionError, match="trusted authorization"):
        skill.create_project({"name": "demo"})
    with pytest.raises(PermissionError, match="trusted authorization"):
        skill.build_project({"name": "demo"})
    with pytest.raises(PermissionError, match="trusted authorization"):
        skill.get_project({"name": "demo"})

    assert manager.calls == []


def test_project_manager_and_workspace_builder_fail_before_mutation():
    class RecordingRepository:
        def __init__(self):
            self.calls = []

        def get(self, name):
            self.calls.append(("get", name))
            return None

        def save(self, project):
            self.calls.append(("save", project))

    repository = RecordingRepository()
    manager = ProjectManager(repository=repository)
    builder = ProjectWorkspaceBuilder(
        project_manager=manager,
        file_service=RecordingFileService(),
    )

    with pytest.raises(PermissionError, match="trusted authorization"):
        manager.create(Project(name="demo"))
    with pytest.raises(PermissionError, match="trusted authorization"):
        builder.build("demo")

    assert repository.calls == []


def test_direct_system_financial_and_improvement_methods_fail_closed():
    class RecordingFinancialAgent:
        def __init__(self):
            self.calls = []

        def paper_trade(self, **kwargs):
            self.calls.append(kwargs)

    financial_agent = RecordingFinancialAgent()
    financial_skill = FinancialSkill(financial_agent)
    uninitialized_agent = FinancialAgent.__new__(FinancialAgent)
    system_skill = SystemSkill.__new__(SystemSkill)
    improvement_skill = ImprovementSkill(
        improvement_service=object(),
        self_awareness=object(),
    )

    with pytest.raises(PermissionError, match="trusted authorization"):
        system_skill._open_application("example")
    with pytest.raises(PermissionError, match="trusted authorization"):
        system_skill.execute("close_application", application="example")
    with pytest.raises(PermissionError, match="trusted authorization"):
        financial_skill._paper_trade(symbol="TEST")
    with pytest.raises(PermissionError, match="trusted authorization"):
        financial_skill.execute("paper_trade", symbol="TEST")
    with pytest.raises(PermissionError, match="trusted authorization"):
        uninitialized_agent.paper_trade(analysis={"symbol": "TEST"})
    with pytest.raises(PermissionError, match="trusted authorization"):
        improvement_skill.execute("improve_self")

    assert financial_agent.calls == []
