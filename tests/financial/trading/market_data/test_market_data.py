from datetime import UTC, datetime, timedelta
import math

import pytest

from ultron.financial.trading.market.models import (
    Instrument,
    MarketType,
)
from ultron.financial.trading.market_data.models import (
    Candle,
    MarketSnapshot,
)
from ultron.financial.trading.market_data.series import (
    MarketSeries,
)
from ultron.financial.trading.market_data.validator import (
    MarketDataValidator,
)


def make_candle(
    timestamp=None,
    symbol="TEST",
    open_price=100.0,
    high=105.0,
    low=95.0,
    close=103.0,
    volume=1000.0,
):
    return Candle(
        symbol=symbol,
        timestamp=timestamp or datetime.now(UTC),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_candle_normalizes_symbol():
    candle = make_candle(symbol=" test ")

    assert candle.symbol == "TEST"


def test_candle_rejects_invalid_ohlc():
    with pytest.raises(ValueError):
        make_candle(
            open_price=100.0,
            high=90.0,
            low=95.0,
            close=103.0,
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("open_price", math.nan),
        ("high", math.inf),
        ("low", -math.inf),
        ("close", math.nan),
        ("volume", math.inf),
    ],
)
def test_candle_rejects_non_finite_market_values(field, invalid_value):
    with pytest.raises(ValueError, match="finite"):
        make_candle(**{field: invalid_value})


def test_market_snapshot_now():
    snapshot = MarketSnapshot.now(
        symbol=" test ",
        price=100.0,
    )

    assert snapshot.symbol == "TEST"
    assert snapshot.price == 100.0
    assert snapshot.timestamp.tzinfo is not None


def test_market_series_properties():
    now = datetime.now(UTC)

    candles = [
        make_candle(
            timestamp=now,
            open_price=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000.0,
        ),
        make_candle(
            timestamp=now + timedelta(minutes=1),
            open_price=102.0,
            high=108.0,
            low=101.0,
            close=106.0,
            volume=1200.0,
        ),
    ]

    series = MarketSeries(candles)

    assert series.symbol == "TEST"
    assert series.closes == [102.0, 106.0]
    assert series.highs == [105.0, 108.0]
    assert series.lows == [95.0, 101.0]
    assert series.volumes == [1000.0, 1200.0]
    assert series.latest.close == 106.0
    assert series.returns() == pytest.approx(
        [(106.0 / 102.0) - 1.0]
    )
    assert series.percentage_change() == pytest.approx(
        ((106.0 - 102.0) / 102.0) * 100.0
    )


def test_validator_accepts_fresh_candle():
    now = datetime.now(UTC)

    candle = make_candle(
        timestamp=now - timedelta(seconds=5)
    )

    validator = MarketDataValidator(
        max_age_seconds=60.0
    )

    result = validator.validate_candle(
        candle,
        now=now,
    )

    assert result["valid"] is True


def test_validator_rejects_stale_candle():
    now = datetime.now(UTC)

    candle = make_candle(
        timestamp=now - timedelta(seconds=61)
    )

    validator = MarketDataValidator(
        max_age_seconds=60.0
    )

    result = validator.validate_candle(
        candle,
        now=now,
    )

    assert result["valid"] is False
    assert "stale" in result["reason"].lower()


def test_validator_rejects_incomplete_candle():
    now = datetime.now(UTC)
    candle = Candle(
        symbol="TEST",
        timestamp=now,
        open=100.0,
        high=105.0,
        low=95.0,
        close=103.0,
        volume=1000.0,
        is_complete=False,
    )

    candle_result = MarketDataValidator().validate_candle(candle, now=now)
    series_result = MarketDataValidator().validate_series([candle])

    assert candle_result["valid"] is False
    assert candle_result["reason"] == "Market candle is incomplete."
    assert series_result["valid"] is False
    assert "incomplete candle" in series_result["reason"]


def test_validator_rejects_future_candle():
    now = datetime.now(UTC)

    candle = make_candle(
        timestamp=now + timedelta(seconds=5)
    )

    validator = MarketDataValidator()

    result = validator.validate_candle(
        candle,
        now=now,
    )

    assert result["valid"] is False
    assert "future" in result["reason"].lower()


def test_validator_rejects_multiple_symbols():
    now = datetime.now(UTC)

    candles = [
        make_candle(
            timestamp=now,
            symbol="TEST",
        ),
        make_candle(
            timestamp=now + timedelta(minutes=1),
            symbol="OTHER",
        ),
    ]

    result = MarketDataValidator().validate_series(
        candles
    )

    assert result["valid"] is False
    assert "multiple symbols" in result["reason"].lower()


def test_validator_rejects_unsorted_series():
    now = datetime.now(UTC)

    candles = [
        make_candle(
            timestamp=now + timedelta(minutes=1)
        ),
        make_candle(
            timestamp=now
        ),
    ]

    result = MarketDataValidator().validate_series(
        candles
    )

    assert result["valid"] is False
    assert "chronologically" in result["reason"].lower()


def test_validator_rejects_duplicate_timestamps():
    now = datetime.now(UTC)

    candles = [
        make_candle(timestamp=now),
        make_candle(timestamp=now),
    ]

    result = MarketDataValidator().validate_series(
        candles
    )

    assert result["valid"] is False
    assert "duplicate" in result["reason"].lower()


def test_validator_accepts_valid_series():
    now = datetime.now(UTC)

    candles = [
        make_candle(
            timestamp=now,
            close=102.0,
        ),
        make_candle(
            timestamp=now + timedelta(minutes=1),
            open_price=102.0,
            high=108.0,
            low=101.0,
            close=106.0,
        ),
    ]

    result = MarketDataValidator().validate_series(
        candles
    )

    assert result["valid"] is True
    assert result["symbol"] == "TEST"
    assert result["count"] == 2


def test_instrument_validation():
    instrument = Instrument(
        symbol="TEST",
        market=MarketType.STOCK,
        base_currency="USD",
        quote_currency="USD",
    )

    assert instrument.symbol == "TEST"
    assert instrument.market == MarketType.STOCK


def test_instrument_rejects_invalid_quantity_step():
    with pytest.raises(ValueError):
        Instrument(
            symbol="TEST",
            market=MarketType.STOCK,
            base_currency="USD",
            quote_currency="USD",
            quantity_step=0,
        )
