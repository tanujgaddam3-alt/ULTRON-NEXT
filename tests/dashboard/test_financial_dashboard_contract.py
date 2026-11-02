from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PATH = ROOT / "dashboard" / "financial_dashboard.py"


class FakeStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[str, object]] = []
        self.captions: list[str] = []
        self.subheaders: list[str] = []
        self.session_state: dict[str, object] = {}

    def set_page_config(self, **_kwargs) -> None:
        return None

    def cache_resource(self, function):
        return function

    def columns(self, count: int):
        return [self for _ in range(count)]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def metric(self, label: str, value: object) -> None:
        self.metrics.append((label, value))

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def subheader(self, value: str) -> None:
        self.subheaders.append(value)

    def divider(self) -> None:
        return None

    def markdown(self, *_args, **_kwargs) -> None:
        return None

    def write(self, *_args, **_kwargs) -> None:
        return None

    def success(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def dataframe(self, *_args, **_kwargs) -> None:
        return None


def _load_dashboard(monkeypatch, fake_streamlit: FakeStreamlit):
    streamlit_module = types.ModuleType("streamlit")
    for name in (
        "set_page_config",
        "cache_resource",
        "columns",
        "metric",
        "caption",
        "subheader",
        "divider",
        "markdown",
        "write",
        "success",
        "warning",
        "dataframe",
    ):
        setattr(streamlit_module, name, getattr(fake_streamlit, name))
    streamlit_module.session_state = fake_streamlit.session_state

    class UnexpectedContainerConstruction:
        def __init__(self):
            raise AssertionError("The dashboard must reuse the process container.")

    container_module = types.ModuleType("ultron.infrastructure.container")
    container_module.Container = UnexpectedContainerConstruction
    singleton = object()
    container_module.container = singleton
    monkeypatch.setitem(sys.modules, "streamlit", streamlit_module)
    monkeypatch.setitem(sys.modules, "ultron.infrastructure.container", container_module)

    module_name = "_ultron_financial_dashboard_test"
    spec = importlib.util.spec_from_file_location(module_name, DASHBOARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module, singleton


def test_financial_dashboard_launcher_defaults_to_simulation_and_preserves_explicit_mode(
    tmp_path,
):
    launcher = tmp_path / "run_financial_dashboard.sh"
    shutil.copy2(ROOT / "run_financial_dashboard.sh", launcher)
    executable = tmp_path / ".venv" / "bin" / "streamlit"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({'backend': os.environ.get('ULTRON_EXECUTION_BACKEND'), "
        "'args': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    for configured, expected in ((None, "simulation"), ("simulation", "simulation"), ("blocked", "blocked")):
        environment = os.environ.copy()
        if configured is None:
            environment.pop("ULTRON_EXECUTION_BACKEND", None)
        else:
            environment["ULTRON_EXECUTION_BACKEND"] = configured
        result = subprocess.run(
            [str(launcher)],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["backend"] == expected
        assert payload["args"] == ["run", "dashboard/financial_dashboard.py"]


def test_financial_dashboard_runtime_dependency_is_declared():
    requirements = [
        line.strip().split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].lower()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert requirements.count("streamlit") == 1


def test_dashboard_maps_current_financial_agent_contract_and_renders_metrics(monkeypatch):
    ui = FakeStreamlit()
    dashboard, _ = _load_dashboard(monkeypatch, ui)
    result = {
        "success": True,
        "symbol": "AAPL",
        "market": {"current_price": 123.45, "trend": "up"},
        "financial_analysis": {"score": 82, "rating": "strong"},
        "regime": {"regime": "bull", "confidence": 0.9},
        "risk": {"risk_level": "low", "warnings": []},
        "decision": {"decision": "hold", "confidence": 0.8},
        "reasoning": {"summary": "Synthetic test evidence."},
        "strategies": ["test-strategy"],
    }

    data = dashboard.extract_analysis(result)
    dashboard.render_analysis(data)

    assert data["financial"] == result["financial_analysis"]
    assert data["regime"] == result["regime"]
    assert ("Financial Score", 82) in ui.metrics
    assert ("Rating", "STRONG") in ui.metrics
    assert ("Risk", "LOW") in ui.metrics
    assert ui.subheaders == ["◉ AAPL Intelligence"]


def test_dashboard_reuses_the_existing_container_singleton(monkeypatch):
    ui = FakeStreamlit()
    dashboard, singleton = _load_dashboard(monkeypatch, ui)

    assert dashboard.get_container() is singleton


def test_alpaca_paper_dashboard_reports_the_successful_query_time(monkeypatch):
    ui = FakeStreamlit()
    dashboard, _ = _load_dashboard(monkeypatch, ui)

    class FakeTradingClient:
        def __init__(self, key, secret, *, paper):
            assert (key, secret, paper) == ("test-key", "test-secret", True)

        def get_account(self):
            return types.SimpleNamespace(
                status="ACTIVE",
                equity="1000.00",
                buying_power="500.00",
            )

        def get_all_positions(self):
            return []

        def get_orders(self):
            return []

    alpaca = types.ModuleType("alpaca")
    alpaca.__path__ = []
    trading = types.ModuleType("alpaca.trading")
    trading.__path__ = []
    client = types.ModuleType("alpaca.trading.client")
    client.TradingClient = FakeTradingClient
    monkeypatch.setitem(sys.modules, "alpaca", alpaca)
    monkeypatch.setitem(sys.modules, "alpaca.trading", trading)
    monkeypatch.setitem(sys.modules, "alpaca.trading.client", client)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")

    account = dashboard.get_alpaca_account()
    dashboard.render_alpaca()

    assert account["connected"] is True
    datetime.fromisoformat(account["lastUpdatedAt"])
    assert any("Last successful account query:" in line for line in ui.captions)
    assert any("PAPER MODE ONLY" in line for line in ui.captions)
