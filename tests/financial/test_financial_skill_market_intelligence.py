from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.models import OrderSide, Position, Trade
from ultron.financial.trading.paper_broker import PaperBroker
from ultron.financial.trading.reliability.reconciliation import (
    PortfolioReconciler,
)
from ultron.skills.financial.skill import FinancialSkill
from ultron.skills.manager import SkillManager


def _manager(financial_agent: object | None = None) -> SkillManager:
    manager = SkillManager()
    manager.register(
        FinancialSkill(
            financial_agent if financial_agent is not None else SimpleNamespace()
        )
    )
    return manager


def _provenance() -> dict[str, str]:
    return {
        "source": "caller supplied",
        "observed_at": datetime.now(UTC).isoformat(),
    }


def test_market_regime_action_returns_analysis_with_unverified_provenance():
    result = _manager().execute(
        "market_regime_analysis",
        symbol="abc",
        closes=[100.0, 101.0, 102.0, 103.0],
        **_provenance(),
    )

    assert result["success"] is True
    assert result["analysis_only"] is True
    assert result["symbol"] == "ABC"
    assert result["data_provenance"]["source_verified"] is False
    assert result["data_provenance"]["freshness_verified"] is False
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": ""},
        {"observed_at": "not-a-timestamp"},
        {"observed_at": "2026-01-01T00:00:00"},
        {
            "observed_at": (
                datetime.now(UTC) + timedelta(minutes=5)
            ).isoformat()
        },
        {"closes": [100.0, float("nan"), 102.0]},
        {"closes": [100.0, 101.0]},
        {"closes": [1e-320, 1e308, 1e-320]},
    ],
)
def test_market_regime_action_rejects_malformed_or_unsafe_inputs(overrides):
    kwargs = {
        "symbol": "ABC",
        "closes": [100.0, 101.0, 102.0],
        **_provenance(),
    }
    kwargs.update(overrides)

    if "closes" in overrides and overrides["closes"] == [
        1e-320,
        1e308,
        1e-320,
    ]:
        with pytest.raises(ValueError, match="non-finite metrics"):
            _manager().execute("market_regime_analysis", **kwargs)
    else:
        with pytest.raises(ValueError, match="validation failed"):
            _manager().execute("market_regime_analysis", **kwargs)


def test_market_breadth_action_discloses_caller_source_and_serializes():
    result = _manager().execute(
        "market_breadth_analysis",
        advancers=2,
        decliners=1,
        unchanged=1,
        **_provenance(),
    )

    assert result["analysis_only"] is True
    assert result["breadth_ratio"] == pytest.approx(1 / 3)
    assert result["advancers_percent"] == 50.0
    assert result["limitations"].startswith("Uses caller-supplied")
    assert result["data_provenance"]["source_verified"] is False
    assert result["data_provenance"]["freshness_verified"] is False
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "overrides",
    [
        {"advancers": True},
        {"decliners": -1},
        {"unchanged": 10_000_001},
        {"advancers": 0, "decliners": 0},
        {"unexpected": True},
    ],
)
def test_market_breadth_action_rejects_invalid_counts(overrides):
    kwargs = {
        "advancers": 3,
        "decliners": 2,
        "unchanged": 0,
        **_provenance(),
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError, match="validation failed"):
        _manager().execute("market_breadth_analysis", **kwargs)


def test_portfolio_reconciliation_action_uses_ultron_reconciler_read_only():
    controller = SimpleNamespace(reconciler=PortfolioReconciler())
    manager = _manager(SimpleNamespace(trading_controller=controller))

    result = manager.execute(
        "portfolio_reconciliation",
        internal={"cash": 100.0, "positions": {}},
        broker_snapshot={"cash": 100.0, "positions": {}},
    )

    assert result["success"] is True
    assert result["read_only"] is True
    assert result["reconciliation"]["reconciled"] is True


def test_portfolio_reconciliation_action_reports_malformed_snapshot():
    controller = SimpleNamespace(reconciler=PortfolioReconciler())
    manager = _manager(SimpleNamespace(trading_controller=controller))

    result = manager.execute(
        "portfolio_reconciliation",
        internal={"cash": 100.0, "positions": {}},
        broker_snapshot={"cash": float("inf"), "positions": {}},
    )

    assert result["success"] is False
    assert result["read_only"] is True
    assert result["reconciliation"]["valid"] is False
    json.dumps(result, allow_nan=False)


def test_paper_performance_analysis_handles_no_positions_without_market_data():
    broker = PaperBroker(initial_capital=1_000.0)
    engine = PaperTradingEngine(broker)
    manager = _manager(
        SimpleNamespace(trading_controller=SimpleNamespace(trading_engine=engine))
    )

    result = manager.execute("paper_performance_analysis")

    assert result["success"] is True
    assert result["analysis_only"] is True
    assert result["statistics"]["open_positions"] == 0
    assert result["quote_timestamps"] == {}
    json.dumps(result, allow_nan=False)


def test_paper_performance_analysis_rejects_ignored_input_fields():
    with pytest.raises(ValueError, match="validation failed"):
        _manager().execute("paper_performance_analysis", symbol="ABC")


def test_paper_performance_analysis_uses_fresh_marks_for_positions():
    broker = PaperBroker(initial_capital=1_000.0)
    broker.portfolio.positions["ABC"] = Position(
        symbol="ABC",
        quantity=2.0,
        average_price=100.0,
    )
    adapter = PaperMarketAdapter(broker, prices={"ABC": 110.0})
    adapter.connect()
    engine = PaperTradingEngine(broker)
    manager = _manager(
        SimpleNamespace(
            trading_controller=SimpleNamespace(
                trading_engine=engine,
                market_data=MarketDataService(adapter),
            )
        )
    )

    result = manager.execute("paper_performance_analysis")

    assert result["success"] is True
    assert result["statistics"]["current_equity"] == 1_220.0
    assert result["quote_timestamps"]["ABC"]
    json.dumps(result, allow_nan=False)


def test_paper_performance_analysis_rejects_freshness_spoofed_stale_quote():
    broker = PaperBroker(initial_capital=1_000.0)
    broker.portfolio.positions["ABC"] = Position(
        symbol="ABC",
        quantity=2.0,
        average_price=100.0,
    )
    timestamp = datetime.now(UTC) - timedelta(minutes=2)
    market_data = SimpleNamespace(
        adapter=SimpleNamespace(max_quote_age_seconds=30.0),
        get_current_price=lambda symbol: {
            "valid": True,
            "symbol": symbol,
            "price": 110.0,
            "timestamp": timestamp,
        },
    )
    engine = PaperTradingEngine(broker)
    manager = _manager(
        SimpleNamespace(
            trading_controller=SimpleNamespace(
                trading_engine=engine,
                market_data=market_data,
            )
        )
    )

    result = manager.execute("paper_performance_analysis")

    assert result["success"] is False
    assert "stale or future-dated" in result["reason"]


def test_paper_performance_analysis_normalizes_unbounded_profit_factor():
    broker = PaperBroker(initial_capital=1_000.0)
    broker.portfolio.trades.append(
        Trade(
            order_id="paper-1",
            symbol="ABC",
            side=OrderSide.SELL,
            quantity=1.0,
            price=110.0,
            fees=0.0,
            realized_pnl=10.0,
        )
    )
    manager = _manager(
        SimpleNamespace(
            trading_controller=SimpleNamespace(
                trading_engine=PaperTradingEngine(broker)
            )
        )
    )

    result = manager.execute("paper_performance_analysis")

    assert result["success"] is True
    assert result["statistics"]["profit_factor"] is None
    assert (
        result["statistics"]["profit_factor_status"]
        == "unbounded_no_losses"
    )
    json.dumps(result, allow_nan=False)
