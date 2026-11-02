from types import SimpleNamespace

import pytest

from ultron.financial.trading.alpaca_paper_broker import AlpacaPaperBroker
from ultron.financial.trading.coinbase_advanced import CoinbaseSpotBroker
from ultron.financial.trading.controller import FinancialTradingController
from ultron.financial.trading.engine import PaperTradingEngine
from ultron.financial.trading.oanda_execution import OandaForexBroker
from ultron.security.permission.rules import (
    require_simulation_execution_backend,
)


class ProviderBrokerSentinel:
    mode = "paper"
    supports_live_orders = False

    def __init__(self):
        self.calls = []

    def get_account_snapshot(self, prices):
        self.calls.append(("snapshot", prices))
        return {"equity": 1000.0}

    def submit(self, order):
        self.calls.append(("submit", order))
        return {"success": True}

    def cancel_order(self, order_id):
        self.calls.append(("cancel", order_id))
        return {"success": True}


@pytest.mark.parametrize("mode", ["paper", "live", None, "unknown"])
@pytest.mark.parametrize(
    "entry_point",
    [
        "controller_execute",
        "controller_execute_from_market",
        "controller_cancel",
        "controller_unlocked",
        "engine_execute",
    ],
)
def test_provider_mode_is_denied_before_broker_invocation(mode, entry_point):
    broker = ProviderBrokerSentinel()
    broker.mode = mode
    controller = FinancialTradingController.__new__(
        FinancialTradingController
    )
    controller.trading_engine = SimpleNamespace(broker=broker)
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine.broker = broker

    with pytest.raises(PermissionError, match="local simulation"):
        if entry_point == "controller_execute":
            controller.execute({}, 1.0)
        elif entry_point == "controller_execute_from_market":
            controller.execute_from_market({})
        elif entry_point == "controller_cancel":
            controller.cancel_order("sentinel-order")
        elif entry_point == "controller_unlocked":
            controller._execute_unlocked({}, 1.0)
        else:
            engine.execute(None)

    assert broker.calls == []


@pytest.mark.parametrize(
    ("broker_type", "method"),
    [
        (AlpacaPaperBroker, "submit"),
        (AlpacaPaperBroker, "cancel_order"),
        (CoinbaseSpotBroker, "submit"),
        (CoinbaseSpotBroker, "cancel_order"),
        (OandaForexBroker, "submit"),
        (OandaForexBroker, "cancel_order"),
    ],
)
def test_provider_adapters_deny_before_client_invocation(
    broker_type,
    method,
):
    broker = broker_type.__new__(broker_type)
    broker.mode = "paper" if broker_type is AlpacaPaperBroker else "live"

    with pytest.raises(PermissionError, match="local simulation"):
        if method == "submit":
            broker.submit(None)
        else:
            broker.cancel_order("sentinel-order")


def test_simulation_broker_is_allowed_by_shared_mode_guard():
    from ultron.financial.trading.paper_broker import PaperBroker
    from ultron.security.permission.rules import require_simulation_broker

    require_simulation_broker(PaperBroker(), "test execution")


@pytest.mark.parametrize("backend", ["", "alpaca_paper", "coinbase_live", "unknown"])
def test_container_rejects_missing_or_provider_execution_mode(backend):
    with pytest.raises(RuntimeError, match="explicitly set to simulation"):
        require_simulation_execution_backend(backend)


def test_container_accepts_only_explicit_simulation_mode():
    require_simulation_execution_backend("simulation")
