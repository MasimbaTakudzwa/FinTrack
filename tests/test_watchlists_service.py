from __future__ import annotations

from pathlib import Path

import pytest

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, Watchlist, WatchlistItem
from sidecar.services import watchlists as svc


def _seed_assets() -> dict[str, int]:
    """Seed a small set of assets. Returns {symbol: id}."""
    with session_scope() as s:
        a1 = Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK)
        a2 = Asset(
            symbol="MSFT", name="Microsoft Corporation", asset_type=AssetType.STOCK
        )
        a3 = Asset(symbol="SPY", name="SPDR S&P 500", asset_type=AssetType.ETF)
        a4 = Asset(symbol="BTC-USD", name="Bitcoin", asset_type=AssetType.CRYPTO)
        s.add_all([a1, a2, a3, a4])
        s.flush()
        return {a.symbol: a.id for a in (a1, a2, a3, a4)}


def test_seed_creates_default_watchlist_with_all_active_assets(
    isolated_db: Path,
) -> None:
    _seed_assets()
    added = svc.seed_default_watchlist()
    assert added == 4
    lists = svc.list_watchlists()
    assert len(lists) == 1
    assert lists[0].name == svc.DEFAULT_WATCHLIST_NAME
    assert lists[0].is_default is True
    assert lists[0].item_count == 4

    detail = svc.get_default_watchlist()
    assert detail is not None
    # Positions dense 0..3, ordered alphabetically by symbol.
    symbols = [i.symbol for i in detail.items]
    assert symbols == ["AAPL", "BTC-USD", "MSFT", "SPY"]
    assert [i.position for i in detail.items] == [0, 1, 2, 3]


def test_seed_is_idempotent(isolated_db: Path) -> None:
    _seed_assets()
    assert svc.seed_default_watchlist() == 4
    assert svc.seed_default_watchlist() == 0  # already seeded
    assert len(svc.list_watchlists()) == 1


def test_seed_backfills_new_assets(isolated_db: Path) -> None:
    ids = _seed_assets()
    svc.seed_default_watchlist()
    # Add a new asset later (simulates the user adding a new symbol).
    with session_scope() as s:
        s.add(Asset(symbol="NVDA", name="NVIDIA", asset_type=AssetType.STOCK))
    added = svc.seed_default_watchlist()
    assert added == 1
    detail = svc.get_default_watchlist()
    assert detail is not None
    assert any(item.symbol == "NVDA" for item in detail.items)
    # Stable positions for pre-existing items: still 0..3 + 4 for the new one.
    by_symbol = {i.symbol: i.position for i in detail.items}
    assert by_symbol["NVDA"] == 4
    assert by_symbol["AAPL"] == 0
    _ = ids  # silence unused — _seed_assets is shared fixture-style helper


def test_create_watchlist_unique_name(isolated_db: Path) -> None:
    svc.create_watchlist("Tech")
    with pytest.raises(svc.WatchlistNameConflictError):
        svc.create_watchlist("Tech")


def test_create_watchlist_strips_and_validates(isolated_db: Path) -> None:
    wl = svc.create_watchlist("  Tech  ")
    assert wl.name == "Tech"
    with pytest.raises(svc.WatchlistError):
        svc.create_watchlist("")
    with pytest.raises(svc.WatchlistError):
        svc.create_watchlist("   ")
    with pytest.raises(svc.WatchlistError):
        svc.create_watchlist("x" * 129)


def test_rename_watchlist(isolated_db: Path) -> None:
    w = svc.create_watchlist("Tech")
    renamed = svc.rename_watchlist(w.id, "Tech Giants")
    assert renamed.name == "Tech Giants"
    lists = svc.list_watchlists()
    assert {w.name for w in lists} == {"Tech Giants"}


def test_rename_to_existing_name_conflicts(isolated_db: Path) -> None:
    w1 = svc.create_watchlist("Tech")
    svc.create_watchlist("Macro")
    with pytest.raises(svc.WatchlistNameConflictError):
        svc.rename_watchlist(w1.id, "Macro")


def test_rename_same_name_is_noop(isolated_db: Path) -> None:
    w = svc.create_watchlist("Tech")
    renamed = svc.rename_watchlist(w.id, "Tech")
    assert renamed.name == "Tech"


def test_rename_unknown_watchlist(isolated_db: Path) -> None:
    with pytest.raises(svc.WatchlistNotFoundError):
        svc.rename_watchlist(9999, "x")


def test_set_default_promotes_and_demotes(isolated_db: Path) -> None:
    a = svc.create_watchlist("A", is_default=True)
    b = svc.create_watchlist("B")
    assert a.is_default is True
    assert b.is_default is False

    promoted = svc.set_default(b.id)
    assert promoted.is_default is True

    # DB invariant: at most one is_default=True.
    with session_scope() as s:
        defaults = [w for w in s.query(Watchlist).all() if w.is_default]
        assert len(defaults) == 1
        assert defaults[0].id == b.id


def test_set_default_on_already_default_is_noop(isolated_db: Path) -> None:
    a = svc.create_watchlist("A", is_default=True)
    r = svc.set_default(a.id)
    assert r.is_default is True


def test_create_as_default_demotes_previous(isolated_db: Path) -> None:
    a = svc.create_watchlist("A", is_default=True)
    b = svc.create_watchlist("B", is_default=True)
    assert b.is_default is True
    with session_scope() as s:
        a_db = s.get(Watchlist, a.id)
        assert a_db is not None and a_db.is_default is False


def test_delete_watchlist(isolated_db: Path) -> None:
    w = svc.create_watchlist("Tech")
    svc.delete_watchlist(w.id)
    assert svc.list_watchlists() == []


def test_delete_default_forbidden(isolated_db: Path) -> None:
    w = svc.create_watchlist("Primary", is_default=True)
    with pytest.raises(svc.CannotDeleteDefaultError):
        svc.delete_watchlist(w.id)


def test_delete_watchlist_cascades_items(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    svc.add_item(w.id, ids["AAPL"])
    svc.add_item(w.id, ids["MSFT"])
    svc.delete_watchlist(w.id)
    with session_scope() as s:
        assert s.query(WatchlistItem).count() == 0


def test_add_item_appends_positions(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    first = svc.add_item(w.id, ids["AAPL"])
    second = svc.add_item(w.id, ids["MSFT"])
    assert first.position == 0
    assert second.position == 1


def test_add_item_duplicate_rejected(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    svc.add_item(w.id, ids["AAPL"])
    with pytest.raises(svc.ItemAlreadyExistsError):
        svc.add_item(w.id, ids["AAPL"])


def test_add_item_unknown_asset(isolated_db: Path) -> None:
    w = svc.create_watchlist("Tech")
    with pytest.raises(svc.AssetNotFoundError):
        svc.add_item(w.id, 9999)


def test_add_item_unknown_watchlist(isolated_db: Path) -> None:
    ids = _seed_assets()
    with pytest.raises(svc.WatchlistNotFoundError):
        svc.add_item(9999, ids["AAPL"])


def test_remove_item_redensifies_positions(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    svc.add_item(w.id, ids["AAPL"])  # pos 0
    svc.add_item(w.id, ids["MSFT"])  # pos 1
    svc.add_item(w.id, ids["SPY"])  # pos 2

    svc.remove_item(w.id, ids["MSFT"])

    detail = svc.get_watchlist(w.id)
    positions = [(i.symbol, i.position) for i in detail.items]
    assert positions == [("AAPL", 0), ("SPY", 1)]


def test_remove_item_not_on_list(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    with pytest.raises(svc.ItemNotFoundError):
        svc.remove_item(w.id, ids["AAPL"])


def test_reorder_items(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    svc.add_item(w.id, ids["AAPL"])
    svc.add_item(w.id, ids["MSFT"])
    svc.add_item(w.id, ids["SPY"])

    # Reverse the order.
    svc.reorder_items(w.id, [ids["SPY"], ids["MSFT"], ids["AAPL"]])

    detail = svc.get_watchlist(w.id)
    assert [i.symbol for i in detail.items] == ["SPY", "MSFT", "AAPL"]
    assert [i.position for i in detail.items] == [0, 1, 2]


def test_reorder_rejects_missing_ids(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    svc.add_item(w.id, ids["AAPL"])
    svc.add_item(w.id, ids["MSFT"])
    with pytest.raises(svc.WatchlistError):
        svc.reorder_items(w.id, [ids["AAPL"]])  # missing MSFT


def test_reorder_rejects_extra_ids(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    svc.add_item(w.id, ids["AAPL"])
    with pytest.raises(svc.WatchlistError):
        svc.reorder_items(w.id, [ids["AAPL"], ids["MSFT"]])  # extra MSFT


def test_reorder_rejects_duplicates(isolated_db: Path) -> None:
    ids = _seed_assets()
    w = svc.create_watchlist("Tech")
    svc.add_item(w.id, ids["AAPL"])
    svc.add_item(w.id, ids["MSFT"])
    with pytest.raises(svc.WatchlistError):
        svc.reorder_items(w.id, [ids["AAPL"], ids["AAPL"]])


def test_list_watchlists_orders_default_first(isolated_db: Path) -> None:
    svc.create_watchlist("Zeta")
    svc.create_watchlist("Alpha")
    svc.create_watchlist("Default", is_default=True)
    lists = svc.list_watchlists()
    assert lists[0].name == "Default"
    assert lists[0].is_default is True
    # Remaining ordered alphabetically.
    assert [w.name for w in lists[1:]] == ["Alpha", "Zeta"]


def test_get_default_returns_none_when_no_default(isolated_db: Path) -> None:
    svc.create_watchlist("Only")  # not default
    assert svc.get_default_watchlist() is None


def test_partial_unique_index_is_enforced(isolated_db: Path) -> None:
    """Verify the DB-level partial unique index prevents two defaults.

    The service layer already demotes before promoting, so this is defence-in-depth.
    We smash two is_default=True rows in directly and expect a constraint failure.
    """
    from sqlalchemy.exc import IntegrityError

    with session_scope() as s:
        s.add(Watchlist(name="A", is_default=True))
        s.flush()

    with pytest.raises(IntegrityError):
        with session_scope() as s:
            s.add(Watchlist(name="B", is_default=True))
            s.flush()
