import pytest

from ultron.improvement.analyzer.code_analyzer import (
    CodeAnalyzer,
)

from ultron.improvement.planner.improvement_planner import (
    ImprovementPlanner,
)

from ultron.improvement.generator.patch_generator import (
    PatchGenerator,
)

from ultron.improvement.validator.quality_checker import (
    QualityChecker,
)

from ultron.improvement.validator.safety_validator import (
    SafetyValidator,
)

from ultron.infrastructure.container import Container
from ultron.improvement.domain.models import Patch
from ultron.improvement.apply.patch_applier import PatchApplier
from ultron.improvement.manager import ImprovementManager


def test_improvement_pipeline():

    analyzer = CodeAnalyzer()

    planner = ImprovementPlanner()

    container = Container()
    generator = container.improvement_manager.generator

    quality = QualityChecker()

    safety = SafetyValidator()


    proposal = analyzer.analyze_failure(
        "FileSkill",
        "Cannot create nested directories",
    )


    plan = planner.create_plan(
        proposal
    )


    patch = generator.generate(
        plan
    )


    quality_result = quality.check(
        patch
    )


    safety_result = safety.validate(
        patch
    )


    assert quality_result["passed"]

    assert safety_result["safe"]


def test_safety_validator_warning_state_is_per_call():
    validator = SafetyValidator()

    protected = Patch(
        target_file="ultron/execution.py",
        description="Protected change",
        changes="update execution",
    )
    ordinary = Patch(
        target_file="ultron/feature.py",
        description="Ordinary change",
        changes="update feature",
    )

    protected_result = validator.validate(protected)
    assert protected_result["requires_approval"]
    assert protected_result["safe"] is False
    result = validator.validate(ordinary)

    assert result["safe"]
    assert result["warnings"] == []
    assert result["errors"] == []


def test_safety_validator_accepts_equivalent_dict_and_object_patches():
    validator = SafetyValidator()
    patch = Patch(
        target_file="ultron/feature.py",
        description="Ordinary change",
        changes="update feature",
        operation="modify",
    )
    patch_dict = {
        "target_file": patch.target_file,
        "description": patch.description,
        "changes": patch.changes,
        "operation": patch.operation,
        "files": [],
    }

    object_result = validator.validate(patch)
    dict_result = validator.validate(patch_dict)

    assert object_result == dict_result
    assert object_result["safe"]


@pytest.mark.parametrize(
    "patch",
    [
        {},
        {"target_file": "feature.py"},
        {"target_file": "feature.py", "description": "x", "changes": 3},
        {"target_file": "feature.py", "description": "x", "changes": "x", "operation": "unknown"},
        {"target_file": "feature.py", "description": "x", "changes": "x", "operation": "create", "files": []},
        None,
    ],
)
def test_safety_validator_rejects_malformed_patch_data(patch):
    result = SafetyValidator().validate(patch)

    assert result["safe"] is False
    assert result["errors"]
    assert result["requires_approval"]


def test_safety_validator_fails_closed_on_unexpected_patch_exception():
    class BrokenPatch:
        @property
        def target_file(self):
            raise RuntimeError("unavailable patch data")

    result = SafetyValidator().validate(BrokenPatch())

    assert result["safe"] is False
    assert result["errors"]
    assert "unavailable patch data" in result["errors"][0]


@pytest.mark.parametrize(
    "patch",
    [
        Patch(
            target_file="ultron/execution.py",
            description="Protected change",
            changes="update execution",
        ),
        Patch(
            target_file="../outside.py",
            description="Traversal attempt",
            changes="update file",
        ),
        Patch(
            target_file="ultron/feature.py",
            description="Deletion request",
            changes="delete old implementation",
        ),
    ],
)
def test_safety_validator_rejects_protected_or_unsafe_targets(patch):
    result = SafetyValidator().validate(patch)

    assert result["safe"] is False
    assert result["requires_approval"]
    assert result["warnings"] or result["errors"]


def test_improvement_manager_blocks_approval_required_patch_application():
    patch = Patch(
        target_file="ultron/execution.py",
        description="Protected change",
        changes="update execution",
        replacement_code="value = 1",
    )

    class Generator:
        def generate(self, plan):
            return patch

    class Applier:
        called = False

        def apply(self, candidate):
            self.called = True
            return {"success": True}

    applier = Applier()
    manager = ImprovementManager(
        analyzer=None,
        planner=None,
        generator=Generator(),
        test_runner=None,
        quality_checker=QualityChecker(),
        safety_validator=SafetyValidator(),
        patch_applier=applier,
    )

    result = manager._apply_pipeline(
        proposal=None,
        plan={"operation": "modify"},
    )

    assert result["accepted"] is False
    assert result["reason"] == "Safety validation failed"
    assert applier.called is False


def test_improvement_manager_cannot_bypass_patch_applier_authorization(tmp_path):
    target = tmp_path / "feature.py"
    target.write_text("value = 0\n", encoding="utf-8")
    patch = Patch(
        target_file="feature.py",
        description="Modify feature",
        changes="update feature",
        replacement_code="value = 1",
    )

    class Generator:
        def generate(self, _plan):
            return patch

    applier = PatchApplier(tmp_path)
    manager = ImprovementManager(
        analyzer=None,
        planner=None,
        generator=Generator(),
        test_runner=None,
        quality_checker=QualityChecker(),
        safety_validator=SafetyValidator(),
        patch_applier=applier,
    )

    result = manager._apply_pipeline(proposal=None, plan={})

    assert result["accepted"] is False
    assert result["reason"] == "Patch application failed"
    assert result["details"]["error"] == (
        "Trusted exact-patch authorization is unavailable; "
        "workspace mutation is disabled."
    )
    assert target.read_text(encoding="utf-8") == "value = 0\n"


def test_creation_validator_checks_the_path_used_by_the_applier():
    patch = Patch(
        target_file="",
        description="Create a component",
        changes="create component",
        operation="create",
        files=[
            {"path": "../outside.py", "content": "value = 1"},
        ],
    )

    result = SafetyValidator().validate(patch)

    assert result["safe"] is False
    assert "Creation file path is not allowed" in result["errors"]


def test_patch_applier_never_accepts_callback_or_mutates_workspace(tmp_path):
    target = tmp_path / "feature.py"
    target.write_text("value = 0\n", encoding="utf-8")
    applier = PatchApplier(tmp_path)

    modified = applier.apply(
        Patch(
            target_file="feature.py",
            description="Modify a workspace file",
            replacement_code="value = 1\n",
        )
    )
    created = applier.apply(
        Patch(
            target_file="",
            description="Create a workspace file",
            operation="create",
            files=[{"path": "new.py", "content": "value = 2\n"}],
        )
    )

    assert modified["success"] is False
    assert created["success"] is False
    assert modified["error"] == created["error"]
    assert not hasattr(applier, "_modify_file")
    assert not hasattr(applier, "_create_files")
    assert target.read_text(encoding="utf-8") == "value = 0\n"
    assert not (tmp_path / "new.py").exists()


def test_patch_applier_does_not_accept_arbitrary_approval_callback(tmp_path):
    with pytest.raises(TypeError):
        PatchApplier(
            tmp_path,
            patch_approval_check=lambda _patch: True,
        )
