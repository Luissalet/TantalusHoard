"""API routers."""

from .agent import router as agent_router
from .health import health, router as status_router
from .ui import router as ui_router

ROUTERS = [health, status_router, ui_router, agent_router]
