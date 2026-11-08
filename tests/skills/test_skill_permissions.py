from __future__ import annotations

import pytest

from ultron.skills.backtesting.skill import BacktestingSkill
from ultron.skills.base import Skill
from ultron.skills.file.skill import FileSkill
from ultron.skills.financial.skill import FinancialSkill
from ultron.skills.financial_analysis.skill import Financial_analysisSkill
from ultron.skills.improvement.skill import ImprovementSkill
from ultron.skills.market_data.skill import Market_dataSkill
from ultron.skills.permission_policy import SkillPermissionPolicy
from ultron.skills.manager import SkillManager
from ultron.skills.project.skill import ProjectSkill
from ultron.skills.risk_management.skill import Risk_managementSkill
from ultron.skills.self_awareness.skill import SelfAwarenessSkill
from ultron.skills.system.skill import SystemSkill
from ultron.skills.terminal.skill import TerminalSkill


def test_every_builtin_skill_declares_permissions_for_each_action():
    builtins = (
        BacktestingSkill,
        FileSkill,
        FinancialSkill,
        Financial_analysisSkill,
        ImprovementSkill,
        Market_dataSkill,
        ProjectSkill,
        Risk_managementSkill,
        SelfAwarenessSkill,
        SystemSkill,
        TerminalSkill,
    )

    for skill in builtins:
        assert skill.action_permissions
        assert set(skill.action_permissions) == set(skill.actions)
        SkillPermissionPolicy.validate_registration(skill)


def test_financial_market_data_actions_declare_market_read_capability():
    assert "market.data.read" in (
        FinancialSkill.action_permissions["position_size_recommendation"]
    )
    assert "market.data.read" in (
        FinancialSkill.action_permissions["paper_performance_analysis"]
    )
    assert "market.data.read" not in (
        FinancialSkill.action_permissions["market_breadth_analysis"]
    )
    assert "trading.paper.execute" not in (
        FinancialSkill.action_permissions["market_breadth_analysis"]
    )
    assert not SkillPermissionPolicy.permits_external_adapter(
        FinancialSkill(financial_agent=object()),
        "paper_trade",
    )


def test_content_and_trading_domains_cannot_cross_grant_permissions():
    class ContentSkill(Skill):
        name = "content"
        actions = ["research"]
        domain = "content"
        action_permissions = {
            "research": frozenset({"trading.analysis"}),
        }

        def execute(self, action, **kwargs):
            return {}

    class TradingSkill(Skill):
        name = "trading"
        actions = ["analyze"]
        domain = "trading"
        action_permissions = {
            "analyze": frozenset({"publishing.prepare"}),
        }

        def execute(self, action, **kwargs):
            return {}

    with pytest.raises(PermissionError, match="outside that domain"):
        SkillPermissionPolicy.validate_registration(ContentSkill)
    with pytest.raises(PermissionError, match="outside that domain"):
        SkillPermissionPolicy.validate_registration(TradingSkill)


def test_live_trading_capability_is_rejected():
    class LiveSkill(Skill):
        name = "live-trading"
        actions = ["submit"]
        domain = "trading"
        action_permissions = {
            "submit": frozenset({"trading.live.execute"}),
        }

        def execute(self, action, **kwargs):
            raise AssertionError("Live execution must remain unavailable.")

    with pytest.raises(PermissionError, match="Live-trading capability"):
        SkillPermissionPolicy.validate_registration(LiveSkill)


def test_permissionless_skill_is_default_denied_and_audited():
    class UnpermissionedSkill(Skill):
        name = "unpermissioned"
        actions = ["search"]

        def __init__(self):
            self.calls = 0

        def execute(self, action, **kwargs):
            self.calls += 1
            return {}

    skill = UnpermissionedSkill()
    manager = SkillManager()
    manager.register(skill)

    with pytest.raises(PermissionError, match="permission contract"):
        manager.execute("search", query="market")

    assert skill.calls == 0
    assert manager.permission_denials[-1]["classification"] == (
        "SKILL_CAPABILITY_DENIED"
    )


def test_protected_skill_action_requires_authorized_caller():
    manager = SkillManager()
    skill = FinancialSkill(financial_agent=object())
    manager.register(skill)

    with pytest.raises(PermissionError, match="trusted authorization"):
        manager.execute(
            "paper_trade",
            symbol="ABC",
            analysis={"symbol": "ABC"},
        )

    assert manager.permission_denials[-1]["classification"] == (
        "CALLER_AUTHORIZATION_REQUIRED"
    )
