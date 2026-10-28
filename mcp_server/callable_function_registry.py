# Vendored from django-hub/jurist_backend/core/services/callable_function_registry.py
"""Canonical callable-function metadata consumed by existing Django surfaces.

MCP wrappers and external callers are being migrated to this registry in stages.
This module currently owns the Django chat-run tool definitions and scopes.
"""

# Ruff's implicit-concatenation fixer conflicts with Black for long guidance strings.
# ruff: noqa: ISC001

from copy import deepcopy
from types import MappingProxyType
from typing import Any

try:
    from jurist_backend.core.services.function_taxonomy import browse_function_map
    from jurist_backend.core.services.function_taxonomy import categorize_function_name
except ModuleNotFoundError:  # Vendored into the Chatbot MCP sidecar beside function_taxonomy.py.
    try:
        from mcp_server.function_taxonomy import browse_function_map
        from mcp_server.function_taxonomy import categorize_function_name
    except ModuleNotFoundError:
        from function_taxonomy import browse_function_map
        from function_taxonomy import categorize_function_name

_EPONA_CHAT_RUN_TOOL_GUIDANCE = [
    {
        "name": "list_epona_matters",
        "operationIds": ["list-epona-matters"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": (
            "Search only matters visible to the authenticated user's connected Epona ContactManager account. "
            "Pass the user's words as query; it matches a matter code prefix or any part of the matter name."
        ),
    },
    {
        "name": "get_epona_matter",
        "operationIds": ["get-epona-matter"],
        "requiredInputs": ["matterKey"],
        "mutatesState": False,
        "invocationGuidance": (
            "Use only a matterKey returned by list_epona_matters; never invent one. "
            "Returns the matter with its parties and the linked Vienna case, if any."
        ),
    },
    {
        "name": "search_epona_contacts",
        "operationIds": ["search-epona-contacts"],
        "requiredInputs": ["query"],
        "mutatesState": False,
        "invocationGuidance": (
            "Search only contacts visible to the authenticated user's connected Epona ContactManager account. "
            "Identity-document, citizen-number and birth data are never available; do not ask for them."
        ),
    },
]

_CHAT_RUN_TOOL_GUIDANCE = [
    *_EPONA_CHAT_RUN_TOOL_GUIDANCE,
    {
        "name": "get_seriesai_company_details",
        "operationIds": ["retrieve-company-details"],
        "requiredInputs": ["caseId", "appId"],
        "mutatesState": False,
        "invocationGuidance": (
            "Read the company profile for the authenticated SeriesAI caseId and appId scope. "
            "Returns the company details and the existing email/Slack summary fields."
        ),
    },
    {
        "name": "get_seriesai_company_cap_table",
        "operationIds": ["retrieve-cap-table"],
        "requiredInputs": ["caseId", "appId"],
        "mutatesState": False,
        "invocationGuidance": (
            "Read the cap table for the authenticated SeriesAI caseId and appId scope. "
            "Returns company ownership entries and summary fields."
        ),
    },
    {
        "name": "list_clio_matters",
        "operationIds": ["list-clio-matters"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": "List only matters visible to the authenticated user's connected Clio Manage account.",
    },
    {
        "name": "list_relativity_workspaces",
        "operationIds": ["list-relativity-workspaces"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": (
            "List only workspaces visible to the authenticated user's connected RelativityOne account."
        ),
    },
    {
        "name": "search_relativity_documents",
        "operationIds": ["search-relativity-documents"],
        "requiredInputs": ["workspaceArtifactId", "condition"],
        "mutatesState": False,
        "invocationGuidance": (
            "Use only a workspaceArtifactId selected from the connected RelativityOne account; never invent one."
        ),
    },
    {
        "name": "get_relativity_document",
        "operationIds": ["get-relativity-document"],
        "requiredInputs": ["workspaceArtifactId", "documentArtifactId"],
        "mutatesState": False,
        "invocationGuidance": (
            "Retrieve only the explicitly selected RelativityOne workspace and document artifact IDs."
        ),
    },
    {
        "name": "search_imanage_documents",
        "operationIds": ["search-imanage-documents"],
        "requiredInputs": ["libraryId", "query"],
        "mutatesState": False,
        "invocationGuidance": (
            "Search only the connected iManage Work library identified by the user or prior tool result."
        ),
    },
    {
        "name": "search_sharepoint_documents",
        "operationIds": ["search-sharepoint-documents"],
        "requiredInputs": ["query"],
        "mutatesState": False,
        "invocationGuidance": (
            "Search documents across every SharePoint library the user's connected account can read. "
            "Pass the user's words as query. Read-only."
        ),
    },
    {
        "name": "search_sharepoint_sites",
        "operationIds": ["search-sharepoint-sites"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": "List or search SharePoint sites the user's connected account can read. Read-only.",
    },
    {
        "name": "list_sharepoint_site_drives",
        "operationIds": ["list-sharepoint-site-drives"],
        "requiredInputs": ["siteId"],
        "mutatesState": False,
        "surfaces": ["chat_proxy"],
        "inputSchemasBySurface": {
            "chat_proxy": {
                "type": "object",
                "properties": {
                    "siteId": {
                        "type": "string",
                        "description": "A siteId returned by search_sharepoint_sites.",
                    },
                },
                "required": ["siteId"],
            },
        },
        "argumentBindingsBySurface": {"chat_proxy": {"siteId": "site_id"}},
        "invocationGuidance": "Use only a siteId returned by search_sharepoint_sites; never invent one.",
    },
    {
        "name": "list_sharepoint_drive_children",
        "operationIds": ["list-sharepoint-drive-children"],
        "requiredInputs": ["driveId"],
        "mutatesState": False,
        "surfaces": ["chat_proxy"],
        "inputSchemasBySurface": {
            "chat_proxy": {
                "type": "object",
                "properties": {
                    "driveId": {"type": "string", "description": "A driveId returned by list_sharepoint_site_drives."},
                    "itemId": {"type": "string", "description": "A folder itemId returned by a prior list_sharepoint_drive_children call."},
                },
                "required": ["driveId"],
            },
        },
        "argumentBindingsBySurface": {"chat_proxy": {"driveId": "drive_id", "itemId": "item_id"}},
        "invocationGuidance": (
            "Use only a driveId from list_sharepoint_site_drives and, to open a folder, only an itemId from a "
            "previous SharePoint result; never invent either."
        ),
    },
    {
        "name": "list_clio_matter_documents",
        "operationIds": ["list-clio-matter-documents"],
        "requiredInputs": ["matterId"],
        "mutatesState": False,
        "invocationGuidance": (
            "List documents only for the explicitly selected matter visible to the authenticated Clio user."
        ),
    },
    {
        "name": "list_clio_document_versions",
        "operationIds": ["list-clio-document-versions"],
        "requiredInputs": ["documentId"],
        "mutatesState": False,
        "invocationGuidance": "List versions only for the explicitly selected Clio document.",
    },
    {
        "name": "import_clio_document_version",
        "operationIds": ["import-clio-version"],
        "requiredInputs": [
            "caseId",
            "matterId",
            "documentId",
            "versionId",
            "confirmation",
        ],
        "mutatesState": True,
        "invocationGuidance": (
            "Require the exact user confirmation CONFIRM CLIO IMPORT before invoking. "
            "Import only the explicitly selected Clio document version into the provided case."
        ),
    },
    {
        "name": "refresh_clio_import",
        "operationIds": ["refresh-clio-import"],
        "requiredInputs": ["importId", "confirmation"],
        "mutatesState": True,
        "invocationGuidance": (
            "Require the exact user confirmation CONFIRM CLIO REFRESH before invoking. "
            "Refresh only the previously imported Clio document identified by importId."
        ),
    },
    {
        "name": "update_clio_matter",
        "operationIds": ["update-clio-matter"],
        "requiredInputs": ["matterId", "fields", "confirmation"],
        "mutatesState": True,
        "invocationGuidance": (
            "Require the exact user confirmation CONFIRM CLIO UPDATE before invoking. Never infer confirmation."
        ),
    },
    {
        "name": "update_clio_contact",
        "operationIds": ["update-clio-contact"],
        "requiredInputs": ["contactId", "fields", "confirmation"],
        "mutatesState": True,
        "invocationGuidance": (
            "Require the exact user confirmation CONFIRM CLIO UPDATE before invoking. Never infer confirmation."
        ),
    },
    {
        "name": "retrieve_case_details",
        "operationIds": ["get-case-metadata"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": "Always pass caseId in the request body.",
    },
    {
        "name": "retrieve_case_timeline",
        "operationIds": ["list-case-timeline"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": "Always pass caseId when listing case timeline entries.",
    },
    {
        "name": "retrieve_latest_docket_entry",
        "operationIds": ["get-latest-docket-entry"],
        "requiredInputs": ["caseId", "appId"],
        "invocationGuidance": (
            "Always pass the exact caseId and appId from the authorized case context "
            "when fetching the latest docket entry."
        ),
    },
    {
        "name": "retrieve_case_calendar",
        "operationIds": ["get-case-calendar"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": "Always pass caseId when fetching the case calendar.",
    },
    {
        "name": "people_insight",
        "operationIds": ["read-people-dossiers"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": (
            "For a specific person lookup, pass caseId and name so the tool can resolve the case individual; "
            "pass email when it is already known. Also pass appId from user context: one person holds a "
            "separate record per app, so without it the tool cannot tell which app's record to answer from. "
            "Use includeAll=true only when the user explicitly asks for the full people list."
        ),
    },
    {
        "name": "retrieve_case_billing_summary",
        "operationIds": ["retrieve-case-billing-summary"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": "Always pass caseId when retrieving a case billing summary.",
    },
    {
        "name": "manage_organization",
        "operationIds": [
            "list-organization-members",
            "get-organization-approval-policy",
            "update-organization-approval-policy",
        ],
        "requiredInputs": ["operation"],
        "mutatesState": True,
        "invocationGuidance": (
            "Use operation=list_members to list members, operation=get_approval_policy to read the policy, "
            "or operation=update_approval_policy with approvalPolicy and explicit confirmation to change it."
        ),
    },
    {
        "name": "list_legal_team_members",
        "operationIds": ["list-legal-team-members"],
        "requiredInputs": [],
        "invocationGuidance": "Use this to list members of a legal team.",
    },
    {
        "name": "list_whatsapp_messages",
        "operationIds": ["whatsapp-messages"],
        "requiredInputs": ["caseId"],
        "mutatesState": False,
        "invocationGuidance": (
            "Read only new WhatsApp Business messages for the explicitly authorized case. "
            "Resolve the connected WhatsApp account through list_whatsapp_connections "
            "unless the user explicitly names a connection; never ask for an opaque "
            "connection ID or read unassociated conversations."
        ),
    },
    {
        "name": "send_whatsapp_message",
        "operationIds": ["whatsapp-messages"],
        "requiredInputs": ["caseId", "connectionId", "to", "message"],
        "mutatesState": True,
        "sendsExternalMessage": True,
        "invocationGuidance": (
            "Send only from the connected firm's WhatsApp Business number after case "
            "authorization; use an approved template when Meta requires one."
        ),
    },
    {
        "name": "associate_whatsapp_message",
        "operationIds": ["whatsapp-message-associate"],
        "requiredInputs": ["caseId", "messageId"],
        "mutatesState": True,
        "invocationGuidance": (
            "Explicitly import the identified WhatsApp message or attachment into the "
            "authorized case; do not attach all messages automatically."
        ),
    },
    {
        "name": "assign_user_to_case",
        "operationIds": ["assign-user-to-case"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": (
            "Always pass caseId and the specific target user; include appId whenever the "
            "case ID may exist in multiple products."
        ),
    },
    {
        "name": "remove_user_from_case",
        "operationIds": ["remove-user-from-case"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": "Always pass caseId, and include the specific user email when removing a collaborator.",
    },
    {
        "name": "create_signature_request",
        "operationIds": ["create-signature-request"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": (
            "Always pass caseId and include the document plus signer details before creating a signature request."
        ),
    },
    {
        "name": "list_case_signatures",
        "operationIds": ["list-case-signatures"],
        "requiredInputs": ["caseId"],
        "invocationGuidance": "Always pass caseId when listing signature requests for a case.",
    },
    {
        "name": "list_organization_signatures",
        "operationIds": ["list-organization-signatures"],
        "requiredInputs": ["organizationId"],
        "invocationGuidance": (
            "Use this for organization/team workspace signature queues. "
            "Always pass the exact organizationId from context. "
            "This spans every matter in the firm, so it is admin-only and returns 403 for "
            "other users - prefer list_case_signatures when the user is asking about one "
            "matter, and relay the 403 as a permissions limit rather than retrying."
        ),
    },
    {
        "name": "get_signature_request_detail",
        "operationIds": ["get-signature-request-detail"],
        "requiredInputs": [],
        "invocationGuidance": "Use this when the user identifies a concrete signature request to inspect.",
    },
    {
        "name": "send_signature_reminder",
        "operationIds": ["send-signature-reminder"],
        "requiredInputs": [],
        "invocationGuidance": "Use this only when a concrete signature request is identified for the reminder.",
    },
    {
        "name": "void_signature_request",
        "operationIds": ["void-signature-request"],
        "requiredInputs": [],
        "invocationGuidance": (
            "Use this only when a concrete signature request is identified and the user explicitly wants it voided."
        ),
    },
    {
        "name": "create_agent_scheduling_booking",
        "operationIds": ["create-agent-scheduling-booking"],
        "requiredInputs": ["appId", "attendee", "startAt", "timeZone"],
        "invocationGuidance": (
            "Use for a direct request to schedule a meeting at a specific time. Pass appId from user context, "
            "attendee.email, an ISO 8601 startAt with offset, and an IANA timeZone. "
            "durationMinutes defaults to 30 when omitted."
        ),
    },
    {
        "name": "generate_pitch_deck",
        "appIds": ("3", "4", "5"),
        "operationIds": ["generate-pitch-deck"],
        "requiredInputs": ["caseId", "purpose", "confirmation", "idempotencyKey"],
        "mutatesState": True,
        "startsAsyncWorkflow": True,
        "description": "Generate a private case-grounded deck for the user's stated audience and goal.",
        "invocationGuidance": (
            "Use only for an authorized case. Before invoking, get the user's explicit "
            "confirmation and a clear purpose for the deck: ask what it is for, who will "
            "use or read it, and what they should understand or decide; "
            "preserve their stated audience, objective, and emphasis in the purpose field (up to 1,200 "
            "characters); ask a concise follow-up if these are missing or materially ambiguous. Pass the exact caseId "
            "and a stable idempotencyKey. Purpose guides the narrative only; it never "
            "expands the authorized case sources. "
            "For app 3/4, use the investor-pitch type. For app 5, use only inhouse_matter_update, "
            "inhouse_ma_transaction, or inhouse_firm_presentation when a specific type is needed; otherwise omit "
            "deckType and let the case choose its default. "
            "Never claim the deck is ready from the queued response."
        ),
    },
    {
        "name": "retrieve_pitch_deck_job_status",
        "appIds": ("3", "4", "5"),
        "operationIds": ["get-pitch-deck-job-status"],
        "requiredInputs": ["caseId", "jobId"],
        "mutatesState": False,
        "startsAsyncWorkflow": False,
        "invocationGuidance": (
            "Use only for an authorized Vienna company case and the exact jobId returned when generation started. "
            "Report the job-service status. If it is queued or generating, say the job is still in progress; "
            "retrieve the result only after status is completed."
        ),
    },
    {
        "name": "retrieve_pitch_deck_job_result",
        "appIds": ("3", "4", "5"),
        "operationIds": ["get-pitch-deck-job-result"],
        "requiredInputs": ["caseId", "jobId"],
        "mutatesState": False,
        "startsAsyncWorkflow": False,
        "invocationGuidance": (
            "Use only after the exact job is reported completed for an authorized Vienna company case. "
            "Return only the private PPTX/PDF download URLs provided by the service; never claim artifacts exist "
            "unless this operation returns them."
        ),
    },
]

_IN_HOUSE_CHAT_RUN_TOOL_GUIDANCE = [
    {
        "name": "retrieve_in_house_requests",
        "operationIds": ["in-house-request-list"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": "List only the authenticated organization's In-House requests.",
    },
    {
        "name": "create_in_house_request",
        "operationIds": ["in-house-request-create"],
        "requiredInputs": ["subject", "description", "idempotencyKey"],
        "mutatesState": True,
        "invocationGuidance": (
            "Require explicit user confirmation of the request details before creating it. Set source_channel to "
            "the turn's runtime channel (slack, microsoft_teams, email, or web) and source_ref to only the routing "
            "references in context: slackChannelId and slackThreadTs, teamsConversationId, or emailMessageId. "
            "Never put message text in source_ref."
        ),
    },
    {
        "name": "search_in_house_knowledge",
        "operationIds": ["in-house-knowledge-search"],
        "requiredInputs": ["q"],
        "mutatesState": False,
        "invocationGuidance": (
            "Use for questions about the organization's contracts, obligations, deadlines, owners, past review "
            "decisions, or requests (for example 'when is notice due and who owns it'). Answer only from the "
            "returned records, cite each fact with its cite value, and quote the clause text."
        ),
    },
    {
        "name": "retrieve_in_house_counterparties",
        "operationIds": ["in-house-counterparty-list"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": "List each counterparty with its agreements, open obligations, and requests.",
    },
    {
        "name": "triage_in_house_request",
        "operationIds": [
            "in-house-request-triage-get",
            "in-house-request-triage-action",
        ],
        "requiredInputs": ["request_id"],
        "mutatesState": True,
        "invocationGuidance": (
            "Legal staff only. Read the AI triage suggestion for one request; apply or dismiss it only after the "
            "user confirms, and pass their edits as overrides."
        ),
    },
    {
        "name": "create_in_house_matter",
        "operationIds": ["in-house-matter-create"],
        "requiredInputs": ["case_id", "matter_type"],
        "mutatesState": True,
        "invocationGuidance": (
            "Create only the explicitly confirmed In-House matter in the authenticated organization."
        ),
    },
    {
        "name": "create_in_house_request_comment",
        "operationIds": ["in-house-request-comment-create"],
        "requiredInputs": ["request_id", "body", "idempotencyKey"],
        "mutatesState": True,
        "invocationGuidance": (
            "Add the user's explicitly provided facts or follow-up to the authenticated organization's request."
        ),
    },
    {
        "name": "transition_in_house_request",
        "operationIds": ["in-house-request-transition"],
        "requiredInputs": ["request_id", "status", "idempotencyKey"],
        "mutatesState": True,
        "invocationGuidance": ("Transition only the explicitly identified request to the user-confirmed status."),
    },
    {
        "name": "create_in_house_contract",
        "operationIds": ["in-house-contract-create"],
        "requiredInputs": ["counterparty", "contract_type"],
        "mutatesState": True,
        "invocationGuidance": (
            "Create a contract record only after the user confirms the counterparty and contract type."
        ),
    },
    {
        "name": "upload_in_house_contract_version",
        "operationIds": ["in-house-contract-version-create"],
        "requiredInputs": [
            "contract_id",
            "content_hash",
            "filename",
            "storage_reference",
        ],
        "mutatesState": True,
        "invocationGuidance": (
            "Register only the explicitly supplied contract artifact and preserve its "
            "content hash and storage reference."
        ),
    },
    {
        "name": "create_in_house_obligation",
        "operationIds": ["in-house-obligation-create"],
        "requiredInputs": ["contract_version", "obligation_type"],
        "mutatesState": True,
        "invocationGuidance": "Create an obligation only from facts explicitly supplied or confirmed by the user.",
    },
    {
        "name": "transition_in_house_obligation",
        "operationIds": ["in-house-obligation-transition"],
        "requiredInputs": ["obligation_id", "status", "idempotencyKey"],
        "mutatesState": True,
        "invocationGuidance": (
            "Confirm, waive, or complete only the explicitly identified obligation after "
            "the user confirms the status."
        ),
    },
    {
        "name": "create_in_house_approval_workflow",
        "operationIds": ["in-house-contract-approval-create"],
        "requiredInputs": ["contract_id", "contract_version", "approver_ids"],
        "mutatesState": True,
        "invocationGuidance": "Start approval only after the user confirms the contract version and approvers.",
    },
    {
        "name": "decide_in_house_approval_step",
        "operationIds": ["in-house-approval-decision"],
        "requiredInputs": ["workflow_id", "step_id", "decision"],
        "mutatesState": True,
        "invocationGuidance": (
            "Record an approval decision only when the authenticated approver explicitly provides it."
        ),
    },
    {
        "name": "retrieve_in_house_matter",
        "operationIds": ["in-house-matter-list"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": "List only the authenticated organization's In-House matters.",
    },
    {
        "name": "retrieve_in_house_contracts",
        "operationIds": ["in-house-contract-list"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": "List only the authenticated organization's In-House contracts and versions.",
    },
    {
        "name": "retrieve_in_house_review_findings",
        "operationIds": ["in-house-review-findings-list"],
        "requiredInputs": ["run_id"],
        "mutatesState": False,
        "invocationGuidance": (
            "List only the authenticated organization's findings for the explicitly identified review run."
        ),
    },
    {
        "name": "retrieve_in_house_review_citations",
        "operationIds": ["in-house-review-finding-citations"],
        "requiredInputs": ["finding_id"],
        "mutatesState": False,
        "invocationGuidance": (
            "Retrieve only the stable source anchors for the explicitly identified "
            "organization-scoped review finding; never claim provider verification unless "
            "the returned source is available."
        ),
    },
    {
        "name": "decide_in_house_review_finding",
        "operationIds": ["in-house-review-finding-decision"],
        "requiredInputs": ["finding_id", "decision", "idempotencyKey"],
        "mutatesState": True,
        "invocationGuidance": (
            "Accept, reject, edit, or waive only the explicitly identified finding after "
            "the legal reviewer confirms the decision."
        ),
    },
    {
        "name": "retrieve_in_house_obligations",
        "operationIds": ["in-house-obligation-list"],
        "requiredInputs": [],
        "mutatesState": False,
        "invocationGuidance": (
            "List only the authenticated organization's In-House obligations and their contract-version scope."
        ),
    },
    {
        "name": "retrieve_in_house_approvals",
        "operationIds": ["in-house-approval-list"],
        "requiredInputs": ["contract_id"],
        "mutatesState": False,
        "invocationGuidance": (
            "List approval history only for the explicitly identified organization-scoped In-House contract."
        ),
    },
]

# These are the remaining ordinary chat-proxy capabilities without an exact
# MCP tool or an existing chat-run registry record. Keep them visible in the
# universal map while their established chat-proxy executor remains in place.
# They are catalog records only: do not advertise them as MCP-executable or
# resolve their operation IDs as aliases until contract parity is verified.
_CHAT_PROXY_ONLY_FUNCTION_RECORDS = [
    {
        "functionId": "create_knowledge_record_proposal",
        "name": "create_knowledge_record_proposal",
        "operationIds": ["create-knowledge-record-proposal"],
        "description": (
            "Extract a source-linked legal or company fact from the current authorized "
            "conversation and create a reviewable proposal."
        ),
        "requiredInputs": ["appId", "scopeType", "scopeId", "sources", "idempotencyKey"],
        "mutatesState": True,
        "invocationGuidance": (
            "Creates a pending approval proposal; do not claim the fact was committed. "
            "Include the returned review URL when present."
        ),
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "create_negotiation_memory_proposal",
        "name": "create_negotiation_memory_proposal",
        "operationIds": ["create-negotiation-memory-proposal"],
        "description": (
            "Extract a source-linked negotiation memory from the current authorized "
            "conversation and create a reviewable proposal."
        ),
        "requiredInputs": ["appId", "scopeType", "scopeId", "sources", "idempotencyKey"],
        "mutatesState": True,
        "invocationGuidance": (
            "Creates a pending approval proposal; do not claim the memory was committed. "
            "Include the returned review URL when present."
        ),
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "decide_meeting_suggestion",
        "name": "decide_meeting_suggestion",
        "operationIds": ["decide-meeting-suggestion"],
        "description": (
            "List pending meeting suggestions or act on one by booking, dismissing, "
            "requesting availability, or rescheduling."
        ),
        "requiredInputs": [],
        "mutatesState": True,
        "invocationGuidance": (
            "Use no decision to list. Apply a decision only from the user's explicit "
            "choice; rescheduling requires a concrete future ISO 8601 start time."
        ),
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "edit_company_action_items",
        "name": "edit_company_action_items",
        "operationIds": ["edit-company-action-items", "seriesai-company-action-items"],
        "description": "Explicitly add, assign, update, complete, cancel, or delete Vienna company action items.",
        "requiredInputs": ["caseId", "operation"],
        "mutatesState": True,
        "invocationGuidance": (
            "Use only for an explicit company-task change. For read-only requests, use "
            "the company action-item retrieval operation. Preserve app and company scope."
        ),
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "generate_engagement_letter",
        "name": "generate_engagement_letter",
        "operationIds": ["engagement-letter-signature", "generate_engagement_letter"],
        "description": "Start the asynchronous engagement-letter workflow and signature process.",
        "requiredInputs": [],
        "mutatesState": True,
        "startsAsyncWorkflow": True,
        "sendsUserEmail": True,
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "import_cap_table_file",
        "name": "import_cap_table_file",
        "operationIds": ["import-cap-table-file"],
        "description": (
            "Import a completed cap-table spreadsheet into the company workspace after " "explicit confirmation."
        ),
        "requiredInputs": ["caseId", "fileName", "s3Bucket", "s3Key", "confirm"],
        "mutatesState": True,
        "requiresExplicitConfirmation": True,
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "import_clio_version",
        "name": "import_clio_version",
        "operationIds": ["import-clio-version"],
        "description": "Import one explicitly selected Clio document version into a case.",
        "requiredInputs": ["caseId", "matterId", "documentId", "versionId", "confirm"],
        "mutatesState": True,
        "startsAsyncWorkflow": True,
        "requiresExplicitConfirmation": True,
        "invocationGuidance": (
            "Import only the exact user-selected version after confirmation of both the "
            "version and destination case."
        ),
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "manage_legal_team",
        "name": "manage_legal_team",
        "operationIds": [
            "list-legal-team-members",
            "rename-legal-team",
            "remove-legal-team-member",
            "assign-legal-team-to-case",
            "delete-legal-team",
        ],
        "description": (
            "Manage legal-team membership, naming, deletion, and case assignment for " "admin-authorized users."
        ),
        "mutatesState": True,
        "requiresExplicitConfirmation": True,
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "map_inbox_review_thread",
        "name": "map_inbox_review_thread",
        "operationIds": ["map-inbox-review-thread"],
        "description": "Assign a reviewed inbox thread to a case after explicit confirmation.",
        "requiredInputs": ["caseId", "integrationId", "providerThreadId", "confirm"],
        "mutatesState": True,
        "requiresExplicitConfirmation": True,
        "invocationGuidance": (
            "Identify the exact provider thread and destination case before asking the " "user to confirm the mapping."
        ),
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "playbook_review",
        "name": "playbook_review",
        "operationIds": ["playbook-review"],
        "description": (
            "Evaluate a supplied document against explicit legal playbook positions "
            "and return anchored verdicts and redlines."
        ),
        "requiredInputs": ["caseId", "documentText"],
        "mutatesState": False,
        "invocationGuidance": (
            "Use only when the user supplies or selects a playbook position set; this is "
            "a structured review, not a general critique."
        ),
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
    {
        "functionId": "tabular_review",
        "name": "tabular_review",
        "operationIds": ["tabular-review"],
        "description": (
            "Compare multiple matter documents in a source-grounded table using "
            "requested or document-specific review dimensions."
        ),
        "requiredInputs": ["caseId", "documents", "columns"],
        "mutatesState": False,
        "invocationGuidance": (
            "Use full extracted text for each selected document, preserve source-bound "
            "evidence, and report unavailable sources instead of silently omitting them."
        ),
        "surfaces": ["chat_proxy"],
        "metadataStatus": "chat_proxy_only",
    },
]

_CHAT_RUN_DENIED_TOOLS = frozenset({"remove_user_from_case"})
_CHAT_RUN_EXPLICIT_CONFIRMATION_TOOLS = frozenset(
    {
        "import_clio_document_version",
        "refresh_clio_import",
        "update_clio_matter",
        "update_clio_contact",
        "send_whatsapp_message",
    }
)


_IN_HOUSE_FUNCTION_NAMES = frozenset(tool["name"] for tool in _IN_HOUSE_CHAT_RUN_TOOL_GUIDANCE)
_SERIESAI_FUNCTION_NAMES = frozenset({"get_seriesai_company_details", "get_seriesai_company_cap_table"})
_FUNCTIONS: dict[str, dict[str, Any]] = {}
for _tool in (*_CHAT_RUN_TOOL_GUIDANCE, *_IN_HOUSE_CHAT_RUN_TOOL_GUIDANCE):
    _name = str(_tool["name"])
    if _name in _FUNCTIONS:
        _message = "Duplicate callable function registry name"
        raise ValueError(_message)
    _FUNCTIONS[_name] = {
        "functionId": _name,
        **_tool,
        "categoryId": categorize_function_name(_name),
        **(
            {
                "surfaces": list(
                    dict.fromkeys([*(_tool.get("surfaces") or []), "chat_proxy"])
                )
            }
            if _name in _IN_HOUSE_FUNCTION_NAMES
            else {}
        ),
        "appIds": (
            tuple(_tool.get("appIds", ()))
            if "appIds" in _tool
            else (
                ("5",) if _name in _IN_HOUSE_FUNCTION_NAMES else ("3", "4") if _name in _SERIESAI_FUNCTION_NAMES else ()
            )
        ),
    }

FUNCTION_REGISTRY = MappingProxyType(_FUNCTIONS)

# Explicit caller-name aliases for functions with the same published operation
# ID. Keep this small and evidence-backed: a name match alone does not prove a
# compatible implementation or request contract.
MCP_CALLER_COMPATIBILITY: dict[str, tuple[dict[str, Any], ...]] = {
    "get_seriesai_company_details": (
        {
            "name": "retrieve_company_details",
            "operationId": "retrieve-company-details",
            "surfaces": ["intent_handler", "chat_proxy"],
            "verification": "verified_shared_operation_id",
            # Older MCP images publish organization_id for this company-scoped
            # lookup. That argument is semantically the selected case/company
            # scope and must be filled from trusted case context.
            "trustedScopeBindings": {"organization_id": "caseId"},
            "requiresCaseId": True,
        },
    ),
    "get_seriesai_company_cap_table": (
        {
            "name": "retrieve_cap_table",
            "operationId": "retrieve-cap-table",
            "surfaces": ["intent_handler", "chat_proxy"],
            "verification": "verified_shared_operation_id",
            "trustedScopeBindings": {"organization_id": "caseId"},
            "requiresCaseId": True,
        },
    ),
    "assign_action_item": (
        {
            "name": "assign_case_action_item",
            "operationId": "assign-action-item",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "complete_action_item": (
        {
            "name": "complete_case_action_item",
            "operationId": "complete-action-item",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "create_action_item": (
        {
            "name": "create_case_action_item",
            "operationId": "create-action-item",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "generate_motion": (
        {
            "name": "draft_motion",
            "operationId": "generate-motion",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
        {
            "name": "start_motion_generation",
            "operationId": "generate-motion",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "recommend_lawsuit": (
        {
            "name": "lawsuit_recommendation",
            "operationId": "recommend-lawsuit",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "list_action_items": (
        {
            "name": "retrieve_case_action_items",
            "operationId": "list-action-items",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
        {
            "name": "retrieve_company_action_items",
            "operationId": "retrieve-company-action-items",
            "surfaces": ["intent_handler", "chat_proxy"],
            "verification": "operation_id_match_only",
            "inputSchemasBySurface": {
                "intent_handler": {
                    "type": "object",
                    "properties": {
                        "appId": {"type": "string"},
                        "userId": {"type": "string"},
                        "caseId": {"type": "string"},
                        "actionItemId": {"type": "string"},
                        "openOnly": {"type": "boolean", "default": True},
                        "resultLimit": {"type": "integer", "minimum": 1, "maximum": 100},
                        "includeLifecycleSuggestions": {"type": "boolean", "default": True},
                    },
                    "required": ["appId", "userId", "caseId"],
                }
            },
            "defaultArguments": {"open_only": True, "include_lifecycle_suggestions": True},
            "argumentBindings": {
                "actionItemId": "item_id",
                "openOnly": "open_only",
                "resultLimit": "result_limit",
                "includeLifecycleSuggestions": "include_lifecycle_suggestions",
            },
            "notes": (
                "actionItemId is an optional exact-item filter; resultLimit is capped at 100; openOnly defaults to true in the legacy "
                "caller. Caller userId is authenticated context and is not forwarded as a Django query parameter."
            ),
        },
    ),
    "list_case_important_dates": (
        {
            "name": "retrieve_case_important_dates",
            "operationId": "list-case-important-dates",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
        {
            "name": "retrieve_company_timeline",
            "operationId": "retrieve-company-timeline",
            "surfaces": ["intent_handler", "chat_proxy"],
            "verification": "operation_id_match_only",
            "inputSchemasBySurface": {
                "intent_handler": {
                    "type": "object",
                    "properties": {
                        "appId": {"type": "string"},
                        "userId": {"type": "string"},
                        "caseId": {"type": "string"},
                        "dateFrom": {"type": "string"},
                        "dateTo": {"type": "string"},
                        "lifecycleStage": {"type": "string"},
                        "resultLimit": {"type": "integer", "minimum": 1},
                    },
                    "required": ["appId", "userId", "caseId"],
                }
            },
            "trustedScopeBindings": {"case_id": "caseId", "app_id": "appId"},
            "defaultArguments": {
                "exclude_external_calendar": True,
                "exclude_booking_projections": True,
                "require_displayable": True,
                "result_limit": 10,
                "sort_order": "desc",
            },
            "argumentBindings": {
                "dateFrom": "date_from",
                "dateTo": "date_to",
                "lifecycleStage": "stage",
                "resultLimit": "result_limit",
            },
            "notes": (
                "Candidate contract for the legacy lifecycle timeline read. Django authenticates the caller; "
                "userId is not forwarded as a query parameter. Defaults exclude calendar and booking rows, "
                "require displayable events, and preserve newest-first ordering. The shared endpoint caps "
                "resultLimit at 100 while the legacy handler does not. The alias remains unverified until "
                "that limit difference and lifecycle response plus Slack/email presentation parity are resolved."
            ),
        },
        {
            "name": "retrieve_company_deadlines",
            "operationId": "retrieve-company-deadlines",
            "surfaces": ["intent_handler", "chat_proxy"],
            "verification": "operation_id_match_only",
            "inputSchemasBySurface": {
                "intent_handler": {
                    "type": "object",
                    "properties": {
                        "appId": {"type": "string"},
                        "userId": {"type": "string"},
                        "caseId": {"type": "string"},
                        "query": {"type": "string"},
                        "dateFrom": {"type": "string"},
                        "dateTo": {"type": "string"},
                        "openOnly": {"type": "boolean"},
                        "resultLimit": {"type": "integer", "minimum": 1},
                    },
                    "required": ["appId", "userId", "caseId"],
                }
            },
            "trustedScopeBindings": {"case_id": "caseId", "app_id": "appId"},
            "defaultArguments": {
                "include_legacy_company_deadlines": True,
                "upcoming_only": True,
                "sort_order": "asc",
            },
            "argumentBindings": {
                "dateFrom": "date_from",
                "dateTo": "date_to",
                "openOnly": "open_only",
                "resultLimit": "result_limit",
            },
            "notes": (
                "Candidate contract for the legacy upcoming-deadline read. Django authenticates the caller; "
                "userId is not forwarded as a query parameter. For compliance-only requests, the agent should "
                "set exclude_external_calendar=true; general schedule requests retain calendar events. The "
                "shared endpoint caps resultLimit at 100 while the legacy handler does not, and returns a "
                "generic items list rather than the legacy deadlines/email/Slack summary shape. Keep this alias "
                "unverified until those request and response differences are reconciled."
            ),
        },
    ),
    "list_my_cases": (
        {
            "name": "retrieve_my_cases",
            "operationId": "list-my-cases",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "verified_shared_operation_id",
        },
    ),
    "get_slack_channel_messages": (
        {
            "name": "retrieve_slack_channel_messages",
            "operationId": "get-slack-channel-messages",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "precedent_query": (
        {
            "name": "search_precedents",
            "operationId": "precedent-query",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "demand_letter": (
        {
            "name": "start_demand_letter_generation",
            "operationId": "demand-letter",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "generate_lawsuit": (
        {
            "name": "start_lawsuit_generation",
            "operationId": "generate-lawsuit",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
    "update_action_item": (
        {
            "name": "update_case_action_item",
            "operationId": "update-action-item",
            "surfaces": ["chat_proxy", "intent_handler"],
            "verification": "operation_id_match_only",
        },
    ),
}

# IntentHandler operations with no proven canonical MCP implementation remain
# visible in the universal navigator as inventory records, never tool schemas.
# Keep inputs and operation IDs absent until their request contracts are mapped.
_ADDITIONAL_INTENT_HANDLER_ONLY_FUNCTION_RECORDS = (
    (
        "archive_case_email_draft",
        "communications",
        "Archive one saved email draft on a named case.",
        (),
        ("intent_handler",),
    ),
    (
        "cap_table_issuance_intent",
        "company_operations",
        "Record a proposed share issuance for a Vienna company; approval is required before committing it.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "delete_case_email_message",
        "communications",
        "Remove one named email from a case record.",
        (),
        ("intent_handler",),
    ),
    (
        "delete_case_individual",
        "case_management",
        "Remove a person from a case's list of individuals.",
        (),
        ("intent_handler",),
    ),
    (
        "delete_ingested_email",
        "communications",
        "Delete an email ingested from the authenticated user's connected inbox.",
        (),
        ("intent_handler",),
    ),
    (
        "document_amendment_request",
        "documents_research",
        "Amend an existing Vienna corporate document, such as adding a cofounder or authorizing shares.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "document_generation_request",
        "documents_research",
        "Review an existing Vienna cap-table document workflow; starting financing documents uses a dedicated action.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "draft_followups",
        "communications",
        "Draft unsent email follow-ups from sent-mail history without sending messages or booking meetings.",
        (),
        ("intent_handler",),
    ),
    (
        "general_chatbot",
        "communications",
        "Answer an open-ended legal or product question when no specific operation applies.",
        (),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "help",
        "communications",
        "Explain what Vienna can do or how to use it.",
        (),
        ("intent_handler",),
    ),
    (
        "lawsuit_generation",
        "documents_research",
        "Draft the initial complaint that starts a lawsuit; this is not motion drafting.",
        ("2",),
        ("intent_handler",),
    ),
    (
        "named_case_scope",
        "case_management",
        "Resolve a request to one clearly referenced, authorized case; otherwise retain account scope.",
        (),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "ownership_query",
        "company_operations",
        "Answer who owns what in a Vienna company, including holders and ownership percentages.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "read_case_individual_dossier",
        "case_management",
        "Show one specific person's profile or dossier on a case.",
        (),
        ("intent_handler",),
    ),
    (
        "request_signature",
        "signatures_approvals",
        "Prepare a signature request for a Vienna company document; sending remains approval-gated.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "retrieve_account_email_drafts",
        "communications",
        "List the authenticated user's active, unlinked email-agent drafts for the current app.",
        (),
        ("intent_handler",),
    ),
    (
        "retrieve_case_email_drafts",
        "communications",
        "List saved email drafts on a named case.",
        (),
        ("intent_handler",),
    ),
    (
        "retrieve_case_email_timeline",
        "communications",
        "Show the email timeline recorded on a named case.",
        (),
        ("intent_handler",),
    ),
    (
        "retrieve_case_individuals",
        "case_management",
        "List the people on a case with their names and roles.",
        (),
        ("intent_handler",),
    ),
    (
        "retrieve_company_marketing_automations",
        "company_operations",
        "List a Vienna company's running marketing automations.",
        ("3", "4"),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "retrieve_company_marketing_performance",
        "company_operations",
        "Show a Vienna company's campaign and marketing performance.",
        ("3", "4"),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "retrieve_company_revenue",
        "company_operations",
        "Show a Vienna company's revenue metrics or revenue trend.",
        ("3", "4"),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "retrieve_company_sales",
        "company_operations",
        "Show a Vienna company's sales metrics.",
        ("3", "4"),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "retrieve_company_upcoming_campaigns",
        "company_operations",
        "List scheduled or draft upcoming campaigns for a Vienna company.",
        ("3", "4"),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "retrieve_company_web_analytics",
        "company_operations",
        "Show a Vienna company's website analytics.",
        ("3", "4"),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "retrieve_inbox_review_items",
        "communications",
        "Show inbox messages awaiting manual case assignment or review.",
        (),
        ("intent_handler",),
    ),
    (
        "retrieve_signature_status",
        "signatures_approvals",
        "Show which Vienna signature requests are still waiting on signers.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "round_status_query",
        "company_operations",
        "Answer the status of a Vienna company's current financing round.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "safe_review_approval",
        "signatures_approvals",
        "Approve or reject a drafted SAFE after the user reviews it.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "seriesai_campaign_read_refusal",
        "company_operations",
        "Route a campaign-read refusal to the authenticated company-data path.",
        ("3", "4"),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "signature_approval",
        "signatures_approvals",
        "Approve sending a prepared Vienna signature request.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "start_workflow_gap_plan",
        "contracts_workflows",
        "Plan a workaround when no available operation can complete a concrete request.",
        (),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "strategic_digest",
        "documents_research",
        "Produce a strategic summary digest of a case.",
        (),
        ("intent_handler", "chat_proxy"),
    ),
    (
        "summarize_financing_document",
        "company_operations",
        "Summarize a named Vienna company's latest financing document.",
        ("3", "4"),
        ("intent_handler",),
    ),
    (
        "update_case_email_draft",
        "communications",
        "Edit the subject, body, or recipients of a saved email draft on a case.",
        (),
        ("intent_handler",),
    ),
    (
        "update_case_email_message",
        "communications",
        "Update or retag one email message recorded on a case.",
        (),
        ("intent_handler",),
    ),
    (
        "update_case_individual",
        "case_management",
        "Edit a person's role, counsel, group, or importance on a case.",
        (),
        ("intent_handler",),
    ),
)

_INTENT_HANDLER_ONLY_FUNCTION_RECORDS = (
    {
        "functionId": "add_cap_table_entry",
        "name": "add_cap_table_entry",
        "categoryId": "company_operations",
        "description": "Append a legacy holder or security entry to a company's embedded cap-table records.",
    },
    {
        "functionId": "update_cap_table_entry",
        "name": "update_cap_table_entry",
        "categoryId": "company_operations",
        "description": "Update a legacy company cap-table entry by its existing entry identifier.",
    },
    {
        "functionId": "edit_company_deadlines",
        "name": "edit_company_deadlines",
        "categoryId": "scheduling",
        "description": "Add, update, or delete a legacy company deadline and its associated timeline event.",
    },
    {
        "functionId": "deadline_acknowledgment",
        "name": "deadline_acknowledgment",
        "categoryId": "scheduling",
        "description": "Acknowledge and complete a named company deadline.",
    },
    {
        "functionId": "advance_lifecycle_stage",
        "name": "advance_lifecycle_stage",
        "categoryId": "company_operations",
        "description": "Advance a company's lifecycle stage and record the corresponding event.",
    },
    {
        "functionId": "classify_lifecycle_upload",
        "name": "classify_lifecycle_upload",
        "categoryId": "documents_research",
        "description": "Classify an uploaded company document and record its lifecycle evidence.",
    },
    {
        "functionId": "diligence_room_prepare",
        "name": "diligence_room_prepare",
        "categoryId": "company_operations",
        "description": "Prepare a company or deal diligence room and identify required documents or missing evidence.",
    },
    {
        "functionId": "request_missing_lifecycle_documents",
        "name": "request_missing_lifecycle_documents",
        "categoryId": "documents_research",
        "description": "Identify missing lifecycle documents and create follow-up requests for them.",
    },
    {
        "functionId": "diligence_room_query",
        "name": "diligence_room_query",
        "categoryId": "company_operations",
        "description": "Search the authorized company or deal diligence records.",
    },
    {
        "functionId": "generate_founder_update",
        "name": "generate_founder_update",
        "categoryId": "company_operations",
        "description": "Generate a founder update grounded in company events and diligence status.",
    },
    {
        "functionId": "generate_board_consent",
        "name": "generate_board_consent",
        "categoryId": "company_operations",
        "description": "Generate a board-consent document from the appropriate approved template.",
    },
    {
        "functionId": "generate_safe",
        "name": "generate_safe",
        "categoryId": "company_operations",
        "description": "Generate a SAFE document using the authorized company profile and deal terms.",
    },
    {
        "functionId": "generate_financing_doc",
        "name": "generate_financing_doc",
        "categoryId": "company_operations",
        "description": "Generate a financing document from a supported template and supplied deal terms.",
    },
    *(
        {
            "functionId": name,
            "name": name,
            "categoryId": category_id,
            "description": description,
            "appIds": list(app_ids),
            "surfaces": list(surfaces),
        }
        for name, category_id, description, app_ids, surfaces in _ADDITIONAL_INTENT_HANDLER_ONLY_FUNCTION_RECORDS
    ),
)

_ALIASES: dict[str, list[str]] = {}
for _function_id, _record in FUNCTION_REGISTRY.items():
    for _alias in dict.fromkeys((_function_id, *_record.get("operationIds", ()))):
        _ALIASES.setdefault(str(_alias), []).append(_function_id)
FUNCTION_ALIAS_INDEX = MappingProxyType({alias: tuple(function_ids) for alias, function_ids in _ALIASES.items()})


def resolve_function_id(identifier: Any) -> str:
    """Resolve a canonical ID or compatibility alias, failing on ambiguity."""
    normalized = str(identifier or "").strip()
    if normalized in FUNCTION_REGISTRY:
        return normalized
    matches = FUNCTION_ALIAS_INDEX.get(normalized, ())
    if not matches:
        message = f"Unknown callable function: {normalized or '<empty>'}"
        raise KeyError(message)
    if len(matches) > 1:
        message = f"Ambiguous callable function alias {normalized!r}: {', '.join(matches)}"
        raise ValueError(message)
    return matches[0]


def get_function_record(identifier: Any) -> dict[str, Any]:
    """Return a mutable copy of one record resolved by ID or alias."""
    return deepcopy(FUNCTION_REGISTRY[resolve_function_id(identifier)])


def chat_run_tool_records(app_id: Any = None) -> list[dict[str, Any]]:
    """Return fresh records visible in a chat-run context for the product scope."""
    normalized_app_id = str(app_id or "").strip()
    return [
        {key: value for key, value in deepcopy(record).items() if key != "appIds"}
        for record in FUNCTION_REGISTRY.values()
        if not record["appIds"] or normalized_app_id in record["appIds"]
    ]


def universal_function_records(  # noqa: C901, PLR0912, PLR0915 - explicit merge precedence across supported sources
    mcp_records: list[dict[str, Any]] | None = None,
    *,
    include_chat_run_records: bool = True,
) -> list[dict[str, Any]]:
    """Merge registered MCP schemas with chat-run metadata into one map view.

    MCP implementations remain registered on the MCP server. This projection
    supplies one navigable record per canonical name and records which existing
    caller surfaces can currently expose it.
    """
    merged: dict[str, dict[str, Any]] = {}
    if include_chat_run_records:
        for source_record in FUNCTION_REGISTRY.values():
            record = deepcopy(source_record)
            name = str(record.get("name") or record.get("functionId") or "").strip()
            if not name:
                continue
            declared_surfaces = record.get("surfaces")
            record["surfaces"] = list(
                dict.fromkeys(
                    [
                        *(declared_surfaces if isinstance(declared_surfaces, list) else []),
                        "chat_run",
                    ]
                )
            )
            compatibility = MCP_CALLER_COMPATIBILITY.get(name)
            if compatibility:
                record["callerAliases"] = deepcopy(list(compatibility))
            merged[name] = record

    if include_chat_run_records:
        for source_record in _CHAT_PROXY_ONLY_FUNCTION_RECORDS:
            record = deepcopy(source_record)
            name = str(record.get("name") or record.get("functionId") or "").strip()
            if name and name not in merged:
                record["categoryId"] = categorize_function_name(name)
                record["mcpCallable"] = False
                merged[name] = record

        for source_record in _INTENT_HANDLER_ONLY_FUNCTION_RECORDS:
            record = deepcopy(dict(source_record))
            name = str(record.get("name") or record.get("functionId") or "").strip()
            if name and name not in merged:
                record.update(
                    {
                        "categoryId": record["categoryId"],
                        "appIds": list(record.get("appIds", ["3", "4"])),
                        "surfaces": list(record.get("surfaces", ["intent_handler"])),
                        "metadataStatus": "legacy_caller_only",
                        "mcpCallable": False,
                        "invocationGuidance": (
                            "Migration inventory record only. No MCP schema or canonical executor is registered; "
                            "this record cannot be invoked through the universal MCP endpoint."
                        ),
                    }
                )
                merged[name] = record

    for source_record in mcp_records or []:
        record = deepcopy(dict(source_record))
        name = str(record.get("name") or record.get("functionId") or "").strip()
        if not name:
            continue
        compatibility = MCP_CALLER_COMPATIBILITY.get(name)
        if compatibility:
            record["callerAliases"] = deepcopy(list(compatibility))
        existing = merged.get(name)
        if existing is None:
            # An allowlisted map query omits unrelated chat-run records, but a
            # matching canonical record still owns the function's app scope,
            # operation IDs, and caller metadata.
            source_record = FUNCTION_REGISTRY.get(name)
            if source_record is not None:
                existing = deepcopy(source_record)
                existing["surfaces"] = list(
                    dict.fromkeys([*(existing.get("surfaces") or []), "chat_run"])
                )
                merged[name] = existing
        if existing is None:
            schema = record.get("inputSchema")
            required = schema.get("required", []) if isinstance(schema, dict) else []
            record.update(
                {
                    "functionId": name,
                    "categoryId": categorize_function_name(name),
                    "operationIds": [name],
                    "requiredInputs": list(required) if isinstance(required, list) else [],
                    "surfaces": ["mcp"],
                    "metadataStatus": "mcp_schema_only",
                    "mcpCallable": True,
                }
            )
            if isinstance(schema, dict):
                record["inputSchemasBySurface"] = {"mcp": deepcopy(schema)}
            merged[name] = record
            continue
        schema = record.get("inputSchema")
        if isinstance(schema, dict):
            existing["inputSchema"] = deepcopy(schema)
            schemas_by_surface = existing.setdefault("inputSchemasBySurface", {})
            if isinstance(schemas_by_surface, dict):
                schemas_by_surface["mcp"] = deepcopy(schema)
        existing["mcpDescription"] = record.get("description")
        if compatibility:
            existing["callerAliases"] = deepcopy(list(compatibility))
        if not existing.get("description"):
            existing["description"] = record.get("description")
        existing["surfaces"] = list(dict.fromkeys([*(existing.get("surfaces") or []), "mcp"]))
        existing["metadataStatus"] = "shared_metadata_and_mcp_schema"
        existing["mcpCallable"] = True

    return [merged[name] for name in sorted(merged)]


def resolve_universal_function_id(identifier: Any, records: list[dict[str, Any]]) -> str:  # noqa: C901
    """Resolve a function, operation ID, or verified caller alias in a universal map view."""

    normalized = str(identifier or "").strip()
    aliases: dict[str, set[str]] = {}
    for record in records:
        canonical_id = str(record.get("functionId") or record.get("name") or "").strip()
        if not canonical_id:
            continue
        identifiers = [canonical_id, str(record.get("name") or "").strip()]
        operation_ids = record.get("operationIds")
        if isinstance(operation_ids, list):
            identifiers.extend(str(value).strip() for value in operation_ids)
        for value in identifiers:
            if value:
                aliases.setdefault(value, set()).add(canonical_id)
        caller_aliases = record.get("callerAliases")
        if isinstance(caller_aliases, list):
            for alias in caller_aliases:
                if not isinstance(alias, dict) or alias.get("verification") != "verified_shared_operation_id":
                    continue
                alias_name = str(alias.get("name") or "").strip()
                if alias_name:
                    aliases.setdefault(alias_name, set()).add(canonical_id)

    matches = aliases.get(normalized, set())
    if not matches:
        message = f"Unknown callable function: {normalized or '<empty>'}"
        raise KeyError(message)
    if len(matches) > 1:
        message = f"Ambiguous callable function alias {normalized!r}: {', '.join(sorted(matches))}"
        raise ValueError(message)
    return next(iter(matches))


def get_universal_function_record(identifier: Any, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a copy of a universal map record resolved by canonical name or verified alias."""
    canonical_id = resolve_universal_function_id(identifier, records)
    for record in records:
        if str(record.get("functionId") or record.get("name") or "").strip() == canonical_id:
            return deepcopy(record)
    message = f"Unknown callable function: {canonical_id}"
    raise KeyError(message)


def hierarchical_function_index(
    records: list[dict[str, Any]] | None = None,
    *,
    app_id: Any = None,
    category_id: str | None = None,
    subcategory_id: str | None = None,
) -> dict[str, Any]:
    """Return the index, a category's records, or one category subgroup."""
    visible_records = records if records is not None else chat_run_tool_records(app_id)
    result = browse_function_map(visible_records, category_id, subcategory_id)
    result["scope"] = str(app_id or "").strip() or "shared"
    return result
