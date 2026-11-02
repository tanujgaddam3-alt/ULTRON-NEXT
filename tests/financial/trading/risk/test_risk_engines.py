import math

import pytest

from ultron.financial.trading.risk.position_sizer import (
    PositionSizer,
)
from ultron.financial.trading.risk.dynamic_exits import (
    DynamicExitConfig,
    DynamicExitEngine,
)
from ultron.financial.trading.risk.correlation import (
    CorrelationEngine,
    CorrelationLimits,
)
from ultron.financial.trading.risk.portfolio_risk import (
    PortfolioRiskEngine,
    PortfolioRiskLimits,
)


# ---------------------------------------------------------
# Position Sizer
# ---------------------------------------------------------


def test_position_sizer_calculates_quantity():
    sizer = PositionSizer(
        risk_per_trade_percent=1.0
    )

    result = sizer.calculate(
        equity=100_000.0,
        entry_price=100.0,
        stop_loss=96.0,
    )

    assert result["approved"] is True
    assert result["quantity"] == pytest.approx(250.0)
    assert result["risk_capital"] == pytest.approx(1000.0)
    assert result["risk_per_unit"] == pytest.approx(4.0)


def test_position_sizer_rejects_invalid_stop():
    sizer = PositionSizer()

    result = sizer.calculate(
        equity=100_000.0,
        entry_price=100.0,
        stop_loss=100.0,
    )

    assert result["approved"] is False
    assert result["quantity"] == 0.0


def test_position_sizer_rejects_invalid_equity():
    sizer = PositionSizer()

    result = sizer.calculate(
        equity=0.0,
        entry_price=100.0,
        stop_loss=96.0,
    )

    assert result["approved"] is False


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf])
def test_position_sizer_rejects_non_finite_equity(invalid_value):
    result = PositionSizer().calculate(
        equity=invalid_value,
        entry_price=100.0,
        stop_loss=96.0,
    )

    assert result["approved"] is False
    assert result["quantity"] == 0.0


# ---------------------------------------------------------
# Dynamic Exits
# ---------------------------------------------------------


def test_dynamic_exit_long():
    engine = DynamicExitEngine()

    result = engine.calculate(
        entry_price=100.0,
        atr=2.0,
        direction="long",
    )

    assert result["approved"] is True
    assert result["stop_loss"] == pytest.approx(96.0)
    assert result["take_profit"] == pytest.approx(108.0)
    assert result["stop_distance"] == pytest.approx(4.0)


def test_dynamic_exit_short():
    engine = DynamicExitEngine()

    result = engine.calculate(
        entry_price=100.0,
        atr=2.0,
        direction="short",
    )

    assert result["approved"] is True
    assert result["stop_loss"] == pytest.approx(104.0)
    assert result["take_profit"] == pytest.approx(92.0)


def test_dynamic_exit_rejects_invalid_direction():
    engine = DynamicExitEngine()

    result = engine.calculate(
        entry_price=100.0,
        atr=2.0,
        direction="sideways",
    )

    assert result["approved"] is False


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf])
def test_dynamic_exit_rejects_non_finite_inputs(invalid_value):
    result = DynamicExitEngine().calculate(
        entry_price=invalid_value,
        atr=2.0,
        direction="long",
    )

    assert result["approved"] is False


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf])
def test_dynamic_exit_config_rejects_non_finite_limits(invalid_value):
    with pytest.raises(ValueError):
        DynamicExitConfig(atr_multiplier=invalid_value)


def test_dynamic_exit_respects_max_stop():
    engine = DynamicExitEngine(
        DynamicExitConfig(
            atr_multiplier=10.0,
            max_stop_percent=5.0,
        )
    )

    result = engine.calculate(
        entry_price=100.0,
        atr=10.0,
        direction="long",
    )

    assert result["approved"] is True
    assert result["stop_distance"] == pytest.approx(5.0)
    assert result["stop_loss"] == pytest.approx(95.0)


# ---------------------------------------------------------
# Correlation
# ---------------------------------------------------------


def test_correlation_engine_allows_low_exposure():
    engine = CorrelationEngine(
        CorrelationLimits(
            max_correlated_exposure_percent=40.0
        )
    )

    result = engine.calculate(
        equity=100_000.0,
        proposed_symbol="TEST",
        proposed_value=10_000.0,
        positions={
            "AAPL": 10_000.0,
        },
        correlations={
            "TEST": {
                "AAPL": 0.2,
            }
        },
    )

    assert result["approved"] is True
    assert result["correlated_exposure"] == pytest.approx(
        12_000.0
    )


def test_correlation_engine_blocks_high_exposure():
    engine = CorrelationEngine(
        CorrelationLimits(
            max_correlated_exposure_percent=40.0
        )
    )

    result = engine.calculate(
        equity=100_000.0,
        proposed_symbol="TEST",
        proposed_value=30_000.0,
        positions={
            "AAPL": 20_000.0,
        },
        correlations={
            "TEST": {
                "AAPL": 1.0,
            }
        },
    )

    assert result["approved"] is False
    assert "exceeded" in result["reason"].lower()


def test_correlation_rejects_invalid_matrix():
    engine = CorrelationEngine()

    with pytest.raises(ValueError):
        engine.calculate(
            equity=100_000.0,
            proposed_symbol="TEST",
            proposed_value=10_000.0,
            positions={},
            correlations={
                "TEST": {
                    "AAPL": 1.5,
                }
            },
        )


# ---------------------------------------------------------
# Portfolio Risk
# ---------------------------------------------------------


def test_portfolio_risk_allows_safe_trade():
    engine = PortfolioRiskEngine()

    result = engine.evaluate(
        equity=100_000.0,
        positions={},
        proposed_symbol="TEST",
        proposed_value=20_000.0,
    )

    assert result["approved"] is True


def test_portfolio_risk_blocks_single_position_limit():
    engine = PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_single_position_percent=25.0
        )
    )

    result = engine.evaluate(
        equity=100_000.0,
        positions={},
        proposed_symbol="TEST",
        proposed_value=30_000.0,
    )

    assert result["approved"] is False
    assert "single-position" in result["reason"].lower()


def test_portfolio_risk_blocks_total_exposure():
    engine = PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_total_exposure_percent=60.0
        )
    )

    result = engine.evaluate(
        equity=100_000.0,
        positions={
            "AAPL": {
                "quantity": 400,
                "price": 100,
            }
        },
        proposed_symbol="TEST",
        proposed_value=25_000.0,
    )

    assert result["approved"] is False
    assert "total portfolio exposure" in (
        result["reason"].lower()
    )


def test_portfolio_risk_blocks_currency_exposure():
    engine = PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_currency_exposure_percent=50.0
        )
    )

    result = engine.evaluate(
        equity=100_000.0,
        positions={
            "AAPL": {
                "quantity": 400,
                "price": 100,
                "currency": "USD",
            }
        },
        proposed_symbol="TEST",
        proposed_value=20_000.0,
        proposed_currency="USD",
    )

    assert result["approved"] is False
    assert "currency exposure" in (
        result["reason"].lower()
    )


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf])
def test_portfolio_risk_rejects_non_finite_exposure(invalid_value):
    result = PortfolioRiskEngine().evaluate(
        equity=100_000.0,
        positions={"AAPL": invalid_value},
        proposed_symbol="TEST",
        proposed_value=10_000.0,
    )

    assert result["approved"] is False
    assert "finite" in result["reason"].lower()
