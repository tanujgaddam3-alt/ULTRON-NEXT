from __future__ import annotations

from typing import Any

from .models import Portfolio, Trade, OrderSide


class PaperPerformanceTracker:
    """
    Calculates performance statistics from a paper-trading portfolio.

    This class does not execute trades or modify portfolio state.
    """

    def analyze(
        self,
        portfolio: Portfolio,
        prices: dict[str, float],
    ) -> dict[str, Any]:

        if not isinstance(portfolio, Portfolio):
            raise TypeError("portfolio must be a Portfolio.")

        if not isinstance(prices, dict):
            raise TypeError("prices must be a dictionary.")

        equity = portfolio.equity(prices)
        initial = portfolio.initial_capital

        net_pnl = equity - initial

        return_percent = (
            (net_pnl / initial) * 100
            if initial > 0
            else 0.0
        )

        trades = list(portfolio.trades)

        sell_trades = [
            trade
            for trade in trades
            if trade.side == OrderSide.SELL
        ]

        winning = [
            trade
            for trade in sell_trades
            if trade.realized_pnl > 0
        ]

        losing = [
            trade
            for trade in sell_trades
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

        closed_trades = len(sell_trades)

        win_rate = (
            len(winning) / closed_trades * 100
            if closed_trades
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

        average_trade = (
            net_pnl / closed_trades
            if closed_trades
            else 0.0
        )

        return {
            "initial_capital": float(initial),
            "current_equity": float(equity),
            "net_pnl": float(net_pnl),
            "return_percent": float(return_percent),
            "total_orders": len(trades),
            "closed_trades": closed_trades,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": float(win_rate),
            "gross_profit": float(gross_profit),
            "gross_loss": float(gross_loss),
            "profit_factor": profit_factor,
            "average_trade_pnl": float(average_trade),
            "realized_pnl": float(
                portfolio.realized_pnl
            ),
            "open_positions": len(
                portfolio.positions
            ),
        }
