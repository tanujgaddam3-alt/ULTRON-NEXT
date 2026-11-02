from __future__ import annotations

import os
import stat
from pathlib import Path


GROWW_CREDENTIAL_KEYS = ("GROWW_API_KEY", "GROWW_API_SECRET")


class GrowwConfigurationError(ValueError):
    """Missing or unsafe local Groww credential configuration."""


def load_groww_credentials(
    credentials_file: str | Path | None = None,
) -> dict[str, str]:
    credentials = {
        name: os.getenv(name, "").strip()
        for name in GROWW_CREDENTIAL_KEYS
    }
    missing = [name for name, value in credentials.items() if not value]
    if not missing:
        return credentials

    path = Path(
        credentials_file
        if credentials_file is not None
        else os.getenv("ULTRON_FINANCIAL_ENV_FILE", ".env.financial")
    ).expanduser()
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return credentials
    except OSError:
        raise GrowwConfigurationError(
            "The configured Groww credentials file is not readable."
        ) from None

    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise GrowwConfigurationError(
            "The Groww credentials file must be a regular file accessible only by its owner."
        )

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise GrowwConfigurationError(
            "The configured Groww credentials file is not readable."
        ) from None

    for line in lines:
        value = line.strip()
        if not value or value.startswith("#") or "=" not in value:
            continue
        name, secret = value.split("=", 1)
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export ") :].strip()
        if name in credentials and not credentials[name]:
            credentials[name] = secret.strip().strip("\"'")
    return credentials
