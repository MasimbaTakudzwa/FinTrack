from __future__ import annotations

import os

os.environ.setdefault("FINTRACK_ENABLE_SCHEDULER", "false")
os.environ.setdefault("FINTRACK_ENABLE_SEED", "false")

from collections.abc import Iterator
from pathlib import Path

import pytest

from sidecar.db import engine as engine_mod
from sidecar.db.migrations_runner import upgrade_to_head


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Reset the global engine/session to use a fresh SQLite file per test."""
    db_file = tmp_path / "fintrack.db"
    monkeypatch.setenv("FINTRACK_DB_PATH", str(db_file))

    from sidecar.config import settings as cfg

    monkeypatch.setattr(cfg, "db_path", str(db_file))

    monkeypatch.setattr(engine_mod, "_engine", None)
    monkeypatch.setattr(engine_mod, "_SessionLocal", None)

    upgrade_to_head(db_path=str(db_file))
    try:
        yield db_file
    finally:
        monkeypatch.setattr(engine_mod, "_engine", None)
        monkeypatch.setattr(engine_mod, "_SessionLocal", None)
