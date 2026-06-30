from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository, mini_apps_service, sage_memory_service


RETENTION_CLASSES: Dict[str, Dict[str, Any]] = {
    "ephemeral": {
        "ttl_days": 1,
        "description": "Short-lived operational traces used for immediate runtime continuity.",
    },
    "active_conversation": {
        "ttl_days": 30,
        "description": "Recent customer/session interaction state required for active support continuity.",
    },
    "compact_summary": {
        "ttl_days": 90,
        "description": "Compacted summaries replacing raw long conversations.",
    },
    "business_aggregate": {
        "ttl_days": 365,
        "description": "Aggregate business patterns and insights without customer identity payloads.",
    },
    "audit_record": {
        "ttl_days": 3650,
        "description": "Security, policy, and governance audit records retained for investigation and compliance.",
    },
}


DATA_STORE_CATALOG: List[Dict[str, Any]] = [
    {
        "store_id": "deployed_agent_conversation_memory",
        "retention_class": "compact_summary",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "deployed_agent_daily_message_usage",
        "retention_class": "active_conversation",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "channel_user_acquisition_touches",
        "retention_class": "business_aggregate",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "deployed_agent_business_insights",
        "retention_class": "business_aggregate",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "external_user_privacy_requests",
        "retention_class": "audit_record",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "external_user_privacy_delete_audits",
        "retention_class": "audit_record",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "agent_channel_events",
        "retention_class": "active_conversation",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "activity_ledger_events",
        "retention_class": "audit_record",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "mini_apps_state",
        "retention_class": "active_conversation",
        "supports_export": True,
        "supports_delete": True,
    },
    {
        "store_id": "sage_memory",
        "retention_class": "compact_summary",
        "supports_export": True,
        "supports_delete": True,
    },
]


_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_LONG_DIGIT_RE = re.compile(r"\b\d{5,}\b")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SECRET_TOKEN_RE = re.compile(r"\b(?:sk-[A-Za-z0-9._-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,})\b", re.IGNORECASE)
_NAME_PATTERN_RE = re.compile(
    r"\b(?:my name is|i am|i'm|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b"
)


def retention_classes_catalog() -> Dict[str, Dict[str, Any]]:
    return {key: dict(value) for key, value in RETENTION_CLASSES.items()}


def data_store_catalog() -> List[Dict[str, Any]]:
    return [dict(item) for item in DATA_STORE_CATALOG]


def _sanitize_insight_text(value: Any) -> str:
    text = str(value or "").strip()
    text = _EMAIL_RE.sub("[email]", text)
    text = _PHONE_RE.sub("[phone]", text)
    text = _LONG_DIGIT_RE.sub("[number]", text)
    text = _SECRET_TOKEN_RE.sub("[redacted-secret]", text)
    text = _NAME_PATTERN_RE.sub(lambda match: match.group(0).split()[0] + " [name]", text)
    if len(text) > 200:
        return text[:199].rstrip() + "…"
    return text


def _sanitize_business_insight_record(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(item or {})
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    forbidden_metadata_keys = {
        "external_user_id",
        "session_key",
        "session_id",
        "phone",
        "phone_number",
        "customer_name",
        "name",
        "email",
        "token",
        "authorization",
    }
    for key in list(metadata.keys()):
        if str(key).strip().lower() in forbidden_metadata_keys:
            metadata.pop(key, None)
    payload["metadata"] = metadata
    examples = []
    for raw in list(payload.get("redacted_examples") or []):
        sanitized = _sanitize_insight_text(raw)
        if sanitized:
            examples.append(sanitized)
    payload["redacted_examples"] = examples[:5]
    return payload


async def build_workspace_retention_inventory(
    *,
    tenant_id: str,
    workspace_id: str,
) -> Dict[str, Any]:
    sql_inventory = await control_plane_repository.build_workspace_data_retention_inventory(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    sql_store_counts = dict(sql_inventory.get("stores") or {})
    mini_apps_export = mini_apps_service.export_mini_apps_state(workspace_id)
    sage_export = sage_memory_service.export_sage_memory(workspace_id=workspace_id)
    stores: List[Dict[str, Any]] = []
    for store in DATA_STORE_CATALOG:
        store_id = str(store.get("store_id") or "").strip()
        if store_id == "mini_apps_state":
            row_count = int(mini_apps_export.get("total_records") or 0)
        elif store_id == "sage_memory":
            row_count = int((sage_export.get("summary") or {}).get("total_count") or 0)
        else:
            row_count = int(sql_store_counts.get(f"{store_id}_count") or 0)
        stores.append(
            {
                **dict(store),
                "row_count": row_count,
                "retention_policy": dict(RETENTION_CLASSES.get(str(store.get("retention_class") or ""), {})),
            }
        )
    return {
        "tenant_id": str(sql_inventory.get("tenant_id") or tenant_id),
        "workspace_id": str(sql_inventory.get("workspace_id") or workspace_id),
        "retention_classes": retention_classes_catalog(),
        "stores": stores,
    }


async def export_workspace_data(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: Optional[str] = None,
    include_business_insights: bool = True,
) -> Dict[str, Any]:
    inventory = await build_workspace_retention_inventory(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    payload: Dict[str, Any] = {
        "export_type": "workspace_data_retention_export",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "inventory": inventory,
        "sage_memory": sage_memory_service.export_sage_memory(workspace_id=workspace_id),
        "mini_apps": mini_apps_service.export_mini_apps_state(workspace_id),
    }
    if include_business_insights and deployed_agent_id:
        items = await control_plane_repository.list_deployed_agent_business_insights(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            status=None,
            limit=200,
            offset=0,
        )
        payload["business_insights"] = {
            "deployed_agent_id": deployed_agent_id,
            "count": len(items),
            "items": [_sanitize_business_insight_record(item) for item in items if isinstance(item, dict)],
        }
    return payload


async def purge_deployed_agent_external_customer_data(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
) -> Dict[str, int]:
    return await control_plane_repository.delete_deployed_agent_external_user_data(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
        channel_key=channel_key,
        external_user_id=external_user_id,
    )


async def purge_deployed_agent_scope_data(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
) -> Dict[str, int]:
    return await control_plane_repository.delete_deployed_agent_scope_data(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent_id=deployed_agent_id,
    )


async def purge_workspace_scope_data(
    *,
    tenant_id: str,
    workspace_id: str,
    confirm: bool,
    wipe_sage_memory_data: bool = True,
    wipe_mini_apps_data: bool = True,
) -> Dict[str, Any]:
    if not bool(confirm):
        raise ValueError("confirm=True is required for workspace data purge.")
    deleted_counts = await control_plane_repository.delete_workspace_scope_data(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    payload: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "deleted_counts": dict(deleted_counts),
    }
    if wipe_sage_memory_data:
        payload["sage_memory_wipe"] = sage_memory_service.wipe_sage_memory(
            workspace_id=workspace_id,
            confirm=sage_memory_service.SAGE_MEMORY_WIPE_CONFIRMATION,
        )
    if wipe_mini_apps_data:
        payload["mini_apps_wipe"] = mini_apps_service.wipe_mini_apps_state(workspace_id)
    return payload
