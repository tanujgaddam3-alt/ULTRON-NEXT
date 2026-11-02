from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """
    Global configuration for ULTRON.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "ULTRON"

    version: str = "2.0.0"

    debug: bool = False

    log_level: str = "INFO"

    data_directory: Path = PROJECT_ROOT / "data"

    log_directory: Path = PROJECT_ROOT / "logs"

    database_url: str = "sqlite:///data/ultron.db"

    model_name: str = "llama3.2"

    timezone: str = "UTC"

    language: str = "en"

    max_memory_results: int = Field(default=5, ge=1)


settings = Settings()
