from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

from .market_data.oanda_adapter import (
    OandaAPIError,
    OandaConfigurationError,
    OandaMarketAdapter,
    OandaV20Client,
)


_CREDENTIAL_KEYS = ("OANDA_API_TOKEN", "OANDA_ACCOUNT_ID")


def _configuration() -> tuple[dict[str, str], str | None]:
    values = {
        key: os.getenv(key, "").strip()
        for key in (*_CREDENTIAL_KEYS, "OANDA_ENVIRONMENT")
    }
    missing = [key for key in _CREDENTIAL_KEYS if not values[key]]
    if not missing:
        values["OANDA_ENVIRONMENT"] = values["OANDA_ENVIRONMENT"] or "practice"
        return values, None

    config_path = Path(
        os.getenv("ULTRON_FINANCIAL_ENV_FILE", ".env.financial")
    ).expanduser()
    try:
        metadata = config_path.stat()
    except FileNotFoundError:
        return values, None
    except OSError:
        return values, "credential_file_unreadable"
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        return values, "credential_file_insecure"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values, "credential_file_unreadable"

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if key in values and not values[key]:
            values[key] = raw_value.strip().strip("\"'")
    values["OANDA_ENVIRONMENT"] = values["OANDA_ENVIRONMENT"] or "practice"
    return values, None


def run_diagnostic(instrument: str = "EUR_USD") -> dict[str, Any]:
    """Run account, instrument and quote reads only; never calls order endpoints."""
    config, file_error = _configuration()
    if file_error:
        return {
            "success": False,
            "read_only": True,
            "stage": "configuration",
            "error": file_error,
            "reason": "The local credentials file is not safely readable.",
            "regional_eligibility": "not_verified",
        }
    missing = [key for key in _CREDENTIAL_KEYS if not config[key]]
    if missing:
        return {
            "success": False,
            "read_only": True,
            "missing_configuration": missing,
            "reason": "No authenticated OANDA requests were attempted.",
            "regional_eligibility": "not_verified",
        }

    stage = "account"
    try:
        client = OandaV20Client(
            token=config["OANDA_API_TOKEN"],
            account_id=config["OANDA_ACCOUNT_ID"],
            environment=config["OANDA_ENVIRONMENT"],
        )
        adapter = OandaMarketAdapter(client=client)
        account = adapter.get_account_snapshot()
        stage = "instrument"
        metadata = adapter.get_instrument(instrument)
        stage = "pricing"
        market_open = adapter.is_market_open(instrument)
        quote = adapter.get_snapshot(instrument) if market_open else None
    except OandaConfigurationError as exc:
        return {
            "success": False,
            "read_only": True,
            "stage": "configuration",
            "error": "invalid_configuration",
            "reason": str(exc),
            "regional_eligibility": "not_verified",
        }
    except ValueError:
        return {
            "success": False,
            "read_only": True,
            "stage": stage,
            "error": "invalid_instrument",
            "reason": "The requested instrument is not a valid OANDA currency pair.",
            "regional_eligibility": "not_verified",
        }
    except OandaAPIError as exc:
        return {
            "success": False,
            "read_only": True,
            "stage": stage,
            "error": exc.failure_type,
            "http_status": exc.status_code,
            "reason": str(exc),
            "regional_eligibility": "not_verified",
        }

    return {
        "success": True,
        "read_only": True,
        "environment": client.environment,
        "account_access_verified": account["account_access_verified"],
        "account_currency": account["currency"],
        "account_status": account["account_status"],
        "account_specific_instrument_available": True,
        "instrument": metadata.symbol,
        "market_status": "tradeable" if market_open else "not_tradeable",
        "quote_available": quote is not None,
        "quote_timestamp": quote["timestamp"].isoformat() if quote else None,
        "quote_age_seconds": round(quote["age_seconds"], 3) if quote else None,
        "regional_eligibility": "not_verified",
        "reason": (
            "Read-only account-specific provider checks completed. Account "
            "instrument availability does not independently establish "
            "jurisdictional eligibility."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run read-only OANDA v20 account and market-data checks."
    )
    parser.add_argument(
        "--instrument",
        default="EUR_USD",
        help="OANDA currency pair, e.g. EUR_USD (default: EUR_USD).",
    )
    args = parser.parse_args()
    result = run_diagnostic(args.instrument)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
