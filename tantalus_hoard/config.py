"""Process-level configuration read from the environment (never from the DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .hoard_link.appconfig import AppPaths, env_flag, env_float, env_int, env_str, load_dotenv
from .hoard_link.guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5197


@dataclass
class Config:
    """Everything the process needs before the database exists."""

    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    port: int = DEFAULT_PORT
    port_strict: bool = False
    allowed_hosts: tuple[str, ...] = ()
    data_dir_configured: bool = False
    http_timeout_s: float = 25.0
    offline: bool = False  # never touch the network (tests): every fetch answers "offline"
    scheduler: bool = True  # background checks; tests and the MCP-only mode switch it off
    browser: bool = True  # allow the headless-browser rung of the fetch ladder
    secrets: dict[str, str] = field(default_factory=dict)  # from .env (+ environment); never written back

    @property
    def paths(self) -> AppPaths:
        """The folder layout every app shares (database, token, url, logs, backend.json, cache)."""
        return AppPaths("tantalus", REPO_ROOT, self.data_dir, self.data_dir_configured)

    @property
    def db_path(self) -> Path:
        return self.paths.db_path

    @property
    def token_path(self) -> Path:
        return self.paths.token_path

    @property
    def url_path(self) -> Path:
        return self.paths.url_path

    @property
    def cache_dir(self) -> Path:
        return self.paths.cache_dir

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser-profile"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def logs_dir(self) -> Path:
        return self.paths.logs_dir

    @property
    def backend_json_path(self) -> Path:
        return self.paths.backend_json_path

    def secret(self, name: str) -> str:
        """Environment first, then .env. ``name`` without the TANTALUS_ prefix (e.g. TELEGRAM_TOKEN)."""
        key = f"TANTALUS_{name}"
        return (os.environ.get(key) or self.secrets.get(key) or "").strip()

    @classmethod
    def from_env(cls) -> "Config":
        raw_dir = env_str("TANTALUS_DATA_DIR")
        return cls(
            data_dir=Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / "data",
            port=env_int("TANTALUS_PORT", "PORT", default=DEFAULT_PORT, minimum=1, maximum=65535),
            port_strict=env_flag("PORT_STRICT"),
            allowed_hosts=parse_allowed_hosts(env_str("TANTALUS_ALLOWED_HOSTS")),
            data_dir_configured=bool(raw_dir),
            http_timeout_s=env_float("TANTALUS_HTTP_TIMEOUT_S", default=25.0, minimum=2.0, maximum=120.0),
            offline=env_flag("TANTALUS_OFFLINE"),
            scheduler=env_flag("TANTALUS_SCHEDULER", True),
            browser=env_flag("TANTALUS_BROWSER", True),
            secrets=load_dotenv(REPO_ROOT / ".env"),
        )
