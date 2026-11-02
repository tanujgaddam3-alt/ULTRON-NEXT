from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
from ultron.financial.trading.market_data.alpaca_adapter import AlpacaMarketAdapter, AlpacaMarketDataError

def fake_adapter(timestamp, price=200.0):
    obj = object.__new__(AlpacaMarketAdapter)
    obj.max_quote_age_seconds = 30.0
    obj.feed = None
    obj._trade_request = lambda **kwargs: kwargs
    obj._data_feed_enum = SimpleNamespace()
    obj._data_client = SimpleNamespace(get_stock_latest_trade=lambda request: {
        'AAPL': SimpleNamespace(timestamp=timestamp, price=price)})
    return obj

def _session(day, open_at, close_at):
    return SimpleNamespace(date=day, open=open_at, close=close_at)


def calendar_adapter(
    timestamp,
    *,
    price=200.0,
    now=datetime(2026, 10, 16, 15, 0, tzinfo=UTC),
    clock_timestamp=None,
    is_open=True,
    sessions=(),
    trade_available=True,
    clock_error=None,
    calendar_error=None,
    feed="IEX",
):
    obj = object.__new__(AlpacaMarketAdapter)
    obj.max_quote_age_seconds = 30.0
    obj.feed = feed
    obj._trade_request = lambda **kwargs: kwargs
    obj._data_feed_enum = lambda value: value
    obj._calendar_request = lambda **kwargs: kwargs
    obj._now = lambda: now
    requests = []

    def get_latest_trade(request):
        requests.append(request)
        if not trade_available:
            return {}
        return {"AAPL": SimpleNamespace(timestamp=timestamp, price=price)}

    clock_timestamp = clock_timestamp or now

    def get_clock():
        if clock_error:
            raise clock_error
        return SimpleNamespace(timestamp=clock_timestamp, is_open=is_open)

    def get_calendar(request):
        if calendar_error:
            raise calendar_error
        return [item for item in sessions if request["start"] <= item.date <= request["end"]]

    obj._data_client = SimpleNamespace(get_stock_latest_trade=get_latest_trade)
    obj._trading_client = SimpleNamespace(get_clock=get_clock, get_calendar=get_calendar)
    obj.requested_trades = requests
    return obj


OCTOBER_SESSION = _session(
    datetime(2026, 10, 16).date(),
    datetime(2026, 10, 16, 13, 30, tzinfo=UTC),
    datetime(2026, 10, 16, 20, 0, tzinfo=UTC),
)

def test_rejects_indian_equity_suffix():
    with pytest.raises(AlpacaMarketDataError, match='not a supported'):
        AlpacaMarketAdapter._validate_symbol('RELIANCE.NS')

def test_rejects_empty_symbol():
    with pytest.raises(ValueError, match='Symbol is required'):
        AlpacaMarketAdapter._validate_symbol(' ')

def test_rejects_stale_trade():
    with pytest.raises(AlpacaMarketDataError, match='stale'):
        fake_adapter(datetime.now(UTC)-timedelta(minutes=5)).get_snapshot('AAPL')

def test_preserves_source_timestamp():
    timestamp = datetime.now(UTC)-timedelta(seconds=2)
    result = fake_adapter(timestamp).get_snapshot('AAPL')
    assert result['timestamp'] == timestamp
    assert result['provider'] == 'alpaca'
    assert result['price'] == 200.0


def test_fresh_trade_during_open_session_has_provenance_and_is_current():
    now = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    adapter = calendar_adapter(
        now - timedelta(seconds=5), now=now, sessions=[OCTOBER_SESSION]
    )

    report = adapter.get_latest_trade_report("AAPL")
    snapshot = adapter.get_snapshot("AAPL")

    assert report["provider_request_status"] == "succeeded"
    assert report["trade_status"] == "fresh"
    assert report["current_quote_permitted"] is True
    assert report["price"] == 200.0
    assert report["feed"] == "IEX"
    assert report["age_seconds"] == 5.0
    assert report["trade_timestamp"] == now - timedelta(seconds=5)
    assert report["retrieved_at"] == now
    assert snapshot["price"] == 200.0
    assert snapshot["currency"] == "USD"
    assert snapshot["trade_status"] == "fresh"
    assert snapshot["freshness"] == "fresh"
    assert adapter.get_price("AAPL") == 200.0
    assert adapter.requested_trades[-1]["feed"] == "iex"


def test_stale_open_session_trade_is_not_a_current_price():
    now = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    adapter = calendar_adapter(
        now - timedelta(seconds=120), now=now, sessions=[OCTOBER_SESSION]
    )

    report = adapter.get_latest_trade_report("AAPL")
    assert report["trade_status"] == "stale"
    assert report["age_seconds"] == 120.0
    assert report["freshness_threshold_seconds"] == 30.0
    assert report["current_quote_permitted"] is False
    assert report["price"] is None
    assert report["last_known_trade"]["status"] == "stale"
    with pytest.raises(AlpacaMarketDataError, match="threshold 30.0s"):
        adapter.get_snapshot("AAPL")


def test_stale_closed_session_trade_is_separate_previous_session_data():
    saturday = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
    friday_trade = datetime(2026, 10, 16, 19, 59, tzinfo=UTC)
    adapter = calendar_adapter(
        friday_trade, now=saturday, clock_timestamp=saturday, is_open=False,
        sessions=[OCTOBER_SESSION],
    )

    report = adapter.get_latest_trade_report("AAPL")
    assert report["market_status"]["status"] == "closed"
    assert report["trade_status"] == "stale"
    assert report["current_quote_permitted"] is False
    assert report["price"] is None
    assert report["last_known_trade"]["session_classification"] == "previous_session"
    with pytest.raises(AlpacaMarketDataError, match="stale"):
        adapter.get_snapshot("AAPL")


def test_no_trade_is_unavailable_and_does_not_fall_back_to_history():
    adapter = calendar_adapter(
        datetime(2026, 10, 16, 14, 59, tzinfo=UTC),
        sessions=[OCTOBER_SESSION], trade_available=False,
    )
    adapter.get_history = lambda *args, **kwargs: pytest.fail("history fallback attempted")

    report = adapter.get_latest_trade_report("AAPL")
    assert report["provider_request_status"] == "succeeded"
    assert report["trade_available"] is False
    assert report["trade_status"] == "unavailable"
    assert report["current_quote_permitted"] is False
    with pytest.raises(AlpacaMarketDataError, match="no latest trade"):
        adapter.get_snapshot("AAPL")


@pytest.mark.parametrize(
    ("timestamp", "price", "message"),
    [
        (datetime(2026, 10, 16, 15, 1, tzinfo=UTC), 200.0, "future-dated"),
        (datetime(2026, 10, 16, 14, 59, tzinfo=UTC), 0.0, "non-finite or non-positive"),
        (datetime(2026, 10, 16, 14, 59, tzinfo=UTC), -1.0, "non-finite or non-positive"),
        (datetime(2026, 10, 16, 14, 59, tzinfo=UTC), float("nan"), "non-finite or non-positive"),
        (datetime(2026, 10, 16, 14, 59, tzinfo=UTC), float("inf"), "non-finite or non-positive"),
    ],
)
def test_rejects_future_trade_and_invalid_prices(timestamp, price, message):
    adapter = calendar_adapter(timestamp, price=price)
    with pytest.raises(AlpacaMarketDataError, match=message):
        adapter.get_snapshot("AAPL")


def test_rejects_timezone_naive_trade_timestamp():
    adapter = calendar_adapter(datetime(2026, 10, 16, 14, 59))
    with pytest.raises(AlpacaMarketDataError, match="timezone-naive"):
        adapter.get_snapshot("AAPL")


def test_clock_failure_and_unknown_market_state_fail_closed():
    now = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    failed_clock = calendar_adapter(
        now - timedelta(seconds=1), now=now, sessions=[OCTOBER_SESSION],
        clock_error=TimeoutError("private network detail"),
    )
    report = failed_clock.get_latest_trade_report("AAPL")
    assert report["market_status"]["status"] == "unknown"
    assert report["current_quote_permitted"] is False
    assert "private network detail" not in str(report)
    with pytest.raises(AlpacaMarketDataError, match="market clock request failed"):
        failed_clock.is_market_open("AAPL")

    inconsistent_clock = calendar_adapter(
        now - timedelta(seconds=1), now=now, sessions=[OCTOBER_SESSION],
        clock_timestamp=now, is_open=False,
    )
    assert inconsistent_clock.get_market_session_status()["status"] == "unknown"


def test_calendar_failure_produces_unknown_session():
    adapter = calendar_adapter(
        datetime(2026, 10, 16, 14, 59, tzinfo=UTC),
        sessions=[OCTOBER_SESSION], calendar_error=ConnectionError("private detail"),
    )

    status = adapter.get_market_session_status()

    assert status["status"] == "unknown"
    assert status["error_category"] == "network"
    assert "private detail" not in str(status)


@pytest.mark.parametrize(
    "day",
    [datetime(2026, 10, 17).date(), datetime(2026, 1, 1).date()],
    ids=["weekend", "exchange-holiday"],
)
def test_calendar_classifies_weekends_and_holidays_as_closed(day):
    now = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=16)
    adapter = calendar_adapter(
        now - timedelta(seconds=1), now=now, clock_timestamp=now,
        is_open=False, sessions=[],
    )

    status = adapter.get_market_session_status()
    assert status["status"] == "closed"
    assert status["calendar_verified"] is True
    assert status["reason"] == "no_regular_session"


def test_calendar_early_close_boundary_is_authoritative():
    day = datetime(2026, 11, 27).date()
    early_close = _session(
        day,
        datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
    )
    during = datetime(2026, 11, 27, 17, 30, tzinfo=UTC)
    after = datetime(2026, 11, 27, 18, 1, tzinfo=UTC)
    open_adapter = calendar_adapter(
        during - timedelta(seconds=1), now=during, clock_timestamp=during,
        is_open=True, sessions=[early_close],
    )
    closed_adapter = calendar_adapter(
        after - timedelta(seconds=1), now=after, clock_timestamp=after,
        is_open=False, sessions=[early_close],
    )

    assert open_adapter.get_market_session_status()["status"] == "open"
    assert closed_adapter.get_market_session_status()["status"] == "closed"


def test_naive_calendar_boundaries_are_new_york_local_times():
    day = datetime(2026, 10, 16).date()
    local_session = _session(
        day, datetime(2026, 10, 16, 9, 30), datetime(2026, 10, 16, 16, 0)
    )
    now = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    adapter = calendar_adapter(
        now - timedelta(seconds=5), now=now, sessions=[local_session]
    )

    status = adapter.get_market_session_status()
    report = adapter.get_latest_trade_report("AAPL")

    assert status["status"] == "open"
    assert status["session_open"] == datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
    assert status["session_close"] == datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    assert report["current_quote_permitted"] is True


def test_naive_calendar_boundaries_classify_last_known_session():
    day = datetime(2026, 10, 16).date()
    local_session = _session(
        day, datetime(2026, 10, 16, 9, 30), datetime(2026, 10, 16, 16, 0)
    )
    now = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
    trade_timestamp = datetime(2026, 10, 16, 19, 59, tzinfo=UTC)
    adapter = calendar_adapter(
        trade_timestamp,
        now=now,
        clock_timestamp=now,
        is_open=False,
        sessions=[local_session],
    )

    report = adapter.get_latest_trade_report("AAPL")

    assert report["market_status"]["status"] == "closed"
    assert report["last_known_trade"]["session_classification"] == "previous_session"


def test_iex_feed_is_preserved_without_provider_fallback():
    adapter = calendar_adapter(datetime(2026, 10, 16, 14, 59, tzinfo=UTC))

    adapter.get_latest_trade_report("AAPL")

    assert len(adapter.requested_trades) == 1
    assert adapter.requested_trades[0]["feed"] == "iex"
    assert adapter._feed_name() == "IEX"


def test_provider_error_is_sanitized_and_never_retries_another_feed():
    adapter = calendar_adapter(datetime(2026, 10, 16, 14, 59, tzinfo=UTC))

    def fail(request):
        adapter.requested_trades.append(request)
        raise RuntimeError("secret=do-not-report; provider denied feed")

    adapter._data_client.get_stock_latest_trade = fail
    with pytest.raises(AlpacaMarketDataError) as caught:
        adapter.get_snapshot("AAPL")

    assert caught.value.category == "provider_error"
    assert "do-not-report" not in str(caught.value)
    assert len(adapter.requested_trades) == 1
    assert adapter.requested_trades[0]["feed"] == "iex"


def test_market_data_service_never_returns_stale_alpaca_price_as_current():
    from ultron.financial.trading.market_data.service import MarketDataService

    now = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    adapter = calendar_adapter(
        now - timedelta(seconds=120), now=now, sessions=[OCTOBER_SESSION]
    )

    with pytest.raises(AlpacaMarketDataError, match="stale"):
        MarketDataService(adapter).get_current_price("AAPL")

def test_rejects_future_timestamp():
    with pytest.raises(AlpacaMarketDataError, match='future-dated'):
        fake_adapter(datetime.now(UTC)+timedelta(minutes=1)).get_snapshot('AAPL')


def _history_adapter(bars):
    class FakeTimeFrame:
        Minute = "minute"
        Hour = "hour"
        Day = "day"

        def __call__(self, amount, unit):
            return (amount, unit)

    obj = object.__new__(AlpacaMarketAdapter)
    obj.feed = None
    obj._bars_request = lambda **kwargs: kwargs
    obj._time_frame = FakeTimeFrame()
    obj._time_frame_unit = SimpleNamespace(Minute="minute")
    obj._data_feed_enum = SimpleNamespace()
    obj.requested_bar_requests = []

    def get_stock_bars(request):
        obj.requested_bar_requests.append(request)
        return SimpleNamespace(data={"AAPL": bars} if bars else {})

    obj._data_client = SimpleNamespace(get_stock_bars=get_stock_bars)
    return obj


def _bar(timestamp, close=200.0):
    return SimpleNamespace(
        timestamp=timestamp,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1000,
        trade_count=None,
        vwap=None,
    )


def test_rejects_out_of_order_historical_bars():
    first = datetime(2026, 1, 2, tzinfo=UTC)
    second = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(AlpacaMarketDataError, match="out of order"):
        _history_adapter([_bar(first), _bar(second)]).get_history("AAPL")


def test_rejects_duplicate_historical_bars():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(AlpacaMarketDataError, match="duplicate"):
        _history_adapter([_bar(timestamp), _bar(timestamp)]).get_history("AAPL")


def test_empty_bar_response_is_successful_and_preserves_request_metadata():
    adapter = _history_adapter([])
    adapter.feed = "IEX"
    adapter._data_feed_enum = lambda value: value

    result = adapter.get_history("AAPL", period="5d", timeframe="1Day", limit=5)
    request = adapter.requested_bar_requests[0]

    assert result["success"] is True
    assert result["count"] == 0
    assert result["bars"] == []
    assert result["period"] == "5d"
    assert result["feed"] == "IEX"
    assert result["timeframe"] == "1Day"
    assert result["limit"] == 5
    assert result["adjustment"] == "all"
    assert request["symbol_or_symbols"] == "AAPL"
    assert request["feed"] == "iex"
    assert request["timeframe"] == "day"
    assert request["limit"] == 5
    assert request["adjustment"].value == "all"
    assert request["start"].tzinfo is not None
    assert request["start"] < request["end"]


def test_five_day_window_includes_midnight_labeled_daily_bar():
    now = datetime(2026, 10, 16, 4, 47, tzinfo=UTC)
    prior_session_bar = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)

    one_day_start = AlpacaMarketAdapter._period_start("1d", now)
    five_day_start = AlpacaMarketAdapter._period_start("5d", now)

    assert prior_session_bar < one_day_start
    assert five_day_start <= prior_session_bar < now


def test_bar_provider_failure_is_not_reported_as_empty_success():
    adapter = _history_adapter([])
    adapter._data_client.get_stock_bars = lambda request: (_ for _ in ()).throw(
        TimeoutError("private provider details")
    )

    with pytest.raises(AlpacaMarketDataError) as caught:
        adapter.get_history("AAPL", period="5d", timeframe="1Day", limit=5)

    assert caught.value.category == "network"
    assert "private provider details" not in str(caught.value)


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        ConnectionError("provider unavailable"),
        RuntimeError("rate limit or permission denied"),
    ],
)
def test_provider_failures_are_normalized_without_fabricated_prices(failure):
    adapter = fake_adapter(datetime.now(UTC))
    adapter._data_client = SimpleNamespace(
        get_stock_latest_trade=lambda request: (_ for _ in ()).throw(failure)
    )

    with pytest.raises(AlpacaMarketDataError):
        adapter.get_snapshot("AAPL")


@pytest.mark.parametrize(
    "trade",
    [
        None,
        SimpleNamespace(timestamp=datetime.now(UTC), price=None),
        SimpleNamespace(timestamp=None, price=200.0),
    ],
)
def test_empty_or_malformed_trade_is_rejected(trade):
    adapter = object.__new__(AlpacaMarketAdapter)
    adapter.max_quote_age_seconds = 30.0
    adapter.feed = None
    adapter._trade_request = lambda **kwargs: kwargs
    adapter._data_feed_enum = SimpleNamespace()
    adapter._data_client = SimpleNamespace(
        get_stock_latest_trade=lambda request: {"AAPL": trade}
    )

    with pytest.raises(AlpacaMarketDataError):
        adapter.get_snapshot("AAPL")
