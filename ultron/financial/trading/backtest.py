from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import sqrt
from typing import Any, Sequence
from uuid import uuid4

from .engine import PaperTradingEngine
from .intelligence.decision import TradingDecisionEngine
from .intelligence.decision_adapter import DecisionSignalAdapter
from .market_data.models import Candle
from .models import OrderSide


@dataclass(frozen=True)
class BacktestConfig:
    """
    Configuration for deterministic historical simulation.

    Decisions are generated using information available through the
    current candle only. Approved entries are executed on the next
    candle's open to prevent same-bar look-ahead bias.
    """

    initial_capital: float = 100_000.0
    timeframe: str = "1d"
    min_history: int = 30
    commission_rate: float = 0.001
    max_position_percent: float = 25.0
    risk_per_trade_percent: float = 1.0
    force_close_at_end: bool = True

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError(
                "Initial capital must be positive."
            )

        if not self.timeframe.strip():
            raise ValueError(
                "Timeframe is required."
            )

        if self.min_history < 2:
            raise ValueError(
                "min_history must be at least 2."
            )

        if self.commission_rate < 0:
            raise ValueError(
                "Commission rate cannot be negative."
            )

        if not 0 < self.max_position_percent <= 100:
            raise ValueError(
                "max_position_percent must be between 0 and 100."
            )

        if not 0 < self.risk_per_trade_percent <= 10:
            raise ValueError(
                "risk_per_trade_percent must be between 0 and 10."
            )


@dataclass(frozen=True)
class BacktestEquityPoint:
    timestamp: datetime
    equity: float
    cash: float
    position_value: float


@dataclass
class BacktestResult:
    """
    Complete result of one deterministic historical simulation.
    """

    run_id: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    initial_capital: float
    final_equity: float
    net_pnl: float
    return_percent: float
    max_drawdown_percent: float
    trade_count: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    average_trade_pnl: float
    volatility_percent: float
    sharpe_ratio: float
    decisions_evaluated: int
    decisions_approved: int
    decisions_rejected: int
    execution_count: int
    execution_rejections: int
    equity_curve: list[BacktestEquityPoint] = field(
        default_factory=list
    )
    trades: list[dict[str, Any]] = field(
        default_factory=list
    )
    events: list[dict[str, Any]] = field(
        default_factory=list
    )

    @property
    def profitable(self) -> bool:
        return self.net_pnl > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start,
            "end": self.end,
            "initial_capital": self.initial_capital,
            "final_equity": self.final_equity,
            "net_pnl": self.net_pnl,
            "return_percent": self.return_percent,
            "max_drawdown_percent": self.max_drawdown_percent,
            "trade_count": self.trade_count,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "profit_factor": self.profit_factor,
            "average_trade_pnl": self.average_trade_pnl,
            "volatility_percent": self.volatility_percent,
            "sharpe_ratio": self.sharpe_ratio,
            "decisions_evaluated": self.decisions_evaluated,
            "decisions_approved": self.decisions_approved,
            "decisions_rejected": self.decisions_rejected,
            "execution_count": self.execution_count,
            "execution_rejections": self.execution_rejections,
            "equity_curve": [
                {
                    "timestamp": point.timestamp,
                    "equity": point.equity,
                    "cash": point.cash,
                    "position_value": point.position_value,
                }
                for point in self.equity_curve
            ],
            "trades": list(self.trades),
            "events": list(self.events),
        }


class FinancialBacktestEngine:
    """
    Historical simulation engine for ULTRON's financial intelligence.

    Pipeline:

        historical candles
              ↓
        information available at N
              ↓
        TradingDecisionEngine
              ↓
        approved decision
              ↓
        DecisionSignalAdapter
              ↓
        next-bar execution
              ↓
        PaperTradingEngine
              ↓
        PaperBroker / Portfolio
              ↓
        equity curve + performance metrics

    The engine is long-only because the current PaperBroker does not
    support opening short positions. SELL decisions therefore close
    existing long positions.
    """

    def __init__(
        self,
        decision_engine: TradingDecisionEngine | None = None,
        config: BacktestConfig | None = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self.decision_engine = (
            decision_engine or TradingDecisionEngine()
        )

    def run(
        self,
        candles: Sequence[Candle],
        *,
        symbol: str | None = None,
    ) -> BacktestResult:
        normalized = self._validate_candles(
            candles,
            symbol=symbol,
        )

        config = self.config
        resolved_symbol = normalized[0].symbol

        from .paper_broker import PaperBroker

        broker = PaperBroker(
            initial_capital=config.initial_capital,
            commission_rate=config.commission_rate,
        )

        trading_engine = PaperTradingEngine(
            broker=broker,
            max_position_percent=config.max_position_percent,
            risk_per_trade_percent=config.risk_per_trade_percent,
        )

        pending_signal = None
        decisions_evaluated = 0
        decisions_approved = 0
        decisions_rejected = 0
        execution_count = 0
        execution_rejections = 0

        equity_curve: list[BacktestEquityPoint] = []
        events: list[dict[str, Any]] = []

        for index, candle in enumerate(normalized):
            prices = {
                resolved_symbol: candle.close,
            }

            # ---------------------------------------------------------
            # 1. Resolve exits from previously opened positions.
            # ---------------------------------------------------------
            position = broker.portfolio.positions.get(
                resolved_symbol
            )

            if position is not None and pending_signal is not None:
                exit_result = self._check_exit(
                    trading_engine,
                    pending_signal,
                    candle,
                )

                if exit_result is not None:
                    execution_count += 1

                    if exit_result.get("success"):
                        pending_signal = None
                        events.append(
                            {
                                "type": "exit",
                                "timestamp": candle.timestamp,
                                "reason": exit_result.get(
                                    "exit_reason",
                                    "exit",
                                ),
                                "price": exit_result.get(
                                    "price"
                                ),
                                "execution": exit_result,
                            }
                        )
                    else:
                        execution_rejections += 1
                        events.append(
                            {
                                "type": "exit_rejected",
                                "timestamp": candle.timestamp,
                                "execution": exit_result,
                            }
                        )

            # ---------------------------------------------------------
            # 2. Execute an entry decision generated on the previous
            #    candle at this candle's OPEN.
            # ---------------------------------------------------------
            if pending_signal is not None and index > 0:
                position = broker.portfolio.positions.get(
                    resolved_symbol
                )

                if position is None:
                    execution_price = float(candle.open)

                    try:
                        execution_signal = self._execution_signal(
                            pending_signal,
                            execution_price,
                        )

                        result = trading_engine.execute(
                            execution_signal,
                            {
                                resolved_symbol: execution_price,
                            },
                        )

                    except (TypeError, ValueError) as exc:
                        result = {
                            "success": False,
                            "status": "rejected",
                            "reason": str(exc),
                        }

                    execution_count += 1

                    if result.get("success"):
                        events.append(
                            {
                                "type": "entry",
                                "timestamp": candle.timestamp,
                                "price": execution_price,
                                "execution": result,
                            }
                        )
                    else:
                        execution_rejections += 1
                        events.append(
                            {
                                "type": "entry_rejected",
                                "timestamp": candle.timestamp,
                                "price": execution_price,
                                "execution": result,
                            }
                        )

                pending_signal = None

            # ---------------------------------------------------------
            # 3. Generate a NEW decision using data through this
            #    candle only.
            #
            #    This is the critical anti-look-ahead boundary.
            # ---------------------------------------------------------
            history = normalized[: index + 1]

            if len(history) >= config.min_history:
                closes = [
                    float(item.close)
                    for item in history
                ]

                equity = broker.portfolio.equity(
                    prices
                )

                decisions_evaluated += 1

                try:
                    decision = self.decision_engine.evaluate(
                        equity=equity,
                        symbol=resolved_symbol,
                        candles_by_timeframe={
                            config.timeframe: closes,
                        },
                        entry_price=float(candle.close),
                    )
                except (TypeError, ValueError) as exc:
                    decisions_rejected += 1
                    events.append(
                        {
                            "type": "decision_error",
                            "timestamp": candle.timestamp,
                            "reason": str(exc),
                        }
                    )
                    decision = None

                if decision is not None:
                    if decision.get("approved", False):
                        decisions_approved += 1

                        try:
                            signal = DecisionSignalAdapter.convert(
                                decision
                            )

                            position = (
                                broker.portfolio.positions.get(
                                    resolved_symbol
                                )
                            )

                            action = signal.action.value

                            # Current broker is long-only.
                            if action == "buy" and position is None:
                                pending_signal = signal

                            elif (
                                action == "sell"
                                and position is not None
                            ):
                                result = self._close_position(
                                    trading_engine,
                                    resolved_symbol,
                                    float(candle.close),
                                )

                                execution_count += 1

                                if result.get("success"):
                                    pending_signal = None
                                    events.append(
                                        {
                                            "type": "decision_exit",
                                            "timestamp": candle.timestamp,
                                            "price": float(
                                                candle.close
                                            ),
                                            "execution": result,
                                        }
                                    )
                                else:
                                    execution_rejections += 1
                                    events.append(
                                        {
                                            "type": "decision_exit_rejected",
                                            "timestamp": candle.timestamp,
                                            "execution": result,
                                        }
                                    )

                        except (TypeError, ValueError) as exc:
                            decisions_rejected += 1
                            events.append(
                                {
                                    "type": "signal_error",
                                    "timestamp": candle.timestamp,
                                    "reason": str(exc),
                                }
                            )

            # ---------------------------------------------------------
            # 4. Mark portfolio to this candle's close.
            # ---------------------------------------------------------
            equity = broker.portfolio.equity(prices)

            cash = float(
                broker.portfolio.cash
            )

            position_value = equity - cash

            equity_curve.append(
                BacktestEquityPoint(
                    timestamp=candle.timestamp,
                    equity=float(equity),
                    cash=cash,
                    position_value=float(position_value),
                )
            )

        # -------------------------------------------------------------
        # 5. Close an outstanding position at the final close.
        # -------------------------------------------------------------
        final_candle = normalized[-1]

        if config.force_close_at_end:
            position = broker.portfolio.positions.get(
                resolved_symbol
            )

            if position is not None:
                result = self._close_position(
                    trading_engine,
                    resolved_symbol,
                    float(final_candle.close),
                )

                execution_count += 1

                if result.get("success"):
                    events.append(
                        {
                            "type": "forced_final_exit",
                            "timestamp": final_candle.timestamp,
                            "price": float(final_candle.close),
                            "execution": result,
                        }
                    )
                else:
                    execution_rejections += 1
                    events.append(
                        {
                            "type": "forced_final_exit_rejected",
                            "timestamp": final_candle.timestamp,
                            "execution": result,
                        }
                    )

                final_equity = broker.portfolio.equity(
                    {resolved_symbol: final_candle.close}
                )

                if (
                    not equity_curve
                    or equity_curve[-1].equity
                    != final_equity
                ):
                    equity_curve.append(
                        BacktestEquityPoint(
                            timestamp=final_candle.timestamp,
                            equity=float(final_equity),
                            cash=float(
                                broker.portfolio.cash
                            ),
                            position_value=0.0,
                        )
                    )

        metrics = self._calculate_metrics(
            broker,
            equity_curve,
            final_candle.close,
            resolved_symbol,
        )

        trades = [
            {
                "order_id": trade.order_id,
                "symbol": trade.symbol,
                "side": trade.side.value,
                "quantity": trade.quantity,
                "price": trade.price,
                "fees": trade.fees,
                "realized_pnl": trade.realized_pnl,
                "timestamp": trade.timestamp,
            }
            for trade in broker.portfolio.trades
        ]

        return BacktestResult(
            run_id=str(uuid4()),
            symbol=resolved_symbol,
            timeframe=config.timeframe,
            start=normalized[0].timestamp,
            end=normalized[-1].timestamp,
            initial_capital=float(
                config.initial_capital
            ),
            final_equity=metrics["final_equity"],
            net_pnl=metrics["net_pnl"],
            return_percent=metrics["return_percent"],
            max_drawdown_percent=metrics[
                "max_drawdown_percent"
            ],
            trade_count=metrics["trade_count"],
            winning_trades=metrics["winning_trades"],
            losing_trades=metrics["losing_trades"],
            win_rate=metrics["win_rate"],
            gross_profit=metrics["gross_profit"],
            gross_loss=metrics["gross_loss"],
            profit_factor=metrics["profit_factor"],
            average_trade_pnl=metrics[
                "average_trade_pnl"
            ],
            volatility_percent=metrics[
                "volatility_percent"
            ],
            sharpe_ratio=metrics["sharpe_ratio"],
            decisions_evaluated=decisions_evaluated,
            decisions_approved=decisions_approved,
            decisions_rejected=decisions_rejected,
            execution_count=execution_count,
            execution_rejections=execution_rejections,
            equity_curve=equity_curve,
            trades=trades,
            events=events,
        )

    @staticmethod
    def _validate_candles(
        candles: Sequence[Candle],
        *,
        symbol: str | None,
    ) -> list[Candle]:
        if not candles:
            raise ValueError(
                "Backtest requires at least one candle."
            )

        normalized = list(candles)

        if any(
            not isinstance(candle, Candle)
            for candle in normalized
        ):
            raise TypeError(
                "Backtest candles must contain Candle objects."
            )

        if any(
            not candle.is_complete
            for candle in normalized
        ):
            raise ValueError(
                "Backtest requires finalized candles; incomplete candles "
                "cannot be used for historical decisions."
            )

        symbols = {
            candle.symbol
            for candle in normalized
        }

        if len(symbols) != 1:
            raise ValueError(
                "Backtest requires candles for exactly one symbol."
            )

        resolved = normalized[0].symbol

        if symbol is not None:
            requested = symbol.strip().upper()

            if not requested:
                raise ValueError(
                    "Symbol cannot be empty."
                )

            if requested != resolved:
                raise ValueError(
                    "Candle symbol does not match requested symbol."
                )

        timestamps = [
            candle.timestamp
            for candle in normalized
        ]

        if timestamps != sorted(timestamps):
            raise ValueError(
                "Backtest candles must be chronologically ordered."
            )

        if len(set(timestamps)) != len(timestamps):
            raise ValueError(
                "Duplicate candle timestamps detected."
            )

        return normalized

    @staticmethod
    def _execution_signal(
        signal,
        execution_price: float,
    ):
        """
        Move an entry signal to the next bar's execution price while
        preserving the decision's absolute risk levels.
        """
        from dataclasses import replace

        if execution_price <= 0:
            raise ValueError(
                "Execution price must be positive."
            )

        return replace(
            signal,
            price=execution_price,
        )

    @staticmethod
    def _check_exit(
        trading_engine: PaperTradingEngine,
        signal,
        candle: Candle,
    ) -> dict[str, Any] | None:
        """
        Resolve intrabar stop/target events conservatively.

        If both stop and target are touched inside one OHLC bar,
        stop-loss wins because historical OHLC data does not reveal
        the true intrabar path.
        """
        if signal.stop_loss is not None:
            if candle.low <= signal.stop_loss:
                return trading_engine.monitor_exit(
                    signal,
                    float(signal.stop_loss),
                )

        if signal.take_profit is not None:
            if candle.high >= signal.take_profit:
                return trading_engine.monitor_exit(
                    signal,
                    float(signal.take_profit),
                )

        return None

    @staticmethod
    def _close_position(
        trading_engine: PaperTradingEngine,
        symbol: str,
        price: float,
    ) -> dict[str, Any]:
        from .signal import SignalAction, TradingSignal

        position = (
            trading_engine.broker.portfolio.positions.get(
                symbol
            )
        )

        if position is None or position.quantity <= 0:
            return {
                "success": True,
                "status": "no_position",
                "symbol": symbol,
            }

        signal = TradingSignal(
            symbol=symbol,
            action=SignalAction.SELL,
            price=price,
            confidence=1.0,
            strategy="BACKTEST_EXIT",
            reason="Backtest position close.",
        )

        return trading_engine.execute(
            signal,
            {symbol: price},
        )

    @staticmethod
    def _calculate_metrics(
        broker,
        equity_curve: Sequence[BacktestEquityPoint],
        final_price: float,
        symbol: str,
    ) -> dict[str, float | int]:
        final_equity = float(
            broker.portfolio.equity(
                {symbol: final_price}
            )
        )

        initial = float(
            broker.portfolio.initial_capital
        )

        net_pnl = final_equity - initial

        return_percent = (
            net_pnl / initial * 100.0
            if initial > 0
            else 0.0
        )

        equities = [
            float(point.equity)
            for point in equity_curve
        ]

        peak = initial
        max_drawdown = 0.0

        for equity in equities:
            peak = max(peak, equity)

            if peak > 0:
                drawdown = (
                    (peak - equity) / peak * 100.0
                )
                max_drawdown = max(
                    max_drawdown,
                    drawdown,
                )

        sells = [
            trade
            for trade in broker.portfolio.trades
            if trade.side == OrderSide.SELL
        ]

        winning = [
            trade
            for trade in sells
            if trade.realized_pnl > 0
        ]

        losing = [
            trade
            for trade in sells
            if trade.realized_pnl < 0
        ]

        gross_profit = sum(
            trade.realized_pnl
            for trade in winning
        )

        gross_loss = abs(
            sum(
                trade.realized_pnl
                for trade in losing
            )
        )

        trade_count = len(sells)

        win_rate = (
            len(winning) / trade_count * 100.0
            if trade_count
            else 0.0
        )

        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (
                float("inf")
                if gross_profit > 0
                else 0.0
            )
        )

        average_trade_pnl = (
            sum(
                trade.realized_pnl
                for trade in sells
            )
            / trade_count
            if trade_count
            else 0.0
        )

        returns: list[float] = []

        for previous, current in zip(
            equities,
            equities[1:],
        ):
            if previous > 0:
                returns.append(
                    (current - previous)
                    / previous
                )

        if len(returns) >= 2:
            mean_return = sum(returns) / len(returns)

            variance = sum(
                (value - mean_return) ** 2
                for value in returns
            ) / (len(returns) - 1)

            stddev = sqrt(variance)

            volatility_percent = (
                stddev * sqrt(len(returns)) * 100.0
            )

            sharpe_ratio = (
                mean_return / stddev * sqrt(len(returns))
                if stddev > 0
                else 0.0
            )
        else:
            volatility_percent = 0.0
            sharpe_ratio = 0.0

        return {
            "final_equity": final_equity,
            "net_pnl": float(net_pnl),
            "return_percent": float(return_percent),
            "max_drawdown_percent": float(
                max_drawdown
            ),
            "trade_count": trade_count,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": float(win_rate),
            "gross_profit": float(gross_profit),
            "gross_loss": float(gross_loss),
            "profit_factor": float(profit_factor),
            "average_trade_pnl": float(
                average_trade_pnl
            ),
            "volatility_percent": float(
                volatility_percent
            ),
            "sharpe_ratio": float(sharpe_ratio),
        }
