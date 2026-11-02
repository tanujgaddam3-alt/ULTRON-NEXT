from __future__ import annotations

import argparse
from pathlib import Path
import sys

from ultron.application.content.status import verify_cre_runtime_database_read_only


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_OPERATIONAL_DATABASE = _REPOSITORY_ROOT / "data" / "ultron_cre.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only CRE schema-v10 runtime compatibility check for a disposable copy."
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args(argv)

    candidate = args.database
    if not candidate.is_absolute():
        parser.error("database path must be absolute")
    try:
        resolved = candidate.resolve(strict=False)
        operational = _OPERATIONAL_DATABASE.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(f"database path is invalid: {error}")
    if resolved == operational:
        parser.error("the repository operational database is not allowed by this check")

    try:
        verify_cre_runtime_database_read_only(resolved)
    except RuntimeError as error:
        print(f"CRE runtime compatibility preflight: FAIL: {error}", file=sys.stderr)
        return 1

    print(f"CRE runtime compatibility preflight: PASS: {resolved}")
    print("CRE schema_version=10")
    print("SQLite user_version=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
