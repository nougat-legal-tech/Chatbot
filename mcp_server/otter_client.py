"""Small, testable client for the Otter.ai Public API."""

from __future__ import annotations

import os
from typing import Any

import httpx

OTTER_API_BASE_URL = os.getenv("OTTER_API_BASE_URL", "https://api.otter.ai/v1").rstrip("/")
_CONNECT_TIMEOUT = float(os.getenv("OTTER_CONNECT_TIMEOUT", "5"))
_READ_TIMEOUT = float(os.getenv("OTTER_READ_TIMEOUT", "60"))


def _api_key() -> str:
    key = os.getenv("OTTER_API_KEY", "").strip()
    if not key:
        message = "OTTER_API_KEY is not configured"
        raise RuntimeError(message)
    return key


async def request(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    credential = str(api_key or _api_key()).strip()
    """Call an Otter read endpoint using the server-side API key."""
    async with httpx.AsyncClient(
        base_url=OTTER_API_BASE_URL,
        headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"},
        timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
        follow_redirects=True,
    ) as client:
        response = await client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        response.raise_for_status()
        return response.json()
