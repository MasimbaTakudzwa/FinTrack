"""SARIMAX-based daily-close forecaster.

Why SARIMAX (and why these orders):
- `(p,d,q) = (1,1,1)` is the simplest ARIMA configuration that handles the
  non-stationarity of price series (via `d=1`, i.e. first differencing) while
  still picking up short-term autocorrelation (`p=1, q=1`). It's the textbook
  default for financial daily closes and fits in well under a second per
  asset on a modern laptop.
- `seasonal_order = (0,0,0,0)` — no seasonality. Daily closes don't have a
  strong weekly or annual seasonal pattern in level terms (volume does, but
  that's not what we're forecasting). If we later want to fold macro
  variables in we'll add them as exogenous regressors, not seasonality.
- Raw-price fit, not log-price. Keeps the forecast in the same units as the
  chart (no post-hoc exponentiation needed) and avoids a sneaky bias when
  re-expressing log-space CIs as price-space CIs. If CI widths look
  unreasonable in practice we'll revisit.

The forecaster is horizon-days *calendar* forward — we do not apply a trading
calendar. For stocks this means "next 14 days" includes weekends; users see
them on the chart but the bars are informational only. For crypto (24/7)
calendar days == trading days, so this simplification has no downside. A
future iteration can pull in `pandas_market_calendars` to skip weekends when
the asset is a stock.

Inputs:
- `closes`: sequence of `(date, float_close)` tuples, ordered oldest-first,
  no gaps on trading days. If the caller hands us PricePoint rows tagged
  `interval="1d"` in ascending order this is exactly what we need.
- `horizon_days`: default 14 (user's explicit request in project settings).

Output: a `ForecastResult` wrapping a list of `ForecastPoint` rows with
80% and 95% CI bands pulled straight from the model's `conf_int()` call at
alpha=0.20 and alpha=0.05 respectively.
"""

from __future__ import annotations

import itertools
import logging
import warnings
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    pass

logger = logging.getLogger(__name__)

# Training data floor. SARIMAX(1,1,1) on ~60 rows gives a noisy fit but
# converges; below this the likelihood surface is too flat to trust. The
# 5y daily backfill normally yields ~1250 rows per stock / ~1825 per crypto,
# so this only trips on newly-added assets whose backfill is still running.
MIN_TRAINING_ROWS = 60

# Model identifier persisted in forecast metadata so the UI can caption the
# chart and the release process doesn't have to rebuild the DB when we tweak
# the order.
MODEL_NAME = "SARIMAX(1,1,1)"


class ForecastError(Exception):
    """Base class for forecast-pipeline errors."""


class InsufficientDataError(ForecastError):
    """Raised when the input series has fewer than MIN_TRAINING_ROWS closes."""


class ForecastFitError(ForecastError):
    """Raised when statsmodels fails to fit the SARIMAX model."""


@dataclass(frozen=True)
class ForecastPoint:
    """One day of forecast output.

    `yhat` is the model's best-estimate median close; the two `lower`/`upper`
    pairs are central-CI bands at the named confidence levels, pulled from
    SARIMAX's closed-form Gaussian likelihood. Values can be negative in
    theory when the 95% band blows past zero — the UI clamps the display at
    zero but the raw numbers are kept truthful so post-hoc metrics don't
    silently lie.
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


def forecast_series(
    closes: list[tuple[date, float]],
    *,
    horizon_days: int = 14,
) -> ForecastResult:
    """Fit SARIMAX(1,1,1) on ``closes`` and project ``horizon_days`` forward.

    Raises:
        InsufficientDataError: fewer than MIN_TRAINING_ROWS training rows.
        ForecastFitError: statsmodels raised during fit or forecast.
    """
    if horizon_days < 1 or horizon_days > 90:
        raise ForecastError(f"horizon_days must be 1..90, got {horizon_days}")
    if len(closes) < MIN_TRAINING_ROWS:
        raise InsufficientDataError(
            f"need at least {MIN_TRAINING_ROWS} closes to train "
            f"SARIMAX, got {len(closes)}"
        )

    # Sanity-check ordering — if a caller hands us a reversed or unsorted
    # series the model would still fit but the "last close" semantics would
    # break catastrophically for the UI.
    dates = [d for d, _ in closes]
    for prev, curr in itertools.pairwise(dates):
        if curr <= prev:
            raise ForecastError(
                f"closes must be strictly ascending by date "
                f"(found {prev} followed by {curr})"
            )

    values = [float(c) for _, c in closes]
    last_date = dates[-1]
    last_close = Decimal(str(values[-1]))

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

    # statsmodels returns numpy arrays for `predicted_mean` and DataFrames
    # (with columns ``lower y``/``upper y``) for `conf_int`. We iterate by
    # positional index to avoid pinning ourselves to the column naming,
    # which has changed across statsmodels versions.
    points: list[ForecastPoint] = []
    for i in range(horizon_days):
        fdate = last_date + timedelta(days=i + 1)
        mean_i = float(mean[i])
        # `conf_int` rows: [lower, upper]. Pull via iloc / numpy indexing so
        # this works with either DataFrame or ndarray return types.
        ci80_row = ci80.iloc[i].tolist() if hasattr(ci80, "iloc") else list(ci80[i])
        ci95_row = ci95.iloc[i].tolist() if hasattr(ci95, "iloc") else list(ci95[i])
        points.append(
            ForecastPoint(
                forecast_date=fdate,
                yhat=mean_i,
                lower_80=float(ci80_row[0]),
                upper_80=float(ci80_row[1]),
                lower_95=float(ci95_row[0]),
                upper_95=float(ci95_row[1]),
            )
        )

    return ForecastResult(
        model=MODEL_NAME,
        horizon_days=horizon_days,
        training_rows=len(values),
        last_close=last_close,
        last_close_date=last_date,
        generated_at=datetime.now(UTC),
        points=points,
    )
