from ultron.world.engine import WorldEngine
from ultron.world.models import SystemInfo


def test_world_engine():

    world = WorldEngine()

    snapshot = world.refresh()

    assert snapshot.system.os != ""
    assert snapshot.system.hostname != ""
    assert snapshot.system.python_version != ""

    assert snapshot.project.directory != ""


def test_unavailable_system_observation_does_not_default_usage_to_zero():
    system = SystemInfo()

    assert system.cpu_usage is None
    assert system.memory_usage is None