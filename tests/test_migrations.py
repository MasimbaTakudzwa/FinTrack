from __future__ import annotations

import sqlite3
from pathlib import Path

from sidecar.db.migrations_runner import upgrade_to_head


def test_upgrade_to_head_creates_assets_table(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    assert db_file.exists()

    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"
        ).fetchone()
        assert row is not None, "assets table not created"

        columns = {r[1] for r in conn.execute("PRAGMA table_info(assets)").fetchall()}
        expected = {"id", "symbol", "name", "asset_type", "is_active", "created_at"}
        assert expected <= columns, f"missing columns: {expected - columns}"

        indexes = {r[1] for r in conn.execute("PRAGMA index_list(assets)").fetchall()}
        assert "ix_assets_symbol" in indexes
    finally:
        conn.close()


def test_upgrade_to_head_creates_price_points_table(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='price_points'"
        ).fetchone()
        assert row is not None, "price_points table not created"

        columns = {r[1] for r in conn.execute("PRAGMA table_info(price_points)").fetchall()}
        expected = {"id", "asset_id", "timestamp", "open", "high", "low", "close", "volume"}
        assert expected <= columns, f"missing columns: {expected - columns}"

        indexes = {r[1] for r in conn.execute("PRAGMA index_list(price_points)").fetchall()}
        assert "ix_price_points_asset_ts" in indexes
        assert "ix_price_points_asset_id" in indexes

        fks = conn.execute("PRAGMA foreign_key_list(price_points)").fetchall()
        assert any(fk[2] == "assets" for fk in fks), "missing FK to assets"
    finally:
        conn.close()


def test_upgrade_to_head_creates_settings_table(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchone()
        assert row is not None, "settings table not created"

        columns = {
            r[1] for r in conn.execute("PRAGMA table_info(settings)").fetchall()
        }
        expected = {"key", "value", "updated_at"}
        assert expected <= columns, f"missing columns: {expected - columns}"

        pk_columns = [
            r[1] for r in conn.execute("PRAGMA table_info(settings)").fetchall() if r[5]
        ]
        assert pk_columns == ["key"], "settings primary key should be 'key'"
    finally:
        conn.close()


def test_upgrade_to_head_creates_article_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "articles" in tables
        assert "article_assets" in tables

        art_cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
        assert {
            "id",
            "url",
            "headline",
            "source",
            "published_at",
            "summary",
            "created_at",
        } <= art_cols

        art_indexes = {
            r[1] for r in conn.execute("PRAGMA index_list(articles)").fetchall()
        }
        assert "ix_articles_url" in art_indexes
        assert "ix_articles_published_at" in art_indexes

        assoc_cols = [
            r for r in conn.execute("PRAGMA table_info(article_assets)").fetchall()
        ]
        assoc_names = {r[1] for r in assoc_cols}
        assert {"article_id", "asset_id"} <= assoc_names
        pk_cols = sorted(r[1] for r in assoc_cols if r[5])
        assert pk_cols == ["article_id", "asset_id"], (
            "article_assets should have composite PK (article_id, asset_id)"
        )

        fks = conn.execute("PRAGMA foreign_key_list(article_assets)").fetchall()
        tables_referenced = {fk[2] for fk in fks}
        assert {"articles", "assets"} <= tables_referenced
    finally:
        conn.close()


def test_upgrade_to_head_creates_watchlist_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "watchlists" in tables
        assert "watchlist_items" in tables

        wl_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(watchlists)").fetchall()
        }
        assert {"id", "name", "is_default", "created_at"} <= wl_cols

        # Partial unique index on is_default (exactly-one-default guarantee).
        wl_indexes = {
            r[1] for r in conn.execute("PRAGMA index_list(watchlists)").fetchall()
        }
        assert "ux_watchlists_default_one" in wl_indexes

        item_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(watchlist_items)").fetchall()
        }
        assert {"id", "watchlist_id", "asset_id", "position", "added_at"} <= item_cols

        item_indexes = {
            r[1]
            for r in conn.execute("PRAGMA index_list(watchlist_items)").fetchall()
        }
        assert "ix_watchlist_items_watchlist_id" in item_indexes
        assert "ix_watchlist_items_asset_id" in item_indexes

        fks = conn.execute(
            "PRAGMA foreign_key_list(watchlist_items)"
        ).fetchall()
        tables_referenced = {fk[2] for fk in fks}
        assert {"watchlists", "assets"} <= tables_referenced
        # Both FKs should cascade on delete.
        for fk in fks:
            assert fk[6] == "CASCADE", f"FK to {fk[2]} must cascade on delete"
    finally:
        conn.close()


def test_upgrade_to_head_creates_price_alerts_table(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='price_alerts'"
        ).fetchone()
        assert row is not None, "price_alerts table not created"

        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(price_alerts)").fetchall()
        }
        expected = {
            "id",
            "asset_id",
            "threshold",
            "direction",
            "is_active",
            "triggered_at",
            "notified_at",
            "note",
            "created_at",
        }
        assert expected <= cols, f"missing columns: {expected - cols}"

        indexes = {
            r[1] for r in conn.execute("PRAGMA index_list(price_alerts)").fetchall()
        }
        assert "ix_price_alerts_asset_id" in indexes
        assert "ix_price_alerts_active_pending" in indexes
        assert "ix_price_alerts_notify_pending" in indexes

        fks = conn.execute("PRAGMA foreign_key_list(price_alerts)").fetchall()
        assert any(fk[2] == "assets" for fk in fks), "missing FK to assets"
        for fk in fks:
            if fk[2] == "assets":
                assert fk[6] == "CASCADE", "FK to assets must cascade on delete"
    finally:
        conn.close()


def test_upgrade_to_head_creates_macro_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "macro_indicators" in tables
        assert "macro_data_points" in tables

        ind_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(macro_indicators)").fetchall()
        }
        assert {"id", "series_id", "name", "is_active"} <= ind_cols

        dp_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(macro_data_points)").fetchall()
        }
        assert {"id", "indicator_id", "date", "value"} <= dp_cols

        dp_indexes = {
            r[1] for r in conn.execute("PRAGMA index_list(macro_data_points)").fetchall()
        }
        assert "ix_macro_data_points_ind_date" in dp_indexes

        fks = conn.execute("PRAGMA foreign_key_list(macro_data_points)").fetchall()
        assert any(fk[2] == "macro_indicators" for fk in fks)
    finally:
        conn.close()
