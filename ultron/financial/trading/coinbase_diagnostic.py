"""Credential-safe, read-only Coinbase account and product diagnostic."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .coinbase_advanced import CoinbaseAPIError, CoinbaseAdvancedClient


def _credential_file_values(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    credential_path = Path(path).expanduser()
    if not credential_path.is_file():
        return {}
    if stat.S_IMODE(credential_path.stat().st_mode) & 0o077:
        raise PermissionError(
            "Coinbase credentials file must not be accessible by group or others."
        )

    values: dict[str, str] = {}
    lines = credential_path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in {"COINBASE_API_KEY_NAME", "COINBASE_API_PRIVATE_KEY"}:
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            value = value[1:]
            chunks = [value]
            while quote not in chunks[-1] and index < len(lines):
                chunks.append(lines[index])
                index += 1
            last = chunks[-1]
            if quote in last:
                chunks[-1] = last.rsplit(quote, 1)[0]
            value = "\n".join(chunks)
        value = value.replace("\\n", "\n").strip()
        if value:
            values[name] = value
    return values


def _configured_credentials(
    credentials_file: str | Path | None,
) -> tuple[dict[str, str], list[str]]:
    environment_values = {
        name: os.getenv(name, "")
        for name in ("COINBASE_API_KEY_NAME", "COINBASE_API_PRIVATE_KEY")
    }
    missing_environment = [
        name for name, value in environment_values.items() if not value.strip()
    ]
    file_values = (
        _credential_file_values(credentials_file)
        if missing_environment
        else {}
    )
    credentials = {
        name: environment_values[name] or file_values.get(name, "")
        for name in environment_values
    }
    missing = [name for name, value in credentials.items() if not value.strip()]
    return credentials, missing


def _amount(value: Any) -> Decimal:
    if not isinstance(value, dict):
        raise ValueError
    amount = Decimal(str(value.get("value")))
    if not amount.is_finite() or amount < 0:
        raise ValueError
    return amount


def run_read_only_diagnostic(
    *,
    product_id: str = "BTC-INR",
    quote_currency: str | None = None,
    credentials_file: str | Path | None = ".env.financial",
    client: Any | None = None,
) -> dict[str, Any]:
    """Check authenticated read access without calling any order endpoint."""
    product_id = product_id.strip().upper()
    parts = product_id.split("-")
    if len(parts) != 2 or not all(part.isalnum() for part in parts):
        raise ValueError("Product must use the BASE-QUOTE format.")
    quote = (quote_currency or parts[1]).strip().upper()
    if quote != parts[1]:
        raise ValueError("Quote currency must match the requested product.")

    credentials, missing = _configured_credentials(credentials_file)
    if missing:
        return {
            "authenticated": False,
            "read_only": True,
            "missing_configuration": missing,
            "product_id": product_id,
            "regional_eligibility": "not_verified",
            "reason": "No authenticated requests were attempted.",
        }

    api = client or CoinbaseAdvancedClient(
        api_key_name=credentials["COINBASE_API_KEY_NAME"],
        api_private_key=credentials["COINBASE_API_PRIVATE_KEY"],
    )
    result: dict[str, Any] = {
        "authenticated": False,
        "read_only": True,
        "product_id": product_id,
        "quote_currency": quote,
        "regional_eligibility": "not_verified",
        "region_api_attestation_available": False,
    }
    try:
        permissions = api.get_key_permissions()
        result["permissions"] = {
            "view": permissions.get("can_view") is True,
            "trade": permissions.get("can_trade") is True,
        }
        accounts = api.get_accounts()
        currency_balances: dict[str, dict[str, Decimal]] = {}
        ready_quote_account = False
        for account in accounts:
            currency = str(account.get("currency", "")).strip().upper()
            if not currency:
                raise ValueError("Coinbase returned an account without a currency.")
            available = _amount(account.get("available_balance"))
            held = _amount(account.get("hold"))
            totals = currency_balances.setdefault(
                currency,
                {"available": Decimal(0), "held": Decimal(0)},
            )
            totals["available"] += available
            totals["held"] += held
            if (
                currency == quote
                and account.get("active") is True
                and account.get("ready") is True
                and account.get("platform", "ACCOUNT_PLATFORM_CONSUMER")
                == "ACCOUNT_PLATFORM_CONSUMER"
            ):
                ready_quote_account = True
        result["accounts"] = {
            "count": len(accounts),
            "quote_account_ready": ready_quote_account,
            "balances_valid": True,
            "currencies": {
                currency: {
                    "available_balance_present": True,
                    "held_balance_present": True,
                    "available_balance_positive": balance["available"] > 0,
                    "held_balance_positive": balance["held"] > 0,
                }
                for currency, balance in sorted(currency_balances.items())
            },
            "amounts_withheld": True,
        }

        product = api.get_product(product_id)
        product_available = (
            product.get("product_id") == product_id
            and product.get("product_type", "SPOT") == "SPOT"
            and product.get("is_disabled") is not True
            and product.get("trading_disabled") is not True
            and product.get("view_only") is not True
            and product.get("cancel_only") is not True
            and product.get("post_only") is not True
        )
        result["product"] = {
            "returned_requested_product": product.get("product_id") == product_id,
            "spot": product.get("product_type", "SPOT") == "SPOT",
            "tradable_for_authenticated_account": product_available,
            "base_increment_present": bool(product.get("base_increment")),
            "quote_increment_present": bool(product.get("quote_increment")),
        }
        fee_summary = api.get_transaction_summary()
        fee_tier = fee_summary.get("fee_tier")
        taker_fee = (
            Decimal(str(fee_tier["taker_fee_rate"]))
            if isinstance(fee_tier, dict) and "taker_fee_rate" in fee_tier
            else None
        )
        if taker_fee is None or not taker_fee.is_finite() or taker_fee < 0:
            raise ValueError("Coinbase returned an invalid SPOT fee tier.")
        result["spot_fee_tier"] = {
            "available": True,
            "taker_rate_present": True,
            "rate_withheld": True,
            "exclusive_tax_configured": (
                isinstance(fee_summary.get("goods_and_services_tax"), dict)
                and fee_summary["goods_and_services_tax"].get("type") == "EXCLUSIVE"
            ),
        }
        result["authenticated"] = True
        result["checks_passed"] = all(
            (
                result["permissions"]["view"],
                result["permissions"]["trade"],
                ready_quote_account,
                product_available,
                result["product"]["base_increment_present"],
                result["product"]["quote_increment_present"],
            )
        )
        result["reason"] = (
            "Read-only authenticated checks completed."
            if result["checks_passed"]
            else "Authenticated access succeeded, but one or more readiness checks failed."
        )
    except CoinbaseAPIError as exc:
        result["failure"] = {
            "category": type(exc).__name__,
            "http_status": exc.status_code,
            "message": str(exc),
        }
        result["reason"] = "Authenticated Coinbase read-only check failed."
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        result["failure"] = {
            "category": type(exc).__name__,
            "message": str(exc),
        }
        result["reason"] = "Coinbase returned incomplete or malformed readiness data."
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only authenticated Coinbase account diagnostic."
    )
    parser.add_argument("--product", default="BTC-INR")
    parser.add_argument("--quote-currency")
    parser.add_argument("--credentials-file", default=".env.financial")
    args = parser.parse_args(argv)
    try:
        result = run_read_only_diagnostic(
            product_id=args.product,
            quote_currency=args.quote_currency,
            credentials_file=args.credentials_file,
        )
    except (OSError, ValueError) as exc:
        result = {
            "authenticated": False,
            "read_only": True,
            "reason": str(exc),
            "regional_eligibility": "not_verified",
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("checks_passed") else 1


if __name__ == "__main__":
    sys.exit(main())
