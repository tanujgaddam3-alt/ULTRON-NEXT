"""Offline checks for the standalone financial bootstrap."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_financial_cli_help_does_not_initialize_full_runtime():
    result = subprocess.run(
        [sys.executable, "financial_main.py", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--self-check" in result.stdout


def test_financial_bootstrap_builds_without_full_container_or_llm():
    script = textwrap.dedent(
        """
        import sys, types
        # This test is offline. Stub only yfinance's import boundary because
        # the audit environment may not have the optional market-data package.
        yf = types.ModuleType('yfinance')
        yf.Ticker = lambda *args, **kwargs: None
        sys.modules['yfinance'] = yf

        from ultron.financial.bootstrap import build_financial_runtime
        runtime = build_financial_runtime(memory_db=':memory:')
        try:
            assert type(runtime.paper_broker).__name__ == 'PaperBroker'
            assert runtime.paper_broker.snapshot({})['equity'] == 100000.0
            assert 'ultron.infrastructure.container' not in sys.modules
            assert 'ultron.ai.llm.ollama_provider' not in sys.modules
        finally:
            runtime.close()
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
