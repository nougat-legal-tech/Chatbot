"""
Vienna Django MCP Server
--------------------------
Wraps the django-hub REST API as MCP tools so LibreChat's chat proxy can
invoke them directly rather than routing through the intentHandler.

Run:
    uvicorn mcp_server.server:app --host 0.0.0.0 --port 8001

Required env vars:
    DJANGO_API_BASE_URL   Base URL of the django-hub API (e.g. https://api-dev.juristai.org)
    MCP_SERVER_SECRET     Optional shared secret that LibreChat includes in X-MCP-Secret
                          to gate access (leave blank to disable the check).

The server picks up the caller's Authorization header (Bearer <token>) from the
MCP request context and forwards it verbatim to every Django endpoint so Django's
normal JWT auth applies.
"""

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import Context
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Django is the source of truth for the function registry. The Chatbot sidecar
# image ships this exact file plus vendored copies of the registry modules in
# mcp_server/, so fall back to those when the Django package is absent.
try:
    from jurist_backend.core.services.callable_function_registry import universal_function_records
    from jurist_backend.core.services.function_taxonomy import browse_function_map as _browse_function_map
except ModuleNotFoundError:
    try:
        from mcp_server.callable_function_registry import universal_function_records
        from mcp_server.function_taxonomy import browse_function_map as _browse_function_map
    except ModuleNotFoundError:  # Tests may load this file directly from its directory.
        from callable_function_registry import universal_function_records
        from function_taxonomy import browse_function_map as _browse_function_map
try:
    from mcp_server.otter_client import request as otter_request
except ModuleNotFoundError:
    from otter_client import request as otter_request

DJANGO_API_BASE_URL = os.getenv("DJANGO_API_BASE_URL", "https://api-dev.juristai.org").rstrip("/")
MCP_SERVER_SECRET = os.getenv("MCP_SERVER_SECRET", "")
_CONNECT_TIMEOUT = float(os.getenv("MCP_DJANGO_CONNECT_TIMEOUT", "5"))
_READ_TIMEOUT = float(os.getenv("MCP_DJANGO_READ_TIMEOUT", "60"))


# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "juristai-django",
    # Uvicorn listens on all interfaces in the ECS image. Passing the same
    # non-loopback host here prevents FastMCP from auto-enabling localhost-only
    # DNS-rebinding protection behind the ALB.
    host=os.getenv("MCP_SERVER_HOST", "0.0.0.0"),  # noqa: S104 - served behind the ALB, see above
    port=int(os.getenv("MCP_SERVER_PORT", "8001")),
    instructions=(
        "Tools for the Vienna legal platform. "
        "Always supply caseId when the tool requires it. "
        "Prefer read-only tools unless the user explicitly requests a mutation."
    ),
)


# ---------------------------------------------------------------------------
# HTTP helper — forwards the caller's auth token to django-hub
# ---------------------------------------------------------------------------


def _client(token: str | None) -> httpx.AsyncClient:
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        scheme, separator, credentials = token.partition(" ")
        if separator and scheme.casefold() == "bearer":
            headers["Authorization"] = f"Bearer {credentials}"
        else:
            headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        base_url=DJANGO_API_BASE_URL,
        headers=headers,
        timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
        follow_redirects=True,
    )


def _token(ctx: Context) -> str | None:
    request_context = getattr(ctx, "request_context", None)
    request = getattr(request_context, "request", None)
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    auth = headers.get("authorization") or headers.get("Authorization")
    return auth or None


# django-hub mounts Django APIs at /api/core/ (config/urls.py). Bare Django
# paths therefore need normalizing, but the LibreChat proxy surface deliberately
# remains at /api/{agents,messages,convos,prompts}/ and must not be rewritten.
_PRESERVED_API_PREFIXES = (
    "/api/core",
    "/api/agents",
    "/api/messages",
    "/api/convos",
    "/api/prompts",
)


def _has_api_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def _resolve_path(path: str) -> str:
    if any(_has_api_prefix(path, prefix) for prefix in _PRESERVED_API_PREFIXES):
        return path
    if path.startswith("/api/"):
        return f"/api/core/{path.removeprefix('/api/')}"
    return path


async def _get(ctx: Context, path: str, params: dict | None = None) -> dict[str, Any]:
    async with _client(_token(ctx)) as c:
        r = await c.get(_resolve_path(path), params={k: v for k, v in (params or {}).items() if v is not None})
        r.raise_for_status()
        return r.json()


async def _post(ctx: Context, path: str, body: dict) -> dict[str, Any]:
    async with _client(_token(ctx)) as c:
        resolved_path = _resolve_path(path)
        r = await c.post(resolved_path, content=json.dumps(body))
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Django's add-case endpoint returns field-level serializer errors
            # as a safe JSON body. Preserve those details across MCP so Vienna
            # can ask for a correction instead of reporting only "HTTP 400".
            if resolved_path == "/api/core/add-case/" and r.status_code == 400:
                try:
                    validation_errors = r.json()
                except ValueError:
                    validation_errors = None
                if isinstance(validation_errors, (dict, list)):
                    detail = json.dumps(validation_errors, ensure_ascii=False, separators=(",", ":"))[:4000]
                    message = f"{exc}. Django validation details: {detail}"
                    raise httpx.HTTPStatusError(
                        message,
                        request=exc.request,
                        response=exc.response,
                    ) from exc
            raise
        return r.json()


_SERIESAI_APP_IDS = frozenset({"3", "4"})


def _seriesai_litigation_block(app_id: str | None, operation: str) -> dict[str, str] | None:
    """Fail fast for litigation tools in the startups/VC workspace; Django enforces the same rule server-side."""
    if str(app_id or "").strip() not in _SERIESAI_APP_IDS:
        return None
    return {
        "error": "This litigation workflow is not available for Vienna.",
        "code": "SERIESAI_LITIGATION_WORKFLOW_UNAVAILABLE",
        "operation": operation,
    }


async def _patch(ctx: Context, path: str, body: dict) -> dict[str, Any]:
    async with _client(_token(ctx)) as c:
        path = _resolve_path(path)
        r = await c.patch(path, content=json.dumps(body))
        r.raise_for_status()
        return r.json()


async def _delete(ctx: Context, path: str, params: dict | None = None) -> dict[str, Any]:
    async with _client(_token(ctx)) as c:
        r = await c.delete(
            _resolve_path(path),
            params={k: v for k, v in (params or {}).items() if v is not None},
        )
        r.raise_for_status()
        return r.json() if r.content else {"status": "deleted"}


@mcp.tool(
    description="Update an allowed Clio Manage matter field after the user explicitly confirms with 'CONFIRM CLIO UPDATE'.",
)
async def update_clio_matter(
    ctx: Context,
    matter_id: str,
    fields: dict[str, Any],
    confirmation: str | None = None,
) -> dict[str, Any]:
    if confirmation != "CONFIRM CLIO UPDATE":
        return {
            "error": "confirmation_required",
            "message": "Ask the user to explicitly confirm the Clio update before retrying.",
        }
    return await _patch(
        ctx,
        f"/api/core/integrations/clio/matters/{matter_id}/",
        {"fields": fields, "confirmation": confirmation},
    )


@mcp.tool(
    description="Update an allowed Clio Manage contact field after the user explicitly confirms with 'CONFIRM CLIO UPDATE'.",
)
async def update_clio_contact(
    ctx: Context,
    contact_id: str,
    fields: dict[str, Any],
    confirmation: str | None = None,
) -> dict[str, Any]:
    if confirmation != "CONFIRM CLIO UPDATE":
        return {
            "error": "confirmation_required",
            "message": "Ask the user to explicitly confirm the Clio update before retrying.",
        }
    return await _patch(
        ctx,
        f"/api/core/integrations/clio/contacts/{contact_id}/",
        {"fields": fields, "confirmation": confirmation},
    )


@mcp.tool(description="List Clio Manage matters visible to the authenticated user, optionally filtered by search text.")
async def list_clio_matters(ctx: Context, search: str | None = None) -> dict[str, Any]:
    payload = await _get(ctx, "/api/core/integrations/clio/matters/")
    if not search:
        return payload
    needle = search.casefold()
    data = payload.get("data")
    if isinstance(data, list):
        payload["data"] = [item for item in data if needle in json.dumps(item, ensure_ascii=False).casefold()]
    return payload


# ===========================================================================
# READ tools (Milestone 1 + supplemental read-only)
# ===========================================================================


@mcp.tool(description="List the cases assigned to the authenticated user.")
async def list_my_cases(ctx: Context) -> dict:
    return await _get(ctx, "/api/my-cases/")


@mcp.tool(
    description=(
        "Fetch core metadata for a case or CourtListener docket. Use case_id for a matter already in the workspace, "
        "or docket_id for a docket returned by search_case."
    )
)
async def get_case_metadata(
    ctx: Context,
    case_id: str | None = None,
    app_id: str | None = None,
    docket_id: str | None = None,
) -> dict:
    payload: dict[str, str] = {}
    if case_id:
        payload["caseId"] = case_id
    if docket_id:
        payload["docketId"] = docket_id
    if not payload:
        raise ValueError("Either case_id or docket_id is required.")
    if app_id:
        payload["appId"] = app_id
    return await _post(ctx, "/api/get-case-metadata/", payload)


@mcp.tool(description="List action items and recommendations for a case.")
async def list_action_items(
    ctx: Context,
    case_id: str,
    status: str | None = None,
    assignee_id: str | None = None,
    app_id: str | None = None,
    open_only: bool | None = None,
    search: str | None = None,
    include_archived: bool | None = None,
    parent_task_id: str | None = None,
    result_limit: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/action-items/",
        {
            "caseId": case_id,
            "status": status,
            "assignedTo": assignee_id,
            "appId": app_id,
            "openOnly": open_only,
            "search": search,
            "includeArchived": include_archived,
            "parentTaskId": parent_task_id,
            "resultLimit": result_limit,
        },
    )


def _set_payload_value(payload: dict[str, object], key: str, value: object) -> None:
    if value is not None:
        payload[key] = value


@mcp.tool(description="Bulk update or delete matching action items for a case.")
async def bulk_edit_case_action_items(
    ctx: Context,
    case_id: str,
    operation: str,
    search_text: str | None = None,
    current_status: str | None = None,
    filter_status: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    apply_to_all: bool | None = None,
    status: str | None = None,
    completed: bool | None = None,
    completion_reason: str | None = None,
    actual_minutes: int | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "operation": operation}
    optional_values = (
        ("searchText", search_text),
        ("currentStatus", current_status),
        ("filterStatus", filter_status),
        ("priority", priority),
        ("assignee", assignee),
        ("applyToAll", apply_to_all),
        ("status", status),
        ("completed", completed),
        ("completionReason", completion_reason),
        ("actualMinutes", actual_minutes),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    _set_payload_value(payload, "appId", app_id)
    return await _post(ctx, "/api/action-items/bulk/", payload)


@mcp.tool(
    description="Create, update, or delete one case action item with legacy-style target resolution. Supply app_id when the case ID exists in multiple products."
)
async def edit_case_action_items(
    ctx: Context,
    case_id: str,
    operation: str,
    item_id: str | None = None,
    title: str | None = None,
    description: str | None = None,
    contains: str | None = None,
    status: str | None = None,
    completed: bool | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    assignee_email: str | None = None,
    assignee_user_id: str | None = None,
    suggested_due_date: str | None = None,
    date: str | None = None,
    estimated_minutes: int | None = None,
    actual_minutes: str | None = None,
    hourly_rate: str | None = None,
    completion_reason: str | None = None,
    source: str | None = None,
    parent_task_id: str | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "operation": operation}
    optional_values = (
        ("itemId", item_id),
        ("title", title),
        ("description", description),
        ("contains", contains),
        ("status", status),
        ("completed", completed),
        ("priority", priority),
        ("assignee", assignee),
        ("assigneeEmail", assignee_email),
        ("assigneeUserId", assignee_user_id),
        ("suggestedDueDate", suggested_due_date),
        ("date", date),
        ("estimatedMinutes", estimated_minutes),
        ("actualMinutes", actual_minutes),
        ("hourlyRate", hourly_rate),
        ("completionReason", completion_reason),
        ("source", source),
        ("parentTaskId", parent_task_id),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    _set_payload_value(payload, "appId", app_id)
    return await _post(ctx, "/api/edit-case-action-items/", payload)


@mcp.tool(
    description=(
        "List deadlines and important dates for an authorized case. Optionally filter by date range, event type, "
        "source kind, practice area, or lifecycle stage; optionally exclude provider-imported calendar rows; "
        "optionally require at least one displayable date or event label, and limit the returned items."
    )
)
async def list_case_important_dates(
    ctx: Context,
    case_id: str,
    app_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    event_type: str | None = None,
    source_kind: str | None = None,
    practice_area: str | None = None,
    stage: str | None = None,
    exclude_external_calendar: bool | None = None,
    require_displayable: bool | None = None,
    result_limit: int | None = None,
) -> dict:
    params = {"caseId": case_id}
    for key, value in (
        ("appId", app_id),
        ("dateFrom", date_from),
        ("dateTo", date_to),
        ("eventType", event_type),
        ("sourceKind", source_kind),
        ("practiceArea", practice_area),
        ("stage", stage),
        ("excludeExternalCalendar", exclude_external_calendar),
        ("requireDisplayable", require_displayable),
        ("resultLimit", result_limit),
    ):
        _set_payload_value(params, key, value)
    return await _get(ctx, "/api/case-important-dates/", params)


@mcp.tool(description="List calendar-style upcoming events and deadlines for a case.")
async def get_case_calendar(ctx: Context, case_id: str, app_id: str | None = None) -> dict:
    return await _get(ctx, "/api/case-calendar/", {"caseId": case_id, "appId": app_id})


@mcp.tool(description="Fetch docket timeline entries for a case.")
async def list_case_timeline(ctx: Context, case_id: str, app_id: str | None = None) -> dict:
    return await _get(ctx, "/api/case-timeline/", {"caseId": case_id, "appId": app_id})


@mcp.tool(description="Fetch the most recent docket entry for a case.")
async def get_latest_docket_entry(ctx: Context, case_id: str, app_id: str | None = None) -> dict:
    return await _get(ctx, "/api/latest-docket-entry/", {"caseId": case_id, "appId": app_id})


@mcp.tool(
    description=(
        "Retrieve a billing summary for a case. Returns billableTaskCount and summaryText, "
        "where summaryText is a JSON-encoded digest with billingHeader, overview metrics, "
        "topBillers, topTasks, missedOpportunities (completed work not billed, e.g. no hourly "
        "rate set or no time logged), upcoming work due in the next 7 days, and a plainText "
        "field. Quote the plainText field when summarizing for the user."
    ),
)
async def retrieve_case_billing_summary(ctx: Context, case_id: str) -> dict:
    return await _get(ctx, f"/api/core/billing/cases/{case_id}/summary")


@mcp.tool(description="List members of a legal team.")
async def list_legal_team_members(ctx: Context, legal_team_id: str) -> dict:
    return await _get(ctx, "/api/legal-team-members/", {"legalTeamId": legal_team_id})


@mcp.tool(description="List members of the current user's organization.")
async def list_organization_members(ctx: Context) -> dict:
    return await _get(ctx, "/api/organization-members/")


@mcp.tool(description="Get the authenticated firm's agent approval policy.")
async def get_organization_approval_policy(ctx: Context) -> dict:
    return await _get(ctx, "/api/organization-approval-policy/")


@mcp.tool(description="Update the firm's agent approval policy after explicit confirmation.")
async def update_organization_approval_policy(
    ctx: Context,
    require_approval_for_all: bool = False,
    required_actions: list[str] | None = None,
    confirmation: str | None = None,
) -> dict:
    if confirmation != "CONFIRM APPROVAL POLICY CHANGE":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM APPROVAL POLICY CHANGE",
            "message": "Review the approval policy before applying this firm-wide change.",
        }
    return await _patch(
        ctx,
        "/api/organization-approval-policy/",
        {
            "approvalPolicy": {
                "requireApprovalForAll": require_approval_for_all,
                "requiredActions": required_actions or [],
            }
        },
    )


@mcp.tool(
    description="Search authenticated, already-ingested firm email by subject, sender, recipient, or message text."
)
async def search_email_messages(
    ctx: Context,
    query: str,
    integration_id: str | None = None,
    case_id: str | None = None,
    limit: int | None = None,
) -> dict:
    if len(str(query or "").strip()) < 2:
        raise ValueError("query must contain at least 2 characters")
    return await _get(
        ctx,
        "/api/integrations/email/messages/search",
        {"query": query, "integrationId": integration_id, "caseId": case_id, "limit": limit},
    )


def _normalized_firm_communication_items(sources: dict[str, object], *, limit: int) -> list[dict[str, object]]:
    """Expose a small common envelope while retaining each provider payload."""
    items: list[dict[str, object]] = []
    for source, payload in sources.items():
        if not isinstance(payload, dict):
            continue
        candidates = next(
            (
                payload.get(key)
                for key in ("items", "messages", "results", "data")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
        for raw in candidates:
            if not isinstance(raw, dict):
                continue
            thread_id = (
                raw.get("threadId")
                or raw.get("thread_id")
                or raw.get("threadTs")
                or raw.get("thread_ts")
                or raw.get("conversationId")
                or raw.get("conversation_id")
                or raw.get("conversationKey")
            )
            matter_id = raw.get("matterId") or raw.get("matter_id") or raw.get("caseId")
            items.append(
                {
                    "source": source,
                    "id": raw.get("id") or raw.get("messageId") or raw.get("ts") or raw.get("internetMessageId"),
                    "threadId": thread_id,
                    "timestamp": raw.get("timestamp")
                    or raw.get("createdAt")
                    or raw.get("receivedAt")
                    or raw.get("date"),
                    "text": raw.get("text") or raw.get("body") or raw.get("snippet") or raw.get("subject"),
                    "sender": raw.get("sender") or raw.get("from") or raw.get("author"),
                    "channel": raw.get("channelName") or raw.get("channelId") or raw.get("chatId"),
                    "caseId": matter_id,
                    "matterId": matter_id,
                    "raw": raw,
                }
            )
    items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return items[:limit]


@mcp.tool(
    description=(
        "Search the explicitly available firm communication sources in one call. Provide a connected "
        "Teams connection id when Teams should be searched; Slack and ingested email are included when "
        "available to the authenticated user. Results are tagged by source and any unavailable source "
        "is reported rather than treated as an empty search. An optional case_id narrows stored email; "
        "provider messages are returned with only the matter metadata the provider supplies."
    ),
)
async def search_firm_communications(
    ctx: Context,
    query: str,
    slack_app_id: str | None = None,
    teams_connection_id: str | None = None,
    teams_app_id: str | None = None,
    email_integration_id: str | None = None,
    case_id: str | None = None,
    days: int | None = None,
    limit: int | None = None,
) -> dict:
    normalized_query = str(query or "").strip()
    if len(normalized_query) < 2:
        raise ValueError("query must contain at least 2 characters")
    try:
        effective_limit = 20 if limit is None else max(1, min(int(limit), 50))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    calls = {
        "slack": search_slack_messages(
            ctx, query=normalized_query, app_id=slack_app_id, days=days, limit=effective_limit
        ),
        "email": search_email_messages(
            ctx, query=normalized_query, integration_id=email_integration_id, case_id=case_id, limit=effective_limit
        ),
    }
    if teams_connection_id:
        calls["teams"] = search_teams_messages(
            ctx,
            connection_id=teams_connection_id,
            query=normalized_query,
            app_id=teams_app_id,
            limit=min(effective_limit, 25),
        )
    results = await asyncio.gather(*calls.values(), return_exceptions=True)
    sources: dict[str, object] = {}
    errors: dict[str, str] = {}
    for source, result in zip(calls, results, strict=False):
        if isinstance(result, Exception):
            errors[source] = str(result)
        else:
            sources[source] = result
    return {
        "query": normalized_query,
        "items": _normalized_firm_communication_items(sources, limit=effective_limit),
        "sources": sources,
        "errors": errors,
        "searchedSources": sorted(sources),
        "unsearchedSources": sorted(set(calls) - set(sources)),
    }


@mcp.tool(description="Record thumbs-up or thumbs-down feedback for an assistant message.")
async def record_assistant_message_feedback(
    ctx: Context,
    conversation_id: str,
    message_id: str,
    rating: str,
    app_id: str | None = None,
    organization_id: str | None = None,
    case_id: str | None = None,
    comment: str | None = None,
    reason: str | None = None,
    source: str | None = None,
    idempotency_key: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/chat/messages/feedback",
        {
            k: v
            for k, v in {
                "conversationId": conversation_id,
                "messageId": message_id,
                "rating": rating,
                "appId": app_id,
                "organizationId": organization_id,
                "caseId": case_id,
                "comment": comment,
                "reason": reason,
                "source": source,
                "idempotencyKey": idempotency_key,
                "metadata": metadata,
            }.items()
            if v is not None
        },
    )


@mcp.tool(description="Answer questions about people involved in a case using dossier metadata.")
async def read_people_dossiers(
    ctx: Context,
    case_id: str,
    app_id: str,
    query: str | None = None,
    name: str | None = None,
    email: str | None = None,
    include_all: bool = False,
) -> dict:
    """Read a case person's dossier using the API's supported lookup fields.

    ``query`` is retained as a compatibility alias for already-published MCP
    schemas, but the API contract is ``name``/``email``.  App scope matters for
    Vienna because the same person can have one record per application.
    """
    body = {"caseId": case_id}
    resolved_name = name or query
    if resolved_name is not None:
        body["name"] = resolved_name
    if email is not None:
        body["email"] = email
    if include_all:
        body["includeAll"] = True
    body["appId"] = app_id
    return await _post(ctx, "/api/read-people-dossiers/", body)


@mcp.tool(description="Search cases and dockets by keyword, docket number, or jurisdiction.")
async def search_case(
    ctx: Context,
    query: str,
    app_id: str | None = None,
    jurisdiction: str | None = None,
    all_docket_types: bool | None = None,
) -> dict:
    # The search term goes in `q`: CaseSearchSerializer declares no `query`
    # field, and DRF drops undeclared keys, so the old payload reached
    # CourtListener with nothing to search for. appId is left to the server
    # unless the caller names one -- hardcoding "2" filtered every criminal
    # docket out of the results.
    body: dict[str, Any] = {"q": query}
    if app_id:
        body["appId"] = app_id
    if jurisdiction:
        body["jurisdiction"] = jurisdiction
    if all_docket_types is not None:
        body["allDocketTypes"] = all_docket_types
    return await _post(ctx, "/api/search-case/", body)


@mcp.tool(description="Generate a case summary artifact for a case, including strategic-summary prompts.")
async def generate_case_summary(
    ctx: Context,
    case_id: str,
    app_id: str,
    query: str | None = None,
    focus: str | None = None,
    support: str | None = None,
    message: str | None = None,
) -> dict:
    payload: dict[str, str] = {"caseId": case_id, "appId": app_id}
    if query is not None:
        payload["query"] = query
    if focus is not None:
        payload["focus"] = focus
    if support is not None:
        payload["support"] = support
    if message is not None:
        payload["message"] = message
    return await _post(ctx, "/api/generate-case-summary/", payload)


@mcp.tool(description="Retrieve a previously generated case summary.")
async def retrieve_case_summary(ctx: Context, case_id: str, app_id: str) -> dict:
    return await _post(ctx, "/api/retrieve-case-summary/", {"caseId": case_id, "appId": app_id})


@mcp.tool(description="Search precedents and case authorities for an authenticated case.")
async def precedent_query(
    ctx: Context,
    query: str,
    case_id: str,
    app_id: str,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "precedent_search")
    if blocked is not None:
        return blocked
    payload: dict[str, Any] = {
        "query": query,
        "caseId": case_id,
        "appId": app_id,
        # MCP is a request/response surface: return the authorities to the
        # caller without also sending email or persisting research artifacts.
        "responseOnly": True,
    }
    return await _post(ctx, "/api/man-search-precs/", payload)


@mcp.tool(description="Search case documents and workspace knowledge for a case.")
async def search_documents(
    ctx: Context,
    case_id: str,
    query: str,
    top_k: int | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "caseId": case_id,
        "docketId": case_id,
        "query": query,
    }
    if top_k is not None:
        payload["top_k"] = top_k
    if app_id is not None:
        payload["appId"] = app_id
    return await _post(ctx, "/api/rag-query-proxy/", payload)


@mcp.tool(
    description=(
        "Search the authenticated user's connected NetDocuments repository. "
        "This is read-only and requires an approved, connected NetDocuments integration."
    ),
)
async def search_netdocuments(ctx: Context, query: str) -> dict:
    return await _get(ctx, "/api/core/integrations/netdocuments/search", {"query": query})


@mcp.tool(description="Search documents in the authenticated user's connected iManage Work library.")
async def search_imanage_documents(
    ctx: Context,
    query: str,
    library_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/core/integrations/imanage/agent/documents/search",
        {"q": query, "libraryId": library_id},
    )


# SharePoint reads. Every refusal ("connect SharePoint first") arrives as a 200
# with ok=False because `_get` raises on any non-2xx and the model would
# otherwise never see the reason.
@mcp.tool(
    description=(
        "Search documents across every SharePoint library the authenticated user's connected account can read. "
        "Read-only."
    ),
)
async def search_sharepoint_documents(ctx: Context, query: str) -> dict:
    return await _get(ctx, "/api/core/integrations/sharepoint/agent/documents/search", {"q": query})


@mcp.tool(description="List or search SharePoint sites the authenticated user's connected account can read.")
async def search_sharepoint_sites(ctx: Context, query: str | None = None) -> dict:
    return await _get(ctx, "/api/core/integrations/sharepoint/agent/sites", {"q": query})


@mcp.tool(description="List the document libraries in one SharePoint site. Use a siteId from search_sharepoint_sites.")
async def list_sharepoint_site_drives(ctx: Context, site_id: str) -> dict:
    return await _get(ctx, f"/api/core/integrations/sharepoint/agent/sites/{quote(site_id, safe='')}/drives")


@mcp.tool(
    description=(
        "List folders and files in a SharePoint document library. Use a driveId from list_sharepoint_site_drives; "
        "pass item_id (a folder itemId from a previous result) to open a folder."
    ),
)
async def list_sharepoint_drive_children(ctx: Context, drive_id: str, item_id: str | None = None) -> dict:
    return await _get(
        ctx,
        f"/api/core/integrations/sharepoint/agent/drives/{quote(drive_id, safe='')}/children",
        {"itemId": item_id},
    )


# Epona ContactManager reads. `tool=1` makes django answer an actionable
# refusal ("connect Epona first") as HTTP 200, because `_get` raises on any
# non-2xx and the model would otherwise never see the reason.
@mcp.tool(
    description=(
        "Search Epona ContactManager matters visible to the authenticated user. "
        "query matches a matter code prefix or any part of the matter name; omit it to list open matters."
    ),
)
async def list_epona_matters(ctx: Context, query: str | None = None, active_only: bool | None = None) -> dict:
    return await _get(
        ctx,
        "/api/core/integrations/epona/matters/",
        {"q": query, "active": "true" if active_only else None, "tool": "1"},
    )


@mcp.tool(
    description=(
        "Get one Epona ContactManager matter with its parties and linked Vienna case. "
        "Use only a matterKey returned by list_epona_matters."
    ),
)
async def get_epona_matter(ctx: Context, matter_key: str) -> dict:
    return await _get(ctx, f"/api/core/integrations/epona/matters/{quote(matter_key, safe=':')}/", {"tool": "1"})


@mcp.tool(
    description=(
        "Search Epona ContactManager contacts visible to the authenticated user. "
        "Identity-document, citizen-number and birth data are never returned."
    ),
)
async def search_epona_contacts(ctx: Context, query: str) -> dict:
    return await _get(ctx, "/api/core/integrations/epona/contacts/", {"q": query, "tool": "1"})


@mcp.tool(description="List libraries in the authenticated user's connected iManage Work tenant.")
async def list_imanage_libraries(ctx: Context) -> dict:
    return await _get(ctx, "/api/core/integrations/imanage/agent/libraries")


@mcp.tool(description="List workspaces in an authenticated iManage Work library.")
async def list_imanage_workspaces(ctx: Context, library_id: str | None = None) -> dict:
    return await _get(
        ctx,
        "/api/core/integrations/imanage/agent/workspaces",
        {"libraryId": library_id},
    )


@mcp.tool(description="List versions for an explicitly selected iManage Work document.")
async def list_imanage_document_versions(ctx: Context, document_id: str, library_id: str | None = None) -> dict:
    return await _get(
        ctx,
        f"/api/core/integrations/imanage/agent/documents/{quote(document_id, safe='')}/versions",
        {"libraryId": library_id},
    )


@mcp.tool(description="List child folders and documents inside an iManage Work container.")
async def list_imanage_children(
    ctx: Context,
    container_id: str,
    library_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        f"/api/core/integrations/imanage/containers/{quote(container_id, safe='')}/children",
        {"libraryId": library_id},
    )


@mcp.tool(description="List workspaces in the authenticated user's connected RelativityOne tenant.")
async def list_relativity_workspaces(ctx: Context) -> dict:
    return await _get(ctx, "/api/core/integrations/relativity/workspaces")


@mcp.tool(description="Search permitted documents in a selected RelativityOne workspace.")
async def search_relativity_documents(
    ctx: Context,
    workspace_artifact_id: str,
    condition: str | None = None,
    fields: list[str] | None = None,
    start: int | None = None,
    length: int | None = None,
) -> dict:
    body: dict[str, object] = {"workspaceArtifactId": workspace_artifact_id}
    if condition is not None:
        body["condition"] = condition
    if fields is not None:
        body["fields"] = fields
    if start is not None:
        body["start"] = start
    if length is not None:
        body["length"] = length
    return await _post(ctx, "/api/core/integrations/relativity/documents/search", body)


@mcp.tool(description="Retrieve metadata for a permitted RelativityOne document.")
async def get_relativity_document(
    ctx: Context,
    workspace_artifact_id: str,
    document_artifact_id: str,
    integration_id: str | None = None,
) -> dict:
    body: dict[str, object] = {
        "workspaceArtifactId": workspace_artifact_id,
        "documentArtifactId": document_artifact_id,
    }
    if integration_id is not None:
        body["integrationId"] = integration_id
    return await _post(ctx, "/api/core/integrations/relativity/documents/detail", body)


@mcp.tool(description="List documents uploaded to the authenticated user's case workspace.")
async def list_uploaded_documents(ctx: Context, case_id: str) -> dict:
    return await _post(ctx, "/api/user-document-delivery/", {"mode": "list", "caseId": case_id})


@mcp.tool(
    description=(
        "Email one or more previously uploaded case documents to the authenticated user; "
        "multiple files are sent as a ZIP."
    ),
)
async def send_uploaded_documents(
    ctx: Context,
    case_id: str,
    file_ids: list[str] | None = None,
    zip_requested: bool | None = None,
) -> dict:
    payload: dict[str, object] = {"mode": "send", "caseId": case_id, "zip": bool(zip_requested)}
    if file_ids:
        payload["fileIds"] = file_ids
    return await _post(ctx, "/api/user-document-delivery/", payload)


@mcp.tool(description="Search case documents and return excerpt-oriented matches for a case.")
async def search_documents_for_excerpts(
    ctx: Context,
    case_id: str,
    query: str,
    top_k: int | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "caseId": case_id,
        "docketId": case_id,
        "query": query,
    }
    if top_k is not None:
        payload["top_k"] = top_k
    if app_id is not None:
        payload["appId"] = app_id
    return await _post(ctx, "/api/rag-query-proxy/", payload)


@mcp.tool(description="Recommend the next filings to prepare for a case.")
async def recommended_filings(ctx: Context, case_id: str, app_id: str | None = None) -> dict:
    blocked = _seriesai_litigation_block(app_id, "recommended_motion")
    if blocked is not None:
        return blocked
    return await _post(ctx, "/api/generate-recommended-motion/", {"caseId": case_id, "appId": app_id})


@mcp.tool(description="Determine the current procedural posture for a case.")
async def procedural_posture(ctx: Context, case_id: str, app_id: str | None = None) -> dict:
    blocked = _seriesai_litigation_block(app_id, "procedural_posture")
    if blocked is not None:
        return blocked
    return await _post(ctx, "/api/classify-docket/", {"caseId": case_id, "appId": app_id})


@mcp.tool(description="Determine the current procedural posture for a case.")
async def get_procedural_posture(ctx: Context, case_id: str, app_id: str | None = None) -> dict:
    """Compatibility alias for callers using the legacy tool name."""
    return await procedural_posture(ctx, case_id, app_id)


@mcp.tool(description="Refresh the latest docket metadata for a case already known to Vienna.")
async def check_docket_updates(
    ctx: Context,
    case_id: str,
    docket_id: str | None = None,
    app_id: str | None = None,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "docket_updates")
    if blocked is not None:
        return blocked
    payload: dict[str, object] = {"caseId": case_id}
    if docket_id is not None:
        payload["docketId"] = docket_id
    if app_id is not None:
        payload["appId"] = app_id
    return await _post(ctx, "/api/retrieve-docket-metadata/", payload)


@mcp.tool(description="List motion templates available to the authenticated user.")
async def list_motion_templates(
    ctx: Context,
    app_id: str | None = None,
    side: str | None = None,
    stage: str | None = None,
    motion_slug: str | None = None,
    include_previews: bool | None = None,
    max_items_per_bucket: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/templates/",
        {
            "appId": app_id or "2",
            "side": side,
            "stage": stage,
            "motionSlug": motion_slug,
            "includePreviews": include_previews,
            "maxItemsPerBucket": max_items_per_bucket,
        },
    )


@mcp.tool(description="Show the latest matching motion template with a short excerpt.")
async def show_motion_template(
    ctx: Context,
    side: str,
    stage: str,
    motion_slug: str,
    app_id: str | None = None,
    include_previews: bool | None = None,
    max_items_per_bucket: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/templates/show/",
        {
            "appId": app_id or "2",
            "side": side,
            "stage": stage,
            "motionSlug": motion_slug,
            "includePreviews": include_previews,
            "maxItemsPerBucket": max_items_per_bucket,
        },
    )


@mcp.tool(description="Templatize a source document into the motion-template store.")
async def templatize_motion_template(
    ctx: Context,
    case_id: str,
    source: dict,
    app_id: str,
    dry_run: bool | None = None,
    overrides: dict | None = None,
    legal_team_ids: list[str] | None = None,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "templatize_motion")
    if blocked is not None:
        return blocked
    return await _post(
        ctx,
        "/api/templates/templatize/",
        {
            "caseId": case_id,
            "appId": app_id,
            "source": source,
            "dryRun": dry_run,
            "overrides": overrides,
            "legalTeamIds": legal_team_ids,
        },
    )


@mcp.tool(description="Soft-delete the latest motion template pointer after an explicit confirmation.")
async def delete_motion_template(
    ctx: Context,
    side: str,
    stage: str,
    motion_slug: str,
    app_id: str,
    confirm_delete: bool | None = None,
    max_items_per_bucket: int | None = None,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "delete_motion_template")
    if blocked is not None:
        return blocked
    return await _post(
        ctx,
        "/api/templates/delete/",
        {
            "appId": app_id,
            "side": side,
            "stage": stage,
            "motionSlug": motion_slug,
            "confirmDelete": confirm_delete,
            "maxItemsPerBucket": max_items_per_bucket,
        },
    )


@mcp.tool(description="Run the general Vienna query processor.")
async def query_processor(ctx: Context, query: str, case_id: str | None = None) -> dict:
    return await _post(ctx, "/api/query-processor/", {"query": query, "caseId": case_id})


@mcp.tool(description="Generate insight about case deadlines.")
async def deadlines_insight(ctx: Context, case_id: str, query: str | None = None) -> dict:
    return await _post(ctx, "/api/deadlines-insight/", {"caseId": case_id, "query": query})


@mcp.tool(description="Retrieve a previously generated document summary.")
async def retrieve_document_summary(ctx: Context, document_id: str, case_id: str | None = None) -> dict:
    return await _post(ctx, "/api/retrieve-document-summary/", {"documentId": document_id, "caseId": case_id})


@mcp.tool(description="Look up the signature requests for a case.")
async def list_case_signatures(ctx: Context, case_id: str) -> dict:
    return await _get(ctx, f"/api/cases/{case_id}/signatures")


@mcp.tool(description="Look up workspace-scoped signature requests for an organization.")
async def list_organization_signatures(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    limit: int | None = None,
) -> dict:
    return await _get(
        ctx,
        f"/api/organizations/{organization_id}/signatures",
        {
            "appId": app_id,
            "status": status,
            "sourceType": source_type,
            "limit": limit,
        },
    )


@mcp.tool(description="Get detail for a specific signature request.")
async def get_signature_request_detail(ctx: Context, signature_id: str) -> dict:
    return await _get(ctx, f"/api/signatures/requests/{signature_id}")


@mcp.tool(description="List Vienna generatable document templates.")
async def list_seriesai_document_templates(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    lifecycle_stage: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "listDocumentTemplates",
                "organizationId": organization_id,
                "appId": app_id,
                "lifecycleStage": lifecycle_stage,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "Save known Vienna company onboarding facts into a scoped company workspace. "
        "Use this when an agent already knows company, formation, people, financing, "
        "SAFE, or cap-table facts; do not invent missing values."
    ),
)
async def save_seriesai_onboarding_context(
    ctx: Context,
    organization_id: str,
    context: dict,
    app_id: str | None = None,
) -> dict:
    payload = {
        "toolName": "saveOnboardingContext",
        "organizationId": organization_id,
        "appId": app_id,
        "context": context,
    }
    return await _post(ctx, "/api/tools/execute/", {k: v for k, v in payload.items() if v is not None})


@mcp.tool(
    description=("Read the current Vienna cap table, including who holds what equity and ownership totals."),
)
async def get_seriesai_cap_table_state(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    as_of: str | None = None,
) -> dict:
    payload = {
        "toolName": "getCapTableState",
        "organizationId": organization_id,
        "appId": app_id,
        "asOf": as_of,
    }
    return await _post(ctx, "/api/tools/execute/", {k: v for k, v in payload.items() if v is not None})


@mcp.tool(description="List committed Vienna cap-table transactions for a company.")
async def list_seriesai_cap_table_transactions(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    stakeholder_id: str | None = None,
    security_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    payload = {
        "toolName": "listCapTableTransactions",
        "organizationId": organization_id,
        "appId": app_id,
        "stakeholderId": stakeholder_id,
        "securityId": security_id,
        "fromDate": from_date,
        "toDate": to_date,
    }
    return await _post(ctx, "/api/tools/execute/", {k: v for k, v in payload.items() if v is not None})


@mcp.tool(description="List uncommitted Vienna cap-table scenarios for a company.")
async def list_seriesai_cap_table_scenarios(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    payload = {
        "toolName": "listCapTableScenarios",
        "organizationId": organization_id,
        "appId": app_id,
    }
    return await _post(ctx, "/api/tools/execute/", {k: v for k, v in payload.items() if v is not None})


@mcp.tool(description="List Vienna financing or equity issuance workflows for a company.")
async def list_seriesai_issuance_workflows(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    status: str | None = None,
) -> dict:
    payload = {
        "toolName": "listIssuanceWorkflows",
        "organizationId": organization_id,
        "appId": app_id,
        "status": status,
    }
    return await _post(ctx, "/api/tools/execute/", {k: v for k, v in payload.items() if v is not None})


@mcp.tool(
    description=(
        "Read a Vienna company's current recurring revenue from its connected Stripe account: "
        "mrr, arr, currency, subscription and customer counts, syncedAt, and a daily MRR series "
        "when one has accumulated. Returns status 'not_connected' when Stripe is not linked -- "
        "report that the connector is not set up rather than reporting zero."
    ),
)
async def get_seriesai_revenue_metrics(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "getRevenueMetrics",
                "organizationId": organization_id,
                "appId": app_id,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "Read the recent transaction detail behind a Vienna company's revenue: payment intents, "
        "subscriptions, customers, payouts, products and balances from the last Stripe sync. "
        "Returns status 'not_connected' when Stripe is not linked."
    ),
)
async def get_seriesai_recent_sales(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "getRecentSales",
                "organizationId": organization_id,
                "appId": app_id,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "Read recent Mailchimp campaign performance for a Vienna company -- sends, opens, clicks "
        "and rates per campaign. count defaults to 25 and is clamped to 1-100. Returns status "
        "'not_connected' when Mailchimp is not linked."
    ),
)
async def get_seriesai_marketing_performance(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    count: int | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "getMarketingPerformance",
                "organizationId": organization_id,
                "appId": app_id,
                "count": count,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "Read Google Analytics 4 traffic for a Vienna company. days defaults to 28 and is clamped "
        "to 1-365. Google Analytics is connector-only, so an unconnected company returns status "
        "'not_connected'."
    ),
)
async def get_seriesai_web_analytics(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    days: int | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "getWebAnalytics",
                "organizationId": organization_id,
                "appId": app_id,
                "days": days,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "Check which data connectors a Vienna company has linked (Stripe, Mailchimp, "
        "Google Analytics) and whether each is healthy. Call this when a metrics tool "
        "returns 'not_connected' so you can tell the user exactly which connector to set up."
    ),
)
async def get_seriesai_connector_status(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "getConnectorStatus",
                "organizationId": organization_id,
                "appId": app_id,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "List the Mailchimp campaigns a Vienna company has scheduled or drafted but NOT yet sent, "
        "soonest first. Use for 'what is going out next', 'do we have anything scheduled', or before "
        "advising on send timing. status 'schedule' means a send time is set (scheduledFor); 'save' "
        "means an unscheduled draft. This is read-only -- it never schedules or sends anything. "
        "count defaults to 25 and is clamped to 1-100. Returns 'not_connected' when Mailchimp is not linked."
    ),
)
async def list_seriesai_upcoming_campaigns(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    count: int | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "listUpcomingCampaigns",
                "organizationId": organization_id,
                "appId": app_id,
                "count": count,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "List a Vienna company's Mailchimp automations / customer journeys with their status and "
        "entry and completion counts. Use for 'what drip campaigns are running'. Accounts with no "
        "classic automations return an empty list. Returns 'not_connected' when Mailchimp is not linked."
    ),
)
async def list_seriesai_marketing_automations(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "listMarketingAutomations",
                "organizationId": organization_id,
                "appId": app_id,
            }.items()
            if v is not None
        },
    )


# ── Slack (read-only, all apps) ──────────────────────────────────────────────
# Unlike the SeriesAI connector tools above, these are not company-scoped: they
# read the Slack workspace the *calling user* connected, on any app.


@mcp.tool(
    description=(
        "List the Slack workspaces this user has connected and whether each is still authorized. "
        "Call this when a Slack read returns 'not_connected' so you can tell the user to connect "
        "Slack rather than reporting that nothing was found."
    ),
)
async def list_slack_workspaces(ctx: Context, app_id: str | None = None) -> dict:
    return await _get(ctx, "/api/integrations/slack", {"appId": app_id})


@mcp.tool(
    description=(
        "List the WhatsApp Business connections the user has authorized, including a safe display "
        "label and connection id. Call this before reading or sending WhatsApp messages when the user "
        "has not named a specific connected number; if it returns no connections, say WhatsApp is not connected."
    ),
)
async def list_whatsapp_connections(ctx: Context, app_id: str | None = None) -> dict:
    return await _get(ctx, "/api/integrations/whatsapp", {"appId": app_id})


@mcp.tool(
    description=(
        "List public channels in the user's connected Slack workspace, with topic, purpose, member "
        "count, and isMember (whether the Vienna bot is in the channel). Messages can only be read "
        "from channels where isMember is true. Use this to resolve a channel name to an id, and to "
        "explain why a channel returned nothing. limit is clamped to 1-50."
    ),
)
async def list_slack_channels(ctx: Context, app_id: str | None = None, limit: int | None = None) -> dict:
    return await _get(ctx, "/api/integrations/slack/channels", {"appId": app_id, "limit": limit})


@mcp.tool(
    description=(
        "Search message text across the Slack channels the Vienna bot is a member of. Pass "
        "distinctive plain words in query -- this is a case-insensitive text match, NOT Slack's own "
        "search syntax, so from:/in:/has: operators will not work (use channel_id to narrow instead). "
        "days is the lookback window (1-365, default 30) and limit the result cap (1-50, default 20). "
        "Channels the bot has not been invited to come back in skippedChannels: when that list is "
        "non-empty, say the search could not cover those channels instead of reporting no results."
    ),
)
async def search_slack_messages(
    ctx: Context,
    query: str,
    app_id: str | None = None,
    channel_id: str | None = None,
    days: int | None = None,
    limit: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/integrations/slack/messages",
        {"query": query, "appId": app_id, "channelId": channel_id, "days": days, "limit": limit},
    )


@mcp.tool(
    description=(
        "Read recent messages from one Slack channel, newest first, with sender names resolved. Pass "
        "channel_id, or channel_name with or without the leading '#'. days is 1-365 (default 7) and "
        "limit 1-200 (default 50). An empty message list means the Vienna bot is not a member of "
        "that channel -- say it needs inviting rather than concluding the channel is quiet."
    ),
)
async def get_slack_channel_messages(
    ctx: Context,
    app_id: str | None = None,
    channel_id: str | None = None,
    channel_name: str | None = None,
    days: int | None = None,
    limit: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/integrations/slack/history",
        {
            "appId": app_id,
            "channelId": channel_id,
            "channelName": channel_name,
            "days": days,
            "limit": limit,
        },
    )


# ── Microsoft Teams (read-only, all apps) ───────────────────────────────────
# These wrap the existing authenticated Teams resource endpoints. The
# connection id is deliberately required for every resource read so the
# caller cannot accidentally search a different connected tenant.


@mcp.tool(
    description=(
        "List the Microsoft Teams connections the user has authorized. Call this before reading Teams "
        "resources when no connection id is known; an empty result means Teams is not connected."
    ),
)
async def list_teams_connections(ctx: Context, app_id: str) -> dict:
    return await _get(ctx, "/api/integrations/teams", {"appId": app_id})


@mcp.tool(
    description=(
        "List teams in an authorized Microsoft Teams connection. Use the connection id returned by "
        "list_teams_connections."
    ),
)
async def list_teams(ctx: Context, connection_id: str, app_id: str) -> dict:
    return await _get(ctx, f"/api/integrations/teams/{quote(str(connection_id), safe='')}/teams", {"appId": app_id})


@mcp.tool(
    description=(
        "List channels in an authorized Microsoft Team. Use connection_id and team_id from the Teams "
        "connection/team listing."
    ),
)
async def list_teams_channels(
    ctx: Context,
    connection_id: str,
    team_id: str,
    app_id: str,
) -> dict:
    path = f"/api/integrations/teams/{quote(str(connection_id), safe='')}/teams/{quote(str(team_id), safe='')}/channels"
    return await _get(ctx, path, {"appId": app_id})


@mcp.tool(
    description=(
        "List chats in an authorized Microsoft Teams connection. Use the returned chat id to read chat messages."
    ),
)
async def list_teams_chats(ctx: Context, connection_id: str, app_id: str) -> dict:
    return await _get(ctx, f"/api/integrations/teams/{quote(str(connection_id), safe='')}/chats", {"appId": app_id})


@mcp.tool(
    description=(
        "Search Microsoft Teams chat messages in an authorized connection using Microsoft Graph. "
        "Provide at least two distinctive words; this searches messages visible to the connected user "
        "and returns no tenant-wide data outside that authorization."
    ),
)
async def search_teams_messages(
    ctx: Context,
    connection_id: str,
    query: str,
    app_id: str,
    limit: int | None = None,
) -> dict:
    path = f"/api/integrations/teams/{quote(str(connection_id), safe='')}/messages/search"
    return await _get(ctx, path, {"query": query, "appId": app_id, "limit": limit})


@mcp.tool(
    description=(
        "Read messages from an authorized Microsoft Teams chat, newest first when supported by the "
        "provider. This is a scoped read, not a tenant-wide search."
    ),
)
async def get_teams_chat_messages(
    ctx: Context,
    connection_id: str,
    chat_id: str,
    app_id: str,
) -> dict:
    path = f"/api/integrations/teams/{quote(str(connection_id), safe='')}/chats/{quote(str(chat_id), safe='')}/messages"
    return await _get(ctx, path, {"appId": app_id})


@mcp.tool(
    description=(
        "Read messages from an authorized Microsoft Teams channel. This is a scoped read, not a "
        "tenant-wide search; resolve the team and channel first."
    ),
)
async def get_teams_channel_messages(
    ctx: Context,
    connection_id: str,
    team_id: str,
    channel_id: str,
    app_id: str,
) -> dict:
    path = (
        f"/api/integrations/teams/{quote(str(connection_id), safe='')}/teams/"
        f"{quote(str(team_id), safe='')}/channels/{quote(str(channel_id), safe='')}/messages"
    )
    return await _get(ctx, path, {"appId": app_id})


@mcp.tool(description="Read new WhatsApp Business messages already associated with an authorized connection.")
async def list_whatsapp_messages(
    ctx: Context,
    connection_id: str,
    case_id: str,
    app_id: str | None = None,
    limit: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/integrations/whatsapp/messages",
        {"connectionId": connection_id, "caseId": case_id, "appId": app_id, "limit": limit},
    )


@mcp.tool(description="Send a WhatsApp Business message after explicit user confirmation.")
async def send_whatsapp_message(
    ctx: Context,
    connection_id: str,
    case_id: str,
    to: str | None = None,
    message: object | None = None,
    context_message_id: str | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {"connectionId": connection_id, "caseId": case_id}
    if to is not None:
        payload["to"] = to
    if message is not None:
        payload["message"] = message
    if context_message_id is not None:
        payload["contextMessageId"] = context_message_id
    if app_id is not None:
        payload["appId"] = app_id
    return await _post(ctx, "/api/integrations/whatsapp/messages", payload)


@mcp.tool(description="Associate one WhatsApp message with an authorized case after confirmation.")
async def associate_whatsapp_message(
    ctx: Context,
    message_id: str,
    case_id: str,
    app_id: str | None = None,
    contact_id: str | None = None,
    import_message: bool = True,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "import": import_message}
    if app_id is not None:
        payload["appId"] = app_id
    if contact_id is not None:
        payload["contactId"] = contact_id
    return await _post(ctx, f"/api/integrations/whatsapp/messages/{message_id}/associate", payload)


# DocuSign is case-scoped and shared by all JuristAI app surfaces.
@mcp.tool(description="List DocuSign envelopes and current statuses available to the current matter.")
async def list_docusign_envelopes(
    ctx: Context,
    case_id: str,
    app_id: str | None = None,
    from_date: str | None = None,
    status: str = "any",
) -> dict:
    return await _get(
        ctx,
        "/api/core/external-integrations/docusign/envelopes/",
        {"appId": app_id, "caseId": case_id, "fromDate": from_date, "status": status},
    )


@mcp.tool(description="Send a PDF to DocuSign after the user explicitly confirms the exact document and signers.")
async def send_docusign_envelope(
    ctx: Context,
    case_id: str,
    document_name: str,
    document_base64: str,
    signers: list[dict],
    app_id: str | None = None,
    email_subject: str | None = None,
    tabs: list[dict] | None = None,
    signature_request_id: str | None = None,
    confirm: bool = False,
) -> dict:
    return await _post(
        ctx,
        "/api/core/external-integrations/docusign/send/",
        {
            "appId": app_id,
            "caseId": case_id,
            "documentName": document_name,
            "documentBase64": document_base64,
            "emailSubject": email_subject,
            "signers": signers,
            "tabs": tabs,
            "signatureRequestId": signature_request_id,
            "confirm": confirm,
        },
    )


@mcp.tool(description="Import a completed DocuSign envelope PDF into the current matter after explicit confirmation.")
async def import_docusign_envelope(
    ctx: Context,
    case_id: str,
    envelope_id: str,
    app_id: str | None = None,
    document_id: str = "combined",
    filename: str | None = None,
    confirm: bool = False,
) -> dict:
    return await _post(
        ctx,
        "/api/core/external-integrations/docusign/import/",
        {
            "appId": app_id,
            "caseId": case_id,
            "envelopeId": envelope_id,
            "documentId": document_id,
            "filename": filename,
            "confirm": confirm,
        },
    )


@mcp.tool(description="Read one DocuSign envelope for the current matter, including its provider status.")
async def get_docusign_envelope(
    ctx: Context,
    case_id: str,
    envelope_id: str,
    app_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        f"/api/core/external-integrations/docusign/envelopes/{envelope_id}/",
        {"appId": app_id, "caseId": case_id},
    )


@mcp.tool(description="Send a confirmed reminder for a DocuSign envelope in the current matter.")
async def remind_docusign_envelope(
    ctx: Context,
    case_id: str,
    envelope_id: str,
    app_id: str | None = None,
    confirm: bool = False,
) -> dict:
    return await _post(
        ctx,
        f"/api/core/external-integrations/docusign/envelopes/{envelope_id}/remind/",
        {"appId": app_id, "caseId": case_id, "confirm": confirm},
    )


@mcp.tool(description="Void a DocuSign envelope after the user explicitly confirms the envelope and reason.")
async def void_docusign_envelope(
    ctx: Context,
    case_id: str,
    envelope_id: str,
    reason: str,
    app_id: str | None = None,
    confirm: bool = False,
) -> dict:
    return await _post(
        ctx,
        f"/api/core/external-integrations/docusign/envelopes/{envelope_id}/void/",
        {"appId": app_id, "caseId": case_id, "reason": reason, "confirm": confirm},
    )


@mcp.tool(
    description="Create an embedded DocuSign signing URL for a named recipient after the user confirms the recipient."
)
async def create_docusign_recipient_view(
    ctx: Context,
    case_id: str,
    envelope_id: str,
    signer: dict,
    return_url: str,
    app_id: str | None = None,
    confirm: bool = False,
) -> dict:
    return await _post(
        ctx,
        f"/api/core/external-integrations/docusign/envelopes/{envelope_id}/recipient-view/",
        {"appId": app_id, "caseId": case_id, "signer": signer, "returnUrl": return_url, "confirm": confirm},
    )


@mcp.tool(
    description="Import a DocuSign Certificate of Completion into the current matter after explicit confirmation."
)
async def import_docusign_certificate(
    ctx: Context,
    case_id: str,
    envelope_id: str,
    app_id: str | None = None,
    confirm: bool = False,
) -> dict:
    return await _post(
        ctx,
        "/api/core/external-integrations/docusign/import-certificate/",
        {"appId": app_id, "caseId": case_id, "envelopeId": envelope_id, "confirm": confirm},
    )


@mcp.tool(description="Generate a Vienna document from a template and answers.")
async def generate_seriesai_document(
    ctx: Context,
    organization_id: str,
    template_id: str,
    app_id: str | None = None,
    answers: dict | None = None,
    company_profile: dict | None = None,
    formats: list[str] | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "generateDocument",
                "organizationId": organization_id,
                "appId": app_id,
                "templateId": template_id,
                "answers": answers,
                "companyProfile": company_profile,
                "formats": formats,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "List which amendments apply to an existing Vienna corporate document -- add a cofounder, "
        "authorize more shares, amend an agreement, amend the bylaws -- and the instruments each would "
        "draft. Pass the original document's interview run (source_run_id) so its facts carry over, or "
        "its template id; omit both to list company-level amendments. Read-only."
    ),
)
async def list_seriesai_document_amendments(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    source_run_id: str | None = None,
    source_template_id: str | None = None,
    company_profile: dict | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "listDocumentAmendments",
                "organizationId": organization_id,
                "appId": app_id,
                "sourceRunId": source_run_id,
                "sourceTemplateId": source_template_id,
                "companyProfile": company_profile,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "Start a Vienna document amendment: one interview run per instrument the change needs "
        "(the amendment plus its approvals), each seeded from the original document so only the new "
        "facts are asked. Signed paperwork is never edited in place. amendment_kind is one of "
        "add_cofounder, authorize_shares, amend_agreement, amend_bylaws."
    ),
)
async def start_seriesai_document_amendment(
    ctx: Context,
    organization_id: str,
    amendment_kind: str,
    app_id: str | None = None,
    source_run_id: str | None = None,
    source_template_id: str | None = None,
    answers: dict | None = None,
    company_profile: dict | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "startDocumentAmendment",
                "organizationId": organization_id,
                "appId": app_id,
                "amendmentKind": amendment_kind,
                "sourceRunId": source_run_id,
                "sourceTemplateId": source_template_id,
                "answers": answers,
                "companyProfile": company_profile,
            }.items()
            if v is not None
        },
    )


@mcp.tool(
    description=(
        "Generate every instrument of a Vienna document amendment at once from supplied answers. "
        "Reports inputs_required with the missing field names rather than producing a blank document. "
        "Never touches the cap table."
    ),
)
async def amend_seriesai_documents(
    ctx: Context,
    organization_id: str,
    amendment_kind: str,
    app_id: str | None = None,
    source_run_id: str | None = None,
    source_template_id: str | None = None,
    answers: dict | None = None,
    company_profile: dict | None = None,
    formats: list[str] | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "amendDocuments",
                "organizationId": organization_id,
                "appId": app_id,
                "amendmentKind": amendment_kind,
                "sourceRunId": source_run_id,
                "sourceTemplateId": source_template_id,
                "answers": answers,
                "companyProfile": company_profile,
                "formats": formats,
            }.items()
            if v is not None
        },
    )


@mcp.tool(description="List resumable Vienna document generation runs for an organization.")
async def list_seriesai_document_runs(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "listDocumentRuns",
                "organizationId": organization_id,
                "appId": app_id,
                "status": status,
                "limit": limit,
                "cursor": cursor,
            }.items()
            if v is not None
        },
    )


@mcp.tool(description="Get a scoped Vienna document generation run.")
async def get_seriesai_document_run(
    ctx: Context,
    organization_id: str,
    run_id: str,
    app_id: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "getDocumentRun",
                "organizationId": organization_id,
                "appId": app_id,
                "runId": run_id,
            }.items()
            if v is not None
        },
    )


@mcp.tool(description="Start a resumable Vienna document generation run from a template.")
async def start_seriesai_document_run(
    ctx: Context,
    organization_id: str,
    template_id: str,
    app_id: str | None = None,
    company_profile: dict | None = None,
    roles: list[str] | None = None,
    language: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "startDocumentRun",
                "organizationId": organization_id,
                "appId": app_id,
                "templateId": template_id,
                "companyProfile": company_profile,
                "roles": roles,
                "language": language,
            }.items()
            if v is not None
        },
    )


@mcp.tool(description="Answer fields for a Vienna document generation run.")
async def answer_seriesai_document_run(
    ctx: Context,
    organization_id: str,
    run_id: str,
    answers: dict,
    app_id: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "answerDocumentRun",
                "organizationId": organization_id,
                "appId": app_id,
                "runId": run_id,
                "answers": answers,
            }.items()
            if v is not None
        },
    )


@mcp.tool(description="Advance a Vienna document generation run to the next question or completion.")
async def advance_seriesai_document_run(
    ctx: Context,
    organization_id: str,
    run_id: str,
    app_id: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "advanceDocumentRun",
                "organizationId": organization_id,
                "appId": app_id,
                "runId": run_id,
            }.items()
            if v is not None
        },
    )


@mcp.tool(description="Generate downloadable artifacts for a completed Vienna document run.")
async def generate_seriesai_document_artifacts(
    ctx: Context,
    organization_id: str,
    run_id: str,
    app_id: str | None = None,
    formats: list[str] | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/tools/execute/",
        {
            k: v
            for k, v in {
                "toolName": "generateDocumentArtifacts",
                "organizationId": organization_id,
                "appId": app_id,
                "runId": run_id,
                "formats": formats,
            }.items()
            if v is not None
        },
    )


@mcp.tool(description="List ranked Vienna signer suggestions for a document or organization.")
async def list_seriesai_signer_suggestions(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    document_instance_id: str | None = None,
    exclude_emails: str | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/signature-signer-suggestions/",
        {
            "organizationId": organization_id,
            "appId": app_id,
            "documentInstanceId": document_instance_id,
            "excludeEmails": exclude_emails,
        },
    )


@mcp.tool(description="List Vienna billing invoices for an organization.")
async def list_seriesai_billing_invoices(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    status: str | None = None,
    invoice_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/core/billing/invoices/",
        {
            "organizationId": organization_id,
            "appId": app_id,
            "status": status,
            "invoiceType": invoice_type,
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": limit,
            "cursor": cursor,
        },
    )


@mcp.tool(description="Get one Vienna billing invoice by id.")
async def get_seriesai_billing_invoice(
    ctx: Context,
    bill_id: str,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        f"/api/core/billing/invoices/{bill_id}/",
        {"organizationId": organization_id, "appId": app_id},
    )


@mcp.tool(description="List Vienna audit events for an organization.")
async def list_seriesai_audit_events(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    target_object_id: str | None = None,
    target_object_type: str | None = None,
    event_type: str | None = None,
    limit: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/audit-events/",
        {
            "organizationId": organization_id,
            "appId": app_id,
            "targetObjectId": target_object_id,
            "targetObjectType": target_object_type,
            "eventType": event_type,
            "limit": limit,
        },
    )


@mcp.tool(description="Get Vienna sales and revenue metrics for an organization.")
async def get_seriesai_sales_metrics(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/metrics/sales/",
        {"organizationId": organization_id, "appId": app_id},
    )


@mcp.tool(description="Get Vienna marketing metrics for an organization.")
async def get_seriesai_marketing_metrics(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    count: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/metrics/marketing/",
        {"organizationId": organization_id, "appId": app_id, "count": count},
    )


@mcp.tool(description="Get Vienna analytics metrics for an organization.")
async def get_seriesai_analytics_metrics(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    days: int | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/metrics/analytics/",
        {"organizationId": organization_id, "appId": app_id, "days": days},
    )


@mcp.tool(description="List Vienna equity or stock plans for an organization.")
async def list_seriesai_stock_plans(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/stock-plans/",
        {"organizationId": organization_id, "appId": app_id},
    )


@mcp.tool(description="List pending Vienna approval actions for an organization.")
async def list_seriesai_pending_actions(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/pending-actions/",
        {"organizationId": organization_id, "appId": app_id},
    )


@mcp.tool(description="List outbound Vienna email queue records for an organization.")
async def list_seriesai_outbound_emails(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/outbound-emails/",
        {"organizationId": organization_id, "appId": app_id},
    )


@mcp.tool(description="List inbound and mapped Vienna email messages for an organization.")
async def list_seriesai_email_messages(
    ctx: Context,
    organization_id: str,
    app_id: str | None = None,
    round_id: str | None = None,
    limit: int | None = None,
    next_token: str | None = None,
) -> dict:
    return await _get(
        ctx,
        "/api/email-messages/",
        {
            "organizationId": organization_id,
            "appId": app_id,
            "roundId": round_id,
            "limit": limit,
            "nextToken": next_token,
        },
    )


@mcp.tool(description="Get a Vienna email thread by message id, provider message id, or internet message id.")
async def get_seriesai_email_thread(
    ctx: Context,
    message_id: str,
    organization_id: str,
    app_id: str | None = None,
    round_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        f"/api/email-messages/{message_id}/thread/",
        {"organizationId": organization_id, "appId": app_id, "roundId": round_id},
    )


@mcp.tool(description="List attachments for a Vienna email message.")
async def list_seriesai_email_attachments(
    ctx: Context,
    message_id: str,
    organization_id: str,
    app_id: str | None = None,
    round_id: str | None = None,
) -> dict:
    return await _get(
        ctx,
        f"/api/email-messages/{message_id}/attachments/",
        {"organizationId": organization_id, "appId": app_id, "roundId": round_id},
    )


# ===========================================================================
# MUTATION tools (Milestone 2 + supplemental mutations)
# ===========================================================================


@mcp.tool(description="Create a new action item for a case.")
async def create_action_item(
    ctx: Context,
    case_id: str,
    title: str,
    description: str | None = None,
    due_date: str | None = None,
    assignee_id: str | None = None,
    priority: str | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "title": title}
    _set_payload_value(payload, "description", description)
    _set_payload_value(payload, "suggestedDueDate", due_date)
    _set_payload_value(payload, "assignedTo", [assignee_id] if assignee_id else None)
    _set_payload_value(payload, "priority", priority)
    _set_payload_value(payload, "appId", app_id)
    return await _post(ctx, "/api/action-items/", payload)


@mcp.tool(
    description=(
        "Convert a selected Slack, Teams, email, or WhatsApp communication into a case action item. "
        "Resolve the target case first and provide the provider message id; the source reference and "
        "bounded excerpt are retained on the task for traceability. This creates a record and requires "
        "the user's explicit request to proceed."
    ),
)
async def convert_communication_to_case_task(
    ctx: Context,
    case_id: str,
    source_type: str,
    source_id: str,
    title: str,
    excerpt: str | None = None,
    due_date: str | None = None,
    assignee_id: str | None = None,
    priority: str | None = None,
    confirmation: str | None = None,
    app_id: str | None = None,
) -> dict:
    if confirmation != "CONFIRM COMMUNICATION CONVERSION":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM COMMUNICATION CONVERSION",
            "message": "Review the source, target case, and task details, then confirm before creating it.",
        }
    normalized_source = str(source_type or "").strip().lower()
    if normalized_source not in {"slack", "teams", "email", "whatsapp"}:
        raise ValueError("source_type must be slack, teams, email, or whatsapp")
    source_id_value = str(source_id or "").strip()
    if not source_id_value:
        raise ValueError("source_id is required")
    source = f"{normalized_source}:{source_id_value}"[:512]
    payload: dict[str, object] = {
        "caseId": case_id,
        "title": title,
        "source": source,
        "assistantExcerpt": str(excerpt or "")[:2000] or None,
    }
    _set_payload_value(payload, "suggestedDueDate", due_date)
    _set_payload_value(payload, "assignedTo", [assignee_id] if assignee_id else None)
    _set_payload_value(payload, "priority", priority)
    _set_payload_value(payload, "appId", app_id)
    return await _post(ctx, "/api/action-items/", {k: v for k, v in payload.items() if v is not None})


@mcp.tool(
    description=(
        "Convert a selected Slack, Teams, email, or WhatsApp communication into a case deadline. "
        "Resolve the target case first and provide the provider message id; the source reference is "
        "included in the deadline description for traceability. This creates a record and requires the "
        "user's explicit request to proceed."
    ),
)
async def convert_communication_to_case_deadline(
    ctx: Context,
    case_id: str,
    source_type: str,
    source_id: str,
    title: str,
    date: str,
    excerpt: str | None = None,
    confirmation: str | None = None,
) -> dict:
    if confirmation != "CONFIRM COMMUNICATION CONVERSION":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM COMMUNICATION CONVERSION",
            "message": "Review the source, target case, and deadline details, then confirm before creating it.",
        }
    normalized_source = str(source_type or "").strip().lower()
    if normalized_source not in {"slack", "teams", "email", "whatsapp"}:
        raise ValueError("source_type must be slack, teams, email, or whatsapp")
    source_id_value = str(source_id or "").strip()
    if not source_id_value:
        raise ValueError("source_id is required")
    reference = f"Source: {normalized_source}:{source_id_value}"
    if excerpt:
        reference += f"\nExcerpt: {str(excerpt)[:2000]}"
    return await _post(
        ctx,
        "/api/case-important-dates/",
        {"caseId": case_id, "title": title, "date": date, "description": reference},
    )


@mcp.tool(
    description=(
        "Convert a selected Slack, Teams, email, or WhatsApp communication into a new case/matter "
        "when no existing case is the right target. Provide the proposed matter name and source "
        "message id. This is a state-changing operation: invoke only after the user explicitly asks "
        "to create the matter, and never infer that request from a search result."
    ),
)
async def convert_communication_to_new_case(
    ctx: Context,
    source_type: str,
    source_id: str,
    case_name: str,
    support: str | None = None,
    role: str | None = None,
    docket_number: str | None = None,
    target_user_id: str | None = None,
    target_user_email: str | None = None,
    app_id: str | None = None,
    confirmation: str | None = None,
) -> dict:
    if confirmation != "CONFIRM COMMUNICATION CONVERSION":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM COMMUNICATION CONVERSION",
            "message": "Review the source and proposed matter details, then confirm before creating it.",
        }
    normalized_source = str(source_type or "").strip().lower()
    if normalized_source not in {"slack", "teams", "email", "whatsapp"}:
        raise ValueError("source_type must be slack, teams, email, or whatsapp")
    source_id_value = str(source_id or "").strip()
    if not source_id_value:
        raise ValueError("source_id is required")
    context = f"Converted from {normalized_source}:{source_id_value}."
    if support:
        context += f" {str(support)[:2000]}"
    payload: dict[str, object] = {"caseName": case_name, "support": context}
    for key, value in {
        "role": role,
        "docketNumber": docket_number,
        "targetUserId": target_user_id,
        "targetUserEmail": target_user_email,
        "appId": app_id,
    }.items():
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/add-case/", payload)


@mcp.tool(description="Update an existing action item.")
async def update_action_item(
    ctx: Context,
    item_id: str,
    title: str | None = None,
    description: str | None = None,
    due_date: str | None = None,
    status: str | None = None,
    priority: str | None = None,
) -> dict:
    body: dict[str, object] = {}
    _set_payload_value(body, "title", title)
    _set_payload_value(body, "description", description)
    _set_payload_value(body, "suggestedDueDate", due_date)
    _set_payload_value(body, "status", status)
    _set_payload_value(body, "priority", priority)
    return await _patch(ctx, f"/api/action-items/{item_id}/", body)


@mcp.tool(description="Assign or reassign an action item to a user.")
async def assign_action_item(ctx: Context, item_id: str, assignee_id: str) -> dict:
    return await _post(ctx, f"/api/action-items/{item_id}/assign/", {"assigneeId": assignee_id})


@mcp.tool(description="Approve or reject a pending Vienna approval action.")
async def resolve_seriesai_pending_action(
    ctx: Context,
    request_id: str,
    organization_id: str,
    decision: str,
    app_id: str | None = None,
    approval_token: str | None = None,
) -> dict:
    payload = {
        "organizationId": organization_id,
        "appId": app_id,
        "decision": decision,
        "approvalToken": approval_token,
    }
    return await _post(
        ctx,
        f"/api/pending-actions/{request_id}/resolve/",
        {k: v for k, v in payload.items() if v is not None},
    )


@mcp.tool(description="Approve or reject a pending Vienna action (e.g. a queued SAFE draft or signature request).")
async def series_ai_resolve_pending_action(
    ctx: Context,
    request_id: str,
    app_id: str,
    organization_id: str,
    decision: str,
    approval_token: str | None = None,
) -> dict:
    """Compatibility alias for the existing scoped pending-action executor."""
    return await resolve_seriesai_pending_action(
        ctx,
        request_id,
        organization_id,
        decision,
        app_id=app_id,
        approval_token=approval_token,
    )


@mcp.tool(description="Create a Vienna equity or stock plan.")
async def create_seriesai_stock_plan(
    ctx: Context,
    organization_id: str,
    plan_name: str,
    authorized_pool_shares: float,
    app_id: str | None = None,
    exercised_shares: float | None = None,
    vested_shares: float | None = None,
    promised_shares: float | None = None,
    status: str | None = None,
    adopted_date: str | None = None,
    board_approval_date: str | None = None,
) -> dict:
    payload = {
        "organizationId": organization_id,
        "appId": app_id,
        "planName": plan_name,
        "authorizedPoolShares": authorized_pool_shares,
        "exercisedShares": exercised_shares,
        "vestedShares": vested_shares,
        "promisedShares": promised_shares,
        "status": status,
        "adoptedDate": adopted_date,
        "boardApprovalDate": board_approval_date,
    }
    return await _post(
        ctx,
        "/api/stock-plans/",
        {k: v for k, v in payload.items() if v is not None},
    )


@mcp.tool(description="Mark an action item as complete.")
async def complete_action_item(ctx: Context, item_id: str) -> dict:
    return await _post(ctx, f"/api/action-items/{item_id}/complete/", {})


@mcp.tool(description="Create an important date or deadline for a case.")
async def create_case_important_date(
    ctx: Context,
    case_id: str,
    title: str,
    date: str,
    description: str | None = None,
    app_id: str | None = None,
) -> dict:
    payload = {
        "caseId": case_id,
        "label": title,
        "date": date,
        "notes": description,
    }
    _set_payload_value(payload, "appId", app_id)
    return await _post(
        ctx,
        "/api/case-important-dates/",
        payload,
    )


@mcp.tool(description="Update an existing important date or deadline.")
async def update_case_important_date(
    ctx: Context,
    case_id: str,
    deadline_id: str,
    title: str | None = None,
    date: str | None = None,
    description: str | None = None,
    app_id: str | None = None,
) -> dict:
    body = {
        k: v
        for k, v in {
            "label": title,
            "date": date,
            "notes": description,
        }.items()
        if v is not None
    }
    _set_payload_value(body, "appId", app_id)
    return await _patch(ctx, f"/api/case-important-dates/{case_id}/{deadline_id}/", body)


@mcp.tool(description="Delete an important date or deadline from a case.")
async def delete_case_important_date(ctx: Context, case_id: str, deadline_id: str, app_id: str | None = None) -> dict:
    params = {}
    _set_payload_value(params, "appId", app_id)
    return await _delete(ctx, f"/api/case-important-dates/{case_id}/{deadline_id}/", params)


@mcp.tool(
    description="Create, update, or delete case deadlines with legacy-style target resolution. Supply app_id when the case ID exists in multiple products."
)
async def edit_case_important_dates(
    ctx: Context,
    case_id: str,
    operation: str,
    deadline_id: str | None = None,
    label: str | None = None,
    date: str | None = None,
    contains: str | None = None,
    notes: str | None = None,
    status: str | None = None,
    completed: bool | None = None,
    source: str | None = None,
    raw_entry: str | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "operation": operation}
    optional_values = (
        ("deadlineId", deadline_id),
        ("label", label),
        ("date", date),
        ("contains", contains),
        ("notes", notes),
        ("status", status),
        ("completed", completed),
        ("source", source),
        ("rawEntry", raw_entry),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    _set_payload_value(payload, "appId", app_id)
    return await _post(ctx, "/api/edit-case-important-dates/", payload)


@mcp.tool(description="Generate or regenerate a bill for a case and billing period.")
async def generate_case_bill(ctx: Context, case_id: str, billing_period: str | None = None) -> dict:
    return await _post(ctx, f"/api/core/billing/cases/{case_id}/generate", {"billingPeriod": billing_period})


@mcp.tool(description="Export logged hours for the authenticated user's assigned firm cases.")
async def export_firm_hours(
    ctx: Context,
    period: str | None = None,
    billing_week_start: str | None = None,
    billing_week_end: str | None = None,
    billing_timezone: str | None = None,
    output_format: str | None = None,
    delivery_mode: str | None = None,
    case_ids: list[str] | None = None,
) -> dict:
    payload: dict[str, object] = {}
    if period is not None:
        payload["period"] = period
    if billing_week_start is not None:
        payload["billingWeekStart"] = billing_week_start
    if billing_week_end is not None:
        payload["billingWeekEnd"] = billing_week_end
    if billing_timezone is not None:
        payload["billingTimezone"] = billing_timezone
    if output_format is not None:
        payload["outputFormat"] = output_format
    if delivery_mode is not None:
        payload["deliveryMode"] = delivery_mode
    if case_ids is not None:
        payload["caseIds"] = case_ids
    return await _post(ctx, "/api/export-firm-hours/", payload)


@mcp.tool(description="Prepare bills for the authenticated user's assigned firm cases.")
async def prepare_all_bills(
    ctx: Context,
    period: str | None = None,
    billing_week_start: str | None = None,
    billing_week_end: str | None = None,
    billing_timezone: str | None = None,
    case_ids: list[str] | None = None,
) -> dict:
    payload: dict[str, object] = {}
    if period is not None:
        payload["period"] = period
    if billing_week_start is not None:
        payload["billingWeekStart"] = billing_week_start
    if billing_week_end is not None:
        payload["billingWeekEnd"] = billing_week_end
    if billing_timezone is not None:
        payload["billingTimezone"] = billing_timezone
    if case_ids is not None:
        payload["caseIds"] = case_ids
    return await _post(ctx, "/api/prepare-all-bills/", payload)


@mcp.tool(description="Send a generated invoice to the client for a case after explicit confirmation.")
async def send_client_invoice(
    ctx: Context,
    *,
    case_id: str | None = None,
    bill_id: str | None = None,
    period: str | None = None,
    billing_week_start: str | None = None,
    billing_week_end: str | None = None,
    billing_timezone: str | None = None,
    client_email: str | None = None,
    client_emails: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    payload = {
        "caseId": case_id,
        "billId": bill_id,
        "period": period,
        "billingWeekStart": billing_week_start,
        "billingWeekEnd": billing_week_end,
        "billingTimezone": billing_timezone,
        "clientEmail": client_email,
        "clientEmails": client_emails or [],
        "confirm": confirm,
    }
    return await _post(ctx, "/api/send-client-invoice/", payload)


@mcp.tool(description="Ask for the most valuable missing case documents for a case.")
async def request_missing_case_documents(ctx: Context, case_id: str, app_id: str | None = None) -> dict:
    return await _post(
        ctx,
        "/api/request-more-case-documents/",
        {
            "caseId": case_id,
            "appId": app_id,
        },
    )


@mcp.tool(description="Upload one or more documents into a case workspace and finalize hydration.")
async def upload_documents(
    ctx: Context,
    case_id: str,
    uploads: list[dict],
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "caseId": case_id,
        "uploads": uploads,
    }
    if app_id is not None:
        payload["appId"] = app_id
    return await _post(ctx, "/api/upload-documents/", payload)


@mcp.tool(description="Update the authenticated user's hourly billing rate.")
async def update_user_hourly_rate(ctx: Context, hourly_rate: float) -> dict:
    return await _post(ctx, "/api/update-user-hourly-rate/", {"hourlyRate": hourly_rate})


@mcp.tool(description="Accept a pending legal team invite for the authenticated user.")
async def accept_legal_team_invite(ctx: Context, invite_id: str) -> dict:
    # AcceptLegalTeamInviteSerializer names the invite id "token".
    return await _post(ctx, "/api/accept-legal-team-invite/", {"token": invite_id})


@mcp.tool(description="Rename a legal team (admin only).")
async def rename_legal_team(ctx: Context, legal_team_id: str, name: str, app_id: str | None = None) -> dict:
    if not app_id:
        return {"error": "app_id_required", "message": "Supply the product app_id for this legal team."}
    return await _post(
        ctx,
        "/api/rename-legal-team/",
        {"legalTeamId": legal_team_id, "appId": app_id, "legalTeamName": name},
    )


@mcp.tool(description="Remove a member from a legal team by user id or email (admin only) after explicit confirmation.")
async def remove_legal_team_member(
    ctx: Context,
    legal_team_id: str,
    member_user_id: str | None = None,
    member_email: str | None = None,
    app_id: str | None = None,
    confirmation: str | None = None,
) -> dict:
    if confirmation != "CONFIRM LEGAL TEAM ACCESS CHANGE":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM LEGAL TEAM ACCESS CHANGE",
            "message": "Review the exact team and member, then confirm before changing team access.",
        }
    if not app_id:
        return {"error": "app_id_required", "message": "Supply the product app_id for this legal team."}
    # The endpoint is LegalTeamScopedSerializer-backed: it requires appId and
    # names the target memberUserId/memberEmail. Posting {legalTeamId, userId}
    # 400'd every agent-driven removal.
    body = {
        "legalTeamId": legal_team_id,
        "appId": app_id,
        "memberUserId": member_user_id,
        "memberEmail": member_email,
    }
    return await _post(ctx, "/api/remove-legal-team-member/", {k: v for k, v in body.items() if v is not None})


@mcp.tool(description="Assign all members of a legal team to a case (admin only) after explicit confirmation.")
async def assign_legal_team_to_case(
    ctx: Context,
    legal_team_id: str,
    case_id: str,
    app_id: str | None = None,
    confirmation: str | None = None,
) -> dict:
    if confirmation != "CONFIRM LEGAL TEAM ACCESS CHANGE":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM LEGAL TEAM ACCESS CHANGE",
            "message": "Review the exact team and case, then confirm before changing case access.",
        }
    if not app_id:
        return {"error": "app_id_required", "message": "Supply the product app_id for this legal team and case."}
    return await _post(
        ctx,
        "/api/assign-legal-team-to-case/",
        {"legalTeamId": legal_team_id, "appId": app_id, "caseId": case_id},
    )


@mcp.tool(description="Delete a legal team (admin only) after explicit confirmation.")
async def delete_legal_team(
    ctx: Context,
    legal_team_id: str,
    app_id: str | None = None,
    confirmation: str | None = None,
) -> dict:
    if confirmation != "CONFIRM LEGAL TEAM DELETE":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM LEGAL TEAM DELETE",
            "message": "Review the exact legal team, then confirm before deleting it.",
        }
    if not app_id:
        return {"error": "app_id_required", "message": "Supply the product app_id for this legal team."}
    return await _post(ctx, "/api/delete-legal-team/", {"legalTeamId": legal_team_id, "appId": app_id})


@mcp.tool(
    description="Assign the authenticated user (or an admin-selected user) to a case after explicit confirmation. Supply app_id when the case ID exists in multiple products."
)
async def assign_user_to_case(
    ctx: Context,
    case_id: str,
    user_id: str | None = None,
    confirmation: str | None = None,
    app_id: str | None = None,
) -> dict:
    if confirmation != "CONFIRM CASE ACCESS CHANGE":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM CASE ACCESS CHANGE",
            "message": "Review the exact case and assignee, then confirm before changing access.",
        }
    payload = {"caseId": case_id, "userId": user_id}
    if app_id:
        payload["appId"] = app_id
    return await _post(ctx, "/api/assign-user-to-case/", payload)


@mcp.tool(description="Add an existing case to a workspace or create a new matter when no docket match exists.")
async def add_case(
    ctx: Context,
    docket_id: str | None = None,
    role: str | None = None,
    case_name: str | None = None,
    docket_number: str | None = None,
    support: str | None = None,
    target_user_id: str | None = None,
    target_user_email: str | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {}
    if docket_id is not None:
        payload["docketId"] = docket_id
    if role is not None:
        payload["role"] = role
    if case_name is not None:
        payload["caseName"] = case_name
    if docket_number is not None:
        payload["docketNumber"] = docket_number
    if support is not None:
        payload["support"] = support
    if target_user_id is not None:
        payload["targetUserId"] = target_user_id
    if target_user_email is not None:
        payload["targetUserEmail"] = target_user_email
    if app_id is not None:
        payload["appId"] = app_id
    return await _post(ctx, "/api/add-case/", payload)


@mcp.tool(
    description="Remove the authenticated user (or an admin-selected user) from a case after explicit confirmation. Supply app_id when the case ID exists in multiple products."
)
async def remove_user_from_case(
    ctx: Context,
    case_id: str,
    user_id: str | None = None,
    confirmation: str | None = None,
    app_id: str | None = None,
) -> dict:
    if confirmation != "CONFIRM CASE ACCESS CHANGE":
        return {
            "error": "confirmation_required",
            "confirmation": "CONFIRM CASE ACCESS CHANGE",
            "message": "Review the exact case and person, then confirm before removing access.",
        }
    payload = {"caseId": case_id, "userId": user_id}
    if app_id:
        payload["appId"] = app_id
    return await _post(ctx, "/api/remove-user-from-case/", payload)


@mcp.tool(description="Invite a collaborator to join a legal team.")
async def legal_team_invite(
    ctx: Context,
    email: str,
    app_id: str,
    legal_team_id: str | None = None,
    case_id: str | None = None,
) -> dict:
    return await _post(
        ctx,
        "/api/legal-team-invite/",
        {
            "email": email,
            "appId": app_id,
            "legalTeamId": legal_team_id,
            "caseId": case_id,
        },
    )


@mcp.tool(description="Create a signature request for a document in a case.")
async def create_signature_request(
    ctx: Context,
    signers: list[dict],
    case_id: str | None = None,
    source_type: str | None = None,
    source_artifact_id: str | None = None,
    source_file_id: str | None = None,
    source_template_id: str | None = None,
    source_download_url: str | None = None,
    document_name: str | None = None,
    description: str | None = None,
    custom_message: str | None = None,
    mode: str | None = None,
    expires_in_days: int | None = None,
) -> dict:
    payload: dict[str, object] = {"signers": signers}
    optional_values = (
        ("caseId", case_id),
        ("sourceType", source_type),
        ("sourceArtifactId", source_artifact_id),
        ("sourceFileId", source_file_id),
        ("sourceTemplateId", source_template_id),
        ("sourceDownloadUrl", source_download_url),
        ("documentName", document_name),
        ("description", description),
        ("customMessage", custom_message),
        ("mode", mode),
        ("expiresInDays", expires_in_days),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/signatures/requests", payload)


@mcp.tool(description="Send a reminder for a pending signature request.")
async def send_signature_reminder(ctx: Context, signature_id: str) -> dict:
    return await _post(ctx, f"/api/signatures/requests/{signature_id}/send-reminder", {})


@mcp.tool(description="Void (cancel) an existing signature request.")
async def void_signature_request(ctx: Context, signature_id: str) -> dict:
    return await _post(ctx, f"/api/signatures/requests/{signature_id}/void", {})


# ===========================================================================
# Workflow-starting tools (async, return a job/tracking ID)
# ===========================================================================


@mcp.tool(description="Start an async motion-generation workflow for a case.")
async def generate_motion(
    ctx: Context,
    case_id: str,
    motion_type: str,
    support: str | None = None,
    include_exact_discovery_quotes: bool = False,
    discovery_file_ids: list[str] | None = None,
    app_id: str | None = None,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "motion_generation")
    if blocked is not None:
        return blocked
    payload: dict[str, object] = {
        "caseId": case_id,
        "motionType": motion_type,
        "support": support,
    }
    _set_payload_value(payload, "appId", app_id)
    if include_exact_discovery_quotes:
        payload["includeExactDiscoveryQuotes"] = True
    if discovery_file_ids:
        payload["discoveryFileIds"] = discovery_file_ids
    return await _post(
        ctx,
        "/api/generate-motion/",
        payload,
    )


@mcp.tool(description="Search case documents and workspace knowledge for a case via the generic RAG search path.")
async def rag_search(
    ctx: Context,
    case_id: str,
    query: str,
    top_k: int | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "caseId": case_id,
        "docketId": case_id,
        "query": query,
    }
    if app_id is not None:
        payload["appId"] = app_id
    if top_k is not None:
        payload["top_k"] = top_k
    return await _post(ctx, "/api/rag-query-proxy/", payload)


@mcp.tool(description="Start an async demand-letter workflow.")
async def demand_letter(
    ctx: Context,
    case_id: str | None = None,
    recipient_name: str | None = None,
    recipient_email: str | None = None,
    subject_line: str | None = None,
    support: str | None = None,
    app_id: str | None = None,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "demand_letter")
    if blocked is not None:
        return blocked
    payload: dict[str, object] = {
        "caseId": case_id,
        "recipientName": recipient_name,
        "recipientEmail": recipient_email,
        "subjectLine": subject_line,
        "support": support,
    }
    _set_payload_value(payload, "appId", app_id)
    return await _post(ctx, "/api/demand-letter/", payload)


@mcp.tool(description="Start an async lawsuit-generation workflow.")
async def generate_lawsuit(
    ctx: Context,
    case_id: str,
    support: str | None = None,
    include_exact_discovery_quotes: bool = False,
    discovery_file_ids: list[str] | None = None,
    app_id: str | None = None,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "lawsuit_generation")
    if blocked is not None:
        return blocked
    payload: dict[str, object] = {
        "caseId": case_id,
        "support": support,
    }
    _set_payload_value(payload, "appId", app_id)
    if include_exact_discovery_quotes:
        payload["includeExactDiscoveryQuotes"] = True
    if discovery_file_ids:
        payload["discoveryFileIds"] = discovery_file_ids
    return await _post(
        ctx,
        "/api/generate-lawsuit/",
        payload,
    )


@mcp.tool(description="Start an async adversarial motion workflow.")
async def adversarial(
    ctx: Context,
    case_id: str,
    support: str | None = None,
    app_id: str | None = None,
    motion_type: str | None = None,
    motion_name: str | None = None,
    include_exact_discovery_quotes: bool = False,
    discovery_file_ids: list[str] | None = None,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "adversarial_generation")
    if blocked is not None:
        return blocked
    payload: dict[str, object] = {"caseId": case_id}
    if support is not None:
        payload["support"] = support
    if app_id is not None:
        payload["appId"] = app_id
    if motion_type is not None:
        payload["motion_type"] = motion_type
    if motion_name is not None:
        payload["motionName"] = motion_name
    if include_exact_discovery_quotes:
        payload["includeExactDiscoveryQuotes"] = True
    if discovery_file_ids:
        payload["discoveryFileIds"] = discovery_file_ids
    return await _post(ctx, "/api/generate-adversarial-step/", payload)


@mcp.tool(description="Start an async deep-research workflow for a case.")
async def deep_research(ctx: Context, case_id: str, app_id: str | None = None) -> dict:
    blocked = _seriesai_litigation_block(app_id, "deep_research")
    if blocked is not None:
        return blocked
    return await _post(ctx, "/api/deep-research/", {"caseId": case_id, "appId": app_id})


@mcp.tool(description="Finish a draft document through the async finish-drafts workflow.")
async def finish_drafts(
    ctx: Context,
    case_id: str,
    document_s3_url: str,
    app_id: str | None = None,
    include_exact_discovery_quotes: bool = False,
    discovery_file_ids: list[str] | None = None,
) -> dict:
    payload: dict[str, object] = {
        "caseId": case_id,
        "documentS3Url": document_s3_url,
        "appId": app_id,
    }
    if include_exact_discovery_quotes:
        payload["includeExactDiscoveryQuotes"] = True
    if discovery_file_ids:
        payload["discoveryFileIds"] = discovery_file_ids
    return await _post(
        ctx,
        "/api/finish-drafts-step/",
        payload,
    )


@mcp.tool(description="Structure pasted or OCR-derived docket entries for a case.")
async def structure_docket_entries(
    ctx: Context,
    case_id: str,
    app_id: str,
    raw_text: str | None = None,
    source_type: str | None = None,
    source_artifact_id: str | None = None,
    docket_number: str | None = None,
    ocr_bucket: str | None = None,
    ocr_key: str | None = None,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "appId": app_id}
    if raw_text is not None:
        payload["rawText"] = raw_text
    if docket_number is not None:
        payload["docketNumber"] = docket_number
    if source_type is not None:
        payload["sourceType"] = source_type
    if source_artifact_id is not None:
        payload["sourceArtifactId"] = source_artifact_id
    if ocr_bucket is not None:
        payload["ocrBucket"] = ocr_bucket
    if ocr_key is not None:
        payload["ocrKey"] = ocr_key
    return await _post(ctx, "/api/manual-docket-structurer/", payload)


@mcp.tool(description="Generate or source an engagement letter and send it for signature.")
async def send_engagement_letter_for_signature(
    ctx: Context,
    user_id: str,
    case_id: str,
    app_id: str,
    signers: list[dict],
    answers: dict | None = None,
    source_artifact_id: str | None = None,
    source_file_id: str | None = None,
    source_download_url: str | None = None,
    document_name: str | None = None,
    custom_message: str | None = None,
) -> dict:
    blocked = _seriesai_litigation_block(app_id, "engagement_letter_signature")
    if blocked is not None:
        return blocked
    return await _post(
        ctx,
        "/api/core/engagement-letter-signature/",
        {
            "userId": user_id,
            "caseId": case_id,
            "appId": app_id,
            "signers": signers,
            "answers": answers,
            "sourceArtifactId": source_artifact_id,
            "sourceFileId": source_file_id,
            "sourceDownloadUrl": source_download_url,
            "documentName": document_name,
            "customMessage": custom_message,
        },
    )


@mcp.tool(description="Generate a document summary asynchronously.")
async def summarize_document(
    ctx: Context,
    document_url: str | None = None,
    case_id: str | None = None,
    app_id: str | None = None,
    docket_entry_id: str | None = None,
    file_id: str | None = None,
    file_key: str | None = None,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "appId": app_id}
    if docket_entry_id is not None:
        payload["docketEntryId"] = docket_entry_id
    elif file_id is not None:
        payload["fileId"] = file_id
    elif file_key is not None:
        payload["fileKey"] = file_key
    elif document_url is not None:
        payload["fileKey"] = document_url
    return await _post(
        ctx,
        "/api/summarize-document/",
        payload,
    )


@mcp.tool(description="Critique a document for accuracy, quality, and legal soundness.")
async def doc_critique(
    ctx: Context,
    document_url: str | None = None,
    case_id: str | None = None,
    app_id: str | None = None,
    s3_links: list[str] | None = None,
    inline_text: str | None = None,
    document_name: str | None = None,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "appId": app_id}
    links = s3_links or ([document_url] if document_url else None)
    if links:
        payload["s3_links"] = links
    if inline_text is not None:
        payload["inline_text"] = inline_text
    if document_name is not None:
        payload["documentName"] = document_name
    return await _post(
        ctx,
        "/api/doc-critique/",
        payload,
    )


@mcp.tool(description="Generate a lawsuit recommendation based on case details.")
async def recommend_lawsuit(ctx: Context, case_id: str | None = None, support: str | None = None) -> dict:
    return await _post(ctx, "/api/recommend-lawsuit/", {"caseId": case_id, "support": support})


# ===========================================================================
# Account-manager pass-through
# ===========================================================================


@mcp.tool(
    description=(
        "Ask the firm/account manager a question or give it an account-level "
        "instruction in plain English. Use it for firm-wide questions across "
        "all matters and members, not a single case: firm matters and "
        "unassigned matters, team members, workload and open tasks by "
        "assignee, who should take a new matter, outstanding invoices, "
        "collected revenue, unbilled work, realization, deadlines and "
        "exceptions across the firm, a firm management digest, the firm "
        "approval policy, and notification delivery preferences (frequency, "
        "time, and channel: email, Slack, Teams). Pass the user's request as "
        "`action`."
    ),
)
async def account_manager(ctx: Context, action: str, payload: dict | None = None) -> dict:
    body = dict(payload or {})
    body.pop("userId", None)  # the server binds the authenticated actor
    body.setdefault("message", action)
    result = await _post(ctx, "/api/account-manager/", body)
    if not isinstance(result, dict):
        return result
    # The raw response carries the full account snapshot (~20 KB); the model
    # only needs the answer and how it was routed.
    return {
        key: result[key]
        for key in ("responseText", "action", "classification", "visual", "workflowRunId")
        if key in result
    } or result


# ===========================================================================
# Scheduling tools
# ===========================================================================


@mcp.tool(
    description=(
        "Add an already-confirmed personal appointment, reservation, or existing external event to the "
        "authenticated user's connected Outlook calendar. This does not book a firm scheduling slot, "
        "send invitations, or create a legal matter deadline. Use only when the user explicitly asks "
        "to add the confirmed event and its title, start time, and timezone are known. Do not ask for a "
        "case or matter. If the user has multiple Outlook calendars, ask which one to use."
    ),
)
async def add_calendar_event(
    ctx: Context,
    app_id: str,
    title: str,
    start_at: str,
    time_zone: str,
    end_at: str | None = None,
    description: str | None = None,
    integration_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "appId": app_id,
        "title": title,
        "startAt": start_at,
        "timeZone": time_zone,
    }
    for key, value in (
        ("endAt", end_at),
        ("description", description),
        ("integrationId", integration_id),
    ):
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/core/integrations/calendar/events", payload)


@mcp.tool(description="List the authenticated host user's scheduling schedules.")
async def list_host_scheduling_schedules(ctx: Context) -> dict:
    return await _get(ctx, "/api/scheduling/schedules")


@mcp.tool(
    description=(
        "Search available scheduling slots. Use startAt, endAt, and timeZone; "
        "provide eventTypeId or slug. Legacy start_time/end_time and snake-case "
        "argument names are also accepted."
    )
)
async def search_scheduling_slots(
    ctx: Context,
    startAt: str | None = None,  # noqa: N803 - MCP schema exposes camelCase fields.
    endAt: str | None = None,  # noqa: N803 - MCP schema exposes camelCase fields.
    timeZone: str | None = None,  # noqa: N803 - MCP schema exposes camelCase fields.
    eventTypeId: str | None = None,  # noqa: N803 - MCP schema exposes camelCase fields.
    slug: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    time_zone: str | None = None,
    startTime: str | None = None,  # noqa: N803 - MCP schema exposes camelCase fields.
    endTime: str | None = None,  # noqa: N803 - MCP schema exposes camelCase fields.
    start_time: str | None = None,
    end_time: str | None = None,
    timezone: str | None = None,
    event_type_id: str | None = None,
) -> dict:
    start_value = startAt or start_at or startTime or start_time
    end_value = endAt or end_at or endTime or end_time
    zone_value = timeZone or time_zone or timezone
    event_type_value = eventTypeId or event_type_id
    if not start_value or not end_value:
        raise ValueError("startAt and endAt are required")
    if not event_type_value and not slug:
        raise ValueError("eventTypeId or slug is required")
    params = {"startAt": start_value, "endAt": end_value}
    if zone_value:
        params["timeZone"] = zone_value
    if event_type_value:
        params["eventTypeId"] = event_type_value
    if slug:
        params["slug"] = slug
    return await _get(ctx, "/api/scheduling/slots/", params)


@mcp.tool(description="List the authenticated host user's event types.")
async def list_host_scheduling_event_types(ctx: Context) -> dict:
    return await _get(ctx, "/api/scheduling/event-types")


@mcp.tool(description="List the host's scheduling bookings.")
async def list_host_scheduling_bookings(ctx: Context) -> dict:
    return await _get(ctx, "/api/scheduling/bookings")


@mcp.tool(description="Create a scheduling booking from a reservation.")
async def create_scheduling_booking(
    ctx: Context,
    event_type_id: str,
    reservation_uid: str,
    reservation_token: str,
    attendee: dict,
    time_zone: str,
    guests: list[str] | None = None,
    booking_answers: dict | None = None,
) -> dict:
    payload: dict[str, object] = {
        "eventTypeId": event_type_id,
        "reservationUid": reservation_uid,
        "reservationToken": reservation_token,
        "attendee": attendee,
        "timeZone": time_zone,
    }
    _set_payload_value(payload, "guests", guests)
    _set_payload_value(payload, "bookingAnswers", booking_answers)
    return await _post(ctx, "/api/scheduling/bookings", payload)


@mcp.tool(description="Create a fixed-time scheduling booking requested by the authenticated host.")
async def create_agent_scheduling_booking(
    ctx: Context,
    app_id: str,
    attendee: dict,
    start_at: str,
    time_zone: str,
    case_id: str | None = None,
    guests: list[str] | None = None,
    end_at: str | None = None,
    duration_minutes: int | None = None,
    title: str | None = None,
    description: str | None = None,
) -> dict:
    """Book a time named by the host without requiring an event type or reservation."""
    payload: dict[str, object] = {
        "appId": app_id,
        "attendee": attendee,
        "startAt": start_at,
        "timeZone": time_zone,
    }
    for key, value in (
        ("caseId", case_id),
        ("guests", guests),
        ("endAt", end_at),
        ("durationMinutes", duration_minutes),
        ("title", title),
        ("description", description),
    ):
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/scheduling/bookings/agent", payload)


@mcp.tool(description="Cancel a scheduling booking.")
async def cancel_scheduling_booking(ctx: Context, booking_id: str, reason: str | None = None) -> dict:
    return await _post(ctx, f"/api/scheduling/bookings/{booking_id}/cancel", {"reason": reason})


@mcp.tool(description="Confirm a pending scheduling booking.")
async def confirm_scheduling_booking(ctx: Context, booking_id: str, token: str | None = None) -> dict:
    payload: dict[str, object] = {}
    _set_payload_value(payload, "token", token)
    return await _post(ctx, f"/api/scheduling/bookings/{booking_id}/confirm", payload)


@mcp.tool(description="Reschedule an existing scheduling booking onto a new reserved slot.")
async def reschedule_scheduling_booking(
    ctx: Context,
    booking_id: str,
    reservation_uid: str,
    reservation_token: str,
    time_zone: str,
    token: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "reservationUid": reservation_uid,
        "reservationToken": reservation_token,
        "timeZone": time_zone,
    }
    _set_payload_value(payload, "token", token)
    return await _post(ctx, f"/api/scheduling/bookings/{booking_id}/reschedule", payload)


@mcp.tool(description="Retry conferencing (e.g. Zoom link) provisioning for a scheduling booking.")
async def retry_scheduling_conferencing(ctx: Context, booking_id: str) -> dict:
    return await _post(ctx, f"/api/scheduling/bookings/{booking_id}/conference/retry", {})


@mcp.tool(description="Connect the authenticated host's Zoom account for scheduling conferencing.")
async def connect_scheduling_conferencing(
    ctx: Context,
    app_id: str | None = None,
    set_as_default: bool | None = None,
) -> dict:
    payload: dict[str, object] = {}
    if app_id is not None:
        payload["appId"] = app_id
    _set_payload_value(payload, "setAsDefault", set_as_default)
    return await _post(ctx, "/api/integrations/conferencing/zoom/connect", payload)


@mcp.tool(description="Disconnect a scheduling conferencing connection (e.g. Zoom).")
async def disconnect_scheduling_conferencing(ctx: Context, connection_id: str, app_id: str) -> dict:
    return await _post(ctx, f"/api/integrations/conferencing/{connection_id}/disconnect", {"appId": app_id})


@mcp.tool(description="Create a new scheduling event type (a bookable meeting kind).")
async def create_scheduling_event_type(
    ctx: Context,
    app_id: str,
    slug: str,
    title: str,
    duration_minutes: int,
    schedule_id: str,
    location_type: str,
    legal_team_id: str | None = None,
    case_id: str | None = None,
    description: str | None = None,
    is_public: bool | None = None,
    is_active: bool | None = None,
) -> dict:
    payload: dict[str, object] = {
        "appId": app_id,
        "slug": slug,
        "title": title,
        "durationMinutes": duration_minutes,
        "scheduleId": schedule_id,
        "locationType": location_type,
    }
    optional_values = (
        ("legalTeamId", legal_team_id),
        ("caseId", case_id),
        ("description", description),
        ("isPublic", is_public),
        ("isActive", is_active),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/scheduling/event-types", payload)


@mcp.tool(description="Update an existing scheduling event type.")
async def update_scheduling_event_type(
    ctx: Context,
    event_type_id: str,
    slug: str | None = None,
    title: str | None = None,
    duration_minutes: int | None = None,
    schedule_id: str | None = None,
    location_type: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
) -> dict:
    payload: dict[str, object] = {}
    optional_values = (
        ("slug", slug),
        ("title", title),
        ("durationMinutes", duration_minutes),
        ("scheduleId", schedule_id),
        ("locationType", location_type),
        ("description", description),
        ("isActive", is_active),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _patch(ctx, f"/api/scheduling/event-types/{event_type_id}", payload)


@mcp.tool(description="Delete a scheduling event type.")
async def delete_scheduling_event_type(ctx: Context, event_type_id: str) -> dict:
    return await _delete(ctx, f"/api/scheduling/event-types/{event_type_id}")


@mcp.tool(
    description="Fetch the public scheduling link for an event type slug (optionally scoped to a case/legal team).",
)
async def get_public_scheduling_link(ctx: Context, slug: str, scope_id: str | None = None) -> dict:
    path = f"/api/scheduling/links/{scope_id}/{slug}/" if scope_id else f"/api/scheduling/links/{slug}/"
    return await _get(ctx, path)


@mcp.tool(description="Create a new scheduling availability schedule for the authenticated host.")
async def create_scheduling_schedule(
    ctx: Context,
    app_id: str,
    timezone: str,
    availability_rules: list[dict],
    minimum_notice_minutes: int | None = None,
    is_default: bool | None = None,
    is_active: bool | None = None,
) -> dict:
    payload: dict[str, object] = {
        "appId": app_id,
        "timezone": timezone,
        "availabilityRules": availability_rules,
    }
    optional_values = (
        ("minimumNoticeMinutes", minimum_notice_minutes),
        ("isDefault", is_default),
        ("isActive", is_active),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/scheduling/schedules", payload)


@mcp.tool(description="Update an existing scheduling availability schedule.")
async def update_scheduling_schedule(
    ctx: Context,
    schedule_id: str,
    timezone: str | None = None,
    availability_rules: list[dict] | None = None,
    minimum_notice_minutes: int | None = None,
    is_default: bool | None = None,
    is_active: bool | None = None,
) -> dict:
    payload: dict[str, object] = {}
    optional_values = (
        ("timezone", timezone),
        ("availabilityRules", availability_rules),
        ("minimumNoticeMinutes", minimum_notice_minutes),
        ("isDefault", is_default),
        ("isActive", is_active),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _patch(ctx, f"/api/scheduling/schedules/{schedule_id}", payload)


@mcp.tool(description="Reserve a scheduling slot ahead of confirming a booking.")
async def create_scheduling_reservation(
    ctx: Context,
    event_type_id: str,
    slot_start_at: str,
    slot_end_at: str,
    time_zone: str,
    attendee: dict | None = None,
) -> dict:
    payload: dict[str, object] = {
        "eventTypeId": event_type_id,
        "slotStartAt": slot_start_at,
        "slotEndAt": slot_end_at,
        "timeZone": time_zone,
    }
    _set_payload_value(payload, "attendee", attendee)
    return await _post(ctx, "/api/scheduling/slots/reservations", payload)


@mcp.tool(description="Delete (release) a scheduling slot reservation.")
async def delete_scheduling_reservation(ctx: Context, reservation_uid: str, reservation_token: str) -> dict:
    return await _delete(
        ctx,
        f"/api/scheduling/slots/reservations/{reservation_uid}/",
        {"reservationToken": reservation_token},
    )


@mcp.tool(description="Reconcile a signature request's state with the e-signature provider.")
async def reconcile_signature_request(ctx: Context, signature_id: str) -> dict:
    return await _post(ctx, f"/api/signatures/requests/{signature_id}/reconcile", {})


@mcp.tool(description="Create a self-sign session so the authenticated user can sign their own document.")
async def create_self_sign_session(ctx: Context, signature_id: str) -> dict:
    return await _post(ctx, f"/api/signatures/requests/{signature_id}/self-sign-session", {})


@mcp.tool(description="Add a new step/collaborator to an in-progress case-add workflow.")
async def add_case_step(
    ctx: Context,
    docket_id: str,
    role: str,
    user_id: str | None = None,
    target_user_id: str | None = None,
    app_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {"docketId": docket_id, "role": role}
    optional_values = (
        ("userId", user_id),
        ("targetUserId", target_user_id),
        ("appId", app_id),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/add-case-step/", payload)


@mcp.tool(description="Trigger (or re-run) AI-driven action-item generation for a case.")
async def cr_action_items_gen(
    ctx: Context,
    case_id: str,
    app_id: str,
    user_id: str | None = None,
    manual_run: bool | None = None,
) -> dict:
    payload: dict[str, object] = {"caseId": case_id, "appId": app_id}
    optional_values = (
        ("userId", user_id),
        ("manualRun", manual_run),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/cr-action-items-gen/", payload)


@mcp.tool(description="Create a brand-new case record.")
async def create_new_case(
    ctx: Context,
    app_id: str,
    case_name: str,
    user_id: str,
    case_id: str | None = None,
    docket_id: str | None = None,
    court_id: str | None = None,
    docket_number: str | None = None,
) -> dict:
    payload: dict[str, object] = {"appId": app_id, "caseName": case_name, "userId": user_id}
    optional_values = (
        ("caseId", case_id),
        ("docketId", docket_id),
        ("courtId", court_id),
        ("docketNumber", docket_number),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/create-new-case/", payload)



@mcp.tool(description="Start a SAFE draft workflow for a Vienna venture investor and queue it for user approval.")
async def series_ai_trigger_safe_draft(  # noqa: PLR0913 - MCP tool schema is intentionally flat
    ctx: Context,
    app_id: str,
    organization_id: str,
    investor_name: str,
    investor_email: str,
    investment_amount: float,
    valuation_cap: float | None = None,
    discount_rate: float | None = None,
    pro_rata_rights: bool | None = None,
    round_id: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "toolName": "triggerSafeDraft",
        "appId": app_id,
        "organizationId": organization_id,
        "investorName": investor_name,
        "investorEmail": investor_email,
        "investmentAmount": investment_amount,
    }
    optional_values = (
        ("valuationCap", valuation_cap),
        ("discountRate", discount_rate),
        ("proRataRights", pro_rata_rights),
        ("roundId", round_id),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/tools/execute/", payload)


@mcp.tool(description="Queue a round update email to venture investors, optionally including a cap table summary.")
async def series_ai_send_round_update(
    ctx: Context,
    app_id: str,
    organization_id: str,
    round_id: str,
    message: str | None = None,
    include_cap_table_summary: bool | None = None,
) -> dict:
    payload: dict[str, object] = {
        "toolName": "sendRoundUpdate",
        "appId": app_id,
        "organizationId": organization_id,
        "roundId": round_id,
    }
    _set_payload_value(payload, "message", message)
    _set_payload_value(payload, "includeCapTableSummary", include_cap_table_summary)
    return await _post(ctx, "/api/tools/execute/", payload)


@mcp.tool(description="Read-only query of a venture organization's committed cap table ownership.")
async def series_ai_query_cap_table_ownership(
    ctx: Context,
    app_id: str,
    organization_id: str,
    as_of: str | None = None,
    include_scenarios: bool | None = None,
) -> dict:
    payload: dict[str, object] = {
        "toolName": "queryCapTableOwnership",
        "appId": app_id,
        "organizationId": organization_id,
    }
    _set_payload_value(payload, "asOf", as_of)
    _set_payload_value(payload, "includeScenarios", include_scenarios)
    return await _post(ctx, "/api/tools/execute/", payload)


@mcp.tool(description="Schedule a venture compliance deadline (83(b) election, Form D, franchise tax, etc.).")
async def series_ai_schedule_compliance_deadline(  # noqa: PLR0913 - MCP tool schema is intentionally flat
    ctx: Context,
    app_id: str,
    organization_id: str,
    deadline_type: str,
    due_date: str,
    linked_object_id: str | None = None,
    linked_object_type: str | None = None,
    notes: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "toolName": "scheduleComplianceDeadline",
        "appId": app_id,
        "organizationId": organization_id,
        "deadlineType": deadline_type,
        "dueDate": due_date,
    }
    optional_values = (
        ("linkedObjectId", linked_object_id),
        ("linkedObjectType", linked_object_type),
        ("notes", notes),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/tools/execute/", payload)


@mcp.tool(description="Request signatures on a Vienna document instance and queue it for user approval.")
async def series_ai_request_signatures_via_email(  # noqa: PLR0913 - MCP tool schema is intentionally flat
    ctx: Context,
    app_id: str,
    organization_id: str,
    document_instance_id: str,
    signers: list[dict],
    message: str | None = None,
    expires_in_days: int | None = None,
) -> dict:
    payload: dict[str, object] = {
        "toolName": "requestSignaturesViaEmail",
        "appId": app_id,
        "organizationId": organization_id,
        "documentInstanceId": document_instance_id,
        "signers": signers,
    }
    _set_payload_value(payload, "message", message)
    _set_payload_value(payload, "expiresInDays", expires_in_days)
    return await _post(ctx, "/api/tools/execute/", payload)


@mcp.tool(description="Invite an external deal participant (investor, accelerator, or deal-side lawyer) to a Vienna venture workspace.")
async def series_ai_invite_external_participant(  # noqa: PLR0913 - MCP tool schema is intentionally flat
    ctx: Context,
    app_id: str,
    organization_id: str,
    participant_type: str,
    organization_name: str,
    primary_contact_name: str,
    primary_contact_email: str,
    role_in_deal: str,
    access_scope: str,
    associated_round_id: str | None = None,
    expires_in_days: int | None = None,
) -> dict:
    payload: dict[str, object] = {
        "toolName": "inviteExternalParticipant",
        "appId": app_id,
        "organizationId": organization_id,
        "participantType": participant_type,
        "organizationName": organization_name,
        "primaryContactName": primary_contact_name,
        "primaryContactEmail": primary_contact_email,
        "roleInDeal": role_in_deal,
        "accessScope": access_scope,
    }
    optional_values = (
        ("associatedRoundId", associated_round_id),
        ("expiresInDays", expires_in_days),
    )
    for key, value in optional_values:
        _set_payload_value(payload, key, value)
    return await _post(ctx, "/api/tools/execute/", payload)


# ===========================================================================
# Otter.ai read tools
# ===========================================================================


async def _otter_request(path: str, *, params: dict[str, object] | None = None) -> dict:
    """Return a structured unavailable result when the optional connector is off."""
    try:
        return await otter_request(path, params=params)
    except RuntimeError as exc:
        if str(exc) == "OTTER_API_KEY is not configured":
            return {"ok": False, "error": "otter_not_configured", "detail": "Otter.ai is not configured."}
        raise


@mcp.tool(description="List Otter.ai channels visible to the configured workspace account.")
async def otter_list_channels() -> dict:
    return await _otter_request("/channels")


@mcp.tool(description="List recent Otter.ai conversations, optionally filtered by channel.")
async def otter_list_conversations(
    include_shared: bool | None = None,
    channel_id: str | None = None,
    limit: int | None = 20,
    cursor: str | None = None,
) -> dict:
    return await _otter_request(
        "/conversations",
        params={
            "include_shared": include_shared,
            "channel_id": channel_id,
            "limit": limit,
            "cursor": cursor,
        },
    )


@mcp.tool(description="Fetch an Otter.ai conversation summary, transcript, insights, outline, and action items.")
async def otter_get_conversation(conversation_id: str, include: str = "all") -> dict:
    return await _otter_request(f"/conversations/{conversation_id}", params={"include": include})


@mcp.tool(description="Get the MP3 download URL for an Otter.ai conversation.")
async def otter_get_conversation_audio(conversation_id: str) -> dict:
    return await _otter_request(f"/conversations/{conversation_id}/audio")


@mcp.tool(description="Get workspace information for the configured Otter.ai account.")
async def otter_get_workspace() -> dict:
    return await _otter_request("/workspace")


@mcp.tool(
    description=(
        "Navigate the callable-function catalog. Omit category_id for the category and subgroup table of contents. "
        "Pass a category_id to get all its tools, or add subcategory_id to narrow to one subgroup."
    )
)
async def browse_function_map(
    ctx: Context,
    category_id: str | None = None,
    tool_names: list[str] | None = None,
    subcategory_id: str | None = None,
) -> dict:
    """Return the hierarchy, a category's full tool records, or one subgroup's records."""
    del ctx
    allowed_names = set(tool_names) if tool_names is not None else None
    records = []
    for tool in mcp._tool_manager.list_tools():  # noqa: SLF001 - FastMCP exposes no public list API here.
        if tool.name == "browse_function_map":
            continue
        if allowed_names is not None and tool.name not in allowed_names:
            continue
        records.append(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
            }
        )
    return _browse_function_map(
        universal_function_records(records, include_chat_run_records=allowed_names is None),
        category_id,
        subcategory_id,
    )


# ===========================================================================
# Starlette app wrapping FastMCP's SSE/Streamable-HTTP transport
# ===========================================================================


class SecretGateMiddleware(BaseHTTPMiddleware):
    """Reject requests that don't include the shared MCP_SERVER_SECRET when set."""

    async def dispatch(self, request: Request, call_next):
        if MCP_SERVER_SECRET:
            provided = request.headers.get("x-mcp-secret", "")
            if provided != MCP_SERVER_SECRET:
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)


app = mcp.streamable_http_app()

if MCP_SERVER_SECRET:
    app.add_middleware(SecretGateMiddleware)
