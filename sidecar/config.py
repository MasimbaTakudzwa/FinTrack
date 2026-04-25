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
    enable_crypto_job: bool = False
    enable_news_job: bool = True
    enable_alerts_job: bool = True
    enable_prices_daily_job: bool = True
    enable_forecasts_job: bool = True
    enable_sentiment_job: bool = True
    ingest_prices_interval_minutes: int = 5
    ingest_crypto_interval_minutes: int = 15
    ingest_news_interval_minutes: int = 15
    ingest_macro_cron_hour: int = 6
    ingest_prices_daily_cron_hour: int = 22
    # APScheduler CronTrigger accepts "mon"-"sun" strings or 0-6 ints (0=Mon).
    # Stored as a weekday int here so it round-trips through the SETTINGS_SPECS
    # INT type cleanly — mapped to the string in _register_jobs. Default = Sunday.
    train_forecasts_cron_day_of_week: int = 6
    train_forecasts_cron_hour: int = 23
    check_alerts_interval_minutes: int = 1
    # Periodic VADER backfill — `ingest_news` already scores new articles
    # inline, so this is mostly relevant on a fresh install where existing
    # articles need a one-off catch-up. Hourly default keeps the job cheap
    # while still catching anything the inline path missed.
    score_news_sentiment_interval_minutes: int = 60

    def resolved_db_path(self) -> str:
        return self.db_path or _default_db_path()


settings = Settings()
