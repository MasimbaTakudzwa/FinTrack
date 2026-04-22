"""Watchlist management service.

Single-user, local-only design: one `watchlists` table + one `watchlist_items`
table. `is_default` is enforced to be true for exactly one row at a time via a
partial unique index (`ux_watchlists_default_one` in migration 0006). The
Dashboard reads from the default watchlist.

Positions are 0-indexed, dense, and caller-maintained — every mutation that can
leave a hole re-densifies under a single transaction. Reorder accepts an
explicit list and renumbers atomically.

All functions raise `ValueError` on business-rule violations (unknown id,
duplicate name, deleting default, etc.). API endpoints translate those into
HTTP 4xx — the service itself is framework-agnostic so it can also be used by
scripts (e.g. seed_default_watchlist).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, Watchlist, WatchlistItem

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_NAME = "Default"


class WatchlistError(ValueError):
    """Raised for business-rule violations (not found, duplicate name, etc.)."""


class WatchlistNotFoundError(WatchlistError):
    pass


class WatchlistNameConflictError(WatchlistError):
    pass


class CannotDeleteDefaultError(WatchlistError):
    pass


class AssetNotFoundError(WatchlistError):
    pass


class ItemAlreadyExistsError(WatchlistError):
    pass


class ItemNotFoundError(WatchlistError):
    pass


@dataclass(frozen=True)
class WatchlistSummary:
    id: int
    name: str
    is_default: bool
    item_count: int


@dataclass(frozen=True)
class WatchlistItemDetail:
    asset_id: int
    symbol: str
    name: str
    asset_type: str
    position: int


@dataclass(frozen=True)
class WatchlistDetail:
    id: int
    name: str
    is_default: bool
    items: list[WatchlistItemDetail]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get(session: Session, watchlist_id: int) -> Watchlist:
    w = session.get(Watchlist, watchlist_id)
    if w is None:
        raise WatchlistNotFoundError(f"watchlist {watchlist_id} not found")
    return w


def _next_position(session: Session, watchlist_id: int) -> int:
    """Return the next position index for appending. 0 for an empty list."""
    result = session.execute(
        select(func.max(WatchlistItem.position)).where(
            WatchlistItem.watchlist_id == watchlist_id
        )
    ).scalar()
    return 0 if result is None else int(result) + 1


def _densify_positions(session: Session, watchlist_id: int) -> None:
    """Re-number positions 0..n-1 in current order. Safe after removals."""
    items = list(
        session.execute(
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id)
            .order_by(WatchlistItem.position, WatchlistItem.id)
        ).scalars()
    )
    for new_pos, item in enumerate(items):
        if item.position != new_pos:
            item.position = new_pos


def _require_asset(session: Session, asset_id: int) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFoundError(f"asset {asset_id} not found")
    return asset


# ---------------------------------------------------------------------------
# Public API — queries
# ---------------------------------------------------------------------------


def list_watchlists() -> list[WatchlistSummary]:
    with session_scope() as s:
        rows = s.execute(
            select(
                Watchlist.id,
                Watchlist.name,
                Watchlist.is_default,
                func.count(WatchlistItem.id),
            )
            .outerjoin(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
            .group_by(Watchlist.id)
            .order_by(Watchlist.is_default.desc(), Watchlist.name)
        ).all()
        return [
            WatchlistSummary(
                id=int(r[0]),
                name=str(r[1]),
                is_default=bool(r[2]),
                item_count=int(r[3] or 0),
            )
            for r in rows
        ]


def get_watchlist(watchlist_id: int) -> WatchlistDetail:
    with session_scope() as s:
        w = _get(s, watchlist_id)
        rows = s.execute(
            select(
                Asset.id,
                Asset.symbol,
                Asset.name,
                Asset.asset_type,
                WatchlistItem.position,
            )
            .join(Asset, Asset.id == WatchlistItem.asset_id)
            .where(WatchlistItem.watchlist_id == watchlist_id)
            .order_by(WatchlistItem.position)
        ).all()
        items = [
            WatchlistItemDetail(
                asset_id=int(r[0]),
                symbol=str(r[1]),
                name=str(r[2]),
                asset_type=str(r[3]),
                position=int(r[4]),
            )
            for r in rows
        ]
        return WatchlistDetail(
            id=w.id, name=w.name, is_default=w.is_default, items=items
        )


def get_default_watchlist() -> WatchlistDetail | None:
    with session_scope() as s:
        w = s.execute(
            select(Watchlist).where(Watchlist.is_default.is_(True))
        ).scalar_one_or_none()
        if w is None:
            return None
        wid = w.id
    # Delegate to get_watchlist for the item hydration query (separate tx is fine —
    # no concurrent writer can remove the default without also clearing is_default).
    return get_watchlist(wid)


# ---------------------------------------------------------------------------
# Public API — mutations
# ---------------------------------------------------------------------------


def create_watchlist(name: str, *, is_default: bool = False) -> WatchlistSummary:
    """Create a new watchlist.

    If `is_default=True`, any existing default is demoted first.
    """
    clean = name.strip()
    if not clean:
        raise WatchlistError("name must not be empty")
    if len(clean) > 128:
        raise WatchlistError("name must be <= 128 chars")
    with session_scope() as s:
        existing = s.execute(
            select(Watchlist.id).where(Watchlist.name == clean)
        ).scalar_one_or_none()
        if existing is not None:
            raise WatchlistNameConflictError(f"watchlist name '{clean}' already exists")

        if is_default:
            s.execute(
                update(Watchlist)
                .where(Watchlist.is_default.is_(True))
                .values(is_default=False)
            )
            s.flush()

        w = Watchlist(name=clean, is_default=is_default)
        s.add(w)
        s.flush()
        return WatchlistSummary(
            id=w.id, name=w.name, is_default=w.is_default, item_count=0
        )


def rename_watchlist(watchlist_id: int, name: str) -> WatchlistSummary:
    clean = name.strip()
    if not clean:
        raise WatchlistError("name must not be empty")
    if len(clean) > 128:
        raise WatchlistError("name must be <= 128 chars")
    with session_scope() as s:
        w = _get(s, watchlist_id)
        if w.name == clean:
            return WatchlistSummary(
                id=w.id,
                name=w.name,
                is_default=w.is_default,
                item_count=_count_items(s, w.id),
            )
        conflict = s.execute(
            select(Watchlist.id).where(
                Watchlist.name == clean, Watchlist.id != watchlist_id
            )
        ).scalar_one_or_none()
        if conflict is not None:
            raise WatchlistNameConflictError(f"watchlist name '{clean}' already exists")
        w.name = clean
        s.flush()
        return WatchlistSummary(
            id=w.id,
            name=w.name,
            is_default=w.is_default,
            item_count=_count_items(s, w.id),
        )


def _count_items(session: Session, watchlist_id: int) -> int:
    n = session.execute(
        select(func.count(WatchlistItem.id)).where(
            WatchlistItem.watchlist_id == watchlist_id
        )
    ).scalar()
    return int(n or 0)


def set_default(watchlist_id: int) -> WatchlistSummary:
    """Mark the given watchlist as default. Demotes any existing default atomically."""
    with session_scope() as s:
        w = _get(s, watchlist_id)
        if w.is_default:
            return WatchlistSummary(
                id=w.id,
                name=w.name,
                is_default=True,
                item_count=_count_items(s, w.id),
            )
        # Clear all other defaults FIRST to satisfy the partial unique index
        # (ux_watchlists_default_one) in the same transaction.
        s.execute(
            update(Watchlist)
            .where(Watchlist.is_default.is_(True))
            .values(is_default=False)
        )
        s.flush()
        w.is_default = True
        s.flush()
        return WatchlistSummary(
            id=w.id,
            name=w.name,
            is_default=True,
            item_count=_count_items(s, w.id),
        )


def delete_watchlist(watchlist_id: int) -> None:
    """Delete a watchlist and its items. Cannot delete the default."""
    with session_scope() as s:
        w = _get(s, watchlist_id)
        if w.is_default:
            raise CannotDeleteDefaultError("cannot delete the default watchlist")
        s.delete(w)


def add_item(watchlist_id: int, asset_id: int) -> WatchlistItemDetail:
    """Append an asset to a watchlist at the next position."""
    with session_scope() as s:
        _get(s, watchlist_id)
        asset = _require_asset(s, asset_id)
        existing = s.execute(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == watchlist_id,
                WatchlistItem.asset_id == asset_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ItemAlreadyExistsError(
                f"asset {asset.symbol} is already on watchlist {watchlist_id}"
            )
        pos = _next_position(s, watchlist_id)
        item = WatchlistItem(
            watchlist_id=watchlist_id, asset_id=asset_id, position=pos
        )
        s.add(item)
        try:
            s.flush()
        except IntegrityError as exc:  # pragma: no cover - race with concurrent writer
            raise ItemAlreadyExistsError(
                f"asset {asset.symbol} is already on watchlist {watchlist_id}"
            ) from exc
        return WatchlistItemDetail(
            asset_id=asset.id,
            symbol=asset.symbol,
            name=asset.name,
            asset_type=str(asset.asset_type),
            position=item.position,
        )


def remove_item(watchlist_id: int, asset_id: int) -> None:
    """Remove an asset from a watchlist. Re-densifies remaining positions."""
    with session_scope() as s:
        _get(s, watchlist_id)
        item = s.execute(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == watchlist_id,
                WatchlistItem.asset_id == asset_id,
            )
        ).scalar_one_or_none()
        if item is None:
            raise ItemNotFoundError(
                f"asset {asset_id} not on watchlist {watchlist_id}"
            )
        s.delete(item)
        s.flush()
        _densify_positions(s, watchlist_id)


def reorder_items(watchlist_id: int, asset_ids: Sequence[int]) -> None:
    """Renumber items 0..n-1 following the provided order.

    The provided list must be a permutation of the current watchlist's asset ids
    — no additions or removals. Raises `WatchlistError` if the set doesn't match.
    """
    with session_scope() as s:
        _get(s, watchlist_id)
        current = list(
            s.execute(
                select(WatchlistItem).where(
                    WatchlistItem.watchlist_id == watchlist_id
                )
            ).scalars()
        )
        current_ids = {item.asset_id for item in current}
        provided_ids = set(asset_ids)
        if len(asset_ids) != len(provided_ids):
            raise WatchlistError("reorder list contains duplicate asset ids")
        if current_ids != provided_ids:
            missing = current_ids - provided_ids
            extra = provided_ids - current_ids
            parts: list[str] = []
            if missing:
                parts.append(f"missing: {sorted(missing)}")
            if extra:
                parts.append(f"extra: {sorted(extra)}")
            raise WatchlistError(
                "reorder list must exactly match current items ("
                + "; ".join(parts)
                + ")"
            )
        by_asset = {item.asset_id: item for item in current}
        # Two-phase renumber to avoid tripping any (watchlist_id, position) unique
        # index if we later add one. Today there is none, so single-pass works,
        # but keep the pattern for forward-compat.
        for new_pos, asset_id in enumerate(asset_ids):
            by_asset[asset_id].position = new_pos


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_default_watchlist() -> int:
    """Ensure a default watchlist exists containing every active asset.

    Called from lifespan. Idempotent — a second call on a populated DB is a no-op
    except for appending any newly-active assets that aren't already on the list.
    Returns the number of items added by this call.
    """
    with session_scope() as s:
        default = s.execute(
            select(Watchlist).where(Watchlist.is_default.is_(True))
        ).scalar_one_or_none()

        if default is None:
            # Try to find a watchlist with the default name in case a previous
            # seed partially ran without is_default set.
            by_name = s.execute(
                select(Watchlist).where(Watchlist.name == DEFAULT_WATCHLIST_NAME)
            ).scalar_one_or_none()
            if by_name is not None:
                by_name.is_default = True
                default = by_name
            else:
                default = Watchlist(name=DEFAULT_WATCHLIST_NAME, is_default=True)
                s.add(default)
                s.flush()

        # Load active assets not already on the list, sorted by symbol for a
        # deterministic initial order.
        existing_asset_ids = set(
            s.execute(
                select(WatchlistItem.asset_id).where(
                    WatchlistItem.watchlist_id == default.id
                )
            ).scalars()
        )
        new_assets = list(
            s.execute(
                select(Asset.id)
                .where(Asset.is_active.is_(True))
                .order_by(Asset.symbol)
            ).scalars()
        )
        added = 0
        start = _next_position(s, default.id)
        for offset, asset_id in enumerate(
            aid for aid in new_assets if aid not in existing_asset_ids
        ):
            s.add(
                WatchlistItem(
                    watchlist_id=default.id,
                    asset_id=asset_id,
                    position=start + offset,
                )
            )
            added += 1

        if added:
            logger.info(
                "seed_default_watchlist: appended %d new assets to '%s'",
                added,
                default.name,
            )
        return added
