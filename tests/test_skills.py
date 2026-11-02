from ultron.skills.manager import SkillManager


def test_system_skill_registered():

    manager = SkillManager()

    skill = manager.registry.get("open_application")

    assert skill is not None
    assert skill.name == "system"

def test_unknown_action():

    manager = SkillManager()

    try:
        manager.execute("does_not_exist")
        assert False

    except ValueError:
        assert True