"""Google Tag Manager API v2 client helpers."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any, Callable

import google.auth
from googleapiclient.discovery import build

_GTM_SCOPES = [
    "https://www.googleapis.com/auth/tagmanager.readonly",
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/tagmanager.delete.containers",
    "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
    "https://www.googleapis.com/auth/tagmanager.publish",
]


@lru_cache(maxsize=1)
def get_gtm_service() -> Any:
    credentials, _ = google.auth.default(scopes=_GTM_SCOPES)
    return build(
        "tagmanager",
        "v2",
        credentials=credentials,
        cache_discovery=False,
    )


async def execute_request(request: Any) -> dict[str, Any]:
    result = await asyncio.to_thread(request.execute)
    return result or {}


async def paginate(
    factory: Callable[[str | None], Any],
    *,
    item_key: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        response = await execute_request(factory(token))
        page_items = response.get(item_key, [])
        if isinstance(page_items, list):
            items.extend(item for item in page_items if isinstance(item, dict))
        token = response.get("nextPageToken")
        if not token:
            return items
