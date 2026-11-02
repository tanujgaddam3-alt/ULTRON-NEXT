ULTRON Market Data / Paper-Trade Integration Patch v1

Apply
-----
1. Back up the project.
2. Activate the project's .venv.
3. Extract apply_patch.py into ~/Projects/ULTRON_INTERFACE_FINAL.
4. From that project root run: python apply_patch.py

The script uses exact-match edits, creates timestamped backups, and stops if an
expected source block is ambiguous. It adds a focused regression test file.

Changes
-------
- Wires MarketDataService(AlpacaMarketAdapter()) into the full container when
  ULTRON_FINANCIAL_MARKET_PROVIDER=alpaca.
- Leaves execution backend separate; default remains simulation/PaperBroker.
- Routes paper-trade through execute_from_market() so price and timestamp are
  obtained from the controller's validated market-data path.
- Removes the unverified manual-price requirement from FinancialSkill.
- Fails closed if no validated market-data service is configured.

Limitation
----------
With provider=yahoo, the controller's MarketDataService remains unconfigured
because the Yahoo skill provider does not implement MarketAdapter. Trading
requests fail closed until a timestamp-aware Yahoo adapter is implemented.
For the intended current path, use provider=alpaca via your private environment
file. Never paste credentials into chat.

Validation
----------
python -m compileall -q ultron/financial/agent/financial_agent.py ultron/skills/financial/skill.py ultron/infrastructure/container.py
ULTRON_FINANCIAL_MARKET_PROVIDER=yahoo python -m pytest -q tests/financial/trading/test_controller_market_integration.py tests/test_financial_bootstrap_isolation.py tests/financial/test_alpaca_marketdata_adapter.py tests/financial/test_market_data_paper_trade_handoff.py
ULTRON_FINANCIAL_MARKET_PROVIDER=yahoo python -m pytest -q tests/financial/test_skill_architecture.py

After offline tests, use your private .env.financial to test Alpaca. A market-closed
or stale-price rejection is correct safety behavior, not proof of provider failure.

Alpaca Read-Only Freshness Diagnostic
------------------------------------
scripts/validate_alpaca_readonly.py reports latest-trade request status,
freshness, the verified regular-session state, feed identity, and whether the
trade may be used as a current quote. The 30-second age threshold is unchanged.
Stale trades remain unavailable through get_snapshot() and get_price(); the
diagnostic may show one separately as last-known data, never as a current quote.
Session boundaries, holidays, weekends, and early closes use Alpaca's trading
calendar. An unavailable or inconsistent clock/calendar fails closed.

ALPACA_DATA_FEED=iex remains an explicit IEX request. IEX identifies a single
exchange feed and is not a consolidated latest trade across all US exchanges.
The diagnostic does not switch feeds or fall back to historical bar closes.
Its paper-account check is read-only, order lifecycle remains not_run, and it
must run with ULTRON_EXECUTION_BACKEND=simulation. Any stale, missing, or
unverified current quote produces a non-zero diagnostic exit status.

TradingView Paper Webhook
-------------------------
The simulation-only TradingView alert integration uses ULTRON's local
PaperBroker and binds its webhook to loopback. See
docs/TRADINGVIEW_PAPER_WEBHOOK.md for its alert schema, secure configuration,
HTTPS reverse-proxy requirements, and paper-only execution semantics.
