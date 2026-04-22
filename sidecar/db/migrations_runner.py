from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


def _resource_base() -> Path:
    """Locate the directory holding bundled resources (``alembic.ini`` + migrations).

    Two code paths:

    - **Dev / editable install**: ``__file__`` lives at
      ``<repo>/sidecar/db/migrations_runner.py``; ``parents[2]`` is the repo root
      which contains ``alembic.ini`` and the migrations directory.
    - **PyInstaller frozen bundle** (``sys.frozen = True``): resources are staged
      under ``sys._MEIPASS`` — in one-folder mode that's the ``_internal/`` dir
      next to the executable; in one-file mode it's a temp extraction dir.
      In either case we bundle ``alembic.ini`` + ``sidecar/db/migrations/`` to
      the root of _MEIPASS via the spec's ``datas`` list, so the dev-mode
      layout holds.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass is not None:
            return Path(meipass)
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _resource_base()
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
