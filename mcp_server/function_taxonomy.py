"""Shared, practice-neutral categories for browsing callable functions."""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

FUNCTION_CATEGORIES: tuple[dict[str, str], ...] = (
    {
        "id": "case_management",
        "label": "Cases, matters, people, and tasks",
        "description": "Find and manage matters, case records, participants, and assigned work.",
    },
    {
        "id": "documents_research",
        "label": "Documents, drafting, and legal research",
        "description": "Find, summarize, review, generate, and research legal or company documents.",
    },
    {
        "id": "contracts_workflows",
        "label": "Contracts, requests, and obligations",
        "description": "Manage contract review, requests, approvals, and ongoing obligations.",
    },
    {
        "id": "communications",
        "label": "Email, messaging, and collaboration",
        "description": "Search and manage messages, email, collaboration channels, and meeting notes.",
    },
    {
        "id": "scheduling",
        "label": "Scheduling, calendars, and deadlines",
        "description": "Find availability and manage bookings, events, calendars, and deadlines.",
    },
    {
        "id": "signatures_approvals",
        "label": "Signatures and approval decisions",
        "description": "Create, review, resolve, and track signature or approval workflows.",
    },
    {
        "id": "company_operations",
        "label": "Company, equity, and business operations",
        "description": "Manage company records, cap tables, financing, sales, and business analytics.",
    },
    {
        "id": "billing_finance",
        "label": "Billing and financial records",
        "description": "Review invoices, billing summaries, and financial account records.",
    },
    {
        "id": "integrations_admin",
        "label": "Connected systems and organization access",
        "description": "Manage connected providers, organization settings, and access records.",
    },
)

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "scheduling",
        re.compile(
            r"schedule|calendar|meeting|deadline|booking|slot|availability|event_type|reservation|conferencing|scheduling_link",
            re.I,
        ),
    ),
    (
        "signatures_approvals",
        re.compile(r"signature|signer|docusign|envelope|recipient|approval|pending_action|self_sign", re.I),
    ),
    (
        "communications",
        re.compile(r"email|message|communication|slack|whatsapp|teams|otter|outbound|inbox|thread", re.I),
    ),
    (
        "company_operations",
        re.compile(
            r"cap_table|stock_plan|issuance|equity|financing|company|series_ai|seriesai|revenue|sales|campaign|marketing|analytics|traction|safe|pitch_deck|onboarding",
            re.I,
        ),
    ),
    (
        "contracts_workflows",
        re.compile(r"contract|obligation|counterparty|in_house|playbook|review_finding|negotiation|engagement", re.I),
    ),
    (
        "documents_research",
        re.compile(
            r"document|template|precedent|motion|filing|lawsuit|demand_letter|research|knowledge|summary|excerpt|artifact|upload|draft|adversarial|rag|query_processor|processor|critique|tabular_review",
            re.I,
        ),
    ),
    ("billing_finance", re.compile(r"billing|invoice|payment|subscription|financial|bill|firm_hours", re.I)),
    (
        "integrations_admin",
        re.compile(
            r"connector|connection|integration|clio|imanage|sharepoint|relativity|epona|organization|member|account|permission|access|user|audit",
            re.I,
        ),
    ),
    (
        "case_management",
        re.compile(r"case|matter|docket|court|people|person|party|action_item|procedural_posture|team", re.I),
    ),
)
_CATEGORY_BY_ID = {category["id"]: category for category in FUNCTION_CATEGORIES}

_FUNCTION_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "find_read",
        "label": "Find and read",
        "description": "Search, list, retrieve, inspect, or preview existing information.",
        "pattern": re.compile(
            r"^(get|list|search|find|retrieve|read|fetch|load|query|view|inspect|preview|check|describe|lookup|show)_",
            re.I,
        ),
    },
    {
        "id": "create_update",
        "label": "Create and update",
        "description": "Create, edit, assign, send, or remove records and settings.",
        "pattern": re.compile(
            r"^(create|add|update|edit|set|save|delete|remove|assign|send|submit|upload|bulk|archive|restore|connect|disconnect|invite|revoke|accept|decline)_",
            re.I,
        ),
    },
    {
        "id": "analyze_generate",
        "label": "Analyze and generate",
        "description": "Analyze, summarize, compare, draft, or generate a result.",
        "pattern": re.compile(
            r"^(analy[sz]e|summari[sz]e|compare|draft|generate|extract|score|recommend|calculate|classify|review|process)_",
            re.I,
        ),
    },
)


def categorize_function_name(name: Any) -> str:
    """Return one primary capability category or fail for an unmapped function."""
    normalized = str(name or "").strip()
    for category_id, pattern in _CATEGORY_PATTERNS:
        if pattern.search(normalized):
            return category_id
    msg = f"Callable function has no category: {normalized or '<empty>'}"
    raise ValueError(msg)


def group_function_name(name: Any) -> str:
    """Return a navigation group within a capability category."""
    normalized = str(name or "").strip()
    for group in _FUNCTION_GROUPS:
        if group["pattern"].search(normalized):
            return str(group["id"])
    return "other_workflows"


def browse_function_map(
    records: Iterable[Mapping[str, Any]],
    category_id: str | None = None,
    subcategory_id: str | None = None,
) -> dict[str, Any]:
    """Return a hierarchical index, a category's functions, or one subgroup's functions."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    subgrouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for source_record in records:
        record = deepcopy(dict(source_record))
        name = str(record.get("name") or record.get("functionId") or "").strip()
        if not name:
            continue
        category = str(record.get("categoryId") or categorize_function_name(name))
        if category not in _CATEGORY_BY_ID:
            msg = f"Unknown callable function category: {category}"
            raise ValueError(msg)
        record["categoryId"] = category
        grouped[category].append(record)
        group = group_function_name(name)
        record["subcategoryId"] = group
        subgrouped[(category, group)].append(record)

    if category_id is not None:
        normalized_category = str(category_id).strip()
        category = _CATEGORY_BY_ID.get(normalized_category)
        if category is None:
            msg = f"Unknown callable function category: {normalized_category or '<empty>'}"
            raise KeyError(msg)
        if subcategory_id is not None:
            normalized_subcategory = str(subcategory_id).strip()
            valid_ids = {str(group["id"]) for group in _FUNCTION_GROUPS} | {"other_workflows"}
            if normalized_subcategory not in valid_ids:
                msg = f"Unknown callable function subcategory: {normalized_subcategory or '<empty>'}"
                raise KeyError(msg)
            return {
                "version": 1,
                "category": deepcopy(category),
                "subcategory": _subcategory_metadata(normalized_subcategory),
                "functions": subgrouped.get((normalized_category, normalized_subcategory), []),
            }
        return {
            "version": 1,
            "category": {
                **deepcopy(category),
                "subcategories": _category_subcategories(normalized_category, subgrouped),
            },
            "functions": grouped.get(normalized_category, []),
        }

    return {
        "version": 1,
        "categories": [
            {
                **category,
                "count": len(grouped[category["id"]]),
                "subcategories": _category_subcategories(category["id"], subgrouped),
            }
            for category in FUNCTION_CATEGORIES
            if grouped[category["id"]]
        ],
    }


def _subcategory_metadata(subcategory_id: str) -> dict[str, str]:
    for group in _FUNCTION_GROUPS:
        if group["id"] == subcategory_id:
            return {key: str(group[key]) for key in ("id", "label", "description")}
    return {
        "id": "other_workflows",
        "label": "Other workflows",
        "description": "Complete or specialized workflows that do not fit the other groups.",
    }


def _category_subcategories(
    category_id: str,
    subgrouped: Mapping[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    ids = [str(group["id"]) for group in _FUNCTION_GROUPS] + ["other_workflows"]
    return [
        {**_subcategory_metadata(group_id), "count": len(subgrouped[(category_id, group_id)])}
        for group_id in ids
        if subgrouped[(category_id, group_id)]
    ]
