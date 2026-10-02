"""FastAPI application factory: request guard, API routers, PWA, static SPA."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import __version__
from .agenda import make_provider
from .api import ROUTERS
from .config import Config
from .hoard_link import family, fam_agenda
from .hoard_link.guard import install_guard
from .hoard_link.service import install_error_handlers, install_pwa, install_spa
from .services import Services

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(config: Config | None = None, services: Services | None = None) -> FastAPI:
    """``services`` lets tests inject a pre-built instance (fake transport, fake link)."""
    config = config or (services.config if services else Config.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or Services(config)
        app.state.services = svc
        svc.start()
        logging.getLogger("tantalus").info("Tantalus's Hoard %s - data in %s", __version__, config.data_dir)
        try:
            yield
        finally:
            svc.stop()

    app = FastAPI(title="Tantalus's Hoard", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.config = config
    family.configure("tantalus", str(config.data_dir), token_file=str(config.token_path))

    install_guard(app, port_getter=lambda: config.port, allowed_hosts=config.allowed_hosts, allowed_env="TANTALUS_ALLOWED_HOSTS")
    # the family agenda (release days): the hub asks with this app's bearer token
    fam_agenda.install_fastapi(app, make_provider(lambda: getattr(app.state, "services", None), lambda: f"http://127.0.0.1:{config.port or 5197}"))

    install_error_handlers(app)

    for router in ROUTERS:
        app.include_router(router)

    install_pwa(app, name="Tantalus's Hoard", short_name="Tantalus", theme="#4a2a6b", background="#140d1c", cache="tantalus-hoard",
                lang="es", static_dir=STATIC_DIR, version=__version__)
    install_spa(app, STATIC_DIR)  # last: the catch-all must come after every route

    return app
