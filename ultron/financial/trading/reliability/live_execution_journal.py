from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


_NONTERMINAL_STATES = (
    "CLAIMED",
    "SUBMITTING",
    "UNRESOLVED",
    "BALANCE_PENDING",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "CANCEL_REQUESTED",
)


class LiveExecutionJournal:
    """Durable, fail-closed journal for live broker order intents."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS live_order_intents (
                    idempotency_key TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    symbol TEXT,
                    side TEXT,
                    quantity TEXT,
                    reference_price TEXT,
                    state TEXT NOT NULL,
                    exchange_order_id TEXT,
                    status_json TEXT,
                    fills_json TEXT,
                    error TEXT
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS live_account_state (
                    state_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS live_daily_equity (
                    utc_date TEXT PRIMARY KEY,
                    starting_equity TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(
            str(self.db_path),
            timeout=20,
            isolation_level=None,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def validate_storage(self) -> dict[str, Any]:
        """Check SQLite's local durability settings and database integrity."""
        with self._connect() as db:
            journal_mode = str(
                db.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()
            synchronous = int(
                db.execute("PRAGMA synchronous").fetchone()[0]
            )
            integrity = str(
                db.execute("PRAGMA integrity_check").fetchone()[0]
            ).lower()
        result = {
            "database_path_absolute": self.db_path.is_absolute(),
            "database_file": self.db_path.is_file(),
            "journal_mode": journal_mode,
            "synchronous": synchronous,
            "integrity_check": integrity,
        }
        result["valid"] = (
            result["database_path_absolute"]
            and result["database_file"]
            and journal_mode == "wal"
            and synchronous == 2
            and integrity == "ok"
        )
        return result

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _utc(value: datetime) -> str:
        if value.tzinfo is None:
            raise ValueError("Journal timestamps must be timezone-aware.")
        return value.astimezone(UTC).isoformat()

    def claim(
        self,
        idempotency_key: str,
        client_order_id: str,
        *,
        created_at: datetime,
    ) -> bool:
        if not idempotency_key.strip() or not client_order_id.strip():
            raise ValueError("Journal identifiers are required.")
        timestamp = self._utc(created_at)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                active = db.execute(
                    """
                    SELECT 1 FROM live_order_intents
                    WHERE state IN (
                        'CLAIMED', 'SUBMITTING', 'UNRESOLVED',
                        'BALANCE_PENDING', 'ACKNOWLEDGED',
                        'PARTIALLY_FILLED', 'CANCEL_REQUESTED'
                    )
                    LIMIT 1
                    """
                ).fetchone()
                if active is not None:
                    db.execute("ROLLBACK")
                    return False
                db.execute(
                    """
                    INSERT INTO live_order_intents (
                        idempotency_key, client_order_id, created_at,
                        updated_at, state
                    ) VALUES (?, ?, ?, ?, 'CLAIMED')
                    """,
                    (idempotency_key, client_order_id, timestamp, timestamp),
                )
                db.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                db.execute("ROLLBACK")
                return False
            except Exception:
                db.execute("ROLLBACK")
                raise

    def prepare_order(self, order: Any) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
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
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise RuntimeError(
                    "Live order intent was not uniquely claimed before submission."
                )
            db.execute("COMMIT")

    def mark_unresolved(self, client_order_id: str, error: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                UPDATE live_order_intents
                SET updated_at=?, state='UNRESOLVED', error=?
                WHERE client_order_id=?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    error[:1000],
                    client_order_id,
                ),
            )

    def mark_not_submitted(self, client_order_id: str) -> None:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """
                UPDATE live_order_intents
                SET updated_at=?, state='REJECTED', error=NULL
                WHERE client_order_id=? AND state='CLAIMED'
                """,
                (datetime.now(UTC).isoformat(), client_order_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Unsubmitted order intent changed unexpectedly.")

    def mark_rejected(self, client_order_id: str, reason: str) -> None:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """
                UPDATE live_order_intents
                SET updated_at=?, state='REJECTED', error=?
                WHERE client_order_id=? AND state='SUBMITTING'
                """,
                (datetime.now(UTC).isoformat(), reason[:1000], client_order_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Rejected live order was absent from the journal.")

    def get_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT idempotency_key, client_order_id, created_at, symbol,
                       state, exchange_order_id, status_json, error
                FROM live_order_intents WHERE idempotency_key=?
                """,
                (idempotency_key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def record_reconciliation(self, result: dict[str, Any]) -> None:
        client_order_id = result.get("client_order_id")
        if not isinstance(client_order_id, str) or not client_order_id:
            raise ValueError("Reconciliation must identify its client order.")
        state = str(result.get("state", "unknown")).upper()
        mapped_state = {
            "ACKNOWLEDGED": "ACKNOWLEDGED",
            "PARTIALLY_FILLED": "PARTIALLY_FILLED",
            "FILLED": "FILLED",
            "CANCEL_REQUESTED": "CANCEL_REQUESTED",
            "CANCELLED": "CANCELLED",
            "REJECTED": "REJECTED",
            "EXPIRED": "EXPIRED",
        }.get(state, "UNRESOLVED")
        if mapped_state in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}:
            mapped_state = "BALANCE_PENDING"
        else:
            mapped_state = "UNRESOLVED"
        status = {
            key: value
            for key, value in result.items()
            if key not in {"fills"}
        }
        fills = result.get("fills", [])
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """
                UPDATE live_order_intents
                SET updated_at=?, state=?, exchange_order_id=?,
                    status_json=?, fills_json=?, error=NULL
                WHERE client_order_id=?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    mapped_state,
                    result.get("order_id"),
                    self._json(status),
                    self._json(fills),
                    client_order_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Reconciled order is absent from the live journal.")

    def rows_needing_recovery(self) -> list[dict[str, Any]]:
        states = ",".join("?" for _ in _NONTERMINAL_STATES)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT idempotency_key, client_order_id, created_at, symbol,
                       state, exchange_order_id
                FROM live_order_intents
                WHERE state IN ({states})
                ORDER BY created_at
                """,
                _NONTERMINAL_STATES,
            ).fetchall()
        return [dict(row) for row in rows]

    def has_unresolved_orders(self) -> bool:
        return bool(self.rows_needing_recovery())

    def compare_and_record_account(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        canonical = self._canonical_balances(snapshot)
        encoded = self._json(canonical)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value_json FROM live_account_state WHERE state_key='balances'"
            ).fetchone()
            if row is None:
                db.execute(
                    """
                    INSERT INTO live_account_state(state_key, value_json, updated_at)
                    VALUES('balances', ?, ?)
                    """,
                    (encoded, datetime.now(UTC).isoformat()),
                )
                db.execute("COMMIT")
                return {"valid": True, "baseline_created": True, "differences": []}

            previous = json.loads(row["value_json"])
            if previous == canonical:
                db.execute("COMMIT")
                return {"valid": True, "baseline_created": False, "differences": []}

            differences = sorted(
                currency
                for currency in set(previous) | set(canonical)
                if previous.get(currency) != canonical.get(currency)
            )
            db.execute("ROLLBACK")
            return {
                "valid": False,
                "baseline_created": False,
                "differences": differences,
                "reason": "Authoritative account balances changed outside a reconciled execution.",
            }

    def record_settled_account(self, snapshot: dict[str, Any]) -> None:
        canonical = self._canonical_balances(snapshot)
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO live_account_state(state_key, value_json, updated_at)
                VALUES('balances', ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (self._json(canonical), datetime.now(UTC).isoformat()),
            )

    @staticmethod
    def _canonical_balances(
        snapshot: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        balances = snapshot.get("balances")
        if not isinstance(balances, dict):
            raise ValueError("Authoritative account snapshot has no balances.")
        canonical: dict[str, dict[str, str]] = {}
        try:
            for currency, balance in balances.items():
                if not isinstance(balance, dict):
                    raise ValueError
                available = Decimal(str(balance["available"]))
                hold = Decimal(str(balance["hold"]))
                if not available.is_finite() or not hold.is_finite():
                    raise ValueError
                canonical[str(currency).upper()] = {
                    "available": format(available.normalize(), "f"),
                    "hold": format(hold.normalize(), "f"),
                }
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Authoritative account balances are malformed.") from exc
        return canonical

    def reconcile_execution_balances(
        self,
        snapshot: dict[str, Any],
        fills: list[dict[str, Any]],
        *,
        tolerance: Decimal = Decimal("0.00000001"),
    ) -> dict[str, Any]:
        actual = self._canonical_balances(snapshot)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT value_json FROM live_account_state WHERE state_key='balances'"
                ).fetchone()
                if row is None:
                    raise RuntimeError("No authoritative balance baseline exists.")
                expected = json.loads(row["value_json"])
                deltas: dict[str, Decimal] = {}
                try:
                    for fill in fills:
                        product_parts = str(fill["symbol"]).upper().split("-")
                        if len(product_parts) != 2:
                            raise ValueError
                        base_currency, quote_currency = product_parts
                        quantity = Decimal(str(fill["quantity"]))
                        price = Decimal(str(fill["price"]))
                        fee = Decimal(str(fill["fee"]))
                        side = str(fill["side"]).lower()
                        if (
                            not quantity.is_finite()
                            or not price.is_finite()
                            or not fee.is_finite()
                            or quantity <= 0
                            or price <= 0
                            or fee < 0
                            or side not in {"buy", "sell"}
                        ):
                            raise ValueError
                        notional = quantity * price
                        direction = Decimal(1) if side == "buy" else Decimal(-1)
                        deltas[base_currency] = (
                            deltas.get(base_currency, Decimal(0))
                            + direction * quantity
                        )
                        deltas[quote_currency] = (
                            deltas.get(quote_currency, Decimal(0))
                            - direction * notional
                        )
                        fee_currency = fill.get("fee_currency")
                        if fee and not fee_currency:
                            raise ValueError
                        if fee_currency:
                            currency = str(fee_currency).upper()
                            deltas[currency] = (
                                deltas.get(currency, Decimal(0)) - fee
                            )
                except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
                    raise ValueError(
                        "Authoritative fills cannot reconcile balances."
                    ) from exc

                differences: list[str] = []
                currencies = set(expected) | set(actual) | set(deltas)
                for currency in currencies:
                    previous = expected.get(
                        currency,
                        {"available": "0", "hold": "0"},
                    )
                    current = actual.get(
                        currency,
                        {"available": "0", "hold": "0"},
                    )
                    expected_available = (
                        Decimal(previous["available"])
                        + deltas.get(currency, Decimal(0))
                    )
                    actual_available = Decimal(current["available"])
                    expected_hold = Decimal(previous["hold"])
                    actual_hold = Decimal(current["hold"])
                    if (
                        abs(actual_available - expected_available) > tolerance
                        or abs(actual_hold - expected_hold) > tolerance
                    ):
                        differences.append(currency)
                if differences:
                    db.execute("ROLLBACK")
                    return {
                        "valid": False,
                        "differences": sorted(differences),
                        "reason": (
                            "Authoritative balances do not reconcile to the "
                            "recorded fills."
                        ),
                    }

                db.execute(
                    """
                    INSERT INTO live_account_state(state_key, value_json, updated_at)
                    VALUES('balances', ?, ?)
                    ON CONFLICT(state_key) DO UPDATE SET
                        value_json=excluded.value_json,
                        updated_at=excluded.updated_at
                    """,
                    (self._json(actual), datetime.now(UTC).isoformat()),
                )
                pending = db.execute(
                    """
                    SELECT client_order_id, status_json
                    FROM live_order_intents WHERE state='BALANCE_PENDING'
                    """
                ).fetchall()
                for pending_order in pending:
                    status_json = json.loads(pending_order["status_json"] or "{}")
                    terminal = str(status_json.get("state", "unknown")).upper()
                    if terminal not in {
                        "FILLED",
                        "CANCELLED",
                        "REJECTED",
                        "EXPIRED",
                    }:
                        raise RuntimeError(
                            "Live order journal has an invalid terminal "
                            "reconciliation state."
                        )
                    db.execute(
                        """
                        UPDATE live_order_intents SET state=?
                        WHERE client_order_id=? AND state='BALANCE_PENDING'
                        """,
                        (terminal, pending_order["client_order_id"]),
                    )
                db.execute("COMMIT")
                return {"valid": True, "differences": []}
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def daily_starting_equity(
        self,
        equity: float,
        *,
        utc_date: date | None = None,
    ) -> float:
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("Daily starting equity must be finite and positive.")
        day = (utc_date or datetime.now(UTC).date()).isoformat()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT OR IGNORE INTO live_daily_equity(utc_date, starting_equity)
                VALUES(?, ?)
                """,
                (day, str(equity)),
            )
            row = db.execute(
                "SELECT starting_equity FROM live_daily_equity WHERE utc_date=?",
                (day,),
            ).fetchone()
            db.execute("COMMIT")
        if row is None:
            raise RuntimeError("Could not load persistent daily starting equity.")
        result = float(row["starting_equity"])
        if not math.isfinite(result) or result <= 0:
            raise RuntimeError("Persistent daily starting equity is invalid.")
        return result
