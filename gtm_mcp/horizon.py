"""FastMCP server factory for Prefect Horizon."""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Awaitable, Callable

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from .auth import build_fastmcp_auth, configure_adc_from_base64
from .safety import SafetyError
from .tools import MUTATION_TOOLS, READ_TOOLS

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


def _add_tool(server: FastMCP, function: ToolFunction, *, read_only: bool) -> None:
    server.add_tool(
        Tool.from_function(
            _with_structured_errors(function),
            annotations=ToolAnnotations(readOnlyHint=read_only),
        )
    )


def create_horizon_server() -> FastMCP:
    configure_adc_from_base64()
    server = FastMCP(
        "Google Tag Manager MCP Server",
        auth=build_fastmcp_auth(),
    )
    for function in READ_TOOLS:
        _add_tool(server, function, read_only=True)
    for function in MUTATION_TOOLS:
        _add_tool(server, function, read_only=False)
    return server
