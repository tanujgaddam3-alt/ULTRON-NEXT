import json

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ultron.financial.trading.market.models import Instrument, MarketType
from ultron.financial.trading.models import (
    BrokerOrderRecord,
    Order,
    OrderSide,
    OrderStatus,
)
from ultron.financial.trading.paper_broker import PaperBroker


def test_alpaca_paper_broker_uses_paper_client_and_broker_order_ids(monkeypatch):
    from ultron.financial.trading import alpaca_paper_broker

    class FakeClient:
        instances = []

        def __init__(self, api_key, secret_key, paper):
            self.paper = paper
            self.orders = {}
            self.cancelled = []
            self.__class__.instances.append(self)

        def get_account(self):
            return SimpleNamespace(status="ACTIVE")

        def submit_order(self, order_data):
            order = SimpleNamespace(
                id="broker-order-1",
                client_order_id=order_data.client_order_id,
                status="accepted",
                submitted_at=datetime.now(UTC),
                filled_qty="0",
                qty=str(order_data.qty),
            )
            self.orders[order.id] = order
            return order

        def get_order_by_id(self, order_id):
            return self.orders[str(order_id)]

        def get_order_by_client_order_id(self, order_id):
            return next(
                order
                for order in self.orders.values()
                if order.client_order_id == order_id
            )

        def cancel_order_by_id(self, order_id):
            self.cancelled.append(order_id)
            self.orders[order_id].status = "canceled"

    monkeypatch.setattr(alpaca_paper_broker, "TradingClient", FakeClient)
    broker = alpaca_paper_broker.AlpacaPaperBroker(
        api_key="test-key",
        secret_key="test-secret",
    )

    assert broker.mode == "paper"
    assert FakeClient.instances[-1].paper is True

    order = Order(
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=1.0,
        price=100.0,
    )
    submitted = broker.submit(order)

    assert submitted["success"] is True
    assert submitted["order_id"] == "broker-order-1"
    assert submitted["client_order_id"] == order.id

    fake_order = FakeClient.instances[-1].orders["broker-order-1"]
    fake_order.filled_qty = "0.4"
    status = broker.get_order_status(submitted["order_id"])
    assert status["state"] == "accepted"
    assert status["filled_quantity"] == pytest.approx(0.4)
    assert status["remaining_quantity"] == pytest.approx(0.6)

    cancelled = broker.cancel_order(submitted["order_id"])
    assert cancelled["success"] is True
    assert FakeClient.instances[-1].cancelled == ["broker-order-1"]

    reconciliation = broker.reconcile_order(submitted["order_id"])
    assert reconciliation["reconciled"] is True
    assert reconciliation["state"] == OrderStatus.CANCELLED.value


def test_alpaca_paper_broker_rejects_missing_credentials_without_client(monkeypatch):
    from ultron.financial.trading.alpaca_paper_broker import AlpacaPaperBroker

    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ALPACA_API_KEY is required"):
        AlpacaPaperBroker()


def test_alpaca_paper_broker_rejects_blocked_paper_account(monkeypatch):
    from ultron.financial.trading import alpaca_paper_broker

    class BlockedClient:
        def __init__(self, api_key, secret_key, paper):
            assert paper is True

        def get_account(self):
            return SimpleNamespace(status="ACTIVE", trading_blocked=True)

    monkeypatch.setattr(alpaca_paper_broker, "TradingClient", BlockedClient)

    with pytest.raises(RuntimeError, match="trading is blocked"):
        alpaca_paper_broker.AlpacaPaperBroker(
            api_key="test-key",
            secret_key="test-secret",
        )


def test_paper_broker_tracks_order_identity_and_final_status():
    broker = PaperBroker(initial_capital=10_000.0, commission_rate=0.0)
    order = Order(symbol="AAPL", side=OrderSide.BUY, quantity=10.0, price=100.0)

    result = broker.submit(order)

    assert result["success"] is True
    assert result["order_id"] == order.id
    assert broker.get_order_status(order.id)["state"] == OrderStatus.FILLED.value
    assert broker.get_order_status(order.id)["filled_quantity"] == 10.0

    assert result["broker_status"]["state"] == OrderStatus.FILLED.value
    assert "order_id" not in result["broker_status"]
    json.dumps(result)

    reconciliation = broker.reconcile_order(order.id)
    assert reconciliation["order_id"] == order.id
    assert reconciliation["fills"][0]["order_id"] == order.id
    assert reconciliation["fills"][0]["fill_id"] == order.id
    json.dumps(reconciliation)


def test_paper_broker_supports_cancellation_and_partial_fill_reconciliation():
    broker = PaperBroker(initial_capital=10_000.0, commission_rate=0.0)
    order = Order(symbol="AAPL", side=OrderSide.BUY, quantity=10.0, price=100.0)
    record = BrokerOrderRecord(
        order_id=order.id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        remaining_quantity=order.quantity,
        filled_quantity=0.0,
        status=OrderStatus.SUBMITTED.value,
        price=order.price,
    )
    broker._orders[order.id] = record

    cancel_result = broker.cancel_order(order.id)

    assert cancel_result["success"] is True
    assert cancel_result["state"] == OrderStatus.CANCELLED.value

    order_status = broker.get_order_status(order.id)
    assert order_status["state"] == OrderStatus.CANCELLED.value
    assert order_status["cancelled"] is True

    reconcile_result = broker.reconcile_order(order.id)
    assert reconcile_result["state"] == OrderStatus.CANCELLED.value
    assert reconcile_result["reconciled"] is True


def test_instrument_precision_rules_cover_equity_crypto_and_forex():
    equity = Instrument(
        symbol="AAPL",
        market=MarketType.STOCK,
        base_currency="USD",
        quote_currency="USD",
        min_quantity=1.0,
        quantity_step=1.0,
        tick_size=0.01,
        contract_size=1.0,
        min_notional=1.0,
        asset_class="equity",
        trading_status="active",
    )
    crypto = Instrument(
        symbol="BTC/USD",
        market=MarketType.CRYPTO,
        base_currency="BTC",
        quote_currency="USD",
        min_quantity=0.001,
        quantity_step=0.001,
        tick_size=0.01,
        contract_size=1.0,
        min_notional=10.0,
        asset_class="crypto",
        trading_status="active",
    )
    forex = Instrument(
        symbol="EUR/USD",
        market=MarketType.FOREX,
        base_currency="EUR",
        quote_currency="USD",
        min_quantity=1000.0,
        quantity_step=1000.0,
        tick_size=0.0001,
        contract_size=100000.0,
        min_notional=10.0,
        asset_class="forex",
        trading_status="active",
    )

    assert equity.validate_quantity(2.0) == 2.0
    assert crypto.validate_quantity(0.001) == 0.001
    assert forex.validate_quantity(2000.0) == 2000.0

    assert equity.eligible_for_order(5.0, 25.0) is True
    assert crypto.eligible_for_order(0.0005, 5.0) is False
    assert forex.eligible_for_order(500.0, 5.0) is False
