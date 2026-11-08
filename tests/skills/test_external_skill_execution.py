from __future__ import annotations

import pytest

from ultron.skills.base import Skill
from ultron.skills.financial.skill import FinancialSkill
from ultron.skills.manager import SkillManager
from ultron.skills.manifest import SkillLifecycle


class _SearchSkill(Skill):
    name = "search"
    actions = ["search"]
    domain = "market"
    action_permissions = {
        "search": frozenset({"market.research.read"}),
    }

    def __init__(self, *, valid_output: bool = True):
        self.valid_output = valid_output
        self.calls = 0

    def validate(self, action, **kwargs):
        return action == "search" and isinstance(kwargs.get("symbol"), str)

    def execute(self, action, **kwargs):
        self.calls += 1
        if self.valid_output:
            return {"summary": f"Research for {kwargs['symbol']}"}
        return "invalid output"


_CONTRACT = {
    "type": "object",
    "properties": {"symbol": {"type": "string"}},
    "required": ["symbol"],
    "additionalProperties": False,
}
_OUTPUT_CONTRACT = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


def _manifest(tmp_path, *, dependency: bool = False) -> str:
    import yaml

    entry = {
        "id": "external-research",
        "display_name": "External Research",
        "category": "market-regime",
        "status": "experimental",
        "summary": "Test-only metadata.",
        "operational_role": {"type": "analysis"},
        "inputs": ["symbol"],
        "outputs": ["summary"],
        "integrations": (
            [
                {
                    "id": "market-feed",
                    "type": "market_data",
                    "requirement": "required",
                }
            ]
            if dependency
            else []
        ),
        "depends_on": [],
        "workflows": [],
        "verification": {"security": "not_verified"},
    }
    path = tmp_path / "skills-index.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "categories": [
                    "advanced-satellite",
                    "core-portfolio",
                    "market-regime",
                    "meta",
                    "strategy-research",
                    "swing-opportunity",
                    "trade-memory",
                    "trade-planning",
                ],
                "skills": [entry],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return str(path)


def _manager(tmp_path, *, dependency: bool = False, valid_output: bool = True):
    manager = SkillManager()
    skill = _SearchSkill(valid_output=valid_output)
    manager.register(skill)
    manager.load_external_manifest(
        _manifest(tmp_path, dependency=dependency),
        source_repository="TraderMonty",
        source_revision="c9b84196a44a56a553293114fdae1f30bdd08c58",
        source_license="MIT",
    )
    return manager, skill


def _enable(manager: SkillManager) -> None:
    manager.registry.update_external_lifecycle(
        "external-research",
        lifecycle=SkillLifecycle.AUDITED,
        verification_status="source_reviewed",
    )
    manager.registry.configure_external_adapter(
        "external-research",
        native_action="search",
        input_contract=_CONTRACT,
        output_contract=_OUTPUT_CONTRACT,
        lifecycle=SkillLifecycle.ADAPTED,
        verification_status="source_reviewed",
    )
    manager.registry.configure_external_adapter(
        "external-research",
        native_action="search",
        input_contract=_CONTRACT,
        output_contract=_OUTPUT_CONTRACT,
        lifecycle=SkillLifecycle.TESTED,
        verification_status="test_verified",
    )
    manager.registry.configure_external_adapter(
        "external-research",
        native_action="search",
        input_contract=_CONTRACT,
        output_contract=_OUTPUT_CONTRACT,
        lifecycle=SkillLifecycle.ENABLED,
        verification_status="test_verified",
    )


def test_external_skill_requires_explicit_verified_enablement(tmp_path):
    manager, skill = _manager(tmp_path)

    with pytest.raises(PermissionError, match="not enabled"):
        manager.execute_external("external-research", {"symbol": "ABC"})
    assert skill.calls == 0

    _enable(manager)
    result = manager.execute_external(
        "external-research",
        {"symbol": "ABC"},
    )
    assert result == {"summary": "Research for ABC"}
    assert skill.calls == 1


def test_external_input_contract_and_required_dependency_are_enforced(tmp_path):
    manager, skill = _manager(tmp_path, dependency=True)
    _enable(manager)

    with pytest.raises(PermissionError, match="dependencies are unavailable"):
        manager.execute_external("external-research", {"symbol": "ABC"})
    assert skill.calls == 0

    with pytest.raises(ValueError, match="unexpected fields"):
        manager.execute_external(
            "external-research",
            {"symbol": "ABC", "extra": True},
            available_dependencies={"market-feed"},
        )
    assert skill.calls == 0

    assert manager.execute_external(
        "external-research",
        {"symbol": "ABC"},
        available_dependencies={"market-feed"},
    ) == {"summary": "Research for ABC"}
    assert skill.calls == 1


def test_external_output_contract_is_enforced(tmp_path):
    manager, skill = _manager(tmp_path, valid_output=False)
    _enable(manager)

    with pytest.raises(ValueError, match="does not match type"):
        manager.execute_external("external-research", {"symbol": "ABC"})
    assert skill.calls == 1


def test_external_skill_search_returns_provenance_without_execution(tmp_path):
    manager, skill = _manager(tmp_path)

    result = manager.search_external_skills(
        "research",
        category="market-regime",
    )

    assert result["metadata_only"] is True
    assert result["execution_performed"] is False
    assert len(result["results"]) == 1
    record = result["results"][0]
    assert record["external_id"] == "external-research"
    assert record["lifecycle"] == "DISCOVERED"
    assert record["verification_status"] == "not_verified"
    assert record["source_revision"] == (
        "c9b84196a44a56a553293114fdae1f30bdd08c58"
    )
    assert record["source_license"] == "MIT"
    assert skill.calls == 0


def test_external_skill_cannot_map_to_protected_paper_trading(tmp_path):
    manager, _ = _manager(tmp_path)
    manager.register(FinancialSkill(financial_agent=object()))
    manager.registry.update_external_lifecycle(
        "external-research",
        lifecycle=SkillLifecycle.AUDITED,
        verification_status="source_reviewed",
    )
    for lifecycle, verification in (
        (SkillLifecycle.ADAPTED, "source_reviewed"),
        (SkillLifecycle.TESTED, "test_verified"),
        (SkillLifecycle.ENABLED, "test_verified"),
    ):
        manager.registry.configure_external_adapter(
            "external-research",
            native_action="paper_trade",
            input_contract=_CONTRACT,
            output_contract=_OUTPUT_CONTRACT,
            lifecycle=lifecycle,
            verification_status=verification,
        )

    with pytest.raises(PermissionError, match="read-only native analysis"):
        manager.execute_external(
            "external-research",
            {"symbol": "ABC"},
        )

    assert manager.permission_denials[-1]["classification"] == (
        "EXTERNAL_SKILL_CAPABILITY_DENIED"
    )


@pytest.mark.parametrize(
    ("query", "limit", "category"),
    [
        (" ", 5, None),
        ("research", 0, None),
        ("research", 21, None),
        ("research", True, None),
        ("research", 5, ""),
    ],
)
def test_external_skill_search_rejects_invalid_search_parameters(
    tmp_path,
    query,
    limit,
    category,
):
    manager, _ = _manager(tmp_path)

    with pytest.raises(ValueError):
        manager.search_external_skills(
            query,
            limit=limit,
            category=category,
        )
