from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sidecar import __version__, scheduler
from sidecar.api.assets import router as assets_router
from sidecar.api.health import router as health_router
from sidecar.api.prices import router as prices_router
from sidecar.config import settings
from sidecar.db.migrations_runner import upgrade_to_head
from sidecar.db.seed import seed_default_assets

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
            created = seed_default_assets()
            if created:
                logger.info("Seeded %d default assets", created)
        except Exception:
            logger.exception("Seeding default assets failed (continuing)")

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


app = FastAPI(title="FinTrack Sidecar", version=__version__, lifespan=lifespan)
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


def main() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Starting FinTrack sidecar on 127.0.0.1:%d", settings.port)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
