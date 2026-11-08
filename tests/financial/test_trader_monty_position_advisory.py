from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
from ultron.financial.trading.market_data.service import MarketDataService
from ultron.financial.trading.paper_broker import PaperBroker
from ultron.skills.financial.skill import FinancialSkill
from ultron.skills.manager import SkillManager


def test_external_position_sizing_advisory_uses_ultron_caps_without_execution():
    broker = PaperBroker(initial_capital=100_000.0, commission_rate=0.0)
    trading_engine = PaperTradingEngine(
        broker,
        max_position_percent=10.0,
        risk_per_trade_percent=1.0,
    )
    adapter = PaperMarketAdapter(broker, prices={"ABC": 100.0})
    adapter.connect()
    market_data = MarketDataService(adapter)
    agent = SimpleNamespace(
        trading_controller=SimpleNamespace(
            trading_engine=trading_engine,
            market_data=market_data,
        )
    )
    manager = SkillManager()
    manager.register(FinancialSkill(agent))

    recommendation = manager.execute(
        "position_size_recommendation",
        symbol="abc",
        entry_price=100.0,
        stop_loss=95.0,
    )

    assert recommendation["success"] is True
    assert recommendation["advisory_only"] is True
    assert recommendation["symbol"] == "ABC"
    assert recommendation["risk"]["requested_quantity"] == 200.0
    assert recommendation["risk"]["exposure_cap"] == 100.0
    assert recommendation["recommended_quantity"] == 100.0
    assert recommendation["market_quote"]["price"] == 100.0
    assert recommendation["portfolio_risk"]["approved"] is True
    assert broker.snapshot({"ABC": 100.0})["positions"] == {}
    assert broker._orders == {}


def test_position_size_advisory_rejects_extreme_integer_inputs():
    broker = PaperBroker()
    trading_engine = PaperTradingEngine(broker)
    agent = SimpleNamespace(
        trading_controller=SimpleNamespace(trading_engine=trading_engine)
    )
    manager = SkillManager()
    manager.register(FinancialSkill(agent))

    with pytest.raises(ValueError, match="validation failed"):
        manager.execute(
            "position_size_recommendation",
            symbol="ABC",
            entry_price=10**10000,
            stop_loss=1.0,
        )

    assert broker._orders == {}


def test_position_size_advisory_rejects_stale_market_quote_without_execution():
    broker = PaperBroker()
    trading_engine = PaperTradingEngine(broker)
    adapter = PaperMarketAdapter(broker, prices={"ABC": 100.0})
    adapter.connect()
    adapter.get_snapshot = lambda symbol: {
        "symbol": symbol,
        "price": 100.0,
        "timestamp": datetime.now(UTC) - timedelta(minutes=1),
    }
    agent = SimpleNamespace(
        trading_controller=SimpleNamespace(
            trading_engine=trading_engine,
            market_data=MarketDataService(adapter),
        )
    )
    manager = SkillManager()
    manager.register(FinancialSkill(agent))

    recommendation = manager.execute(
        "position_size_recommendation",
        symbol="ABC",
        entry_price=100.0,
        stop_loss=95.0,
    )

    assert recommendation["success"] is False
    assert recommendation["stage"] == "market_data"
    assert "stale" in recommendation["reason"].lower()
    assert broker._orders == {}


def test_position_size_advisory_rejects_naive_quote_timestamp():
    broker = PaperBroker()
    trading_engine = PaperTradingEngine(broker)
    adapter = PaperMarketAdapter(broker, prices={"ABC": 100.0})
    adapter.connect()
    market_data = SimpleNamespace(
        get_current_price=lambda symbol: {
            "valid": True,
            "symbol": symbol,
            "price": 100.0,
            "timestamp": datetime(2026, 1, 1),
        },
        adapter=adapter,
    )
    agent = SimpleNamespace(
        trading_controller=SimpleNamespace(
            trading_engine=trading_engine,
            market_data=market_data,
        )
    )
    manager = SkillManager()
    manager.register(FinancialSkill(agent))

    recommendation = manager.execute(
        "position_size_recommendation",
        symbol="ABC",
        entry_price=100.0,
        stop_loss=95.0,
    )

    assert recommendation["success"] is False
    assert recommendation["stage"] == "market_data"
    assert "timezone-aware" in recommendation["reason"]
    assert broker._orders == {}
