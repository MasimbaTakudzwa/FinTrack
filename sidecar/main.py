from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from sidecar import __version__, scheduler
from sidecar.api.alerts import router as alerts_router
from sidecar.api.analytics import router as analytics_router
from sidecar.api.assets import router as assets_router
from sidecar.api.config import router as config_router
from sidecar.api.forecast import router as forecast_router
from sidecar.api.health import router as health_router
from sidecar.api.macro import router as macro_router
from sidecar.api.news import router as news_router
from sidecar.api.portfolio import router as portfolio_router
from sidecar.api.prices import router as prices_router
from sidecar.api.quotes import router as quotes_router
from sidecar.api.watchlists import router as watchlists_router
from sidecar.config import settings
from sidecar.db.migrations_runner import upgrade_to_head
from sidecar.db.seed import seed_all_defaults
from sidecar.services.watchlists import seed_default_watchlist

PARENT_WATCHDOG_INTERVAL_SECONDS = 2.0

ALLOWED_ORIGINS = [
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "https://tauri.localhost",
]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("Running database migrations...")
    upgrade_to_head()
    logger.info("Database ready at %s", settings.resolved_db_path())

    if settings.enable_seed:
        try:
            assets_created, indicators_created = seed_all_defaults()
            if assets_created or indicators_created:
                logger.info(
                    "Seeded %d assets and %d macro indicators",
                    assets_created,
                    indicators_created,
                )
        except Exception:
            logger.exception("Seeding defaults failed (continuing)")
        try:
            # Runs every startup: creates the default watchlist if missing, and
            # back-fills any newly-seeded assets onto it (idempotent).
            added = seed_default_watchlist()
            if added:
                logger.info("Appended %d assets to the default watchlist", added)
        except Exception:
            logger.exception("Seeding default watchlist failed (continuing)")

    if settings.enable_scheduler:
        try:
            scheduler.start()
        except Exception:
            logger.exception("Scheduler startup failed (continuing)")

    try:
        yield
    finally:
        if settings.enable_scheduler:
            scheduler.shutdown(wait=False)


AUTH_HEADER = "x-fintrack-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})


app = FastAPI(title="FinTrack Sidecar", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def auth_and_host_guard(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Enforce the per-launch token + a Host allowlist when a token is set.

    Localhost is reachable by every local process and, via DNS rebinding, by
    malicious web pages — CORS only governs browser *reads*, not whether a
    state-changing request executes server-side. So:

    * Host allowlist (127.0.0.1 / localhost) defeats DNS rebinding.
    * A per-launch bearer token (``X-FinTrack-Token``) gates everything except
      the health probe and CORS preflight, so other local processes can't
      drive the API.

    Both are skipped entirely when ``auth_token`` is empty (tests / standalone
    runs), so the default developer experience is unchanged.
    """
    token = settings.auth_token
    if token:
        host = request.headers.get("host", "")
        hostname = host.rsplit(":", 1)[0] if host else ""
        if hostname not in ALLOWED_HOSTS:
            return JSONResponse(status_code=403, content={"detail": "forbidden host"})
        path = request.url.path
        exempt = request.method == "OPTIONS" or path.startswith("/api/health")
        if not exempt and request.headers.get(AUTH_HEADER, "") != token:
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(assets_router)
app.include_router(prices_router)
app.include_router(quotes_router)
app.include_router(macro_router)
app.include_router(news_router)
app.include_router(watchlists_router)
app.include_router(alerts_router)
app.include_router(forecast_router)
app.include_router(analytics_router)
app.include_router(portfolio_router)
app.include_router(config_router)


def _watch_parent(initial_ppid: int) -> None:
    while True:
        time.sleep(PARENT_WATCHDOG_INTERVAL_SECONDS)
        current = os.getppid()
        if current != initial_ppid:
            logger.warning(
                "Parent process (pid=%d) is gone (now pid=%d); exiting",
                initial_ppid,
                current,
            )
            os._exit(0)


def _start_parent_watchdog() -> None:
    if os.environ.get("FINTRACK_DISABLE_PARENT_WATCHDOG") == "1":
        return
    initial_ppid = os.getppid()
    if initial_ppid == 1:
        return
    t = threading.Thread(
        target=_watch_parent,
        args=(initial_ppid,),
        name="parent-watchdog",
        daemon=True,
    )
    t.start()
    logger.info("Parent watchdog started (parent pid=%d)", initial_ppid)


def main() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _start_parent_watchdog()
    logger.info("Starting FinTrack sidecar on 127.0.0.1:%d", settings.port)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
