import sqlite3
from datetime import UTC, datetime

import pytest

from ultron.financial.trading.controller import FinancialTradingController
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.market.adapters.paper import PaperMarketAdapter
from ultron.financial.trading.paper_broker import PaperBroker
from ultron.financial.trading.risk.portfolio_risk import (
    PortfolioRiskEngine,
    PortfolioRiskLimits,
)
from ultron.financial.trading.signal import SignalAction, TradingSignal


def make_controller():
    broker = PaperBroker(initial_capital=100_000)
    engine = PaperTradingEngine(broker)
    adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()
    controller = FinancialTradingController(
        engine,
        market_adapter=adapter,
    )
    return controller, broker


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


def test_controller_buy_end_to_end():
    controller, broker = make_controller()

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is True
    assert result["execution"]["status"] == "filled"
    assert result["signal"].action.value == "buy"

    position = broker.snapshot(
        {"TEST": 100.0}
    )["positions"]["TEST"]

    assert position["quantity"] > 0


def test_portfolio_risk_rejection_prevents_paper_submission():
    calls = []

    class SubmissionSpy(PaperBroker):
        def submit(self, order):
            calls.append(order)
            return super().submit(order)

    broker = SubmissionSpy(initial_capital=100_000)
    engine = PaperTradingEngine(
        broker,
        portfolio_risk_engine=PortfolioRiskEngine(
            PortfolioRiskLimits(max_single_position_percent=1.0)
        ),
    )
    before_submit_calls = []
    signal = TradingSignal(
        symbol="TEST",
        action=SignalAction.BUY,
        price=100.0,
        stop_loss=95.0,
    )

    result = engine.execute(
        signal,
        before_submit=before_submit_calls.append,
    )

    assert result["success"] is False
    assert result["stage"] == "portfolio_risk"
    assert result["portfolio_risk"]["approved"] is False
    assert result["submission_started"] is False
    assert before_submit_calls == []
    assert calls == []
    assert broker._orders == {}
    assert broker.snapshot({"TEST": 100.0})["trade_count"] == 0


def test_missing_stop_loss_blocks_buy_before_broker_submission():
    calls = []

    class SubmissionSpy(PaperBroker):
        def submit(self, order):
            calls.append(order)
            return super().submit(order)

    broker = SubmissionSpy(initial_capital=100_000)
    engine = PaperTradingEngine(broker)
    signal = TradingSignal(
        symbol="TEST",
        action=SignalAction.BUY,
        price=100.0,
    )

    result = engine.execute(signal)

    assert result["success"] is False
    assert "stop-loss" in result["reason"].lower()
    assert calls == []
    assert broker._orders == {}


def test_direct_risk_advisory_discloses_average_entry_fallback():
    from ultron.financial.trading.models import Position

    broker = PaperBroker(initial_capital=100_000)
    broker.portfolio.positions["OLD"] = Position(
        symbol="OLD",
        quantity=10.0,
        average_price=50.0,
    )
    engine = PaperTradingEngine(broker)

    evaluation = engine.evaluate_buy(
        symbol="TEST",
        entry_price=100.0,
        stop_loss=95.0,
        market_prices={"TEST": 100.0},
    )

    assert evaluation["approved"] is True
    assert evaluation["risk_coverage"]["existing_position_marks"] == {
        "OLD": "average_entry_fallback"
    }
    assert (
        evaluation["risk_coverage"]["currency_exposure"]
        == "unsupported_missing_currency_metadata"
    )
    assert (
        evaluation["risk_coverage"]["correlation_risk"]
        == "unsupported_missing_verified_matrix"
    )


def test_portfolio_risk_rejection_releases_controller_idempotency_claim():
    broker = PaperBroker(initial_capital=100_000)
    engine = PaperTradingEngine(
        broker,
        portfolio_risk_engine=PortfolioRiskEngine(
            PortfolioRiskLimits(max_single_position_percent=1.0)
        ),
    )
    adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()
    controller = FinancialTradingController(
        engine,
        market_adapter=adapter,
    )

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is False
    assert result["execution"]["stage"] == "portfolio_risk"
    assert controller.idempotency.size() == 0
    assert broker._orders == {}
    with sqlite3.connect(controller.idempotency.db_path) as db:
        row = db.execute(
            "SELECT approved, decision_json FROM risk_decisions "
            "WHERE idempotency_key = ?",
            (result["idempotency_key"],),
        ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert '"portfolio_risk"' in row[1]


def test_approved_risk_decision_is_persisted_before_broker_submission():
    class AuditCheckingBroker(PaperBroker):
        idempotency_store = None

        def submit(self, order):
            with sqlite3.connect(self.idempotency_store.db_path) as db:
                row = db.execute(
                    "SELECT approved FROM risk_decisions "
                    "WHERE symbol = ? AND action = 'buy' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (order.symbol,),
                ).fetchone()
            assert row == (1,)
            return super().submit(order)

    broker = AuditCheckingBroker(initial_capital=100_000)
    engine = PaperTradingEngine(broker)
    adapter = PaperMarketAdapter(broker, prices={"TEST": 100.0})
    adapter.connect()
    controller = FinancialTradingController(
        engine,
        market_adapter=adapter,
    )
    broker.idempotency_store = controller.idempotency

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is True
    assert result["execution"]["risk_decision"]["approved"] is True


def test_risk_audit_failure_blocks_broker_submission():
    controller, broker = make_controller()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Risk audit unavailable.")

    controller.idempotency.record_risk_decision = fail_audit
    with pytest.raises(RuntimeError, match="Risk audit unavailable"):
        controller.execute(
            analysis=make_analysis(),
            price=100.0,
            price_timestamp=datetime.now(UTC),
        )

    assert broker._orders == {}
    assert controller.idempotency.has_processed(
        "TEST:buy:100.00000000:trend_following"
    )


def test_risk_reducing_sell_remains_available_and_cannot_short():
    from ultron.financial.trading.models import Position

    broker = PaperBroker(initial_capital=100_000)
    broker.portfolio.positions["TEST"] = Position(
        symbol="TEST",
        quantity=10.0,
        average_price=100.0,
    )
    engine = PaperTradingEngine(
        broker,
        portfolio_risk_engine=PortfolioRiskEngine(
            PortfolioRiskLimits(max_single_position_percent=1.0)
        ),
    )
    sell = TradingSignal(
        symbol="TEST",
        action=SignalAction.SELL,
        price=100.0,
    )

    sold = engine.execute(sell)
    oversold = engine.execute(sell)

    assert sold["success"] is True
    assert sold["quantity"] == 10.0
    assert oversold["success"] is False
    assert oversold["status"] == "rejected"
    assert broker.snapshot({"TEST": 100.0})["positions"] == {}
    assert broker.snapshot({"TEST": 100.0})["trade_count"] == 1


def test_take_profit_exit():
    controller, broker = make_controller()

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    signal = result["signal"]

    exit_result = controller.trading_engine.monitor_exit(
        signal,
        market_price=108.0,
    )

    assert exit_result["success"] is True
    assert exit_result["status"] == "filled"
    assert exit_result["exit_reason"] == "take_profit"

    assert broker.snapshot(
        {"TEST": 108.0}
    )["positions"] == {}


def test_stop_loss_exit():
    controller, broker = make_controller()

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    signal = result["signal"]

    exit_result = controller.trading_engine.monitor_exit(
        signal,
        market_price=96.0,
    )

    assert exit_result["success"] is True
    assert exit_result["status"] == "filled"
    assert exit_result["exit_reason"] == "stop_loss"

    assert broker.snapshot(
        {"TEST": 96.0}
    )["positions"] == {}


def test_duplicate_order_is_blocked():
    controller, broker = make_controller()

    first = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    second = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert first["success"] is True
    assert first["execution"]["status"] == "filled"

    assert second["success"] is True
    assert second["status"] == "duplicate"

    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 1


def test_hold_does_not_create_order():
    controller, broker = make_controller()

    analysis = make_analysis()
    analysis["decision"]["decision"] = "avoid"
    analysis["decision"]["confidence"] = 0.0

    result = controller.execute(
        analysis=analysis,
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is True
    assert result["execution"]["status"] == "held"

    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 0


def test_paused_trading_blocks_buy():
    controller, broker = make_controller()

    controller.trading_control.pause(
        "Emergency pause."
    )

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert result["success"] is False
    assert result["status"] == "paused"
    assert result["stage"] == "trading_control"

    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 0


def test_stale_price_blocks_trade():
    controller, broker = make_controller()

    controller.market_data.adapter.get_snapshot = lambda symbol: {
        "symbol": symbol,
        "price": 100.0,
        "timestamp": datetime.fromtimestamp(0, UTC),
    }

    result = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    from ultron.financial.trading.reliability.price_guard import (
        PriceSnapshot,
    )

    snapshot = PriceSnapshot(
        symbol="TEST",
        price=100.0,
        timestamp=datetime.fromtimestamp(
            0,
            UTC,
        ),
    )

    guard = controller.price_guard.validate(
        snapshot
    )

    assert guard["valid"] is False
    assert "stale" in guard["reason"].lower()

    assert result["success"] is False
    assert result["stage"] == "market_data"
    assert "stale" in result["reason"].lower()
    assert broker.snapshot({"TEST": 100.0})["trade_count"] == 0


def test_daily_loss_guard_blocks_new_trade():
    controller, broker = make_controller()

    controller.daily_starting_equity = 100_000.0

    broker.portfolio.cash = 97_000.0
    broker.portfolio.positions.clear()

    blocked = controller.execute(
        analysis=make_analysis(),
        price=100.0,
        price_timestamp=datetime.now(UTC),
    )

    assert blocked["success"] is False
    assert blocked["status"] == "halted"
    assert blocked["stage"] == "daily_loss_guard"

    assert broker.snapshot(
        {"TEST": 100.0}
    )["trade_count"] == 0


def test_portfolio_reconciliation_passes_when_state_matches():
    controller, broker = make_controller()

    portfolio = broker.snapshot(
        {"TEST": 100.0}
    )

    result = controller.reconciler.reconcile(
        internal={
            "cash": portfolio["cash"],
            "positions": portfolio["positions"],
        },
        broker=portfolio,
    )

    assert result["valid"] is True
    assert result["reconciled"] is True
    assert result["cash_match"] is True
    assert result["positions_match"] is True
    assert result["differences"] == []


def test_portfolio_reconciliation_detects_position_mismatch():
    controller, broker = make_controller()

    # Use the actual Position model instead of inserting
    # a raw dictionary into Portfolio.positions.
    from ultron.financial.trading.models import Position

    broker.portfolio.positions["TEST"] = Position(
        symbol="TEST",
        quantity=100.0,
        average_price=100.0,
    )

    portfolio = broker.snapshot(
        {"TEST": 100.0}
    )

    internal = {
        "cash": portfolio["cash"],
        "positions": {
            "TEST": {
                "quantity": 50.0,
                "average_price": 100.0,
            }
        },
    }

    result = controller.reconciler.reconcile(
        internal=internal,
        broker=portfolio,
    )

    assert result["valid"] is False
    assert result["reconciled"] is False
    assert result["positions_match"] is False
    assert len(result["differences"]) == 1

    assert result["differences"][0]["type"] == (
        "position_quantity"
    )


def test_portfolio_reconciliation_rejects_incomplete_or_malformed_snapshots():
    controller, _ = make_controller()
    valid = {"cash": 100.0, "positions": {}}
    invalid_pairs = [
        ({"positions": {}}, {"cash": 100.0, "positions": {}}),
        (valid, {"cash": 100.0}),
        ({"cash": 100.0, "positions": []}, valid),
        (valid, {"cash": 100.0, "positions": {"TEST": []}}),
        (valid, {"cash": 100.0, "positions": {"TEST": {"quantity": float("nan")}}}),
        ({"cash": float("inf"), "positions": {}}, valid),
    ]

    for internal, broker in invalid_pairs:
        result = controller.reconciler.reconcile(internal, broker)
        assert result["valid"] is False
        assert result["reconciled"] is False
        assert result["cash_match"] is False
        assert result["positions_match"] is False


def test_portfolio_reconciliation_rejects_invalid_tolerances():
    controller, _ = make_controller()
    portfolio = {"cash": 100.0, "positions": {}}

    for cash_tolerance, quantity_tolerance in [
        (float("nan"), 0.0),
        (0.0, float("inf")),
        (-0.01, 0.0),
        ("invalid", 0.0),
        (True, 0.0),
    ]:
        result = controller.reconciler.reconcile(
            portfolio,
            portfolio,
            cash_tolerance=cash_tolerance,
            quantity_tolerance=quantity_tolerance,
        )
        assert result["valid"] is False
        assert result["reconciled"] is False


def test_signal_generator_uses_atr_for_risk_levels():
    from ultron.financial.trading.signal_generator import (
        FinancialSignalGenerator,
    )

    generator = FinancialSignalGenerator()

    signal = generator.generate(
        make_analysis(),
        100.0,
        indicators={"atr_14": 2.0},
    )

    assert signal.action.value == "buy"
    assert signal.stop_loss == 96.0
    assert signal.take_profit == 106.0


def test_signal_generator_falls_back_without_atr():
    from ultron.financial.trading.signal_generator import (
        FinancialSignalGenerator,
    )

    generator = FinancialSignalGenerator()

    signal = generator.generate(
        make_analysis(),
        100.0,
    )

    assert signal.stop_loss == 96.0
    assert signal.take_profit == 108.0


def test_signal_generator_ignores_invalid_atr():
    from ultron.financial.trading.signal_generator import (
        FinancialSignalGenerator,
    )

    generator = FinancialSignalGenerator()

    signal = generator.generate(
        make_analysis(),
        100.0,
        indicators={"atr_14": "invalid"},
    )

    assert signal.stop_loss == 96.0
    assert signal.take_profit == 108.0
