from pathlib import Path

from loguru import logger

from ultron.config.settings import settings

LOG_FILE = Path(settings.log_directory)
LOG_FILE.mkdir(parents=True, exist_ok=True)

logger.remove()

logger.add(
    LOG_FILE / "ultron.log",
    rotation="10 MB",
    retention="30 days",
    level=settings.log_level,
)

logger.add(
    lambda msg: print(msg, end=""),
    level=settings.log_level,
)

ultron_logger = logger
