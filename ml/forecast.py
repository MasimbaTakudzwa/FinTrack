"""Daily-close forecaster — SARIMAX + ETS (Holt-Winters) engines.

Why two engines? SARIMAX is a strong default for financial daily closes —
it handles non-stationarity via differencing and pulls short-term
autocorrelation from `(p,q) = (1,1)`. But for assets that are mostly
trend-and-noise without much short-term autocorrelation (commodity ETFs,
broad-market indexes), ETS-style exponential smoothing produces
empirically tighter bands at the cost of being slower to adapt to regime
changes. Letting the user pick — and persisting which engine fitted the
current row — gives them visibility into the trade-off without forcing
us to pick one.

Both engines share:
- raw-price fits (no log transform — keeps CIs in price-space and avoids
  re-expression bias).
- `enforce_stationarity=False, enforce_invertibility=False` for SARIMAX so
  noisy series don't cause optimiser hangs; the CI bands widen rather than
  the fit failing outright.
- `MIN_TRAINING_ROWS = 60` floor — both engines are too noisy below that.
- Calendar-day forecast horizon (we don't apply a trading calendar). Stocks
  show weekend bars informationally; crypto's 24/7 market means calendar
  days == trading days. A future iteration can fold in `pandas_market_calendars`.

The forecaster is horizon-days *calendar* forward — we do not apply a trading
calendar. For stocks this means "next 14 days" includes weekends; users see
them on the chart but the bars are informational only. For crypto (24/7)
calendar days == trading days, so this simplification has no downside.

Inputs:
- `closes`: sequence of `(date, float_close)` tuples, ordered oldest-first,
  no gaps on trading days. If the caller hands us PricePoint rows tagged
  `interval="1d"` in ascending order this is exactly what we need.
- `horizon_days`: default 14 (user's explicit request in project settings).
- `engine`: `"sarimax"` (default) or `"holt_winters"` (ETS).

Output: a `ForecastResult` wrapping a list of `ForecastPoint` rows with
80% and 95% CI bands pulled straight from the model's `conf_int()` call at
alpha=0.20 and alpha=0.05 respectively.
"""

from __future__ import annotations

import itertools
import logging
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, TypeAlias

logger = logging.getLogger(__name__)

# Training data floor. Both engines on ~60 rows give a noisy fit but
# converge; below this the likelihood surface is too flat to trust. The
# 5y daily backfill normally yields ~1250 rows per stock / ~1825 per crypto,
# so this only trips on newly-added assets whose backfill is still running.
MIN_TRAINING_ROWS = 60

# Engine identifier — UI uses these as the canonical names. Persisted
# verbatim into `Forecast.model` (alongside the parameter signature) so the
# caption can show "SARIMAX(1,1,1) · trained 2h ago" or
# "Holt-Winters (ETS A,A,N) · trained 2h ago" without re-deriving from
# config.
ForecastEngine: TypeAlias = Literal["sarimax", "holt_winters"]
ENGINES: tuple[ForecastEngine, ...] = ("sarimax", "holt_winters")
DEFAULT_ENGINE: ForecastEngine = "sarimax"

# Display names persisted in `Forecast.model`. Used in the UI caption.
SARIMAX_MODEL_NAME = "SARIMAX(1,1,1)"
HOLT_WINTERS_MODEL_NAME = "Holt-Winters (ETS A,A,N)"

# Backwards-compat alias for callers / tests written against the original
# single-engine API. New code should import ``SARIMAX_MODEL_NAME``.
MODEL_NAME = SARIMAX_MODEL_NAME


class ForecastError(Exception):
    """Base class for forecast-pipeline errors."""


class InsufficientDataError(ForecastError):
    """Raised when the input series has fewer than MIN_TRAINING_ROWS closes."""


class ForecastFitError(ForecastError):
    """Raised when statsmodels fails to fit the chosen engine."""


@dataclass(frozen=True)
class ForecastPoint:
    """One day of forecast output.

    `yhat` is the model's best-estimate median close; the two `lower`/`upper`
    pairs are central-CI bands at the named confidence levels, pulled from
    the engine's likelihood (Gaussian for SARIMAX; Gaussian-on-residuals for
    ETS additive). Values can be negative in theory when the 95% band blows
    past zero — the UI clamps the display at zero but the raw numbers are
    kept truthful so post-hoc metrics don't silently lie.
    """

    forecast_date: date
    yhat: float
    lower_80: float
    upper_80: float
    lower_95: float
    upper_95: float


@dataclass(frozen=True)
class ForecastResult:
    """Everything the API / UI needs to render + caption a forecast."""

    model: str
    horizon_days: int
    training_rows: int
    last_close: Decimal
    last_close_date: date
    generated_at: datetime
    points: list[ForecastPoint] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation shared by both engines
# ---------------------------------------------------------------------------


def _validate_inputs(
    closes: Sequence[tuple[date, float]], horizon_days: int
) -> tuple[list[date], list[float]]:
    if horizon_days < 1 or horizon_days > 90:
        raise ForecastError(f"horizon_days must be 1..90, got {horizon_days}")
    if len(closes) < MIN_TRAINING_ROWS:
        raise InsufficientDataError(
            f"need at least {MIN_TRAINING_ROWS} closes to train, "
            f"got {len(closes)}"
        )

    dates = [d for d, _ in closes]
    for prev, curr in itertools.pairwise(dates):
        if curr <= prev:
            raise ForecastError(
                f"closes must be strictly ascending by date "
                f"(found {prev} followed by {curr})"
            )

    values = [float(c) for _, c in closes]
    return dates, values


# ---------------------------------------------------------------------------
# SARIMAX engine
# ---------------------------------------------------------------------------


def _import_sarimax() -> Any:
    """Lazy import so a sidecar without `requirements-ml.txt` installed still
    boots — only consumers that actually call the forecaster pay the
    statsmodels import cost (~1-2 s, but one-time per process).
    """
    # statsmodels ships without a py.typed marker as of 0.14.x — the import is
    # opaque to mypy, hence the ignore. If/when upstream publishes stubs we
    # can drop this.
    from statsmodels.tsa.statespace.sarimax import SARIMAX  # type: ignore[import-untyped]

    return SARIMAX


def _forecast_sarimax(
    dates: list[date], values: list[float], horizon_days: int
) -> tuple[list[ForecastPoint], str]:
    """Fit SARIMAX(1,1,1) and project forward. Returns (points, model_name)."""
    sarimax_cls = _import_sarimax()

    # statsmodels emits a cloud of FutureWarnings and ConvergenceWarnings on
    # every fit; silence them inside the fit so we don't pollute the sidecar
    # logs on scheduled retrains. Real failures still raise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = sarimax_cls(
                values,
                order=(1, 1, 1),
                seasonal_order=(0, 0, 0, 0),
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            # `disp=False` suppresses the maximum-likelihood optimiser's
            # iteration log (~30 lines per fit); `maxiter` capped so a
            # pathological series can't hang the scheduler.
            results = model.fit(disp=False, maxiter=100)
            fc = results.get_forecast(steps=horizon_days)
            mean = fc.predicted_mean
            ci80 = fc.conf_int(alpha=0.20)
            ci95 = fc.conf_int(alpha=0.05)
        except Exception as exc:  # statsmodels raises many subclasses
            raise ForecastFitError(f"SARIMAX fit/forecast failed: {exc}") from exc

    return (
        _materialise_points(dates[-1], horizon_days, mean, ci80, ci95),
        SARIMAX_MODEL_NAME,
    )


# ---------------------------------------------------------------------------
# Holt-Winters / ETS engine
# ---------------------------------------------------------------------------


def _import_ets_and_pandas() -> tuple[Any, Any]:
    """Lazy import for ETSModel + pandas. ETSModel's `get_prediction` only
    yields proper prediction intervals when fed a pandas Series with a
    DatetimeIndex (the numpy-array path lacks the index machinery and
    raises `'numpy.ndarray' object has no attribute 'index'`)."""
    import pandas as pd  # type: ignore[import-untyped]
    from statsmodels.tsa.exponential_smoothing.ets import (  # type: ignore[import-untyped]
        ETSModel,
    )

    return ETSModel, pd


def _forecast_holt_winters(
    dates: list[date], values: list[float], horizon_days: int
) -> tuple[list[ForecastPoint], str]:
    """Fit ETS(A,A,N) (additive level + additive trend, no seasonality) and
    forecast forward. Returns (points, model_name).

    We use the unified ETS framework rather than the legacy
    ``ExponentialSmoothing`` class because ETS gives us closed-form CI
    bands (``get_prediction().conf_int(alpha=...)``); the legacy API only
    offers Monte-Carlo simulation, which would multiply our retrain cost
    by N-simulations per asset.

    Why no seasonality on daily closes — equity prices are dominated by
    trend + idiosyncratic shocks, not by weekly cycles. Adding seasonal=
    "add" with period=7 fits more parameters with no out-of-sample lift on
    our tested seed assets, so we keep the simpler model. Macro / crypto
    exceptions can be added later by tagging asset_type into the engine
    chooser.
    """
    ets_cls, pd = _import_ets_and_pandas()

    # ETS's prediction-interval path requires a pandas Series with a
    # DatetimeIndex; a plain numpy array trips it with
    # ``'numpy.ndarray' object has no attribute 'index'``. Build the index
    # from the supplied dates so the forecast horizon picks up consecutive
    # calendar days automatically.
    series = pd.Series(values, index=pd.to_datetime(dates))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = ets_cls(
                series,
                error="add",
                trend="add",
                seasonal=None,
                damped_trend=False,
                initialization_method="estimated",
            )
            # ETS exposes a maxiter knob via `fit(maxiter=...)` on the
            # underlying scipy optimiser. 200 is plenty for a single ETS
            # fit; we cap it to keep a bad series from hanging the
            # scheduler.
            results = model.fit(disp=False, maxiter=200)
            # `start`/`end` accept positional integers when working off a
            # DatetimeIndex — out-of-sample positions are mapped to the
            # next calendar day automatically.
            pred = results.get_prediction(
                start=len(values), end=len(values) + horizon_days - 1
            )
            mean = pred.predicted_mean
            # ETS's `summary_frame()` returns a DataFrame with columns
            # ``mean``, ``pi_lower`` / ``pi_upper`` — we ask for the bands
            # at the matching alphas. Equivalent to SARIMAX's `conf_int`
            # but the API is intentionally different upstream.
            sf80 = results.get_prediction(
                start=len(values), end=len(values) + horizon_days - 1
            ).summary_frame(alpha=0.20)
            sf95 = results.get_prediction(
                start=len(values), end=len(values) + horizon_days - 1
            ).summary_frame(alpha=0.05)
            # Pull just the [lower, upper] columns so `_materialise_points`
            # can iterate them positionally — matches SARIMAX's `conf_int`.
            ci80 = sf80[["pi_lower", "pi_upper"]]
            ci95 = sf95[["pi_lower", "pi_upper"]]
        except Exception as exc:
            raise ForecastFitError(
                f"Holt-Winters/ETS fit/forecast failed: {exc}"
            ) from exc

    return (
        _materialise_points(dates[-1], horizon_days, mean, ci80, ci95),
        HOLT_WINTERS_MODEL_NAME,
    )


# ---------------------------------------------------------------------------
# Shared point materialisation
# ---------------------------------------------------------------------------


def _materialise_points(
    last_date: date,
    horizon_days: int,
    mean: Any,
    ci80: Any,
    ci95: Any,
) -> list[ForecastPoint]:
    """Convert the engine's ``mean`` + CI return values into our wire shape.

    Handles both shapes our two engines emit:
    - SARIMAX returns a numpy array for `predicted_mean` and DataFrames with
      RangeIndex for `conf_int` — both support positional indexing via
      ``[i]`` / ``.iloc[i]``.
    - ETS (Holt-Winters) returns pandas Series/DataFrames indexed by the
      training data's DatetimeIndex, so positional access has to go through
      ``.iloc`` exclusively (``mean[i]`` becomes a label lookup and KeyErrors).
    """

    def _pos(arr: Any, i: int) -> Any:
        # `iloc` is the unambiguous positional accessor for Series and
        # DataFrames; falls back to plain indexing for ndarrays / lists.
        if hasattr(arr, "iloc"):
            return arr.iloc[i]
        return arr[i]

    points: list[ForecastPoint] = []
    for i in range(horizon_days):
        fdate = last_date + timedelta(days=i + 1)
        mean_i = float(_pos(mean, i))
        ci80_row = _pos(ci80, i)
        ci95_row = _pos(ci95, i)
        ci80_list = ci80_row.tolist() if hasattr(ci80_row, "tolist") else list(ci80_row)
        ci95_list = ci95_row.tolist() if hasattr(ci95_row, "tolist") else list(ci95_row)
        points.append(
            ForecastPoint(
                forecast_date=fdate,
                yhat=mean_i,
                lower_80=float(ci80_list[0]),
                upper_80=float(ci80_list[1]),
                lower_95=float(ci95_list[0]),
                upper_95=float(ci95_list[1]),
            )
        )
    return points


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


_ENGINES = {
    "sarimax": _forecast_sarimax,
    "holt_winters": _forecast_holt_winters,
}


def forecast_series(
    closes: Sequence[tuple[date, float]] | list[tuple[date, float]],
    *,
    horizon_days: int = 14,
    engine: ForecastEngine = DEFAULT_ENGINE,
) -> ForecastResult:
    """Fit the chosen engine on ``closes`` and project ``horizon_days`` forward.

    Args:
        closes: ``(date, close_price)`` tuples, oldest-first, strictly
            ascending dates.
        horizon_days: number of calendar days forward to predict (1..90).
        engine: ``"sarimax"`` (default) or ``"holt_winters"``.

    Raises:
        ForecastError: invalid horizon, non-ascending dates, or unknown engine.
        InsufficientDataError: fewer than MIN_TRAINING_ROWS training rows.
        ForecastFitError: engine raised during fit or forecast.
    """
    if engine not in _ENGINES:
        raise ForecastError(
            f"unknown engine {engine!r}; expected one of {sorted(_ENGINES)}"
        )

    dates, values = _validate_inputs(closes, horizon_days)
    last_date = dates[-1]
    last_close = Decimal(str(values[-1]))

    points, model_name = _ENGINES[engine](dates, values, horizon_days)

    return ForecastResult(
        model=model_name,
        horizon_days=horizon_days,
        training_rows=len(values),
        last_close=last_close,
        last_close_date=last_date,
        generated_at=datetime.now(UTC),
        points=points,
    )
