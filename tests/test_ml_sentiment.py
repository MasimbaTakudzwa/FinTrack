"""VADER sentiment scorer — pure-compute tests.

Covers the public surface of ``ml.sentiment``: ``score_text``,
``score_many``, ``classify``, plus the empty-input short-circuit. We don't
exhaustively test VADER itself (upstream is well-tested) — instead we
verify our wrapper preserves shape, ordering, and edge cases.
"""

from __future__ import annotations

import importlib

import pytest

from ml import sentiment as sentiment_mod
from ml.sentiment import (
    NEGATIVE_THRESHOLD,
    POSITIVE_THRESHOLD,
    SentimentBackendError,
    classify,
)


@pytest.fixture(autouse=True)
def _reset_analyzer_cache() -> None:
    """Force a fresh analyzer per test so monkeypatches stay isolated."""
    importlib.reload(sentiment_mod)


def test_score_text_positive_returns_positive_compound() -> None:
    score = sentiment_mod.score_text("This is a wonderful breakthrough, very exciting!")
    assert score > POSITIVE_THRESHOLD


def test_score_text_negative_returns_negative_compound() -> None:
    score = sentiment_mod.score_text("Tragic accident kills many; devastating losses reported.")
    assert score < NEGATIVE_THRESHOLD


def test_score_text_empty_string_short_circuits_to_zero() -> None:
    assert sentiment_mod.score_text("") == 0.0
    assert sentiment_mod.score_text("   ") == 0.0


def test_score_text_returns_value_in_unit_range() -> None:
    score = sentiment_mod.score_text("absolutely amazing brilliant fantastic")
    assert -1.0 <= score <= 1.0


def test_score_many_preserves_order_and_length() -> None:
    inputs = [
        "great success",
        "",
        "horrible loss",
        "Federal Reserve announces today",
    ]
    scores = sentiment_mod.score_many(inputs)
    assert len(scores) == len(inputs)
    assert scores[0] > 0
    assert scores[1] == 0.0
    assert scores[2] < 0


def test_score_many_empty_input_returns_empty() -> None:
    assert sentiment_mod.score_many([]) == []


def test_classify_positive() -> None:
    assert classify(0.5) == "positive"
    assert classify(POSITIVE_THRESHOLD) == "positive"


def test_classify_negative() -> None:
    assert classify(-0.5) == "negative"
    assert classify(NEGATIVE_THRESHOLD) == "negative"


def test_classify_neutral_window() -> None:
    assert classify(0.0) == "neutral"
    assert classify(0.04) == "neutral"
    assert classify(-0.04) == "neutral"


def test_classify_none_treated_as_neutral() -> None:
    assert classify(None) == "neutral"


def test_lazy_import_failure_raises_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If vaderSentiment isn't installed, ``score_text`` raises the typed error.

    We can't easily uninstall the package mid-suite, but we can simulate the
    import failure by clearing the cached analyzer and patching the import
    inside ``_get_analyzer`` to raise.
    """
    sentiment_mod._analyzer = None

    def _fail() -> object:
        raise SentimentBackendError("simulated missing vaderSentiment")

    monkeypatch.setattr(sentiment_mod, "_get_analyzer", _fail)
    with pytest.raises(SentimentBackendError):
        sentiment_mod.score_text("anything")
