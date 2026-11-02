from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.ultron_cutover_identity import report_identity
from scripts.ultron_release_bundle import build_bundle


def test_cutover_identity_reports_release_database_and_sidecars_without_database_open(tmp_path):
    root = tmp_path / "release-source"
    root.mkdir()
    (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    listed = tmp_path / "files.txt"
    listed.write_text("app.py\n", encoding="utf-8")
    archive = tmp_path / "release.zip"
    build_bundle(
        root,
        listed,
        archive,
        "known-release",
        source_revision="abc123",
        supported_cre_schemas=[10],
    )

    database = tmp_path / "identity.sqlite"
    database.write_bytes(b"database-main")
    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")
    wal.write_bytes(b"wal-state")
    shm.write_bytes(b"shm-state")
    before = {
        path: path.read_bytes()
        for path in (database, wal, shm)
    }

    report = report_identity(archive, database)

    assert report["status"] == "IDENTITY_RECORDED"
    assert report["release"]["manifest"]["sourceRevision"] == "abc123"
    assert report["database"]["sha256"] == hashlib.sha256(b"database-main").hexdigest()
    assert report["database"]["sidecars"] == {
        "identity.sqlite-wal": {"size": 9, "sha256": hashlib.sha256(b"wal-state").hexdigest()},
        "identity.sqlite-shm": {"size": 9, "sha256": hashlib.sha256(b"shm-state").hexdigest()},
    }
    assert all(path.read_bytes() == before[path] for path in before)


@pytest.mark.parametrize("missing", ["release", "database"])
def test_cutover_identity_requires_both_existing_targets(tmp_path, missing):
    database = tmp_path / "database.sqlite"
    database.write_bytes(b"database")
    archive = tmp_path / "missing.zip"

    if missing == "release":
        with pytest.raises(ValueError, match="existing paths"):
            report_identity(archive, database)
    else:
        root = tmp_path / "root"
        root.mkdir()
        listed = tmp_path / "files.txt"
        listed.write_text("missing\n", encoding="utf-8")
        with pytest.raises(ValueError, match="existing paths"):
            report_identity(archive, tmp_path / "missing.sqlite")
