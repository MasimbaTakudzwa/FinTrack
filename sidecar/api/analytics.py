"""Cross-asset analytics — correlation matrix, etc.

Sits alongside ``forecast`` / ``news`` / ``prices`` as a pure-derived
endpoint family: every metric here is computed from data already
captured by the existing ingestion pipeline. No new background jobs,
no new persisted state.

Currently exposes:

- ``GET /api/analytics/correlations/?symbols=A,B,C&lookback_days=N`` —
  pairwise Pearson correlation on daily log-returns. Drives the
  diversification heatmap on Market overview. Symbols are accepted as a
  comma-separated list rather than repeated query params so the URL
  stays compact even with 10+ assets in a watchlist.
- ``GET /api/analytics/correlations/default-watchlist/?lookback_days=N`` —
  convenience wrapper that resolves the user's default watchlist and
  feeds its assets to the correlation engine. UI uses this for the
  zero-config "show me the heatmap" path.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from ml.correlation import (
    MIN_OVERLAP_DAYS,
    CorrelationCell,
    CorrelationMatrix,
    compute_correlation_matrix,
)
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, Watchlist, WatchlistItem

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

logger = logging.getLogger(__name__)


class CorrelationCellModel(BaseModel):
    symbol_a: str
    symbol_b: str
    coefficient: float
    overlap: int


class CorrelationMatrixModel(BaseModel):
    """Square correlation matrix payload.

    ``cells`` covers the upper triangle plus the diagonal — the UI
    mirrors when rendering the lower half. ``min_overlap_days`` is
    surfaced so the client knows the threshold below which it should
    fade out a cell rather than hard-coding it on the JS side.
    """

    symbols: list[str]
    lookback_days: int
    asset_count: int
    min_overlap_days: int
    cells: list[CorrelationCellModel]


def _matrix_to_model(matrix: CorrelationMatrix) -> CorrelationMatrixModel:
    return CorrelationMatrixModel(
        symbols=matrix.symbols,
        lookback_days=matrix.lookback_days,
        asset_count=matrix.asset_count,
        min_overlap_days=MIN_OVERLAP_DAYS,
        cells=[_cell_to_model(c) for c in matrix.cells],
    )


def _cell_to_model(cell: CorrelationCell) -> CorrelationCellModel:
    return CorrelationCellModel(
        symbol_a=cell.symbol_a,
        symbol_b=cell.symbol_b,
        coefficient=cell.coefficient,
        overlap=cell.overlap,
    )


def _parse_symbols(raw: str) -> list[str]:
    """Split a comma-separated symbol list, trim, dedup, uppercase."""
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    # Preserve first-seen order while dropping dupes — dict is the
    # idiomatic ordered-set in modern Python.
    return list(dict.fromkeys(parts))


@router.get("/correlations/", response_model=CorrelationMatrixModel)
def get_correlations(
    symbols: Annotated[str, Query(min_length=1)],
    lookback_days: Annotated[int, Query(ge=7, le=730)] = 90,
) -> CorrelationMatrixModel:
    """Pairwise correlation matrix for an explicit symbol list.

    422 fires when ``symbols`` is empty after parsing OR when
    ``lookback_days`` is out of range. The matrix endpoint stays
    permissive about unknown symbols — they're silently dropped during
    matrix construction (asset has to exist in our DB to contribute
    daily-close data) so the UI just sees a smaller-than-requested
    matrix when the user types a typo.
    """
    parsed = _parse_symbols(symbols)
    if not parsed:
        raise HTTPException(
            status_code=422, detail="symbols must include at least one valid ticker"
        )
    matrix = compute_correlation_matrix(parsed, lookback_days=lookback_days)
    return _matrix_to_model(matrix)


@router.get(
    "/correlations/default-watchlist/", response_model=CorrelationMatrixModel
)
def get_default_watchlist_correlations(
    lookback_days: Annotated[int, Query(ge=7, le=730)] = 90,
) -> CorrelationMatrixModel:
    """Correlation matrix scoped to the user's default watchlist.

    UI calls this from the Market page for the zero-config path. 404
    when no default watchlist exists (i.e. the seed hasn't run yet) —
    the UI falls back to "all active assets" via the explicit-symbols
    endpoint in that case.
    """
    with session_scope() as session:
        wl = session.execute(
            select(Watchlist).where(Watchlist.is_default.is_(True))
        ).scalar_one_or_none()
        if wl is None:
            raise HTTPException(
                status_code=404, detail="No default watchlist exists"
            )
        rows = session.execute(
            select(Asset.symbol)
            .join(WatchlistItem, WatchlistItem.asset_id == Asset.id)
            .where(WatchlistItem.watchlist_id == wl.id)
            .order_by(WatchlistItem.position.asc())
        ).scalars().all()
        symbols = list(rows)

    matrix = compute_correlation_matrix(symbols, lookback_days=lookback_days)
    return _matrix_to_model(matrix)
