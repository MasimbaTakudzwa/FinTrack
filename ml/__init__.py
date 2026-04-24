"""FinTrack Phase 2 ML package — local forecasting.

Everything in here runs on the user's machine, no cloud inference. The package
is intentionally small — the entire forecasting pipeline (train, persist,
serve) is three modules:

- `ml.forecast` — pure-compute: fit a SARIMAX model on a series of daily
  closes and return a list of `ForecastPoint`s with 80% / 95% CI bands.
- `ml.persistence` — storage layer: persist the latest forecast per asset,
  load it on demand, delete it when stale.
- `ml.jobs` — scheduler entry + "retrain now" wrapper; orchestrates fetch →
  train → persist for one or many assets.

Importing this package DOES NOT import statsmodels eagerly — the heavy deps
are pulled in only when a forecasting function is actually called, so a
sidecar without `requirements-ml.txt` installed still boots and serves
everything else.
"""

from __future__ import annotations

__all__ = ["forecast", "jobs", "persistence"]
