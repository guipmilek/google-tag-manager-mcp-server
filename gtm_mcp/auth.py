"""ADC and optional FastMCP OAuth configuration for Horizon."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from fastmcp.server.auth.providers.google import GoogleProvider

_ADC_PATH = Path("/tmp/google-tag-manager-adc.json")


def configure_deployment_credentials() -> Path | None:
    """Materialize Google ADC for Horizon.

    ``MCP_CREDENTIALS`` accepts either the shared credential envelope or a raw
    Google credential object. Existing deployments that still use
    ``GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64`` remain functional while
    they are migrated.
    """

    encoded = os.getenv("MCP_CREDENTIALS", "").strip()
    source_name = "MCP_CREDENTIALS"
    if not encoded:
        encoded = os.getenv(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64", ""
        ).strip()
        source_name = "GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64"
    if not encoded:
        return None
    try:
        payload = json.loads(
            base64.b64decode(encoded, validate=True).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"{source_name} must be a base64-encoded JSON object."
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{source_name} must decode to a JSON object.")
    credentials = payload.get("google_credentials")
    if credentials is None and isinstance(payload.get("type"), str):
        credentials = payload
    if credentials is None:
        return None
    if not isinstance(credentials, dict):
        raise RuntimeError(
            "MCP_CREDENTIALS.google_credentials must be a JSON object."
        )

    raw = json.dumps(credentials, separators=(",", ":")).encode("utf-8")
    credentials_path = _ADC_PATH
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = credentials_path.with_suffix(
        credentials_path.suffix + ".tmp"
    )
    temporary_path.write_bytes(raw)
    temporary_path.chmod(0o600)
    temporary_path.replace(credentials_path)
    credentials_path.chmod(0o600)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
    return credentials_path


def build_fastmcp_auth() -> GoogleProvider | None:
    client_id = os.getenv("GOOGLE_TAG_MANAGER_MCP_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_TAG_MANAGER_MCP_OAUTH_CLIENT_SECRET")
    if bool(client_id) != bool(client_secret):
        raise RuntimeError(
            "GOOGLE_TAG_MANAGER_MCP_OAUTH_CLIENT_ID and "
            "GOOGLE_TAG_MANAGER_MCP_OAUTH_CLIENT_SECRET must be configured together."
        )
    if not client_id or not client_secret:
        return None
    base_url = os.getenv(
        "GOOGLE_TAG_MANAGER_MCP_BASE_URL", "http://localhost:8080"
    ).rstrip("/")
    return GoogleProvider(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        required_scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
    )
