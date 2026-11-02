from __future__ import annotations

import json
import math
import threading
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any, Callable

from ultron.financial.trading.broker import BrokerAdapter
from ultron.financial.trading.market_data.oanda_adapter import (
    OandaAPIError,
    OandaExecutionClient,
    OandaMarketAdapter,
)
from ultron.financial.trading.models import Order, OrderSide, OrderStatus
from ultron.financial.trading.reliability.live_execution_journal import (
    LiveExecutionJournal,
)
from ultron.security.permission.rules import require_simulation_broker


class OandaExecutionJournal(LiveExecutionJournal):
    """Durable OANDA journal with account-currency and position semantics."""

    _ACCOUNT_KEY = "oanda_v20_account"

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS oanda_order_context (
                    client_order_id TEXT PRIMARY KEY,
                    baseline_transaction_id TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS oanda_execution_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL,
                    transaction_json TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _canonical_account(snapshot: dict[str, Any]) -> dict[str, Any]:
        try:
            account_id = str(snapshot["account_id"])
            currency = str(snapshot["currency"]).upper()
            last_transaction_id = str(snapshot["last_transaction_id"])
            amounts = {
                name: Decimal(str(snapshot[source]))
                for name, source in (
                    ("balance", "cash"),
                    ("equity", "equity"),
                    ("margin_available", "buying_power"),
                    ("margin_used", "margin_used"),
                    ("unrealized_pl", "unrealized_pl"),
                )
            }
            positions = snapshot["positions"]
            pending_count = snapshot["pending_order_count"]
            open_trade_count = snapshot["open_trade_count"]
        except (KeyError, TypeError, ValueError):
            raise ValueError("OANDA authoritative account state is incomplete.") from None
        if (
            not account_id
            or len(currency) != 3
            or not last_transaction_id.isdigit()
            or not isinstance(positions, dict)
            or isinstance(pending_count, bool)
            or not isinstance(pending_count, int)
            or pending_count < 0
            or isinstance(open_trade_count, bool)
            or not isinstance(open_trade_count, int)
            or open_trade_count < 0
            or any(not value.is_finite() for value in amounts.values())
            or amounts["balance"] < 0
            or amounts["equity"] < 0
            or amounts["margin_available"] < 0
            or amounts["margin_used"] < 0
        ):
            raise ValueError("OANDA authoritative account state is invalid.")
        canonical_positions: dict[str, str] = {}
        for symbol, value in positions.items():
            if not isinstance(value, dict) or "quantity" not in value:
                raise ValueError("OANDA account positions are malformed.")
            quantity = Decimal(str(value["quantity"]))
            if not quantity.is_finite():
                raise ValueError("OANDA account positions are non-finite.")
            canonical_positions[str(symbol).upper()] = format(
                quantity.normalize(), "f"
            )
        return {
            "account_id": account_id,
            "currency": currency,
            "last_transaction_id": last_transaction_id,
            "amounts": {
                key: format(value.normalize(), "f")
                for key, value in amounts.items()
            },
            "positions": canonical_positions,
            "pending_order_count": pending_count,
            "open_trade_count": open_trade_count,
        }

    def compare_and_record_account(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        canonical = self._canonical_account(snapshot)
        encoded = self._json(canonical)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value_json FROM live_account_state WHERE state_key=?",
                (self._ACCOUNT_KEY,),
            ).fetchone()
            if row is None:
                db.execute(
                    """
                    INSERT INTO live_account_state(state_key, value_json, updated_at)
                    VALUES(?, ?, ?)
                    """,
                    (self._ACCOUNT_KEY, encoded, datetime.now(UTC).isoformat()),
                )
                db.execute("COMMIT")
                return {"valid": True, "baseline_created": True, "differences": []}
            previous = json.loads(row["value_json"])
            if previous == canonical:
                db.execute("COMMIT")
                return {"valid": True, "baseline_created": False, "differences": []}
            differences = sorted(
                key for key in set(previous) | set(canonical)
                if previous.get(key) != canonical.get(key)
            )
            db.execute("ROLLBACK")
            return {
                "valid": False,
                "baseline_created": False,
                "differences": differences,
                "reason": (
                    "OANDA account state changed outside a reconciled execution."
                ),
            }

    def prepare_order(self, order: Any) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account_row = db.execute(
                "SELECT value_json FROM live_account_state WHERE state_key=?",
                (self._ACCOUNT_KEY,),
            ).fetchone()
            if account_row is None:
                db.execute("ROLLBACK")
                raise RuntimeError("OANDA account baseline is unavailable.")
            baseline_id = json.loads(account_row["value_json"]).get(
                "last_transaction_id"
            )
            cursor = db.execute(
                """
                UPDATE live_order_intents
                SET updated_at=?, symbol=?, side=?, quantity=?,
                    reference_price=?, state='SUBMITTING'
                WHERE client_order_id=? AND state='CLAIMED'
                """,
                (
                    timestamp,
                    str(order.symbol).strip().upper(),
                    str(order.side.value).upper(),
                    str(order.quantity),
                    str(order.price),
                    order.id,
                ),
            )
            if cursor.rowcount != 1 or not isinstance(baseline_id, str):
                db.execute("ROLLBACK")
                raise RuntimeError(
                    "OANDA intent was not uniquely prepared with a transaction checkpoint."
                )
            db.execute(
                """
                INSERT INTO oanda_order_context(client_order_id, baseline_transaction_id)
                VALUES(?, ?)
                """,
                (order.id, baseline_id),
            )
            db.execute("COMMIT")

    def baseline_transaction_id(self, client_order_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT baseline_transaction_id
                FROM oanda_order_context WHERE client_order_id=?
                """,
                (client_order_id,),
            ).fetchone()
        return str(row["baseline_transaction_id"]) if row else None

    def rows_needing_recovery(self) -> list[dict[str, Any]]:
        rows = super().rows_needing_recovery()
        for row in rows:
            row["baseline_transaction_id"] = self.baseline_transaction_id(
                row["client_order_id"]
            )
        return rows

    def reconcile_execution_account(
        self,
        snapshot: dict[str, Any],
        reconciliations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        actual = self._canonical_account(snapshot)
        if actual["pending_order_count"] != 0:
            return {
                "valid": False,
                "differences": ["pending_order_count"],
                "reason": "OANDA still reports pending orders after reconciliation.",
            }
        if not reconciliations:
            return {
                "valid": False,
                "differences": ["order_transactions"],
                "reason": "OANDA transaction evidence is required for settlement.",
            }
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                baseline_row = db.execute(
                    "SELECT value_json FROM live_account_state WHERE state_key=?",
                    (self._ACCOUNT_KEY,),
                ).fetchone()
                if baseline_row is None:
                    raise RuntimeError("OANDA account baseline is unavailable.")
                baseline = json.loads(baseline_row["value_json"])
                expected_positions = {
                    symbol: Decimal(quantity)
                    for symbol, quantity in baseline["positions"].items()
                }
                accepted_transactions: list[tuple[str, str, str]] = []
                max_transaction_id = int(baseline["last_transaction_id"])
                any_fill = False
                expected_balance: Decimal | None = None
                for result in reconciliations:
                    if not result.get("reconciled"):
                        raise ValueError("OANDA order outcome is not authoritative.")
                    state = str(result.get("state", "")).lower()
                    if state not in {"filled", "cancelled", "rejected", "expired"}:
                        raise ValueError("OANDA order is not in a terminal state.")
                    transactions = result.get("transactions")
                    if not isinstance(transactions, list) or not transactions:
                        raise ValueError("OANDA order transaction evidence is missing.")
                    for transaction in transactions:
                        if not isinstance(transaction, dict):
                            raise ValueError("OANDA transaction is malformed.")
                        transaction_id = transaction.get("id")
                        if not isinstance(transaction_id, str) or not transaction_id.isdigit():
                            raise ValueError("OANDA transaction ID is invalid.")
                        max_transaction_id = max(max_transaction_id, int(transaction_id))
                        client_order_id = str(result.get("client_order_id", ""))
                        encoded = self._json(transaction)
                        existing = db.execute(
                            """
                            SELECT transaction_json FROM oanda_execution_transactions
                            WHERE transaction_id=?
                            """,
                            (transaction_id,),
                        ).fetchone()
                        if existing is not None and existing["transaction_json"] != encoded:
                            raise RuntimeError("OANDA transaction evidence changed.")
                        if existing is None:
                            accepted_transactions.append(
                                (transaction_id, client_order_id, encoded)
                            )
                    fills = result.get("fills", [])
                    if not isinstance(fills, list):
                        raise ValueError("OANDA fill evidence is malformed.")
                    requested_quantity = Decimal(str(result.get("requested_quantity", "0")))
                    signed_filled_quantity = Decimal(0)
                    for fill in fills:
                        quantity = Decimal(str(fill["quantity"]))
                        price = Decimal(str(fill["price"]))
                        fee = Decimal(str(fill["fee"]))
                        side = str(fill["side"]).lower()
                        symbol = str(fill["symbol"]).upper()
                        if (
                            not quantity.is_finite()
                            or not price.is_finite()
                            or not fee.is_finite()
                            or quantity <= 0
                            or price <= 0
                            or fee < 0
                            or side not in {"buy", "sell"}
                        ):
                            raise ValueError("OANDA fill values are invalid.")
                        signed_quantity = quantity if side == "buy" else -quantity
                        signed_filled_quantity += signed_quantity
                        expected_positions[symbol] = (
                            expected_positions.get(symbol, Decimal(0))
                            + signed_quantity
                        )
                        transaction = next(
                            (
                                item for item in transactions
                                if str(item.get("id")) == str(fill.get("fill_id"))
                            ),
                            None,
                        )
                        if transaction is None:
                            raise ValueError("OANDA fill is not linked to a transaction.")
                        if "accountBalance" in transaction:
                            expected_balance = Decimal(str(transaction["accountBalance"]))
                        any_fill = True
                    if fills and abs(signed_filled_quantity) != requested_quantity:
                        raise ValueError(
                            "OANDA FOK order reported a partial or mismatched fill."
                        )
                    if state == "filled" and not fills:
                        raise ValueError("OANDA reported a fill without fill transactions.")
                    if state != "filled" and fills:
                        raise ValueError("OANDA reported fills for a non-filled order.")

                if int(actual["last_transaction_id"]) != max_transaction_id:
                    raise ValueError(
                        "OANDA account changed beyond the reconciled transaction range."
                    )
                actual_positions = {
                    symbol: Decimal(quantity)
                    for symbol, quantity in actual["positions"].items()
                }
                all_symbols = set(expected_positions) | set(actual_positions)
                differences = sorted(
                    symbol for symbol in all_symbols
                    if abs(
                        expected_positions.get(symbol, Decimal(0))
                        - actual_positions.get(symbol, Decimal(0))
                    ) > Decimal("0.0000001")
                )
                if differences:
                    db.execute("ROLLBACK")
                    return {
                        "valid": False,
                        "differences": differences,
                        "reason": (
                            "OANDA positions do not reconcile to authoritative fills."
                        ),
                    }
                if any_fill:
                    actual_balance = Decimal(actual["amounts"]["balance"])
                    if expected_balance is None or actual_balance != expected_balance:
                        db.execute("ROLLBACK")
                        return {
                            "valid": False,
                            "differences": ["balance"],
                            "reason": (
                                "OANDA balance does not match the authoritative fill transaction."
                            ),
                        }
                elif actual["amounts"]["balance"] != baseline["amounts"]["balance"]:
                    db.execute("ROLLBACK")
                    return {
                        "valid": False,
                        "differences": ["balance"],
                        "reason": (
                            "OANDA balance changed without a reconciled fill transaction."
                        ),
                    }

                for transaction_id, client_order_id, encoded in accepted_transactions:
                    db.execute(
                        """
                        INSERT INTO oanda_execution_transactions(
                            transaction_id, client_order_id, transaction_json
                        ) VALUES(?, ?, ?)
                        """,
                        (transaction_id, client_order_id, encoded),
                    )
                db.execute(
                    """
                    UPDATE live_account_state SET value_json=?, updated_at=?
                    WHERE state_key=?
                    """,
                    (
                        self._json(actual),
                        datetime.now(UTC).isoformat(),
                        self._ACCOUNT_KEY,
                    ),
                )
                for result in reconciliations:
                    state = str(result["state"]).upper()
                    client_order_id = str(result["client_order_id"])
                    db.execute(
                        """
                        UPDATE live_order_intents SET state=?, updated_at=?
                        WHERE client_order_id=? AND state='BALANCE_PENDING'
                        """,
                        (state, datetime.now(UTC).isoformat(), client_order_id),
                    )
                db.execute("COMMIT")
                return {"valid": True, "differences": []}
            except (InvalidOperation, KeyError, TypeError, ValueError, RuntimeError):
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise


class OandaForexBroker(BrokerAdapter):
    """FOK, account-scoped OANDA execution; disabled unless explicitly gated."""

    provider_key = "oanda_v20"

    def __init__(
        self,
        client: OandaExecutionClient,
        market_adapter: OandaMarketAdapter,
        *,
        eligibility_verifier: Callable[[str, str], dict[str, Any]] | None = None,
        live_submission_enabled: bool = False,
        live_trading_authorized: bool = False,
        max_price_slippage_bps: float = 10.0,
    ) -> None:
        super().__init__(mode="live")
        if not isinstance(client, OandaExecutionClient):
            raise ValueError("OANDA live execution requires OandaExecutionClient.")
        if not math.isfinite(max_price_slippage_bps) or max_price_slippage_bps <= 0:
            raise ValueError("Price slippage bound must be finite and positive.")
        self.client = client
        self.market_adapter = market_adapter
        self.eligibility_verifier = eligibility_verifier
        self.live_submission_enabled = bool(
            live_submission_enabled and live_trading_authorized
        )
        self.max_price_slippage_bps = float(max_price_slippage_bps)
        self._orders: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @property
    def supports_live_orders(self) -> bool:
        return True

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise OandaAPIError(
                f"OANDA returned invalid {field}.",
                failure_type="invalid_response",
            ) from None
        if not result.is_finite():
            raise OandaAPIError(
                f"OANDA returned non-finite {field}.",
                failure_type="invalid_response",
            )
        return result

    def get_account_snapshot(
        self,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.market_adapter.get_account_snapshot(prices)
        positions: dict[str, dict[str, float]] = {}
        for symbol, position in snapshot["positions"].items():
            if position["short_quantity"] < 0:
                raise RuntimeError(
                    "OANDA short positions are not supported by the shared long-only risk engine."
                )
            quantity = float(position["long_quantity"])
            if quantity > 0:
                positions[symbol] = {
                    "quantity": quantity,
                    "available_quantity": quantity,
                    "average_price": float(position["long_average_price"]),
                }
        if snapshot["pending_order_count"] != 0:
            raise RuntimeError(
                "OANDA has pending orders; reconcile them before risk-increasing activity."
            )
        if snapshot["account_status"].upper() != "OPEN":
            raise RuntimeError("OANDA account is not reported as open.")
        return {
            **snapshot,
            "cash": snapshot["buying_power"],
            "positions": positions,
            "provider": self.provider_key,
        }

    def snapshot(self, prices: dict[str, float]) -> dict[str, Any]:
        return self.get_account_snapshot(prices)

    def _verify_eligibility(self, symbol: str) -> None:
        if self.eligibility_verifier is None:
            raise RuntimeError(
                "OANDA account and regional eligibility evidence is required."
            )
        evidence = self.eligibility_verifier(self.client.account_id, symbol)
        if (
            not isinstance(evidence, dict)
            or evidence.get("eligible") is not True
            or evidence.get("account_id") != self.client.account_id
            or symbol not in evidence.get("instruments", [])
            or not isinstance(evidence.get("evidence_reference"), str)
            or not evidence["evidence_reference"].strip()
        ):
            raise RuntimeError(
                "OANDA account or instrument eligibility is not authoritatively verified."
            )

    def submit(self, order: Order) -> dict[str, Any]:
        require_simulation_broker(self, "OANDA order submission")
        if not self.live_submission_enabled:
            return {
                "success": False,
                "accepted": False,
                "status": OrderStatus.REJECTED.value,
                "reason": "OANDA live execution is disabled or not explicitly authorized.",
            }
        symbol = OandaMarketAdapter._instrument_name(order.symbol)
        self._verify_eligibility(symbol)
        instrument = self.market_adapter.get_instrument(symbol)
        constraints = self.client.get_instruments(symbol).get("instruments")
        if not isinstance(constraints, list) or len(constraints) != 1:
            raise RuntimeError("OANDA instrument constraints are unavailable.")
        metadata = constraints[0]
        quantity = self._decimal(order.quantity, "order quantity")
        quantity_step = self._decimal(instrument.quantity_step, "trade unit precision")
        minimum = self._decimal(instrument.min_quantity, "minimum trade size")
        maximum = self._decimal(metadata.get("maximumOrderUnits"), "maximum order units")
        if (
            quantity < minimum
            or quantity > maximum
            or quantity % quantity_step != 0
        ):
            return {
                "success": False,
                "accepted": False,
                "status": OrderStatus.REJECTED.value,
                "reason": "Order quantity violates OANDA instrument constraints.",
            }
        quote = self.market_adapter.get_snapshot(symbol)
        side = order.side.value.lower()
        if side not in {"buy", "sell"}:
            raise ValueError("OANDA order side is unsupported.")
        account = self.get_account_snapshot({symbol: float(quote["price"])})
        if account["currency"] != instrument.quote_currency:
            raise RuntimeError(
                "OANDA margin is not checked for quote/account currency mismatches."
            )
        margin_rate = self._decimal(metadata.get("marginRate"), "margin rate")
        executable_price = self._decimal(
            quote["ask"] if side == "buy" else quote["bid"],
            "executable quote",
        )
        required_margin = quantity * executable_price * margin_rate
        if required_margin > self._decimal(account["buying_power"], "available margin"):
            return {
                "success": False,
                "accepted": False,
                "status": OrderStatus.REJECTED.value,
                "reason": "OANDA available margin is insufficient.",
            }
        position = account["positions"].get(symbol, {})
        if side == "sell" and quantity > Decimal(str(position.get("quantity", 0))):
            return {
                "success": False,
                "accepted": False,
                "status": OrderStatus.REJECTED.value,
                "reason": "OANDA sell orders are restricted to reducing verified long positions.",
            }
        precision = int(metadata["displayPrecision"])
        price_step = Decimal(1).scaleb(-precision)
        slippage = Decimal(str(self.max_price_slippage_bps)) / Decimal(10_000)
        bound = (
            executable_price * (Decimal(1) + slippage)
            if side == "buy"
            else executable_price * (Decimal(1) - slippage)
        )
        rounding = ROUND_UP if side == "buy" else ROUND_DOWN
        bound = bound.quantize(price_step, rounding=rounding)
        signed_units = quantity if side == "buy" else -quantity
        order_request: dict[str, Any] = {
            "type": "MARKET",
            "instrument": symbol,
            "units": format(signed_units, "f"),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT" if side == "buy" else "REDUCE_ONLY",
            "priceBound": format(bound, "f"),
            "clientExtensions": {"id": order.id},
        }
        try:
            response = self.client.create_order(order_request)
        except OandaAPIError as exc:
            if exc.failure_type in {"authentication", "permission", "http", "not_found"}:
                return {
                    "success": False,
                    "accepted": False,
                    "status": OrderStatus.REJECTED.value,
                    "reason": "OANDA rejected the order request.",
                }
            return {
                "success": False,
                "accepted": False,
                "status": OrderStatus.UNKNOWN.value,
                "reason": "OANDA submission outcome is uncertain; recovery is required.",
                "failure_type": exc.failure_type,
            }
        result = self._normalize_response(response, order)
        with self._lock:
            if result.get("order_id"):
                self._orders[result["order_id"]] = result
        return result

    def _normalize_response(
        self,
        response: dict[str, Any],
        order: Order,
        *,
        transactions: list[dict[str, Any]] | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        order_create = response.get("orderCreateTransaction")
        fill = response.get("orderFillTransaction")
        cancel = response.get("orderCancelTransaction")
        reject = response.get("orderRejectTransaction")
        if transactions is None:
            transactions = [
                value for key, value in response.items()
                if key.endswith("Transaction") and isinstance(value, dict)
            ]
        if fill is not None:
            filled_units = abs(self._decimal(fill.get("units"), "filled units"))
            if filled_units != self._decimal(order.quantity, "requested units"):
                state = OrderStatus.PARTIALLY_FILLED.value
            else:
                state = OrderStatus.FILLED.value
            fee_value = sum(
                (
                    abs(self._decimal(fill.get(field, "0"), field))
                    for field in ("commission", "guaranteedExecutionFee")
                ),
                Decimal(0),
            )
            fill_record = {
                "fill_id": str(fill.get("id", "")),
                "symbol": order.symbol.replace("/", "_").upper(),
                "side": order.side.value.lower(),
                "quantity": float(filled_units),
                "price": float(self._decimal(fill.get("price"), "fill price")),
                "fee": float(fee_value),
                "fee_currency": self.market_adapter.get_account_snapshot()[
                    "currency"
                ],
                "financing": float(
                    self._decimal(fill.get("financing", "0"), "financing")
                ),
                "timestamp": fill.get("time"),
            }
            fills = [fill_record]
        else:
            fills = []
            if cancel is not None:
                state = OrderStatus.CANCELLED.value
            elif reject is not None:
                state = OrderStatus.REJECTED.value
            else:
                state = OrderStatus.UNKNOWN.value
        exchange_id = order_id or (
            str(order_create.get("id"))
            if isinstance(order_create, dict) and order_create.get("id") is not None
            else None
        )
        terminal = state in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REJECTED.value,
        }
        return {
            "success": state in {OrderStatus.FILLED.value, OrderStatus.CANCELLED.value},
            "accepted": exchange_id is not None or bool(order_create),
            "status": state,
            "state": state,
            "order_id": exchange_id,
            "client_order_id": order.id,
            "symbol": order.symbol.replace("/", "_").upper(),
            "side": order.side.value.lower(),
            "requested_quantity": float(order.quantity),
            "filled_quantity": sum(fill["quantity"] for fill in fills),
            "fills": fills,
            "transactions": transactions,
            "reconciled": terminal,
        }

    def reconcile_submission(
        self,
        client_order_id: str,
        symbol: str,
        *,
        created_at: datetime,
        since_transaction_id: str | None = None,
    ) -> dict[str, Any]:
        del created_at
        if not since_transaction_id:
            return {"reconciled": False, "state": OrderStatus.UNKNOWN.value}
        response = self.client.get_transactions_since(since_transaction_id)
        transactions = response.get("transactions")
        if not isinstance(transactions, list):
            raise OandaAPIError(
                "OANDA transaction history omitted transactions.",
                failure_type="invalid_response",
            )
        matches = [
            item for item in transactions
            if isinstance(item, dict)
            and isinstance(item.get("clientExtensions"), dict)
            and item["clientExtensions"].get("id") == client_order_id
        ]
        creates = [
            item for item in matches
            if str(item.get("type", "")).endswith(("ORDER", "MARKET_ORDER"))
            or item.get("reason") == "CLIENT_ORDER"
        ]
        if len(creates) != 1:
            return {
                "reconciled": False,
                "state": OrderStatus.UNKNOWN.value,
                "client_order_id": client_order_id,
                "transactions": matches,
            }
        create = creates[0]
        batch_id = create.get("batchID")
        related = [
            item for item in transactions
            if isinstance(item, dict) and item.get("batchID") == batch_id
        ]
        order = Order(
            symbol=symbol,
            side=OrderSide.BUY if Decimal(str(create.get("units", "0"))) > 0 else OrderSide.SELL,
            quantity=abs(float(Decimal(str(create.get("units", "0"))))),
            price=float(Decimal(str(create.get("priceBound", "1")))),
            id=client_order_id,
        )
        result = self._normalize_transactions(
            related,
            order,
            order_id=str(create.get("id")),
        )
        return result

    def _normalize_transactions(
        self,
        transactions: list[dict[str, Any]],
        order: Order,
        *,
        order_id: str,
    ) -> dict[str, Any]:
        create = next(
            (item for item in transactions if item.get("id") == order_id),
            {},
        )
        fill = next(
            (item for item in transactions if item.get("type") == "ORDER_FILL"),
            None,
        )
        cancel = next(
            (item for item in transactions if "ORDER_CANCEL" in str(item.get("type"))),
            None,
        )
        reject = next(
            (item for item in transactions if "ORDER_REJECT" in str(item.get("type"))),
            None,
        )
        response = {
            "orderCreateTransaction": create,
            "orderFillTransaction": fill,
            "orderCancelTransaction": cancel,
            "orderRejectTransaction": reject,
        }
        result = self._normalize_response(
            response,
            order,
            transactions=transactions,
            order_id=order_id,
        )
        result["reconciled"] = result["state"] != OrderStatus.UNKNOWN.value
        return result

    def reconcile_order(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            cached = self._orders.get(order_id)
        if cached is not None and cached.get("reconciled"):
            return cached
        create_response = self.client.get_transaction(order_id)
        create = create_response.get("transaction")
        if not isinstance(create, dict):
            return {"reconciled": False, "state": OrderStatus.UNKNOWN.value}
        since = self.client.get_transactions_since(order_id)
        transactions = since.get("transactions")
        if not isinstance(transactions, list):
            raise OandaAPIError(
                "OANDA transaction history omitted transactions.",
                failure_type="invalid_response",
            )
        batch_id = create.get("batchID")
        related = [create] + [
            item for item in transactions
            if isinstance(item, dict) and item.get("batchID") == batch_id
        ]
        order = Order(
            symbol=str(create.get("instrument", "")),
            side=OrderSide.BUY if Decimal(str(create.get("units", "0"))) > 0 else OrderSide.SELL,
            quantity=abs(float(Decimal(str(create.get("units", "0"))))),
            price=float(Decimal(str(create.get("priceBound", "1")))),
            id=str(create.get("clientExtensions", {}).get("id", "")),
        )
        return self._normalize_transactions(
            related,
            order,
            order_id=order_id,
        )

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        return self.reconcile_order(order_id)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        require_simulation_broker(self, "OANDA order cancellation")
        if not self.live_submission_enabled:
            return {
                "success": False,
                "status": OrderStatus.REJECTED.value,
                "reason": "OANDA live execution is disabled.",
            }
        try:
            response = self.client.cancel_order(order_id)
        except OandaAPIError:
            return {
                "success": False,
                "status": OrderStatus.UNKNOWN.value,
                "reason": "OANDA cancellation outcome is uncertain; recovery is required.",
            }
        return {
            "success": bool(response.get("orderCancelTransaction")),
            "status": (
                OrderStatus.CANCELLED.value
                if response.get("orderCancelTransaction")
                else OrderStatus.UNKNOWN.value
            ),
            "order_id": order_id,
            "transactions": [
                value for key, value in response.items()
                if key.endswith("Transaction") and isinstance(value, dict)
            ],
        }
