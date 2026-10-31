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
    registered_tools = server.mcp._tool_manager.list_tools()
    callable_tools = {tool.name for tool in registered_tools} - {"browse_function_map"}
    assert sum(item["count"] for item in index["categories"]) >= len(callable_tools)
    docusign_list = next(tool for tool in registered_tools if tool.name == "list_docusign_envelopes")
    assert "case_id" not in docusign_list.parameters.get("required", [])
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
    assert "company's profile" in company_details["mcpDescription"]
    assert company_details["callerAliases"][0]["name"] == "retrieve_company_details"
    assert company_details["callerAliases"][0]["verification"] == "verified_shared_operation_id"
    revenue_metrics = next(
        record for record in company_operations["functions"] if record["name"] == "get_seriesai_revenue_metrics"
    )
    assert revenue_metrics["appIds"] == ["3", "4", "5"]
    case_management = asyncio.run(server.browse_function_map(object(), "case_management"))
    important_dates = next(
        record for record in case_management["functions"] if record["name"] == "list_case_important_dates"
    )
    timeline_alias = next(
        alias for alias in important_dates["callerAliases"] if alias["name"] == "retrieve_company_timeline"
    )
    assert timeline_alias["verification"] == "operation_id_match_only"
    assert timeline_alias["defaultArguments"]["sort_order"] == "desc"
    assert timeline_alias["argumentBindings"]["lifecycleStage"] == "stage"
    deadlines_alias = next(
        alias for alias in important_dates["callerAliases"] if alias["name"] == "retrieve_company_deadlines"
    )
    assert deadlines_alias["verification"] == "operation_id_match_only"
    assert deadlines_alias["defaultArguments"]["upcoming_only"] is True
    assert deadlines_alias["argumentBindings"]["openOnly"] == "open_only"

    integrations = asyncio.run(server.browse_function_map(object(), "integrations_admin"))
    sharepoint_drives = next(
        record for record in integrations["functions"] if record["name"] == "list_sharepoint_site_drives"
    )
    assert sharepoint_drives["inputSchemasBySurface"]["chat_proxy"]["required"] == ["siteId"]
    assert sharepoint_drives["argumentBindingsBySurface"]["chat_proxy"] == {"siteId": "site_id"}
    sharepoint_children = next(
        record for record in integrations["functions"] if record["name"] == "list_sharepoint_drive_children"
    )
    assert sharepoint_children["inputSchemasBySurface"]["chat_proxy"]["required"] == ["driveId"]
    assert sharepoint_children["argumentBindingsBySurface"]["chat_proxy"] == {
        "driveId": "drive_id",
        "itemId": "item_id",
    }

    legacy_inventory_by_category = {
        "company_operations": {
            "add_cap_table_entry",
            "update_cap_table_entry",
            "advance_lifecycle_stage",
            "diligence_room_prepare",
            "diligence_room_query",
            "generate_founder_update",
            "generate_board_consent",
            "generate_safe",
            "generate_financing_doc",
            "cap_table_issuance_intent",
            "ownership_query",
            "round_status_query",
            "retrieve_company_marketing_automations",
            "retrieve_company_marketing_performance",
            "retrieve_company_revenue",
            "retrieve_company_sales",
            "retrieve_company_upcoming_campaigns",
            "retrieve_company_web_analytics",
            "seriesai_campaign_read_refusal",
            "summarize_financing_document",
        },
        "documents_research": {
            "classify_lifecycle_upload",
            "request_missing_lifecycle_documents",
            "document_amendment_request",
            "document_generation_request",
            "lawsuit_generation",
            "strategic_digest",
        },
        "scheduling": {"edit_company_deadlines", "deadline_acknowledgment"},
        "communications": {
            "archive_case_email_draft",
            "delete_case_email_message",
            "delete_ingested_email",
            "draft_followups",
            "general_chatbot",
            "help",
            "retrieve_account_email_drafts",
            "retrieve_case_email_drafts",
            "retrieve_case_email_timeline",
            "retrieve_inbox_review_items",
            "update_case_email_draft",
            "update_case_email_message",
        },
        "case_management": {
            "delete_case_individual",
            "named_case_scope",
            "read_case_individual_dossier",
            "retrieve_case_individuals",
            "update_case_individual",
        },
        "signatures_approvals": {
            "request_signature",
            "retrieve_signature_status",
            "safe_review_approval",
            "signature_approval",
        },
        "contracts_workflows": {"start_workflow_gap_plan"},
    }
    for category_id, expected_names in legacy_inventory_by_category.items():
        inventory_category = asyncio.run(server.browse_function_map(object(), category_id))
        inventory_records = {record["name"]: record for record in inventory_category["functions"]}
        assert expected_names <= inventory_records.keys()
        for name in expected_names:
            record = inventory_records[name]
            assert record["mcpCallable"] is False
            assert "intent_handler" in record["surfaces"]
            assert "inputSchema" not in record
            assert "operationIds" not in record

    subgroup = category["subcategories"][0]
    selected_subgroup = asyncio.run(server.browse_function_map(object(), category["id"], subcategory_id=subgroup["id"]))
    assert selected_subgroup["subcategory"]["id"] == subgroup["id"]
    assert len(selected_subgroup["functions"]) == subgroup["count"]
    assert all(record["subcategoryId"] == subgroup["id"] for record in selected_subgroup["functions"])

    contract_category = next(item for item in index["categories"] if item["id"] == "contracts_workflows")
    workflow_group = next(
        item for item in contract_category["subcategories"] if item["id"] == "manage_workflow"
    )
    workflow_tools = asyncio.run(
        server.browse_function_map(object(), "contracts_workflows", subcategory_id="manage_workflow")
    )
    workflow_names = {record["name"] for record in workflow_tools["functions"]}
    assert {
        "triage_in_house_request",
        "transition_in_house_request",
        "transition_in_house_obligation",
        "decide_in_house_review_finding",
    } <= workflow_names
    assert workflow_group["count"] == len(workflow_names)


def test_in_house_request_list_is_callable_and_remains_app_scoped_when_allowlisted(monkeypatch):
    selected = asyncio.run(
        server.browse_function_map(object(), "contracts_workflows", ["retrieve_in_house_requests"])
    )
    record = selected["functions"][0]
    assert record["name"] == "retrieve_in_house_requests"
    assert record["appIds"] == ("5",)
    assert {"chat_run", "chat_proxy", "mcp"}.issubset(record["surfaces"])
    assert record["mcpCallable"] is True

    obligations = asyncio.run(
        server.browse_function_map(object(), "contracts_workflows", ["retrieve_in_house_obligations"])
    )["functions"][0]
    assert obligations["name"] == "retrieve_in_house_obligations"
    assert obligations["appIds"] == ("5",)
    case_id_schema = obligations["inputSchema"]["properties"]["case_id"]
    assert {"type": "string"} in case_id_schema.get("anyOf", [case_id_schema])
    assert "case_id" not in obligations["inputSchema"].get("required", [])

    for tool_name in (
        "retrieve_in_house_matter",
        "retrieve_in_house_contracts",
        "retrieve_in_house_counterparties",
        "search_in_house_knowledge",
    ):
        scoped_tool = asyncio.run(
            server.browse_function_map(object(), "contracts_workflows", [tool_name])
        )["functions"][0]
        assert scoped_tool["name"] == tool_name
        assert scoped_tool["appIds"] == ("5",)
        assert scoped_tool["mcpCallable"] is True
    assert obligations["appIds"] == ("5",)

    captured = {}

    async def fake_get(ctx, path, params=None):
        captured.update(ctx=ctx, path=path, params=params)
        return [{"id": "request-1"}]

    monkeypatch.setattr(server, "_get", fake_get)
    ctx = object()
    assert asyncio.run(server.retrieve_in_house_requests(ctx)) == [{"id": "request-1"}]
    assert captured == {"ctx": ctx, "path": "/api/in-house/requests/", "params": None}


def test_lifecycle_stage_capability_uses_company_operations_category():
    result = server._browse_function_map([{"name": "advance_lifecycle_stage"}])

    assert [category["id"] for category in result["categories"]] == ["company_operations"]


def test_in_house_category_map_preserves_required_idempotency_inputs():
    selected = asyncio.run(server.browse_function_map(object(), "contracts_workflows"))
    records = {record["name"]: record for record in selected["functions"]}

    assert records["create_in_house_request"]["requiredInputs"] == [
        "subject", "description", "idempotencyKey"
    ]
    assert records["create_in_house_request_comment"]["requiredInputs"] == [
        "request_id", "body", "idempotencyKey"
    ]
    assert records["transition_in_house_request"]["requiredInputs"] == [
        "request_id", "status", "idempotencyKey"
    ]


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
            "itemId": None,
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
            open_only=True,
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
            "openOnly": True,
            "resultLimit": 50,
        },
    }
def test_deadline_acknowledgment_forwards_approval_to_shared_edit_api(monkeypatch):
    calls = []

    async def fake_post(ctx, path, payload):
        calls.append((ctx, path, payload))
        return payload

    monkeypatch.setattr(server, "_post", fake_post)
    ctx = object()
    result = asyncio.run(
        server.edit_case_important_dates(
            ctx,
            case_id="case-1",
            operation="acknowledge",
            deadline_id="deadline-1",
            app_id="4",
            approved=True,
        )
    )

    assert result["approved"] is True
    assert calls == [
        (
            ctx,
            "/api/edit-case-important-dates/",
            {
                "caseId": "case-1",
                "operation": "acknowledge",
                "deadlineId": "deadline-1",
                "appId": "4",
                "approved": True,
            },
        )
    ]
