from datetime import UTC, datetime, timedelta

from ultron.financial.trading.controller import (
    FinancialTradingController,
)
from ultron.financial.trading.engine import (
    PaperTradingEngine,
)
from ultron.financial.trading.market.adapters.paper import (
    PaperMarketAdapter,
)
from ultron.financial.trading.market_data.service import (
    MarketDataService,
)
from ultron.financial.trading.models import Order, OrderSide
from ultron.financial.trading.paper_broker import (
    PaperBroker,
)


def make_stack():
    broker = PaperBroker(initial_capital=100_000)

    adapter = PaperMarketAdapter(
        broker,
        prices={"TEST": 100.0},
    )
    adapter.connect()

    market_data = MarketDataService(adapter)

    engine = PaperTradingEngine(broker)
    controller = FinancialTradingController(
        engine,
        market_data=market_data,
    )

    return (
        controller,
        broker,
        adapter,
        market_data,
    )


def make_analysis():
    return {
        "symbol": "TEST",
        "decision": {
            "decision": "consider",
            "confidence": 0.90,
        },
        "reasoning": {
            "confidence": 0.90,
        },
        "risk": {
            "risk": {
                "risk_level": "low",
            }
        },
        "strategies": ["trend_following"],
    }


def test_market_price_can_feed_controller():
    controller, broker, _, market_data = make_stack()

    market_result = market_data.get_current_price(
        "TEST"
    )

    assert market_result["valid"] is True
    assert market_result["price"] == 100.0

    result = controller.execute(
        analysis=make_analysis(),
        price=market_result["price"],
        price_timestamp=market_result["timestamp"],
    )

    assert result["success"] is True
    assert result["execution"]["status"] == "filled"

    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 1


def test_market_price_change_can_be_used_by_controller():
    controller, broker, adapter, market_data = make_stack()

    adapter.prices["TEST"] = 105.0

    market_result = market_data.get_current_price(
        "TEST"
    )

    result = controller.execute(
        analysis=make_analysis(),
        price=market_result["price"],
        price_timestamp=market_result["timestamp"],
    )

    assert result["success"] is True
    assert result["execution"]["status"] == "filled"

    assert broker.snapshot(
        {"TEST": 105.0}
    )["trade_count"] == 1


def test_stale_market_timestamp_is_rejected_by_controller():
    controller, broker, adapter, _ = make_stack()
    adapter.get_snapshot = lambda symbol: {
        "symbol": symbol,
        "price": 100.0,
        "timestamp": datetime.now(UTC) - timedelta(seconds=60),
    }

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is False
    assert result["stage"] == "market_data"
    assert "stale" in result["reason"].lower()

    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 0


def test_controller_ignores_caller_entry_price_and_uses_verified_quote():
    controller, broker, _, _ = make_stack()

    result = controller.execute(
        analysis=make_analysis(),
        price=9999.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is True
    assert result["execution"]["price"] == 100.0
    assert broker._orders
    assert next(iter(broker._orders.values())).price == 100.0


def test_controller_rejects_missing_instrument_metadata():
    class MissingInstrumentAdapter(PaperMarketAdapter):
        def get_instrument(self, symbol):
            return None

    broker = PaperBroker(initial_capital=100_000)
    adapter = MissingInstrumentAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()
    controller = FinancialTradingController(
        PaperTradingEngine(broker),
        market_adapter=adapter,
    )

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is False
    assert result["stage"] == "instrument_validation"
    assert broker._orders == {}


def test_controller_uses_fresh_marks_for_existing_positions():
    from ultron.financial.trading.models import Position

    broker = PaperBroker(initial_capital=100_000)
    broker.portfolio.positions["OLD"] = Position(
        symbol="OLD",
        quantity=10.0,
        average_price=50.0,
    )
    adapter = PaperMarketAdapter(
        broker,
        prices={"TEST": 100.0, "OLD": 55.0},
    )
    adapter.connect()
    controller = FinancialTradingController(
        PaperTradingEngine(broker),
        market_adapter=adapter,
    )

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is True
    coverage = result["execution"]["risk_coverage"]
    assert coverage["existing_position_marks"] == {
        "OLD": "provided_market_price"
    }
    assert coverage["currency_exposure"] == "supported"
    assert coverage["correlation_risk"] == "unsupported_missing_verified_matrix"
    assert result["execution"]["portfolio_risk"]["currency_exposure"] == (
        550.0 + result["execution"]["quantity"] * 100.0
    )


def test_paper_market_data_adapter_cannot_submit_orders():
    broker = PaperBroker(initial_capital=100_000)
    adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()

    order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        quantity=1.0,
        price=100.0,
    )

    try:
        adapter.place_order(order)
        assert False, "Expected direct adapter submission to be blocked."
    except RuntimeError as exc:
        assert "FinancialTradingController" in str(exc)
    assert broker._orders == {}


def test_market_adapter_price_validation_reaches_controller():
    controller, broker, adapter, market_data = make_stack()

    adapter.prices["TEST"] = -10.0

    try:
        market_data.get_current_price("TEST")
        assert False, "Expected invalid price error."
    except ValueError as exc:
        assert "invalid paper price" in str(exc).lower()

    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 0


def test_controller_rejects_market_adapter_without_source_timestamp():
    class PriceOnlyAdapter:
        def get_price(self, symbol):
            return 100.0

        def is_market_open(self, symbol):
            return True

    broker = PaperBroker(initial_capital=100_000)
    controller = FinancialTradingController(
        PaperTradingEngine(broker),
        market_adapter=PriceOnlyAdapter(),
    )

    result = controller.execute_from_market(make_analysis())

    assert result["success"] is False
    assert result["stage"] == "market_data"
    assert "timestamped" in result["reason"]
    assert broker.snapshot({"TEST": 100.0})["trade_count"] == 0
