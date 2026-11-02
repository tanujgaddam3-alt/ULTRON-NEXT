from __future__ import annotations

import os

from ultron.financial.trading.coinbase_diagnostic import (
    run_read_only_diagnostic,
)


class ReadOnlyCoinbaseClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_key_permissions(self):
        self.calls.append("permissions")
        return {"can_view": True, "can_trade": True}

    def get_accounts(self):
        self.calls.append("accounts")
        return [
            {
                "currency": "INR",
                "active": True,
                "ready": True,
                "platform": "ACCOUNT_PLATFORM_CONSUMER",
                "available_balance": {"value": "1000.50"},
                "hold": {"value": "25"},
            }
        ]

    def get_product(self, product_id: str):
        self.calls.append("product")
        return {
            "product_id": product_id,
            "product_type": "SPOT",
            "is_disabled": False,
            "trading_disabled": False,
            "view_only": False,
            "cancel_only": False,
            "post_only": False,
            "base_increment": "0.00000001",
            "quote_increment": "1",
        }

    def get_transaction_summary(self):
        self.calls.append("fee")
        return {"fee_tier": {"taker_fee_rate": "0.01"}}


def test_missing_credentials_report_names_without_network_calls(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("COINBASE_API_KEY_NAME", raising=False)
    monkeypatch.delenv("COINBASE_API_PRIVATE_KEY", raising=False)
    client = ReadOnlyCoinbaseClient()

    result = run_read_only_diagnostic(
        credentials_file=tmp_path / "absent.env",
        client=client,
    )

    assert result["authenticated"] is False
    assert result["missing_configuration"] == [
        "COINBASE_API_KEY_NAME",
        "COINBASE_API_PRIVATE_KEY",
    ]
    assert result["regional_eligibility"] == "not_verified"
    assert result["reason"] == "No authenticated requests were attempted."
    assert client.calls == []


def test_read_only_diagnostic_checks_authoritative_read_endpoints_without_amounts(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("COINBASE_API_KEY_NAME", "configured-key")
    monkeypatch.setenv("COINBASE_API_PRIVATE_KEY", "configured-private-key")
    client = ReadOnlyCoinbaseClient()

    result = run_read_only_diagnostic(
        credentials_file=tmp_path / "absent.env",
        client=client,
    )

    assert result["authenticated"] is True
    assert result["checks_passed"] is True
    assert result["permissions"] == {"view": True, "trade": True}
    assert result["accounts"]["quote_account_ready"] is True
    assert result["accounts"]["currencies"]["INR"] == {
        "available_balance_present": True,
        "held_balance_present": True,
        "available_balance_positive": True,
        "held_balance_positive": True,
    }
    assert result["accounts"]["amounts_withheld"] is True
    assert result["product"]["tradable_for_authenticated_account"] is True
    assert result["regional_eligibility"] == "not_verified"
    assert result["region_api_attestation_available"] is False
    assert client.calls == ["permissions", "accounts", "product", "fee"]
    assert "1000.50" not in repr(result)
    assert "25" not in repr(result)


def test_credential_file_is_read_without_echoing_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("COINBASE_API_KEY_NAME", raising=False)
    monkeypatch.delenv("COINBASE_API_PRIVATE_KEY", raising=False)
    credential_path = tmp_path / "private.env"
    credential_path.write_text(
        "COINBASE_API_KEY_NAME=private-key-id\n"
        "COINBASE_API_PRIVATE_KEY='line-one\\nline-two'\n",
        encoding="utf-8",
    )
    os.chmod(credential_path, 0o600)
    client = ReadOnlyCoinbaseClient()

    result = run_read_only_diagnostic(
        credentials_file=credential_path,
        client=client,
    )

    assert result["authenticated"] is True
    assert result["checks_passed"] is True
    assert "private-key-id" not in repr(result)
    assert "line-one" not in repr(result)


def test_insecure_credential_file_is_rejected_without_network_calls(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("COINBASE_API_KEY_NAME", raising=False)
    monkeypatch.delenv("COINBASE_API_PRIVATE_KEY", raising=False)
    credential_path = tmp_path / "insecure.env"
    credential_path.write_text(
        "COINBASE_API_KEY_NAME=private-key-id\n"
        "COINBASE_API_PRIVATE_KEY=private-key\n",
        encoding="utf-8",
    )
    os.chmod(credential_path, 0o644)

    client = ReadOnlyCoinbaseClient()
    try:
        run_read_only_diagnostic(
            credentials_file=credential_path,
            client=client,
        )
    except PermissionError as exc:
        assert "group or others" in str(exc)
    else:
        raise AssertionError("Insecure credentials file was accepted.")
    assert client.calls == []


def test_authenticated_but_unready_account_is_not_marked_eligible(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("COINBASE_API_KEY_NAME", "configured-key")
    monkeypatch.setenv("COINBASE_API_PRIVATE_KEY", "configured-private-key")

    class UnreadyClient(ReadOnlyCoinbaseClient):
        def get_accounts(self):
            accounts = super().get_accounts()
            accounts[0]["ready"] = False
            return accounts

    result = run_read_only_diagnostic(
        credentials_file=tmp_path / "absent.env",
        client=UnreadyClient(),
    )

    assert result["authenticated"] is True
    assert result["checks_passed"] is False
    assert result["accounts"]["quote_account_ready"] is False
    assert result["regional_eligibility"] == "not_verified"
