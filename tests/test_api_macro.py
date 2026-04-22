from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import MacroDataPoint, MacroIndicator
from sidecar.main import app


def _seed_series() -> None:
    with session_scope() as s:
        ind_cpi = MacroIndicator(
            series_id="CPIAUCSL",
            name="CPI",
            description="desc",
            units="idx",
            frequency="monthly",
        )
        ind_gdp = MacroIndicator(
            series_id="GDP",
            name="GDP",
            description="desc",
            units="$B",
            frequency="quarterly",
            is_active=False,
        )
        s.add(ind_cpi)
        s.add(ind_gdp)
        s.flush()

        for i, day in enumerate(
            [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]
        ):
            s.add(
                MacroDataPoint(
                    indicator_id=ind_cpi.id,
                    date=day,
                    value=Decimal("300.0") + Decimal(i),
                )
            )


def test_list_indicators_returns_active_only_by_default(isolated_db: Path) -> None:
    _seed_series()
    client = TestClient(app)
    r = client.get("/api/macro/")
    assert r.status_code == 200
    series = [row["series_id"] for row in r.json()]
    assert series == ["CPIAUCSL"]

    r_all = client.get("/api/macro/?active_only=false")
    assert {row["series_id"] for row in r_all.json()} == {"CPIAUCSL", "GDP"}


def test_get_series_unknown_returns_404(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.get("/api/macro/NOPE/")
    assert r.status_code == 404


def test_get_series_returns_points_ascending(isolated_db: Path) -> None:
    _seed_series()
    client = TestClient(app)
    r = client.get("/api/macro/CPIAUCSL/")
    assert r.status_code == 200
    body = r.json()
    assert body["series_id"] == "CPIAUCSL"
    assert body["count"] == 4
    dates = [p["date"] for p in body["points"]]
    assert dates == sorted(dates)


def test_get_series_limit_returns_most_recent(isolated_db: Path) -> None:
    _seed_series()
    client = TestClient(app)
    r = client.get("/api/macro/CPIAUCSL/", params={"limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["points"][0]["date"] == "2026-03-01"
    assert body["points"][-1]["date"] == "2026-04-01"


def test_get_series_date_range(isolated_db: Path) -> None:
    _seed_series()
    client = TestClient(app)
    r = client.get(
        "/api/macro/CPIAUCSL/",
        params={"from": "2026-02-01", "to": "2026-03-01"},
    )
    assert r.status_code == 200
    body = r.json()
    dates = [p["date"] for p in body["points"]]
    assert dates == ["2026-02-01", "2026-03-01"]


def test_series_id_is_case_insensitive(isolated_db: Path) -> None:
    _seed_series()
    client = TestClient(app)
    r = client.get("/api/macro/cpiaucsl/")
    assert r.status_code == 200
    assert r.json()["series_id"] == "CPIAUCSL"
