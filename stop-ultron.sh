#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="$ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  printf '%s\n' "ULTRON shutdown failed: expected project interpreter is missing: $PYTHON" >&2
  exit 1
fi

exec "$PYTHON" "$ROOT/scripts/ultron_service_manager.py" stop --root "$ROOT"
