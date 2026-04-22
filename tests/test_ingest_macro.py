from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from sidecar.config import settings as cfg
from sidecar.db.engine import session_scope
from sidecar.db.models import MacroDataPoint, MacroIndicator
from sidecar.ingestion.fred_fetcher import MacroPoint


def _seed_indicators() -> None:
    with session_scope() as s:
        s.add(
            MacroIndicator(
                series_id="CPIAUCSL",
                name="CPI",
                description="desc",
                units="idx",
                frequency="monthly",
            )
        )
        s.add(
            MacroIndicator(
                series_id="UNRATE",
                name="Unemployment",
                description="desc",
                units="%",
                frequency="monthly",
            )
        )


def test_ingest_macro_skips_when_no_api_key(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "fred_api_key", None)
    from sidecar.scheduler import jobs

    called = {"n": 0}

    def fake_fetch(series_ids: list[str], api_key: str) -> list[MacroPoint]:
        called["n"] += 1
        return []

    monkeypatch.setattr(jobs, "fetch_macro_series_many", fake_fetch)

    assert jobs.ingest_macro() == 0
    assert called["n"] == 0


def test_ingest_macro_inserts_points(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_indicators()
    monkeypatch.setattr(cfg, "fred_api_key", "fake-key")

    from sidecar.scheduler import jobs

    def fake_fetch(series_ids: list[str], api_key: str) -> list[MacroPoint]:
        return [
            MacroPoint("CPIAUCSL", date(2026, 1, 1), Decimal("300.1")),
            MacroPoint("CPIAUCSL", date(2026, 2, 1), Decimal("301.2")),
            MacroPoint("UNRATE", date(2026, 1, 1), Decimal("4.1")),
        ]

    monkeypatch.setattr(jobs, "fetch_macro_series_many", fake_fetch)

    inserted = jobs.ingest_macro()
    assert inserted == 3

    with session_scope() as s:
        rows = s.execute(select(MacroDataPoint)).scalars().all()
        assert len(rows) == 3


def test_ingest_macro_is_idempotent(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_indicators()
    monkeypatch.setattr(cfg, "fred_api_key", "fake-key")

    from sidecar.scheduler import jobs

    points = [
        MacroPoint("CPIAUCSL", date(2026, 1, 1), Decimal("300.1")),
        MacroPoint("UNRATE", date(2026, 1, 1), Decimal("4.1")),
    ]
    monkeypatch.setattr(jobs, "fetch_macro_series_many", lambda ids, key: list(points))

    first = jobs.ingest_macro()
    second = jobs.ingest_macro()
    assert first == 2
    assert second == 0


def test_ingest_macro_drops_unknown_series(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_indicators()
    monkeypatch.setattr(cfg, "fred_api_key", "fake-key")

    from sidecar.scheduler import jobs

    def fake_fetch(series_ids: list[str], api_key: str) -> list[MacroPoint]:
        return [
            MacroPoint("CPIAUCSL", date(2026, 1, 1), Decimal("300.1")),
            MacroPoint("GHOST", date(2026, 1, 1), Decimal("999")),
        ]

    monkeypatch.setattr(jobs, "fetch_macro_series_many", fake_fetch)

    inserted = jobs.ingest_macro()
    assert inserted == 1


def test_ingest_macro_with_no_indicators(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "fred_api_key", "fake-key")

    from sidecar.scheduler import jobs

    called = {"n": 0}

    def fake_fetch(series_ids: list[str], api_key: str) -> list[MacroPoint]:
        called["n"] += 1
        return []

    monkeypatch.setattr(jobs, "fetch_macro_series_many", fake_fetch)

    assert jobs.ingest_macro() == 0
    assert called["n"] == 0


def test_ingest_macro_chunks_large_backfills(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first-run FRED backfill can exceed SQLite's 32766-variable statement
    limit — monthly CPI since 1947 plus daily DGS10 since 1962 is already
    ~17k rows at 3 cols each = ~51k bind params. The job must chunk the bulk
    insert so this succeeds end-to-end on real data, not only on tiny fixtures.
    """
    _seed_indicators()
    monkeypatch.setattr(cfg, "fred_api_key", "fake-key")

    from sidecar.scheduler import jobs

    # 1200 CPI points is well past any sensible single-statement cap;
    # paired with UNRATE points it forces multiple chunks through the loop.
    cpi_points = [
        MacroPoint(
            "CPIAUCSL",
            date(2000 + (i // 12), (i % 12) + 1, 1),
            Decimal("100") + Decimal(i) / Decimal("10"),
        )
        for i in range(1200)
    ]
    unrate_points = [
        MacroPoint(
            "UNRATE",
            date(2000 + (i // 12), (i % 12) + 1, 1),
            Decimal("4.0"),
        )
        for i in range(50)
    ]
    monkeypatch.setattr(
        jobs,
        "fetch_macro_series_many",
        lambda ids, key: cpi_points + unrate_points,
    )

    inserted = jobs.ingest_macro()
    assert inserted == 1250

    with session_scope() as s:
        rows = s.execute(select(MacroDataPoint)).scalars().all()
        assert len(rows) == 1250
