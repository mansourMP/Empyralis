from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import rust_runtime_kernel_client
from server_modules import workspace_context


SAGE_MEMORY_CATEGORY_DEFINITIONS: dict[str, dict[str, str]] = {
    "safe_general": {
        "label": "Safe",
        "description": "Safe, general context Sage can use freely.",
    },
    "sensitive": {
        "label": "Sensitive",
        "description": "Sensitive work context Sage should handle carefully.",
    },
    "private": {
        "label": "Private",
        "description": "Private personal context with tighter handling.",
    },
    "critical_restricted": {
        "label": "Critical",
        "description": "Restricted facts such as secrets, credentials, or critical data.",
    },
}

SAGE_MEMORY_ENTRY_LIMIT = 50
SAGE_MEMORY_WIPE_CONFIRMATION = "WIPE SAGE MEMORY"

SAGE_MEMORY_CATEGORY_ALIASES: dict[str, str] = {
    "work_context": "safe_general",
    "profile_fact": "safe_general",
    "saved_preference": "safe_general",
    "top_of_mind": "sensitive",
    "brief_history": "sensitive",
    "earlier_context": "sensitive",
    "long_term_background": "sensitive",
    "project_context": "sensitive",
    "personal_context": "private",
    "app_state": "private",
    "critical": "critical_restricted",
    "restricted": "critical_restricted",
    "red": "critical_restricted",
    "orange": "private",
    "yellow": "sensitive",
    "green": "safe_general",
}

_SAGE_MEMORY_EXPECTED_NEXT_ACTIONS: dict[str, str] = {
    "upsert_sage_memory_entry": "write_sage_memory_entry",
    "update_sage_memory_entry": "update_sage_memory_entry",
    "delete_sage_memory_entry": "delete_sage_memory_entry",
    "wipe_sage_memory": "wipe_sage_memory",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_file(workspace_id: str) -> Path:
    return workspace_context.workspace_scope_dir(workspace_id) / "sage_memory.json"


def _default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _utc_now_iso(),
        "entries": [],
    }


def _safe_read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        payload = _default_state()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("entries"), list):
        raw["entries"] = []
    return raw


def _save_state(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _state_file(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _utc_now_iso()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _enforce_sage_memory_state_decision(
    *,
    operation: str,
    workspace_id: str,
    actor_user_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    status: str = "active",
) -> Dict[str, Any]:
    record = dict(payload or {})
    expected_next_action = _SAGE_MEMORY_EXPECTED_NEXT_ACTIONS.get(
        str(operation or "").strip(),
        str(operation or "").strip(),
    )
    try:
        payload_bytes = len(json.dumps(record, sort_keys=True).encode("utf-8"))
    except Exception:
        payload_bytes = 0
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-state-store-decision",
            {
                "operation": operation,
                "state_class": "sage_memory",
                "storage_engine": "durable_postgres",
                "tenant_id": _coerce_text(workspace_id) or "default",
                "workspace_id": _coerce_text(workspace_id) or "default",
                "actor_id": _coerce_text(actor_user_id) or "sage_memory_service",
                "status": status,
                "payload": record,
                "payload_bytes": payload_bytes,
                "workspace_access": True,
                "owner_access": True,
                "destructive_approval": True,
            },
        )
        if str(decision.get("next_action") or "").strip() != expected_next_action:
            raise HTTPException(
                status_code=423,
                detail={
                    "error": "rust_runtime_state_store_denied",
                    "operation": operation,
                    "reason": f"unexpected_next_action:{decision.get('next_action')}",
                },
            )
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        result = getattr(exc, "result", None)
        if not isinstance(result, dict):
            result = {}
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_runtime_state_store_denied",
                "operation": result.get("operation") or operation,
                "reason": result.get("reason") or str(exc),
            },
        ) from exc


def _clip_text(value: Any, *, limit: int = 180) -> str:
    text = _coerce_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_category(category: str) -> str:
    raw_category = _coerce_text(category)
    return SAGE_MEMORY_CATEGORY_ALIASES.get(raw_category, raw_category)


def _require_category(category: str) -> tuple[str, dict[str, str]]:
    normalized_category = _normalize_category(category)
    definition = SAGE_MEMORY_CATEGORY_DEFINITIONS.get(normalized_category)
    if not definition:
        raise HTTPException(status_code=400, detail="Unsupported Sage memory category.")
    return normalized_category, definition


def _normalize_history(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "id": _coerce_text(item.get("id")) or f"memory-history-{uuid.uuid4().hex}",
                "action": _coerce_text(item.get("action")) or "updated",
                "actor_user_id": _coerce_text(item.get("actor_user_id")) or None,
                "occurred_at": _coerce_text(item.get("occurred_at")) or _utc_now_iso(),
                "note": _coerce_text(item.get("note")) or None,
            }
        )
    return normalized[:12]


def _normalize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    category, definition = _require_category(_coerce_text(entry.get("category")))
    title = _coerce_text(entry.get("title"))
    content = _coerce_text(entry.get("content"))
    if not title:
        raise HTTPException(status_code=400, detail="Sage memory title is required.")
    if not content:
        raise HTTPException(status_code=400, detail="Sage memory content is required.")
    created_at = _coerce_text(entry.get("created_at")) or _utc_now_iso()
    updated_at = _coerce_text(entry.get("updated_at")) or created_at
    history = _normalize_history(entry.get("history"))
    return {
        "id": _coerce_text(entry.get("id")) or f"memory-{uuid.uuid4().hex}",
        "category": category,
        "category_label": definition["label"],
        "title": title,
        "content": content,
        "summary": _clip_text(content),
        "pinned": bool(entry.get("pinned")),
        "source": _coerce_text(entry.get("source")) or "manual",
        "source_label": _coerce_text(entry.get("source_label")) or "Saved manually",
        "created_at": created_at,
        "updated_at": updated_at,
        "updated_by": _coerce_text(entry.get("updated_by")) or None,
        "history": history,
    }


def _entry_sort_key(item: Dict[str, Any]) -> tuple[int, float, str]:
    updated_at = _coerce_text(item.get("updated_at"))
    try:
        updated_ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp() if updated_at else 0.0
    except ValueError:
        updated_ts = 0.0
    return (
        0 if bool(item.get("pinned")) else 1,
        -updated_ts,
        str(item.get("title") or ""),
    )


def _read_state(workspace_id: str) -> Dict[str, Any]:
    raw = _safe_read_json(_state_file(workspace_id))
    entries: List[Dict[str, Any]] = []
    for item in list(raw.get("entries") or []):
        if not isinstance(item, dict):
            continue
        try:
            entries.append(_normalize_entry(item))
        except HTTPException:
            continue
    raw["entries"] = sorted(entries, key=_entry_sort_key)
    return raw


def _entry_categories(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts = {category: 0 for category in SAGE_MEMORY_CATEGORY_DEFINITIONS}
    for entry in entries:
        category = _coerce_text(entry.get("category"))
        if category in counts:
            counts[category] += 1
    return [
        {
            "id": category,
            "label": definition["label"],
            "description": definition["description"],
            "count": counts.get(category, 0),
        }
        for category, definition in SAGE_MEMORY_CATEGORY_DEFINITIONS.items()
    ]


def _summary(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "total_count": len(entries),
        "max_count": SAGE_MEMORY_ENTRY_LIMIT,
        "remaining_count": max(0, SAGE_MEMORY_ENTRY_LIMIT - len(entries)),
        "pinned_count": sum(1 for entry in entries if bool(entry.get("pinned"))),
        "category_counts": {
            category: sum(1 for entry in entries if entry.get("category") == category)
            for category in SAGE_MEMORY_CATEGORY_DEFINITIONS
        },
    }


def _history_event(*, action: str, actor_user_id: Optional[str], note: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": f"memory-history-{uuid.uuid4().hex}",
        "action": action,
        "actor_user_id": _coerce_text(actor_user_id) or None,
        "occurred_at": _utc_now_iso(),
        "note": _coerce_text(note) or None,
    }


def list_sage_memory(*, workspace_id: str) -> Dict[str, Any]:
    state = _read_state(workspace_id)
    entries = list(state.get("entries") or [])
    return {
        "items": entries,
        "categories": _entry_categories(entries),
        "summary": _summary(entries),
        "updated_at": state.get("updated_at"),
        "memory_context": build_sage_memory_context_block(workspace_id=workspace_id),
    }


def sage_memory_storage_policy(*, workspace_id: str) -> Dict[str, Any]:
    state = _read_state(workspace_id)
    entries = list(state.get("entries") or [])
    summary = _summary(entries)
    return {
        "authority": "cloud_canonical",
        "runtime_format": "structured_classes",
        "markdown_format": "import_export_only",
        "local_cache_policy": "encrypted_cache_only",
        "max_entries": SAGE_MEMORY_ENTRY_LIMIT,
        "used_entries": summary["total_count"],
        "remaining_entries": summary["remaining_count"],
        "categories": _entry_categories(entries),
        "capabilities": {
            "list": True,
            "create": True,
            "update": True,
            "delete": True,
            "export": True,
            "workspace_wipe": True,
        },
        "retention": {
            "memory": "kept until user deletes or wipes workspace memory",
            "markdown": "export/import format only, not runtime authority",
            "artifact_ttl": "managed separately by artifact retention policy",
            "audit_log": "stored separately from assistant transcript and memory",
        },
        "updated_at": state.get("updated_at"),
    }


def _memory_export_markdown(*, workspace_id: str, entries: List[Dict[str, Any]], updated_at: Any) -> str:
    lines: List[str] = [
        "# Sage Memory Export",
        "",
        f"- Workspace: {workspace_id}",
        f"- Exported at: {_utc_now_iso()}",
        f"- Memory updated at: {_coerce_text(updated_at) or 'never'}",
        f"- Runtime authority: structured classes in Empyralis cloud",
        "",
    ]
    for category, definition in SAGE_MEMORY_CATEGORY_DEFINITIONS.items():
        category_entries = [entry for entry in entries if entry.get("category") == category]
        lines.append(f"## {definition['label']}")
        lines.append("")
        if not category_entries:
            lines.append("_No entries._")
            lines.append("")
            continue
        for entry in category_entries:
            pinned = " pinned" if bool(entry.get("pinned")) else ""
            lines.append(f"### {_coerce_text(entry.get('title'))}{pinned}")
            lines.append("")
            lines.append(_coerce_text(entry.get("content")))
            lines.append("")
            lines.append(f"- ID: {_coerce_text(entry.get('id'))}")
            lines.append(f"- Updated: {_coerce_text(entry.get('updated_at'))}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def export_sage_memory(*, workspace_id: str) -> Dict[str, Any]:
    payload = list_sage_memory(workspace_id=workspace_id)
    entries = list(payload.get("items") or [])
    policy = sage_memory_storage_policy(workspace_id=workspace_id)
    return {
        "export_type": "sage_memory",
        "workspace_id": workspace_id,
        "exported_at": _utc_now_iso(),
        "items": entries,
        "categories": payload.get("categories") or [],
        "summary": payload.get("summary") or {},
        "storage_policy": policy,
        "markdown": _memory_export_markdown(
            workspace_id=workspace_id,
            entries=entries,
            updated_at=payload.get("updated_at"),
        ),
    }


def wipe_sage_memory(
    *,
    workspace_id: str,
    actor_user_id: Optional[str] = None,
    confirm: str,
) -> Dict[str, Any]:
    if _coerce_text(confirm) != SAGE_MEMORY_WIPE_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation phrase required: {SAGE_MEMORY_WIPE_CONFIRMATION}",
        )
    state = _read_state(workspace_id)
    entries = list(state.get("entries") or [])
    deleted_count = len(entries)
    previous_updated_at = state.get("updated_at")
    wiped_state = {
        "version": int(state.get("version") or 1),
        "updated_at": _utc_now_iso(),
        "entries": [],
        "last_wipe": {
            "actor_user_id": _coerce_text(actor_user_id) or None,
            "occurred_at": _utc_now_iso(),
            "deleted_count": deleted_count,
        },
    }
    _enforce_sage_memory_state_decision(
        operation="wipe_sage_memory",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        payload=wiped_state,
        status="archived",
    )
    _save_state(workspace_id, wiped_state)
    return {
        "deleted_count": deleted_count,
        "previous_updated_at": previous_updated_at,
        **list_sage_memory(workspace_id=workspace_id),
        "storage_policy": sage_memory_storage_policy(workspace_id=workspace_id),
    }


def upsert_memory_entry(
    *,
    workspace_id: str,
    category: str,
    title: str,
    content: str,
    pinned: bool = False,
    actor_user_id: Optional[str] = None,
    entry_id: Optional[str] = None,
    source: str = "manual",
    source_label: str = "Saved manually",
) -> Dict[str, Any]:
    state = _read_state(workspace_id)
    entries = list(state.get("entries") or [])
    resolved_entry_id = _coerce_text(entry_id)
    matched_index = next((index for index, item in enumerate(entries) if _coerce_text(item.get("id")) == resolved_entry_id), -1)
    now = _utc_now_iso()

    if matched_index >= 0:
        existing = dict(entries[matched_index])
        history = _normalize_history(existing.get("history"))
        history.insert(0, _history_event(action="corrected", actor_user_id=actor_user_id))
        normalized = _normalize_entry(
            {
                **existing,
                "category": category,
                "title": title,
                "content": content,
                "pinned": pinned if pinned or bool(existing.get("pinned")) else False,
                "source": source,
                "source_label": source_label,
                "updated_at": now,
                "updated_by": _coerce_text(actor_user_id) or None,
                "history": history[:12],
            }
        )
        entries[matched_index] = normalized
    else:
        if len(entries) >= SAGE_MEMORY_ENTRY_LIMIT:
            raise HTTPException(status_code=409, detail="Sage memory limit reached.")
        normalized = _normalize_entry(
            {
                "id": resolved_entry_id or None,
                "category": category,
                "title": title,
                "content": content,
                "pinned": pinned,
                "source": source,
                "source_label": source_label,
                "created_at": now,
                "updated_at": now,
                "updated_by": _coerce_text(actor_user_id) or None,
                "history": [_history_event(action="saved", actor_user_id=actor_user_id)],
            }
        )
        entries.insert(0, normalized)

    state["entries"] = entries
    _enforce_sage_memory_state_decision(
        operation="upsert_sage_memory_entry",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        payload=normalized,
        status="active",
    )
    _save_state(workspace_id, state)
    return {
        "entry": normalized,
        **list_sage_memory(workspace_id=workspace_id),
    }


def set_memory_entry_pinned(
    *,
    workspace_id: str,
    entry_id: str,
    pinned: bool,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    state = _read_state(workspace_id)
    entries = list(state.get("entries") or [])
    for index, item in enumerate(entries):
        if _coerce_text(item.get("id")) != _coerce_text(entry_id):
            continue
        history = _normalize_history(item.get("history"))
        history.insert(
            0,
            _history_event(
                action="pinned" if pinned else "unpinned",
                actor_user_id=actor_user_id,
            ),
        )
        updated = _normalize_entry(
            {
                **item,
                "pinned": bool(pinned),
                "updated_at": _utc_now_iso(),
                "updated_by": _coerce_text(actor_user_id) or None,
                "history": history[:12],
            }
        )
        entries[index] = updated
        state["entries"] = entries
        _enforce_sage_memory_state_decision(
            operation="update_sage_memory_entry",
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            payload=updated,
            status="active",
        )
        _save_state(workspace_id, state)
        return {
            "entry": updated,
            **list_sage_memory(workspace_id=workspace_id),
        }
    raise HTTPException(status_code=404, detail="Sage memory entry not found.")


def delete_memory_entry(
    *,
    workspace_id: str,
    entry_id: str,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    state = _read_state(workspace_id)
    entries = list(state.get("entries") or [])
    remaining: List[Dict[str, Any]] = []
    deleted: Optional[Dict[str, Any]] = None
    for item in entries:
        if deleted is None and _coerce_text(item.get("id")) == _coerce_text(entry_id):
            history = _normalize_history(item.get("history"))
            history.insert(0, _history_event(action="forgotten", actor_user_id=actor_user_id))
            deleted = {
                **item,
                "history": history[:12],
            }
            continue
        remaining.append(item)
    if deleted is None:
        raise HTTPException(status_code=404, detail="Sage memory entry not found.")
    state["entries"] = remaining
    _enforce_sage_memory_state_decision(
        operation="delete_sage_memory_entry",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        payload=deleted,
        status="archived",
    )
    _save_state(workspace_id, state)
    return {
        "deleted": deleted,
        **list_sage_memory(workspace_id=workspace_id),
    }


def build_sage_memory_context_block(
    *,
    workspace_id: str,
    limit_per_category: int = 3,
    include_restricted: bool = False,
) -> str:
    state = _read_state(workspace_id)
    entries = list(state.get("entries") or [])
    if not entries:
        return ""

    lines: List[str] = ["Sage memory"]
    for category, definition in SAGE_MEMORY_CATEGORY_DEFINITIONS.items():
        category_entries = [item for item in entries if item.get("category") == category][: max(1, int(limit_per_category))]
        if not category_entries:
            continue
        if category == "critical_restricted" and not include_restricted:
            lines.append(f"{definition['label']}")
            lines.append("- Restricted memory exists but is withheld from model context.")
            continue
        lines.append(f"{definition['label']}")
        for item in category_entries:
            pin_marker = " [pinned]" if bool(item.get("pinned")) else ""
            lines.append(
                f"- {str(item.get('title') or '').strip()}: {str(item.get('content') or '').strip()}{pin_marker}"
            )
    return "\n".join(lines).strip()
