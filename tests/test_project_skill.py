import pytest

from ultron.skills.project.skill import ProjectSkill
from ultron.projects.manager import ProjectManager
from ultron.projects.repository.sqlite import SQLiteProjectRepository


def test_project_creation_skill_fails_closed_without_trusted_authorization():

    repository = SQLiteProjectRepository(
        database=":memory:"
    )

    manager = ProjectManager(
        repository=repository,
    )


    skill = ProjectSkill(
        manager
    )


    with pytest.raises(PermissionError, match="trusted authorization"):
        skill.execute(
            "create_project",
            name="Mark15",
            description="Prototype project",
        )

    assert repository.connection.execute(
        "SELECT COUNT(*) FROM projects WHERE name = ?",
        ("Mark15",),
    ).fetchone()[0] == 0
