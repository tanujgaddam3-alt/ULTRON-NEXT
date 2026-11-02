#!/usr/bin/env python3
"""Report release and database file identity without opening or changing a database."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Sequence

try:
    from scripts.ultron_release_bundle import BundleError, verify_bundle
except ModuleNotFoundError:
    from ultron_release_bundle import BundleError, verify_bundle


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def report_identity(release_archive: Path, database: Path) -> dict:
    try:
        release_archive = release_archive.resolve(strict=True)
        database = database.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("Release archive and database must be existing paths.") from error
    try:
        manifest = verify_bundle(release_archive)
    except BundleError as error:
        raise ValueError(str(error)) from error
    if not database.is_file() or database.is_symlink():
        raise ValueError("Database identity target must be a regular non-symlink file.")

    sidecars = {}
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists():
            sidecars[sidecar.name] = {
                "size": sidecar.stat().st_size,
                "sha256": _sha256(sidecar),
            }
    return {
        "status": "IDENTITY_RECORDED",
        "release": {
            "path": str(release_archive),
            "sha256": _sha256(release_archive),
            "manifest": manifest,
        },
        "database": {
            "path": str(database),
            "size": database.stat().st_size,
            "sha256": _sha256(database),
            "sidecars": sidecars,
            "logicalSchemaNotOpened": True,
            "logicalSchemaEvidenceRequired": "A separate disposable-copy validation must supply schema evidence.",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-archive", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        print(json.dumps(report_identity(arguments.release_archive, arguments.database), sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
