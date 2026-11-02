"""Autonomous, simulation-only financial runtime for ULTRON.

Uses ULTRON's real Container, FinancialSkill, MarketDataService, trading controller,
risk guards and PaperBroker. It never creates a live broker or submits live orders.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import math
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("ultron.autonomous_trading")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return repr(value)


def _value(obj: Any, *names: str, default=None):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _action_text(action: Any) -> str:
    value = getattr(action, "value", action)
    return str(value).lower()


class AutonomousTradingRuntime:
    """Runs recurring analysis, guarded paper entries, exits and SQLite journaling."""

    def __init__(
        self,
        container=None,
        *,
        symbols: list[str] | None = None,
        interval_seconds: int | None = None,
        state_path: str | Path | None = None,
        max_positions: int | None = None,
        max_price_divergence_pct: float | None = None,
    ):
        backend = os.getenv("ULTRON_EXECUTION_BACKEND", "simulation").strip().lower()
        if backend != "simulation":
            raise RuntimeError(
                "Autonomous runtime is simulation-only. Set "
                "ULTRON_EXECUTION_BACKEND=simulation."
            )

        if container is None:
            from ultron.infrastructure.container import Container
            container = Container()
            self._require_safe_environment()

        self.container = container
        self.skill = getattr(container, "financial_skill", None)
        self.controller = getattr(container, "financial_trading_controller", None)
        if self.skill is None or self.controller is None:
            raise RuntimeError("ULTRON container lacks financial skill/controller.")
        if getattr(self.controller, "market_data", None) is None:
            raise RuntimeError(
                "Financial controller has no validated market-data service. "
                "Set ULTRON_FINANCIAL_MARKET_PROVIDER=alpaca."
            )
        broker = getattr(getattr(self.controller, "trading_engine", None), "broker", None)
        if broker is None or type(broker).__name__ != "PaperBroker":
            raise RuntimeError(
                "Autonomous runtime requires the existing local PaperBroker. "
                f"Found {type(broker).__name__ if broker else 'None'}."
            )

        self.symbols = self._normalize_symbols(
            symbols or os.getenv("ULTRON_AUTONOMOUS_SYMBOLS", "AAPL,MSFT,NVDA,SPY").split(",")
        )
        self.interval_seconds = int(
            interval_seconds or os.getenv("ULTRON_AUTONOMOUS_INTERVAL_SECONDS", "60")
        )
        self.max_positions = int(
            max_positions or os.getenv("ULTRON_AUTONOMOUS_MAX_POSITIONS", "3")
        )
        self.max_price_divergence_pct = float(
            max_price_divergence_pct
            if max_price_divergence_pct is not None
            else os.getenv("ULTRON_AUTONOMOUS_MAX_PRICE_DIVERGENCE_PCT", "1.0")
        )
        if self.interval_seconds < 15:
            raise ValueError("Monitoring interval must be at least 15 seconds.")
        if self.max_positions < 1:
            raise ValueError("Maximum open positions must be at least 1.")
        if not 0 < self.max_price_divergence_pct <= 5:
            raise ValueError("Price divergence limit must be in (0, 5] percent.")

        default_state = Path("data/financial/autonomous_trading.sqlite3")
        self.state_path = Path(state_path or os.getenv("ULTRON_AUTONOMOUS_STATE_DB", default_state))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # Runtime opts into durable cross-process idempotency explicitly.
        from ultron.financial.trading.reliability.idempotency import OrderIdempotency
        idem_path = os.getenv("ULTRON_IDEMPOTENCY_DB", str(self.state_path.with_name("order_idempotency.sqlite3")))
        self.controller.idempotency = OrderIdempotency(db_path=idem_path)
        self._init_db()
        self._install_guarded_exit_method()
        self._recover_paper_broker()

    @staticmethod
    def _require_safe_environment():
        provider = os.getenv("ULTRON_FINANCIAL_MARKET_PROVIDER", "yahoo").strip().lower()
        backend = os.getenv("ULTRON_EXECUTION_BACKEND", "simulation").strip().lower()
        if backend != "simulation":
            raise RuntimeError(
                "Autonomous runtime is simulation-only. Set "
                "ULTRON_EXECUTION_BACKEND=simulation."
            )
        if provider != "alpaca":
            raise RuntimeError(
                "Autonomous runtime requires ULTRON_FINANCIAL_MARKET_PROVIDER=alpaca; "
                "Yahoo is not wired to the validated MarketDataService."
            )

    @staticmethod
    def _normalize_symbols(symbols):
        result = []
        for symbol in symbols:
            value = str(symbol).strip().upper()
            if value and value not in result:
                if not value.replace(".", "").replace("-", "").isalnum():
                    raise ValueError(f"Invalid market symbol: {value!r}")
                result.append(value)
        if not result:
            raise ValueError("At least one symbol is required.")
        return result

    def _connect(self):
        connection = sqlite3.connect(self.state_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _init_db(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    stop_loss REAL,
                    take_profit REAL,
                    confidence REAL NOT NULL,
                    strategy TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    replayed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS execution_intents (
                    intent_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, symbol TEXT NOT NULL,
                    reason TEXT NOT NULL, state TEXT NOT NULL,
                    result_json TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS tradingview_events (
                    event_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'PROCESSING', 'COMPLETED', 'REJECTED', 'RECONCILIATION_REQUIRED'
                    )),
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS position_rules (
                    symbol TEXT PRIMARY KEY,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    opened_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(executions)")}
            if "intent_id" not in columns:
                db.execute("ALTER TABLE executions ADD COLUMN intent_id TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_intent_id ON executions(intent_id) WHERE intent_id IS NOT NULL")

    def _install_guarded_exit_method(self):
        """Add an exit path that reuses controller.execute and all its guards."""
        controller = self.controller
        if callable(getattr(controller, "execute_exit_from_market", None)):
            return
        if not callable(getattr(controller, "execute", None)):
            raise RuntimeError("Trading controller has no execute() method.")
        if not hasattr(controller, "signal_generator"):
            raise RuntimeError(
                "Controller has no signal_generator; refusing to invent an exit path."
            )

        def execute_exit_from_market(
            this, symbol: str, reason: str, *, intent_id: str | None = None
        ):
            symbol = str(symbol).strip().upper()
            if not symbol:
                return {"success": False, "stage": "validation", "reason": "Empty exit symbol."}
            service = getattr(this, "market_data", None)
            if service is None:
                return {"success": False, "stage": "market_data", "reason": "Market data unavailable."}
            try:
                market = service.get_current_price(symbol)
            except Exception as exc:
                return {"success": False, "stage": "market_data", "reason": str(exc)}
            if not isinstance(market, dict) or not market.get("valid"):
                return {
                    "success": False, "stage": "market_data",
                    "reason": market.get("reason", "Market price validation failed.")
                    if isinstance(market, dict) else "Invalid market-data result.",
                    "market_data": market,
                }

            original_generator = this.signal_generator
            runtime = self

            class _ExitSignalGenerator:
                def generate(self, *args, **kwargs):
                    template = original_generator.generate(*args, **kwargs)
                    try:
                        action_type = type(template.action)
                        sell_action = getattr(action_type, "SELL", None)
                        if sell_action is None:
                            sell_action = action_type("sell")
                        if dataclasses.is_dataclass(template):
                            return dataclasses.replace(
                                template,
                                action=sell_action,
                                stop_loss=None,
                                take_profit=None,
                                reason=f"Autonomous risk exit: {reason}",
                            )
                        cloned = __import__("copy").copy(template)
                        cloned.action = sell_action
                        cloned.stop_loss = None
                        cloned.take_profit = None
                        cloned.reason = f"Autonomous risk exit: {reason}"
                        return cloned
                    except Exception as exc:
                        raise RuntimeError(f"Cannot safely construct SELL signal: {exc}") from exc

            # Runtime is single-threaded. Restore the original generator even on failure.
            try:
                this.signal_generator = _ExitSignalGenerator()
                analysis = {
                    "symbol": symbol,
                    "decision": {"decision": "watch", "confidence": 1.0},
                    "reasoning": {"confidence": 1.0, "risks": []},
                    "risk": {"risk": {"risk_level": "low"}},
                    "strategies": ["autonomous_exit"],
                }
                if intent_id is not None:
                    analysis["logical_intent_id"] = intent_id
                result = this.execute(
                    analysis=analysis,
                    price=float(market["price"]),
                    price_timestamp=market["timestamp"],
                )
                if isinstance(result, dict):
                    result.setdefault("market_data", {
                        "provider": market.get("provider"),
                        "feed": market.get("feed"),
                        "age_seconds": market.get("age_seconds"),
                    })
                return result
            except Exception as exc:
                return {"success": False, "stage": "exit", "reason": str(exc)}
            finally:
                this.signal_generator = original_generator

        controller.execute_exit_from_market = __import__("types").MethodType(
            execute_exit_from_market, controller
        )

    def _execution_rows(self):
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM executions ORDER BY id ASC"
            ).fetchall()

    def _recover_paper_broker(self):
        """Restore exact journaled paper fills into a fresh in-memory PaperBroker.

        Recovery is state restoration, not a new trading decision. Replay the
        recorded side, quantity, and price directly through the simulation-only
        broker instead of regenerating a signal and recalculating its size.
        """
        from ultron.financial.trading.models import Order, OrderSide

        broker = self.controller.trading_engine.broker
        try:
            snapshot = broker.snapshot({})
            positions = snapshot.get("positions", {})
            trade_count = int(snapshot.get("trade_count", 0))
        except Exception as exc:
            raise RuntimeError(f"Cannot inspect PaperBroker state: {exc}") from exc

        with self._connect() as db:
            unresolved = db.execute("SELECT intent_id, symbol, state FROM execution_intents WHERE state IN ('PREPARED', 'RECONCILIATION_REQUIRED') ORDER BY created_at").fetchall()
            unresolved_events = db.execute(
                "SELECT event_id, symbol, status FROM tradingview_events "
                "WHERE status IN ('PROCESSING', 'RECONCILIATION_REQUIRED') "
                "ORDER BY created_at"
            ).fetchall()
        if unresolved or unresolved_events:
            details = ", ".join(
                [
                    f"{row['intent_id']}:{row['symbol']}:{row['state']}"
                    for row in unresolved
                ]
                + [
                    f"tradingview:{row['event_id']}:{row['symbol']}:{row['status']}"
                    for row in unresolved_events
                ]
            )
            raise RuntimeError("Unresolved execution intent(s) require reconciliation; refusing automatic broker replay or new autonomous trading: " + details)
        rows = self._execution_rows()
        if not rows:
            LOG.info("No prior simulated executions to recover.")
            return
        if trade_count > 0 or positions:
            LOG.info("PaperBroker already has state; skipping journal replay.")
            return

        replayed = 0
        for row in rows:
            action_text = str(row["action"]).strip().lower()
            if action_text not in {"buy", "sell"}:
                raise RuntimeError(
                    f"Paper portfolio recovery stopped at journal entry {row['id']}: "
                    f"unsupported action {action_text!r}"
                )

            # Exact filled quantity is stored in the controller's engine response.
            quantity = None
            try:
                recorded = json.loads(row["result_json"] or "{}")
                engine_result = recorded.get("execution", {})
                if isinstance(engine_result, dict):
                    quantity = engine_result.get("quantity")
                    if quantity is None:
                        order_result = engine_result.get("order", {})
                        if isinstance(order_result, dict):
                            quantity = order_result.get("quantity")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

            try:
                quantity = float(quantity)
                price = float(row["price"])
            except (TypeError, ValueError):
                raise RuntimeError(
                    f"Paper portfolio recovery stopped at journal entry {row['id']}: "
                    "exact fill quantity or price is missing; refusing to guess."
                )
            if quantity <= 0 or price <= 0:
                raise RuntimeError(
                    f"Paper portfolio recovery stopped at journal entry {row['id']}: "
                    "fill quantity and price must be positive."
                )

            side = OrderSide.BUY if action_text == "buy" else OrderSide.SELL
            order = Order(
                symbol=str(row["symbol"]).strip().upper(),
                side=side,
                quantity=quantity,
                price=price,
            )
            try:
                result = broker.submit(order)
            except Exception as exc:
                raise RuntimeError(
                    f"Paper portfolio recovery stopped at journal entry {row['id']}: {exc}"
                ) from exc
            if not isinstance(result, dict) or not result.get("success"):
                raise RuntimeError(
                    f"Paper portfolio recovery failed at journal entry {row['id']}: {result!r}"
                )

            with self._connect() as db:
                db.execute("UPDATE executions SET replayed=1 WHERE id=?", (row["id"],))
            replayed += 1

        recovered = broker.snapshot({})
        if int(recovered.get("trade_count", -1)) != replayed:
            raise RuntimeError(
                "Paper portfolio recovery verification failed: "
                f"expected {replayed} replayed fills, broker reports "
                f"{recovered.get('trade_count')!r} trades."
            )
        LOG.warning(
            "Recovered %d exact journaled simulated fill(s). "
            "This is local simulation-state recovery, not broker reconciliation.",
            replayed,
        )

    def _rebuild_signal(self, row):
        generator = self.controller.signal_generator
        seed_analysis = {
            "symbol": row["symbol"],
            "decision": {"decision": "consider", "confidence": row["confidence"]},
            "reasoning": {"confidence": row["confidence"], "risks": []},
            "risk": {"risk": {"risk_level": "low"}},
            "strategies": [row["strategy"] or "autonomous_recovery"],
        }
        # A BUY-shaped template supplies the exact project signal type.
        template = generator.generate(seed_analysis, float(row["price"]))
        action_type = type(template.action)
        action = getattr(action_type, row["action"].upper(), None)
        if action is None:
            action = action_type(row["action"].lower())
        changes = {
            "action": action,
            "price": float(row["price"]),
            "stop_loss": row["stop_loss"],
            "take_profit": row["take_profit"],
            "confidence": float(row["confidence"]),
            "strategy": row["strategy"],
            "reason": row["reason"],
        }
        if dataclasses.is_dataclass(template):
            return dataclasses.replace(template, **changes)
        clone = __import__("copy").copy(template)
        for key, value in changes.items():
            setattr(clone, key, value)
        return clone

    def _snapshot(self):
        return self.controller.trading_engine.broker.snapshot({})

    @staticmethod
    def _position_quantity(position):
        value = _value(position, "quantity", "qty", "shares", "position_qty", default=0)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _position_entry(position):
        value = _value(position, "entry_price", "average_entry_price", "avg_entry_price", "price")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _positions(self):
        snapshot = self._snapshot()
        positions = snapshot.get("positions", {}) or {}
        if isinstance(positions, dict):
            return [(str(symbol).upper(), position) for symbol, position in positions.items()
                    if self._position_quantity(position) > 0]
        return []

    def _rules(self, symbol, position):
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM position_rules WHERE symbol=?", (symbol,)
            ).fetchone()
        entry = self._position_entry(position)
        if row:
            entry = float(row["entry_price"])
            stop = float(row["stop_loss"])
            target = float(row["take_profit"])
        elif entry and entry > 0:
            stop, target = entry * 0.96, entry * 1.08
            with self._connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO position_rules "
                    "(symbol,entry_price,stop_loss,take_profit,opened_at) VALUES (?,?,?,?,?)",
                    (symbol, entry, stop, target, _utc_now()),
                )
        else:
            return None
        return {"entry_price": entry, "stop_loss": stop, "take_profit": target}

    def _record_cycle(self, symbol, outcome, details):
        with self._connect() as db:
            db.execute(
                "INSERT INTO cycles(created_at,symbol,outcome,details_json) VALUES (?,?,?,?)",
                (_utc_now(), symbol, outcome, json.dumps(details, default=_json_default)),
            )

    def _begin_execution_intent(self, symbol, reason):
        """Persist intent before an order can be submitted."""
        intent_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connect() as db:
            db.execute("INSERT INTO execution_intents (intent_id,created_at,updated_at,symbol,reason,state) VALUES (?,?,?,?,?,'PREPARED')", (intent_id, now, now, str(symbol).strip().upper(), str(reason)))
        return intent_id

    def _set_execution_intent(self, intent_id, state, result=None, error=None):
        with self._connect() as db:
            db.execute("UPDATE execution_intents SET updated_at=?, state=?, result_json=?, error=? WHERE intent_id=?", (_utc_now(), state, json.dumps(result, default=_json_default) if result is not None else None, str(error)[:2000] if error is not None else None, intent_id))
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError(f"Execution intent {intent_id} disappeared.")

    def _execute_with_durable_intent(self, symbol, reason, operation):
        """Prepare before execution; never auto-retry an uncertain operation."""
        intent_id = self._begin_execution_intent(symbol, reason)
        try:
            result = operation()
        except BaseException as exc:
            try:
                self._set_execution_intent(intent_id, "RECONCILIATION_REQUIRED", error=f"Execution outcome uncertain: {type(exc).__name__}: {exc}")
            except Exception as state_exc:
                LOG.critical("Could not persist uncertain state for intent %s: %s", intent_id, state_exc)
            raise
        execution = result.get("execution", {}) if isinstance(result, dict) else {}
        status = str(_value(execution, "status", default="")).lower()
        signal = result.get("signal") if isinstance(result, dict) else None
        action = _action_text(_value(signal, "action", default=_value(execution, "action", default="")))
        filled = bool(isinstance(result, dict) and result.get("success") and status in {"filled", "closed", "executed"} and action in {"buy", "sell"})
        if not filled:
            self._set_execution_intent(intent_id, "REJECTED", result=result)
            return result
        try:
            self._record_execution(symbol, result, reason, intent_id=intent_id)
            with self._connect() as db:
                row = db.execute("SELECT state FROM execution_intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is None or row["state"] != "COMPLETED":
                raise RuntimeError("Execution recorder returned without committing a COMPLETED intent.")
        except Exception as exc:
            try:
                self._set_execution_intent(intent_id, "RECONCILIATION_REQUIRED", result=result, error=f"Fill succeeded but journal persistence failed: {type(exc).__name__}: {exc}")
            except Exception as state_exc:
                LOG.critical("Journal write and intent-state update both failed for %s: %s", intent_id, state_exc)
            raise RuntimeError(f"Execution {intent_id} may have filled, but durable journaling failed; runtime must stop for reconciliation.") from exc
        return result

    def _claim_tradingview_event(self, alert):
        fingerprint = alert.fingerprint()
        now = _utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM tradingview_events WHERE event_id=?",
                (alert.event_id,),
            ).fetchone()
            if existing is not None:
                db.execute("COMMIT")
                return existing, False, fingerprint
            db.execute(
                "INSERT INTO tradingview_events "
                "(event_id,fingerprint,created_at,updated_at,symbol,action,status) "
                "VALUES (?,?,?,?,?,?,'PROCESSING')",
                (
                    alert.event_id,
                    fingerprint,
                    now,
                    now,
                    alert.symbol,
                    alert.action,
                ),
            )
            db.execute("COMMIT")
        return None, True, fingerprint

    def _finish_tradingview_event(self, event_id, state, summary):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE tradingview_events SET updated_at=?, status=?, result_json=? "
                "WHERE event_id=? AND status='PROCESSING'",
                (
                    _utc_now(),
                    state,
                    json.dumps(summary, separators=(",", ":"), allow_nan=False),
                    event_id,
                ),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                db.execute("ROLLBACK")
                raise RuntimeError("TradingView event state changed unexpectedly.")
            db.execute("COMMIT")

    @staticmethod
    def _tradingview_execution_summary(result):
        if not isinstance(result, dict):
            return "unknown", "invalid_result", False
        execution = result.get("execution")
        if not isinstance(execution, dict):
            execution = result
        status = str(execution.get("status", result.get("status", "unknown"))).lower()
        signal = result.get("signal")
        action = _action_text(_value(signal, "action", default=execution.get("action", "")))
        filled = bool(
            result.get("success") is True
            and status in {"filled", "closed", "executed"}
            and action in {"buy", "sell"}
        )
        stage = str(result.get("stage", "execution"))
        if status in {
            "duplicate",
            "idempotency_pending",
            "journal_conflict",
            "reconciliation_required",
            "unknown",
        }:
            status = "reconciliation_required"
        return status, stage, filled

    def _tradingview_buy(self, alert):
        open_positions = self._positions()
        if any(symbol == alert.symbol for symbol, _position in open_positions):
            return {"success": False, "status": "already_held", "stage": "position_limit"}
        if len(open_positions) >= self.max_positions:
            return {"success": False, "status": "position_limit", "stage": "position_limit"}

        try:
            analysis = self.skill.execute(
                "analyze", symbol=alert.symbol, run_backtest=False
            )
        except Exception:
            return {"success": False, "status": "analysis_failed", "stage": "analysis"}
        if (
            not isinstance(analysis, dict)
            or analysis.get("success") is False
            or str(analysis.get("symbol", "")).strip().upper() != alert.symbol
        ):
            return {"success": False, "status": "analysis_failed", "stage": "analysis"}

        decision = analysis.get("decision")
        if not isinstance(decision, dict) or str(decision.get("decision", "")).lower() != "consider":
            return {"success": False, "status": "decision_rejected", "stage": "decision"}
        try:
            confidence = float(decision.get("confidence", 0.0))
        except (TypeError, ValueError, OverflowError):
            confidence = 0.0
        risk = analysis.get("risk", {})
        if isinstance(risk, dict):
            risk = risk.get("risk", risk)
        risk_level = str(_value(risk, "risk_level", default="high")).lower()
        if not math.isfinite(confidence) or confidence < 0.75 or risk_level == "high":
            return {"success": False, "status": "decision_rejected", "stage": "decision"}

        current_market_data = self.controller.market_data
        if current_market_data is None:
            return {"success": False, "status": "market_data_unavailable", "stage": "market_data"}
        try:
            current = current_market_data.get_current_price(alert.symbol)
        except Exception:
            return {"success": False, "status": "market_data_unavailable", "stage": "market_data"}
        if not isinstance(current, dict) or not current.get("valid"):
            return {"success": False, "status": "market_data_rejected", "stage": "market_data"}

        validated_price = float(current["price"])
        analysis_price = self._analysis_market_price(analysis)
        if analysis_price is None:
            return {"success": False, "status": "analysis_price_missing", "stage": "price_divergence"}
        for reference_price in (alert.reference_price, analysis_price):
            divergence = abs(reference_price - validated_price) / validated_price * 100.0
            if divergence > self.max_price_divergence_pct:
                return {"success": False, "status": "price_divergence", "stage": "price_divergence"}

        analysis["logical_intent_id"] = alert.event_id
        return self._execute_with_durable_intent(
            alert.symbol,
            f"TradingView strategy trigger {alert.strategy_id}",
            lambda: self.skill.execute(
                "paper_trade", symbol=alert.symbol, analysis=analysis
            ),
        )

    def _tradingview_sell(self, alert):
        if not any(symbol == alert.symbol for symbol, _position in self._positions()):
            return {"success": False, "status": "no_position", "stage": "position"}
        exit_method = getattr(self.controller, "execute_exit_from_market", None)
        if not callable(exit_method):
            return {"success": False, "status": "exit_path_unavailable", "stage": "execution"}
        return self._execute_with_durable_intent(
            alert.symbol,
            f"TradingView strategy exit {alert.strategy_id}",
            lambda: exit_method(
                alert.symbol,
                "TradingView strategy exit",
                intent_id=alert.event_id,
            ),
        )

    def process_tradingview_alert(self, alert):
        """Process one validated TradingView trigger through guarded paper paths."""
        from ultron.financial.trading.tradingview_webhook import TradingViewAlert

        if not isinstance(alert, TradingViewAlert):
            return {"accepted": False, "status": "rejected", "error": "invalid_alert"}
        try:
            existing, claimed, fingerprint = self._claim_tradingview_event(alert)
        except Exception:
            return {
                "accepted": False,
                "event_id": alert.event_id,
                "status": "reconciliation_required",
                "error": "event_journal_unavailable",
            }

        if not claimed:
            if existing["fingerprint"] != fingerprint:
                return {
                    "accepted": False,
                    "event_id": alert.event_id,
                    "status": "event_id_conflict",
                    "error": "event_id_reused_with_different_alert",
                }
            state = str(existing["status"])
            if state == "PROCESSING":
                return {
                    "accepted": False,
                    "event_id": alert.event_id,
                    "status": "processing",
                    "error": "event_already_claimed",
                }
            if state == "RECONCILIATION_REQUIRED":
                return {
                    "accepted": False,
                    "event_id": alert.event_id,
                    "status": "reconciliation_required",
                    "error": "event_requires_reconciliation",
                }
            try:
                summary = json.loads(existing["result_json"] or "{}")
            except (TypeError, ValueError):
                summary = {}
            return {
                "accepted": True,
                "event_id": alert.event_id,
                "status": "duplicate",
                "execution_status": summary.get("execution_status", state.lower()),
            }

        try:
            result = (
                self._tradingview_buy(alert)
                if alert.action == "buy"
                else self._tradingview_sell(alert)
            )
            execution_status, stage, filled = self._tradingview_execution_summary(result)
            if filled:
                state = "COMPLETED"
            elif execution_status == "reconciliation_required":
                state = "RECONCILIATION_REQUIRED"
            else:
                state = "REJECTED"
            summary = {"execution_status": execution_status, "stage": stage}
            self._finish_tradingview_event(alert.event_id, state, summary)
        except Exception:
            summary = {"execution_status": "reconciliation_required", "stage": "execution"}
            try:
                self._finish_tradingview_event(
                    alert.event_id, "RECONCILIATION_REQUIRED", summary
                )
            except Exception:
                LOG.critical(
                    "Could not persist uncertain TradingView event state for %s.",
                    alert.event_id,
                )
            return {
                "accepted": False,
                "event_id": alert.event_id,
                "status": "reconciliation_required",
                "error": "execution_outcome_uncertain",
            }

        return {
            "accepted": state == "COMPLETED",
            "event_id": alert.event_id,
            "status": state.lower(),
            "execution_status": execution_status,
            "stage": stage,
        }

    def _record_execution(self, symbol, result, reason="", intent_id=None):
        execution = result.get("execution", {}) if isinstance(result, dict) else {}
        signal = result.get("signal") if isinstance(result, dict) else None
        action = str(_value(execution, "action", default=_value(signal, "action", default=""))).lower()
        action = getattr(_value(signal, "action", default=action), "value", action).lower()
        status = str(_value(execution, "status", default="")).lower()
        if not (result.get("success") and status in {"filled", "closed", "executed"} and action in {"buy", "sell"}):
            return
        price = _value(signal, "price", default=_value(execution, "price"))
        if price is None:
            price = _value(execution, "average_price", default=None)
        if price is None:
            LOG.error("Execution succeeded but returned no fill price; journal entry omitted.")
            return
        stop = _value(signal, "stop_loss")
        target = _value(signal, "take_profit")
        confidence = float(_value(signal, "confidence", default=0.0) or 0.0)
        strategy = str(_value(signal, "strategy", default="autonomous"))
        signal_reason = str(_value(signal, "reason", default=reason))
        with self._connect() as db:
            db.execute(
                """INSERT INTO executions
                   (created_at,symbol,action,price,stop_loss,take_profit,confidence,strategy,reason,result_json,intent_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (_utc_now(), symbol, action, float(price), stop, target, confidence,
                 strategy, signal_reason, json.dumps(result, default=_json_default), intent_id),
            )
            if action == "buy":
                entry = float(price)
                db.execute(
                    "INSERT OR REPLACE INTO position_rules "
                    "(symbol,entry_price,stop_loss,take_profit,opened_at) VALUES (?,?,?,?,?)",
                    (symbol, entry, float(stop or entry * 0.96),
                     float(target or entry * 1.08), _utc_now()),
                )
            elif action == "sell":
                db.execute("DELETE FROM position_rules WHERE symbol=?", (symbol,))
            if intent_id is not None:
                db.execute("UPDATE execution_intents SET updated_at=?, state='COMPLETED', result_json=?, error=NULL WHERE intent_id=?", (_utc_now(), json.dumps(result, default=_json_default), intent_id))
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError(f"Execution intent {intent_id} disappeared during commit.")

    def _monitor_exits(self):
        for symbol, position in self._positions():
            rules = self._rules(symbol, position)
            if not rules:
                LOG.error("%s has an open position but no trusted entry price; exit automation skipped.", symbol)
                continue
            market = self.controller.market_data.get_current_price(symbol)
            if not isinstance(market, dict) or not market.get("valid"):
                LOG.warning("%s exit check skipped: %s", symbol,
                            market.get("reason") if isinstance(market, dict) else "invalid market data")
                continue
            price = float(market["price"])
            reason = None
            if price <= rules["stop_loss"]:
                reason = f"stop-loss threshold reached ({price:.4f} <= {rules['stop_loss']:.4f})"
            elif price >= rules["take_profit"]:
                reason = f"take-profit threshold reached ({price:.4f} >= {rules['take_profit']:.4f})"
            if reason:
                LOG.warning("%s exit condition: %s", symbol, reason)
                result = self._execute_with_durable_intent(symbol, reason, lambda: self.controller.execute_exit_from_market(symbol, reason))
                LOG.info("Exit result for %s: %s", symbol, json.dumps(result, default=_json_default))

    @staticmethod
    def _analysis_market_price(analysis):
        for obj in (
            analysis.get("market"),
            (analysis.get("reasoning") or {}).get("market"),
            ((analysis.get("reasoning") or {}).get("current") or {}).get("market"),
        ):
            if isinstance(obj, dict):
                value = obj.get("current_price", obj.get("price"))
                try:
                    if value is not None and float(value) > 0:
                        return float(value)
                except (TypeError, ValueError):
                    pass
        return None

    def _evaluate_symbol(self, symbol):
        snapshot = self._snapshot()
        current_positions = {s for s, _ in self._positions()}
        if symbol in current_positions:
            self._record_cycle(symbol, "already_held", {"symbol": symbol})
            return

        if len(current_positions) >= self.max_positions:
            self._record_cycle(symbol, "position_limit", {"open_positions": sorted(current_positions)})
            return

        # Unique per evaluation; callers must preserve the ID for retries of this intent.
        logical_intent_id = uuid.uuid4().hex
        try:
            analysis = self.skill.execute("analyze", symbol=symbol)
        except Exception as exc:
            self._record_cycle(symbol, "analysis_error", {"error": str(exc)})
            LOG.exception("%s analysis failed", symbol)
            return
        if not isinstance(analysis, dict) or analysis.get("success") is False:
            self._record_cycle(symbol, "analysis_failed", {"result": analysis})
            LOG.warning("%s analysis did not succeed.", symbol)
            return

        analysis["logical_intent_id"] = logical_intent_id
        decision = _value(analysis.get("decision"), "decision", default="")
        decision = str(decision).strip().lower()
        confidence = float(_value(analysis.get("decision"), "confidence", default=0.0) or 0.0)
        risk = analysis.get("risk", {})
        if isinstance(risk, dict):
            risk = risk.get("risk", risk)
        risk_level = str(_value(risk, "risk_level", default="high")).lower()
        if decision != "consider":
            self._record_cycle(symbol, "watch", {
                "decision": decision, "confidence": confidence, "risk_level": risk_level,
            })
            LOG.info("%s: WATCH (decision=%s confidence=%.2f risk=%s)", symbol, decision, confidence, risk_level)
            return

        try:
            current = self.controller.market_data.get_current_price(symbol)
        except Exception as exc:
            self._record_cycle(symbol, "market_data_error", {"error": str(exc)})
            LOG.warning("%s entry skipped: market data error: %s", symbol, exc)
            return
        if not isinstance(current, dict) or not current.get("valid"):
            details = current if isinstance(current, dict) else {"result": repr(current)}
            self._record_cycle(symbol, "market_data_rejected", details)
            LOG.warning("%s entry skipped: %s", symbol, details)
            return

        analysis_price = self._analysis_market_price(analysis)
        current_price = float(current["price"])
        if analysis_price is None:
            self._record_cycle(symbol, "analysis_price_missing", {"decision": decision})
            LOG.warning("%s entry skipped: analysis has no usable price for cross-check.", symbol)
            return
        divergence = abs(analysis_price - current_price) / current_price * 100.0
        if divergence > self.max_price_divergence_pct:
            self._record_cycle(symbol, "price_divergence", {
                "analysis_price": analysis_price, "validated_price": current_price,
                "divergence_pct": divergence,
            })
            LOG.warning("%s entry skipped: analysis/validated price divergence %.3f%% exceeds %.3f%%.",
                        symbol, divergence, self.max_price_divergence_pct)
            return

        result = self._execute_with_durable_intent(symbol, "strategy entry", lambda: self.skill.execute("paper_trade", symbol=symbol, analysis=analysis))
        LOG.info("%s guarded entry evaluation: %s", symbol, json.dumps(result, default=_json_default))
        self._record_cycle(symbol, "paper_trade_result", result)
        if not result.get("success"):
            LOG.warning("%s paper entry was rejected: %s", symbol, result.get("reason", result))
        else:
            execution = result.get("execution", {})
            if str(_value(execution, "status", default="")).lower() == "held":
                LOG.info("%s remains on HOLD; no order submitted.", symbol)

    def run_cycle(self):
        LOG.info("Autonomous cycle started; symbols=%s", ",".join(self.symbols))
        self._monitor_exits()
        # Avoid market data calls for entry evaluation when US equities are closed.
        adapter = getattr(self.controller.market_data, "adapter", None)
        market_open = True
        if adapter is not None and callable(getattr(adapter, "is_market_open", None)):
            try:
                market_open = bool(adapter.is_market_open(self.symbols[0]))
            except Exception as exc:
                market_open = False
                LOG.warning("Market clock unavailable; entry scan fails closed: %s", exc)
        if not market_open:
            LOG.info("US market is closed; exit/entry checks requiring current data are skipped.")
            return
        for symbol in self.symbols:
            self._evaluate_symbol(symbol)
        LOG.info("Autonomous cycle finished.")

    def run_forever(self):
        LOG.info(
            "Starting autonomous simulation runtime. symbols=%s interval=%ss max_positions=%s state=%s",
            ",".join(self.symbols), self.interval_seconds, self.max_positions, self.state_path,
        )
        while True:
            started = time.monotonic()
            try:
                self.run_cycle()
            except KeyboardInterrupt:
                LOG.info("Shutdown requested; stopping safely.")
                return
            except Exception:
                LOG.exception("Cycle failed; no unsafe fallback will be attempted.")
            elapsed = time.monotonic() - started
            time.sleep(max(1.0, self.interval_seconds - elapsed))


def main(argv=None):
    parser = argparse.ArgumentParser(description="ULTRON autonomous paper-trading runtime")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--symbols", help="Comma-separated symbols; overrides ULTRON_AUTONOMOUS_SYMBOLS.")
    parser.add_argument("--interval", type=int, help="Cycle interval in seconds (minimum 15).")
    parser.add_argument("--state-db", help="SQLite state database path.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("ULTRON_AUTONOMOUS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runtime = AutonomousTradingRuntime(
        symbols=args.symbols.split(",") if args.symbols else None,
        interval_seconds=args.interval,
        state_path=args.state_db,
    )
    if args.once:
        runtime.run_cycle()
    else:
        runtime.run_forever()


if __name__ == "__main__":
    main()
