"""FastMCP server factory for Prefect Horizon."""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Awaitable, Callable

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from .auth import build_fastmcp_auth, configure_deployment_credentials
from .safety import SafetyError
from .tools import TOOL_DEFINITIONS

ToolFunction = Callable[..., Awaitable[Any]]


def _with_structured_errors(function: ToolFunction) -> ToolFunction:
    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return await function(*args, **kwargs)
        except SafetyError as exc:
            return {"error": {"type": type(exc).__name__, **exc.as_dict()}}
        except Exception as exc:
            return {
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            }

    wrapped.__signature__ = inspect.signature(function)  # type: ignore[attr-defined]
    return wrapped


def _add_tool(
    server: FastMCP,
    function: ToolFunction,
    *,
    title: str,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
) -> None:
    server.add_tool(
        Tool.from_function(
            _with_structured_errors(function),
            annotations=ToolAnnotations(
                title=title,
                readOnlyHint=read_only,
                destructiveHint=destructive,
                idempotentHint=idempotent,
                openWorldHint=True,
            ),
        )
    )


def create_horizon_server() -> FastMCP:
    configure_deployment_credentials()
    server = FastMCP(
        "Google Tag Manager MCP Server",
        auth=build_fastmcp_auth(),
    )
    for (
        function,
        title,
        read_only,
        destructive,
        idempotent,
    ) in TOOL_DEFINITIONS:
        _add_tool(
            server,
            function,
            title=title,
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
        )
    return server
