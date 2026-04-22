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
