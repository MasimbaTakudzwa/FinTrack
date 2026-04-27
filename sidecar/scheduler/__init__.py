from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from sidecar.config import settings
from sidecar.scheduler.jobs import (
    check_price_alerts,
    ingest_crypto,
    ingest_macro,
    ingest_news,
    ingest_prices,
    ingest_prices_daily,
    score_news_sentiment_job,
    train_forecasts_job,
)
from sidecar.services.settings import load_effective_config

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_lock = Lock()


def _build_scheduler(db_path: str) -> BackgroundScheduler:
    jobstores = {"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")}
    executors = {"default": ThreadPoolExecutor(max_workers=4)}
    job_defaults = {
        "misfire_grace_time": 60,
        "coalesce": True,
        "max_instances": 1,
    }
    return BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone="UTC",
    )


def _register_jobs(scheduler: BackgroundScheduler, config: dict[str, Any]) -> None:
    """Add / update / remove jobs to match `config`.

    `replace_existing=True` upserts in the persistent jobstore; missing jobs
    are removed via `remove_job` (wrapped in suppress to tolerate a not-yet-
    seeded jobstore on first start).

    Interval-triggered jobs are scheduled with ``next_run_time=now`` so a
    freshly-launched sidecar populates data immediately instead of waiting a
    full interval (otherwise the dashboard shows empty price history for the
    first 5-15 minutes after cold start).
    """
    now = datetime.now(UTC)

    scheduler.add_job(
        ingest_prices,
        trigger=IntervalTrigger(minutes=int(config["ingest_prices.interval_minutes"])),
        id="ingest_prices",
        name="Ingest OHLCV prices from yfinance",
        replace_existing=True,
        next_run_time=now,
    )

    if bool(config["ingest_crypto.enabled"]):
        scheduler.add_job(
            ingest_crypto,
            trigger=IntervalTrigger(
                minutes=int(config["ingest_crypto.interval_minutes"])
            ),
            id="ingest_crypto",
            name="Ingest OHLC crypto prices from CoinGecko",
            replace_existing=True,
            next_run_time=now,
        )
    else:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job("ingest_crypto")

    if bool(config["ingest_news.enabled"]):
        scheduler.add_job(
            ingest_news,
            trigger=IntervalTrigger(
                minutes=int(config["ingest_news.interval_minutes"])
            ),
            id="ingest_news",
            name="Ingest news articles from Yahoo RSS",
            replace_existing=True,
            next_run_time=now,
        )
    else:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job("ingest_news")

    if config.get("fred_api_key"):
        # Fire-on-first-add semantics: when `ingest_macro` is being added for
        # the first time (no prior job in the jobstore), kick off a backfill
        # immediately so a user who just pasted their FRED key sees data
        # without waiting up to 24h for the cron. On subsequent registrations
        # — sidecar restart with the job already persisted, or a reconfigure
        # that only changes the cron hour — we skip `next_run_time` so the
        # existing schedule is honoured.
        macro_kwargs: dict[str, Any] = {}
        if scheduler.get_job("ingest_macro") is None:
            macro_kwargs["next_run_time"] = now
        scheduler.add_job(
            ingest_macro,
            trigger=CronTrigger(
                hour=int(config["ingest_macro.cron_hour_utc"]), minute=0
            ),
            id="ingest_macro",
            name="Ingest macro observations from FRED",
            replace_existing=True,
            **macro_kwargs,
        )
    else:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job("ingest_macro")

    if bool(config["ingest_prices_daily.enabled"]):
        # Same fire-on-first-add pattern as ingest_macro — on a fresh install
        # the user needs their 5y backfill to land in seconds, not on the next
        # day's cron. On reconfigure / restart we respect the existing schedule.
        daily_kwargs: dict[str, Any] = {}
        if scheduler.get_job("ingest_prices_daily") is None:
            daily_kwargs["next_run_time"] = now
        scheduler.add_job(
            ingest_prices_daily,
            trigger=CronTrigger(
                hour=int(config["ingest_prices_daily.cron_hour_utc"]), minute=0
            ),
            id="ingest_prices_daily",
            name="Ingest daily closes (5y backfill + incremental)",
            replace_existing=True,
            **daily_kwargs,
        )
    else:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job("ingest_prices_daily")

    if bool(config["check_alerts.enabled"]):
        scheduler.add_job(
            check_price_alerts,
            trigger=IntervalTrigger(
                minutes=int(config["check_alerts.interval_minutes"])
            ),
            id="check_price_alerts",
            name="Scan active price alerts for threshold crossings",
            replace_existing=True,
            next_run_time=now,
        )
    else:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job("check_price_alerts")

    if bool(config["train_forecasts.enabled"]):
        # Weekly SARIMAX retrain. Fires on the configured day-of-week + hour
        # (UTC). Fire-on-first-add is intentionally NOT applied here: the fit
        # is CPU-intensive (~seconds per asset x N assets) and we don't want
        # to slam a freshly-installed machine mid-startup — `ingest_prices_daily`
        # has to land first before there's any training data anyway. Users
        # who want a forecast *now* can hit the "Retrain" button in the UI.
        scheduler.add_job(
            train_forecasts_job,
            trigger=CronTrigger(
                day_of_week=int(config["train_forecasts.cron_day_of_week"]),
                hour=int(config["train_forecasts.cron_hour_utc"]),
                minute=0,
            ),
            id="train_forecasts",
            name="Weekly SARIMAX retrain for every active asset",
            replace_existing=True,
        )
    else:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job("train_forecasts")

    if bool(config["score_news_sentiment.enabled"]):
        # Backfill any unscored articles via VADER. The new-article path is
        # already covered inline by `ingest_news` so this job is a safety net
        # for historical rows imported before sentiment was wired plus the
        # very-occasional "ingest_news scored 0 because the ML backend
        # blipped" case. Fire-on-first-add so a fresh install with imported
        # articles populates immediately.
        sentiment_kwargs: dict[str, Any] = {}
        if scheduler.get_job("score_news_sentiment") is None:
            sentiment_kwargs["next_run_time"] = now
        scheduler.add_job(
            score_news_sentiment_job,
            trigger=IntervalTrigger(
                minutes=int(config["score_news_sentiment.interval_minutes"])
            ),
            id="score_news_sentiment",
            name="VADER sentiment backfill for unscored articles",
            replace_existing=True,
            **sentiment_kwargs,
        )
    else:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job("score_news_sentiment")


def start() -> BackgroundScheduler | None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return _scheduler
        if not settings.enable_scheduler:
            logger.info("Scheduler disabled via settings")
            return None
        db_path = settings.resolved_db_path()
        scheduler = _build_scheduler(db_path)
        _register_jobs(scheduler, load_effective_config())
        scheduler.start()
        logger.info("Scheduler started (jobstore=%s)", db_path)
        _scheduler = scheduler
        return scheduler


def shutdown(wait: bool = False) -> None:
    global _scheduler
    with _lock:
        if _scheduler is None:
            return
        try:
            _scheduler.shutdown(wait=wait)
            logger.info("Scheduler stopped")
        finally:
            _scheduler = None


def reconfigure() -> bool:
    """Re-register jobs against the current effective config.

    Returns True if the running scheduler was updated; False if no scheduler
    is running (in which case the next `start()` will pick up the new config
    automatically).
    """
    with _lock:
        if _scheduler is None:
            return False
        _register_jobs(_scheduler, load_effective_config())
        logger.info("Scheduler jobs reconfigured from effective config")
        return True


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
