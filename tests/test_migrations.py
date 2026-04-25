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


def test_upgrade_to_head_adds_interval_to_price_points(tmp_path: Path) -> None:
    """0008 widens price_points with an `interval` column + new unique key.

    Existing rows must be backfilled to `"5m"` (historical fetcher output), the
    old `uq_price_points_asset_ts` constraint must be gone, and the new
    `uq_price_points_asset_ts_interval` constraint must be present so 5m and 1d
    bars at the same timestamp can coexist.

    SQLite folds inline `CONSTRAINT ... UNIQUE (...)` clauses into an anonymous
    ``sqlite_autoindex_*`` entry in ``PRAGMA index_list`` — the constraint
    name only survives in the CREATE TABLE text stored in ``sqlite_master``.
    """
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        col_rows = conn.execute("PRAGMA table_info(price_points)").fetchall()
        col_names = {r[1] for r in col_rows}
        assert "interval" in col_names, "interval column missing from price_points"

        # `interval` must be NOT NULL with a server default of "5m".
        interval_row = next(r for r in col_rows if r[1] == "interval")
        # (cid, name, type, notnull, dflt, pk)
        assert interval_row[3] == 1, "interval must be NOT NULL"
        default_value = interval_row[4]
        assert default_value is not None
        assert "5m" in str(default_value), (
            f"interval server default must be '5m', got {default_value!r}"
        )

        # The constraint name lives in the table's CREATE statement only.
        create_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='price_points'"
        ).fetchone()[0]
        assert "uq_price_points_asset_ts_interval" in create_sql, (
            f"new unique constraint missing from CREATE TABLE:\n{create_sql}"
        )
        assert "uq_price_points_asset_ts " not in create_sql.replace(
            "uq_price_points_asset_ts_interval", ""
        ), "old (asset_id, timestamp) unique constraint must be dropped"
        # The new constraint must cover all three columns.
        assert "UNIQUE (asset_id, timestamp, interval)" in create_sql, (
            f"unique constraint columns wrong:\n{create_sql}"
        )

        # And functionally: inserting a 5m and a 1d bar at the same timestamp
        # for the same asset must both succeed.
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO assets (symbol, name, asset_type, is_active, created_at) "
            "VALUES ('TEST', 'Test', 'stock', 1, '2026-01-01 00:00:00')"
        )
        aid = conn.execute("SELECT id FROM assets WHERE symbol='TEST'").fetchone()[0]
        conn.execute(
            "INSERT INTO price_points (asset_id, timestamp, interval, open, high, low, close, volume) "
            "VALUES (?, '2026-01-01 00:00:00', '5m', 1, 1, 1, 1, 0)",
            (aid,),
        )
        conn.execute(
            "INSERT INTO price_points (asset_id, timestamp, interval, open, high, low, close, volume) "
            "VALUES (?, '2026-01-01 00:00:00', '1d', 1, 1, 1, 1, 0)",
            (aid,),
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM price_points WHERE asset_id=?", (aid,)
        ).fetchone()[0]
        assert count == 2, "5m + 1d bars at same timestamp should both insert"
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


def test_upgrade_to_head_creates_forecasts_table(tmp_path: Path) -> None:
    """0009 adds a `forecasts` table with at most one row per asset.

    Enforced via a named unique constraint ``uq_forecasts_asset_id`` so the
    persistence layer can use `ON CONFLICT(asset_id) DO UPDATE` as its upsert
    primitive. FK to assets must cascade on delete so stale forecasts don't
    outlive their owning asset.
    """
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='forecasts'"
        ).fetchone()
        assert row is not None, "forecasts table not created"

        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(forecasts)").fetchall()
        }
        expected = {
            "id",
            "asset_id",
            "model",
            "horizon_days",
            "training_rows",
            "last_close",
            "last_close_date",
            "generated_at",
            "points_json",
        }
        assert expected <= cols, f"missing columns: {expected - cols}"

        indexes = {
            r[1] for r in conn.execute("PRAGMA index_list(forecasts)").fetchall()
        }
        assert "ix_forecasts_asset_id" in indexes

        # Named unique constraint shows up in `sqlite_master.sql` text; SQLite
        # doesn't surface it via PRAGMA index_list (it's folded into
        # sqlite_autoindex_*). Verify via raw DDL.
        create_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='forecasts'"
        ).fetchone()[0]
        assert "uq_forecasts_asset_id" in create_sql
        assert "UNIQUE (asset_id)" in create_sql

        # Functional check: inserting two rows with the same asset_id must fail.
        conn.execute(
            "INSERT INTO assets (symbol, name, asset_type, is_active, created_at) "
            "VALUES ('AAPL', 'Apple', 'stock', 1, CURRENT_TIMESTAMP)"
        )
        asset_id = conn.execute(
            "SELECT id FROM assets WHERE symbol='AAPL'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO forecasts "
            "(asset_id, model, horizon_days, training_rows, last_close, "
            " last_close_date, generated_at, points_json) "
            "VALUES (?, 'SARIMAX', 14, 100, 150.0, '2026-04-20', "
            " '2026-04-20T00:00:00+00:00', '[]')",
            (asset_id,),
        )
        try:
            conn.execute(
                "INSERT INTO forecasts "
                "(asset_id, model, horizon_days, training_rows, last_close, "
                " last_close_date, generated_at, points_json) "
                "VALUES (?, 'SARIMAX', 14, 100, 151.0, '2026-04-20', "
                " '2026-04-20T00:00:00+00:00', '[]')",
                (asset_id,),
            )
            raise AssertionError("expected unique-asset_id violation")
        except sqlite3.IntegrityError:
            pass

        fks = conn.execute("PRAGMA foreign_key_list(forecasts)").fetchall()
        assert any(fk[2] == "assets" for fk in fks), "missing FK to assets"
        for fk in fks:
            if fk[2] == "assets":
                assert fk[6] == "CASCADE", "FK to assets must cascade on delete"
    finally:
        conn.close()


def test_upgrade_to_head_adds_sentiment_to_articles(tmp_path: Path) -> None:
    """0010 adds a nullable `sentiment` Float column to `articles` plus an
    index on it (used by the News page sentiment filter and the asset
    sentiment-summary endpoint).
    """
    db_file = tmp_path / "test.db"
    upgrade_to_head(db_path=str(db_file))

    conn = sqlite3.connect(db_file)
    try:
        cols = {
            r[1]: r for r in conn.execute("PRAGMA table_info(articles)").fetchall()
        }
        assert "sentiment" in cols, "sentiment column not added to articles"

        # nullable=True
        sentiment_col = cols["sentiment"]
        # PRAGMA table_info row: (cid, name, type, notnull, dflt_value, pk)
        assert sentiment_col[3] == 0, "sentiment must be nullable"
        assert sentiment_col[2].upper() in ("FLOAT", "REAL"), (
            f"unexpected sentiment column type: {sentiment_col[2]}"
        )

        indexes = {
            r[1] for r in conn.execute("PRAGMA index_list(articles)").fetchall()
        }
        assert "ix_articles_sentiment" in indexes
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
