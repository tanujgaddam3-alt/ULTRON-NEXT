from __future__ import annotations

from datetime import UTC, datetime

from ultron.financial.trading import oanda_diagnostic


def clear_oanda_environment(monkeypatch):
    for name in (
        "OANDA_API_TOKEN",
        "OANDA_ACCOUNT_ID",
        "OANDA_ENVIRONMENT",
        "ULTRON_FINANCIAL_ENV_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_missing_credentials_do_not_attempt_authentication(monkeypatch, tmp_path):
    clear_oanda_environment(monkeypatch)
    monkeypatch.setenv("ULTRON_FINANCIAL_ENV_FILE", str(tmp_path / "missing.env"))

    result = oanda_diagnostic.run_diagnostic()

    assert result["success"] is False
    assert result["read_only"] is True
    assert result["missing_configuration"] == [
        "OANDA_API_TOKEN",
        "OANDA_ACCOUNT_ID",
    ]
    assert "No authenticated" in result["reason"]


def test_diagnostic_rejects_world_readable_credentials_file(monkeypatch, tmp_path):
    clear_oanda_environment(monkeypatch)
    credentials = tmp_path / ".env.financial"
    credentials.write_text(
        "OANDA_API_TOKEN=secret-token\nOANDA_ACCOUNT_ID=secret-account\n"
    )
    credentials.chmod(0o644)
    monkeypatch.setenv("ULTRON_FINANCIAL_ENV_FILE", str(credentials))

    result = oanda_diagnostic.run_diagnostic()

    assert result["error"] == "credential_file_insecure"
    assert "secret-token" not in str(result)
    assert "secret-account" not in str(result)


def test_diagnostic_reads_restricted_credentials_and_withholds_account_data(
    monkeypatch, tmp_path
):
    clear_oanda_environment(monkeypatch)
    credentials = tmp_path / ".env.financial"
    credentials.write_text(
        "OANDA_API_TOKEN=secret-token\n"
        "OANDA_ACCOUNT_ID=secret-account\n"
        "OANDA_ENVIRONMENT=practice\n"
    )
    credentials.chmod(0o600)
    monkeypatch.setenv("ULTRON_FINANCIAL_ENV_FILE", str(credentials))

    class FakeClient:
        def __init__(self, *, token, account_id, environment):
            assert token == "secret-token"
            assert account_id == "secret-account"
            self.environment = environment

    class FakeInstrument:
        symbol = "EUR_USD"

    class FakeAdapter:
        def __init__(self, *, client):
            self.client = client

        def get_account_snapshot(self):
            return {
                "account_access_verified": True,
                "currency": "USD",
                "account_status": "unknown",
                "cash": 12345.0,
            }

        def get_instrument(self, instrument):
            assert instrument == "EUR_USD"
            return FakeInstrument()

        def is_market_open(self, instrument):
            return True

        def get_snapshot(self, instrument):
            return {
                "timestamp": datetime.now(UTC),
                "age_seconds": 0.01,
                "price": 1.08,
            }

    monkeypatch.setattr(oanda_diagnostic, "OandaV20Client", FakeClient)
    monkeypatch.setattr(oanda_diagnostic, "OandaMarketAdapter", FakeAdapter)

    result = oanda_diagnostic.run_diagnostic()

    assert result["success"] is True
    assert result["account_access_verified"] is True
    assert result["account_specific_instrument_available"] is True
    assert result["quote_available"] is True
    assert result["regional_eligibility"] == "not_verified"
    rendered = str(result)
    for secret in ("secret-token", "secret-account", "12345.0", "1.08"):
        assert secret not in rendered


def test_diagnostic_reports_http_authentication_failure_safely(monkeypatch):
    clear_oanda_environment(monkeypatch)
    monkeypatch.setenv("OANDA_API_TOKEN", "secret-token")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "secret-account")

    class FakeClient:
        environment = "practice"

    class FakeAdapter:
        def __init__(self, *, client):
            pass

        def get_account_snapshot(self):
            raise oanda_diagnostic.OandaAPIError(
                "OANDA rejected a read-only request (HTTP 401).",
                status_code=401,
                failure_type="authentication",
            )

    monkeypatch.setattr(oanda_diagnostic, "OandaV20Client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(oanda_diagnostic, "OandaMarketAdapter", FakeAdapter)

    result = oanda_diagnostic.run_diagnostic()

    assert result["success"] is False
    assert result["stage"] == "account"
    assert result["error"] == "authentication"
    assert result["http_status"] == 401
    assert "secret-token" not in str(result)
    assert "secret-account" not in str(result)
