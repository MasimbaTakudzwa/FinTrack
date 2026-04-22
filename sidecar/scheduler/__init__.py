from __future__ import annotations

import logging
from threading import Lock

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from sidecar.config import settings
from sidecar.scheduler.jobs import ingest_prices

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


def _register_jobs(scheduler: BackgroundScheduler) -> None:
    scheduler.add_job(
        ingest_prices,
        trigger=IntervalTrigger(minutes=settings.ingest_prices_interval_minutes),
        id="ingest_prices",
        name="Ingest OHLCV prices from yfinance",
        replace_existing=True,
    )


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
        _register_jobs(scheduler)
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


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
