"""/api/health (the shared probe) and /api/status."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import SERVICE, __version__
from ..hoard_link.service import health_router
from .deps import services

router = APIRouter(prefix="/api")


def _extra(request: Request) -> dict:
    # Cheap on purpose: the launcher, the hub and the MCP bridge poll this.
    svc = services(request)
    return {"dataDirConfigured": request.app.state.config.data_dir_configured, "offline": svc.config.offline,
            "counts": svc.counts(), "scheduler": svc.scheduler.status()["running"]}


health = health_router(SERVICE, __version__, extra=_extra)


@router.get("/status")
def status(request: Request):
    return services(request).status()
