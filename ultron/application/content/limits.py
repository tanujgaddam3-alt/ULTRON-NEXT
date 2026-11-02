from __future__ import annotations

import os


_DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
_ABSOLUTE_MAX_SOURCE_BYTES = 32 * 1024 * 1024

try:
    MAX_SOURCE_BYTES = int(
        os.environ.get("ULTRON_CRE_MAX_SOURCE_BYTES", str(_DEFAULT_MAX_SOURCE_BYTES))
    )
except ValueError as exc:
    raise RuntimeError("ULTRON_CRE_MAX_SOURCE_BYTES must be an integer.") from exc

if not 1024 * 1024 < MAX_SOURCE_BYTES <= _ABSOLUTE_MAX_SOURCE_BYTES:
    raise RuntimeError(
        "ULTRON_CRE_MAX_SOURCE_BYTES must be greater than 1 MiB and at most 32 MiB."
    )
