# Forecast revamp — uncertainty-first

**Date:** 2026-06-16
**Status:** approved (framing), implementing

## Goal

Make the asset-detail forecast read like a professional trading overlay — upper/lower
tolerances around an internal projection — instead of four thin "random" CI lines,
**without** over-promising predictive power the data can't support.

## Honest framing (drives scope)

A 14-day daily-close point forecast of a liquid equity is, in practice, a random walk:
the best naive prediction is "today's price", and no model reliably beats it on
direction at this horizon. Therefore:

- The **point line** stays the model's output, but we don't chase point accuracy.
- The **uncertainty (band width)** is the real, improvable, honest value — volatility
  clusters and is forecastable, so the cone width comes from realized volatility.
- **Trader channels** (regression, Bollinger) are *descriptive geometry*, labelled as
  trend/volatility context — never as "forecast".
- We **surface accuracy vs. a naive random-walk baseline** so the UI never over-promises.

## Components

### 1. Shaded probability fan (frontend render)
Render the existing `lower_80/upper_80/lower_95/upper_95` bands as a filled cone:
95% as a light fill, 80% as a darker nested fill, dashed median on top. Replaces the
four edge-lines in `CandleChart`. lightweight-charts v5 has no built-in
area-between-two-curves, so use a **custom series primitive** (`ISeriesPrimitive`) that
draws the two polygons + median via the pane's price/time coordinate converters. Only
shown on the daily (3D/1W/All) timeframes, where it already lives.

### 2. Volatility-aware band width (backend, `ml/forecast.py`)
After the engine produces point forecasts, **override** the CI bands with a calibrated,
engine-agnostic cone derived from recent realized volatility:

- daily log-returns of the training closes → EWMA volatility (RiskMetrics λ=0.94).
- h-step std = `ewma_sigma * sqrt(h)` (random-walk scaling, h = 1..horizon).
- `lower/upper_80 = yhat ± 1.2816·std`, `lower/upper_95 = yhat ± 1.9600·std`.

Shared post-processing applied to both SARIMAX and Holt-Winters outputs, so the cone is
consistent and interpretable. Point (`yhat`) and accuracy metrics are unchanged.

### 3. Descriptive trader overlays (frontend, client-computed)
Two new toggleable overlays on the daily chart, computed in the browser from the daily
closes (no backend), clearly labelled as descriptive context:

- **Regression channel** — least-squares trend line through the visible daily closes +
  parallel rails at ±k·(residual std), extended a few bars forward.
- **Bollinger bands** — SMA(20) ± 2σ over the daily closes.

Rendered as plain line series in `CandleChart` (new optional props), gated to multi-day
timeframes like forecast/sentiment, each with its own toggle button + tooltip.

### 4. Accuracy vs. random-walk baseline (backend + frontend)
- `ml/accuracy.py`: alongside the model's rolling MAPE / directional accuracy, compute
  the **naive random-walk baseline** (predict last close for the whole horizon) over the
  same evaluation snapshots, and a "skill" delta.
- `ForecastAccuracyPanel`: show model vs. naive side by side + a plain-language verdict
  (e.g. "about as accurate as assuming no change") so the projection is never oversold.

## Out of scope (deliberately)
- Sentiment- or macro-fed point models (no out-of-sample edge on daily returns).
- Heavier ML models chasing point accuracy.
- Monte Carlo scenario "spaghetti" paths — nice-to-have, deferred; the shaded fan already
  conveys the same uncertainty more cleanly.

## Files touched
- `ml/forecast.py` — EWMA-vol band post-processing (`_apply_volatility_bands`).
- `ml/accuracy.py` + `sidecar/api/forecast.py` (or analytics) — naive baseline in the
  accuracy payload.
- `shell/src/components/CandleChart.tsx` — forecast-fan primitive; regression/Bollinger
  line series.
- `shell/src/pages/AssetDetail.tsx` — compute TA overlays from daily series; toggles.
- `shell/src/components/ForecastAccuracyPanel.tsx` — model-vs-naive display.
- Tests: `tests/test_ml_forecast.py` (band calibration), `tests/test_ml_accuracy.py`
  (naive baseline), and shell lint/build.

## Testing / verification
- Bands widen with `sqrt(h)` and scale with realized vol; 95% wider than 80%; symmetric
  about `yhat`. Unit-tested on synthetic low- vs high-vol series.
- Naive-baseline accuracy computed on the same snapshots as the model; delta sign correct.
- `pytest` green, `ruff` + `mypy --strict` (sidecar + ml) clean, shell `lint` + `build`
  clean. Rebuild `.dmg`, copy to `~/Downloads`, push `main`.
