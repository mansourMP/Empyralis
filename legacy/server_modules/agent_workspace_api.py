from __future__ import annotations

import json
import mimetypes
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote, urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse

from scripts.platform_execution import current_platform_context, supported_device_actions
from server_modules.api_contract import ApiArtifactListResponse, ApiArtifactPreviewResponse
from server_modules.auth import enforce_workspace_access
from server_modules import artifact_service
from server_modules import entitlements_service
from server_modules import run_state_repository
from server_modules.run_service import RunExecutionServices, build_server_system_run_execution_services
from server_modules.schemas import DeviceExecuteRequest, WorkspaceFileDeleteRequest, WorkspaceFileWriteRequest
from server_modules.turn_runtime import (
    build_execute_unowned_system_run_start_request_via_turn_runtime,
    execute_built_unowned_system_run_start_request_via_turn_runtime,
    execute_run_start_request_via_turn_runtime as execute_async_run_start_request_via_turn_runtime,
)
from server_modules.vault_helpers import list_vault_connectors, resolve_vault_credential


def _late_server_export(name: str):
    import server as _server

    return getattr(_server, name)


def _refresh_server_exports():
    import server as _server

    globals().update(_server.__dict__)
    return _server


def _normalize_agent_role(value: Any) -> str:
    try:
        from server_modules.runs_delegation import normalize_agent_role as _normalize
    except Exception:
        return str(value or "").strip().lower()
    return str(_normalize(value) or "").strip().lower()


def _normalize_workspace_id(value: Any) -> str:
    token = str(value or "").strip()
    return token or "default"


def _workspace_entitlement_payload(
    cache: Dict[str, Dict[str, Any]],
    workspace_id: str,
) -> Dict[str, Any]:
    token = _normalize_workspace_id(workspace_id)
    payload = cache.get(token)
    if payload is None:
        payload = entitlements_service.workspace_entitlement_payload_for_workspace_id(workspace_id=token)
        cache[token] = payload
    return payload


def _workspace_history_cutoff_ts(
    cache: Dict[str, Dict[str, Any]],
    workspace_id: str,
) -> float:
    payload = _workspace_entitlement_payload(cache, workspace_id)
    capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
    history_window_days = max(1, int(capabilities.get("history_window_days") or 30))
    return float(datetime.now(timezone.utc).timestamp()) - float(history_window_days * 86400)


def _payload_timestamp_seconds(payload: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        text = str(payload.get(key) or "").strip()
        if not text:
            continue
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _ensure_artifacts_access_for_workspace(workspace_id: str) -> None:
    payload = entitlements_service.workspace_entitlement_payload_for_workspace_id(
        workspace_id=_normalize_workspace_id(workspace_id),
    )
    capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
    if not bool(capabilities.get("artifacts_enabled")):
        raise HTTPException(status_code=403, detail="Artifacts are not included in this workspace plan.")


def _active_workspace_scope_from_request(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    header_value = str(request.headers.get("x-empyralis-workspace-id") or "").strip()
    if header_value:
        return header_value
    query_params = getattr(request, "query_params", None)
    if query_params is None:
        return None
    for key in ("workspace_id", "workspaceId"):
        value = str(query_params.get(key) or "").strip()
        if value:
            return value
    return None


def _enforce_active_workspace_scope(
    request: Optional[Request],
    artifact_workspace_id: str,
) -> None:
    requested_workspace_id = _active_workspace_scope_from_request(request)
    if requested_workspace_id and requested_workspace_id != str(artifact_workspace_id or "").strip():
        raise HTTPException(status_code=403, detail="Artifact is outside the active workspace scope.")


def _artifact_content_disposition(filename: str, *, disposition: str = "attachment") -> str:
    safe_filename = str(filename or "artifact.bin").replace('"', "").strip() or "artifact.bin"
    ascii_filename = safe_filename.encode("ascii", "ignore").decode("ascii").strip() or "artifact.bin"
    if ascii_filename == safe_filename:
        return f'{disposition}; filename="{ascii_filename}"'
    return f"{disposition}; filename*=UTF-8''{quote(safe_filename)}"


def _load_workspace_artifact_record(
    artifact_id: str,
    *,
    current_user: Any,
    request: Optional[Request] = None,
) -> tuple[dict[str, Any], Path, str, str]:
    payload = artifact_service.load_artifact_metadata_by_id(artifact_id)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=404, detail="Artifact not found.")

    workspace_id = str(payload.get("workspace_id") or "default").strip() or "default"
    tenant_id = str(payload.get("tenant_id") or "").strip() or None
    _ensure_artifacts_access_for_workspace(workspace_id)
    enforce_workspace_access(
        current_user,
        workspace_id,
        tenant_id=tenant_id,
        minimum_role="viewer",
    )
    _enforce_active_workspace_scope(request, workspace_id)

    target = artifact_service.resolve_artifact_content_path_by_id(artifact_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Artifact content is unavailable for this canonical artifact.")

    file_name = artifact_service.artifact_download_filename(payload, target.name)
    media_type = artifact_service.artifact_content_type(payload, target.name)
    return payload, target, media_type, file_name


def _artifact_detail_payload_for_id(
    artifact_id: str,
    *,
    current_user: Any,
    request: Optional[Request] = None,
) -> dict[str, Any]:
    payload, target, media_type, file_name = _load_workspace_artifact_record(
        artifact_id,
        current_user=current_user,
        request=request,
    )
    preview_kind = _workspace_artifact_preview_kind(target, media_type)
    text_preview = _read_workspace_text_preview(target) if preview_kind == "text" else None
    return {
        "ok": True,
        "artifact_id": str(payload.get("artifact_id") or artifact_id).strip() or artifact_id,
        "workspace_id": str(payload.get("workspace_id") or "default").strip() or "default",
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "run_id": str(payload.get("run_id") or "").strip() or None,
        "uri": str(payload.get("uri") or "").strip() or None,
        "kind": str(payload.get("kind") or "artifact").strip() or "artifact",
        "label": str(payload.get("label") or file_name).strip() or file_name,
        "file_name": file_name,
        "media_type": media_type,
        "preview_kind": preview_kind,
        "byte_size": int(payload.get("byte_size") or target.stat().st_size),
        "created_at": str(payload.get("created_at") or "").strip() or None,
        "machine_id": str(payload.get("machine_id") or "").strip() or None,
        "step_id": str(payload.get("step_id") or "").strip() or None,
        "step_index": payload.get("step_index"),
        "step_number": payload.get("step_number"),
        "text_preview": text_preview,
        "retention": dict(payload.get("retention") or {}),
        "metadata": dict(payload.get("metadata") or {}),
    }


def _artifact_file_response_for_id(
    artifact_id: str,
    *,
    current_user: Any,
    request: Optional[Request] = None,
    disposition: str = "attachment",
):
    _, target, media_type, file_name = _load_workspace_artifact_record(
        artifact_id,
        current_user=current_user,
        request=request,
    )
    return FileResponse(
        path=str(target),
        media_type=media_type,
        headers={
            "Content-Disposition": _artifact_content_disposition(
                file_name,
                disposition=disposition,
            ),
        },
    )


execute_system_run_start_request_via_turn_runtime = build_execute_unowned_system_run_start_request_via_turn_runtime()


def _agent_workspace_run_execution_services() -> RunExecutionServices:
    return build_server_system_run_execution_services(
        late_server_export=_late_server_export,
    )


def _execute_workspace_run_request(request: Any) -> Dict[str, Any]:
    return execute_built_unowned_system_run_start_request_via_turn_runtime(
        request,
        execute_system_run_start_request_via_turn_runtime_fn=execute_system_run_start_request_via_turn_runtime,
        build_run_execution_services_fn=_agent_workspace_run_execution_services,
    )


async def _execute_workspace_run_request_async(
    request: Any,
    *,
    current_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    system_user = (
        dict(current_user)
        if isinstance(current_user, dict)
        else {"auth_type": "api_key", "user_id": "", "email": ""}
    )
    return await execute_async_run_start_request_via_turn_runtime(
        request,
        current_user=system_user,
        stamp_request_owner_fn=lambda req, current_user: req,
        services=_agent_workspace_run_execution_services(),
    )


AGENT_WORKSPACE_LABELS: Dict[str, str] = {
    "orchestrator": "Orchestrator",
    "support": "Support Inbox",
    "sales": "Sales / Booking",
    "research": "Research / Memory",
    "finance": "Finance / Sheets",
    "builder": "Builder Ops",
    "private-assistant": "Private Assistant",
}

AGENT_WORKSPACE_SUMMARIES: Dict[str, str] = {
    "orchestrator": "routing, approvals, cross-agent coordination",
    "support": "customer messages, feedback, escalation",
    "sales": "leads, follow-ups, booking flow",
    "research": "briefs, memory, study and analysis",
    "finance": "spreadsheets, reports, finance ops",
    "builder": "code, design, automations, file work",
    "private-assistant": "study, planning, reminders",
}

AGENT_WORKSPACE_CHANNELS: Dict[str, Set[str]] = {
    "orchestrator": {"telegram", "whatsapp", "email"},
    "support": {"telegram", "whatsapp", "email"},
    "sales": {"telegram", "whatsapp", "email"},
    "research": {"email"},
    "finance": {"email"},
    "builder": set(),
    "private-assistant": {"telegram", "whatsapp", "email"},
}

AGENT_WORKSPACE_KEYWORDS: Dict[str, List[str]] = {
    "support": ["support", "customer", "feedback", "reply", "inbox", "message", "complaint", "email", "telegram", "whatsapp"],
    "sales": ["sales", "lead", "booking", "book", "appointment", "follow-up", "follow up", "pipeline", "convert", "slot"],
    "research": ["research", "study", "exam", "memory", "brief", "summary", "docs", "analyze", "analysis", "plan"],
    "finance": ["finance", "sheet", "sheets", "excel", "budget", "invoice", "expense", "revenue", "report", "spreadsheet"],
    "builder": ["build", "builder", "code", "debug", "fix", "frontend", "backend", "design", "automation", "script", "workflow", "platform"],
    "private-assistant": ["personal", "private", "reminder", "study", "exam", "habit", "routine", "travel", "note", "calendar"],
}

AGENT_WORKSPACE_PATH_HINTS = {
    "path",
    "file",
    "files",
    "attachment",
    "artifact",
    "artifacts",
    "output",
    "outputs",
    "export",
    "exports",
    "image",
    "images",
    "screenshot",
    "screenshots",
    "preview",
    "uri",
    "url",
    "relative_path",
}

AGENT_WORKSPACE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
AGENT_WORKSPACE_DATA_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".tsv"}
AGENT_WORKSPACE_DOC_EXTENSIONS = {".md", ".pdf", ".txt", ".doc", ".docx", ".ppt", ".pptx", ".html"}
AGENT_WORKSPACE_FILE_EXTENSIONS = AGENT_WORKSPACE_IMAGE_EXTENSIONS | AGENT_WORKSPACE_DATA_EXTENSIONS | AGENT_WORKSPACE_DOC_EXTENSIONS | {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".scss",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
}
AGENT_WORKSPACE_BUILDER_REPO_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".scss",
    ".json",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".sql",
}
AGENT_WORKSPACE_IGNORED_PATH_PREFIXES = {
    ".orion-media/",
    ".orion-stack/",
    ".next/",
    "node_modules/",
    ".git/",
    ".venv/",
    "venv/",
}
AGENT_WORKSPACE_SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".npmrc",
    ".pypirc",
    "runtime_key",
    "ops_daemon_key",
    "stack.env",
    "users.db",
}
WORKSPACE_PREVIEW_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".svg",
}
WORKSPACE_PREVIEW_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".scss",
    ".html",
    ".sh",
    ".toml",
    ".log",
    ".sql",
    ".xml",
}
WORKSPACE_PREVIEW_TEXT_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-sh",
}
AGENT_WORKSPACE_DIFF_TEXT_KEYS = {
    "patch",
    "diff",
    "unified_diff",
    "content",
    "content_after",
    "updated_content",
    "new_content",
    "after",
}


def _agent_workspace_search_text(item: Dict[str, Any]) -> str:
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    payload_parts = [
        str(item.get("run_id") or ""),
        str(item.get("user_goal") or ""),
        str(item.get("result_summary") or ""),
        str(item.get("pack_id") or ""),
        str(item.get("engine") or ""),
        str(item.get("status") or ""),
        str(metadata.get("outcome_pack") or ""),
    ]
    pack_inputs = metadata.get("pack_inputs")
    if pack_inputs is not None:
        payload_parts.append(json.dumps(_json_safe(pack_inputs), ensure_ascii=True))
    return " ".join(part for part in payload_parts if part).lower()


def _resolve_agent_role_for_workspace_item(item: Dict[str, Any]) -> Tuple[str, str]:
    explicit = _normalize_agent_role(item.get("agent_role"))
    source = str(item.get("agent_role_source") or "").strip().lower()
    if explicit:
        return explicit, source or "explicit"

    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    nested_explicit = _normalize_agent_role(metadata.get("agent_role"))
    nested_source = str(metadata.get("agent_role_source") or "").strip().lower()
    if nested_explicit:
        return nested_explicit, nested_source or "explicit"

    pack_id = str(item.get("pack_id") or metadata.get("outcome_pack") or "").strip().lower()
    mapped = OUTCOME_PACK_AGENT_ROLE_MAP.get(pack_id)
    if mapped:
        return mapped, "outcome_pack"

    haystack = _agent_workspace_search_text(item)
    best_role = ""
    best_score = 0
    for role, keywords in AGENT_WORKSPACE_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score > best_score:
            best_role = role
            best_score = score
    if best_role and best_score > 0:
        return best_role, "inferred"
    return "", ""


def _hydrate_workspace_item_role(item: Dict[str, Any]) -> Dict[str, Any]:
    hydrated = dict(item)
    role, source = _resolve_agent_role_for_workspace_item(hydrated)
    if role and not hydrated.get("agent_role"):
        hydrated["agent_role"] = role
    if source and not hydrated.get("agent_role_source"):
        hydrated["agent_role_source"] = source
    return hydrated


def _looks_like_workspace_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 2048:
        return False
    if any(char in text for char in ["\n", "\r", "\t"]):
        return False
    lowered = text.lower()
    if lowered.startswith(("artifact://", "http://", "https://", "file://", "/", "./", "../", ".orion-")):
        return True
    for ext in AGENT_WORKSPACE_FILE_EXTENSIONS:
        if lowered.endswith(ext):
            return True
    if "/" in text or "\\" in text:
        filename = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "." in filename and len(filename.rsplit(".", 1)[-1]) <= 8:
            return True
    return False


def _normalize_workspace_material_path(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith(("artifact://", "http://", "https://", "file://")):
        return text
    cwd = Path.cwd()
    try:
        candidate = Path(text).expanduser()
        if candidate.is_absolute():
            try:
                relative = candidate.relative_to(cwd)
                normalized = relative.as_posix()
            except Exception:
                normalized = candidate.as_posix()
        else:
            normalized = candidate.as_posix()
    except Exception:
        normalized = text.replace("\\", "/")
    normalized = normalized.lstrip("./")
    return normalized or None


def _workspace_repo_area(path_value: str) -> str:
    normalized = str(path_value or "").strip().replace("\\", "/")
    if not normalized:
        return "unknown"
    if normalized.startswith(("http://", "https://")):
        return "external"
    for prefix in AGENT_WORKSPACE_IGNORED_PATH_PREFIXES:
        if normalized.startswith(prefix):
            return "runtime"
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return "root"
    if len(parts) == 1:
        return "root"
    return parts[0]


def _local_companion_root_path() -> Path:
    configured = (
        str(os.getenv("ORION_LOCAL_COMPANION_ROOT") or "").strip()
        or str(os.getenv("ORION_COGNITIVE_OPERATOR_ROOT") or "").strip()
        or str(Path.cwd())
    )
    return Path(configured).expanduser().resolve()


def _workspace_allowed_roots() -> List[Path]:
    roots: List[Path] = []
    for candidate in [_local_companion_root_path(), _git_repo_root(), Path.cwd().resolve()]:
        if not candidate:
            continue
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _workspace_path_is_restricted(path_value: str) -> bool:
    normalized = str(path_value or "").strip().replace("\\", "/").lstrip("./")
    if not normalized:
        return True
    lowered = normalized.lower()
    for prefix in AGENT_WORKSPACE_IGNORED_PATH_PREFIXES:
        cleaned = prefix.rstrip("/").lower()
        if lowered == cleaned or lowered.startswith(f"{cleaned}/"):
            return True
    parts = [part for part in lowered.split("/") if part and part != "."]
    if not parts:
        return True
    if any(part.startswith(".") for part in parts):
        return True
    if any(part in AGENT_WORKSPACE_SENSITIVE_FILENAMES for part in parts):
        return True
    return False


def _ensure_workspace_path_allowed(path_value: str) -> None:
    if _workspace_path_is_restricted(path_value):
        raise HTTPException(status_code=403, detail="Path is not available through workspace APIs.")


def _resolve_workspace_material_target(normalized_path: str) -> Optional[Path]:
    target_value = str(normalized_path or "").strip()
    if not target_value:
        return None
    if artifact_service.is_artifact_uri(target_value):
        return artifact_service.resolve_artifact_content_path(target_value)
    if target_value.lower().startswith(("http://", "https://", "file://")):
        return None
    if _workspace_path_is_restricted(target_value):
        return None

    candidate = Path(target_value).expanduser()
    if candidate.is_absolute():
        candidate_paths = [candidate.resolve()]
    else:
        candidate_paths = []
        for root in _workspace_allowed_roots():
            resolved = (root / candidate).resolve()
            if resolved not in candidate_paths:
                candidate_paths.append(resolved)

    allowed_roots = _workspace_allowed_roots()
    for resolved in candidate_paths:
        for root in allowed_roots:
            try:
                resolved.relative_to(root)
            except Exception:
                continue
            if resolved.exists() and resolved.is_file():
                return resolved
    return None


def _workspace_artifact_payload_rows(value: Any, *, run_id: str, updated_at: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        if artifact_service.is_canonical_artifact_payload(value):
            record = artifact_service.load_artifact_metadata(artifact_service.canonical_artifact_reference(value) or "") or {}
            merged = dict(record) if isinstance(record, dict) else {}
            merged.update(dict(value))
            location = str(
                merged.get("uri")
                or merged.get("uri_or_path")
                or merged.get("file_path")
                or merged.get("path")
                or ""
            ).strip()
            if location:
                rows.append(
                    {
                        "artifact_id": str(merged.get("artifact_id") or "").strip() or None,
                        "uri_or_path": location,
                        "label": merged.get("label") or Path(location).name or location[:120],
                        "kind": str(merged.get("kind") or "artifact").strip().lower() or "artifact",
                        "source": "explicit",
                        "run_id": run_id,
                        "updated_at": merged.get("created_at") or updated_at,
                        "created_at": merged.get("created_at") or updated_at,
                        "content_type": merged.get("content_type") or merged.get("mime_type"),
                        "byte_size": merged.get("byte_size"),
                        "step_id": merged.get("step_id"),
                        "step_index": merged.get("step_index"),
                        "step_number": merged.get("step_number"),
                        "machine_id": merged.get("machine_id"),
                        "storage_backend": merged.get("storage_backend"),
                        "storage_bucket": merged.get("storage_bucket"),
                        "storage_region": merged.get("storage_region"),
                        "storage_endpoint": merged.get("storage_endpoint"),
                        "object_key": merged.get("object_key"),
                        "retention": merged.get("retention") if isinstance(merged.get("retention"), dict) else {},
                    }
                )
            return rows
        for nested in value.values():
            rows.extend(_workspace_artifact_payload_rows(nested, run_id=run_id, updated_at=updated_at))
        return rows
    if isinstance(value, list):
        for nested in value:
            rows.extend(_workspace_artifact_payload_rows(nested, run_id=run_id, updated_at=updated_at))
    return rows


def _workspace_artifact_preview_kind(path: Path, media_type: str) -> str:
    extension = path.suffix.lower()
    normalized_media_type = str(media_type or "").strip().lower()
    if extension in WORKSPACE_PREVIEW_IMAGE_EXTENSIONS or normalized_media_type.startswith("image/"):
        return "image"
    if (
        extension in WORKSPACE_PREVIEW_TEXT_EXTENSIONS
        or normalized_media_type.startswith("text/")
        or normalized_media_type in WORKSPACE_PREVIEW_TEXT_MIME_TYPES
    ):
        return "text"
    return "binary"


def _read_workspace_text_preview(path: Path, *, limit: int = 12000) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.read(limit + 1)
    if len(content) <= limit:
        return content
    return content[:limit] + "\n...truncated..."


def _git_repo_root() -> Optional[Path]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(Path.cwd()),
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    raw = str(completed.stdout or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.exists() else None


def _extract_diff_text_from_payload(value: Any, depth: int = 0) -> Optional[str]:
    if depth > 5 or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if "\n" in text and any(marker in text for marker in ["@@", "---", "+++", "diff --git"]):
            return text[:16000]
        return None
    if isinstance(value, list):
        for item in value:
            found = _extract_diff_text_from_payload(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).strip().lower() in AGENT_WORKSPACE_DIFF_TEXT_KEYS:
                found = _extract_diff_text_from_payload(nested, depth + 1)
                if found:
                    return found
        for nested in value.values():
            found = _extract_diff_text_from_payload(nested, depth + 1)
            if found:
                return found
    return None


def _event_mentions_workspace_path(event: Dict[str, Any], normalized_path: str) -> bool:
    target = str(normalized_path or "").strip()
    if not target:
        return False
    data = event.get("data")
    if data is not None:
        for raw_value, _ in _iter_workspace_material_values(data, "event.data"):
            candidate = _normalize_workspace_material_path(raw_value)
            if candidate == target:
                return True
    message = str(event.get("message") or "").strip()
    if target and target in message:
        return True
    return False


def _build_workspace_evidence_rows(snapshot: Dict[str, Any], normalized_path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for event in snapshot.get("events") if isinstance(snapshot.get("events"), list) else []:
        if not isinstance(event, dict):
            continue
        if not _event_mentions_workspace_path(event, normalized_path):
            continue
        data = event.get("data")
        diff_text = _extract_diff_text_from_payload(data)
        rows.append(
            {
                "ts": event.get("ts"),
                "event": event.get("event"),
                "message": event.get("message"),
                "diff_text": diff_text,
                "data_preview": _compact_event_text(json.dumps(_json_safe(data), ensure_ascii=True), limit=1200) if data is not None else None,
            }
        )
        if len(rows) >= 12:
            break
    return rows


def _git_diff_for_workspace_path(normalized_path: str) -> Tuple[str, str]:
    if _workspace_path_is_restricted(normalized_path):
        return "", "Path is not available through workspace APIs."
    repo_root = _git_repo_root()
    if repo_root is None:
        return "", "Git repository unavailable."
    target = (repo_root / normalized_path).resolve()
    try:
        target.relative_to(repo_root.resolve())
    except Exception:
        return "", "Path is outside the repository root."
    if not target.exists():
        return "", "File does not exist in the current repository."

    relative = target.relative_to(repo_root).as_posix()
    try:
        status_proc = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--", relative],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception as exc:
        return "", f"Git status failed: {exc}"
    status_text = str(status_proc.stdout or "").strip()
    if not status_text:
        return "", "No working tree changes for this file."

    is_untracked = status_text.startswith("??")
    try:
        if is_untracked:
            diff_proc = subprocess.run(
                ["git", "-C", str(repo_root), "diff", "--no-index", "--", "/dev/null", str(target)],
                check=False,
                capture_output=True,
                text=True,
                timeout=6,
            )
        else:
            diff_proc = subprocess.run(
                ["git", "-C", str(repo_root), "diff", "--no-ext-diff", "--unified=12", "HEAD", "--", relative],
                check=False,
                capture_output=True,
                text=True,
                timeout=6,
            )
    except Exception as exc:
        return "", f"Git diff failed: {exc}"
    diff_text = str(diff_proc.stdout or "").strip()
    if not diff_text:
        return "", "No diff text available for this file."
    return diff_text[:24000], "current_repo_diff"


def _iter_workspace_material_values(value: Any, key_hint: str = "") -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    if isinstance(value, dict):
        if artifact_service.is_canonical_artifact_payload(value):
            reference = artifact_service.canonical_artifact_reference(value)
            if reference:
                found.append((reference, key_hint or "artifact.uri"))
            return found
        for key, nested in value.items():
            next_hint = f"{key_hint}.{key}" if key_hint else str(key)
            found.extend(_iter_workspace_material_values(nested, next_hint))
        return found
    if isinstance(value, list):
        for idx, nested in enumerate(value):
            next_hint = f"{key_hint}[{idx}]"
            found.extend(_iter_workspace_material_values(nested, next_hint))
        return found
    if isinstance(value, str) and _looks_like_workspace_path(value):
        found.append((value.strip(), key_hint))
    return found


def _workspace_material_kind(value: str, key_hint: str) -> str:
    lowered = str(value or "").strip().lower()
    hint = str(key_hint or "").strip().lower()
    extension = Path(lowered).suffix.lower()
    if "screenshot" in hint or extension in AGENT_WORKSPACE_IMAGE_EXTENSIONS:
        return "image" if "screenshot" not in hint else "screenshot"
    if extension in AGENT_WORKSPACE_DATA_EXTENSIONS or "sheet" in hint or "spreadsheet" in hint:
        return "data"
    if extension in AGENT_WORKSPACE_DOC_EXTENSIONS or any(token in hint for token in ["report", "brief", "summary"]):
        return "report"
    if lowered.startswith(("http://", "https://")):
        return "link"
    return "file"


def _workspace_material_source(key_hint: str) -> str:
    lowered = str(key_hint or "").strip().lower()
    return "explicit" if any(token in lowered for token in AGENT_WORKSPACE_PATH_HINTS) else "inferred"


def _append_workspace_material(
    bucket: List[Dict[str, Any]],
    seen: Set[Tuple[str, str]],
    *,
    identifier: str,
    label: str,
    kind: str,
    source: str,
    run_id: str,
    updated_at: Optional[str],
    path_key: str,
) -> Optional[Dict[str, Any]]:
    key = (run_id, identifier)
    if key in seen:
        return None
    seen.add(key)
    item = {
        path_key: identifier,
        "label": label,
        "kind": kind,
        "source": source,
        "run_id": run_id,
        "updated_at": updated_at,
    }
    bucket.append(item)
    return item


def _collect_workspace_materials_for_snapshot(
    snapshot: Dict[str, Any],
    *,
    files: List[Dict[str, Any]],
    artifacts: List[Dict[str, Any]],
    seen_files: Set[Tuple[str, str]],
    seen_artifacts: Set[Tuple[str, str]],
    file_limit: int,
    artifact_limit: int,
) -> None:
    run_id = str(snapshot.get("run_id") or "").strip()
    updated_at = snapshot.get("updated_at") or snapshot.get("completed_at") or snapshot.get("created_at")
    sources = [
        snapshot.get("result_data"),
        snapshot.get("events"),
    ]
    for payload in sources:
        if payload is None:
            continue
        for artifact_row in _workspace_artifact_payload_rows(payload, run_id=run_id, updated_at=updated_at):
            if len(artifacts) >= artifact_limit:
                break
            identifier = str(artifact_row.get("uri_or_path") or "").strip()
            if not identifier:
                continue
            artifact_payload = dict(artifact_row)
            artifact_payload["label"] = artifact_row.get("label") or Path(identifier).name or identifier[:120]
            artifact_payload["kind"] = artifact_row.get("kind") or "artifact"
            artifact_payload["source"] = artifact_row.get("source") or "explicit"
            artifact_payload["run_id"] = run_id
            artifact_payload["updated_at"] = artifact_row.get("updated_at") or updated_at
            appended = _append_workspace_material(
                artifacts,
                seen_artifacts,
                identifier=identifier,
                label=str(artifact_payload.get("label") or ""),
                kind=str(artifact_payload.get("kind") or "artifact"),
                source=str(artifact_payload.get("source") or "explicit"),
                run_id=run_id,
                updated_at=artifact_payload.get("updated_at"),
                path_key="uri_or_path",
            )
            if appended is not None:
                appended.update(
                    {
                        "artifact_id": artifact_payload.get("artifact_id"),
                        "content_type": artifact_payload.get("content_type"),
                        "byte_size": artifact_payload.get("byte_size"),
                        "created_at": artifact_payload.get("created_at"),
                        "step_id": artifact_payload.get("step_id"),
                        "step_index": artifact_payload.get("step_index"),
                        "step_number": artifact_payload.get("step_number"),
                        "machine_id": artifact_payload.get("machine_id"),
                        "storage_backend": artifact_payload.get("storage_backend"),
                        "storage_bucket": artifact_payload.get("storage_bucket"),
                        "storage_region": artifact_payload.get("storage_region"),
                        "storage_endpoint": artifact_payload.get("storage_endpoint"),
                        "object_key": artifact_payload.get("object_key"),
                        "retention": artifact_payload.get("retention") if isinstance(artifact_payload.get("retention"), dict) else {},
                    }
                )
        for raw_value, key_hint in _iter_workspace_material_values(payload):
            normalized_value = _normalize_workspace_material_path(str(raw_value or ""))
            if not normalized_value:
                continue
            label = Path(str(normalized_value or "").strip()).name or str(normalized_value or "").strip()[:120]
            kind = _workspace_material_kind(raw_value, key_hint)
            source = _workspace_material_source(key_hint)
            if kind == "file":
                if len(files) >= file_limit:
                    continue
                _append_workspace_material(
                    files,
                    seen_files,
                    identifier=str(normalized_value),
                    label=label,
                    kind=kind,
                    source=source,
                    run_id=run_id,
                    updated_at=updated_at,
                    path_key="path",
                )
            else:
                if len(artifacts) >= artifact_limit:
                    continue
                _append_workspace_material(
                    artifacts,
                    seen_artifacts,
                    identifier=str(normalized_value),
                    label=label,
                    kind=kind,
                    source=source,
                    run_id=run_id,
                    updated_at=updated_at,
                    path_key="uri_or_path",
                )


def _builder_file_activity_from_snapshot(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    run_id = str(snapshot.get("run_id") or "").strip()
    updated_at = snapshot.get("updated_at") or snapshot.get("completed_at") or snapshot.get("created_at")
    activity_by_path: Dict[str, Dict[str, Any]] = {}
    payload_sources = [
        ("result_data", snapshot.get("result_data")),
        ("events", snapshot.get("events")),
        (
            "pack_inputs",
            (
                snapshot.get("context", {}).get("metadata", {}).get("pack_inputs")
                if isinstance(snapshot.get("context"), dict)
                and isinstance(snapshot.get("context", {}).get("metadata"), dict)
                else None
            ),
        ),
    ]

    def record_path(raw_value: str, key_hint: str, event_name: str = "") -> None:
        normalized = _normalize_workspace_material_path(raw_value)
        if not normalized or normalized.startswith(("http://", "https://")):
            return
        extension = Path(normalized).suffix.lower()
        if extension and extension not in AGENT_WORKSPACE_BUILDER_REPO_EXTENSIONS:
            return
        if any(normalized.startswith(prefix) for prefix in AGENT_WORKSPACE_IGNORED_PATH_PREFIXES):
            return
        area = _workspace_repo_area(normalized)
        source = _workspace_material_source(key_hint)
        current = activity_by_path.get(normalized)
        if current is None:
            current = {
                "path": normalized,
                "label": Path(normalized).name or normalized,
                "kind": "file",
                "source": source,
                "run_id": run_id,
                "last_run_id": run_id,
                "updated_at": updated_at,
                "area": area,
                "extension": extension or None,
                "touch_count": 0,
                "run_count": 0,
                "last_event": event_name or None,
                "_run_ids": set(),
            }
            activity_by_path[normalized] = current
        current["touch_count"] = int(current.get("touch_count") or 0) + 1
        run_ids = current.get("_run_ids")
        if isinstance(run_ids, set) and run_id:
            run_ids.add(run_id)
            current["run_count"] = len(run_ids)
        if source == "explicit":
            current["source"] = "explicit"
        if event_name:
            current["last_event"] = event_name
        current["last_run_id"] = run_id or current.get("last_run_id")
        candidate_ts = _parse_utc_ts(updated_at)
        current_ts = _parse_utc_ts(current.get("updated_at"))
        if candidate_ts and (current_ts is None or candidate_ts >= current_ts):
            current["updated_at"] = updated_at

    for source_name, payload in payload_sources:
        if payload is None:
            continue
        if source_name == "events" and isinstance(payload, list):
            for event in payload:
                if not isinstance(event, dict):
                    continue
                event_name = str(event.get("event") or event.get("message") or "").strip().lower()
                data = event.get("data")
                if data is None:
                    continue
                for raw_value, key_hint in _iter_workspace_material_values(data, "event.data"):
                    record_path(raw_value, key_hint, event_name)
            continue
        for raw_value, key_hint in _iter_workspace_material_values(payload, source_name):
            record_path(raw_value, key_hint, source_name)

    rows: List[Dict[str, Any]] = []
    for item in activity_by_path.values():
        run_ids = item.pop("_run_ids", None)
        if isinstance(run_ids, set):
            item["run_count"] = len(run_ids)
        rows.append(item)
    rows.sort(
        key=lambda item: (
            -int(item.get("touch_count") or 0),
            str(item.get("updated_at") or ""),
            str(item.get("path") or ""),
        )
    )
    return rows


def _agent_workspace_channel_bindings(
    *,
    agent_role: str,
    workspace_id: Optional[str],
    telegram_payload: Dict[str, Any],
    whatsapp_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    allowed = AGENT_WORKSPACE_CHANNELS.get(agent_role, set())
    if not allowed:
        return []
    connectors = list_vault_connectors(workspace_id)
    connector_channel_map = {
        "telegram_bot": "telegram",
        "whatsapp_twilio": "whatsapp",
        "google_workspace": "email",
        "microsoft_365": "email",
        "discord_bot": "discord",
        "instagram_business": "instagram",
    }
    bindings: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for connector in connectors:
        provider = str(connector.get("connector") or "").strip().lower()
        channel = connector_channel_map.get(provider)
        if not channel or channel not in allowed:
            continue
        metadata = connector.get("metadata") if isinstance(connector.get("metadata"), dict) else {}
        assigned_role = _normalize_agent_role(metadata.get("agent_role"))
        paused = bool(metadata.get("paused"))
        if assigned_role and assigned_role != agent_role:
            continue
        if agent_role == "orchestrator" and assigned_role:
            continue
        identity_value = (
            str(metadata.get("chat_id") or "").strip()
            or str(metadata.get("bot_username") or "").strip()
            or str(metadata.get("from_number") or "").strip()
            or str(metadata.get("phone_number") or "").strip()
            or str(metadata.get("emailAddress") or "").strip()
            or str(metadata.get("calendar_id") or "").strip()
        )
        if not identity_value:
            try:
                secret = resolve_vault_credential(
                    str(connector.get("id") or "").strip(),
                    _normalize_workspace_id(connector.get("workspace_id")),
                    connector_id=provider,
                    tool_name="workspace_channel_identity",
                    purpose="channel_identity_projection",
                    actor_type="control_plane",
                    allowed_fields=[
                        "chat_id",
                        "bot_username",
                        "from_number",
                        "phone_number",
                        "emailAddress",
                        "calendar_id",
                    ],
                )
            except Exception:
                secret = {}
            identity_value = (
                str(secret.get("chat_id") or "").strip()
                or str(secret.get("bot_username") or "").strip()
                or str(secret.get("from_number") or "").strip()
                or str(secret.get("phone_number") or "").strip()
                or str(secret.get("emailAddress") or "").strip()
                or str(secret.get("calendar_id") or "").strip()
            )
        if assigned_role:
            mode = "assigned"
        else:
            mode = "dedicated" if identity_value else "shared"
        if paused:
            status = "paused"
        elif channel == "telegram":
            status = "active" if bool((telegram_payload.get("autopilot") or {}).get("active")) else "idle"
            if str((telegram_payload.get("autopilot") or {}).get("last_error") or "").strip():
                status = "attention"
        elif channel == "whatsapp":
            status = "active" if bool((whatsapp_payload.get("autopilot") or {}).get("active")) else "idle"
            if str((whatsapp_payload.get("autopilot") or {}).get("last_error") or "").strip():
                status = "attention"
        else:
            status = "active"
        key = (channel, str(connector.get("id") or ""))
        if key in seen:
            continue
        seen.add(key)
        bindings.append(
            {
                "connector_id": str(connector.get("id") or "").strip() or None,
                "channel": channel,
                "connector": provider,
                "label": connector.get("label"),
                "mode": mode,
                "identity_label": identity_value or None,
                "routing_scope": AGENT_WORKSPACE_LABELS.get(assigned_role, assigned_role) if assigned_role else "Shared workspace",
                "agent_role": assigned_role or None,
                "status": status,
                "updated_at": connector.get("updated_at") or connector.get("created_at"),
            }
        )
    return bindings


def _agent_workspace_status(
    *,
    agent_role: str,
    active_count: int,
    queued_count: int,
    claimed_count: int,
    pending_approvals: int,
    runtime_ok: bool,
    memory_ready: bool,
    worker_summary: Dict[str, Any],
    telegram_payload: Dict[str, Any],
    whatsapp_payload: Dict[str, Any],
    channel_bindings: List[Dict[str, Any]],
) -> Tuple[str, str]:
    total_open = active_count + queued_count + claimed_count
    if agent_role == "orchestrator":
        if not runtime_ok:
            return "attention", "runtime needs attention"
        if pending_approvals > 0:
            return "attention", f"{pending_approvals} approvals waiting"
        if total_open > 0:
            return "busy", f"{total_open} runs in flight"
        return "active", "routing and approvals are ready"
    if agent_role == "research" and not memory_ready:
        return "attention", "memory is degraded"
    if agent_role == "builder":
        if total_open > 0 or int(worker_summary.get("busy") or 0) > 0:
            return "busy", f"{max(total_open, int(worker_summary.get('busy') or 0))} builder workloads open"
        if int(worker_summary.get("online") or 0) > 0:
            return "active", f"{int(worker_summary.get('online') or 0)} workers online"
        return "offline", "no local workers online"
    if agent_role in {"support", "sales", "private-assistant"}:
        channel_errors = [
            str((telegram_payload.get("autopilot") or {}).get("last_error") or "").strip(),
            str((whatsapp_payload.get("autopilot") or {}).get("last_error") or "").strip(),
        ]
        if any(channel_errors):
            return "attention", "channel routing has errors"
    if total_open > 0:
        return "busy", f"{total_open} runs in progress"
    if channel_bindings:
        return "active", f"{len(channel_bindings)} channel bindings ready"
    return "idle", "standing by"


def _list_workspace_live_runs_bounded(
    *,
    workspace_id: Optional[str] = None,
    states: Optional[Set[str]] = None,
    page_size: int = 200,
) -> List[Dict[str, Any]]:
    normalized_states = sorted(
        {
            str(state or "").strip().lower()
            for state in (states or set())
            if str(state or "").strip()
        }
    )
    items: List[Dict[str, Any]] = []
    offset = 0
    safe_page_size = max(1, min(int(page_size), 500))
    while True:
        page = run_state_repository.sync_list_live_runs_page(
            limit=safe_page_size,
            offset=offset,
            workspace_id=workspace_id,
            states=normalized_states or None,
        )
        if not page:
            break
        items.extend(item for item in page if isinstance(item, dict))
        if len(page) < safe_page_size:
            break
        offset += safe_page_size
    return items


def _list_workspace_pending_approvals_bounded(
    *,
    workspace_id: Optional[str] = None,
    page_size: int = 200,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    offset = 0
    safe_page_size = max(1, min(int(page_size), 500))
    while True:
        page = run_state_repository.sync_list_pending_approvals_page(
            limit=safe_page_size,
            offset=offset,
            workspace_id=workspace_id,
        )
        if not page:
            break
        items.extend(item for item in page if isinstance(item, dict))
        if len(page) < safe_page_size:
            break
        offset += safe_page_size
    return items


def register_agent_workspace_routes(app) -> None:
    import server as _server

    module_globals = globals()
    module_globals.update(_server.__dict__)

    @app.get("/agents/workspace/snapshot", dependencies=[Depends(require_admin_api_key)])
    async def get_agents_workspace_snapshot(
        workspace_id: Optional[str] = None,
        history_limit: int = 30,
        audit_limit: int = 80,
        current_user=Depends(require_admin_api_key),
    ):
        _refresh_server_exports()
        safe_history_limit = max(1, min(int(history_limit), 200))
        safe_audit_limit = max(1, min(int(audit_limit), 300))
        workspace_filter = _normalize_workspace_id(enforce_workspace_access(current_user, workspace_id)) if workspace_id else None

        workers_payload = handle_get_local_workers_status()
        queue_payload = handle_get_local_run_queue(workspace_id=workspace_filter, limit=120)
        telegram_payload = await handle_telegram_autopilot_status()
        whatsapp_payload = await handle_whatsapp_autopilot_status()
        metrics_payload = await _late_server_export("get_runtime_metrics")()
        schedules_payload = await _late_server_export("list_schedules")(workspace_id=workspace_filter)
        memory_snapshot = _memory_health_snapshot()

        active_statuses = {"starting", "running", "running_local", "queued_local", "waiting_for_input"}
        active_runs: List[Dict[str, Any]] = []
        pending_approvals: List[Dict[str, Any]] = []
        run_ids_for_workspace: Set[str] = set()
        live_runs = _list_workspace_live_runs_bounded(
            workspace_id=workspace_filter,
            states=active_statuses,
        )

        for run in live_runs:
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                continue
            context = run.get("context") if isinstance(run.get("context"), dict) else {}
            metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
            run_workspace = _normalize_workspace_id(context.get("workspace_id"))
            if workspace_filter and run_workspace != workspace_filter:
                continue
            run_ids_for_workspace.add(run_id)
            status = str(run.get("status") or "").strip().lower()
            if status in active_statuses:
                active_runs.append(
                    {
                        "run_id": run_id,
                        "status": status,
                        "engine": run.get("engine"),
                        "workspace_id": run_workspace,
                        "user_goal": str(context.get("user_goal") or ""),
                        "agent_role": str(metadata.get("agent_role") or "").strip() or None,
                        "agent_role_source": str(metadata.get("agent_role_source") or "").strip() or None,
                        "created_at": run.get("created_at"),
                        "updated_at": run.get("updated_at"),
                    }
                )

        for approval in _list_workspace_pending_approvals_bounded(workspace_id=workspace_filter):
            run_id = str(approval.get("run_id") or "").strip()
            if run_id:
                run_ids_for_workspace.add(run_id)
            pending_approvals.append(
                {
                    "run_id": run_id,
                    "approval_id": str(approval.get("approval_id") or "").strip(),
                    "prompt": str(approval.get("prompt") or "Approval required."),
                    "status": str(approval.get("status") or "pending").strip().lower() or "pending",
                    "agent_role": str(approval.get("agent_role") or "").strip() or None,
                    "requested_at": approval.get("requested_at"),
                    "expires_at": approval.get("expires_at"),
                    "correlation_id": approval.get("correlation_id"),
                }
            )

        with RUN_HISTORY_LOCK:
            history_items_all = list(RUN_HISTORY)
        history_filtered_all = [
            item for item in history_items_all if _history_item_matches(item, workspace_filter, None, None)
        ]
        for item in history_filtered_all:
            run_id = str(item.get("run_id") or "").strip()
            if run_id:
                run_ids_for_workspace.add(run_id)
        history_recent = [_summarize_history_item(item) for item in history_filtered_all[:safe_history_limit]]

        with APPROVAL_AUDIT_LOCK:
            audit_items = list(APPROVAL_AUDIT)
        audit_filtered: List[Dict[str, Any]] = []
        for item in audit_items:
            if not isinstance(item, dict):
                continue
            run_id_value = str(item.get("run_id") or "").strip()
            if workspace_filter and run_id_value and run_id_value not in run_ids_for_workspace:
                continue
            audit_filtered.append(
                {
                    "id": item.get("id"),
                    "ts": item.get("ts"),
                    "stage": item.get("stage"),
                    "decision": item.get("decision"),
                    "actor": item.get("actor"),
                    "source": item.get("source"),
                    "run_id": item.get("run_id"),
                    "approval_id": item.get("approval_id"),
                    "correlation_id": item.get("correlation_id"),
                    "note": item.get("note"),
                }
            )
            if len(audit_filtered) >= safe_audit_limit:
                break

        ops_daemon_policy = {
            "enabled": os.getenv("ORION_OPS_DAEMON_ENABLED", "0") == "1",
            "autorecover_enabled": os.getenv("ORION_OPS_DAEMON_AUTORECOVER", "1") == "1",
            "poll_seconds": max(2.0, min(float(os.getenv("ORION_OPS_DAEMON_HEALTH_POLL_SECONDS", "8")), 120.0)),
            "failure_threshold": max(1, min(int(os.getenv("ORION_OPS_DAEMON_FAILURE_THRESHOLD", "2")), 20)),
            "cooldown_seconds": max(5, min(int(os.getenv("ORION_OPS_DAEMON_RECOVERY_COOLDOWN_SECONDS", "45")), 3600)),
            "max_recoveries_per_hour": max(1, min(int(os.getenv("ORION_OPS_DAEMON_MAX_RECOVERIES_PER_HOUR", "8")), 100)),
            "require_worker": os.getenv("ORION_OPS_DAEMON_REQUIRE_WORKER", "1") == "1",
            "require_telegram": os.getenv("ORION_OPS_DAEMON_REQUIRE_TELEGRAM", "1") == "1",
        }

        runs_metrics = metrics_payload.get("runs") if isinstance(metrics_payload.get("runs"), dict) else {}
        kpis_metrics = metrics_payload.get("kpis") if isinstance(metrics_payload.get("kpis"), dict) else {}
        schedule_items = schedules_payload.get("items") if isinstance(schedules_payload.get("items"), list) else []
        active_schedule_items = [item for item in schedule_items if isinstance(item, dict) and bool(item.get("enabled"))]

        return {
            "ok": True,
            "workspace_id": workspace_filter or "all",
            "updated_at": _utc_now_iso(),
            "runtime": {
                "ok": not ORION_ENGINE_VALIDATION_ERRORS,
                "auth_mode": ORION_AUTH_MODE,
                "memory_ready": bool(memory_snapshot.get("manager_ready")),
                "errors": list(ORION_ENGINE_VALIDATION_ERRORS),
                "single_agent_mode": ORION_SINGLE_AGENT_MODE,
                "single_agent_role": ORION_SINGLE_AGENT_ROLE,
            },
            "kpis": {
                "runs_started": int(runs_metrics.get("started") or 0),
                "runs_completed": int(runs_metrics.get("completed") or 0),
                "runs_failed": int(runs_metrics.get("failed") or 0),
                "runs_timeout": int(runs_metrics.get("timeout") or 0),
                "active_runs": int(runs_metrics.get("active") or 0),
                "waiting_for_input": int(runs_metrics.get("waiting_for_input") or 0),
                "completion_rate": float(runs_metrics.get("completion_rate") or 0.0),
                "avg_run_duration_ms": float(kpis_metrics.get("avg_run_duration_ms") or 0.0),
                "avg_time_to_first_value_ms": float(kpis_metrics.get("avg_time_to_first_value_ms") or 0.0),
                "avg_human_wait_ms": float(kpis_metrics.get("avg_human_wait_ms") or 0.0),
            },
            "live": {
                "workers": workers_payload,
                "telegram": telegram_payload,
                "whatsapp": whatsapp_payload,
                "queue": {
                    "queued_count": int(queue_payload.get("queued_count") or 0),
                    "claimed_count": int(queue_payload.get("claimed_count") or 0),
                },
            },
            "tasks": {
                "active_runs": active_runs,
                "queued_runs": queue_payload.get("queued_runs") if isinstance(queue_payload.get("queued_runs"), list) else [],
                "claimed_runs": queue_payload.get("claimed_runs") if isinstance(queue_payload.get("claimed_runs"), list) else [],
                "recent_runs": history_recent,
            },
            "schedules": {
                "active_count": len(active_schedule_items),
                "items": active_schedule_items[:8],
            },
            "approvals": {
                "pending": pending_approvals,
                "audit": audit_filtered,
            },
            "policy": {
                "scaling": ops_daemon_policy,
            },
        }

    @app.get("/agents/workspace/agents/{agent_role}", dependencies=[Depends(require_admin_api_key)])
    async def get_agent_workspace_detail(
        agent_role: str,
        workspace_id: Optional[str] = None,
        history_limit: int = 24,
        file_limit: int = 40,
        artifact_limit: int = 40,
        current_user=Depends(require_admin_api_key),
    ):
        target_role = _normalize_agent_role(agent_role)
        if not target_role:
            raise HTTPException(status_code=400, detail="Unknown agent role.")
        if ORION_SINGLE_AGENT_MODE and target_role != ORION_SINGLE_AGENT_ROLE:
            raise HTTPException(status_code=400, detail="Single-agent mode is enabled. Only the orchestrator workspace is available.")

        workspace_filter = _normalize_workspace_id(enforce_workspace_access(current_user, workspace_id)) if workspace_id else None
        safe_history_limit = max(1, min(int(history_limit), 100))
        safe_file_limit = max(1, min(int(file_limit), 120))
        safe_artifact_limit = max(1, min(int(artifact_limit), 120))

        workers_payload = handle_get_local_workers_status()
        queue_payload = handle_get_local_run_queue(workspace_id=workspace_filter, limit=120)
        telegram_payload = await handle_telegram_autopilot_status()
        whatsapp_payload = await handle_whatsapp_autopilot_status()
        memory_snapshot = _memory_health_snapshot()

        worker_summary = workers_payload.get("summary") if isinstance(workers_payload.get("summary"), dict) else {}
        active_statuses = {"starting", "running", "running_local", "queued_local", "waiting_for_input"}

        active_runs: List[Dict[str, Any]] = []
        pending_approvals: List[Dict[str, Any]] = []
        scoped_run_ids: Set[str] = set()
        live_runs = _list_workspace_live_runs_bounded(
            workspace_id=workspace_filter,
            states=active_statuses,
        )

        for run in live_runs:
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                continue
            snapshot = _hydrate_workspace_item_role(_serialize_run_snapshot(run_id, run))
            run_workspace = _normalize_workspace_id(snapshot.get("workspace_id"))
            if workspace_filter and run_workspace != workspace_filter:
                continue
            resolved_role = str(snapshot.get("agent_role") or "").strip().lower()
            if target_role != "orchestrator" and resolved_role != target_role:
                continue
            scoped_run_ids.add(run_id)
            status = str(snapshot.get("status") or "").strip().lower()
            if status in active_statuses:
                active_runs.append(_summarize_history_item(snapshot))
        for approval in _list_workspace_pending_approvals_bounded(workspace_id=workspace_filter):
            run_id = str(approval.get("run_id") or "").strip()
            approval_role = str(approval.get("agent_role") or "").strip().lower()
            if target_role != "orchestrator" and approval_role != target_role:
                continue
            if run_id:
                scoped_run_ids.add(run_id)
            metadata = approval.get("metadata") if isinstance(approval.get("metadata"), dict) else {}
            pending_approvals.append(
                {
                    "run_id": run_id,
                    "approval_id": str(approval.get("approval_id") or "").strip(),
                    "prompt": str(approval.get("prompt") or "Approval required."),
                    "status": str(approval.get("status") or "pending").strip().lower() or "pending",
                    "agent_role": approval_role or None,
                    "agent_role_source": str(metadata.get("agent_role_source") or "").strip() or None,
                    "requested_at": approval.get("requested_at"),
                    "expires_at": approval.get("expires_at"),
                    "correlation_id": approval.get("correlation_id"),
                }
            )

        queued_runs_all = queue_payload.get("queued_runs") if isinstance(queue_payload.get("queued_runs"), list) else []
        claimed_runs_all = queue_payload.get("claimed_runs") if isinstance(queue_payload.get("claimed_runs"), list) else []
        queued_runs: List[Dict[str, Any]] = []
        claimed_runs: List[Dict[str, Any]] = []
        for item in queued_runs_all:
            if not isinstance(item, dict):
                continue
            hydrated = _hydrate_workspace_item_role(item)
            if target_role != "orchestrator" and str(hydrated.get("agent_role") or "").strip().lower() != target_role:
                continue
            if str(hydrated.get("run_id") or "").strip():
                scoped_run_ids.add(str(hydrated.get("run_id") or "").strip())
            queued_runs.append(hydrated)
        for item in claimed_runs_all:
            if not isinstance(item, dict):
                continue
            hydrated = _hydrate_workspace_item_role(item)
            if target_role != "orchestrator" and str(hydrated.get("agent_role") or "").strip().lower() != target_role:
                continue
            if str(hydrated.get("run_id") or "").strip():
                scoped_run_ids.add(str(hydrated.get("run_id") or "").strip())
            claimed_runs.append(hydrated)

        with RUN_HISTORY_LOCK:
            history_items_all = list(RUN_HISTORY)
        history_items: List[Dict[str, Any]] = []
        for item in history_items_all:
            if not isinstance(item, dict):
                continue
            if workspace_filter and str(item.get("workspace_id") or "").strip() != workspace_filter:
                continue
            hydrated = _hydrate_workspace_item_role(item)
            if target_role != "orchestrator" and str(hydrated.get("agent_role") or "").strip().lower() != target_role:
                continue
            history_items.append(_summarize_history_item(hydrated))
            run_id_value = str(hydrated.get("run_id") or "").strip()
            if run_id_value:
                scoped_run_ids.add(run_id_value)
            if len(history_items) >= safe_history_limit:
                break

        relation_sources: List[Dict[str, Any]] = []
        relation_sources.extend(active_runs)
        relation_sources.extend(queued_runs)
        relation_sources.extend(claimed_runs)
        relation_sources.extend(history_items)
        scoped_child_counts: Dict[str, int] = {}
        for item in relation_sources:
            if not isinstance(item, dict):
                continue
            parent_run_id = _normalize_run_id_token(item.get("parent_run_id"))
            if parent_run_id:
                scoped_child_counts[parent_run_id] = scoped_child_counts.get(parent_run_id, 0) + 1
        for collection in (active_runs, queued_runs, claimed_runs, history_items):
            for item in collection:
                if not isinstance(item, dict):
                    continue
                run_id_value = str(item.get("run_id") or "").strip()
                item["child_run_count"] = scoped_child_counts.get(run_id_value, 0)

        with APPROVAL_AUDIT_LOCK:
            audit_items_all = list(APPROVAL_AUDIT)
        audit_items: List[Dict[str, Any]] = []
        for item in audit_items_all:
            if not isinstance(item, dict):
                continue
            run_id_value = str(item.get("run_id") or "").strip()
            if workspace_filter and run_id_value and run_id_value not in scoped_run_ids:
                continue
            if target_role != "orchestrator" and run_id_value and run_id_value not in scoped_run_ids:
                continue
            if target_role != "orchestrator" and not run_id_value:
                continue
            audit_items.append(
                {
                    "id": item.get("id"),
                    "ts": item.get("ts"),
                    "stage": item.get("stage"),
                    "decision": item.get("decision"),
                    "actor": item.get("actor"),
                    "source": item.get("source"),
                    "run_id": item.get("run_id"),
                    "approval_id": item.get("approval_id"),
                    "correlation_id": item.get("correlation_id"),
                    "note": item.get("note"),
                }
            )
            if len(audit_items) >= max(60, safe_history_limit * 3):
                break

        files: List[Dict[str, Any]] = []
        artifacts: List[Dict[str, Any]] = []
        seen_files: Set[Tuple[str, str]] = set()
        seen_artifacts: Set[Tuple[str, str]] = set()
        builder_activity: Dict[str, Dict[str, Any]] = {}
        material_run_ids = list(scoped_run_ids)[: max(safe_file_limit, safe_artifact_limit, safe_history_limit)]
        for run_id_value in material_run_ids:
            try:
                replay_snapshot = _get_replay_payload(run_id_value)
            except HTTPException:
                continue
            if target_role == "builder":
                for entry in _builder_file_activity_from_snapshot(replay_snapshot):
                    path_value = str(entry.get("path") or "").strip()
                    if not path_value:
                        continue
                    existing = builder_activity.get(path_value)
                    if existing is None:
                        builder_activity[path_value] = dict(entry)
                        continue
                    existing["touch_count"] = int(existing.get("touch_count") or 0) + int(entry.get("touch_count") or 0)
                    existing["run_count"] = int(existing.get("run_count") or 0) + int(entry.get("run_count") or 0)
                    existing["source"] = "explicit" if entry.get("source") == "explicit" or existing.get("source") == "explicit" else existing.get("source")
                    candidate_ts = _parse_utc_ts(entry.get("updated_at"))
                    current_ts = _parse_utc_ts(existing.get("updated_at"))
                    if candidate_ts and (current_ts is None or candidate_ts >= current_ts):
                        existing["updated_at"] = entry.get("updated_at")
                        existing["last_event"] = entry.get("last_event") or existing.get("last_event")
                        existing["last_run_id"] = entry.get("last_run_id") or existing.get("last_run_id")
            _collect_workspace_materials_for_snapshot(
                replay_snapshot,
                files=files,
                artifacts=artifacts,
                seen_files=seen_files,
                seen_artifacts=seen_artifacts,
                file_limit=safe_file_limit,
                artifact_limit=safe_artifact_limit,
            )
            if target_role == "builder":
                if len(builder_activity) >= safe_file_limit and len(artifacts) >= safe_artifact_limit:
                    break
            elif len(files) >= safe_file_limit and len(artifacts) >= safe_artifact_limit:
                break

        if target_role == "builder" and builder_activity:
            files = sorted(
                builder_activity.values(),
                key=lambda item: (
                    str(item.get("area") or ""),
                    -int(item.get("touch_count") or 0),
                    str(item.get("path") or ""),
                ),
            )[:safe_file_limit]

        channel_bindings = _agent_workspace_channel_bindings(
            agent_role=target_role,
            workspace_id=workspace_filter,
            telegram_payload=telegram_payload,
            whatsapp_payload=whatsapp_payload,
        )

        status, status_reason = _agent_workspace_status(
            agent_role=target_role,
            active_count=len(active_runs),
            queued_count=len(queued_runs),
            claimed_count=len(claimed_runs),
            pending_approvals=len(pending_approvals),
            runtime_ok=not ORION_ENGINE_VALIDATION_ERRORS,
            memory_ready=bool(memory_snapshot.get("manager_ready")),
            worker_summary=worker_summary,
            telegram_payload=telegram_payload,
            whatsapp_payload=whatsapp_payload,
            channel_bindings=channel_bindings,
        )

        return {
            "ok": True,
            "workspace_id": workspace_filter or "default",
            "updated_at": _utc_now_iso(),
            "agent": {
                "id": target_role,
                "label": AGENT_WORKSPACE_LABELS.get(target_role, target_role),
                "role_summary": AGENT_WORKSPACE_SUMMARIES.get(target_role, ""),
                "status": status,
                "status_reason": status_reason,
            },
            "overview": {
                "active_runs": len(active_runs),
                "queued_runs": len(queued_runs),
                "claimed_runs": len(claimed_runs),
                "history_items": len(history_items),
                "pending_approvals": len(pending_approvals),
                "files": len(files),
                "artifacts": len(artifacts),
                "channels": len(channel_bindings),
                "last_completed_run_id": history_items[0].get("run_id") if history_items else None,
            },
            "work": {
                "active_runs": active_runs,
                "queued_runs": queued_runs,
                "claimed_runs": claimed_runs,
            },
            "history": history_items,
            "files": files,
            "artifacts": artifacts,
            "channels": channel_bindings,
            "approvals": {
                "pending": pending_approvals,
                "audit": audit_items,
            },
        }

    async def _workspace_artifacts_payload(
        workspace_id: Optional[str] = None,
        history_limit: int = 80,
        limit: int = 120,
        current_user: Any = None,
    ):
        workspace_filter = _normalize_workspace_id(enforce_workspace_access(current_user, workspace_id)) if workspace_id else None
        entitlement_cache: Dict[str, Dict[str, Any]] = {}
        if workspace_filter:
            _ensure_artifacts_access_for_workspace(workspace_filter)
        safe_history_limit = max(1, min(int(history_limit), 160))
        safe_limit = max(1, min(int(limit), 240))

        snapshots_by_run: Dict[str, Dict[str, Any]] = {}
        ordered_run_ids: List[str] = []

        def remember_snapshot(snapshot: Dict[str, Any]) -> None:
            run_id = str(snapshot.get("run_id") or "").strip()
            if not run_id:
                return
            if run_id in snapshots_by_run:
                return
            snapshots_by_run[run_id] = snapshot
            ordered_run_ids.append(run_id)

        with RUN_HISTORY_LOCK:
            history_items_all = list(RUN_HISTORY)
        for item in history_items_all:
            if not isinstance(item, dict):
                continue
            item_workspace_id = _normalize_workspace_id(item.get("workspace_id"))
            if workspace_filter and item_workspace_id != workspace_filter:
                continue
            item_entitlements = _workspace_entitlement_payload(entitlement_cache, item_workspace_id)
            item_capabilities = item_entitlements.get("capabilities") if isinstance(item_entitlements.get("capabilities"), dict) else {}
            if not bool(item_capabilities.get("artifacts_enabled")):
                continue
            event_ts = _payload_timestamp_seconds(item, "updated_at", "completed_at", "created_at")
            if event_ts is not None and event_ts < _workspace_history_cutoff_ts(entitlement_cache, item_workspace_id):
                continue
            remember_snapshot(_hydrate_workspace_item_role(dict(item)))
            if len(ordered_run_ids) >= safe_history_limit:
                break

        active_statuses = {"starting", "running", "running_local", "queued_local", "waiting_for_input"}
        for run in _list_workspace_live_runs_bounded(workspace_id=workspace_filter, states=active_statuses):
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                continue
            snapshot = _hydrate_workspace_item_role(_serialize_run_snapshot(run_id, run))
            snapshot_workspace_id = _normalize_workspace_id(snapshot.get("workspace_id"))
            if workspace_filter and snapshot_workspace_id != workspace_filter:
                continue
            snapshot_entitlements = _workspace_entitlement_payload(entitlement_cache, snapshot_workspace_id)
            snapshot_capabilities = snapshot_entitlements.get("capabilities") if isinstance(snapshot_entitlements.get("capabilities"), dict) else {}
            if not bool(snapshot_capabilities.get("artifacts_enabled")):
                continue
            event_ts = _payload_timestamp_seconds(snapshot, "updated_at", "completed_at", "created_at")
            if event_ts is not None and event_ts < _workspace_history_cutoff_ts(entitlement_cache, snapshot_workspace_id):
                continue
            if str(snapshot.get("status") or "").strip().lower() not in active_statuses:
                continue
            remember_snapshot(snapshot)
            if len(ordered_run_ids) >= safe_history_limit:
                break

        files: List[Dict[str, Any]] = []
        artifacts: List[Dict[str, Any]] = []
        seen_files: Set[Tuple[str, str]] = set()
        seen_artifacts: Set[Tuple[str, str]] = set()
        for run_id in ordered_run_ids[:safe_history_limit]:
            try:
                replay_snapshot = _get_replay_payload(run_id)
            except HTTPException:
                continue
            _collect_workspace_materials_for_snapshot(
                replay_snapshot,
                files=files,
                artifacts=artifacts,
                seen_files=seen_files,
                seen_artifacts=seen_artifacts,
                file_limit=safe_limit,
                artifact_limit=safe_limit,
            )
            if len(files) >= safe_limit and len(artifacts) >= safe_limit:
                break

        combined_items: List[Dict[str, Any]] = []
        summary = {
            "total": 0,
            "screenshots": 0,
            "reports": 0,
            "data": 0,
            "links": 0,
            "files": 0,
        }

        def append_material(item: Dict[str, Any], path_key: str) -> None:
            run_id = str(item.get("run_id") or "").strip()
            run_snapshot = snapshots_by_run.get(run_id, {})
            connector_binding = _resolve_run_connector_binding(run_snapshot) if isinstance(run_snapshot, dict) else None
            kind = str(item.get("kind") or "file").strip().lower() or "file"
            location = str(item.get(path_key) or "").strip()
            if not location:
                return
            if kind in {"screenshot", "image"}:
                summary["screenshots"] += 1
            elif kind == "report":
                summary["reports"] += 1
            elif kind == "data":
                summary["data"] += 1
            elif kind == "link":
                summary["links"] += 1
            else:
                summary["files"] += 1
            agent_role = str(run_snapshot.get("agent_role") or "").strip().lower() or None
            combined_items.append(
                {
                    "id": f"{run_id}:{location}",
                    "label": item.get("label") or Path(location).name or location[:120],
                    "kind": kind,
                    "source": item.get("source") or "inferred",
                    "run_id": run_id or None,
                    "updated_at": item.get("updated_at") or run_snapshot.get("updated_at") or run_snapshot.get("created_at"),
                    "created_at": item.get("created_at"),
                    "uri_or_path": location,
                    "artifact_id": item.get("artifact_id"),
                    "content_type": item.get("content_type"),
                    "byte_size": item.get("byte_size"),
                    "step_id": item.get("step_id"),
                    "step_index": item.get("step_index"),
                    "step_number": item.get("step_number"),
                    "machine_id": item.get("machine_id"),
                    "storage_backend": item.get("storage_backend"),
                    "storage_bucket": item.get("storage_bucket"),
                    "storage_region": item.get("storage_region"),
                    "storage_endpoint": item.get("storage_endpoint"),
                    "object_key": item.get("object_key"),
                    "retention": item.get("retention") if isinstance(item.get("retention"), dict) else {},
                    "focus_target": "screenshots" if kind in {"screenshot", "image"} else "artifacts",
                    "agent_role": agent_role,
                    "agent_label": AGENT_WORKSPACE_LABELS.get(agent_role or "", agent_role) if agent_role else None,
                    "run_status": run_snapshot.get("status"),
                    "result_summary": run_snapshot.get("result_summary"),
                    "pack_id": run_snapshot.get("pack_id"),
                    "connector_binding": connector_binding,
                }
            )

        for item in artifacts:
            if isinstance(item, dict):
                append_material(item, "uri_or_path")
        for item in files:
            if isinstance(item, dict):
                append_material(item, "path")

        combined_items.sort(
            key=lambda item: (
                _parse_utc_ts(item.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc),
                str(item.get("run_id") or ""),
                str(item.get("label") or ""),
            ),
            reverse=True,
        )
        if len(combined_items) > safe_limit:
            combined_items = combined_items[:safe_limit]

        summary["total"] = len(combined_items)
        return {
            "ok": True,
            "workspace_id": workspace_filter or "default",
            "updated_at": _utc_now_iso(),
            "summary": summary,
            "items": combined_items,
        }

    @app.get("/artifacts", dependencies=[Depends(require_api_key)], response_model=ApiArtifactListResponse)
    async def get_artifacts(
        workspace_id: Optional[str] = None,
        history_limit: int = 80,
        limit: int = 120,
        current_user=Depends(require_api_key),
    ):
        return await _workspace_artifacts_payload(
            workspace_id=workspace_id,
            history_limit=history_limit,
            limit=limit,
            current_user=current_user,
        )

    @app.get("/artifacts/workspace", dependencies=[Depends(require_api_key)], response_model=ApiArtifactListResponse)
    async def get_workspace_artifacts(
        workspace_id: Optional[str] = None,
        history_limit: int = 80,
        limit: int = 120,
        current_user=Depends(require_api_key),
    ):
        return await _workspace_artifacts_payload(
            workspace_id=workspace_id,
            history_limit=history_limit,
            limit=limit,
            current_user=current_user,
        )

    async def _artifact_preview_payload(
        path: str,
        *,
        current_user: Any,
    ):
        normalized_path = _normalize_workspace_material_path(path)
        if not normalized_path:
            raise HTTPException(status_code=400, detail="path is required.")

        artifact_metadata = artifact_service.load_artifact_metadata(normalized_path)
        if artifact_metadata is not None:
            _ensure_artifacts_access_for_workspace(artifact_metadata.get("workspace_id") or "default")
            enforce_workspace_access(
                current_user,
                artifact_metadata.get("workspace_id") or "default",
                tenant_id=artifact_metadata.get("tenant_id"),
                minimum_role="viewer",
            )
            target = artifact_service.resolve_artifact_content_path(normalized_path)
            if target is None:
                raise HTTPException(status_code=404, detail="Artifact content is unavailable for this canonical artifact.")
            media_type = str(
                artifact_metadata.get("content_type")
                or artifact_metadata.get("mime_type")
                or mimetypes.guess_type(str(target.name))[0]
                or "application/octet-stream"
            ).strip() or "application/octet-stream"
            preview_kind = _workspace_artifact_preview_kind(target, media_type)
            text_preview = _read_workspace_text_preview(target) if preview_kind == "text" else None
            file_name = str(artifact_metadata.get("file_name") or artifact_metadata.get("label") or target.name).strip() or target.name
            return {
                "ok": True,
                "path": normalized_path,
                "kind": preview_kind,
                "media_type": media_type,
                "byte_size": int(artifact_metadata.get("byte_size") or target.stat().st_size),
                "text_preview": text_preview,
                "file_url": f"/artifacts/file?{urlencode({'path': normalized_path})}",
                "download_url": f"/artifacts/content?{urlencode({'path': normalized_path})}",
                "file_name": file_name,
                "note": (
                    "Image preview available from object storage."
                    if preview_kind == "image"
                    else "Text preview available from object storage."
                    if preview_kind == "text"
                    else "Binary artifact available from object storage."
                ),
            }

        lowered = normalized_path.lower()
        if str((current_user or {}).get("auth_type") or "").strip().lower() != "api_key":
            _ensure_artifacts_access_for_workspace(str((current_user or {}).get("workspace_id") or "default"))
        if lowered.startswith(("http://", "https://")):
            return {
                "ok": True,
                "path": normalized_path,
                "kind": "link",
                "media_type": "text/uri-list",
                "byte_size": None,
                "text_preview": normalized_path,
                "file_url": normalized_path,
                "file_name": Path(normalized_path).name or normalized_path,
                "note": "External artifact link.",
            }
        if lowered.startswith("file://"):
            raise HTTPException(status_code=400, detail="file:// artifacts are not previewable from the runtime.")

        target = _resolve_workspace_material_target(normalized_path)
        if target is None:
            raise HTTPException(status_code=404, detail="Artifact file not found inside allowed workspace roots.")

        media_type = mimetypes.guess_type(str(target.name))[0] or "application/octet-stream"
        preview_kind = _workspace_artifact_preview_kind(target, media_type)
        text_preview = _read_workspace_text_preview(target) if preview_kind == "text" else None

        return {
            "ok": True,
            "path": normalized_path,
            "kind": preview_kind,
            "media_type": media_type,
            "byte_size": target.stat().st_size,
            "text_preview": text_preview,
            "file_url": f"/artifacts/file?{urlencode({'path': normalized_path})}",
            "download_url": f"/artifacts/content?{urlencode({'path': normalized_path})}",
            "file_name": target.name,
            "note": (
                "Image preview available."
                if preview_kind == "image"
                else "Text preview available."
                if preview_kind == "text"
                else "Binary artifact available for download or inspection."
            ),
        }

    @app.get("/artifacts/preview", dependencies=[Depends(require_api_key)], response_model=ApiArtifactPreviewResponse)
    async def get_artifact_preview(
        path: str,
        current_user=Depends(require_api_key),
    ):
        return await _artifact_preview_payload(path, current_user=current_user)

    async def _artifact_file_response(
        path: str,
        *,
        current_user: Any,
    ):
        normalized_path = _normalize_workspace_material_path(path)
        if not normalized_path:
            raise HTTPException(status_code=400, detail="path is required.")
        artifact_metadata = artifact_service.load_artifact_metadata(normalized_path)
        if artifact_metadata is not None:
            _ensure_artifacts_access_for_workspace(artifact_metadata.get("workspace_id") or "default")
            enforce_workspace_access(
                current_user,
                artifact_metadata.get("workspace_id") or "default",
                tenant_id=artifact_metadata.get("tenant_id"),
                minimum_role="viewer",
            )
            target = artifact_service.resolve_artifact_content_path(normalized_path)
            if target is None:
                raise HTTPException(status_code=404, detail="Artifact content is unavailable for this canonical artifact.")
            media_type = str(
                artifact_metadata.get("content_type")
                or artifact_metadata.get("mime_type")
                or mimetypes.guess_type(str(target.name))[0]
                or "application/octet-stream"
            ).strip() or "application/octet-stream"
            filename = str(artifact_metadata.get("file_name") or artifact_metadata.get("label") or target.name).strip() or target.name
            return FileResponse(path=str(target), media_type=media_type, filename=filename)
        if normalized_path.lower().startswith(("http://", "https://", "file://")):
            raise HTTPException(status_code=400, detail="Only local artifact files are supported.")

        target = _resolve_workspace_material_target(normalized_path)
        if target is None:
            raise HTTPException(status_code=404, detail="Artifact file not found inside allowed workspace roots.")
        if str((current_user or {}).get("auth_type") or "").strip().lower() != "api_key":
            _ensure_artifacts_access_for_workspace(str((current_user or {}).get("workspace_id") or "default"))

        media_type = mimetypes.guess_type(str(target.name))[0] or "application/octet-stream"
        return FileResponse(path=str(target), media_type=media_type, filename=target.name)

    @app.get("/artifacts/content", dependencies=[Depends(require_api_key)])
    async def get_artifact_content(
        path: str,
        current_user=Depends(require_api_key),
    ):
        return await _artifact_file_response(path, current_user=current_user)

    @app.get("/artifacts/file", dependencies=[Depends(require_api_key)])
    async def get_artifact_file(
        path: str,
        current_user=Depends(require_api_key),
    ):
        return await _artifact_file_response(path, current_user=current_user)

    @app.get("/artifacts/{artifact_id}", dependencies=[Depends(require_api_key)])
    async def get_artifact_detail(
        artifact_id: str,
        request: Request,
        current_user=Depends(require_api_key),
    ):
        return _artifact_detail_payload_for_id(
            artifact_id,
            current_user=current_user,
            request=request,
        )

    @app.get("/artifacts/{artifact_id}/file", dependencies=[Depends(require_api_key)])
    async def get_artifact_file_by_id(
        artifact_id: str,
        request: Request,
        current_user=Depends(require_api_key),
    ):
        return _artifact_file_response_for_id(
            artifact_id,
            current_user=current_user,
            request=request,
            disposition="inline",
        )

    @app.get("/artifacts/{artifact_id}/content", dependencies=[Depends(require_api_key)])
    async def get_artifact_content_by_id(
        artifact_id: str,
        request: Request,
        current_user=Depends(require_api_key),
    ):
        return _artifact_file_response_for_id(
            artifact_id,
            current_user=current_user,
            request=request,
            disposition="attachment",
        )

    @app.get("/agents/workspace/file-diff", dependencies=[Depends(require_admin_api_key)])
    async def get_agent_workspace_file_diff(
        path: str,
        run_id: Optional[str] = None,
    ):
        normalized_path = _normalize_workspace_material_path(path)
        if not normalized_path:
            raise HTTPException(status_code=400, detail="path is required.")
        _ensure_workspace_path_allowed(normalized_path)

        target_run_id = str(run_id or "").strip()
        snapshots: List[Dict[str, Any]] = []
        if target_run_id:
            try:
                snapshots.append(_get_replay_payload(target_run_id))
            except HTTPException:
                raise HTTPException(status_code=404, detail="Run ID not found.")
        else:
            with RUN_HISTORY_LOCK:
                history_items = list(RUN_HISTORY)
            for item in history_items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("agent_role") or "").strip().lower() != "builder":
                    continue
                snapshots.append(item)
                if len(snapshots) >= 20:
                    break

        evidence_rows: List[Dict[str, Any]] = []
        selected_run_id = target_run_id
        for snapshot in snapshots:
            rows = _build_workspace_evidence_rows(snapshot, normalized_path)
            if not rows:
                continue
            if not selected_run_id:
                selected_run_id = str(snapshot.get("run_id") or "").strip()
            evidence_rows.extend(rows)
            if len(evidence_rows) >= 12:
                break

        preview_text = ""
        preview_kind = "none"
        source = "unavailable"
        note = ""
        if evidence_rows:
            explicit_diff = next((row.get("diff_text") for row in evidence_rows if str(row.get("diff_text") or "").strip()), None)
            if explicit_diff:
                preview_text = str(explicit_diff)
                preview_kind = "diff"
                source = "replay_evidence"
                note = "Diff preview extracted from run replay evidence."
            else:
                preview_blocks: List[str] = []
                for row in evidence_rows[:6]:
                    event_name = str(row.get("event") or "").strip()
                    message = str(row.get("message") or "").strip()
                    data_preview = str(row.get("data_preview") or "").strip()
                    parts: List[str] = []
                    if event_name:
                        parts.append(f"[{event_name}]")
                    if message:
                        parts.append(message)
                    if data_preview:
                        parts.append(data_preview)
                    if parts:
                        preview_blocks.append("\n".join(parts))
                preview_text = "\n\n".join(
                    preview_blocks
                ).strip()
                preview_kind = "evidence"
                source = "replay_evidence"
                note = "Run replay mentions this file, but no structured diff payload was recorded."

        if not preview_text:
            diff_text, diff_source = _git_diff_for_workspace_path(normalized_path)
            if diff_text:
                preview_text = diff_text
                preview_kind = "diff"
                source = diff_source
                note = "Current working tree diff for this file."
            else:
                note = diff_source or "No diff preview available."

        return {
            "ok": True,
            "path": normalized_path,
            "area": _workspace_repo_area(normalized_path),
            "run_id": selected_run_id or None,
            "source": source,
            "preview_kind": preview_kind,
            "preview": preview_text,
            "note": note,
            "evidence": [
                {
                    "ts": row.get("ts"),
                    "event": row.get("event"),
                    "message": row.get("message"),
                }
                for row in evidence_rows[:12]
            ],
        }

    def _core_workspace_root() -> Path:
        return _local_companion_root_path()

    def _ensure_inside_root(target: Path, root: Path) -> None:
        try:
            target.relative_to(root)
        except Exception as exc:
            raise HTTPException(status_code=403, detail="Path must stay inside the Gateway workspace folder.") from exc

    def _resolve_workspace_path(path_value: str, *, expect_dir: bool = False, must_exist: bool = True) -> Path:
        normalized = _normalize_workspace_material_path(path_value)
        if not normalized:
            raise HTTPException(status_code=400, detail="path is required.")
        _ensure_workspace_path_allowed(normalized)
        lowered = normalized.lower()
        if lowered.startswith(("http://", "https://", "file://")):
            raise HTTPException(status_code=400, detail="Only local paths are supported.")
        root = _core_workspace_root()
        candidate = Path(normalized).expanduser()
        target = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        _ensure_inside_root(target, root)
        if must_exist and not target.exists():
            raise HTTPException(status_code=404, detail="Path not found.")
        if expect_dir and (not target.exists() or not target.is_dir()):
            raise HTTPException(status_code=400, detail="Path is not a directory.")
        if not expect_dir and must_exist and not target.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file.")
        return target

    def _is_text_path(target: Path) -> bool:
        suffix = target.suffix.lower()
        return suffix in WORKSPACE_PREVIEW_TEXT_EXTENSIONS or not suffix

    def _relative_to_root(target: Path, root: Path) -> str:
        try:
            return target.relative_to(root).as_posix()
        except Exception:
            return target.as_posix()

    def _should_ignore_path(rel_path: str) -> bool:
        if _workspace_path_is_restricted(rel_path):
            return True
        lowered = rel_path.replace("\\", "/").lstrip("./")
        for prefix in AGENT_WORKSPACE_IGNORED_PATH_PREFIXES:
            cleaned = prefix.rstrip("/")
            if lowered == cleaned or lowered.startswith(prefix):
                return True
        return False

    @app.get("/files/list", dependencies=[Depends(require_admin_api_key)])
    async def list_workspace_files(
        path: Optional[str] = None,
        limit: int = 200,
    ):
        root = _core_workspace_root()
        safe_limit = max(1, min(int(limit), 500))
        target = root if not path else _resolve_workspace_path(path, expect_dir=True)
        _ensure_inside_root(target, root)
        rows: List[Dict[str, Any]] = []
        for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            rel_path = _relative_to_root(entry, root)
            if _should_ignore_path(rel_path):
                continue
            rows.append(
                {
                    "name": entry.name,
                    "path": rel_path,
                    "kind": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                    "updated_at": datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            )
            if len(rows) >= safe_limit:
                break
        return {
            "ok": True,
            "root": ".",
            "path": _relative_to_root(target, root),
            "items": rows,
        }

    @app.get("/files/read", dependencies=[Depends(require_admin_api_key)])
    async def read_workspace_file(
        path: str,
        max_bytes: int = 120000,
    ):
        safe_limit = max(1024, min(int(max_bytes), 500000))
        target = _resolve_workspace_path(path, expect_dir=False)
        if not _is_text_path(target):
            raise HTTPException(status_code=400, detail="Only text files are supported in V1.")
        content = _read_workspace_text_preview(target, limit=safe_limit)
        return {
            "ok": True,
            "path": _normalize_workspace_material_path(path),
            "content": content,
            "truncated": len(content) >= safe_limit,
        }

    @app.post("/files/write", dependencies=[Depends(require_admin_api_key)])
    async def write_workspace_file(body: WorkspaceFileWriteRequest):
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        path = str(payload.get("path") or "").strip()
        content = payload.get("content")
        overwrite = bool(payload.get("overwrite"))
        workspace_id = _normalize_workspace_id(payload.get("workspace_id")) if payload.get("workspace_id") is not None else None
        if not path:
            raise HTTPException(status_code=400, detail="path is required.")
        root = _core_workspace_root()
        target = _resolve_workspace_path(path, expect_dir=False, must_exist=False)
        _ensure_inside_root(target, root)
        if not _is_text_path(target):
            raise HTTPException(status_code=400, detail="Only text files are supported in V1.")
        rel_path = _relative_to_root(target, root)
        operations = [
            {
                "tool": "filesystem.read_write",
                "mode": "write",
                "path": rel_path,
                "content": content if content is not None else "",
                "overwrite": overwrite,
            }
        ]
        metadata = {
            "outcome_pack": LOCAL_EXECUTION_PACK_ID,
            "execution_target": EXECUTION_TARGET_LOCAL_COMPANION,
            "pack_inputs": {"operations": operations},
            "action_policy": {"approval_actions": ["filesystem.read_write"]},
            "agent_role": "builder",
        }
        req = RunStartRequest(
            engine="orion",
            workspace_id=workspace_id,
            user_goal=f"Write file {rel_path}",
            metadata=metadata,
        )
        created = await _execute_workspace_run_request_async(req)
        return {"ok": True, **created}

    @app.post("/files/delete_request", dependencies=[Depends(require_admin_api_key)])
    async def delete_workspace_file_request(body: WorkspaceFileDeleteRequest):
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        path = str(payload.get("path") or "").strip()
        workspace_id = _normalize_workspace_id(payload.get("workspace_id")) if payload.get("workspace_id") is not None else None
        if not path:
            raise HTTPException(status_code=400, detail="path is required.")
        root = _core_workspace_root()
        target = _resolve_workspace_path(path, expect_dir=False, must_exist=True)
        _ensure_inside_root(target, root)
        if not _is_text_path(target):
            raise HTTPException(status_code=400, detail="Only text files are supported in V1.")
        rel_path = _relative_to_root(target, root)
        operations = [
            {
                "tool": "filesystem.read_write",
                "mode": "delete",
                "path": rel_path,
            }
        ]
        metadata = {
            "outcome_pack": LOCAL_EXECUTION_PACK_ID,
            "execution_target": EXECUTION_TARGET_LOCAL_COMPANION,
            "pack_inputs": {"operations": operations},
            "action_policy": {"approval_actions": ["filesystem.read_write"]},
            "agent_role": "builder",
        }
        req = RunStartRequest(
            engine="orion",
            workspace_id=workspace_id,
            user_goal=f"Delete file {rel_path}",
            metadata=metadata,
        )
        created = await _execute_workspace_run_request_async(req)
        return {"ok": True, **created}

    DEVICE_ACTIONS = supported_device_actions()

    @app.post("/device/execute", dependencies=[Depends(require_admin_api_key)])
    async def execute_device_action(body: DeviceExecuteRequest):
        payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        action = str(payload.get("action") or "").strip().lower()
        workspace_id = _normalize_workspace_id(payload.get("workspace_id")) if payload.get("workspace_id") is not None else None
        if not DEVICE_ACTIONS:
            platform_name = current_platform_context().key
            raise HTTPException(status_code=400, detail=f"Device actions are not supported on {platform_name}.")
        if not action or action not in DEVICE_ACTIONS:
            raise HTTPException(status_code=400, detail="Unknown or missing device action.")
        command = DEVICE_ACTIONS[action]
        operations = [
            {
                "tool": "shell.execute",
                "capability": f"device.{action}",
                "timeout_seconds": 20,
                "cwd": ".",
            }
        ]
        metadata = {
            "outcome_pack": LOCAL_EXECUTION_PACK_ID,
            "execution_target": EXECUTION_TARGET_LOCAL_COMPANION,
            "pack_inputs": {"operations": operations},
            "action_policy": {
                "blocked_actions": ["delete_files", "delete_records", "transfer_funds"],
                "approval_actions": ["shell.execute"],
            },
            "agent_role": "private-assistant",
        }
        req = RunStartRequest(
            engine="orion",
            workspace_id=workspace_id,
            user_goal=f"Device action: {action}",
            metadata=metadata,
        )
        created = await _execute_workspace_run_request_async(req)
        return {"ok": True, **created}
