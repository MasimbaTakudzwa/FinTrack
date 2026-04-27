"""Sentiment scoring jobs — `score_articles` and `score_article_ids`.

We run VADER for real here (it's fast enough that mocking adds noise without
saving meaningful time) and seed a small `Article` corpus per test. Coverage:

* New articles are scored on first call
* Already-scored articles are not re-scored (idempotency)
* `score_article_ids` only touches the requested IDs
* When the VADER backend is unavailable, both jobs return 0 cleanly without
  partial writes (so the periodic backfill picks up the work next tick).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ml.jobs import score_article_ids, score_articles
from ml.sentiment import SentimentBackendError
from sidecar.db.engine import session_scope
from sidecar.db.models import Article


def _add_article(
    *,
    url: str,
    headline: str,
    sentiment: float | None = None,
) -> int:
    with session_scope() as s:
        article = Article(
            url=url,
            headline=headline,
            source="Test",
            published_at=datetime.now(UTC),
            sentiment=sentiment,
        )
        s.add(article)
        s.flush()
        return article.id


def test_score_articles_scores_unscored_rows(isolated_db: Path) -> None:
    aid_pos = _add_article(url="https://t/1", headline="Outstanding earnings beat")
    aid_neg = _add_article(url="https://t/2", headline="Catastrophic loss reported")
    aid_neu = _add_article(url="https://t/3", headline="Reuters publishes report")

    assert score_articles() == 3

    with session_scope() as s:
        rows = {a.id: a.sentiment for a in s.query(Article).all()}
    pos = rows[aid_pos]
    neg = rows[aid_neg]
    assert pos is not None and pos > 0
    assert neg is not None and neg < 0
    assert rows[aid_neu] is not None  # neutralish


def test_score_articles_is_idempotent(isolated_db: Path) -> None:
    _add_article(url="https://t/1", headline="Wonderful achievement")
    assert score_articles() == 1
    # Second run finds no unscored rows.
    assert score_articles() == 0


def test_score_articles_skips_already_scored(isolated_db: Path) -> None:
    pre = _add_article(
        url="https://t/pre", headline="Anything", sentiment=0.42
    )
    fresh = _add_article(url="https://t/new", headline="Tragic disaster")

    assert score_articles() == 1  # only the unscored one

    with session_scope() as s:
        rows = {a.id: a.sentiment for a in s.query(Article).all()}
    # Pre-scored value preserved exactly
    assert rows[pre] == 0.42
    fresh_score = rows[fresh]
    assert fresh_score is not None and fresh_score < 0


def test_score_articles_empty_corpus_returns_zero(isolated_db: Path) -> None:
    assert score_articles() == 0


def test_score_article_ids_scores_only_requested(isolated_db: Path) -> None:
    aid_a = _add_article(url="https://t/a", headline="Wonderful")
    aid_b = _add_article(url="https://t/b", headline="Tragic")
    aid_c = _add_article(url="https://t/c", headline="Neutral wire report")

    assert score_article_ids([aid_a, aid_c]) == 2

    with session_scope() as s:
        rows = {a.id: a.sentiment for a in s.query(Article).all()}
    assert rows[aid_a] is not None
    assert rows[aid_c] is not None
    assert rows[aid_b] is None  # not requested → still null


def test_score_article_ids_empty_input_returns_zero(isolated_db: Path) -> None:
    assert score_article_ids([]) == 0


def test_score_article_ids_unknown_id_silently_ignored(isolated_db: Path) -> None:
    # No articles seeded; passing IDs that don't exist must not blow up.
    assert score_article_ids([999, 1000]) == 0


def test_score_articles_backend_unavailable_returns_zero(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_article(url="https://t/1", headline="Anything")

    def _raise(_texts: object) -> object:
        raise SentimentBackendError("simulated missing backend")

    # Patch where ml.jobs imports score_many (not the source module).
    monkeypatch.setattr("ml.jobs.score_many", _raise)
    assert score_articles() == 0
    # Row remains unscored — periodic backfill will pick it up next tick.
    with session_scope() as s:
        row = s.query(Article).first()
        assert row is not None
        assert row.sentiment is None


def test_score_article_ids_backend_unavailable_returns_zero(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aid = _add_article(url="https://t/1", headline="Anything")

    def _raise(_texts: object) -> object:
        raise SentimentBackendError("simulated missing backend")

    monkeypatch.setattr("ml.jobs.score_many", _raise)
    assert score_article_ids([aid]) == 0
    with session_scope() as s:
        row = s.query(Article).first()
        assert row is not None
        assert row.sentiment is None
