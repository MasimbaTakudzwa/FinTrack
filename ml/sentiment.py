"""VADER-based sentiment scoring for news headlines.

VADER (Valence Aware Dictionary and sEntiment Reasoner) is a rule-based
sentiment analyzer tuned for short social-media-style text — exactly the
shape of news headlines we're scoring. It's a great fit for FinTrack
because:

* It's pure-Python and ships its own lexicon — no NLTK download, no model
  weights to host, no GPU.
* It produces a single ``compound`` score in ``[-1.0, +1.0]`` per text,
  which slots cleanly into a ``Float`` column and into UI bands
  (positive / neutral / negative).
* It scores >5000 headlines/second on a single CPU thread — backfilling our
  ~hundred-articles-per-day corpus is essentially free.
* Fully on-device; no headlines leave the user's machine.

We deliberately avoid a transformer-based model (FinBERT, distil-roberta-
sst2, etc.) for Phase 2's first cut — the size, RAM, and warm-up cost
aren't justified yet for the headline-only use case. Future work could
swap the ``score_text`` implementation behind ``ml.sentiment`` without
touching the API or scheduler layers.

Boundaries (mirrors ``ml.forecast`` style):
- ``score_text(text)`` — single-shot, returns a compound score.
- ``score_many(texts)`` — batch helper, same output shape.
- ``classify(score)`` — translate compound score → "positive"/"neutral"/
  "negative" using VADER's own conventional thresholds.

statsmodels-style lazy import: ``vaderSentiment`` is in ``requirements-ml.txt``
only, so importing this module won't blow up a sidecar without the ML deps
installed — the analyzer is constructed on first ``score_*`` call.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


# Conventional VADER classification thresholds — see the upstream README:
# https://github.com/cjhutto/vaderSentiment#about-the-scoring
POSITIVE_THRESHOLD: float = 0.05
NEGATIVE_THRESHOLD: float = -0.05


# Module-level analyzer cache. The constructor reads + parses VADER's lexicon
# (~30 KB of text), which is cheap but unnecessary to redo on every call.
_analyzer: Any = None
_analyzer_lock = Lock()


class SentimentBackendError(RuntimeError):
    """Raised when the VADER backend isn't installed or fails to load."""


def _get_analyzer() -> Any:
    global _analyzer
    if _analyzer is not None:
        return _analyzer
    with _analyzer_lock:
        if _analyzer is not None:
            return _analyzer
        try:
            # Lazy import — vaderSentiment is in requirements-ml.txt; importing
            # this module without ML deps installed should not raise.
            from vaderSentiment.vaderSentiment import (  # type: ignore[import-untyped]
                SentimentIntensityAnalyzer,
            )
        except ImportError as exc:
            raise SentimentBackendError(
                "vaderSentiment is not installed; run "
                "`pip install -r requirements-ml.txt`"
            ) from exc
        _analyzer = SentimentIntensityAnalyzer()
        return _analyzer


def score_text(text: str) -> float:
    """Return VADER's compound score for a single string.

    Empty / whitespace-only inputs return 0.0 (treated as neutral) without
    invoking the analyzer — VADER itself handles the case correctly but
    the short-circuit saves a tiny bit of overhead during backfills of
    sparse corpora.
    """
    if not text or not text.strip():
        return 0.0
    analyzer = _get_analyzer()
    scores: dict[str, float] = analyzer.polarity_scores(text)
    return float(scores.get("compound", 0.0))


def score_many(texts: Sequence[str]) -> list[float]:
    """Score a batch of strings, preserving order. Empty strings → 0.0."""
    if not texts:
        return []
    # Loading the analyzer is the expensive part; pull it once outside the
    # loop. The per-text scoring itself is pure Python rule matching and
    # already very fast (>5K headlines/sec on a single thread).
    analyzer = _get_analyzer()
    out: list[float] = []
    for text in texts:
        if not text or not text.strip():
            out.append(0.0)
            continue
        scores: dict[str, float] = analyzer.polarity_scores(text)
        out.append(float(scores.get("compound", 0.0)))
    return out


def classify(score: float | None) -> str:
    """Bucket a compound score into 'positive' / 'neutral' / 'negative'.

    ``None`` (article hasn't been scored yet) is treated as 'neutral' for UI
    purposes — the caller can use ``score is None`` separately if the UI
    distinguishes "unscored" from "scored neutral".
    """
    if score is None:
        return "neutral"
    if score >= POSITIVE_THRESHOLD:
        return "positive"
    if score <= NEGATIVE_THRESHOLD:
        return "negative"
    return "neutral"
