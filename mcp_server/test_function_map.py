import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("chatbot_mcp_server_function_map_test", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


def test_function_map_returns_category_table_of_contents_then_selected_schemas():
    index = asyncio.run(server.browse_function_map(object()))

    assert index["version"] == 1
    assert index["categories"]
    # The map merges every callable MCP tool with Django chat-run guidance records.
    callable_tools = {tool.name for tool in server.mcp._tool_manager.list_tools()} - {"browse_function_map"}
    assert sum(item["count"] for item in index["categories"]) >= len(callable_tools)
    category = next(item for item in index["categories"] if item["id"] == "case_management")
    assert category["count"] > 0
    assert category["subcategories"]

    selected = asyncio.run(server.browse_function_map(object(), category["id"]))
    assert selected["category"]["id"] == "case_management"
    assert selected["functions"]
    assert all(record["categoryId"] == "case_management" for record in selected["functions"])
    assert all("inputSchema" in record for record in selected["functions"] if "mcp" in record["surfaces"])
    assert all(record["name"] != "browse_function_map" for record in selected["functions"])

    company_operations = asyncio.run(server.browse_function_map(object(), "company_operations"))
    company_details = next(
        record for record in company_operations["functions"] if record["name"] == "get_seriesai_company_details"
    )
    assert company_details["callerAliases"][0]["name"] == "retrieve_company_details"

    subgroup = category["subcategories"][0]
    selected_subgroup = asyncio.run(server.browse_function_map(object(), category["id"], subcategory_id=subgroup["id"]))
    assert selected_subgroup["subcategory"]["id"] == subgroup["id"]
    assert len(selected_subgroup["functions"]) == subgroup["count"]
    assert all(record["subcategoryId"] == subgroup["id"] for record in selected_subgroup["functions"])


def test_action_item_filters_match_django_api_query_contract(monkeypatch):
    captured = {}

    async def fake_get(ctx, path, params=None):
        captured.update(path=path, params=params)
        return {"items": []}

    monkeypatch.setattr(server, "_get", fake_get)
    asyncio.run(
        server.list_action_items(
            SimpleNamespace(),
            case_id="matter-1",
            status="open",
            assignee_id="user-1",
            app_id="4",
            open_only=True,
            search="renewal",
            include_archived=False,
            parent_task_id="task-1",
            result_limit=80,
            include_lifecycle_suggestions=True,
        )
    )

    assert captured == {
        "path": "/api/action-items/",
        "params": {
            "caseId": "matter-1",
            "status": "open",
            "assignedTo": "user-1",
            "appId": "4",
            "openOnly": True,
            "search": "renewal",
            "includeArchived": False,
            "parentTaskId": "task-1",
            "resultLimit": 80,
            "includeLifecycleSuggestions": True,
        },
    }


def test_important_date_filters_match_django_api_query_contract(monkeypatch):
    captured = {}

    async def fake_get(ctx, path, params=None):
        captured.update(path=path, params=params)
        return {"items": []}

    monkeypatch.setattr(server, "_get", fake_get)
    asyncio.run(
        server.list_case_important_dates(
            SimpleNamespace(),
            case_id="company-1",
            app_id="4",
            date_from="2026-01-01",
            date_to="2026-12-31",
            event_type="lifecycle_transition",
            source_kind="company_record",
            practice_area="startup",
            stage="seed",
            exclude_external_calendar=True,
            exclude_booking_projections=True,
            require_displayable=True,
            result_limit=50,
        )
    )

    assert captured == {
        "path": "/api/case-important-dates/",
        "params": {
            "caseId": "company-1",
            "appId": "4",
            "dateFrom": "2026-01-01",
            "dateTo": "2026-12-31",
            "eventType": "lifecycle_transition",
            "sourceKind": "company_record",
            "practiceArea": "startup",
            "stage": "seed",
            "excludeExternalCalendar": True,
            "excludeBookingProjections": True,
            "requireDisplayable": True,
            "resultLimit": 50,
        },
    }
