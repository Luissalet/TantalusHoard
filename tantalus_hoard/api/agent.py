"""/api/agent/* — the bridge used by mcp_server.py (Bearer token from <DATA_DIR>/mcp-token): the shared agent router."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from ..agent_tools import AGENT_INSTRUCTIONS, call_tool, tool_catalog
from ..errors import TantalusError
from ..hoard_link.agentkit import make_agent_router


def _call(name: str, arguments: dict[str, Any], request: Request) -> Any:
    return call_tool(request.app.state.services, name, arguments)


router = make_agent_router(tools_fn=tool_catalog, call_fn=_call, token_fn=lambda request: request.app.state.services.token,
                           instructions=AGENT_INSTRUCTIONS, app_name="tantalus", error_types=(TantalusError,))
