from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .models import (
    BacktestConfig,
    BacktestResult,
    Trade,
    TradingStrategy,
)


class BacktestEngine:
    """
    Production-grade historical strategy simulator.

    Responsibilities:
        - validate historical market data
        - generate SMA crossover signals
        - simulate positions
        - model transaction costs and slippage
        - track portfolio equity
        - calculate risk-adjusted metrics
        - compare strategy performance with buy-and-hold

    This engine performs research/simulation only.
    It never places live orders.
    """

    REQUIRED_COLUMNS = ("Close",)

    def run(
        self,
        history: pd.DataFrame,
        strategy: TradingStrategy,
        initial_capital: float = 10_000.0,
        config: BacktestConfig | None = None,
    ) -> dict[str, Any]:
        """
        Run a historical simulation.

        `initial_capital` is retained for backward compatibility.
        When `config` is supplied, its capital configuration takes
        precedence.
        """

        self._validate_strategy(strategy)
        data = self._prepare_history(history)

        if config is None:
            config = BacktestConfig(
                initial_capital=initial_capital,
            )

        initial_capital = float(config.initial_capital)

        self._validate_capital(initial_capital)

        data = self._build_indicators(
            data,
            strategy,
        )

        (
            final_capital,
            equity_curve,
            trades,
            exposure_periods,
        ) = self._simulate(
            data=data,
            config=config,
        )

        equity = pd.Series(
            equity_curve,
            dtype=float,
        )

        returns = equity.pct_change().replace(
            [np.inf, -np.inf],
            np.nan,
        ).dropna()

        strategy_return = (
            (final_capital - initial_capital)
            / initial_capital
        ) * 100.0

        max_drawdown = self._max_drawdown(equity)

        volatility = self._annualized_volatility(
            returns,
            config.periods_per_year,
        )

        downside_deviation = (
            self._downside_deviation(
                returns,
                config.periods_per_year,
            )
        )

        sharpe = self._sharpe_ratio(
            returns,
            config.periods_per_year,
        )

        sortino = self._sortino_ratio(
            returns,
            config.periods_per_year,
        )

        calmar = self._calmar_ratio(
            strategy_return,
            max_drawdown,
        )

        benchmark_return = self._benchmark_return(
            data,
        )

        win_rate = self._win_rate(trades)

        profit_factor = self._profit_factor(trades)

        average_trade_return = (
            self._average_trade_return(trades)
        )

        exposure_percent = (
            self._exposure_percent(
                exposure_periods,
                len(data),
            )
        )

        warnings = self._warnings(
            data=data,
            trades=trades,
            max_drawdown=max_drawdown,
            benchmark_return=benchmark_return,
        )

        result = BacktestResult(
            strategy=strategy.name,
            initial_capital=initial_capital,
            final_capital=final_capital,
            return_percent=strategy_return,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            trade_count=len(trades),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            volatility=volatility,
            downside_deviation=downside_deviation,
            exposure_percent=exposure_percent,
            benchmark_return_percent=benchmark_return,
            profit_factor=profit_factor,
            average_trade_return_percent=average_trade_return,
            trades=trades,
            warnings=warnings,
        )

        return result.as_dict()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_strategy(
        strategy: TradingStrategy,
    ) -> None:

        if not isinstance(
            strategy,
            TradingStrategy,
        ):
            raise TypeError(
                "strategy must be a TradingStrategy."
            )

    @staticmethod
    def _validate_capital(
        initial_capital: float,
    ) -> None:

        if not np.isfinite(initial_capital):
            raise ValueError(
                "initial_capital must be finite."
            )

        if initial_capital <= 0:
            raise ValueError(
                "initial_capital must be positive."
            )

    @classmethod
    def _prepare_history(
        cls,
        history: pd.DataFrame,
    ) -> pd.DataFrame:

        if not isinstance(
            history,
            pd.DataFrame,
        ):
            raise TypeError(
                "history must be a pandas DataFrame."
            )

        missing = [
            column
            for column in cls.REQUIRED_COLUMNS
            if column not in history.columns
        ]

        if missing:
            raise ValueError(
                "Historical data is missing required "
                f"columns: {missing}"
            )

        data = history.copy()

        data["Close"] = pd.to_numeric(
            data["Close"],
            errors="coerce",
        )

        data = data.replace(
            [np.inf, -np.inf],
            np.nan,
        )

        data = data.dropna(
            subset=["Close"]
        )

        data = data[data["Close"] > 0]

        if data.empty:
            raise ValueError(
                "Historical data contains no valid prices."
            )

        data = data.sort_index()

        return data

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------

    @staticmethod
    def _build_indicators(
        data: pd.DataFrame,
        strategy: TradingStrategy,
    ) -> pd.DataFrame:

        result = data.copy()

        result["SHORT"] = (
            result["Close"]
            .rolling(
                window=strategy.short_window,
                min_periods=strategy.short_window,
            )
            .mean()
        )

        result["LONG"] = (
            result["Close"]
            .rolling(
                window=strategy.long_window,
                min_periods=strategy.long_window,
            )
            .mean()
        )

        return result

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    @staticmethod
    def _simulate(
        data: pd.DataFrame,
        config: BacktestConfig,
    ) -> tuple[
        float,
        list[float],
        list[Trade],
        list[int],
    ]:

        cash = float(config.initial_capital)

        quantity = 0.0
        position = False

        trades: list[Trade] = []
        equity_curve: list[float] = []
        exposure_periods: list[int] = []

        active_trade: Trade | None = None

        for index, row in data.iterrows():

            price = float(row["Close"])

            short_ma = row["SHORT"]
            long_ma = row["LONG"]

            if (
                not position
                and pd.notna(short_ma)
                and pd.notna(long_ma)
                and short_ma > long_ma
            ):
                execution_price = (
                    price
                    * (1.0 + config.slippage_rate)
                )

                available_cash = cash

                commission = (
                    available_cash
                    * config.commission_rate
                )

                capital_after_cost = (
                    available_cash
                    - commission
                )

                if capital_after_cost > 0:

                    quantity = (
                        capital_after_cost
                        / execution_price
                    )

                    cash = 0.0

                    position = True

                    active_trade = Trade(
                        entry_time=index,
                        entry_price=execution_price,
                        quantity=quantity,
                        entry_cost=commission,
                    )

            elif (
                position
                and pd.notna(short_ma)
                and pd.notna(long_ma)
                and short_ma < long_ma
            ):
                execution_price = (
                    price
                    * (1.0 - config.slippage_rate)
                )

                gross_value = (
                    quantity
                    * execution_price
                )

                commission = (
                    gross_value
                    * config.commission_rate
                )

                cash = (
                    gross_value
                    - commission
                )

                if active_trade is not None:

                    active_trade.exit_time = index
                    active_trade.exit_price = (
                        execution_price
                    )
                    active_trade.exit_cost = commission

                    trades.append(
                        active_trade
                    )

                active_trade = None
                quantity = 0.0
                position = False

            portfolio_value = cash

            if position:
                portfolio_value += (
                    quantity * price
                )
                exposure_periods.append(1)
            else:
                exposure_periods.append(0)

            equity_curve.append(
                float(portfolio_value)
            )

        # Liquidate an open position at the final
        # available market price so final capital is
        # economically realized.
        if position and active_trade is not None:

            final_price = float(
                data["Close"].iloc[-1]
            )

            execution_price = (
                final_price
                * (1.0 - config.slippage_rate)
            )

            gross_value = (
                quantity
                * execution_price
            )

            commission = (
                gross_value
                * config.commission_rate
            )

            cash = (
                gross_value
                - commission
            )

            active_trade.exit_time = (
                data.index[-1]
            )

            active_trade.exit_price = (
                execution_price
            )

            active_trade.exit_cost = commission

            trades.append(
                active_trade
            )

            if equity_curve:
                equity_curve[-1] = float(cash)

        return (
            float(cash),
            equity_curve,
            trades,
            exposure_periods,
        )

    # ------------------------------------------------------------------
    # Performance metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _max_drawdown(
        equity: pd.Series,
    ) -> float:

        if equity.empty:
            return 0.0

        peak = equity.cummax()

        drawdown = (
            (equity - peak)
            / peak
        )

        return float(
            abs(drawdown.min()) * 100.0
        )

    @staticmethod
    def _annualized_volatility(
        returns: pd.Series,
        periods_per_year: int,
    ) -> float:

        if len(returns) < 2:
            return 0.0

        std = returns.std()

        if not np.isfinite(std):
            return 0.0

        return float(
            std * np.sqrt(periods_per_year) * 100.0
        )

    @staticmethod
    def _downside_deviation(
        returns: pd.Series,
        periods_per_year: int,
    ) -> float:

        if returns.empty:
            return 0.0

        downside = returns[
            returns < 0
        ]

        if downside.empty:
            return 0.0

        deviation = np.sqrt(
            np.mean(
                np.square(downside)
            )
        )

        return float(
            deviation
            * np.sqrt(periods_per_year)
            * 100.0
        )

    @staticmethod
    def _sharpe_ratio(
        returns: pd.Series,
        periods_per_year: int,
    ) -> float:

        if len(returns) < 2:
            return 0.0

        std = returns.std()

        if (
            not np.isfinite(std)
            or std <= 0
        ):
            return 0.0

        value = (
            returns.mean()
            / std
        ) * np.sqrt(periods_per_year)

        return float(
            value
            if np.isfinite(value)
            else 0.0
        )

    @staticmethod
    def _sortino_ratio(
        returns: pd.Series,
        periods_per_year: int,
    ) -> float:

        if returns.empty:
            return 0.0

        downside = returns[
            returns < 0
        ]

        if downside.empty:
            return 0.0

        deviation = np.sqrt(
            np.mean(
                np.square(downside)
            )
        )

        if deviation <= 0:
            return 0.0

        value = (
            returns.mean()
            / deviation
        ) * np.sqrt(periods_per_year)

        return float(
            value
            if np.isfinite(value)
            else 0.0
        )

    @staticmethod
    def _calmar_ratio(
        return_percent: float,
        max_drawdown: float,
    ) -> float:

        if max_drawdown <= 0:
            return 0.0

        return float(
            return_percent / max_drawdown
        )

    # ------------------------------------------------------------------
    # Trade statistics
    # ------------------------------------------------------------------

    @staticmethod
    def _win_rate(
        trades: list[Trade],
    ) -> float:

        closed = [
            trade
            for trade in trades
            if trade.is_closed
        ]

        if not closed:
            return 0.0

        wins = sum(
            trade.net_pnl > 0
            for trade in closed
        )

        return float(
            wins / len(closed) * 100.0
        )

    @staticmethod
    def _profit_factor(
        trades: list[Trade],
    ) -> float:

        closed = [
            trade
            for trade in trades
            if trade.is_closed
        ]

        profits = sum(
            max(trade.net_pnl, 0.0)
            for trade in closed
        )

        losses = sum(
            abs(min(trade.net_pnl, 0.0))
            for trade in closed
        )

        if losses <= 0:
            return (
                float(profits)
                if profits > 0
                else 0.0
            )

        return float(
            profits / losses
        )

    @staticmethod
    def _average_trade_return(
        trades: list[Trade],
    ) -> float:

        closed = [
            trade
            for trade in trades
            if trade.is_closed
            and trade.entry_price > 0
        ]

        if not closed:
            return 0.0

        returns = [
            (
                trade.net_pnl
                /
                (
                    trade.entry_price
                    * trade.quantity
                )
            )
            * 100.0
            for trade in closed
        ]

        return float(
            np.mean(returns)
        )

    @staticmethod
    def _exposure_percent(
        exposure_periods: list[int],
        total_periods: int,
    ) -> float:

        if total_periods <= 0:
            return 0.0

        return float(
            sum(exposure_periods)
            / total_periods
            * 100.0
        )

    # ------------------------------------------------------------------
    # Benchmark
    # ------------------------------------------------------------------

    @staticmethod
    def _benchmark_return(
        data: pd.DataFrame,
    ) -> float:

        if len(data) < 2:
            return 0.0

        first = float(
            data["Close"].iloc[0]
        )

        last = float(
            data["Close"].iloc[-1]
        )

        if first <= 0:
            return 0.0

        return float(
            (
                (last - first)
                / first
            )
            * 100.0
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @staticmethod
    def _warnings(
        data: pd.DataFrame,
        trades: list[Trade],
        max_drawdown: float,
        benchmark_return: float,
    ) -> list[str]:

        warnings: list[str] = []

        if len(data) < 50:
            warnings.append(
                "Limited historical sample size."
            )

        if not trades:
            warnings.append(
                "Strategy generated no completed trades."
            )

        if max_drawdown >= 30:
            warnings.append(
                "High historical drawdown."
            )

        if benchmark_return > 0:
            strategy_return = (
                trades[-1].net_pnl
                if trades
                else 0.0
            )

            if strategy_return < 0:
                warnings.append(
                    "Strategy experienced losing trades "
                    "while the benchmark was positive."
                )

        return warnings