from __future__ import annotations

import threading

from datetime import UTC, datetime
from math import isfinite
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .engine import PaperTradingEngine
from .signal_generator import FinancialSignalGenerator

from .reliability.price_guard import (
    PriceGuard,
    PriceSnapshot,
)

from .reliability.reconciliation import (
    PortfolioReconciler,
)

from .reliability.idempotency import (
    OrderIdempotency,
)
from .reliability.live_execution_journal import (
    LiveExecutionJournal,
)

from .risk.daily_loss_guard import (
    DailyLossGuard,
)

from .operations.trading_control import (
    TradingControl,
)
from .market.interface import MarketAdapter
from .market_data.service import MarketDataService
from .models import OrderStatus
from ultron.security.permission.rules import require_simulation_broker

class FinancialTradingController:
    """
    Coordinates financial decisions with a guarded broker execution path.

    Pipeline:

        FinancialAgent
            ↓
        PriceGuard
            ↓
        SignalGenerator
            ↓
        TradingControl
            ↓
        DailyLossGuard
            ↓
        PortfolioReconciliation
            ↓
        Idempotency
            ↓
        PaperTradingEngine
            ↓
        PaperBroker or durably journaled live broker
    """

    def __init__(
        self,
        trading_engine: PaperTradingEngine,
        signal_generator: FinancialSignalGenerator | None = None,
        price_guard: PriceGuard | None = None,
        idempotency: OrderIdempotency | None = None,
        daily_loss_guard: DailyLossGuard | None = None,
        trading_control: TradingControl | None = None,
        reconciler: PortfolioReconciler | None = None,
        market_data: MarketDataService | None = None,
        market_adapter: MarketAdapter | None = None,
        live_execution_journal: LiveExecutionJournal | None = None,
    ):
        if trading_engine is None:
            raise ValueError(
                "Trading engine is required."
            )

        self._execution_lock = threading.RLock()

        self.trading_engine = trading_engine

        self.market_adapter = market_adapter

        self.market_data = market_data

        if self.market_data is None and self.market_adapter is not None:
         self.market_data = MarketDataService(
        self.market_adapter
    )

        self.signal_generator = (
            signal_generator
            or FinancialSignalGenerator()
        )

        self.price_guard = (
            price_guard
            or PriceGuard(
                max_age_seconds=30
            )
        )

        self.idempotency = (
            idempotency
            or OrderIdempotency()
        )

        self.daily_loss_guard = (
            daily_loss_guard
            or DailyLossGuard(
                max_daily_loss_percent=2.0
            )
        )

        self.trading_control = (
            trading_control
            or TradingControl()
        )

        self.reconciler = (
            reconciler
            or PortfolioReconciler()
        )

        self.live_execution_journal = live_execution_journal
        self._live_broker = bool(
            getattr(self.trading_engine.broker, "supports_live_orders", False)
        )
        require_simulation_broker(
            self.trading_engine.broker,
            "Financial trading controller",
        )
        initial_portfolio = self.trading_engine.broker.get_account_snapshot({})
        initial_equity = float(initial_portfolio["equity"])
        if self._live_broker:
            if self.live_execution_journal is None:
                raise RuntimeError(
                    "Live broker requires a durable order journal."
                )
            self._recover_live_orders()
            initial_portfolio = self.trading_engine.broker.get_account_snapshot({})
            if self.live_execution_journal.has_unresolved_orders():
                raise RuntimeError(
                    "The live broker has unresolved live orders; trading is blocked pending reconciliation."
                )
            if not self.live_execution_journal.rows_needing_recovery():
                account_check = self.live_execution_journal.compare_and_record_account(
                    initial_portfolio
                )
                if not account_check["valid"]:
                    raise RuntimeError(
                        "Live broker account state does not match the durable execution journal."
                    )
            initial_equity = float(initial_portfolio["equity"])
            self.daily_starting_equity = (
                self.live_execution_journal.daily_starting_equity(initial_equity)
            )
        else:
            self.daily_starting_equity = initial_equity

    def _recover_live_orders(self) -> None:
        journal = self.live_execution_journal
        broker = self.trading_engine.broker
        if journal is None:
            raise RuntimeError("Live execution recovery requires a durable journal.")

        recovered_fills: list[dict[str, Any]] = []
        recovered_provider_orders: list[dict[str, Any]] = []
        recovered_orders = False
        for row in journal.rows_needing_recovery():
            client_order_id = row["client_order_id"]
            if row["state"] == "CLAIMED":
                journal.mark_not_submitted(client_order_id)
                continue
            try:
                exchange_order_id = row.get("exchange_order_id")
                if exchange_order_id:
                    result = broker.reconcile_order(exchange_order_id)
                    result["client_order_id"] = client_order_id
                elif row.get("symbol"):
                    created_at = datetime.fromisoformat(row["created_at"])
                    if getattr(broker, "provider_key", None) == "oanda_v20":
                        result = broker.reconcile_submission(
                            client_order_id,
                            row["symbol"],
                            created_at=created_at,
                            since_transaction_id=row.get("baseline_transaction_id"),
                        )
                    else:
                        result = broker.reconcile_submission(
                            client_order_id,
                            row["symbol"],
                            created_at=created_at,
                        )
                else:
                    journal.mark_unresolved(
                        client_order_id,
                        "Order intent was submitted without a durable product identifier.",
                    )
                    continue
            except (RuntimeError, TypeError, ValueError) as exc:
                journal.mark_unresolved(
                    client_order_id,
                    f"Recovery failed: {type(exc).__name__}",
                )
                continue

            if not result.get("reconciled", False):
                journal.mark_unresolved(
                    client_order_id,
                    "Coinbase order history did not resolve the submission.",
                )
                continue
            journal.record_reconciliation(result)
            recovered_orders = True
            recovered_fills.extend(result.get("fills", []))
            recovered_provider_orders.append(result)

        remaining = journal.rows_needing_recovery()
        if any(row["state"] != "BALANCE_PENDING" for row in remaining):
            return
        account = broker.get_account_snapshot({})
        if recovered_orders:
            provider_reconciler = getattr(
                journal, "reconcile_execution_account", None
            )
            if getattr(broker, "provider_key", None) == "oanda_v20":
                if not callable(provider_reconciler):
                    raise RuntimeError(
                        "OANDA provider-specific account reconciliation is unavailable."
                    )
                balance_check = provider_reconciler(account, recovered_provider_orders)
            else:
                balance_check = journal.reconcile_execution_balances(
                    account,
                    recovered_fills,
                )
            if not balance_check["valid"]:
                raise RuntimeError(
                    "Recovered live orders do not reconcile to authoritative account state."
                )

    def _live_account_snapshot(
        self,
        prices: dict[str, float],
    ) -> dict[str, Any]:
        snapshot = self.trading_engine.broker.get_account_snapshot(prices)
        journal = self.live_execution_journal
        if journal is None:
            raise RuntimeError("Live account reconciliation journal is unavailable.")
        result = journal.compare_and_record_account(snapshot)
        if not result["valid"]:
            raise RuntimeError(
                "Authoritative broker state differs from the durable account baseline."
            )
        return snapshot

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Request cancellation and durably record its authoritative outcome."""
        require_simulation_broker(
            self.trading_engine.broker,
            "Order cancellation",
        )
        if not self._live_broker:
            return self.trading_engine.broker.cancel_order(order_id)
        journal = self.live_execution_journal
        if journal is None:
            raise RuntimeError("Live order cancellation requires a durable journal.")
        result = self.trading_engine.broker.cancel_order(order_id)
        status = self.trading_engine.broker.reconcile_order(order_id)
        if status.get("reconciled"):
            journal.record_reconciliation(status)
            if status.get("state") in {"cancelled", "filled", "rejected", "expired"}:
                account = self.trading_engine.broker.get_account_snapshot({})
                provider_reconciler = getattr(
                    journal, "reconcile_execution_account", None
                )
                if (
                    getattr(self.trading_engine.broker, "provider_key", None)
                    == "oanda_v20"
                ):
                    if not callable(provider_reconciler):
                        raise RuntimeError(
                            "OANDA provider-specific account reconciliation is unavailable."
                        )
                    balance_check = provider_reconciler(account, [status])
                else:
                    balance_check = journal.reconcile_execution_balances(
                        account,
                        status.get("fills", []),
                    )
                if not balance_check["valid"]:
                    raise RuntimeError(
                        "Cancelled order does not reconcile to authoritative account state."
                    )
        return {**result, "reconciliation": status}

    def execute_from_market(
        self,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch a validated market price and execute the trading decision."""
        require_simulation_broker(
            self.trading_engine.broker,
            "Financial order execution",
        )
        if self.market_data is None:
            return {
                "success": False,
                "stage": "market_data",
                "reason": "Market data service is not configured.",
            }

        if not isinstance(analysis, dict):
            return {
                "success": False,
                "stage": "validation",
                "reason": "Financial analysis must be a dictionary.",
            }

        symbol = str(analysis.get("symbol", "")).strip().upper()
        if not symbol:
            return {
                "success": False,
                "stage": "market_data",
                "reason": "Financial analysis must contain a symbol.",
            }

        try:
            market_result = self.market_data.get_current_price(symbol)
        except (RuntimeError, TypeError, ValueError) as exc:
            return {
                "success": False,
                "stage": "market_data",
                "reason": str(exc),
            }

        if not market_result.get("valid", False):
            return {
                "success": False,
                "stage": "market_data",
                "reason": market_result.get(
                    "reason",
                    "Market data validation failed.",
                ),
                "market_data": market_result,
            }

        return self.execute(
            analysis=analysis,
            price=float(market_result["price"]),
            price_timestamp=market_result["timestamp"],
        )

    def execute(
        self,
        analysis: dict[str, Any],
        price: float,
        price_timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        require_simulation_broker(
            self.trading_engine.broker,
            "Financial order execution",
        )
        # Serialize the idempotency-check -> broker-execution -> idempotency-record
        # transaction within this process. Without this lock, concurrent calls can
        # both observe an empty idempotency key and both submit an order.
        with self._execution_lock:
            return self._execute_unlocked(
                analysis=analysis,
                price=price,
                price_timestamp=price_timestamp,
            )

    def _execute_unlocked(
        self,
        analysis: dict[str, Any],
        price: float,
        price_timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Validate and execute a financial trading decision through the configured
        broker, keeping live execution gated by durable recovery and risk checks.
        """
        require_simulation_broker(
            self.trading_engine.broker,
            "Financial order execution",
        )

        # ---------------------------------------------------------
        # Basic validation
        # ---------------------------------------------------------

        if not isinstance(analysis, dict):
            return {
                "success": False,
                "stage": "validation",
                "reason": (
                    "Financial analysis must be a dictionary."
                ),
            }

        if not isfinite(price) or price <= 0:
            return {
                "success": False,
                "stage": "validation",
                "reason": "Price must be finite and positive.",
            }

        # ---------------------------------------------------------
        # Price freshness protection
        # ---------------------------------------------------------

        if price_timestamp is None:
            return {
                "success": False,
                "stage": "price_guard",
                "reason": "A source price timestamp is required; refusing an untimestamped price.",
            }
        timestamp = price_timestamp

        symbol = str(
            analysis.get("symbol", "")
        ).strip().upper()

        snapshot = PriceSnapshot(
            symbol=symbol,
            price=float(price),
            timestamp=timestamp,
        )

        guard_result = self.price_guard.validate(
            snapshot
        )

        if not guard_result.get("valid", False):
            return {
                "success": False,
                "stage": "price_guard",
                "reason": guard_result.get(
                    "reason",
                    "Price validation failed.",
                ),
                "price_guard": guard_result,
            }

        # ---------------------------------------------------------
        # Generate trading signal
        # ---------------------------------------------------------

        try:
            signal = self.signal_generator.generate(
                analysis,
                price,
            )

        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "stage": "signal_generation",
                "reason": str(exc),
                "price_guard": guard_result,
            }
        is_hold = signal.action.value == "hold"
        execution_instrument = None

        if not is_hold:
            adapter = (
                self.market_data.adapter
                if self.market_data is not None
                else self.market_adapter
            )
            get_instrument = getattr(adapter, "get_instrument", None)
            if callable(get_instrument):
                try:
                    instrument = get_instrument(signal.symbol)
                except (RuntimeError, TypeError, ValueError) as exc:
                    return {
                        "success": False,
                        "status": "blocked",
                        "stage": "instrument_validation",
                        "reason": f"Instrument metadata could not be validated: {exc}",
                        "signal": signal,
                    }
                execution_instrument = instrument
                if not getattr(instrument, "orderable", True):
                    return {
                        "success": False,
                        "status": "blocked",
                        "stage": "instrument_validation",
                        "reason": (
                            f"{signal.symbol} is market-data-only and cannot be "
                            "submitted as a paper or live order."
                        ),
                        "signal": signal,
                        "instrument_type": getattr(
                            getattr(instrument, "instrument_type", None),
                            "value",
                            "unknown",
                        ),
                    }
                if getattr(instrument, "exchange", None) in {"NSE", "BSE"}:
                    metadata_is_current = getattr(
                        instrument, "metadata_is_current", None
                    )
                    if not callable(metadata_is_current) or not metadata_is_current():
                        return {
                            "success": False,
                            "status": "blocked",
                            "stage": "instrument_validation",
                            "reason": (
                                f"{signal.symbol} has no current, verified exchange "
                                "instrument metadata for paper or live order validation."
                            ),
                            "signal": signal,
                        }

        if self._live_broker and not is_hold:
            if not getattr(
                self.trading_engine.broker,
                "live_submission_enabled",
                False,
            ):
                return {
                    "success": False,
                    "status": "disabled",
                    "stage": "live_authorization",
                    "reason": (
                        "Live order submission is disabled. The development kill switch "
                        "and explicit authorization are required."
                    ),
                    "signal": signal,
                    "price_guard": guard_result,
                }
            if self.live_execution_journal is None:
                return {
                    "success": False,
                    "status": "blocked",
                    "stage": "execution_journal",
                    "reason": "Durable live execution journal is unavailable.",
                    "signal": signal,
                }
            if self.live_execution_journal.has_unresolved_orders():
                return {
                    "success": False,
                    "status": "reconciliation_required",
                    "stage": "execution_journal",
                    "reason": (
                        "A prior live order is unresolved; reconcile it before "
                        "submitting another order."
                    ),
                    "signal": signal,
                }

         # ---------------------------------------------------------
        # Manual trading control
        # ---------------------------------------------------------

        control_result = (
            self.trading_control.validate()
        )

        if not control_result.get("valid", False):

            # Emergency SELL exits remain possible.
            if signal.action.value != "sell":
                return {
                    "success": False,
                    "status": "paused",
                    "stage": "trading_control",
                    "reason": control_result.get(
                        "reason",
                        "Trading is manually paused.",
                    ),
                    "signal": signal,
                    "trading_control": control_result,
                    "portfolio": (
                        self.trading_engine.broker.snapshot(
                            {signal.symbol: price}
                        )
                    ),
                    "price_guard": guard_result,
                }

        # ---------------------------------------------------------
        # Daily loss protection
        # ---------------------------------------------------------

        try:
            current_portfolio = (
                self._live_account_snapshot({signal.symbol: price})
                if self._live_broker
                else self.trading_engine.broker.snapshot(
                    {signal.symbol: price}
                )
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            return {
                "success": False,
                "status": "reconciliation_failed",
                "stage": "account_snapshot",
                "reason": str(exc),
                "signal": signal,
                "price_guard": guard_result,
                "trading_control": control_result,
            }

        current_equity = float(
            current_portfolio["equity"]
        )

        daily_loss_result = (
            self.daily_loss_guard.validate(
                starting_equity=(
                    self.daily_starting_equity
                ),
                current_equity=current_equity,
            )
        )

        if not daily_loss_result.get(
            "valid",
            False,
        ):

            # Emergency SELL exits remain possible.
            if signal.action.value != "sell":
                return {
                    "success": False,
                    "status": "halted",
                    "stage": "daily_loss_guard",
                    "reason": daily_loss_result.get(
                        "reason",
                        "Daily loss limit exceeded.",
                    ),
                    "signal": signal,
                    "daily_loss": daily_loss_result,
                    "portfolio": current_portfolio,
                    "price_guard": guard_result,
                    "trading_control": control_result,
                }

        # ---------------------------------------------------------
        # Portfolio reconciliation
        # ---------------------------------------------------------

        broker_portfolio = current_portfolio

        # Current paper-trading architecture has a single
        # authoritative broker state. Until an independent
        # internal state store exists, use the broker snapshot
        # as the internal baseline.
        internal_portfolio = {
            "cash": broker_portfolio["cash"],
            "positions": {
                symbol: dict(position)
                for symbol, position
                in broker_portfolio[
                    "positions"
                ].items()
            },
        }

        reconciliation = (
            self.reconciler.reconcile(
                internal=internal_portfolio,
                broker=broker_portfolio,
            )
        )

        if not reconciliation.get(
            "valid",
            False,
        ):
            return {
                "success": False,
                "status": "reconciliation_failed",
                "stage": "reconciliation",
                "reason": reconciliation.get(
                    "reason",
                    "Portfolio reconciliation failed.",
                ),
                "signal": signal,
                "reconciliation": reconciliation,
                "portfolio": broker_portfolio,
                "price_guard": guard_result,
                "daily_loss": daily_loss_result,
                "trading_control": control_result,
            }
                # ---------------------------------------------------------
        # HOLD signals do not create orders
        # ---------------------------------------------------------

        if is_hold:
            result = self.trading_engine.execute(
                signal,
                market_prices={
                    signal.symbol: price,
                },
            )

            return {
                "success": bool(
                    result.get("success", False)
                ),
                "signal": signal,
                "execution": result,
                "portfolio": result.get(
                    "portfolio"
                ),
                "price_guard": guard_result,
                "daily_loss": daily_loss_result,
                "trading_control": control_result,
                "reconciliation": reconciliation,
            }

        # ---------------------------------------------------------
        # Idempotency
        # ---------------------------------------------------------

        # Explicit logical IDs deduplicate retries without conflating independent decisions.
        logical_intent_id = (analysis.get("logical_intent_id") or analysis.get("intent_id") or analysis.get("order_intent_id"))
        if logical_intent_id is not None:
            logical_intent_id = str(logical_intent_id).strip()
            if not logical_intent_id:
                return {"success": False, "status": "invalid_intent_id", "stage": "idempotency", "reason": "Logical intent ID cannot be blank.", "signal": signal}
            idempotency_key = f"intent:{logical_intent_id}"
        else:
            # Backward-compatible duplicate protection within this controller instance.
            idempotency_key = (
                f"{signal.symbol}:"
                f"{signal.action.value}:"
                f"{signal.price:.8f}:"
                f"{signal.strategy or ''}"
            )

        if self._live_broker:
            return self._execute_live_order(
                signal=signal,
                price=price,
                idempotency_key=idempotency_key,
                guard_result=guard_result,
                daily_loss_result=daily_loss_result,
                control_result=control_result,
                reconciliation=reconciliation,
                instrument=execution_instrument,
            )

        existing = self.idempotency.check(
            idempotency_key
        )

        if existing is not None:
            return {
                "success": True,
                "status": "duplicate",
                "stage": "idempotency",
                "reason": (
                    "Duplicate order prevented."
                ),
                "idempotency_key": (
                    idempotency_key
                ),
                "original_order_id": (
                    existing.order_id
                ),
                "signal": signal,
                "portfolio": (
                    self.trading_engine.broker.snapshot(
                        {signal.symbol: price}
                    )
                ),
                "price_guard": guard_result,
                "daily_loss": daily_loss_result,
                "trading_control": control_result,
                "reconciliation": reconciliation,
            }

        # Persistent atomic idempotency claim, shared across processes.
        claim = getattr(self.idempotency, "claim", None)
        if not callable(claim):
            raise RuntimeError("Persistent idempotency claim unavailable; refusing execution.")
        if not claim(idempotency_key):
            existing = self.idempotency.check(idempotency_key)
            return {
                "success": False,
                "status": "duplicate" if existing else "idempotency_pending",
                "stage": "idempotency",
                "reason": "Order key already completed." if existing else "Order key already claimed or unresolved; reconciliation required.",
                "idempotency_key": idempotency_key,
                "original_order_id": existing.order_id if existing else None,
                "signal": signal,
                "portfolio": self.trading_engine.broker.snapshot({signal.symbol: price}),
                "price_guard": guard_result,
                "daily_loss": daily_loss_result,
                "trading_control": control_result,
                "reconciliation": reconciliation,
            }

        # An exception after claim leaves it reserved; reconcile before retrying.
        engine_kwargs: dict[str, Any] = {}
        if (
            execution_instrument is not None
            and getattr(execution_instrument, "exchange", None) in {"NSE", "BSE"}
        ):
            engine_kwargs["instrument"] = execution_instrument
        result = self.trading_engine.execute(
            signal,
            market_prices={signal.symbol: price},
            **engine_kwargs,
        )

        # ---------------------------------------------------------
        # Record successful order for idempotency
        # ---------------------------------------------------------

        if result.get("success") and result.get(
            "order"
        ):
            order_id = result[
                "order"
            ].get("order_id")

            if order_id:
                self.idempotency.record(
                    idempotency_key,
                    order_id,
                )

        # ---------------------------------------------------------
        # Final response
        # ---------------------------------------------------------

        return {
            "success": bool(
                result.get("success", False)
            ),
            "signal": signal,
            "execution": result,
            "portfolio": result.get(
                "portfolio"
            ),
            "price_guard": guard_result,
            "daily_loss": daily_loss_result,
            "trading_control": control_result,
            "reconciliation": reconciliation,
            "idempotency_key": idempotency_key,
        }

    def _execute_live_order(
        self,
        *,
        signal: Any,
        price: float,
        idempotency_key: str,
        guard_result: dict[str, Any],
        daily_loss_result: dict[str, Any],
        control_result: dict[str, Any],
        reconciliation: dict[str, Any],
        instrument: Any | None = None,
    ) -> dict[str, Any]:
        require_simulation_broker(
            self.trading_engine.broker,
            "Provider-backed order execution",
        )
        journal = self.live_execution_journal
        broker = self.trading_engine.broker
        if journal is None:
            raise RuntimeError("Live execution journal is unavailable.")
        client_order_id = str(
            uuid5(NAMESPACE_URL, f"ultron-live-order:{idempotency_key}")
        )
        created_at = datetime.now(UTC)
        if not journal.claim(
            idempotency_key,
            client_order_id,
            created_at=created_at,
        ):
            existing = journal.get_by_key(idempotency_key)
            return {
                "success": False,
                "status": "duplicate" if existing else "journal_conflict",
                "stage": "execution_journal",
                "reason": (
                    "This live intent has already been claimed. Reconcile it; "
                    "automatic resubmission is prohibited."
                ),
                "client_order_id": client_order_id,
                "original_order_id": (
                    existing.get("exchange_order_id") if existing else None
                ),
                "signal": signal,
            }

        def persist_before_submit(order: Any) -> None:
            fresh = broker.get_account_snapshot({signal.symbol: price})
            account_check = journal.compare_and_record_account(fresh)
            if not account_check["valid"]:
                raise RuntimeError(
                    "Broker account state changed before order submission."
                )
            journal.prepare_order(order)

        try:
            engine_kwargs: dict[str, Any] = {
                "order_id": client_order_id,
                "before_submit": persist_before_submit,
            }
            if (
                instrument is not None
                and getattr(instrument, "exchange", None) in {"NSE", "BSE"}
            ):
                engine_kwargs["instrument"] = instrument
            execution = self.trading_engine.execute(
                signal,
                market_prices={signal.symbol: price},
                **engine_kwargs,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            intent = journal.get_by_key(idempotency_key)
            if intent is not None and intent.get("state") == "CLAIMED":
                journal.mark_not_submitted(client_order_id)
            else:
                journal.mark_unresolved(
                    client_order_id,
                    f"Execution outcome requires reconciliation: {type(exc).__name__}",
                )
            return {
                "success": False,
                "status": "reconciliation_required",
                "stage": "live_execution",
                "reason": (
                    "Live execution did not complete safely; resolve its durable "
                    "journal entry before retrying."
                ),
                "failure_type": type(exc).__name__,
                "client_order_id": client_order_id,
                "signal": signal,
            }

        order_result = execution.get("order")
        if not isinstance(order_result, dict):
            journal.mark_not_submitted(client_order_id)
            return {
                "success": bool(execution.get("success", False)),
                "status": execution.get("status", "rejected"),
                "stage": "live_execution",
                "reason": execution.get("reason", "Order was not submitted."),
                "execution": execution,
                "signal": signal,
                "price_guard": guard_result,
                "daily_loss": daily_loss_result,
                "trading_control": control_result,
                "reconciliation": reconciliation,
                "idempotency_key": idempotency_key,
            }

        if (
            order_result.get("success") is not True
            or order_result.get("accepted") is not True
        ):
            intent = journal.get_by_key(idempotency_key)
            if (
                order_result.get("status") == OrderStatus.REJECTED.value
                and intent is not None
                and intent.get("state") == "SUBMITTING"
            ):
                journal.mark_rejected(
                    client_order_id,
                    str(order_result.get("reason", "The broker rejected the order.")),
                )
                return {
                    "success": False,
                    "status": OrderStatus.REJECTED.value,
                    "stage": "live_execution",
                    "reason": "The broker rejected the order before acceptance.",
                    "execution": execution,
                    "signal": signal,
                    "price_guard": guard_result,
                    "daily_loss": daily_loss_result,
                    "trading_control": control_result,
                    "reconciliation": reconciliation,
                    "idempotency_key": idempotency_key,
                }
            journal.mark_unresolved(
                client_order_id,
                "Broker submission outcome is not confirmed.",
            )
            return {
                "success": False,
                "status": OrderStatus.UNKNOWN.value,
                "stage": "live_execution",
                "reason": (
                    "Broker submission outcome is uncertain; recovery is required "
                    "before another order can be submitted."
                ),
                "execution": execution,
                "client_order_id": client_order_id,
                "signal": signal,
            }

        exchange_order_id = order_result.get("order_id")
        if not isinstance(exchange_order_id, str) or not exchange_order_id:
            journal.mark_unresolved(
                client_order_id,
                "Broker returned no order identifier after submission.",
            )
            return {
                "success": False,
                "status": OrderStatus.UNKNOWN.value,
                "stage": "live_execution",
                "reason": "Order outcome requires authoritative broker recovery.",
                "client_order_id": client_order_id,
                "signal": signal,
            }

        try:
            authoritative = broker.reconcile_order(exchange_order_id)
            authoritative["client_order_id"] = client_order_id
            journal.record_reconciliation(authoritative)
        except (RuntimeError, TypeError, ValueError) as exc:
            journal.mark_unresolved(
                client_order_id,
                f"Authoritative order reconciliation failed: {type(exc).__name__}",
            )
            return {
                "success": False,
                "status": OrderStatus.UNKNOWN.value,
                "stage": "order_reconciliation",
                "reason": (
                "The broker accepted the request but authoritative order/fill "
                    "reconciliation did not complete."
                ),
                "client_order_id": client_order_id,
                "order_id": exchange_order_id,
                "signal": signal,
            }

        terminal_states = {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REJECTED.value,
            OrderStatus.EXPIRED.value,
        }
        if authoritative.get("state") not in terminal_states:
            journal.mark_unresolved(
                client_order_id,
                "Order remains active or has an unresolved status.",
            )
            return {
                "success": False,
                "status": OrderStatus.UNKNOWN.value,
                "stage": "order_reconciliation",
                "reason": "Broker order is not in a reconciled terminal state.",
                "client_order_id": client_order_id,
                "order_id": exchange_order_id,
                "reconciliation": authoritative,
                "signal": signal,
            }

        try:
            account = broker.get_account_snapshot({signal.symbol: price})
            provider_reconciler = getattr(
                journal, "reconcile_execution_account", None
            )
            if getattr(broker, "provider_key", None) == "oanda_v20":
                if not callable(provider_reconciler):
                    raise RuntimeError(
                        "OANDA provider-specific account reconciliation is unavailable."
                    )
                balance_check = provider_reconciler(account, [authoritative])
            else:
                balance_check = journal.reconcile_execution_balances(
                    account,
                    authoritative.get("fills", []),
                )
        except (RuntimeError, TypeError, ValueError) as exc:
            journal.mark_unresolved(
                client_order_id,
                f"Account reconciliation failed: {type(exc).__name__}",
            )
            return {
                "success": False,
                "status": OrderStatus.UNKNOWN.value,
                "stage": "balance_reconciliation",
                "reason": "Broker account state did not reconcile after execution.",
                "client_order_id": client_order_id,
                "order_id": exchange_order_id,
                "signal": signal,
            }
        if not balance_check["valid"]:
            journal.mark_unresolved(
                client_order_id,
                "Broker account state differs from authoritative order fills.",
            )
            return {
                "success": False,
                "status": "reconciliation_failed",
                "stage": "balance_reconciliation",
                "reason": balance_check["reason"],
                "differences": balance_check["differences"],
                "client_order_id": client_order_id,
                "order_id": exchange_order_id,
                "signal": signal,
            }

        execution["order"] = {**order_result, **authoritative}
        execution["status"] = authoritative["state"]
        execution["success"] = True
        execution["portfolio"] = account
        return {
            "success": True,
            "signal": signal,
            "execution": execution,
            "portfolio": account,
            "price_guard": guard_result,
            "daily_loss": daily_loss_result,
            "trading_control": control_result,
            "reconciliation": reconciliation,
            "order_reconciliation": authoritative,
            "balance_reconciliation": balance_check,
            "idempotency_key": idempotency_key,
            "client_order_id": client_order_id,
        }
