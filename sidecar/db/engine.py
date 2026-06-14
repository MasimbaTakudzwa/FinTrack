from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from sidecar.config import settings


def make_engine(db_path: str | None = None) -> Engine:
    path = db_path or settings.resolved_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{path}"
    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    _install_pragmas(engine)
    return engine


def _install_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def set_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
# Guards lazy init of the globals above. The scheduler fires several jobs with
# ``next_run_time=now`` on cold start, so multiple ThreadPoolExecutor threads
# can race into ``get_engine``/``get_session_factory`` the very first time —
# without this lock two threads could each build an engine and bind sessions
# to a discarded one.
_init_lock = Lock()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        with _init_lock:
            if _engine is None:
                _engine = make_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        # Resolve the engine before taking the lock — get_engine() manages its
        # own locking, and _init_lock is non-reentrant so calling it while held
        # would deadlock.
        engine = get_engine()
        with _init_lock:
            if _SessionLocal is None:
                _SessionLocal = sessionmaker(
                    bind=engine, autoflush=False, expire_on_commit=False
                )
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    s = get_session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
