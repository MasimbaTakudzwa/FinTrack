from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from sidecar.db import models  # noqa: F401  -- register model metadata
from sidecar.db.base import Base
from sidecar.db.engine import make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_db_path() -> str | None:
    override = config.attributes.get("db_path")
    return override if isinstance(override, str) else None


def run_migrations_offline() -> None:
    from sidecar.config import settings

    db_path = _resolve_db_path() or settings.resolved_db_path()
    url = f"sqlite:///{db_path}"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_resolve_db_path())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
