from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    """Supported financial asset classes."""

    EQUITY = "equity"
    CRYPTO = "crypto"
    FOREX = "forex"


def normalize_symbol(symbol: str, asset_class: AssetClass | None = None) -> str:
    """
    Normalize user-facing symbols into provider-compatible symbols.

    Examples:
        BTC/USD -> BTC-USD
        USD/INR -> USDINR=X
        EUR/USD -> EURUSD=X
        AAPL    -> AAPL
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("A market symbol is required.")

    value = symbol.strip().upper().replace(" ", "")
    indian_indices = {
        "NIFTY": "^NSEI",
        "NIFTY50": "^NSEI",
        "NIFTY 50": "^NSEI",
        "^NSEI": "^NSEI",
        "SENSEX": "^BSESN",
        "BSESENSEX": "^BSESN",
        "^BSESN": "^BSESN",
    }
    if value in indian_indices:
        return indian_indices[value]

    detected = asset_class or classify_asset(value)

    if detected is AssetClass.CRYPTO:
        if "/" in value:
            base, quote = value.split("/", 1)
            return f"{base}-{quote}"
        return value

    if detected is AssetClass.FOREX:
        if "/" in value:
            base, quote = value.split("/", 1)
            return f"{base}{quote}=X"
        if value.endswith("=X"):
            return value
        return f"{value}=X"

    return value


def classify_asset(symbol: str) -> AssetClass:
    """Infer an asset class from a common user-facing symbol."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("A market symbol is required.")

    value = symbol.strip().upper().replace(" ", "")

    # Common crypto notation.
    if "/" in value:
        base, quote = value.split("/", 1)
        if base and quote:
            fiat = {
                "USD", "EUR", "GBP", "JPY", "INR",
                "AUD", "CAD", "CHF", "NZD",
                "SGD", "HKD", "CNY", "CNH",
                "SEK", "NOK", "DKK", "ZAR",
                "BRL", "MXN", "KRW", "TRY",
            }

            if base in fiat and quote in fiat:
                return AssetClass.FOREX

            return AssetClass.CRYPTO

    if value.endswith("=X"):
        return AssetClass.FOREX

    if value.endswith("-USD") or value.endswith("-USDT") or value.endswith("-USDC"):
        return AssetClass.CRYPTO

    # Common crypto symbols without an explicit quote.
    known_crypto = {
        "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "DOT",
        "AVAX", "LINK", "LTC", "BCH", "MATIC", "UNI",
    }
    if value in known_crypto:
        return AssetClass.CRYPTO

    return AssetClass.EQUITY
