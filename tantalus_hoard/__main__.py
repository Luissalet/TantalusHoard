"""`python -m tantalus_hoard` — run the app with uvicorn on 127.0.0.1 (the shared ``hoard_link.service.run_main``)."""

from __future__ import annotations

import os

from .hoard_link.service import run_main


def main(argv=None) -> int:
    if not os.environ.get("TANTALUS_PORT") and os.environ.get("PORT"):
        os.environ["TANTALUS_PORT"] = os.environ["PORT"]  # the generic PORT variable stays a fallback
    return run_main(service="tantalus-hoard", package="tantalus_hoard", default_port=5197, app_factory="tantalus_hoard.main:create_app",
                    data_dir_env="TANTALUS_DATA_DIR", port_env="TANTALUS_PORT", open_browser_default=False, title="Tantalus's Hoard",
                    argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
