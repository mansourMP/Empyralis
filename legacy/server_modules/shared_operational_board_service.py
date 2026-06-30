from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import activity_ledger_service
from server_modules import memory_service
from server_modules import rust_runtime_kernel_client


BOARD_ENTRY_KINDS = {
    "shared_instruction",
    "sop_playbook",
    "handoff_note",
    "shared_artifact_link",
}
BOARD_PERMISSION_TIERS = {
    "read": 1,
    "propose_update": 2,
    "publish_update": 3,
}
BOARD_ACTION_STATUS = {
    "propose_update": "proposed",
    "publish_update": "published",
}
FORBIDDEN_SHARED_BOARD_KEYS = {
    "captain_context",
    "captain_identity",
    "captain_memory",
    "captain_profile",
    "private_context",
    "private_memory",
    "raw_content",
    "raw_memory",
    "session_transcript",
    "specialist_context",
    "specialist_memory",
    "transcript",
    "unified_memory",
    "workspace_memory_snapshot",
}


def _normalize_workspace_id(workspace_id: str) -> str:
    return str(workspace_id or "default").strip() or "default"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> List[Any]:
    return list(value or []) if isinstance(value, list) else []


def _enforce_shared_board_state_decision(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    record: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        payload_bytes = len(json.dumps(record, sort_keys=True).encode("utf-8"))
    except Exception:
        payload_bytes = 0
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-state-store-decision",
            {
                "operation": "write_shared_operational_board_entry",
                "state_class": "shared_operational_board",
                "storage_engine": "durable_postgres",
                "tenant_id": str(tenant_id or "").strip() or _normalize_workspace_id(workspace_id),
                "workspace_id": _normalize_workspace_id(workspace_id),
                "actor_id": str(actor_id or "").strip() or "shared-board",
                "status": str(record.get("status") or "active").strip() or "active",
                "payload": record,
                "payload_bytes": payload_bytes,
                "workspace_access": True,
                "owner_access": True,
                "destructive_approval": True,
            },
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        result = getattr(exc, "result", None)
        if not isinstance(result, dict):
            result = {}
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_runtime_state_store_denied",
                "operation": result.get("operation") or "write_shared_operational_board_entry",
                "reason": result.get("reason") or str(exc),
            },
        ) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "write_shared_operational_board_entry":
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_runtime_state_store_invalid_next_action",
                "operation": "write_shared_operational_board_entry",
                "reason": (
                    "Rust runtime state store returned unexpected next_action for "
                    f"shared operational board write: {next_action or 'missing'}"
                ),
            },
        )
    return decision


def normalize_board_permission(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in BOARD_PERMISSION_TIERS:
        raise HTTPException(status_code=400, detail="board_permission must be read, propose_update, or publish_update.")
    return token


def _permission_allows(granted: str, required: str) -> bool:
    return BOARD_PERMISSION_TIERS[normalize_board_permission(granted)] >= BOARD_PERMISSION_TIERS[normalize_board_permission(required)]


def _require_permission(granted: str, required: str) -> None:
    if _permission_allows(granted, required):
        return
    raise HTTPException(status_code=403, detail=f"shared board action requires {required} permission.")


def normalize_board_entry_kind(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in BOARD_ENTRY_KINDS:
        raise HTTPException(
            status_code=400,
            detail="entry_kind must be shared_instruction, sop_playbook, handoff_note, or shared_artifact_link.",
        )
    return token


def _assert_no_forbidden_keys(value: Any, *, path: str = "shared_board") -> None:
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            token = str(raw_key or "").strip().lower()
            if token in FORBIDDEN_SHARED_BOARD_KEYS:
                raise HTTPException(status_code=400, detail=f"{path}.{raw_key} is not allowed in the shared operational board.")
            _assert_no_forbidden_keys(raw_value, path=f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_forbidden_keys(item, path=f"{path}[{index}]")


def _normalize_links(value: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in _coerce_list(value):
        payload = _coerce_dict(item)
        url = str(payload.get("url") or payload.get("href") or "").strip()
        label = str(payload.get("label") or payload.get("title") or url).strip()
        if not url:
            continue
        record = {
            "label": label or url,
            "url": url,
        }
        if str(payload.get("note") or "").strip():
            record["note"] = str(payload.get("note") or "").strip()
        items.append(record)
    return items


def _normalize_artifacts(value: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in _coerce_list(value):
        payload = _coerce_dict(item)
        artifact_id = str(payload.get("artifact_id") or payload.get("id") or "").strip()
        uri = str(payload.get("uri") or payload.get("path") or "").strip()
        label = str(payload.get("label") or payload.get("name") or artifact_id or uri).strip()
        if not (artifact_id or uri):
            continue
        record = {
            "label": label or "Artifact",
        }
        if artifact_id:
            record["artifact_id"] = artifact_id
        if uri:
            record["uri"] = uri
        items.append(record)
    return items


def _normalize_metadata(value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    _assert_no_forbidden_keys(payload, path="shared_board.metadata")
    normalized: Dict[str, Any] = {}
    for key, raw_value in payload.items():
        token = str(key or "").strip()
        if not token:
            continue
        if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            normalized[token] = raw_value
        elif isinstance(raw_value, list):
            normalized[token] = [
                item
                for item in raw_value
                if isinstance(item, (str, int, float, bool)) or item is None
            ]
        elif isinstance(raw_value, dict):
            normalized[token] = {
                str(child_key): child_value
                for child_key, child_value in raw_value.items()
                if isinstance(child_value, (str, int, float, bool)) or child_value is None
            }
    return normalized


def _normalize_entry_payload(
    *,
    entry_kind: Any,
    title: Any,
    body: Any,
    links: Any,
    artifacts: Any,
    metadata: Any,
) -> Dict[str, Any]:
    normalized_kind = normalize_board_entry_kind(entry_kind)
    normalized_title = str(title or "").strip()
    normalized_body = str(body or "").strip()
    normalized_links = _normalize_links(links)
    normalized_artifacts = _normalize_artifacts(artifacts)
    normalized_metadata = _normalize_metadata(metadata)
    _assert_no_forbidden_keys(
        {
            "title": normalized_title,
            "body": normalized_body,
            "links": normalized_links,
            "artifacts": normalized_artifacts,
            "metadata": normalized_metadata,
        }
    )
    if not normalized_title:
        raise HTTPException(status_code=400, detail="shared board title is required.")
    if not (normalized_body or normalized_links or normalized_artifacts):
        raise HTTPException(status_code=400, detail="shared board entry requires body, links, or artifacts.")
    return {
        "entry_kind": normalized_kind,
        "title": normalized_title,
        "body": normalized_body or None,
        "links": normalized_links,
        "artifacts": normalized_artifacts,
        "metadata": normalized_metadata,
    }


def _board_dir(workspace_id: str) -> Path:
    return memory_service.workspace_sidecar_dir(_normalize_workspace_id(workspace_id), "shared_operational_board")


def _board_log_path(workspace_id: str) -> Path:
    return _board_dir(workspace_id) / "revisions.jsonl"


def _read_board_records(workspace_id: str) -> List[Dict[str, Any]]:
    path = _board_log_path(workspace_id)
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines:
        if not str(line or "").strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(dict(payload))
    return items


def _revision_sort_key(item: Dict[str, Any]) -> tuple[str, int]:
    return (
        str(item.get("created_at") or ""),
        int(item.get("revision_number") or 0),
    )


def _project_board_entry(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "entry_id": str(record.get("entry_id") or "").strip() or None,
        "revision_id": str(record.get("revision_id") or "").strip() or None,
        "revision_number": int(record.get("revision_number") or 0),
        "status": str(record.get("status") or "").strip() or None,
        "entry_kind": str(record.get("entry_kind") or "").strip() or None,
        "title": str(record.get("title") or "").strip() or None,
        "body": str(record.get("body") or "").strip() or None,
        "links": _coerce_list(record.get("links")),
        "artifacts": _coerce_list(record.get("artifacts")),
        "metadata": _coerce_dict(record.get("metadata")),
        "created_at": str(record.get("created_at") or "").strip() or None,
        "published_at": str(record.get("published_at") or "").strip() or None,
        "author": _coerce_dict(record.get("author")),
        "permission_tier": str(record.get("permission_tier") or "").strip() or None,
    }


def list_shared_operational_board_entries(
    workspace_id: str,
    *,
    actor_permission: str = "read",
    include_proposed: bool = False,
    entry_kind: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    permission = normalize_board_permission(actor_permission)
    if include_proposed:
        _require_permission(permission, "propose_update")
    kind_filter = normalize_board_entry_kind(entry_kind) if entry_kind is not None else None
    published_by_entry: Dict[str, Dict[str, Any]] = {}
    latest_by_entry: Dict[str, Dict[str, Any]] = {}
    for record in _read_board_records(workspace_id):
        if kind_filter and str(record.get("entry_kind") or "").strip() != kind_filter:
            continue
        entry_id = str(record.get("entry_id") or "").strip()
        if not entry_id:
            continue
        current_latest = latest_by_entry.get(entry_id)
        if current_latest is None or _revision_sort_key(record) > _revision_sort_key(current_latest):
            latest_by_entry[entry_id] = record
        if str(record.get("status") or "").strip() == "published":
            current_published = published_by_entry.get(entry_id)
            if current_published is None or _revision_sort_key(record) > _revision_sort_key(current_published):
                published_by_entry[entry_id] = record
    source = latest_by_entry if include_proposed else published_by_entry
    items = [_project_board_entry(record) for record in source.values()]
    items.sort(key=lambda item: (str(item.get("created_at") or ""), int(item.get("revision_number") or 0)), reverse=True)
    safe_limit = max(1, min(int(limit or 50), 200))
    return items[:safe_limit]


def get_shared_operational_board_history(
    workspace_id: str,
    entry_id: str,
    *,
    actor_permission: str = "read",
    include_proposed: bool = False,
) -> List[Dict[str, Any]]:
    permission = normalize_board_permission(actor_permission)
    if include_proposed:
        _require_permission(permission, "propose_update")
    token = str(entry_id or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="entry_id is required.")
    items: List[Dict[str, Any]] = []
    for record in _read_board_records(workspace_id):
        if str(record.get("entry_id") or "").strip() != token:
            continue
        if not include_proposed and str(record.get("status") or "").strip() != "published":
            continue
        items.append(_project_board_entry(record))
    items.sort(key=lambda item: (str(item.get("created_at") or ""), int(item.get("revision_number") or 0)), reverse=True)
    return items


async def write_shared_operational_board_entry(
    workspace_id: str,
    *,
    tenant_id: str,
    actor_permission: str,
    action: str,
    actor: Optional[Dict[str, Any]] = None,
    entry_id: Optional[str] = None,
    entry_kind: str,
    title: str,
    body: Optional[str] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    permission = normalize_board_permission(actor_permission)
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in BOARD_ACTION_STATUS:
        raise HTTPException(status_code=400, detail="action must be propose_update or publish_update.")
    _require_permission(permission, normalized_action)
    payload = _normalize_entry_payload(
        entry_kind=entry_kind,
        title=title,
        body=body,
        links=links,
        artifacts=artifacts,
        metadata=metadata,
    )
    existing_records = _read_board_records(workspace_id)
    normalized_entry_id = str(entry_id or "").strip()
    if not normalized_entry_id:
        normalized_entry_id = "sboard_" + hashlib.sha1(
            json.dumps(
                {
                    "workspace_id": _normalize_workspace_id(workspace_id),
                    "title": payload["title"],
                    "entry_kind": payload["entry_kind"],
                    "ts": _utc_now_iso(),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
    entry_records = [
        record
        for record in existing_records
        if str(record.get("entry_id") or "").strip() == normalized_entry_id
    ]
    current_kind = str(entry_records[-1].get("entry_kind") or "").strip() if entry_records else ""
    if current_kind and current_kind != payload["entry_kind"]:
        raise HTTPException(status_code=409, detail="entry_kind cannot change for an existing shared board entry.")
    revision_number = max((int(record.get("revision_number") or 0) for record in entry_records), default=0) + 1
    created_at = _utc_now_iso()
    revision_id = "sboardrev_" + hashlib.sha1(
        json.dumps(
            {
                "workspace_id": _normalize_workspace_id(workspace_id),
                "entry_id": normalized_entry_id,
                "revision_number": revision_number,
                "created_at": created_at,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    record = {
        "entry_id": normalized_entry_id,
        "revision_id": revision_id,
        "revision_number": revision_number,
        "status": BOARD_ACTION_STATUS[normalized_action],
        "entry_kind": payload["entry_kind"],
        "title": payload["title"],
        "body": payload["body"],
        "links": payload["links"],
        "artifacts": payload["artifacts"],
        "metadata": payload["metadata"],
        "created_at": created_at,
        "published_at": created_at if normalized_action == "publish_update" else None,
        "permission_tier": permission,
        "author": {
            "type": str(_coerce_dict(actor).get("type") or "user").strip() or "user",
            "id": str(_coerce_dict(actor).get("id") or "").strip() or None,
            "display_name": str(_coerce_dict(actor).get("display_name") or "").strip() or None,
            "role": str(_coerce_dict(actor).get("role") or "").strip() or None,
        },
    }
    _enforce_shared_board_state_decision(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=str(record["author"].get("id") or record["author"].get("display_name") or "shared-board").strip(),
        record=record,
    )
    path = _board_log_path(workspace_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")

    activity_event = None
    try:
        activity_event = await activity_ledger_service.append_activity_event(
            tenant_id=str(tenant_id or "").strip(),
            workspace_id=_normalize_workspace_id(workspace_id),
            actor_type=str(record["author"].get("type") or "user").strip() or "user",
            actor_id=str(record["author"].get("id") or record["author"].get("display_name") or "shared-board").strip() or "shared-board",
            event_class="memory_update",
            detail_level="timeline_detail",
            action=f"shared_board_{normalized_action}",
            channel="shared_operational_board",
            direction="system",
            title=payload["title"],
            summary=str(payload.get("body") or f"{payload['entry_kind']} {normalized_action}").strip()[:320],
            status=record["status"],
            payload={
                "entry_id": normalized_entry_id,
                "revision_id": revision_id,
                "entry_kind": payload["entry_kind"],
                "board_action": normalized_action,
            },
            metadata={
                "entry_id": normalized_entry_id,
                "revision_id": revision_id,
                "entry_kind": payload["entry_kind"],
                "permission_tier": permission,
                "board_action": normalized_action,
                "version": revision_number,
            },
            artifacts=payload["artifacts"],
        )
    except Exception:
        activity_event = None

    current = list_shared_operational_board_entries(
        workspace_id,
        actor_permission=permission,
        include_proposed=True,
        limit=200,
    )
    current_entry = next((item for item in current if item.get("entry_id") == normalized_entry_id), _project_board_entry(record))
    return {
        "entry": current_entry,
        "revision": _project_board_entry(record),
        "activity_event_id": str(_coerce_dict(activity_event).get("id") or "").strip() or None,
    }
