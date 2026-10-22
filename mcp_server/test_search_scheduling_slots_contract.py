import asyncio
import importlib.util
import json
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
    with pytest.raises(ValueError, match="eventTypeId or slug is required"):
        asyncio.run(
            server.search_scheduling_slots(
                object(),
                start_at="2026-10-21T09:00:00Z",
                end_at="2026-10-21T17:00:00Z",
                time_zone="UTC",
            )
        )




def test_search_scheduling_slots_accepts_api_camel_case_arguments(monkeypatch):
    async def fake_get(_ctx, _path, params):
        return params

    monkeypatch.setattr(server, "_get", fake_get)
    result = asyncio.run(
        server.search_scheduling_slots(
            object(),
            startAt="2026-10-21T09:00:00Z",
            endAt="2026-10-21T17:00:00Z",
            timeZone="America/New_York",
            eventTypeId="event-type-1",
        )
    )
    assert result == {
        "startAt": "2026-10-21T09:00:00Z",
        "endAt": "2026-10-21T17:00:00Z",
        "timeZone": "America/New_York",
        "eventTypeId": "event-type-1",
    }


def test_search_scheduling_slots_accepts_legacy_action_arguments(monkeypatch):
    async def fake_get(_ctx, _path, params):
        return params

    monkeypatch.setattr(server, "_get", fake_get)
    result = asyncio.run(
        server.search_scheduling_slots(
            object(),
            start_time="2026-10-21T09:00:00Z",
            end_time="2026-10-21T17:00:00Z",
            event_type_id="event-type-1",
        )
    )
    assert result == {
        "startAt": "2026-10-21T09:00:00Z",
        "endAt": "2026-10-21T17:00:00Z",
        "eventTypeId": "event-type-1",
    }


def test_action_specs_require_canonical_time_bounds_and_keep_selectors_nullable():
    config_dir = MODULE_PATH.parents[1] / "config"
    for filename in (
        "juristai-agent-action-spec.json",
        "juristai-agent-action-spec.STAGED-SAFE.json",
    ):
        spec = json.loads((config_dir / filename).read_text(encoding="utf-8"))
        operation = spec["paths"]["/api/core/scheduling/slots"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}
        assert "never use it to set Outlook email follow-up due dates" in operation["description"]
        assert set(parameters) == {"startAt", "endAt", "eventTypeId", "slug", "timeZone"}
        assert {name for name, parameter in parameters.items() if parameter.get("required")} == {"startAt", "endAt"}
        assert all(parameters[name]["schema"].get("nullable") for name in ("eventTypeId", "slug", "timeZone"))
