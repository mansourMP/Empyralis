from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from server_modules.memory_service import save_daily_log
from server_modules import failure_policy_service, memory_summary_service
from server_modules import internal_tool_markup_service
from server_modules import rust_runtime_kernel_client
from server_modules.error_contracts import STORAGE_READ_FAILURE, STORAGE_WRITE_FAILURE, SEVERITY_WARNING


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TRANSCRIPTS_ROOT = _REPO_ROOT / ".orion-stack" / "transcripts"
logger = logging.getLogger(__name__)


def _normalize_workspace_token(workspace_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(workspace_id or "default").strip()).strip("-")
    return token or "default"


def _normalize_agent_token(agent_install_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(agent_install_id or "").strip()).strip("-")
    return token or "install"


def _enforce_session_transcript_state_decision(*, payload: Dict[str, Any]) -> Dict[str, Any]:
    workspace_id = str(payload.get("workspace_id") or "default").strip() or "default"
    thread_id = str(payload.get("thread_id") or "").strip()
    try:
        payload_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
    except Exception:
        payload_bytes = 0
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-state-store-decision",
            {
                "operation": "append_session_transcript",
                "state_class": "session_transcripts",
                "storage_engine": "durable_postgres",
                "tenant_id": workspace_id,
                "workspace_id": workspace_id,
                "actor_id": thread_id or "session_transcript_store",
                "status": "active",
                "payload": payload,
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
            result.get("reason")
            or "Rust runtime state store denied session transcript append."
        ) from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "append_session_transcript":
        raise RuntimeError(
            "Rust runtime state store returned unexpected next_action for "
            f"session transcript append: {next_action or 'missing'}"
        )
    return decision


def _transcript_dir(workspace_id: str, agent_install_id: str | None = None) -> Path:
    path = _TRANSCRIPTS_ROOT / _normalize_workspace_token(workspace_id)
    normalized_agent_install_id = str(agent_install_id or "").strip()
    if normalized_agent_install_id:
        path = path / "agents" / _normalize_agent_token(normalized_agent_install_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _compact_line(text: Any, limit: int = 320) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return _compact_line(content, limit=1200)
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() != "text":
                continue
            text = _compact_line(item.get("text"), limit=1200)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _is_inter_session_user_message(item: Dict[str, Any]) -> bool:
    provenance = item.get("provenance")
    if isinstance(provenance, dict) and str(provenance.get("kind") or "").strip() == "inter_session":
        return True
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        provenance = metadata.get("provenance")
        if isinstance(provenance, dict) and str(provenance.get("kind") or "").strip() == "inter_session":
            return True
    return False


def _normalized_transcript_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for item in list(messages or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        if role == "user" and _is_inter_session_user_message(item):
            continue
        content = _extract_message_text(item.get("content"))
        if not content:
            continue
        if role == "assistant":
            content = internal_tool_markup_service.strip_internal_tool_markup(content)
            if not content:
                continue
        if role == "user" and content.lstrip().startswith("/"):
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def build_transcript_summary(
    *,
    messages: List[Dict[str, Any]],
    user_message: str,
    assistant_reply: str,
) -> str:
    safe_messages = _normalized_transcript_messages(messages)
    return memory_summary_service.build_transcript_summary(
        messages=safe_messages,
        user_message=user_message,
        assistant_reply=assistant_reply,
    )


def build_deployed_agent_memory_summary(
    *,
    messages: List[Dict[str, Any]],
    prior_summary: Optional[str] = None,
    max_chars: int = 2200,
) -> str:
    return memory_summary_service.build_deployed_agent_memory_summary(
        messages=messages,
        prior_summary=prior_summary,
        max_chars=max_chars,
    )


def _latest_message_by_role(messages: List[Dict[str, Any]], role: str) -> str:
    normalized_role = str(role or "").strip().lower()
    for item in reversed(list(messages or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != normalized_role:
            continue
        content = _compact_line(item.get("content"), limit=420)
        if content:
            return content
    return ""


def list_session_transcript_summaries(
    *,
    workspace_id: str,
    agent_install_id: str | None = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    transcript_dir = _transcript_dir(workspace_id, agent_install_id=agent_install_id)
    safe_limit = max(1, min(int(limit or 5), 20))
    out: List[Dict[str, Any]] = []
    transcript_files = sorted(transcript_dir.glob("*.jsonl"), reverse=True)
    for transcript_path in transcript_files:
        try:
            lines = transcript_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            failure_policy_service.log_degraded_operation(
                logger=logger,
                code="transcript_read_failed",
                message="Failed to read transcript file.",
                error_class=STORAGE_READ_FAILURE,
                degraded_component="session_transcript_store",
                severity=SEVERITY_WARNING,
                retryable=False,
                status_code=500,
                metadata={
                    "workspace_id": workspace_id,
                    "agent_install_id": agent_install_id,
                    "path": str(transcript_path),
                },
                exc=exc,
            )
            continue
        for raw_line in reversed(lines):
            if len(out) >= safe_limit:
                return out
            try:
                payload = json.loads(raw_line)
            except Exception as exc:
                failure_policy_service.log_degraded_operation(
                    logger=logger,
                    code="transcript_parse_failed",
                    message="Failed to parse transcript record.",
                    error_class=STORAGE_READ_FAILURE,
                    degraded_component="session_transcript_store",
                    severity=SEVERITY_WARNING,
                    retryable=False,
                    status_code=500,
                    metadata={
                        "workspace_id": workspace_id,
                        "agent_install_id": agent_install_id,
                        "path": str(transcript_path),
                    },
                    exc=exc,
                )
                continue
            messages = _normalized_transcript_messages(
                [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
            )
            summary = build_transcript_summary(
                messages=messages,
                user_message=_latest_message_by_role(messages, "user"),
                assistant_reply=_latest_message_by_role(messages, "assistant"),
            )
            out.append(
                {
                    "path": str(transcript_path),
                    "timestamp": str(payload.get("timestamp") or "").strip() or None,
                    "thread_id": str(payload.get("thread_id") or "").strip() or None,
                    "provider": str(payload.get("provider") or "").strip() or None,
                    "model": str(payload.get("model") or "").strip() or None,
                    "message_count": len(messages),
                    "summary": summary,
                }
            )
    return out


def load_latest_session_transcript_messages(
    *,
    workspace_id: str,
    thread_id: str,
    agent_install_id: str | None = None,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        return []
    safe_limit = max(1, min(int(limit or 12), 40))
    transcript_dir = _transcript_dir(workspace_id, agent_install_id=agent_install_id)
    transcript_files = sorted(transcript_dir.glob("*.jsonl"), reverse=True)
    for transcript_path in transcript_files:
        try:
            lines = transcript_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            failure_policy_service.log_degraded_operation(
                logger=logger,
                code="transcript_read_failed",
                message="Failed to read transcript file.",
                error_class=STORAGE_READ_FAILURE,
                degraded_component="session_transcript_store",
                severity=SEVERITY_WARNING,
                retryable=False,
                status_code=500,
                metadata={
                    "workspace_id": workspace_id,
                    "agent_install_id": agent_install_id,
                    "path": str(transcript_path),
                },
                exc=exc,
            )
            continue
        for raw_line in reversed(lines):
            try:
                payload = json.loads(raw_line)
            except Exception as exc:
                failure_policy_service.log_degraded_operation(
                    logger=logger,
                    code="transcript_parse_failed",
                    message="Failed to parse transcript record.",
                    error_class=STORAGE_READ_FAILURE,
                    degraded_component="session_transcript_store",
                    severity=SEVERITY_WARNING,
                    retryable=False,
                    status_code=500,
                    metadata={
                        "workspace_id": workspace_id,
                        "agent_install_id": agent_install_id,
                        "path": str(transcript_path),
                    },
                    exc=exc,
                )
                continue
            if str(payload.get("thread_id") or "").strip() != normalized_thread_id:
                continue
            messages = _normalized_transcript_messages(
                [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
            )
            return messages[-safe_limit:]
    return []


def save_session_transcript(
    *,
    workspace_id: str,
    agent_install_id: str | None = None,
    thread_id: str,
    provider: Optional[str],
    model: Optional[str],
    messages: List[Dict[str, Any]],
    user_message: str,
    assistant_reply: str,
) -> Dict[str, Any]:
    now = _utc_now()
    day_token = now.strftime("%Y-%m-%d")
    transcript_path = _transcript_dir(workspace_id, agent_install_id=agent_install_id) / f"{day_token}.jsonl"
    payload = {
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "workspace_id": _normalize_workspace_token(workspace_id),
        "agent_install_id": str(agent_install_id or "").strip() or None,
        "thread_id": str(thread_id or "").strip() or None,
        "provider": str(provider or "").strip() or None,
        "model": str(model or "").strip() or None,
        "messages": _normalized_transcript_messages([dict(item) for item in messages if isinstance(item, dict)]),
    }
    _enforce_session_transcript_state_decision(payload=payload)
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    summary = build_transcript_summary(
        messages=messages,
        user_message=user_message,
        assistant_reply=assistant_reply,
    )
    degraded_operations: List[Dict[str, Any]] = []
    if summary:
        try:
            save_daily_log(workspace_id, summary, agent_install_id=agent_install_id)
        except Exception as exc:
            degraded_operations.append(
                failure_policy_service.log_degraded_operation(
                    logger=logger,
                    code="daily_log_persist_failed",
                    message="Failed to persist transcript-derived daily log summary.",
                    error_class=STORAGE_WRITE_FAILURE,
                    degraded_component="session_transcript_daily_log",
                    severity=SEVERITY_WARNING,
                    retryable=False,
                    status_code=500,
                    metadata={
                        "workspace_id": workspace_id,
                        "agent_install_id": agent_install_id,
                        "thread_id": thread_id,
                        "path": str(transcript_path),
                    },
                    exc=exc,
                )
            )
    return {
        "path": str(transcript_path),
        "summary": summary,
        "degraded_operations": degraded_operations,
    }
