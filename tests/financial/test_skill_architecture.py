from ultron.skills.base import Skill
from ultron.skills.market_data.skill import Market_dataSkill
from ultron.skills.financial_analysis.skill import Financial_analysisSkill
from ultron.skills.risk_management.skill import Risk_managementSkill
from ultron.skills.backtesting.skill import BacktestingSkill


class FakeMarketProvider:
    def get_price(self, symbol):
        class Price:
            pass
        result = Price()
        result.symbol = symbol
        result.price = 123.45
        from datetime import datetime
        result.timestamp = datetime(2026, 1, 1)
        return result

    def get_history(self, symbol, period="1y"):
        return FakeHistory()


class FakeHistory:
    def to_dict(self):
        return {"Close": {"0": 123.45}}


class FakeMarketAnalyzer:
    def analyze(self, history):
        return {"success": True, "trend": "bullish"}


class FakeCompany:
    def __init__(self):
        self.symbol = "TEST"
        self.profit_margin = 0.2
        self.debt_to_equity = 20
        self.pe_ratio = 15
        self.revenue_growth = 0.1


class FakeCompanyProvider:
    def get_company(self, symbol):
        return FakeCompany()


class FakeFinancialAnalyzer:
    def analyze(self, company, market):
        return {"score": 70, "rating": "strong"}


class FakeRiskAnalyzer:
    def analyze(self, company, market):
        return {"risk_score": 10, "risk_level": "low"}


def test_financial_skills_share_common_skill_contract():
    market = Market_dataSkill(
        provider=FakeMarketProvider(),
        analyzer=FakeMarketAnalyzer(),
        company_provider=FakeCompanyProvider(),
    )
    analysis = Financial_analysisSkill(
        analyzer=FakeFinancialAnalyzer(),
        market=market,
    )
    risk = Risk_managementSkill(analyzer=FakeRiskAnalyzer())
    backtesting = BacktestingSkill()

    for skill in (market, analysis, risk, backtesting):
        assert isinstance(skill, Skill)
        assert skill.name
        assert skill.description
        assert skill.version
        assert skill.actions
        assert skill.info()["name"] == skill.name
        for action in skill.actions:
            assert skill.supports(action)


def test_market_skill_supports_canonical_and_legacy_execution():
    skill = Market_dataSkill(
        provider=FakeMarketProvider(),
        analyzer=FakeMarketAnalyzer(),
        company_provider=FakeCompanyProvider(),
    )

    assert skill.validate("price", symbol="test")
    assert skill.execute("price", symbol="test")["symbol"] == "TEST"
    assert skill.execute({"action": "price", "symbol": "test"})["price"] == 123.45
    assert not skill.validate("price", symbol="")
    assert not skill.validate("unknown", symbol="TEST")


def test_analysis_and_risk_skills_use_common_action_contract():
    market = Market_dataSkill(
        provider=FakeMarketProvider(),
        analyzer=FakeMarketAnalyzer(),
        company_provider=FakeCompanyProvider(),
    )
    analysis = Financial_analysisSkill(
        analyzer=FakeFinancialAnalyzer(),
        market=market,
    )
    risk = Risk_managementSkill(analyzer=FakeRiskAnalyzer())

    assert analysis.validate("analyze", symbol="TEST")
    result = analysis.execute("analyze", symbol="TEST")
    assert result["success"] is True
    assert result["analysis"]["rating"] == "strong"

    company = {"profit_margin": 0.2, "debt_to_equity": 20}
    market_data = {"volatility": 0.01, "trend": "bullish"}
    assert risk.validate("analyze", company=company, market=market_data)
    assert risk.execute("analyze", company=company, market=market_data)["success"] is True
