"""FinTrack Phase 2 ML package — local forecasting + sentiment + accuracy.

Everything in here runs on the user's machine, no cloud inference. The
package stays small and tightly scoped:

Forecasting:
- `ml.forecast` — pure-compute: fit SARIMAX or Holt-Winters (ETS) on a
  series of daily closes and return a list of `ForecastPoint`s with
  80% / 95% CI bands.
- `ml.persistence` — storage layer: persist the latest forecast per asset
  (single-row hot path) AND append every save into ``forecast_snapshots``
  for accuracy tracking.
- `ml.jobs` — scheduler entry + "retrain now" wrapper; orchestrates fetch →
  train → persist for one or many assets, plus the sentiment backfill job.
- `ml.accuracy` — pure-compute: turn forecast snapshots + actual closes
  into MAPE / RMSE / directional metrics, broken out per engine so the
  user can see which model fits their data better.

Sentiment:
- `ml.sentiment` — VADER-based headline scorer; returns a compound score in
  ``[-1, +1]``. Used by ``ingest_news`` (inline) and the
  ``score_articles`` backfill (batch).

Importing this package DOES NOT import statsmodels or vaderSentiment
eagerly — the heavy deps are pulled in only when an ML function is
actually called, so a sidecar without `requirements-ml.txt` installed
still boots and serves everything else.
"""

from __future__ import annotations

__all__ = ["accuracy", "forecast", "jobs", "persistence", "sentiment"]
