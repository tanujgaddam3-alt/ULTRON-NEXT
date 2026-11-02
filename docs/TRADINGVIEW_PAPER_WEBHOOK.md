# TradingView Paper Webhook

ULTRON accepts TradingView strategy alerts as triggers for its existing
simulation-only financial runtime. TradingView does not place orders into its
built-in Paper Trading account through this integration. Automated simulated
execution uses ULTRON's local `PaperBroker`.

## Runtime Configuration

Provide these settings through the deployment's secret/configuration manager.
Do not put the webhook secret in source control, command history, URLs, logs,
or support messages.

```text
ULTRON_EXECUTION_BACKEND=simulation
ULTRON_FINANCIAL_MARKET_PROVIDER=alpaca
ALPACA_DATA_FEED=iex
ULTRON_TRADINGVIEW_ALLOWED_SYMBOLS=AAPL,MSFT
ULTRON_TRADINGVIEW_STRATEGY_ID=ultron-paper-v1
ULTRON_TRADINGVIEW_WEBHOOK_SECRET=<high-entropy secret, at least 32 bytes>
ULTRON_AUTONOMOUS_STATE_DB=data/financial/autonomous_trading.sqlite3
```

Alpaca credentials must be supplied through the normal secure process
environment. The webhook runtime refuses non-simulation backends and refuses
any broker other than the existing local `PaperBroker`. Keep the SQLite state
database on durable local storage; it contains event idempotency, execution
journals, and paper portfolio recovery state.

Start ULTRON from the project root:

```sh
.venv/bin/python scripts/run_ultron_tradingview_webhook.py \
  --state-db data/financial/autonomous_trading.sqlite3 \
  --port 8765
```

The server binds only to `127.0.0.1`. Do not expose it directly to the public
internet. Configure a trusted reverse proxy to terminate HTTPS and forward
`POST /webhook/tradingview` to `127.0.0.1:8765/webhook/tradingview`. The proxy
must not log request bodies. The shared secret is carried in the JSON body
because TradingView alerts do not provide a configurable authentication
header; TLS is required for the public hop.

## TradingView Alert

Configure the alert for strategy order events and use this exact JSON schema.
Replace the secret placeholder inside TradingView's protected alert
configuration with the provisioned secret; do not paste it into code or
documentation. `event_id` must be unique per logical alert. This example
combines ticker, bar time, strategy order ID, and action; strategies that can
emit multiple identical orders in one bar must add a unique order sequence.

```json
{
  "schema": "ultron.tradingview.v1",
  "secret": "<provisioned webhook secret>",
  "event_id": "{{ticker}}:{{time}}:{{strategy.order.id}}:{{strategy.order.action}}",
  "strategy_id": "ultron-paper-v1",
  "symbol": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "price": "{{strategy.order.price}}",
  "timestamp": "{{timenow}}"
}
```

Only allowlisted symbols, the configured strategy ID, `buy`/`sell` actions,
finite positive reference prices, timezone-aware timestamps no more than 120
seconds old, and a valid authenticated payload are accepted. The TradingView
price is a reference only. ULTRON obtains its own fresh validated market price
and rejects excessive divergence; it never uses the alert price as an
execution price.

For BUY, ULTRON runs its financial analysis and requires its own `consider`
decision, confidence and risk gates, an available position slot, current
market data, and all existing controller, daily-loss, position-sizing,
idempotency, and journal safeguards. For SELL, ULTRON can only close an
existing local paper position through the controller's guarded exit path; it
does not open short positions.

## Acknowledgments And Recovery

- `completed` means the local paper fill and durable journal write completed.
- `rejected` means ULTRON processed the alert but a decision, market-data,
  pause, risk, position, or broker check prevented a fill.
- `duplicate` means the same event ID and payload were already recorded; no
  second execution occurs.
- `processing` or `reconciliation_required` means do not retry with a new ID.
  Resolve the durable runtime state first. Startup fails closed when an event
  or execution intent remains unresolved.
- Invalid authentication returns 401; invalid schema/data returns 400;
  oversized requests return 413; in-progress or conflicting event IDs return
  409.

Unit and loopback HTTP tests exercise local handling only. They do not prove
TradingView alert delivery, public DNS, reverse-proxy TLS, or an external
TradingView-to-host connection. No live broker orders are supported by this
integration.