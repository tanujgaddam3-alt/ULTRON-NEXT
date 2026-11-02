import pytest

from ultron.financial.trading.intelligence.decision_adapter import (
    DecisionSignalAdapter,
)
from ultron.financial.trading.signal import SignalAction


def test_approved_decision_becomes_buy_signal():
    decision = {
        "approved": True,
        "action": "buy",
        "symbol": " test ",
        "entry_price": 100.0,
        "confidence": 0.90,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "reasons": ["bullish trend", "strong momentum"],
    }

    signal = DecisionSignalAdapter.convert(decision)

    assert signal.symbol == "TEST"
    assert signal.action == SignalAction.BUY
    assert signal.price == 100.0
    assert signal.stop_loss == 95.0
    assert signal.take_profit == 110.0
    assert signal.confidence == 0.90
    assert signal.strategy == "ULTRON_DECISION_ENGINE"


def test_unapproved_decision_is_rejected():
    with pytest.raises(ValueError, match="approved"):
        DecisionSignalAdapter.convert({
            "approved": False,
        })


def test_invalid_action_is_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        DecisionSignalAdapter.convert({
            "approved": True,
            "action": "explode",
            "symbol": "TEST",
            "entry_price": 100.0,
        })


def test_exit_values_can_come_from_exits():
    decision = {
        "approved": True,
        "action": "buy",
        "symbol": "TEST",
        "entry_price": 100.0,
        "confidence": 0.80,
        "exits": {
            "stop_loss": 96.0,
            "take_profit": 108.0,
        },
    }

    signal = DecisionSignalAdapter.convert(decision)

    assert signal.stop_loss == 96.0
    assert signal.take_profit == 108.0


def test_reasons_are_combined():
    decision = {
        "approved": True,
        "action": "buy",
        "symbol": "TEST",
        "entry_price": 100.0,
        "confidence": 0.80,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "reasons": ["trend", "momentum"],
    }

    signal = DecisionSignalAdapter.convert(decision)

    assert signal.reason == "trend momentum"
