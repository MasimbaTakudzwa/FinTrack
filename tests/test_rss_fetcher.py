"""Tests for sidecar.ingestion.rss_fetcher.

We never hit the network — ``_http_get`` is monkeypatched to return sample
RSS bytes, and feedparser handles the XML parsing as it would in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sidecar.ingestion import rss_fetcher
from sidecar.ingestion.rss_fetcher import (
    RSSFetcherError,
    fetch_news_for_many,
    fetch_news_for_symbol,
)


def _rss_bytes(items: list[dict[str, str]]) -> bytes:
    entries = "\n".join(
        f"""
        <item>
          <title>{i['title']}</title>
          <link>{i['link']}</link>
          <pubDate>{i['pub']}</pubDate>
          <description>{i.get('summary', '')}</description>
        </item>"""
        for i in items
    )
    return (
        f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Yahoo Finance - AAPL</title>
    <link>https://finance.yahoo.com/quote/AAPL</link>
    <description>Latest news for AAPL</description>
    {entries}
  </channel>
</rss>"""
    ).encode()


def test_fetch_news_for_symbol_parses_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _rss_bytes(
        [
            {
                "title": "Apple beats earnings expectations",
                "link": "https://example.com/story/1",
                "pub": "Wed, 22 Apr 2026 12:30:00 GMT",
                "summary": "Apple posted strong Q2 results.",
            },
            {
                "title": "Apple unveils new iPhone",
                "link": "https://example.com/story/2",
                "pub": "Wed, 22 Apr 2026 14:00:00 GMT",
                "summary": "Announced at the spring event.",
            },
        ]
    )

    def _fake_http_get(url: str, *, timeout: float) -> bytes:
        assert "AAPL" in url
        return sample

    monkeypatch.setattr(rss_fetcher, "_http_get", _fake_http_get)

    items = fetch_news_for_symbol("AAPL")
    assert len(items) == 2
    assert items[0].symbol == "AAPL"
    assert items[0].url == "https://example.com/story/1"
    assert items[0].headline == "Apple beats earnings expectations"
    assert items[0].source == "Yahoo Finance"
    assert items[0].published_at == datetime(2026, 4, 22, 12, 30, tzinfo=UTC)
    assert items[0].summary == "Apple posted strong Q2 results."


def test_fetch_news_for_symbol_skips_entries_missing_critical_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _rss_bytes(
        [
            # missing link
            {"title": "Headline with no URL", "link": "", "pub": "Wed, 22 Apr 2026 12:00:00 GMT"},
            # missing pubDate — feedparser won't populate published_parsed
            {"title": "Headline no date", "link": "https://example.com/x", "pub": ""},
            # valid
            {
                "title": "Valid story",
                "link": "https://example.com/valid",
                "pub": "Wed, 22 Apr 2026 12:30:00 GMT",
            },
        ]
    )
    monkeypatch.setattr(rss_fetcher, "_http_get", lambda url, *, timeout: sample)

    items = fetch_news_for_symbol("AAPL")
    assert len(items) == 1
    assert items[0].url == "https://example.com/valid"


def test_fetch_news_for_symbol_truncates_long_headline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_title = "x" * 700
    sample = _rss_bytes(
        [
            {
                "title": long_title,
                "link": "https://example.com/long",
                "pub": "Wed, 22 Apr 2026 12:30:00 GMT",
            }
        ]
    )
    monkeypatch.setattr(rss_fetcher, "_http_get", lambda url, *, timeout: sample)

    items = fetch_news_for_symbol("AAPL")
    assert len(items) == 1
    assert len(items[0].headline) == 512


def test_fetch_news_for_symbol_raises_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rss_fetcher, "_backoff_sleep", lambda attempt: None)

    calls = {"n": 0}

    def _boom(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        raise ConnectionError("boom")

    # Patch requests.get so _http_get exercises its retry loop.
    import requests

    monkeypatch.setattr(requests, "get", _boom)

    with pytest.raises(RSSFetcherError):
        fetch_news_for_symbol("AAPL", timeout=0.01)
    assert calls["n"] == rss_fetcher.MAX_ATTEMPTS


def test_fetch_news_for_many_swallows_per_symbol_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _selective_fetch(symbol: str, *, timeout: float) -> list[rss_fetcher.NewsItem]:
        if symbol == "BROKEN":
            raise RSSFetcherError("fetch failed")
        return [
            rss_fetcher.NewsItem(
                url=f"https://example.com/{symbol}",
                headline=f"{symbol} story",
                source="Yahoo Finance",
                published_at=datetime(2026, 4, 22, 12, 30, tzinfo=UTC),
                summary=None,
                symbol=symbol,
            )
        ]

    monkeypatch.setattr(rss_fetcher, "fetch_news_for_symbol", _selective_fetch)

    items = fetch_news_for_many(["AAPL", "BROKEN", "MSFT"])
    assert {i.symbol for i in items} == {"AAPL", "MSFT"}
    assert len(items) == 2
