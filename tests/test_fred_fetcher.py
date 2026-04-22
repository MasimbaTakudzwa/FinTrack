from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from sidecar.ingestion import fred_fetcher
from sidecar.ingestion.fred_fetcher import (
    FetcherError,
    fetch_macro_series,
    fetch_macro_series_many,
)


class _FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400 and self.status_code != 429:
            raise AssertionError("unexpected raise_for_status call")


def test_fetch_macro_series_parses_observations(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "observations": [
            {"date": "2026-01-01", "value": "300.1"},
            {"date": "2026-02-01", "value": "301.3"},
            {"date": "2026-03-01", "value": "."},  # FRED sentinel for missing
            {"date": "", "value": "5.0"},
            {"date": "2026-04-01", "value": "302.0"},
        ]
    }
    monkeypatch.setattr(
        fred_fetcher.requests,
        "get",
        lambda url, params, timeout: _FakeResp(200, payload),
    )
    points = fetch_macro_series("CPIAUCSL", "fake-key")
    assert len(points) == 3
    assert points[0].date == date(2026, 1, 1)
    assert points[0].value == Decimal("300.1")
    assert points[2].date == date(2026, 4, 1)


def test_fetch_macro_series_with_date_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> Any:
        captured["params"] = params
        return _FakeResp(200, {"observations": []})

    monkeypatch.setattr(fred_fetcher.requests, "get", fake_get)
    fetch_macro_series(
        "CPIAUCSL",
        "fake-key",
        observation_start=date(2025, 1, 1),
        observation_end=date(2026, 1, 1),
    )
    assert captured["params"]["observation_start"] == "2025-01-01"
    assert captured["params"]["observation_end"] == "2026-01-01"
    assert captured["params"]["api_key"] == "fake-key"
    assert captured["params"]["file_type"] == "json"


def test_fetch_macro_series_raises_on_non_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fred_fetcher.requests,
        "get",
        lambda url, params, timeout: _FakeResp(200, ["not", "a", "dict"]),
    )
    with pytest.raises(FetcherError):
        fetch_macro_series("CPIAUCSL", "fake-key")


def test_fetch_macro_series_many_skips_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(
        series_id: str,
        api_key: str,
        *,
        observation_start: date | None = None,
        observation_end: date | None = None,
    ) -> list[fred_fetcher.MacroPoint]:
        if series_id == "BAD":
            raise FetcherError("boom")
        return [
            fred_fetcher.MacroPoint(
                series_id=series_id, date=date(2026, 1, 1), value=Decimal("1.0")
            )
        ]

    monkeypatch.setattr(fred_fetcher, "fetch_macro_series", fake_fetch)
    points = fetch_macro_series_many(["GOOD1", "BAD", "GOOD2"], "fake-key")
    assert {p.series_id for p in points} == {"GOOD1", "GOOD2"}


def test_http_get_retries_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> Any:
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResp(429, None)
        return _FakeResp(200, {"observations": []})

    monkeypatch.setattr(fred_fetcher.requests, "get", fake_get)
    monkeypatch.setattr(fred_fetcher, "_backoff_sleep", lambda attempt: None)
    data = fred_fetcher._http_get("http://fake", {"k": "v"})
    assert calls["n"] == 3
    assert data == {"observations": []}
