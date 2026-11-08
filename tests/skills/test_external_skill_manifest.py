from __future__ import annotations

import pytest

from ultron.skills.manifest import (
    SkillLifecycle,
    load_external_skill_manifest,
)
from ultron.skills.base import Skill
from ultron.skills.contracts import validate_contract_schema
from ultron.skills.registry import SkillRegistry


class _NativeSearchSkill(Skill):
    name = "native-search"
    actions = ["search"]
    domain = "market"
    action_permissions = {
        "search": frozenset({"market.research.read"}),
    }

    def execute(self, action, **kwargs):
        return {}


_CATEGORIES = [
    "advanced-satellite",
    "core-portfolio",
    "market-regime",
    "meta",
    "strategy-research",
    "swing-opportunity",
    "trade-memory",
    "trade-planning",
]


def _entry(
    external_id: str,
    *,
    depends_on: list[str] | None = None,
    integrations: list[dict] | None = None,
) -> dict:
    return {
        "id": external_id,
        "display_name": external_id.replace("-", " ").title(),
        "category": "market-regime",
        "status": "experimental",
        "summary": "Metadata-only test entry.",
        "operational_role": {"type": "analysis"},
        "inputs": ["symbol"],
        "outputs": ["summary"],
        "integrations": integrations or [],
        "depends_on": depends_on or [],
        "workflows": [],
        "verification": {"security": "not_verified"},
    }


def _write_manifest(tmp_path, skills: list[dict]) -> str:
    import yaml

    path = tmp_path / "skills-index.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "categories": _CATEGORIES,
                "skills": skills,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return str(path)


def _load(tmp_path, skills: list[dict]):
    return load_external_skill_manifest(
        _write_manifest(tmp_path, skills),
        source_repository="TraderMonty",
        source_revision="c9b84196a44a56a553293114fdae1f30bdd08c58",
        source_license="MIT",
    )


def test_manifest_preserves_provenance_and_unverified_lifecycle(tmp_path):
    manifest = _load(tmp_path, [_entry("market-regime-detector")])

    record = manifest.skills[0]
    assert manifest.source_revision == "c9b84196a44a56a553293114fdae1f30bdd08c58"
    assert manifest.source_license == "MIT"
    assert record.external_id == "market-regime-detector"
    assert record.lifecycle is SkillLifecycle.DISCOVERED
    assert record.verification_status == "not_verified"
    assert record.verification_claims == {"security": "not_verified"}
    assert record.native_action is None
    assert record.input_contract is None


def test_manifest_rejects_duplicate_ids_and_unknown_dependencies(tmp_path):
    duplicate = _entry("same-skill")
    with pytest.raises(ValueError, match="Duplicate external skill ID"):
        _load(tmp_path, [duplicate, _entry("same-skill")])

    with pytest.raises(ValueError, match="depends on unknown skill"):
        _load(tmp_path, [_entry("dependent", depends_on=["missing-skill"])])


def test_manifest_rejects_untrusted_permission_metadata_and_unknown_fields(tmp_path):
    entry = _entry("untrusted-permission")
    entry["permissions"] = ["trading.live.execute"]
    with pytest.raises(ValueError, match="unsupported fields"):
        _load(tmp_path, [entry])

    entry = _entry("unknown-field")
    entry["capabilities"] = ["market.data.read"]
    with pytest.raises(ValueError, match="unsupported fields"):
        _load(tmp_path, [entry])


@pytest.mark.parametrize(
    "workflow",
    ["../secret", "/absolute", "folder/workflow", r"folder\\workflow"],
)
def test_manifest_rejects_workflow_path_traversal(tmp_path, workflow):
    entry = _entry("unsafe-workflow")
    entry["workflows"] = [workflow]

    with pytest.raises(ValueError, match="safe workflow identifiers"):
        _load(tmp_path, [entry])


def test_manifest_rejects_duplicate_yaml_keys_and_aliases(tmp_path):
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        "schema_version: 1\nschema_version: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not valid YAML"):
        load_external_skill_manifest(
            path,
            source_repository="TraderMonty",
            source_revision="revision",
            source_license="MIT",
        )

    path.write_text(
        "schema_version: 1\ncategories: &categories []\n"
        "skills: *categories\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not valid YAML"):
        load_external_skill_manifest(
            path,
            source_repository="TraderMonty",
            source_revision="revision",
            source_license="MIT",
        )


def test_manifest_rejects_oversized_input(tmp_path):
    path = tmp_path / "large.yaml"
    path.write_bytes(b" " * (2 * 1024 * 1024 + 1))

    with pytest.raises(ValueError, match="exceeds"):
        load_external_skill_manifest(
            path,
            source_repository="TraderMonty",
            source_revision="revision",
            source_license="MIT",
        )


def test_manifest_rejects_required_skill_dependency_cycles(tmp_path):
    with pytest.raises(ValueError, match="dependency cycle"):
        _load(
            tmp_path,
            [
                _entry("skill-a", depends_on=["skill-b"]),
                _entry("skill-b", depends_on=["skill-a"]),
            ],
        )


def test_required_skill_dependencies_resolve_only_after_verified_enablement(
    tmp_path,
):
    manifest = _load(
        tmp_path,
        [
            _entry("base-skill"),
            _entry("dependent-skill", depends_on=["base-skill"]),
        ],
    )
    registry = SkillRegistry()
    registry.register(_NativeSearchSkill())
    registry.register_external_manifest(manifest)

    contract = {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
        "additionalProperties": False,
    }
    for external_id in ("base-skill", "dependent-skill"):
        registry.update_external_lifecycle(
            external_id,
            lifecycle=SkillLifecycle.AUDITED,
            verification_status="source_reviewed",
        )
        registry.configure_external_adapter(
            external_id,
            native_action="search",
            input_contract=contract,
            output_contract=contract,
            lifecycle=SkillLifecycle.ADAPTED,
            verification_status="source_reviewed",
        )
        registry.configure_external_adapter(
            external_id,
            native_action="search",
            input_contract=contract,
            output_contract=contract,
            lifecycle=SkillLifecycle.TESTED,
            verification_status="test_verified",
        )
        registry.configure_external_adapter(
            external_id,
            native_action="search",
            input_contract=contract,
            output_contract=contract,
            lifecycle=SkillLifecycle.ENABLED,
            verification_status="test_verified",
        )

    state = registry.resolve_external_dependencies("dependent-skill")
    assert state == {
        "resolved": True,
        "required": ["base-skill"],
        "missing": [],
    }

    changed_contract = {
        "type": "object",
        "properties": {"symbol": {"type": "integer"}},
        "required": ["symbol"],
        "additionalProperties": False,
    }
    with pytest.raises(ValueError, match="Changing a tested adapter"):
        registry.configure_external_adapter(
            "base-skill",
            native_action="search",
            input_contract=contract,
            output_contract=changed_contract,
            lifecycle=SkillLifecycle.ENABLED,
            verification_status="test_verified",
        )

    registry.update_external_lifecycle(
        "base-skill",
        lifecycle=SkillLifecycle.DISABLED,
        verification_status="test_verified",
    )
    state = registry.resolve_external_dependencies("dependent-skill")
    assert state["resolved"] is False
    assert state["missing"] == ["base-skill"]


def test_lifecycle_cannot_skip_audit_adaptation_or_tests(tmp_path):
    registry = SkillRegistry()
    registry.register(_NativeSearchSkill())
    registry.register_external_manifest(
        _load(tmp_path, [_entry("external-skill")])
    )
    contract = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    with pytest.raises(ValueError, match="must first be AUDITED"):
        registry.configure_external_adapter(
            "external-skill",
            native_action="search",
            input_contract=contract,
            output_contract=contract,
            lifecycle=SkillLifecycle.ADAPTED,
            verification_status="source_reviewed",
        )


@pytest.mark.parametrize(
    "schema",
    [
        {"type": [{}]},
        {"type": ["unsupported"]},
        {1: "not-a-string-key", "type": "object"},
    ],
)
def test_contract_schema_rejects_malformed_types_and_keys(schema):
    with pytest.raises(ValueError):
        validate_contract_schema(schema)
