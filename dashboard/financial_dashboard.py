from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from ultron.infrastructure.container import Container, container as application_container


st.set_page_config(
    page_title="ULTRON Financial Intelligence",
    page_icon="◉",
    layout="wide",
)


@st.cache_resource
def get_container() -> Container:
    return application_container


def safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_money(value: Any) -> str:
    return f"${safe_number(value):,.2f}"


def get_alpaca_account() -> dict[str, Any]:
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")

    if not key or not secret:
        return {
            "connected": False,
            "reason": "Alpaca credentials are not loaded.",
        }

    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(key, secret, paper=True)
        account = client.get_account()
        positions = client.get_all_positions()
        orders = client.get_orders()

        return {
            "connected": True,
            "status": str(account.status),
            "equity": safe_number(account.equity),
            "buying_power": safe_number(account.buying_power),
            "positions": positions,
            "orders": orders,
            "lastUpdatedAt": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        return {
            "connected": False,
            "reason": str(exc),
        }


def extract_analysis(result: dict[str, Any]) -> dict[str, Any]:
    current = result.get("current", result)

    financial = current.get(
        "analysis",
        current.get("financial_analysis", current.get("financial", {})),
    )
    market = current.get("market", {})
    regime = current.get("regime_context", current.get("regime", {}))
    risk = current.get("risk", {})
    decision = result.get("decision", current.get("decision", {}))
    reasoning = result.get("reasoning", {})

    return {
        "symbol": result.get("symbol", current.get("symbol", "")),
        "price": market.get("current_price"),
        "financial": financial,
        "market": market,
        "regime": regime,
        "risk": risk,
        "decision": decision,
        "reasoning": reasoning,
        "strategies": current.get("strategies", []),
        "backtest": result.get("backtest", current.get("backtest")),
        "success": result.get("success", False),
    }


def render_analysis(data: dict[str, Any]) -> None:
    financial = data["financial"]
    market = data["market"]
    regime = data["regime"]
    risk = data["risk"]
    decision = data["decision"]

    st.subheader(f"◉ {data['symbol']} Intelligence")
    st.caption(
        f"Analysis received locally at {data.get('receivedAt', 'unknown')}; "
        "provider-source freshness is not reported."
    )

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric("Price", fmt_money(data["price"]))
    c2.metric("Financial Score", financial.get("score", "N/A"))
    c3.metric("Rating", financial.get("rating", "N/A").upper())
    c4.metric("Risk", risk.get("risk_level", "N/A").upper())
    c5.metric(
        "Confidence",
        f"{safe_number(decision.get('confidence', 0)) * 100:.1f}%",
    )

    st.divider()

    left, right = st.columns(2)

    with left:
        st.markdown("### Market")
        st.write(
            {
                "Trend": market.get("trend"),
                "Volatility": market.get("volatility"),
                "SMA 20": market.get("sma_20"),
                "SMA 50": market.get("sma_50"),
                "Regime": regime.get("regime"),
                "Regime confidence": regime.get("confidence"),
            }
        )

    with right:
        st.markdown("### ULTRON Decision")

        action = (
            decision.get("decision")
            or decision.get("action")
            or "UNKNOWN"
        )

        st.markdown(f"# **{str(action).upper()}**")

        summary = (
            decision.get("summary")
            or decision.get("reasoning")
            or data["reasoning"].get("summary")
            or "No reasoning summary available."
        )

        st.write(summary)

    strengths = (
        decision.get("strengths")
        or data["reasoning"].get("strengths")
        or financial.get("reasons", [])
    )

    risks = (
        decision.get("risks")
        or data["reasoning"].get("risks")
        or risk.get("warnings", [])
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Strengths")
        for item in strengths:
            st.success(str(item))

    with col2:
        st.markdown("### Risks")
        for item in risks:
            st.warning(str(item))

    if data["strategies"]:
        st.markdown("### Strategies")
        st.write(", ".join(map(str, data["strategies"])))


def render_backtest(backtest: Any) -> None:
    st.subheader("Historical Validation")

    if not backtest:
        st.info("Backtest was not requested.")
        return

    if not isinstance(backtest, dict):
        st.warning("Backtest returned an unexpected result.")
        return

    best = backtest.get("evolved_best") or backtest.get("best")

    if not isinstance(best, dict):
        st.warning("No best strategy was returned.")
        return

    performance = best.get("performance", {})

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Best Strategy",
        best.get("strategy", "N/A"),
    )
    c2.metric(
        "Return",
        f"{safe_number(performance.get('return_percent')):.2f}%",
    )
    c3.metric(
        "Max Drawdown",
        f"{safe_number(performance.get('max_drawdown')):.2f}%",
    )
    c4.metric(
        "Sharpe",
        f"{safe_number(performance.get('sharpe_ratio')):.2f}",
    )

    validation = backtest.get("validation", {})

    if validation:
        st.markdown("#### Validation")

        st.write(
            {
                "Validation return":
                    validation.get("validation_return_percent"),
                "Benchmark return":
                    validation.get("benchmark_return_percent"),
                "Outperformed benchmark":
                    validation.get("outperformed_benchmark"),
                "Robust":
                    validation.get("robust"),
                "Warnings":
                    validation.get("warnings", []),
            }
        )


def render_alpaca() -> None:
    st.subheader("Alpaca Paper Account")

    account = get_alpaca_account()

    if not account["connected"]:
        st.error(f"Alpaca unavailable: {account['reason']}")
        return

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Status", account["status"])
    c2.metric("Equity", fmt_money(account["equity"]))
    c3.metric("Buying Power", fmt_money(account["buying_power"]))
    c4.metric("Positions", len(account["positions"]))

    st.caption(
        "PAPER MODE ONLY. No live-money execution is exposed by this dashboard. "
        f"Last successful account query: {account['lastUpdatedAt']}."
    )

    if account["positions"]:
        st.markdown("### Positions")

        rows = []

        for position in account["positions"]:
            rows.append(
                {
                    "Symbol": position.symbol,
                    "Qty": position.qty,
                    "Avg Entry": position.avg_entry_price,
                    "Market Value": position.market_value,
                    "Unrealized P/L": position.unrealized_pl,
                }
            )

        st.dataframe(rows, use_container_width=True)

    if account["orders"]:
        st.markdown("### Recent Orders")

        rows = []

        for order in account["orders"][:20]:
            rows.append(
                {
                    "Symbol": order.symbol,
                    "Side": str(order.side),
                    "Qty": order.qty,
                    "Status": str(order.status),
                    "Type": str(order.type),
                }
            )

        st.dataframe(rows, use_container_width=True)


def main() -> None:
    st.title("ULTRON")
    st.caption("Financial Intelligence + Paper Trading Control Center")

    with st.sidebar:
        st.header("Analysis")

        symbol = st.text_input(
            "Symbol",
            value="AAPL",
            max_chars=20,
        ).strip().upper()

        run_backtest = st.checkbox(
            "Run historical backtest",
            value=False,
        )

        analyze = st.button(
            "ANALYZE",
            type="primary",
            use_container_width=True,
        )

        st.divider()

        st.caption("Execution Mode")
        st.success("PAPER ONLY")

    render_alpaca()

    if analyze:
        if not symbol:
            st.error("Enter a market symbol.")
            return

        container = get_container()

        with st.spinner(f"ULTRON analyzing {symbol}..."):
            try:
                result = container.financial_agent.analyze(
                    symbol=symbol,
                    run_backtest=run_backtest,
                )

                if not isinstance(result, dict):
                    st.error("FinancialAgent returned an invalid result.")
                    return

                if not result.get("success", False):
                    st.error(
                        result.get(
                            "error",
                            result.get("reason", "Analysis failed."),
                        )
                    )
                    return

                data = extract_analysis(result)
                data["receivedAt"] = datetime.now(timezone.utc).isoformat()

                st.session_state["last_analysis"] = result

                render_analysis(data)

                if run_backtest:
                    render_backtest(data["backtest"])

            except Exception as exc:
                st.exception(exc)

    st.divider()

    st.subheader("Paper Execution")

    analysis = st.session_state.get("last_analysis")

    if analysis:
        symbol = analysis.get("symbol", "")

        st.info(
            f"Analysis loaded for {symbol}. "
            "Execution remains behind ULTRON's existing paper-trading controller."
        )

        execute = st.button(
            "EXECUTE ULTRON PAPER TRADE",
            use_container_width=True,
        )

        if execute:
            try:
                market = analysis.get("market", {})
                price = market.get("current_price")

                if not price:
                    st.error("No valid current price is available.")
                    return

                result = get_container().financial_agent.paper_trade(
                    analysis=analysis,
                    price=float(price),
                )

                if result.get("success"):
                    st.success("ULTRON paper trade executed.")
                    st.json(result)
                else:
                    st.error(
                        result.get(
                            "reason",
                            "Paper trade was rejected.",
                        )
                    )
                    st.json(result)

            except Exception as exc:
                st.exception(exc)

    else:
        st.info("Run an analysis first.")


if __name__ == "__main__":
    main()
