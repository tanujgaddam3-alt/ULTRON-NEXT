from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .market_data.groww_adapter import GrowwMarketAdapter, GrowwMarketDataError
from .market_data.groww_credentials import (
    GROWW_CREDENTIAL_KEYS,
    GrowwConfigurationError,
    load_groww_credentials,
)


def _sanitized_error_fields(error: GrowwMarketDataError) -> dict[str, Any]:
    return {
        "error": error.failure_type,
        "error_status": error.http_status,
        "provider_error_code": error.provider_code,
        "error_message": error.diagnostic_message,
    }


def run_read_only_diagnostic(
    *,
    symbol: str = "NIFTY50",
    credentials_file: str | Path | None = ".env.financial",
    adapter: Any | None = None,
) -> dict[str, Any]:
    """Verify Groww-authenticated read-only instrument, quote, and candle access."""
    canonical = GrowwMarketAdapter.normalize_symbol(symbol)
    if adapter is None:
        try:
            credentials = load_groww_credentials(credentials_file)
        except GrowwConfigurationError:
            return {
                "authenticated": False,
                "read_only": True,
                "stage": "configuration",
                "error": "credential_file_unavailable_or_insecure",
                "regional_eligibility": "not_verified",
            }
        missing = [
            name
            for name in GROWW_CREDENTIAL_KEYS
            if not credentials.get(name)
        ]
        if missing:
            return {
                "authenticated": False,
                "read_only": True,
                "missing_configuration": missing,
                "reason": "No authenticated Groww requests were attempted.",
                "regional_eligibility": "not_verified",
            }
        try:
            adapter = GrowwMarketAdapter(
                api_key=credentials[GROWW_CREDENTIAL_KEYS[0]],
                api_secret=credentials[GROWW_CREDENTIAL_KEYS[1]],
                credentials_file=str(credentials_file) if credentials_file else None,
            )
        except GrowwConfigurationError:
            return {
                "authenticated": False,
                "read_only": True,
                "stage": "configuration",
                "error": "groww_sdk_or_credentials_unavailable",
                "regional_eligibility": "not_verified",
            }
        except GrowwMarketDataError as exc:
            return {
                "authenticated": False,
                "read_only": True,
                "stage": "authentication",
                **_sanitized_error_fields(exc),
                "regional_eligibility": "not_verified",
            }

    stage = "instrument"
    try:
        instrument = adapter.get_instrument(canonical)
    except GrowwMarketDataError as exc:
        return {
            "authenticated": False,
            "read_only": True,
            "stage": stage,
            **_sanitized_error_fields(exc),
            "regional_eligibility": "not_verified",
        }

    result: dict[str, Any] = {
        "authenticated": False,
        "read_only": True,
        "instrument_available": True,
        "instrument": instrument.symbol,
        "exchange": instrument.exchange,
        "instrument_type": instrument.instrument_type.value,
        "orderable": False,
        "quote_available": False,
        "history_available": False,
        "history_candle_count": 0,
        "regional_eligibility": "not_verified",
    }

    stage = "quote"
    try:
        quote = adapter.get_snapshot(canonical)
        result["authenticated"] = True
        result["quote_available"] = True
        result["quote_timestamp"] = quote["timestamp"].isoformat()
        result["quote_age_seconds"] = round(float(quote["age_seconds"]), 3)
    except GrowwMarketDataError as exc:
        result["quote_error"] = exc.failure_type
        result["quote_error_status"] = exc.http_status
        result["quote_provider_error_code"] = exc.provider_code
        result["quote_error_message"] = exc.diagnostic_message

    stage = "historical_data"
    try:
        candles = adapter.get_history(canonical, period="5d", interval="1d")
        result["authenticated"] = True
        result["history_available"] = bool(candles)
        result["history_candle_count"] = len(candles)
    except GrowwMarketDataError as exc:
        result["history_error"] = exc.failure_type
        result["history_error_status"] = exc.http_status
        result["history_provider_error_code"] = exc.provider_code
        result["history_error_message"] = exc.diagnostic_message

    result["success"] = (
        result["authenticated"]
        and result["instrument_available"]
        and (result["quote_available"] or result["history_available"])
    )
    result["reason"] = (
        "Authenticated read-only market-data calls completed. This does not "
        "verify regional eligibility, data licensing, or permission for trading."
        if result["success"]
        else "No authenticated Groww market-data call completed successfully."
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run read-only Groww instrument and market-data checks."
    )
    parser.add_argument(
        "--symbol",
        default="NIFTY50",
        help="NIFTY50 or SENSEX (default: NIFTY50).",
    )
    parser.add_argument(
        "--credentials-file",
        default=None,
        help="Restricted local credentials file (default: ULTRON_FINANCIAL_ENV_FILE or .env.financial).",
    )
    args = parser.parse_args()
    result = run_read_only_diagnostic(
        symbol=args.symbol,
        credentials_file=args.credentials_file,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
