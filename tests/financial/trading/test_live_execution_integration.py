from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
import sqlite3

import pytest

from ultron.financial.trading.coinbase_advanced import (
    CoinbaseRequestTimeout,
    CoinbaseSpotBroker,
)
from ultron.financial.trading.controller import FinancialTradingController
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.models import Order, OrderSide
from ultron.financial.trading.reliability.live_execution_journal import (
    LiveExecutionJournal,
)
from ultron.financial.trading.operations.trading_control import TradingControl


class CoinbaseExecutionFixture:
    def __init__(
        self,
        *,
        timeout_after_acceptance: bool = False,
        partial_fill: bool = False,
        external_balance_change_after_submit: bool = False,
        active_order: bool = False,
    ) -> None:
        self.timeout_after_acceptance = timeout_after_acceptance
        self.partial_fill = partial_fill
        self.external_balance_change_after_submit = (
            external_balance_change_after_submit
        )
        self.active_order = active_order
        self.submit_calls = 0
        self.accounts = {
            "INR": {"available": Decimal("50000"), "hold": Decimal("0")},
            "BTC": {"available": Decimal("0"), "hold": Decimal("0")},
        }
        self.orders: dict[str, dict[str, str]] = {}
        self.fills: dict[str, list[dict[str, str]]] = {}

    def get_key_permissions(self):
        return {"can_view": True, "can_trade": True}

    def get_transaction_summary(self):
        return {
            "fee_tier": {"taker_fee_rate": "0.001"},
            "goods_and_services_tax": {"rate": "0.18", "type": "EXCLUSIVE"},
        }

    def get_accounts(self):
        return [
            {
                "uuid": f"fixture-{currency.lower()}-account",
                "currency": currency,
                "active": True,
                "ready": True,
                "platform": "ACCOUNT_PLATFORM_CONSUMER",
                "available_balance": {"value": str(balance["available"])},
                "hold": {"value": str(balance["hold"])},
            }
            for currency, balance in self.accounts.items()
        ]

    def get_product(self, product_id: str):
        return {
            "product_id": product_id,
            "product_type": "SPOT",
            "is_disabled": False,
            "trading_disabled": False,
            "view_only": False,
            "cancel_only": False,
            "post_only": False,
            "base_increment": "0.00000001",
            "quote_increment": "1",
            "base_min_size": "0.00000001",
            "base_max_size": "1000",
            "quote_min_size": "1",
            "quote_max_size": "100000000000",
        }

    def get_product_book(self, product_id: str):
        return {
            "pricebook": {
                "product_id": product_id,
                "time": datetime.now(UTC).isoformat(),
                "bids": [{"price": "5000000", "size": "1"}],
                "asks": [{"price": "5000000", "size": "1"}],
            }
        }

    def create_limit_ioc_order(self, **payload: str):
        self.submit_calls += 1
        order_id = f"exchange-order-{self.submit_calls}"
        requested = Decimal(payload["base_size"])
        filled = (
            Decimal(0)
            if self.active_order
            else requested / 2
            if self.partial_fill
            else requested
        )
        price = Decimal(payload["limit_price"])
        fee = Decimal("0.5") if filled else Decimal(0)
        terminal_status = (
            "OPEN"
            if self.active_order
            else "CANCELLED"
            if self.partial_fill
            else "FILLED"
        )
        self.orders[order_id] = {
            "order_id": order_id,
            "client_order_id": payload["client_order_id"],
            "product_id": payload["product_id"],
            "side": payload["side"],
            "status": terminal_status,
            "base_size": str(requested),
            "filled_size": str(filled),
            "average_filled_price": str(price),
            "total_fees": str(fee),
        }
        self.fills[order_id] = [
            {
                "entry_id": f"fill-{order_id}",
                "order_id": order_id,
                "product_id": payload["product_id"],
                "side": payload["side"],
                "size": str(filled),
                "price": str(price),
                "commission": str(fee),
                "commission_asset": "INR",
                "trade_time": datetime.now(UTC).isoformat(),
            }
        ] if filled else []
        self.accounts["BTC"]["available"] += filled
        self.accounts["INR"]["available"] -= filled * price + fee
        if self.external_balance_change_after_submit and filled:
            self.accounts["INR"]["available"] -= Decimal("1")
        if self.timeout_after_acceptance:
            raise CoinbaseRequestTimeout("simulated transport timeout")
        return {
            "success": True,
            "success_response": {
                "order_id": order_id,
                "status": terminal_status,
            },
        }

    def get_order(self, order_id: str):
        return {"order": self.orders[order_id]}

    def get_order_fills(self, order_id: str):
        return self.fills[order_id]

    def cancel_order(self, order_id: str):
        self.orders[order_id]["status"] = "CANCELLED"
        return {
            "results": [{"order_id": order_id, "success": True}],
        }

    def get_orders(self, *, product_id: str, start_date: datetime, end_date: datetime):
        del start_date, end_date
        return [
            order
            for order in self.orders.values()
            if order["product_id"] == product_id
        ]


def make_analysis(intent_id: str = "integration-intent"):
    return {
        "symbol": "BTC-INR",
        "logical_intent_id": intent_id,
        "decision": {"decision": "consider", "confidence": 0.9},
        "risk": {"risk": {"risk_level": "low"}},
        "strategies": ["trend_following"],
    }


def make_controller(
    tmp_path,
    client,
    *,
    authorized: bool = True,
    trading_control: TradingControl | None = None,
):
    broker = CoinbaseSpotBroker(
        client=client,
        quote_currency="INR",
        account_region="IN",
        eligibility_account_id="fixture-inr-account",
        region_eligibility_confirmed=True,
        live_trading_authorized=authorized,
        regional_eligibility_verifier=lambda region, product: {
            "verified": True,
            "region": region,
            "product_id": product,
            "account_id": "fixture-inr-account",
            "source": "test-authoritative-evidence",
            "evidence_id": f"{region}:{product}",
        },
    )
    journal = LiveExecutionJournal(tmp_path / "coinbase-live.sqlite3")
    controller = FinancialTradingController(
        trading_engine=PaperTradingEngine(broker, max_position_percent=25.0),
        live_execution_journal=journal,
        trading_control=trading_control,
    )
    return controller, broker, journal


def execute(controller, intent_id: str = "integration-intent"):
    return controller.execute(
        analysis=make_analysis(intent_id),
        price=5_000_000,
        price_timestamp=datetime.now(UTC),
    )


def test_live_execution_is_durable_sized_reconciled_and_idempotent(tmp_path):
    client = CoinbaseExecutionFixture()
    controller, broker, journal = make_controller(tmp_path, client)

    result = execute(controller)

    assert result["success"] is True
    assert result["order_reconciliation"]["state"] == "filled"
    assert result["balance_reconciliation"]["valid"] is True
    assert client.submit_calls == 1
    assert result["execution"]["order"]["fees"] == 0.5
    assert result["execution"]["order"]["fills"][0]["fee_currency"] == "INR"
    assert journal.has_unresolved_orders() is False
    record = journal.get_by_key("intent:integration-intent")
    assert record is not None
    assert record["state"] == "FILLED"
    assert record["exchange_order_id"] == "exchange-order-1"

    duplicate = execute(controller)
    assert duplicate["status"] == "duplicate"
    assert client.submit_calls == 1

    submitted = client.orders["exchange-order-1"]
    requested_notional = Decimal(submitted["base_size"]) * Decimal(
        submitted["average_filled_price"]
    )
    assert requested_notional <= Decimal("12500")
    assert broker.get_account_snapshot({})["balances"]["INR"]["hold"] == 0


def test_partial_fill_is_reconciled_with_authoritative_fee_and_balances(tmp_path):
    client = CoinbaseExecutionFixture(partial_fill=True)
    controller, _, journal = make_controller(tmp_path, client)

    result = execute(controller)

    assert result["success"] is True
    assert result["order_reconciliation"]["state"] == "cancelled"
    assert result["order_reconciliation"]["filled_quantity"] > 0
    assert result["order_reconciliation"]["remaining_quantity"] > 0
    assert result["balance_reconciliation"]["valid"] is True
    assert journal.get_by_key("intent:integration-intent")["state"] == "CANCELLED"


def test_cancel_request_is_reconciled_and_durably_finalized(tmp_path):
    client = CoinbaseExecutionFixture(active_order=True)
    controller, _, journal = make_controller(tmp_path, client)

    initial = execute(controller)

    assert initial["success"] is False
    assert initial["status"] == "unknown"
    assert journal.has_unresolved_orders() is True

    cancelled = controller.cancel_order("exchange-order-1")

    assert cancelled["success"] is True
    assert cancelled["reconciliation"]["state"] == "cancelled"
    assert journal.get_by_key("intent:integration-intent")["state"] == "CANCELLED"
    assert journal.has_unresolved_orders() is False


def test_timeout_after_exchange_acceptance_recovers_without_resubmission(tmp_path):
    client = CoinbaseExecutionFixture(timeout_after_acceptance=True)
    controller, _, journal = make_controller(tmp_path, client)
    initial = execute(controller)

    assert initial["success"] is False
    assert initial["status"] == "unknown"
    assert journal.has_unresolved_orders() is True
    assert client.submit_calls == 1

    recovered_controller, _, recovered_journal = make_controller(tmp_path, client)

    assert recovered_journal.has_unresolved_orders() is False
    assert recovered_journal.get_by_key("intent:integration-intent")["state"] == "FILLED"
    assert execute(recovered_controller)["status"] == "duplicate"
    assert client.submit_calls == 1


def test_restart_recovers_terminal_order_waiting_for_balance_commit(tmp_path):
    client = CoinbaseExecutionFixture()
    _, broker, journal = make_controller(tmp_path, client)
    client_order_id = "durable-crash-order-id"
    assert journal.claim(
        "intent:crash-window",
        client_order_id,
        created_at=datetime.now(UTC),
    )
    order = Order(
        symbol="BTC-INR",
        side=OrderSide.BUY,
        quantity=0.001,
        price=5_000_000,
        id=client_order_id,
    )
    journal.prepare_order(order)
    accepted = broker.submit(order)
    authoritative = broker.reconcile_order(accepted["order_id"])
    authoritative["client_order_id"] = client_order_id
    journal.record_reconciliation(authoritative)
    assert journal.get_by_key("intent:crash-window")["state"] == "BALANCE_PENDING"

    recovered_controller, _, recovered_journal = make_controller(tmp_path, client)

    assert recovered_journal.get_by_key("intent:crash-window")["state"] == "FILLED"
    assert recovered_journal.has_unresolved_orders() is False
    assert client.submit_calls == 1
    assert execute(recovered_controller, "crash-window")["status"] == "duplicate"
    assert client.submit_calls == 1


def test_development_kill_switch_blocks_submission(tmp_path):
    client = CoinbaseExecutionFixture()
    controller, _, journal = make_controller(tmp_path, client, authorized=False)

    result = execute(controller)

    assert result["success"] is False
    assert result["stage"] == "live_authorization"
    assert client.submit_calls == 0
    assert journal.rows_needing_recovery() == []


def test_live_price_freshness_and_manual_pause_block_orders(tmp_path):
    stale_client = CoinbaseExecutionFixture()
    stale_controller, _, _ = make_controller(tmp_path / "stale", stale_client)
    stale = stale_controller.execute(
        analysis=make_analysis("stale-intent"),
        price=5_000_000,
        price_timestamp=datetime.fromtimestamp(0, UTC),
    )
    assert stale["stage"] == "price_guard"
    assert stale_client.submit_calls == 0

    paused_client = CoinbaseExecutionFixture()
    control = TradingControl()
    control.pause("Operator pause for integration test.")
    paused_controller, _, _ = make_controller(
        tmp_path / "paused",
        paused_client,
        trading_control=control,
    )
    paused = execute(paused_controller, "paused-intent")
    assert paused["stage"] == "trading_control"
    assert paused_client.submit_calls == 0


def test_external_balance_change_fails_closed_before_order_submission(tmp_path):
    client = CoinbaseExecutionFixture()
    controller, _, _ = make_controller(tmp_path, client)
    client.accounts["INR"]["available"] -= Decimal("1")

    result = execute(controller)

    assert result["success"] is False
    assert result["status"] == "reconciliation_failed"
    assert client.submit_calls == 0


def test_journal_checks_wal_full_sync_integrity_and_duplicate_claims(tmp_path):
    journal = LiveExecutionJournal(tmp_path / "persistent.sqlite3")
    storage = journal.validate_storage()
    assert storage["valid"] is True
    assert storage["journal_mode"] == "wal"
    assert storage["synchronous"] == 2
    assert storage["integrity_check"] == "ok"

    created_at = datetime.now(UTC)
    assert journal.claim(
        "intent:one",
        "client-order-one",
        created_at=created_at,
    )
    assert not journal.claim(
        "intent:one",
        "client-order-one-duplicate",
        created_at=created_at,
    )
    assert not journal.claim(
        "intent:two",
        "client-order-two",
        created_at=created_at,
    )


def test_journal_claim_is_atomic_across_independent_connections(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    journals = [LiveExecutionJournal(path) for _ in range(8)]
    created_at = datetime.now(UTC)

    def claim(index: int) -> bool:
        return journals[index].claim(
            "intent:concurrent",
            f"client-order-{index}",
            created_at=created_at,
        )

    with ThreadPoolExecutor(max_workers=len(journals)) as executor:
        results = list(executor.map(claim, range(len(journals))))

    assert sum(results) == 1
    recovered = LiveExecutionJournal(path)
    rows = recovered.rows_needing_recovery()
    assert len(rows) == 1
    assert rows[0]["state"] == "CLAIMED"
    assert recovered.has_unresolved_orders() is True


def test_unavailable_execution_journal_blocks_submission(tmp_path):
    client = CoinbaseExecutionFixture()
    controller, _, journal = make_controller(tmp_path, client)
    database_path = journal.db_path
    database_path.unlink()
    database_path.mkdir()

    with pytest.raises(sqlite3.OperationalError):
        execute(controller, "database-unavailable")

    assert client.submit_calls == 0


def test_balance_reconciliation_detects_available_to_hold_mismatch(tmp_path):
    journal = LiveExecutionJournal(tmp_path / "balances.sqlite3")
    journal.record_settled_account(
        {
            "balances": {
                "INR": {"available": "90", "hold": "10"},
            }
        }
    )

    result = journal.reconcile_execution_balances(
        {
            "balances": {
                "INR": {"available": "100", "hold": "0"},
            }
        },
        [],
    )

    assert result["valid"] is False
    assert result["differences"] == ["INR"]


def test_fill_balance_mismatch_stays_unresolved_and_blocks_retry(tmp_path):
    client = CoinbaseExecutionFixture(
        external_balance_change_after_submit=True,
    )
    controller, _, journal = make_controller(tmp_path, client)

    result = execute(controller)

    assert result["success"] is False
    assert result["stage"] == "balance_reconciliation"
    assert journal.has_unresolved_orders() is True
    assert client.submit_calls == 1

    retry = execute(controller)
    assert retry["status"] == "reconciliation_required"
    assert client.submit_calls == 1


def test_daily_loss_limit_blocks_live_submission(tmp_path):
    client = CoinbaseExecutionFixture()
    controller, _, journal = make_controller(tmp_path, client)
    client.accounts["INR"]["available"] = Decimal("48000")
    journal.record_settled_account(
        {
            "balances": {
                "INR": {"available": 48000.0, "hold": 0.0},
                "BTC": {"available": 0.0, "hold": 0.0},
            }
        }
    )

    result = execute(controller)

    assert result["success"] is False
    assert result["stage"] == "daily_loss_guard", result
    assert client.submit_calls == 0


def test_recovery_keeps_unaccepted_timeout_unresolved_and_never_resubmits(tmp_path):
    client = CoinbaseExecutionFixture()
    controller, _, journal = make_controller(tmp_path, client)
    claimed = journal.claim(
        "intent:unknown",
        "stable-client-order-id",
        created_at=datetime.now(UTC),
    )
    assert claimed is True
    journal.prepare_order(
        Order(
            symbol="BTC-INR",
            side=OrderSide.BUY,
            quantity=0.001,
            price=5_000_000,
            id="stable-client-order-id",
        )
    )
    journal.mark_unresolved("stable-client-order-id", "submission timed out")

    with pytest.raises(RuntimeError, match="unresolved live orders"):
        make_controller(tmp_path, client)

    assert client.submit_calls == 0
    assert journal.has_unresolved_orders() is True
    assert controller.live_execution_journal is journal
