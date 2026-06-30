from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from server_modules import rust_runtime_kernel_client
from server_modules import secret_redaction_service
from server_modules import workspace_context


PROOF_LOG_STORE_VERSION = 1
PROOF_LOG_RECORD_VERSION = "sage_proof_log_record_v1"
PROOF_LOG_STATE_CLASS = "sage_proof_logs"
PROOF_LOG_APPEND_OPERATION = "append_sage_proof_log"
PROOF_LOG_MAX_RECORDS = 1_000
PROOF_LOG_DEFAULT_LIMIT = 50
PROOF_LOG_MAX_LIMIT = 200

_LOCK = threading.RLock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _state_file(workspace_id: str) -> Path:
    return workspace_context.workspace_scope_dir(workspace_id) / "sage_proof_logs.json"


def _default_state() -> Dict[str, Any]:
    return {
        "version": PROOF_LOG_STORE_VERSION,
        "updated_at": _utc_now_iso(),
        "proof_logs": [],
    }


def _safe_read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        payload = _default_state()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("proof_logs"), list):
        raw["proof_logs"] = []
    raw["version"] = int(raw.get("version") or PROOF_LOG_STORE_VERSION)
    return raw


def _save_state(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _state_file(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["version"] = PROOF_LOG_STORE_VERSION
    payload["updated_at"] = _utc_now_iso()
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
    return payload


def _normalize_limit(limit: int | None) -> int:
    try:
        value = int(limit or PROOF_LOG_DEFAULT_LIMIT)
    except Exception:
        value = PROOF_LOG_DEFAULT_LIMIT
    return max(1, min(PROOF_LOG_MAX_LIMIT, value))


def _load_filtered_records(
    *,
    workspace_id: str,
    tenant_id: str = "",
    status: str = "",
    surface: str = "",
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    normalized_workspace_id = _coerce_text(workspace_id) or "default"
    normalized_tenant_id = _coerce_text(tenant_id)
    normalized_status = _coerce_text(status).lower()
    normalized_surface = _coerce_text(surface).lower()
    with _LOCK:
        state = _safe_read_json(_state_file(normalized_workspace_id))
        items = [dict(item) for item in state.get("proof_logs", []) if isinstance(item, dict)]
    if normalized_tenant_id:
        items = [item for item in items if _coerce_text(item.get("tenant_id")) == normalized_tenant_id]
    if normalized_status:
        items = [item for item in items if _coerce_text(item.get("status")).lower() == normalized_status]
    if normalized_surface:
        items = [item for item in items if _coerce_text(item.get("surface")).lower() == normalized_surface]
    return state, items


def _coerce_dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: List[Dict[str, Any]] = []
    for item in value[:100]:
        if isinstance(item, dict):
            sanitized = secret_redaction_service.sanitize_mapping(item)
            items.append(sanitized)
        elif _coerce_text(item):
            items.append({"summary": secret_redaction_service.redact_text(item)})
    return items


def _sanitize_proof_log(proof_log: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = secret_redaction_service.sanitize_mapping(proof_log)
    secret_redaction_service.assert_secrets_free(sanitized, context="sage_proof_log")
    return sanitized


def _proof_id_for_record(*, workspace_id: str, trace_id: str, surface: str, proof_log: Dict[str, Any]) -> str:
    if trace_id:
        basis = ":".join(
            [
                _coerce_text(workspace_id) or "default",
                _coerce_text(trace_id),
                _coerce_text(surface) or "sage",
                _coerce_text(proof_log.get("recipe_id")),
                _coerce_text(proof_log.get("title")),
            ]
        )
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
        return f"proof_{digest}"
    return f"proof_{uuid.uuid4().hex[:16]}"


def _record_summary(proof_log: Dict[str, Any]) -> Dict[str, Any]:
    checked = _coerce_dict_list(proof_log.get("checked"))
    changes = _coerce_dict_list(proof_log.get("changes"))
    blocked = _coerce_dict_list(proof_log.get("blocked"))
    approvals = _coerce_dict_list(proof_log.get("approvals"))
    return {
        "checked_count": len(checked),
        "changes_count": len(changes),
        "blocked_count": len(blocked),
        "approval_count": len(approvals),
    }


def _build_record(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    trace_id: str,
    surface: str,
    proof_log: Dict[str, Any],
    status: str,
    title: str,
    source: str,
) -> Dict[str, Any]:
    sanitized = _sanitize_proof_log(proof_log)
    now = _utc_now_iso()
    normalized_surface = _coerce_text(surface) or "sage"
    normalized_status = _coerce_text(status) or _coerce_text(sanitized.get("status")) or "logged"
    normalized_title = _coerce_text(title) or _coerce_text(sanitized.get("title")) or "Sage proof log"
    record = {
        "version": PROOF_LOG_RECORD_VERSION,
        "proof_id": _proof_id_for_record(
            workspace_id=workspace_id,
            trace_id=_coerce_text(trace_id),
            surface=normalized_surface,
            proof_log=sanitized,
        ),
        "tenant_id": _coerce_text(tenant_id) or _coerce_text(workspace_id) or "default",
        "workspace_id": _coerce_text(workspace_id) or "default",
        "actor_user_id": _coerce_text(actor_user_id),
        "trace_id": _coerce_text(trace_id),
        "surface": normalized_surface,
        "source": _coerce_text(source) or "sage",
        "status": normalized_status,
        "title": normalized_title,
        "created_at": now,
        "updated_at": now,
        "recipe_id": _coerce_text(sanitized.get("recipe_id")),
        "checked": _coerce_dict_list(sanitized.get("checked")),
        "changes": _coerce_dict_list(sanitized.get("changes")),
        "blocked": _coerce_dict_list(sanitized.get("blocked")),
        "approvals": _coerce_dict_list(sanitized.get("approvals")),
        "proof_log": sanitized,
        "summary": _record_summary(sanitized),
    }
    return record


def _enforce_proof_log_state_decision(*, record: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload_bytes = len(json.dumps(record, sort_keys=True).encode("utf-8"))
    except Exception:
        payload_bytes = 0
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-state-store-decision",
            {
                "operation": PROOF_LOG_APPEND_OPERATION,
                "state_class": PROOF_LOG_STATE_CLASS,
                "storage_engine": "durable_postgres",
                "tenant_id": _coerce_text(record.get("tenant_id")) or "default",
                "workspace_id": _coerce_text(record.get("workspace_id")) or "default",
                "actor_id": _coerce_text(record.get("actor_user_id")) or "sage_proof_log_service",
                "status": _coerce_text(record.get("status")) or "logged",
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
        raise RuntimeError(
            result.get("reason") or "Rust runtime state store denied Sage proof log append."
        ) from exc
    next_action = _coerce_text(decision.get("next_action"))
    if next_action != PROOF_LOG_APPEND_OPERATION:
        raise RuntimeError(
            f"Rust runtime state store returned unexpected next_action for Sage proof log append: {next_action or 'missing'}"
        )
    return decision


def append_proof_log(
    *,
    tenant_id: str,
    workspace_id: str,
    proof_log: Dict[str, Any],
    actor_user_id: str = "",
    trace_id: str = "",
    surface: str = "sage",
    status: str = "",
    title: str = "",
    source: str = "sage",
) -> Dict[str, Any]:
    if not isinstance(proof_log, dict) or not proof_log:
        raise ValueError("proof_log must be a non-empty object.")
    normalized_workspace_id = _coerce_text(workspace_id) or "default"
    record = _build_record(
        tenant_id=tenant_id,
        workspace_id=normalized_workspace_id,
        actor_user_id=actor_user_id,
        trace_id=trace_id,
        surface=surface,
        proof_log=proof_log,
        status=status,
        title=title,
        source=source,
    )
    _enforce_proof_log_state_decision(record=record)
    with _LOCK:
        state = _safe_read_json(_state_file(normalized_workspace_id))
        existing = [item for item in state.get("proof_logs", []) if isinstance(item, dict)]
        prior = next((item for item in existing if _coerce_text(item.get("proof_id")) == record["proof_id"]), None)
        if prior and _coerce_text(prior.get("created_at")):
            record["created_at"] = _coerce_text(prior.get("created_at"))
        state["proof_logs"] = [
            record,
            *[item for item in existing if _coerce_text(item.get("proof_id")) != record["proof_id"]],
        ][:PROOF_LOG_MAX_RECORDS]
        _save_state(normalized_workspace_id, state)
    return record


def list_proof_logs(
    *,
    workspace_id: str,
    tenant_id: str = "",
    status: str = "",
    surface: str = "",
    limit: int | None = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _coerce_text(workspace_id) or "default"
    state, items = _load_filtered_records(
        workspace_id=normalized_workspace_id,
        tenant_id=tenant_id,
        status=status,
        surface=surface,
    )
    limited = items[: _normalize_limit(limit)]
    return {
        "version": PROOF_LOG_STORE_VERSION,
        "updated_at": _coerce_text(state.get("updated_at")),
        "count": len(limited),
        "total_count": len(items),
        "items": limited,
    }


def get_proof_log(*, workspace_id: str, proof_id: str, tenant_id: str = "") -> Dict[str, Any] | None:
    normalized_workspace_id = _coerce_text(workspace_id) or "default"
    normalized_proof_id = _coerce_text(proof_id)
    normalized_tenant_id = _coerce_text(tenant_id)
    if not normalized_proof_id:
        return None
    _, items = _load_filtered_records(
        workspace_id=normalized_workspace_id,
        tenant_id=normalized_tenant_id,
    )
    for item in items:
        if _coerce_text(item.get("proof_id")) == normalized_proof_id:
            return item
    return None


def summarize_proof_logs(*, workspace_id: str, tenant_id: str = "") -> Dict[str, Any]:
    _, items = _load_filtered_records(workspace_id=workspace_id, tenant_id=tenant_id)
    status_counts: Dict[str, int] = {}
    surface_counts: Dict[str, int] = {}
    totals = {
        "checked_count": 0,
        "changes_count": 0,
        "blocked_count": 0,
        "approval_count": 0,
    }
    latest_created_at = ""
    for item in items:
        status = _coerce_text(item.get("status")) or "logged"
        surface = _coerce_text(item.get("surface")) or "sage"
        status_counts[status] = status_counts.get(status, 0) + 1
        surface_counts[surface] = surface_counts.get(surface, 0) + 1
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        for key in totals:
            try:
                totals[key] += int(summary.get(key) or 0)
            except Exception:
                pass
        created_at = _coerce_text(item.get("created_at"))
        if created_at and created_at > latest_created_at:
            latest_created_at = created_at
    return {
        "version": PROOF_LOG_STORE_VERSION,
        "total_count": len(items),
        "status_counts": status_counts,
        "surface_counts": surface_counts,
        "latest_created_at": latest_created_at,
        **totals,
    }
