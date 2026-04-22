from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_db_path() -> str:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "sidecar").is_dir():
        return str(cwd / "fintrack.db")
    data_dir = Path(user_data_dir(appname="FinTrack", appauthor="FinTrack"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "fintrack.db")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FINTRACK_", env_file=".env", extra="ignore")

    port: int = 8765
    db_path: str = ""
    log_level: str = "info"
    fred_api_key: str | None = None
    enable_scheduler: bool = True
    enable_seed: bool = True
    ingest_prices_interval_minutes: int = 5

    def resolved_db_path(self) -> str:
        return self.db_path or _default_db_path()


settings = Settings()
