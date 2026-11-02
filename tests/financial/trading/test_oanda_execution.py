from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from ultron.financial.trading.market_data.oanda_adapter import (
    OandaAPIError,
    OandaExecutionClient,
    OandaMarketAdapter,
)
from ultron.financial.trading.models import Order, OrderSide, OrderStatus
from ultron.financial.trading.oanda_execution import (
    OandaExecutionJournal,
    OandaForexBroker,
)


def _account(
    *,
    transaction_id: str = "42",
    long_units: str = "1000",
    balance: str = "10000",
    pending: int = 0,
):
    return {
        "account": {
            "id": "account-fixture",
            "currency": "USD",
            "balance": balance,
            "NAV": balance,
            "marginAvailable": "9500",
            "marginUsed": "500",
            "unrealizedPL": "0",
            "lastTransactionID": transaction_id,
            "pendingOrderCount": pending,
            "openTradeCount": 1 if long_units != "0" else 0,
            "status": "OPEN",
            "positions": [
                {
                    "instrument": "EUR_USD",
                    "long": {
                        "units": long_units,
                        "averagePrice": "1.08" if long_units != "0" else "0",
                    },
                    "short": {"units": "0", "averagePrice": "0"},
                }
            ],
        }
    }


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def close(self):
        pass


class FixtureExecutionClient(OandaExecutionClient):
    def __init__(self, *, order_response=None, fail_submission=None):
        super().__init__(
            token="fixture-token",
            account_id="account-fixture",
            opener=lambda request, **kwargs: Response({}),
        )
        self.order_response = order_response
        self.fail_submission = fail_submission
        self.order_requests = []
        self.account = _account()
        self.transactions: list[dict] = []

    def get_account(self):
        return self.account

    def get_instruments(self, instrument=None):
        return {
            "instruments": [
                {
                    "name": instrument or "EUR_USD",
                    "type": "CURRENCY",
                    "displayPrecision": 5,
                    "tradeUnitsPrecision": 0,
                    "minimumTradeSize": "1",
                    "maximumOrderUnits": "1000000",
                    "marginRate": "0.05",
                }
            ]
        }

    def get_price(self, instrument):
        return {
            "prices": [
                {
                    "instrument": instrument,
                    "status": "tradeable",
                    "time": datetime.now(UTC).isoformat(),
                    "bids": [{"price": "1.08210", "liquidity": "1000000"}],
                    "asks": [{"price": "1.08230", "liquidity": "1000000"}],
                }
            ]
        }

    def create_order(self, order):
        self.order_requests.append(order)
        if self.fail_submission is not None:
            raise self.fail_submission
        return self.order_response or {}

    def get_transactions_since(self, transaction_id):
        return {"transactions": self.transactions}

    def get_transaction(self, transaction_id):
        match = next(
            (item for item in self.transactions if item["id"] == transaction_id),
            None,
        )
        return {"transaction": match} if match else {}

    def cancel_order(self, order_id):
        return {
            "orderCancelTransaction": {
                "id": "43",
                "orderID": order_id,
                "type": "ORDER_CANCEL",
            }
        }


def _eligibility(account_id, symbol):
    return {
        "eligible": True,
        "account_id": account_id,
        "instruments": [symbol],
        "evidence_reference": "fixture-only-not-external-evidence",
    }


def _broker(
    client,
    *,
    enabled=True,
    authorized=True,
    verifier=_eligibility,
):
    return OandaForexBroker(
        client,
        OandaMarketAdapter(client),
        eligibility_verifier=verifier,
        live_submission_enabled=enabled,
        live_trading_authorized=authorized,
    )


def test_oanda_execution_client_orders_are_separate_from_readonly_client():
    seen = []

    def opener(request, *, timeout):
        seen.append(request)
        return Response({"orderCreateTransaction": {"id": "43"}})

    client = OandaExecutionClient(
        token="fixture-secret",
        account_id="account-fixture",
        opener=opener,
    )
    client.create_order({"type": "MARKET", "units": "10"})

    request = seen[0]
    assert request.method == "POST"
    assert urlsplit(request.full_url).path.endswith("/orders")
    assert request.get_header("Authorization") == "Bearer fixture-secret"
    assert json.loads(request.data) == {
        "order": {"type": "MARKET", "units": "10"}
    }
    assert "fixture-secret" not in request.full_url


def test_oanda_execution_client_classifies_rate_limit_without_exposing_body():
    from urllib.error import HTTPError

    def opener(request, *, timeout):
        raise HTTPError(
            request.full_url,
            429,
            "private error",
            hdrs=None,
            fp=None,
        )

    client = OandaExecutionClient(
        token="fixture-secret",
        account_id="account-fixture",
        opener=opener,
    )
    with pytest.raises(OandaAPIError) as error:
        client.create_order({"type": "MARKET", "units": "10"})
    assert error.value.failure_type == "rate_limit"
    assert "private error" not in str(error.value)
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("enabled", "authorized"),
    [(False, True), (True, False)],
)
def test_oanda_submission_requires_both_live_gates(enabled, authorized):
    client = FixtureExecutionClient()
    result = _broker(client, enabled=enabled, authorized=authorized).submit(
        Order("EUR_USD", OrderSide.BUY, 100, 1.0822, id="stable-client-id")
    )
    assert result["status"] == OrderStatus.REJECTED.value
    assert client.order_requests == []


def test_oanda_submission_requires_eligibility_evidence():
    client = FixtureExecutionClient()
    broker = _broker(client, verifier=None)
    with pytest.raises(RuntimeError, match="eligibility evidence"):
        broker.submit(
            Order("EUR_USD", OrderSide.BUY, 100, 1.0822, id="stable-client-id")
        )
    assert client.order_requests == []


def test_oanda_submits_bounded_fok_and_maps_authoritative_fill():
    client = FixtureExecutionClient(
        order_response={
            "orderCreateTransaction": {
                "id": "43",
                "batchID": "43",
                "type": "MARKET_ORDER",
            },
            "orderFillTransaction": {
                "id": "44",
                "batchID": "43",
                "orderID": "43",
                "type": "ORDER_FILL",
                "units": "100",
                "price": "1.0824",
                "commission": "0",
                "guaranteedExecutionFee": "0",
                "financing": "0",
                "accountBalance": "10000",
                "time": datetime.now(UTC).isoformat(),
            },
        }
    )
    result = _broker(client).submit(
        Order("EUR_USD", OrderSide.BUY, 100, 1.0822, id="stable-client-id")
    )

    request = client.order_requests[0]
    assert request["instrument"] == "EUR_USD"
    assert request["units"] == "100"
    assert request["type"] == "MARKET"
    assert request["timeInForce"] == "FOK"
    assert request["clientExtensions"]["id"] == "stable-client-id"
    assert Decimal(request["priceBound"]) >= Decimal("1.08230")
    assert result["state"] == OrderStatus.FILLED.value
    assert result["order_id"] == "43"
    assert result["fills"][0]["quantity"] == 100
    assert result["fills"][0]["price"] == pytest.approx(1.0824)


def test_oanda_timeout_is_uncertain_and_never_resubmitted():
    client = FixtureExecutionClient(
        fail_submission=OandaAPIError(
            "timeout",
            failure_type="timeout",
        )
    )
    result = _broker(client).submit(
        Order("EUR_USD", OrderSide.BUY, 100, 1.0822, id="stable-client-id")
    )
    assert result["status"] == OrderStatus.UNKNOWN.value
    assert result["accepted"] is False
    assert len(client.order_requests) == 1


def test_oanda_rejects_short_opening_and_stale_account_state():
    client = FixtureExecutionClient()
    broker = _broker(client)
    result = broker.submit(
        Order("EUR_USD", OrderSide.SELL, 1001, 1.0822, id="stable-client-id")
    )
    assert result["status"] == OrderStatus.REJECTED.value
    assert client.order_requests == []

    client.account = _account(pending=1)
    with pytest.raises(RuntimeError, match="pending orders"):
        broker.snapshot({"EUR_USD": 1.08})
    assert client.order_requests == []


def _snapshot(
    *,
    transaction_id="42",
    balance="10000",
    quantity="100",
):
    return {
        "account_id": "account-fixture",
        "currency": "USD",
        "last_transaction_id": transaction_id,
        "cash": float(balance),
        "equity": float(balance),
        "buying_power": 9500.0,
        "margin_used": 500.0,
        "unrealized_pl": 0.0,
        "positions": {
            "EUR_USD": {
                "quantity": float(quantity),
            }
        } if Decimal(quantity) else {},
        "pending_order_count": 0,
        "open_trade_count": 1 if Decimal(quantity) else 0,
    }


def _filled_result():
    return {
        "client_order_id": "stable-client-id",
        "order_id": "43",
        "reconciled": True,
        "state": "filled",
        "requested_quantity": 100.0,
        "transactions": [
            {"id": "43", "type": "MARKET_ORDER", "batchID": "43"},
            {
                "id": "44",
                "type": "ORDER_FILL",
                "batchID": "43",
                "units": "100",
                "accountBalance": "10000",
            },
        ],
        "fills": [
            {
                "fill_id": "44",
                "symbol": "EUR_USD",
                "side": "buy",
                "quantity": 100,
                "price": 1.0824,
                "fee": 0,
            }
        ],
    }


def _prepare_journal(journal):
    assert journal.compare_and_record_account(_snapshot())["valid"]
    assert journal.claim(
        "intent-1",
        "stable-client-id",
        created_at=datetime.now(UTC),
    )
    journal.prepare_order(
        Order(
            "EUR_USD",
            OrderSide.BUY,
            100,
            1.0822,
            id="stable-client-id",
        )
    )


def test_oanda_journal_persists_checkpoint_and_reconciles_after_restart(tmp_path):
    path = tmp_path / "oanda.sqlite3"
    journal = OandaExecutionJournal(path)
    _prepare_journal(journal)
    assert journal.baseline_transaction_id("stable-client-id") == "42"
    journal.record_reconciliation(_filled_result())

    restarted = OandaExecutionJournal(path)
    assert restarted.rows_needing_recovery()[0]["baseline_transaction_id"] == "42"
    check = restarted.reconcile_execution_account(
        _snapshot(transaction_id="44", quantity="200"),
        [_filled_result()],
    )

    assert check == {"valid": True, "differences": []}
    assert restarted.has_unresolved_orders() is False
    assert restarted.get_by_key("intent-1")["state"] == "FILLED"


def test_oanda_journal_fails_closed_on_reconciliation_mismatches(tmp_path):
    journal = OandaExecutionJournal(tmp_path / "oanda.sqlite3")
    _prepare_journal(journal)
    journal.record_reconciliation(_filled_result())

    result = journal.reconcile_execution_account(
        _snapshot(transaction_id="44", quantity="199"),
        [_filled_result()],
    )

    assert result["valid"] is False
    assert result["differences"] == ["EUR_USD"]
    assert journal.has_unresolved_orders() is True


def test_oanda_journal_claim_is_concurrently_idempotent(tmp_path):
    journal = OandaExecutionJournal(tmp_path / "oanda.sqlite3")

    def claim(_):
        return journal.claim(
            "same-intent",
            "same-client-id",
            created_at=datetime.now(UTC),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, range(8)))

    assert sum(results) == 1


def test_oanda_recovery_searches_transaction_history_by_stable_client_id():
    client = FixtureExecutionClient()
    client.transactions = [
        {
            "id": "43",
            "batchID": "43",
            "type": "MARKET_ORDER",
            "reason": "CLIENT_ORDER",
            "units": "100",
            "instrument": "EUR_USD",
            "clientExtensions": {"id": "stable-client-id"},
        },
        {
            "id": "44",
            "batchID": "43",
            "type": "ORDER_FILL",
            "orderID": "43",
            "units": "100",
            "price": "1.0824",
            "commission": "0",
            "accountBalance": "10000",
            "time": datetime.now(UTC).isoformat(),
        },
    ]
    broker = _broker(client)
    result = broker.reconcile_submission(
        "stable-client-id",
        "EUR_USD",
        created_at=datetime.now(UTC),
        since_transaction_id="42",
    )

    assert result["reconciled"] is True
    assert result["state"] == OrderStatus.FILLED.value
    assert result["order_id"] == "43"
    assert result["fills"][0]["fill_id"] == "44"

