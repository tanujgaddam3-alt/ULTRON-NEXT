#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil, sys

root = Path.cwd()
files = [
    root / "ultron/infrastructure/container.py",
    root / "ultron/financial/agent/financial_agent.py",
    root / "ultron/skills/financial/skill.py",
]
if any(not p.is_file() for p in files):
    sys.exit("Run this script from ~/Projects/ULTRON_INTERFACE_FINAL.")
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

def edit(path, old, new, label):
    src = path.read_text()
    if src.count(old) != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {src.count(old)}. Stopped.")
    backup = path.with_suffix(path.suffix + f".{stamp}.bak")
    shutil.copy2(path, backup)
    path.write_text(src.replace(old, new, 1))
    print(f"Updated {path.relative_to(root)}; backup={backup.name}")

container = root / "ultron/infrastructure/container.py"
edit(container,
    "from ultron.financial.trading.controller import FinancialTradingController\n",
    "from ultron.financial.trading.controller import FinancialTradingController\n"
    "from ultron.financial.trading.market_data.service import MarketDataService\n"
    "from ultron.financial.trading.market_data.alpaca_adapter import AlpacaMarketAdapter\n",
    "market-data imports")
edit(container,
"""        self.financial_trading_controller = (
            FinancialTradingController(
                trading_engine=self.financial_trading_engine,
            )
        )
""",
"""        # Market data and order execution are separate settings. Alpaca supplies
        # prices here; the execution backend remains paper/simulation-only.
        market_provider = os.getenv(
            "ULTRON_FINANCIAL_MARKET_PROVIDER", "yahoo"
        ).strip().lower()
        if market_provider == "alpaca":
            self.financial_trading_market_service = MarketDataService(
                AlpacaMarketAdapter()
            )
        elif market_provider == "yahoo":
            # Yahoo's skill provider is not a MarketAdapter. Fail closed rather
            # than accepting an unverified price without a source timestamp.
            self.financial_trading_market_service = None
        else:
            raise RuntimeError(
                "Invalid ULTRON_FINANCIAL_MARKET_PROVIDER. "
                "Allowed values: yahoo, alpaca."
            )

        self.financial_trading_controller = (
            FinancialTradingController(
                trading_engine=self.financial_trading_engine,
                market_data=self.financial_trading_market_service,
            )
        )
""", "container controller wiring")

agent = root / "ultron/financial/agent/financial_agent.py"
edit(agent,
"""    def paper_trade(
        self,
        analysis: dict[str, Any],
        price: float,
    ) -> dict[str, Any]:
""",
"""    def paper_trade(
        self,
        analysis: dict[str, Any],
        price: float | None = None,
    ) -> dict[str, Any]:
""", "agent paper-trade signature")
edit(agent,
"""        try:
            return self.trading_controller.execute(
                analysis=analysis,
                price=price,
            )

        except (TypeError, ValueError) as exc:
""",
"""        try:
            # Caller-supplied prices lack a trusted source timestamp. Obtain the
            # price and timestamp through the controller's guarded market path.
            execute_from_market = getattr(
                self.trading_controller, "execute_from_market", None
            )
            if callable(execute_from_market):
                return execute_from_market(analysis=analysis)
            return {
                "success": False,
                "stage": "market_data",
                "reason": "Controller does not support validated market-data execution.",
            }

        except (TypeError, ValueError) as exc:
""", "agent guarded execution")

skill = root / "ultron/skills/financial/skill.py"
edit(skill,
"""        if action == "paper_trade":
            price = kwargs.get("price")

            if not isinstance(price, (int, float)):
                return False

            if price <= 0:
                return False

        return True
""",
"""        # Paper-trade price comes from the controller's validated market-data
        # path; caller-provided prices are not trusted for execution.
        return True
""", "skill validation")
edit(skill,
"""        symbol = kwargs.get("symbol")
        price = kwargs.get("price")

        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(
                "Paper trading requires a market symbol."
            )

        if not isinstance(price, (int, float)):
            raise ValueError(
                "Paper trading requires a numeric price."
            )

        if price <= 0:
            raise ValueError(
                "Paper trading price must be positive."
            )

        analysis = kwargs.get("analysis")
""",
"""        symbol = kwargs.get("symbol")

        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(
                "Paper trading requires a market symbol."
            )

        analysis = kwargs.get("analysis")
""", "skill price requirement")
edit(skill,
"""        return self.financial_agent.paper_trade(
            analysis=analysis,
            price=float(price),
        )
""",
"""        return self.financial_agent.paper_trade(
            analysis=analysis,
        )
""", "skill-to-agent handoff")

test = root / "tests/financial/test_market_data_paper_trade_handoff.py"
if test.exists():
    raise RuntimeError(f"Refusing to overwrite existing test: {test}")
test.parent.mkdir(parents=True, exist_ok=True)
test.write_text("""from ultron.skills.financial.skill import FinancialSkill


class FakeController:
    def __init__(self, configured=True):
        self.market_data = object() if configured else None
        self.calls = []

    def execute_from_market(self, analysis):
        self.calls.append(analysis)
        if self.market_data is None:
            return {"success": False, "stage": "market_data",
                    "reason": "Market data service is not configured."}
        return {"success": True, "status": "hold"}


class FakeAgent:
    def __init__(self, controller):
        self.trading_controller = controller

    def analyze(self, symbol, run_backtest=False):
        return {"success": True, "symbol": symbol,
                "decision": {"decision": "watch", "confidence": 0.6}}

    def paper_trade(self, analysis, price=None):
        return self.trading_controller.execute_from_market(analysis=analysis)


def test_paper_trade_does_not_require_manual_price():
    controller = FakeController()
    skill = FinancialSkill(FakeAgent(controller))
    assert skill.validate("paper_trade", symbol="AAPL")
    result = skill.execute("paper_trade", symbol="AAPL")
    assert result["success"] is True
    assert controller.calls[0]["symbol"] == "AAPL"


def test_paper_trade_fails_closed_without_market_data():
    controller = FakeController(configured=False)
    skill = FinancialSkill(FakeAgent(controller))
    result = skill.execute("paper_trade", symbol="AAPL")
    assert result["success"] is False
    assert result["stage"] == "market_data"


def test_supplied_analysis_uses_guarded_controller_path():
    controller = FakeController()
    skill = FinancialSkill(FakeAgent(controller))
    analysis = {"symbol": "MSFT", "decision": {"decision": "avoid"}}
    result = skill.execute("paper_trade", symbol="MSFT", analysis=analysis)
    assert result["success"] is True
    assert controller.calls == [analysis]
""")
print(f"Created {test.relative_to(root)}")
