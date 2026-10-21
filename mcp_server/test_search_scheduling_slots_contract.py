import asyncio
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("chatbot_mcp_server_scheduling_test", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


def test_search_scheduling_slots_uses_django_query_contract(monkeypatch):
    calls = []

    async def fake_get(ctx, path, params):
        calls.append((ctx, path, params))
        return params

    monkeypatch.setattr(server, "_get", fake_get)
    ctx = object()

    result = asyncio.run(
        server.search_scheduling_slots(
            ctx,
            start_at="2026-10-21T09:00:00Z",
            end_at="2026-10-21T17:00:00Z",
            time_zone="America/New_York",
            event_type_id="event-type-1",
        )
    )

    assert result == {
        "startAt": "2026-10-21T09:00:00Z",
        "endAt": "2026-10-21T17:00:00Z",
        "timeZone": "America/New_York",
        "eventTypeId": "event-type-1",
    }
    assert calls == [(ctx, "/api/scheduling/slots/", result)]


def test_search_scheduling_slots_accepts_slug_and_requires_selector(monkeypatch):
    async def fake_get(_ctx, _path, params):
        return params

    monkeypatch.setattr(server, "_get", fake_get)
    result = asyncio.run(
        server.search_scheduling_slots(
            object(),
            start_at="2026-10-21T09:00:00Z",
            end_at="2026-10-21T17:00:00Z",
            time_zone="UTC",
            slug="intro-call",
        )
    )
    assert result["slug"] == "intro-call"
    with pytest.raises(ValueError, match="event_type_id or slug is required"):
        asyncio.run(
            server.search_scheduling_slots(
                object(),
                start_at="2026-10-21T09:00:00Z",
                end_at="2026-10-21T17:00:00Z",
                time_zone="UTC",
            )
        )
