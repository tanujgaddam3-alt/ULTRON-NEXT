from __future__ import annotations

from pathlib import Path

import pytest

from ultron.application.runtime.service_entrypoint import (
    DEFAULT_BIND_HOST,
    DEFAULT_BIND_PORT,
    DEFAULT_RUNTIME_ID,
    _runtime_configuration,
)


def test_runtime_configuration_preserves_production_defaults(monkeypatch, tmp_path: Path):
    for name in (
        "ULTRON_INTERFACE_BIND_HOST",
        "ULTRON_INTERFACE_BIND_PORT",
        "ULTRON_RUNTIME_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    assert _runtime_configuration(tmp_path) == (
        DEFAULT_BIND_HOST,
        DEFAULT_BIND_PORT,
        DEFAULT_RUNTIME_ID,
    )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ULTRON_INTERFACE_BIND_HOST", "0.0.0.0", "loopback IP"),
        ("ULTRON_INTERFACE_BIND_HOST", "not a host", "loopback IP"),
        ("ULTRON_INTERFACE_BIND_PORT", "not-a-port", "integer"),
        ("ULTRON_INTERFACE_BIND_PORT", "0", "integer"),
        ("ULTRON_INTERFACE_BIND_PORT", "65536", "integer"),
        ("ULTRON_RUNTIME_ID", "../candidate", "URL-safe"),
    ],
)
def test_runtime_configuration_rejects_invalid_values(
    monkeypatch,
    tmp_path: Path,
    name: str,
    value: str,
    message: str,
):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=message):
        _runtime_configuration(tmp_path)


def _configure_isolated_instance(monkeypatch, tmp_path: Path) -> Path:
    database = tmp_path / "candidate-v10.sqlite"
    database.touch()
    monkeypatch.setenv("ULTRON_RUNTIME_ID", "candidate-v10")
    monkeypatch.setenv("ULTRON_CRE_DATABASE", str(database))
    monkeypatch.setenv("ULTRON_OPERATION_DB", str(tmp_path / "candidate-ops.sqlite"))
    monkeypatch.setenv(
        "ULTRON_CRE_CONTENT_STORAGE_ROOT",
        str(tmp_path / "candidate-content"),
    )
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_TOKEN", "C" * 43)
    monkeypatch.setenv("ULTRON_INTERFACE_AUTH_PRINCIPAL", "candidate-operator")
    monkeypatch.setenv("ULTRON_CRE_WORKER_ENABLED", "false")
    monkeypatch.setenv("ULTRON_CRE_YOUTUBE_PUBLISHING_ENABLED", "false")
    monkeypatch.setenv("ULTRON_CRE_PUBLISHING_DRY_RUN_ENABLED", "false")
    return database


def test_runtime_configuration_accepts_explicit_isolated_instance(
    monkeypatch,
    tmp_path: Path,
):
    _configure_isolated_instance(monkeypatch, tmp_path)
    monkeypatch.setenv("ULTRON_INTERFACE_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("ULTRON_INTERFACE_BIND_PORT", "18080")

    assert _runtime_configuration(tmp_path) == (
        "127.0.0.1",
        18080,
        "candidate-v10",
    )


def test_runtime_configuration_rejects_production_database_for_candidate(
    monkeypatch,
    tmp_path: Path,
):
    database = _configure_isolated_instance(monkeypatch, tmp_path)
    production_named_database = tmp_path / "ultron_cre.db"
    production_named_database.touch()
    monkeypatch.setenv("ULTRON_CRE_DATABASE", str(production_named_database))

    with pytest.raises(RuntimeError, match="production CRE database"):
        _runtime_configuration(tmp_path)
    assert database.is_file()


def test_runtime_configuration_requires_workers_and_publishing_disabled(
    monkeypatch,
    tmp_path: Path,
):
    _configure_isolated_instance(monkeypatch, tmp_path)
    monkeypatch.setenv("ULTRON_CRE_WORKER_ENABLED", "true")

    with pytest.raises(RuntimeError, match="ULTRON_CRE_WORKER_ENABLED=false"):
        _runtime_configuration(tmp_path)
