"""Prefect Horizon entrypoint for the Google Tag Manager MCP server."""

from gtm_mcp.horizon import create_horizon_server

mcp = create_horizon_server()
