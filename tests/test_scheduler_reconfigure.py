"""Scheduler reconfigure integration tests.

These tests spin up a real BackgroundScheduler in `paused` mode — jobs are
actually persisted to the SQLite jobstore (so `replace_existing=True` does
what it says), but the scheduler thread never fires. That keeps the tests
deterministic while still covering the real reconfigure code paths.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from sidecar.scheduler import _register_jobs, reconfigure
from sidecar.services.settings import apply_updates


@pytest.fixture
def paused_scheduler(isolated_db: Path) -> Iterator[BackgroundScheduler]:
    sched = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{isolated_db}")},
        timezone="UTC",
    )
    sched.start(paused=True)
    try:
        yield sched
    finally:
        sched.shutdown(wait=False)


DEFAULT_CONFIG = {
    "ingest_prices.interval_minutes": 5,
    "ingest_crypto.enabled": False,
    "ingest_crypto.interval_minutes": 15,
    "ingest_news.enabled": True,
    "ingest_news.interval_minutes": 15,
    "ingest_macro.cron_hour_utc": 6,
    "ingest_prices_daily.enabled": True,
    "ingest_prices_daily.cron_hour_utc": 22,
    "train_forecasts.enabled": True,
    "train_forecasts.cron_day_of_week": 6,
    "train_forecasts.cron_hour_utc": 23,
    "fred_api_key": "",
    "check_alerts.enabled": True,
    "check_alerts.interval_minutes": 1,
}


def test_register_jobs_adds_ingest_prices_with_default_interval(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    job = paused_scheduler.get_job("ingest_prices")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 5 * 60
    assert paused_scheduler.get_job("ingest_crypto") is None
    assert paused_scheduler.get_job("ingest_macro") is None


def test_register_jobs_adds_news_job_by_default(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    job = paused_scheduler.get_job("ingest_news")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 15 * 60


def test_register_jobs_removes_disabled_news(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    assert paused_scheduler.get_job("ingest_news") is not None

    _register_jobs(
        paused_scheduler, dict(DEFAULT_CONFIG, **{"ingest_news.enabled": False})
    )
    assert paused_scheduler.get_job("ingest_news") is None


def test_register_jobs_adds_check_alerts_by_default(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    job = paused_scheduler.get_job("check_price_alerts")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 60


def test_register_jobs_removes_disabled_check_alerts(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    assert paused_scheduler.get_job("check_price_alerts") is not None

    _register_jobs(
        paused_scheduler, dict(DEFAULT_CONFIG, **{"check_alerts.enabled": False})
    )
    assert paused_scheduler.get_job("check_price_alerts") is None


def test_register_jobs_adds_crypto_when_enabled(
    paused_scheduler: BackgroundScheduler,
) -> None:
    config = dict(DEFAULT_CONFIG, **{
        "ingest_crypto.enabled": True,
        "ingest_crypto.interval_minutes": 30,
    })
    _register_jobs(paused_scheduler, config)
    job = paused_scheduler.get_job("ingest_crypto")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 30 * 60


def test_register_jobs_adds_macro_only_when_fred_key_set(
    paused_scheduler: BackgroundScheduler,
) -> None:
    config = dict(DEFAULT_CONFIG, **{
        "ingest_macro.cron_hour_utc": 9,
        "fred_api_key": "a-key",
    })
    _register_jobs(paused_scheduler, config)
    job = paused_scheduler.get_job("ingest_macro")
    assert job is not None
    # CronTrigger exposes fields; check hour=9.
    assert any(f.name == "hour" and "9" in str(f) for f in job.trigger.fields)


def test_register_jobs_fires_macro_immediately_on_first_add_with_fred_key(
    paused_scheduler: BackgroundScheduler,
) -> None:
    """When ``ingest_macro`` is added for the first time and the user has a
    FRED key, fire immediately so backfill runs within seconds instead of
    waiting up to 24 hours for the cron's natural first fire.
    """
    before = datetime.now(UTC)
    _register_jobs(
        paused_scheduler,
        dict(DEFAULT_CONFIG, **{"fred_api_key": "a-key"}),
    )
    after = datetime.now(UTC)

    job = paused_scheduler.get_job("ingest_macro")
    assert job is not None and job.next_run_time is not None
    window = (before - timedelta(seconds=1), after + timedelta(seconds=1))
    assert window[0] <= job.next_run_time <= window[1], (
        "ingest_macro next_run_time should be ~now on first-add with FRED key"
    )


def test_register_jobs_does_not_refire_macro_on_reconfigure(
    paused_scheduler: BackgroundScheduler,
) -> None:
    """Subsequent registrations (sidecar restart, cron hour change) must not
    reset ``next_run_time`` to now — let the cron's natural schedule win so
    the daily backfill happens at the configured UTC hour, not on every
    scheduler start.
    """
    # First register: seeds the job. Fire-on-first-add means next_run_time=now.
    _register_jobs(
        paused_scheduler,
        dict(DEFAULT_CONFIG, **{"fred_api_key": "a-key"}),
    )
    assert paused_scheduler.get_job("ingest_macro") is not None

    # Second register: job already exists, so we skip next_run_time. The cron
    # trigger's natural next-fire (hour=10 UTC today or tomorrow) should win.
    _register_jobs(
        paused_scheduler,
        dict(
            DEFAULT_CONFIG,
            **{"fred_api_key": "a-key", "ingest_macro.cron_hour_utc": 10},
        ),
    )

    job = paused_scheduler.get_job("ingest_macro")
    assert job is not None and job.next_run_time is not None
    # Cron fires at hour=10, minute=0 every day; next_run_time must match.
    assert job.next_run_time.hour == 10
    assert job.next_run_time.minute == 0


def test_register_jobs_adds_ingest_prices_daily_by_default(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    job = paused_scheduler.get_job("ingest_prices_daily")
    assert job is not None
    assert any(
        f.name == "hour" and "22" in str(f) for f in job.trigger.fields
    )


def test_register_jobs_removes_disabled_ingest_prices_daily(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    assert paused_scheduler.get_job("ingest_prices_daily") is not None

    _register_jobs(
        paused_scheduler,
        dict(DEFAULT_CONFIG, **{"ingest_prices_daily.enabled": False}),
    )
    assert paused_scheduler.get_job("ingest_prices_daily") is None


def test_register_jobs_fires_prices_daily_immediately_on_first_add(
    paused_scheduler: BackgroundScheduler,
) -> None:
    """First-add fire semantics for the daily backfill: a fresh install needs
    the 5y history to land in seconds, not up to 24h later on the configured
    cron hour.
    """
    before = datetime.now(UTC)
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    after = datetime.now(UTC)

    job = paused_scheduler.get_job("ingest_prices_daily")
    assert job is not None and job.next_run_time is not None
    window = (before - timedelta(seconds=1), after + timedelta(seconds=1))
    assert window[0] <= job.next_run_time <= window[1], (
        "ingest_prices_daily next_run_time should be ~now on first-add"
    )


def test_register_jobs_does_not_refire_prices_daily_on_reconfigure(
    paused_scheduler: BackgroundScheduler,
) -> None:
    """On sidecar restart / reconfigure the existing job's schedule wins — we
    don't want to re-fire the 5y download every time the user opens the app.
    """
    # First register — fire-on-first-add means next_run_time=now.
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    assert paused_scheduler.get_job("ingest_prices_daily") is not None

    # Second register — job already exists, so we skip next_run_time.
    _register_jobs(
        paused_scheduler,
        dict(DEFAULT_CONFIG, **{"ingest_prices_daily.cron_hour_utc": 4}),
    )
    job = paused_scheduler.get_job("ingest_prices_daily")
    assert job is not None and job.next_run_time is not None
    # Cron now fires at hour=4, minute=0 daily — natural next-fire must match.
    assert job.next_run_time.hour == 4
    assert job.next_run_time.minute == 0


def test_register_jobs_adds_train_forecasts_by_default(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    job = paused_scheduler.get_job("train_forecasts")
    assert job is not None
    # Weekly cron fires on day_of_week=6 (Sunday) at hour=23 UTC.
    assert any(
        f.name == "day_of_week" and "6" in str(f) for f in job.trigger.fields
    )
    assert any(f.name == "hour" and "23" in str(f) for f in job.trigger.fields)


def test_register_jobs_removes_disabled_train_forecasts(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    assert paused_scheduler.get_job("train_forecasts") is not None

    _register_jobs(
        paused_scheduler,
        dict(DEFAULT_CONFIG, **{"train_forecasts.enabled": False}),
    )
    assert paused_scheduler.get_job("train_forecasts") is None


def test_register_jobs_does_not_fire_train_forecasts_immediately(
    paused_scheduler: BackgroundScheduler,
) -> None:
    """SARIMAX retraining is CPU-intensive and shouldn't slam a fresh install.

    Users who want a forecast right now can hit the "Retrain" button in the
    UI — the weekly cron is explicitly opt-out of fire-on-first-add.
    """
    now = datetime.now(UTC)
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    job = paused_scheduler.get_job("train_forecasts")
    assert job is not None and job.next_run_time is not None
    # The cron's natural next-fire must be >= tomorrow's scheduled hour; it
    # must not land within seconds of "now" like the fire-on-first-add jobs.
    assert job.next_run_time > now + timedelta(minutes=1)


def test_register_jobs_updates_train_forecasts_schedule(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    _register_jobs(
        paused_scheduler,
        dict(
            DEFAULT_CONFIG,
            **{
                "train_forecasts.cron_day_of_week": 0,  # Monday
                "train_forecasts.cron_hour_utc": 5,
            },
        ),
    )
    job = paused_scheduler.get_job("train_forecasts")
    assert job is not None
    assert any(
        f.name == "day_of_week" and "0" in str(f) for f in job.trigger.fields
    )
    assert any(f.name == "hour" and "5" in str(f) for f in job.trigger.fields)


def test_register_jobs_removes_disabled_crypto(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(
        paused_scheduler,
        dict(DEFAULT_CONFIG, **{"ingest_crypto.enabled": True}),
    )
    assert paused_scheduler.get_job("ingest_crypto") is not None

    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    assert paused_scheduler.get_job("ingest_crypto") is None


def test_register_jobs_updates_interval_in_place(
    paused_scheduler: BackgroundScheduler,
) -> None:
    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    assert (
        paused_scheduler.get_job("ingest_prices").trigger.interval.total_seconds()
        == 300
    )

    _register_jobs(
        paused_scheduler,
        dict(DEFAULT_CONFIG, **{"ingest_prices.interval_minutes": 20}),
    )
    assert (
        paused_scheduler.get_job("ingest_prices").trigger.interval.total_seconds()
        == 1200
    )


def test_register_jobs_fires_interval_jobs_immediately_on_first_register(
    paused_scheduler: BackgroundScheduler,
) -> None:
    """Interval jobs must have ``next_run_time`` set to ~now, not now+interval.

    Without this, a freshly-launched sidecar shows an empty dashboard for the
    first 5-15 minutes because ``ingest_prices`` / ``ingest_news`` haven't
    fired yet.
    """
    before = datetime.now(UTC)
    _register_jobs(
        paused_scheduler,
        dict(DEFAULT_CONFIG, **{"ingest_crypto.enabled": True}),
    )
    after = datetime.now(UTC)

    window = (before - timedelta(seconds=1), after + timedelta(seconds=1))
    for job_id in (
        "ingest_prices",
        "ingest_crypto",
        "ingest_news",
        "check_price_alerts",
    ):
        job = paused_scheduler.get_job(job_id)
        assert job is not None and job.next_run_time is not None
        assert window[0] <= job.next_run_time <= window[1], (
            f"{job_id} next_run_time outside expected window"
        )


def test_reconfigure_returns_false_when_scheduler_not_running(
    isolated_db: Path,
) -> None:
    # _scheduler is module-level; tests should never start it, so it stays None.
    assert reconfigure() is False


def test_reconfigure_picks_up_db_updates(
    paused_scheduler: BackgroundScheduler,
) -> None:
    import sidecar.scheduler as sched_mod

    _register_jobs(paused_scheduler, dict(DEFAULT_CONFIG))
    sched_mod._scheduler = paused_scheduler
    try:
        apply_updates(
            {
                "ingest_prices.interval_minutes": 42,
                "ingest_crypto.enabled": True,
            }
        )
        assert reconfigure() is True
        assert (
            paused_scheduler.get_job("ingest_prices").trigger.interval.total_seconds()
            == 42 * 60
        )
        assert paused_scheduler.get_job("ingest_crypto") is not None
    finally:
        sched_mod._scheduler = None
