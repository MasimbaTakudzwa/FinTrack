from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from sidecar import __version__

router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health/", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
