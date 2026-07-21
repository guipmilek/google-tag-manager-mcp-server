"""ADC and optional FastMCP OAuth configuration for Horizon."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from fastmcp.server.auth.providers.google import GoogleProvider


def configure_adc_from_base64() -> Path | None:
    encoded = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64", "").strip()
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64 is not valid base64 JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64 must decode to a JSON object."
        )

    credentials_path = Path(
        os.getenv("GOOGLE_TAG_MANAGER_ADC_PATH", "/tmp/google-tag-manager-adc.json")
    )
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = credentials_path.with_suffix(credentials_path.suffix + ".tmp")
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
