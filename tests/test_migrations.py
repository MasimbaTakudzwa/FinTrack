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
