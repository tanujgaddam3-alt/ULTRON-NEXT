# ULTRON Financial Research: standalone ML baseline

This is the first research tranche for the standalone financial subsystem. It adds a CPU-friendly, offline training and evaluation path. It does **not** place trades and does not connect to the dashboard.

## Intended order of work

1. Start the financial subsystem independently from the full ULTRON runtime.
2. Verify data-provider wiring, risk controls, timestamps, persistence, and paper-broker lifecycle.
3. Load a licensed historical OHLCV dataset and validate it.
4. Train baseline signal models with chronological partitions and purged labels.
5. Evaluate an expanding-window walk-forward protocol on validation dates, then report once on the separate chronological final holdout with explicit transaction-cost assumptions and benchmark metrics.
6. Add regime-specific evaluation and robust risk sizing only after the baseline is reproducible.
7. Run shadow signals and then paper trading with fresh market data, position monitoring, persistent state, and kill switches.
8. Integrate with the dashboard only after the independent service passes acceptance tests.

## Dependencies

From the project root, activate the existing virtual environment and install the base requirements first:

```bash
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-financial-ml.txt
```

The financial research pipeline uses pandas, NumPy, scikit-learn, and joblib. It runs on CPU and does not load Ollama or an LLM.

## Dataset format

The loader accepts one combined CSV or a directory of CSV files. Required columns are date, open, high, low, close, and volume. A ticker/symbol column is accepted; for per-symbol files, the filename becomes the ticker. Common capitalization and aliases are normalized.

Use data you have permission to use and inspect its license, corporate-action adjustments, timezone, missing dates, delisted-symbol coverage, and survivorship bias. The CSV loader checks OHLCV schema, numeric/range validity, and duplicate ticker/date rows; it cannot verify a dataset's license, source provenance, completeness, survivorship bias, or adjustment methodology. Record and review those externally before interpreting results. Do not merge adjusted close with unadjusted OHLC. The loader prefers `Close` when both `Close` and `Adj Close` are present to keep OHLC-derived features internally consistent; for split-adjusted research, use a dataset whose OHLC fields are all adjusted consistently.

## Train a first baseline

```bash
python financial_research.py --data /path/to/nifty50_daily_csv_directory
```

Or for one combined CSV:

```bash
python financial_research.py --data /path/to/ohlcv.csv --output data/financial/research
```

Artifacts:

- `market_signal_model.joblib`: fitted model and feature metadata.
- `training_report.json`: data ranges, walk-forward validation and final-holdout metrics, and cost-aware simulations.

The model estimates the probability that the next session's open-to-close return is positive. Features are computed from data available at the prior close. It assumes entry at the next session open and exit at that session close. It does not model overnight close-to-open returns, slippage, spread, taxes, liquidity, corporate actions, broker rejections, or operational failures beyond the configured per-side cost. It is a research baseline, not a production trading strategy.

Defaults: chronological 70% initial train / 15% validation / 15% final test by date; an expanding-window, up-to-three-fold walk-forward evaluation over validation dates; a supplied 0.55 probability threshold; and a legacy generic 10 basis points per side for non-Indian symbols. Indian symbols require an explicit user-provided cost estimate. Classification reports include Brier score and log loss as probabilistic scoring metrics; those aggregate scores alone do not establish calibration. Each walk-forward fold trains only on earlier dates and purges labels crossing its start and end boundaries. The model parameters and threshold remain fixed across folds. The final test segment is evaluated separately and is not used for training or threshold selection. Freeze the protocol before examining final-test results; if protocol or parameters are changed in response to those results, that segment is no longer an untouched holdout. These costs are only a simple per-side assumption: spread, slippage, taxes, liquidity, fill uncertainty, and execution constraints remain unmodeled.

## Candidate datasets

Start with daily OHLCV, not a 13 GB intraday dataset. Examples to review:

- NIFTY-50 Stock Market Data: https://www.kaggle.com/datasets/rohanrao/nifty50-stock-market-data
- NSE stock daily data (2016-2017; useful for loader smoke tests, not sufficient as the only training history): https://www.kaggle.com/minatverma/nse-stocks-data
- Stock Market Signal: Predict Next-Day Returns competition (anonymized engineered features; useful as a separate ML benchmark, not directly suitable for OHLCV backtesting): https://www.kaggle.com/competitions/stock-market-signal-predict-next-day-returns/data
- NIFTY-50 minute data (large; defer until daily pipeline is stable): https://www.kaggle.com/general/353753

Check each dataset's current license and contents on Kaggle before using it. Data freshness and quality are not guaranteed merely because a dataset is hosted there.

## Important safety boundary

This command only trains and evaluates a model. It cannot submit orders. Paper trading remains a separate stage and should not be enabled for model signals until the market-data timestamp path, position exits, persistent portfolio state, daily loss controls, idempotency, and reconciliation have been validated.

The trading controller accepts market prices only through a timestamped snapshot
that passes freshness checks. Adapters exposing only `get_price()` do not provide
enough provenance to authorize execution and are rejected; they must supply a
source timestamp rather than relying on the service to synthesize one. Paper
simulation adapters may timestamp their local simulated quotes explicitly.
Snapshots with bid/ask values are checked for completeness, positive finite
prices, and crossed markets. The service reports midpoint, absolute spread, and
spread in basis points when the provider supplies a quote. Incomplete candles
are rejected from validated series.

Portfolio reconciliation rejects snapshots that omit cash or a positions
dictionary, contain malformed position quantities, or use non-finite values or
invalid tolerances. The controller currently builds its in-process comparison
from the broker-authoritative snapshot, so this check validates snapshot
structure but is not an independent persisted portfolio ledger. Coinbase's
separate durable balance baseline and authoritative fill reconciliation remain
the independent accounting check for that broker.

## Indian indices and instruments (read-only research)

Set `ULTRON_FINANCIAL_MARKET_PROVIDER=india_yahoo` to opt into the existing
Yahoo Finance adapter through `MarketDataService`. It recognizes `NIFTY 50`,
`NIFTY50`, and `^NSEI` as the NSE index data identity `NIFTY50` (`^NSEI` at
Yahoo), and `SENSEX`, `BSE SENSEX`, and `^BSESN` as BSE index identity `SENSEX`
(`^BSESN` at Yahoo). It can retrieve latest available index bars and historical
OHLCV where the source returns them, retaining provider timestamps and marking
partial current-session daily candles incomplete. Timestamps are represented
in `Asia/Kolkata`; the weekday 09:15–15:30 session is only an estimate because
no exchange holiday calendar is configured.

Yahoo Finance availability through `yfinance` is not evidence of licensed,
authorized, or real-time NSE/BSE data access. The adapter explicitly reports
real-time status as unknown/delayed, does not assert authorized feed access,
and rejects timestamp-naive data. The source and its licensing, latency,
historical coverage, and completeness have not been externally verified here.
Do not use these quotes for execution.

The shared instrument model distinguishes equities, indices, ETFs, futures,
and options. NSE/BSE instruments returned by the Yahoo adapter remain
non-orderable because current provider-authoritative contract metadata is not
available. Index records are always market-data-only; controller validation
blocks them before any paper or live order path. Indian cash securities and
derivatives require fresh, verified exchange metadata (including applicable
tick/lot/quantity constraints) before they can be made orderable. Unknown India
tick sizes, lot sizes, quantities, and notionals remain unset rather than
falling back to generic values. No changing lot sizes, expiries, strikes, or
costs are hardcoded.

The generic paper adapter, central paper engine, and `PaperBroker` also reject
the recognized index identities directly, including when a caller bypasses
Yahoo metadata and attempts a raw paper order.

The existing research CSV loader, chronological split, purged labels,
walk-forward evaluation, and artifact path accept NIFTY/Sensex symbols. An
Indian-index or NSE/BSE equity backtest/training run must supply
`cost_bps_per_side` explicitly;
this is an operator estimate, not an asserted Indian-market charge schedule.
Exchange/broker fees, taxes, spread, slippage, liquidity, and fill uncertainty
remain unmodeled unless independently sourced and added. Results remain
research-only. Paper orders for the indices themselves are blocked; paper
orders for a future Indian executable instrument require fresh verified
metadata and still pass through the existing controller/risk path.

## Groww authenticated Indian index market data (read-only)

Set `ULTRON_FINANCIAL_MARKET_PROVIDER=groww` to select the authenticated
Groww Trade API market-data adapter. It uses the documented Groww Python SDK
with `GROWW_API_KEY` and `GROWW_API_SECRET`, or reads those values from
`ULTRON_FINANCIAL_ENV_FILE` (default `.env.financial`) only when that file is
owner-only. The SDK exchanges the API key and secret for an access token via
`POST /v1/token/api/access`; its approval flow hashes the secret concatenated
with the current epoch-seconds timestamp using SHA-256. Groww requires daily
approval for API-key/secret keys. The SDK sends the timestamp as a JSON number,
while the cURL documentation's request schema labels it a string; the SDK flow
is retained rather than duplicating token generation. The SDK returns the token
string, which the adapter passes to its client. NIFTY 50 and Sensex are resolved
against Groww's current instrument directory and remain data-only, non-orderable
indices. There is no fallback to Yahoo when Groww is unavailable.

The adapter uses the documented `get_quote` API for a timestamped latest
quote and the current `get_historical_candles` SDK method for daily candles.
Historical requests are chunked within Groww's documented 180-day per-request
limit. The quote response timestamp is documented as epoch milliseconds;
historical timestamps are timezone-naive exchange-local strings, which the
adapter interprets as `Asia/Kolkata` and marks as that explicit assumption.
Index candle volume is retained as unavailable when Groww returns `null`,
rather than being fabricated as zero. The adapter rejects stale or malformed
values, marks the current daily candle incomplete during the estimated
NSE/BSE session, and includes Groww provenance. Market hours use a weekday
09:15-15:30 estimate; no verified exchange holiday calendar is available in
the adapter. The quote endpoint is documented as real-time, but actual
account access, feed entitlement, and observed latency are not verified until
the read-only diagnostic succeeds.

Run `python -m ultron.financial.trading.groww_diagnostic` for read-only
instrument, quote, and candle checks. It does not return prices, balances,
account identifiers, or credentials, and does not call order operations.
The cURL guide states that access tokens expire daily at 06:00 and API-key /
secret keys require daily Cloud API Keys approval. The SDK exposes only the
token string, so the adapter does not inspect token expiry metadata or renew
tokens automatically; authentication failures fail closed without retry.
Groww SDK 401 errors are reported as authentication failures and 403 errors as
authorization failures; a 400 is reported as a sanitized bad request and does
not independently establish that daily approval was the cause. The cURL guide
requires `Authorization: Bearer ...`, `Accept: application/json`, and
`X-API-VERSION: 1.0` on API requests; the adapter supplies the documented
`Accept` header for SDK read requests. The verified docs list a 150-requests
per-24-hour limit for access-token generation and shared limits for API types;
the adapter does not retry. Its supported history window is capped at 1080
days and is split into documented 180-day `get_historical_candles` requests.
The older `get_historical_candle_data` method is deprecated and is not used.

When a Groww request fails, the read-only diagnostic reports the normalized
error category, an HTTP status when retained by the SDK, and a fixed,
sanitized explanation. It may report a provider error code separately when the
SDK exposes a non-HTTP code. Raw SDK response messages are never included
because they are not guaranteed to be free of sensitive content. An absent
HTTP status means the SDK did not retain a verifiable numeric HTTP status; it
must not be inferred from a provider business-error code.

Official references:

- [Groww Python SDK introduction and authentication](https://groww.in/trade-api/docs/python-sdk)
- [Groww instrument directory](https://groww.in/trade-api/docs/python-sdk/instruments)
- [Groww live quote data](https://groww.in/trade-api/docs/python-sdk/live-data)
- [Groww historical candle data](https://groww.in/trade-api/docs/python-sdk/historical-data)
- [Groww API key management](https://groww.in/trade-api/api-keys)

Authenticated market-data access does not establish regional eligibility,
data licensing, or permission to deploy automated trading. The runtime
continues to use the local paper broker; this integration contains no Groww
order submission, modification, or cancellation methods.

No Indian execution broker has been integrated. Before a real execution
adapter is considered, the operator must confirm the intended account's API eligibility,
authorized market-data rights, instrument coverage, order/modify/cancel and
fill/transaction-history semantics, rate limits, session behavior, tick/lot/
quantity/notional limits, fees and levies, margin rules for derivatives, and
durable recovery support. Broker API access alone is not treated as authority
to deploy an automated strategy.

This project makes no legal determination about SEBI, NSE, or BSE requirements.
Before any automated deployment, the operator and broker must verify the
current requirements applicable to that account and strategy, including any
algorithm identification/registration or approval, API-access, network/static
IP, audit, and exchange controls that apply. Consult current primary material
from [SEBI](https://www.sebi.gov.in/legal/circulars.html),
[NSE](https://www.nseindia.com/resources/exchange-communication-circulars),
and [BSE](https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx)
and the selected broker; none of those obligations has been verified for an
account in this workspace.

## OANDA v20 forex market data (read-only)

Set `ULTRON_FINANCIAL_MARKET_PROVIDER=oanda` with `OANDA_API_TOKEN` and
`OANDA_ACCOUNT_ID` to use OANDA's authenticated REST v20 account pricing,
account instrument metadata, and candle endpoints. The adapter uses the
practice endpoint by default; `OANDA_ENVIRONMENT=live` explicitly selects the
live REST host for read-only requests. It polls the provider rather than
streaming, retains provider timestamps, rejects stale/future prices, reports
bid/ask spread and top-level liquidity where supplied, and preserves the
provider's candle-completeness flag. The account instrument list is specific
to the authorized account and reflects OANDA's regulatory division, but this
does not independently prove the user's jurisdictional or legal eligibility.
OANDA personal access tokens are sensitive account credentials and may grant
access to multiple subaccounts. Keep the token protected; although this adapter
exposes only GET requests, that client-side limitation does not narrow the
token's permissions at OANDA.

Run `python -m ultron.financial.trading.oanda_diagnostic --instrument EUR_USD`
for a sanitized read-only diagnostic. Credentials are read from the process
environment or a local `.env.financial` file only when its permissions are
restricted to the owner. The diagnostic does not print account IDs, token
values, balances, or call order endpoints. This adapter does not implement
order submission, cancellation, or live forex execution; select a paper or
simulation execution backend. Account authentication and provider access remain
unverified until the diagnostic succeeds using operator-provisioned credentials.

## Coinbase Advanced Trade spot adapter

`ULTRON_FINANCIAL_MARKET_PROVIDER=coinbase` selects Coinbase Advanced Trade
spot quotes and daily OHLCV candles for crypto symbols such as `BTC-INR` or
`BTC/INR`. Coinbase public market data does not require credentials. Historical
requests obey Coinbase's 350-candle response limit and only include completed
candles. This setting does not silently fall back to Yahoo or Alpaca.

Authenticated account and execution operations use a Coinbase CDP API key
configured through `COINBASE_API_KEY_NAME` and
`COINBASE_API_PRIVATE_KEY`. The ES256 key needs view permission; order
submission also requires trade permission. Before submitting, the broker checks
that an active, ready quote-currency account and tradable spot product are
available. The Advanced Trade API response does not verify account residency.
For the currently configured India eligibility gate, `COINBASE_ACCOUNT_REGION=IN`
and `COINBASE_REGION_ELIGIBILITY_CONFIRMED=true` are operator assertions, not
proof from Coinbase that the particular account or product is eligible. The
broker now fails closed unless a trusted regional-eligibility verifier supplies
account- and product-specific authoritative evidence; the main container does
not currently wire such a verifier, so regional eligibility blocks live
submission even when the environment assertion is true. Evidence must also
identify `COINBASE_ELIGIBILITY_ACCOUNT_ID`, and that UUID must match an active,
ready quote-currency account UUID returned by Coinbase. This prevents evidence
for an unrelated account from satisfying the gate; the account UUID is not
written to diagnostic output.

The adapter submits marketable spot limit IOC orders using Coinbase's
documented `sor_limit_ioc` order configuration and a stable ULTRON client order
ID. An API acceptance is not treated as a fill: order status, fills,
commissions, cancellation outcomes, and balances are retrieved from Coinbase.
Account snapshots include held as well as available asset quantities; available
and held amounts are also reported separately. The broker reads Coinbase's
authenticated [SPOT transaction summary](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/fees/get-transaction-summary)
and reserves the reported taker fee (including exclusive GST) against
available quote funds before a buy. Timed-out submissions remain
unresolved; `CoinbaseSpotBroker.reconcile_submission` searches Coinbase's
authoritative order history by client order ID and does not automatically
resubmit. The main `Container` wires the live broker through the financial
trading engine and controller, which apply price freshness, trading pause,
daily loss, position sizing, exposure, durable idempotency, and balance
reconciliation checks. The SQLite journal records the intent before any order
request, stores stable client IDs and authoritative order/fill data, reconciles
account balances including fees, and blocks startup/trading when recovery is
uncertain. The broker also rejects execution prices that differ by more than
0.5% from the controller's risk-checked reference price.

Keep `ULTRON_EXECUTION_BACKEND=simulation` and
`ULTRON_COINBASE_LIVE_ORDERS_ENABLED` unset during development. Real order
submission requires the `coinbase_live` backend, Coinbase market data, an
absolute `ULTRON_COINBASE_EXECUTION_DB` path on operator-provisioned durable
storage, `ULTRON_COINBASE_LIVE_ORDERS_ENABLED=true`, and the explicit
`ULTRON_LIVE_TRADING_AUTHORIZATION=I_ACKNOWLEDGE_REAL_ORDER_RISK` acknowledgement.
The account region and `COINBASE_REGION_ELIGIBILITY_CONFIRMED=true` remain
operator assertions because the API does not expose a Coinbase attestation of
account residency; they cannot authorize orders. Do not enable live execution
until a read-only authenticated account check and authoritative regional/product
eligibility evidence have been obtained for the intended account and supplied
through a trusted verifier. The current workspace has not verified those
external prerequisites, and the main container has no trusted verifier wired;
live order submission therefore fails closed. This implementation is not
approved or claimed to be operational or production-ready.

Run `python -m ultron.financial.trading.coinbase_diagnostic --product BTC-INR`
to perform a read-only authenticated check of key permissions, account
readiness, balance-field validity, product tradability, and spot fee-tier
availability. It reads the two Coinbase credentials from the process
environment or a mode-`0600` `.env.financial` file, never prints credential
values or balance amounts, and never calls an order endpoint. A successful
diagnostic does not verify account residency or establish regional eligibility.
