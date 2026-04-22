from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "sidecar" / "db" / "migrations"


def _make_config(db_path: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if db_path is not None:
        cfg.attributes["db_path"] = db_path
    return cfg


def upgrade_to_head(db_path: str | None = None) -> None:
    command.upgrade(_make_config(db_path), "head")
