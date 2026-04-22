"""Yahoo Finance RSS news fetcher.

Pulls headlines from `https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}`.
Articles are normalised to `NewsItem` dataclasses; the caller (ingest_news job)
is responsible for dedup by URL and for linking articles to assets.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import requests

logger = logging.getLogger(__name__)

YAHOO_RSS_URL = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline"
    "?s={symbol}&region=US&lang=en-US"
)
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_USER_AGENT = "FinTrack/0.1 (+https://github.com/MasimbaTakudzwa/FinTrack)"
MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 15.0


@dataclass(frozen=True)
class NewsItem:
    url: str
    headline: str
    source: str
    published_at: datetime
    summary: str | None
    symbol: str


class RSSFetcherError(RuntimeError):
    pass


def _backoff_sleep(attempt: int) -> None:
    delay = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
    jitter = random.uniform(0, delay * 0.25)
    time.sleep(delay + jitter)


def _parse_published(entry: Any) -> datetime | None:
    """Extract a UTC datetime from a feedparser entry.

    feedparser surfaces several candidate fields; we try in order:
    `published_parsed` (struct_time), then `updated_parsed`. Naive struct_times
    from Yahoo are already UTC.
    """
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None) or (
            entry.get(attr) if hasattr(entry, "get") else None
        )
        if val is None:
            continue
        try:
            # struct_time has 9 fields; the first 6 are y/m/d/h/m/s.
            return datetime(
                val[0], val[1], val[2], val[3], val[4], val[5], tzinfo=UTC
            )
        except (TypeError, ValueError, IndexError):
            continue
    return None


def _entry_to_item(entry: Any, symbol: str) -> NewsItem | None:
    url = (getattr(entry, "link", None) or "").strip()
    headline = (getattr(entry, "title", None) or "").strip()
    if not url or not headline:
        return None
    published_at = _parse_published(entry)
    if published_at is None:
        return None

    summary_raw = getattr(entry, "summary", None)
    summary = summary_raw.strip() if isinstance(summary_raw, str) and summary_raw.strip() else None

    # Yahoo's feed doesn't expose a per-entry source; use the feed's source or fallback
    source_raw = getattr(entry, "source", None)
    if isinstance(source_raw, dict):
        source = source_raw.get("title") or "Yahoo Finance"
    else:
        source = "Yahoo Finance"

    return NewsItem(
        url=url,
        headline=headline[:512],
        source=source[:128],
        published_at=published_at,
        summary=summary,
        symbol=symbol,
    )


def _http_get(url: str, *, timeout: float) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/rss+xml"},
            )
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "RSS fetch failed for %s (attempt %d/%d): %s",
                url,
                attempt,
                MAX_ATTEMPTS,
                exc,
            )
            if attempt < MAX_ATTEMPTS:
                _backoff_sleep(attempt)
    raise RSSFetcherError(f"RSS fetch failed after {MAX_ATTEMPTS} attempts") from last_exc


def fetch_news_for_symbol(
    symbol: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[NewsItem]:
    """Fetch and normalise Yahoo Finance RSS entries for a single symbol.

    Malformed entries (missing url/headline/timestamp) are silently dropped.
    Raises RSSFetcherError on network failure after retries.
    """
    url = YAHOO_RSS_URL.format(symbol=symbol)
    raw = _http_get(url, timeout=timeout)
    parsed = feedparser.parse(raw)
    entries = getattr(parsed, "entries", []) or []
    items: list[NewsItem] = []
    for entry in entries:
        item = _entry_to_item(entry, symbol)
        if item is not None:
            items.append(item)
    return items


def fetch_news_for_many(
    symbols: Iterable[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[NewsItem]:
    """Fetch news for each symbol, swallowing per-symbol failures.

    A failing feed for one symbol should not abort the whole batch — the
    scheduler will retry on the next tick.
    """
    all_items: list[NewsItem] = []
    for symbol in symbols:
        try:
            items = fetch_news_for_symbol(symbol, timeout=timeout)
        except RSSFetcherError as exc:
            logger.warning("news: skipping %s: %s", symbol, exc)
            continue
        all_items.extend(items)
    return all_items
