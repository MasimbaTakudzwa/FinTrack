from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from sidecar.ingestion.yfinance_fetcher import FetcherError

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


@dataclass(frozen=True)
class MacroPoint:
    series_id: str
    date: date
    value: Decimal


def _backoff_sleep(attempt: int) -> None:
    delay = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
    jitter = random.uniform(0, delay * 0.25)
    time.sleep(delay + jitter)


def _http_get(url: str, params: dict[str, Any]) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 429:
                logger.warning("FRED rate-limited (attempt %d/%d)", attempt, MAX_ATTEMPTS)
                if attempt < MAX_ATTEMPTS:
                    _backoff_sleep(attempt)
                    continue
                raise FetcherError(f"FRED rate-limited after {MAX_ATTEMPTS} attempts")
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "FRED request failed (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, exc
            )
            if attempt < MAX_ATTEMPTS:
                _backoff_sleep(attempt)
    raise FetcherError(f"FRED request failed after {MAX_ATTEMPTS} attempts") from last_exc


def fetch_macro_series(
    series_id: str,
    api_key: str,
    *,
    observation_start: date | None = None,
    observation_end: date | None = None,
) -> list[MacroPoint]:
    """Fetch observations for a single FRED series.

    Returns points with parsed date + Decimal value. Missing/invalid entries
    (FRED marks them with ".") are skipped.
    """
    params: dict[str, Any] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    }
    if observation_start is not None:
        params["observation_start"] = observation_start.isoformat()
    if observation_end is not None:
        params["observation_end"] = observation_end.isoformat()

    data = _http_get(FRED_BASE, params)
    if not isinstance(data, dict):
        raise FetcherError(f"FRED returned non-dict payload for {series_id}: {type(data)!r}")

    observations = data.get("observations") or []
    if not isinstance(observations, list):
        raise FetcherError(f"FRED {series_id}: observations is not a list")

    points: list[MacroPoint] = []
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        raw_date = obs.get("date")
        raw_value = obs.get("value")
        if not raw_date or raw_value in (None, "", "."):
            continue
        try:
            parsed_date = date.fromisoformat(str(raw_date))
            parsed_value = Decimal(str(raw_value))
        except (ValueError, InvalidOperation):
            continue
        points.append(MacroPoint(series_id=series_id, date=parsed_date, value=parsed_value))
    return points


def fetch_macro_series_many(
    series_ids: Iterable[str],
    api_key: str,
    *,
    observation_start: date | None = None,
    observation_end: date | None = None,
) -> list[MacroPoint]:
    """Fetch observations for multiple FRED series, skipping failures."""
    all_points: list[MacroPoint] = []
    seen: set[str] = set()
    for sid in series_ids:
        if sid in seen:
            continue
        seen.add(sid)
        try:
            all_points.extend(
                fetch_macro_series(
                    sid,
                    api_key,
                    observation_start=observation_start,
                    observation_end=observation_end,
                )
            )
        except FetcherError as exc:
            logger.warning("Skipping FRED series %s: %s", sid, exc)
    return all_points
