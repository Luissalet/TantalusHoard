"""One error type for every expected failure, so the API, the agent tools and the UI report it the same way."""

from __future__ import annotations

from typing import Any, Mapping

from .hoard_link.agentkit import AppError


class TantalusError(AppError):
    """An expected, explainable failure: a stable ``code``, a human ``message`` and an actionable ``hint``.

    ``status`` is the HTTP status the REST layer uses; ``details`` carries structured extras (for
    example the per-field issues of an invalid strategy spec). The body and the status table come from the commons' ``AppError``.
    """

    STATUS: Mapping[str, int] = {
        **AppError.STATUS,
        "fetch_failed": 502,
        "blocked": 409,
        "needs_human": 409,
        "rate_limited": 429,
        "unsafe_url": 400,
        "robots_disallowed": 409,
        "channel_not_configured": 400,
    }

    def __init__(self, code: str, message: str, hint: str = "", *, status: int | None = None, **details: Any):
        super().__init__(code, message, hint=hint, status=status, details=details)
