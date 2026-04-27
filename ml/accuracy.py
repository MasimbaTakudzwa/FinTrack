"""Rolling forecast-accuracy metrics — MAPE / RMSE / directional accuracy.

Once a forecast horizon elapses we can compare the model's predictions
against the actual closes that landed. This module turns
``ForecastSnapshot`` rows + matching ``PricePoint`` daily closes into
the three metrics traders typically use to compare engines:

- **MAPE** (mean absolute percentage error) — easy to read, in percent
  units, comparable across assets at different price levels.
- **RMSE** (root mean squared error) — same units as the price, weights
  large errors heavily, good for spotting blow-ups.
- **Directional accuracy** — fraction of forecasts that called the up /
  down move correctly relative to ``last_close``. Often the metric
  that matters most for trading decisions even when MAPE looks
  identical between engines.

Per-engine breakdown lets the user answer "should I switch from SARIMAX
to Holt-Winters for this asset?" — the headline question that motivated
Phase 2's two-engine design in the first place.

Pure-compute boundary: this module reads from ``ml.persistence`` /
``sidecar.db.engine`` but doesn't import statsmodels — it's a stats
calculator over already-stored predictions, not a fitter. Lazy heavy
imports kept upstream.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from ml.forecast import ForecastResult
from ml.persistence import load_snapshots
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, PricePoint

logger = logging.getLogger(__name__)


# A forecast can only be evaluated after at least one of its horizon
# dates has actually elapsed (and we have a real close on that date).
# Below this we report "no evaluable points" rather than a misleading
# zero — UI surfaces the count so users understand why a recently-
# trained engine has no metrics yet.
MIN_EVALUABLE_POINTS = 1


@dataclass(frozen=True)
class EngineAccuracy:
    """Aggregate metrics for a single (asset, engine) pair over a window.

    All metric fields are ``None`` when there are not enough evaluable
    points to compute meaningfully — UI distinguishes "engine had a
    snapshot but its horizon hasn't elapsed yet" from "engine fitted
    fine but missed direction 60% of the time".
    """

    engine: str
    snapshots: int  # number of distinct forecast snapshots in the window
    evaluable_points: int  # individual (snapshot, day) pairs that had an actual close
    mape: float | None  # mean absolute % error
    rmse: float | None  # root mean squared error in price units
    directional: float | None  # fraction of forecasts that called direction correctly


@dataclass(frozen=True)
class AccuracyReport:
    """Top-level accuracy summary for one asset.

    ``per_engine`` is sorted by MAPE ascending (best engine first) —
    UI orders the breakdown table by it. ``overall`` aggregates across
    every engine in the window, useful when the user hasn't switched
    engines and just wants one headline number.
    """

    symbol: str
    days: int
    per_engine: list[EngineAccuracy]
    overall: EngineAccuracy | None
    actuals_available: int  # number of daily closes inside the window


# ---------------------------------------------------------------------------
# Pure-compute helpers
# ---------------------------------------------------------------------------


def _evaluable_pairs(
    snapshot: ForecastResult, actuals: dict[date, float]
) -> list[tuple[float, float, float]]:
    """Walk a snapshot's predictions and yield ``(predicted, actual, last_close)``
    for every forecast date that has a real close in ``actuals``.

    ``last_close`` is the snapshot's own training-tail close — used by the
    directional metric to decide which way the forecast was leaning.
    """
    last_close = float(snapshot.last_close)
    pairs: list[tuple[float, float, float]] = []
    for point in snapshot.points:
        actual = actuals.get(point.forecast_date)
        if actual is None:
            continue
        pairs.append((point.yhat, actual, last_close))
    return pairs


def _compute_metrics(
    pairs: list[tuple[float, float, float]],
) -> tuple[float | None, float | None, float | None]:
    """Compute (MAPE, RMSE, directional) over (predicted, actual, last_close)
    triples. All three are ``None`` when ``len(pairs) < MIN_EVALUABLE_POINTS``."""
    if len(pairs) < MIN_EVALUABLE_POINTS:
        return None, None, None

    abs_pct_errors: list[float] = []
    squared_errors: list[float] = []
    direction_hits = 0
    direction_evaluable = 0

    for predicted, actual, last_close in pairs:
        # MAPE — skip zero-actuals (would divide by zero); a flat-zero
        # asset is a bizarre edge case that shouldn't poison the metric.
        if actual != 0:
            abs_pct_errors.append(abs(actual - predicted) / abs(actual) * 100.0)
        squared_errors.append((actual - predicted) ** 2)
        # Directional — count the forecast as a "hit" iff it leaned the
        # same way the actual close moved relative to ``last_close``.
        # Ties on either side don't count as evaluable (the model wasn't
        # making a directional call).
        pred_diff = predicted - last_close
        actual_diff = actual - last_close
        if pred_diff != 0 and actual_diff != 0:
            direction_evaluable += 1
            if (pred_diff > 0) == (actual_diff > 0):
                direction_hits += 1

    mape = sum(abs_pct_errors) / len(abs_pct_errors) if abs_pct_errors else None
    rmse = math.sqrt(sum(squared_errors) / len(squared_errors)) if squared_errors else None
    directional = (
        direction_hits / direction_evaluable if direction_evaluable else None
    )
    return mape, rmse, directional


# ---------------------------------------------------------------------------
# DB-aware orchestration
# ---------------------------------------------------------------------------


def _load_actuals_for_asset(
    asset_id: int, *, since_days: int
) -> dict[date, float]:
    """Pull daily closes for an asset over the last N days, keyed by date.

    Used as the truth-set when scoring snapshots. ``since_days`` is
    intentionally generous — a snapshot's horizon may stretch up to
    ``horizon_days`` past its ``generated_at``, so we want at least
    ``since_days + horizon_days`` of actuals; the caller picks ``since_days``
    accordingly via ``window_days``.
    """
    with session_scope() as session:
        rows = session.execute(
            select(PricePoint.timestamp, PricePoint.close).where(
                PricePoint.asset_id == asset_id,
                PricePoint.interval == "1d",
            )
        ).all()
    actuals: dict[date, float] = {}
    for ts, close in rows:
        actuals[ts.date()] = float(close)
    return actuals


@dataclass
class _EngineAccumulator:
    """Per-engine running state — collected across snapshots within a
    single accuracy run, then collapsed into an ``EngineAccuracy`` at the end."""

    snapshots: int = 0
    pairs: list[tuple[float, float, float]] = field(default_factory=list)


def compute_accuracy(symbol: str, *, days: int = 30) -> AccuracyReport:
    """Return an ``AccuracyReport`` for one asset over the last N days.

    ``days`` is the window for the snapshot generation timestamp — i.e.
    "every forecast made in the last N days, scored against actual closes
    that have landed since". Snapshots whose horizon hasn't fully elapsed
    still contribute their already-evaluable points, which keeps the
    metric fresh even with a 14-day default horizon.
    """
    sym = symbol.strip().upper()

    with session_scope() as session:
        asset_id_row = session.execute(
            select(Asset.id).where(Asset.symbol == sym)
        ).scalar_one_or_none()
    if asset_id_row is None:
        return AccuracyReport(
            symbol=sym,
            days=days,
            per_engine=[],
            overall=None,
            actuals_available=0,
        )
    asset_id = int(asset_id_row)

    snapshots = load_snapshots(asset_id, since_days=days)
    actuals = _load_actuals_for_asset(asset_id, since_days=days)
    actuals_available = len(actuals)

    if not snapshots:
        # No snapshots at all — nothing to score, regardless of how many
        # actuals are on file. Return an empty report so the UI shows
        # the "no accuracy data yet" CTA.
        return AccuracyReport(
            symbol=sym,
            days=days,
            per_engine=[],
            overall=None,
            actuals_available=actuals_available,
        )

    # When there are snapshots but no actuals (e.g. a brand-new install
    # whose forecast horizons haven't elapsed yet), we still want to
    # surface the snapshot count via per_engine so the UI can show
    # "1 snapshot, 0 evaluable yet" instead of an empty panel. The
    # per-snapshot loop below handles this — `_evaluable_pairs` returns
    # [] when actuals is empty, so each snapshot increments only the
    # `snapshots` counter.

    by_engine: dict[str, _EngineAccumulator] = {}
    overall = _EngineAccumulator()

    for snap in snapshots:
        pairs = _evaluable_pairs(snap, actuals)
        if not pairs:
            # Snapshot's horizon hasn't elapsed yet — still counts toward
            # the "snapshots seen" tally (so the UI can say "5 snapshots,
            # 0 evaluable yet") but contributes no pairs.
            acc = by_engine.setdefault(snap.model, _EngineAccumulator())
            acc.snapshots += 1
            overall.snapshots += 1
            continue
        acc = by_engine.setdefault(snap.model, _EngineAccumulator())
        acc.snapshots += 1
        acc.pairs.extend(pairs)
        overall.snapshots += 1
        overall.pairs.extend(pairs)

    per_engine: list[EngineAccuracy] = []
    for engine, acc in by_engine.items():
        mape, rmse, directional = _compute_metrics(acc.pairs)
        per_engine.append(
            EngineAccuracy(
                engine=engine,
                snapshots=acc.snapshots,
                evaluable_points=len(acc.pairs),
                mape=mape,
                rmse=rmse,
                directional=directional,
            )
        )

    # Sort per-engine by MAPE ascending (best engine first). Engines with
    # None MAPE (no evaluable pairs yet) sink to the bottom.
    per_engine.sort(
        key=lambda e: (e.mape is None, e.mape if e.mape is not None else 0.0)
    )

    overall_mape, overall_rmse, overall_dir = _compute_metrics(overall.pairs)
    overall_acc = EngineAccuracy(
        engine="all",
        snapshots=overall.snapshots,
        evaluable_points=len(overall.pairs),
        mape=overall_mape,
        rmse=overall_rmse,
        directional=overall_dir,
    )

    return AccuracyReport(
        symbol=sym,
        days=days,
        per_engine=per_engine,
        overall=overall_acc,
        actuals_available=actuals_available,
    )
