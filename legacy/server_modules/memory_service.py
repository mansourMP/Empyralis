from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import HTTPException

from server_modules import agent_memory as _workspace_memory_store
from server_modules import memory_summary_service
from server_modules import rust_runtime_kernel_client
from server_modules import workspace_context_memory_adapter
from server_modules.telemetry import get_tracer, set_span_attributes
from server_modules.workspace_context import (
    ALLOWED_CONTEXT_FILENAMES,
    read_workspace_context_file,
    read_workspace_context_files,
    normalize_workspace_context_filename,
    write_workspace_context_file,
)

_DAILY_MEMORY_ENTRY_MAX_CHARS = 4000
_DAILY_MEMORY_ENTRY_MIN_CHARS = 32
_DAILY_MEMORY_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s&]+"),
    re.compile(r"(?i)\b(authorization:\s*bearer)\s+[^\s]+"),
)
_TRANSCRIPT_MARKERS = (
    re.compile(r"(?im)^\s*(user|assistant|system)\s*:\s*.{0,}$"),
)
_TRANSCRIPT_HISTORY_MARKERS = (
    re.compile(r"(?im)^\s*(user|assistant|system|human|ai|tool)\s*:\s*.{0,}$"),
    re.compile(r"(?im)^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*(user|assistant|system|human|ai)\b"),
)
_DAILY_NOTE_DURABILITY_HINTS = re.compile(
    r"(?i)\b("
    r"preference|prefer|decision|decided|policy|constraint|rule|default|"
    r"goal|plan|roadmap|project|architecture|context|customer|workflow|"
    r"always|never|must|should|ownership|priority|blocked|risk|billing|runtime"
    r")\b"
)
_DAILY_NOTE_TEMPORARY_HINTS = re.compile(
    r"(?i)\b("
    r"temp|temporary|for now|right now|quick test|scratch|maybe later|"
    r"ignore this|wip|draft only|just testing|random thought"
    r")\b"
)
_SAFE_CONSOLIDATION_TARGET_FILES = {"MEMORY.md", "GOALS.md", "PROCEDURES.md", "REFLECTION.md"}
_SAFE_CONSOLIDATION_DEFAULT_TARGET = "MEMORY.md"
_MEMORY_DEFAULT_ACTOR = "system"


def _append_note_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _strip_private_note_noise(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(text.split())


def _redact_daily_note_payload(value: str) -> str:
    text = str(value or "")
    for pattern in _DAILY_MEMORY_SECRET_PATTERNS:
        text = pattern.sub("[redacted-secret]", text)
    return text.strip()


def _looks_like_chat_transcript(value: str) -> bool:
    normalized = str(value or "")
    markers = 0
    for marker in _TRANSCRIPT_MARKERS:
        markers += len(marker.findall(normalized))
    return markers >= 2


def _looks_like_raw_chat_history(value: str) -> bool:
    normalized = str(value or "")
    markers = 0
    for marker in _TRANSCRIPT_HISTORY_MARKERS:
        markers += len(marker.findall(normalized))
    return markers >= 2


def _looks_like_temporary_noise(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    if len(normalized) < _DAILY_MEMORY_ENTRY_MIN_CHARS:
        return True
    if normalized in {"ok", "thanks", "thank you", "got it", "noted", "sure", "done", "yes", "no", "yeah"}:
        return True
    if not re.search(r"[.!?;:]|\\b(preference|decide|decision|plan|fact|goal|todo|note|update|constraint|policy|bug|project|customer|context|issue|insight|next|because|when|if|should|must|mustn't|avoid|prefer|priority)\b", normalized):
        return True
    return False


def _build_daily_note_entry(note: str) -> str:
    raw_redacted = _redact_daily_note_payload(str(note or ""))
    if _looks_like_chat_transcript(raw_redacted) or _looks_like_raw_chat_history(raw_redacted):
        raise ValueError("Daily memory note appears to include raw chat transcript content.")
    cleaned = _redact_daily_note_payload(_strip_private_note_noise(raw_redacted))
    if not cleaned:
        raise ValueError("Daily memory note cannot be empty.")
    if len(cleaned) > _DAILY_MEMORY_ENTRY_MAX_CHARS:
        raise ValueError("Daily memory note exceeds maximum length.")
    if _looks_like_temporary_noise(cleaned):
        raise ValueError("Daily memory note appears to be temporary noise. Include durable facts, decisions, or preferences.")
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    return f"- [{timestamp}] {cleaned}"


def _daily_entry_body(value: str) -> str:
    line = str(value or "").strip()
    match = re.match(r"^- \[[^\]]+\]\s*(.+)$", line)
    if match:
        return str(match.group(1) or "").strip()
    return line


def _normalize_daily_similarity_text(value: str) -> str:
    lowered = str(value or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    return " ".join(cleaned.split())


def _daily_similarity_tokens(value: str) -> Set[str]:
    normalized = _normalize_daily_similarity_text(value)
    tokens = {
        token
        for token in normalized.split(" ")
        if len(token) >= 4 and token not in {"that", "this", "with", "from", "have", "will", "then"}
    }
    return tokens


def _daily_note_similarity(a: str, b: str) -> float:
    a_tokens = _daily_similarity_tokens(a)
    b_tokens = _daily_similarity_tokens(b)
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    if union <= 0:
        return 0.0
    return float(intersection) / float(union)


def _classify_daily_note_usefulness(value: str) -> tuple[bool, str]:
    text = str(value or "").strip()
    if not text:
        return False, "empty"
    score = 0
    if _DAILY_NOTE_DURABILITY_HINTS.search(text):
        score += 2
    if len(text) >= 96:
        score += 1
    if re.search(r"\b\d{2,}\b", text):
        score += 1
    if _DAILY_NOTE_TEMPORARY_HINTS.search(text):
        score -= 2
    if _looks_like_raw_chat_history(text):
        score -= 3
    if score < 2:
        return False, "not_useful_for_future_behavior"
    return True, "useful"


def _safe_consolidation_target_for_text(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if re.search(r"\b(goal|goals|milestone|roadmap|plan|target|objective|next step)\b", normalized):
        return "GOALS.md"
    if re.search(r"\b(procedure|process|workflow|runbook|steps|how to|playbook|operating)\b", normalized):
        return "PROCEDURES.md"
    if re.search(r"\b(reflection|lesson|learned|mistake|improve|retrospective|insight)\b", normalized):
        return "REFLECTION.md"
    return _SAFE_CONSOLIDATION_DEFAULT_TARGET


def _iter_daily_note_bodies(content: str) -> List[str]:
    out: List[str] = []
    for raw_line in str(content or "").splitlines():
        body = _daily_entry_body(raw_line)
        if not body:
            continue
        if body.startswith("#"):
            continue
        out.append(body)
    return out


def _normalize_root_file_set(values: Optional[List[str]]) -> Set[str]:
    if not values:
        return set(_SAFE_CONSOLIDATION_TARGET_FILES)
    normalized: Set[str] = set()
    for item in values:
        token = normalize_workspace_context_filename(str(item or "").strip())
        if token not in _SAFE_CONSOLIDATION_TARGET_FILES:
            raise ValueError(f"Unsupported consolidation target file: {token}")
        normalized.add(token)
    return normalized


def _normalize_memory_actor(value: Any) -> str:
    token = " ".join(str(value or "").split()).strip()
    return token[:128] if token else _MEMORY_DEFAULT_ACTOR


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

def _normalize_workspace_id(workspace_id: str) -> str:
    return str(workspace_id or "default").strip() or "default"


def _enforce_memory_state_decision(
    *,
    operation: str,
    workspace_id: str,
    actor_id: str = _MEMORY_DEFAULT_ACTOR,
    payload: Optional[Dict[str, Any]] = None,
    status: str = "active",
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    record = dict(payload or {})
    try:
        payload_bytes = len(json.dumps(record, sort_keys=True).encode("utf-8"))
    except Exception:
        payload_bytes = 0
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-state-store-decision",
            {
                "operation": operation,
                "state_class": "workspace_memory",
                "storage_engine": "durable_postgres",
                "tenant_id": normalized_workspace_id,
                "workspace_id": normalized_workspace_id,
                "actor_id": _normalize_memory_actor(actor_id),
                "status": status,
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
                "operation": result.get("operation") or operation,
                "reason": result.get("reason") or str(exc),
            },
        ) from exc
    expected_next_action = {
        "upsert_workspace_memory": "write_workspace_memory",
        "delete_workspace_memory": "delete_workspace_memory",
        "append_workspace_daily_log": "append_workspace_daily_log",
        "update_workspace_context_file": "write_workspace_context_file",
    }.get(operation, operation)
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != expected_next_action:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_runtime_state_store_invalid_next_action",
                "operation": operation,
                "reason": (
                    "Rust runtime state store returned unexpected next_action "
                    f"for workspace memory operation {operation}: {next_action or 'missing'}"
                ),
            },
        )
    return decision


def _normalize_memory_lookup_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_. -]+", " ", str(value or "").strip().lower())
    return re.sub(r"\s+", "_", cleaned).strip("_")


def _trim_memory_value(value: str) -> str:
    return str(value or "").strip().rstrip(".")


@dataclass(slots=True)
class MemoryQuery:
    tenant_id: str = ""
    workspace_id: str = "default"
    agent_install_id: str = ""
    session_id: str = ""
    text: str = ""
    limit: int = 5
    include_workspace_context: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryItem:
    source: str
    text: str
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryResult:
    items: List[MemoryItem] = field(default_factory=list)
    context_blocks: List[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkspaceMemorySnapshot:
    workspace_id: str
    agent_install_id: str = ""
    entries: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""

    def as_payload(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "agent_install_id": self.agent_install_id or None,
            "entries": list(self.entries),
            "text": self.text,
        }


UtcNowFn = Callable[[], Any]
UtcNowIsoFn = Callable[[], str]
ParseUtcTsFn = Callable[[Any], Any]
NormalizeWorkspaceFn = Callable[[Any], Optional[str]]
JsonSafeFn = Callable[[Any], Any]
CompactTextFn = Callable[[Any, int], str]
EmitLogFn = Callable[[Any, str, str], None]
RefreshArchivedFn = Callable[[str, Dict[str, Any]], None]


class _RuntimeMemoryCompat:
    def __init__(self) -> None:
        self.ORION_MEMORY_ENABLED = False
        self.ORION_MEMORY_LANCEDB_URI = ""
        self.ORION_MEMORY_DB_PATH = ""
        self.ORION_MEMORY_READ_K = 5
        self.ORION_MEMORY_RETENTION_DAYS_DEFAULT = 30
        self.ORION_MEMORY_MAX_TEXT_CHARS = 6000
        self.MEMORY_BUCKETS: Set[str] = set()
        self.RuntimeMemoryManager: Any = None
        self.UTC_NOW: Optional[UtcNowFn] = None
        self.UTC_NOW_ISO: Optional[UtcNowIsoFn] = None
        self.PARSE_UTC_TS: Optional[ParseUtcTsFn] = None
        self.NORMALIZE_WORKSPACE_ID: Optional[NormalizeWorkspaceFn] = None
        self.JSON_SAFE: Optional[JsonSafeFn] = None
        self.COMPACT_EVENT_TEXT: Optional[CompactTextFn] = None
        self.EMIT_LOG: Optional[EmitLogFn] = None
        self.REFRESH_ARCHIVED_RUN_SNAPSHOT: Optional[RefreshArchivedFn] = None
        self.MEMORY_MANAGER_LOCK = threading.Lock()
        self.MEMORY_MANAGER_INSTANCE: Any = None
        self.MEMORY_MANAGER_ERROR: Optional[str] = None

    def _configure_runtime_memory(
        self,
        *,
        memory_enabled: bool,
        memory_lancedb_uri: str,
        memory_db_path: str,
        memory_read_k: int,
        memory_retention_days_default: int,
        memory_max_text_chars: int,
        memory_buckets: Set[str],
        runtime_memory_manager: Any,
        utc_now: UtcNowFn,
        utc_now_iso: UtcNowIsoFn,
        parse_utc_ts: ParseUtcTsFn,
        normalize_workspace_id: NormalizeWorkspaceFn,
        json_safe: JsonSafeFn,
        compact_event_text: CompactTextFn,
        emit_log: EmitLogFn,
        refresh_archived_run_snapshot: RefreshArchivedFn,
    ) -> None:
        self.ORION_MEMORY_ENABLED = bool(memory_enabled)
        self.ORION_MEMORY_LANCEDB_URI = str(memory_lancedb_uri)
        self.ORION_MEMORY_DB_PATH = str(memory_db_path)
        self.ORION_MEMORY_READ_K = int(memory_read_k)
        self.ORION_MEMORY_RETENTION_DAYS_DEFAULT = int(memory_retention_days_default)
        self.ORION_MEMORY_MAX_TEXT_CHARS = int(memory_max_text_chars)
        self.MEMORY_BUCKETS = set(memory_buckets)
        self.RuntimeMemoryManager = runtime_memory_manager
        self.UTC_NOW = utc_now
        self.UTC_NOW_ISO = utc_now_iso
        self.PARSE_UTC_TS = parse_utc_ts
        self.NORMALIZE_WORKSPACE_ID = normalize_workspace_id
        self.JSON_SAFE = json_safe
        self.COMPACT_EVENT_TEXT = compact_event_text
        self.EMIT_LOG = emit_log
        self.REFRESH_ARCHIVED_RUN_SNAPSHOT = refresh_archived_run_snapshot

    def _utc_now(self) -> Any:
        if self.UTC_NOW is None:
            raise RuntimeError("UTC_NOW not configured")
        return self.UTC_NOW()

    def _utc_now_iso(self) -> str:
        if self.UTC_NOW_ISO is None:
            raise RuntimeError("UTC_NOW_ISO not configured")
        return self.UTC_NOW_ISO()

    def _parse_utc_ts(self, value: Any) -> Any:
        if self.PARSE_UTC_TS is None:
            raise RuntimeError("PARSE_UTC_TS not configured")
        return self.PARSE_UTC_TS(value)

    def _normalize_workspace_id(self, value: Any) -> Optional[str]:
        if self.NORMALIZE_WORKSPACE_ID is None:
            raise RuntimeError("NORMALIZE_WORKSPACE_ID not configured")
        return self.NORMALIZE_WORKSPACE_ID(value)

    def _json_safe(self, value: Any) -> Any:
        if self.JSON_SAFE is None:
            raise RuntimeError("JSON_SAFE not configured")
        return self.JSON_SAFE(value)

    def _compact_event_text(self, value: Any, limit: int = 800) -> str:
        if self.COMPACT_EVENT_TEXT is None:
            raise RuntimeError("COMPACT_EVENT_TEXT not configured")
        return self.COMPACT_EVENT_TEXT(value, limit)

    def _emit_log(self, log_queue: Any, level: str, message: str, event: str = "runtime", data: Optional[dict] = None) -> None:
        if self.EMIT_LOG is None:
            raise RuntimeError("EMIT_LOG not configured")
        self.EMIT_LOG(log_queue, level, message, event=event, data=data)

    def _refresh_archived_run_snapshot(self, run_id: str, run: Dict[str, Any]) -> None:
        if self.REFRESH_ARCHIVED_RUN_SNAPSHOT is None:
            return
        self.REFRESH_ARCHIVED_RUN_SNAPSHOT(run_id, run)

    def _normalize_memory_bucket(self, value: Any, *, required: bool = True) -> Optional[str]:
        bucket = str(value or "").strip().lower()
        if not bucket:
            if required:
                raise HTTPException(status_code=400, detail="memory bucket is required.")
            return None
        if bucket not in self.MEMORY_BUCKETS:
            raise HTTPException(status_code=400, detail="memory bucket must be one of: profile, project, session.")
        return bucket

    def _memory_manager(self) -> Any:
        if not self.ORION_MEMORY_ENABLED:
            self.MEMORY_MANAGER_ERROR = "memory_disabled"
            return None
        with self.MEMORY_MANAGER_LOCK:
            if self.MEMORY_MANAGER_INSTANCE is not None:
                return self.MEMORY_MANAGER_INSTANCE
            if self.RuntimeMemoryManager is None:
                self.MEMORY_MANAGER_ERROR = "memory_manager_import_failed"
                return None
            try:
                self.MEMORY_MANAGER_INSTANCE = self.RuntimeMemoryManager(
                    lancedb_uri=self.ORION_MEMORY_LANCEDB_URI,
                    sqlite_path=self.ORION_MEMORY_DB_PATH,
                )
                self.MEMORY_MANAGER_ERROR = None
                return self.MEMORY_MANAGER_INSTANCE
            except Exception as exc:
                self.MEMORY_MANAGER_ERROR = f"memory_manager_init_failed: {exc}"
                self.MEMORY_MANAGER_INSTANCE = None
                return None

    def _memory_manager_or_503(self) -> Any:
        manager = self._memory_manager()
        if manager is None:
            reason = self.MEMORY_MANAGER_ERROR or "memory_unavailable"
            raise HTTPException(status_code=503, detail=reason)
        return manager


# Internal runtime-memory state after folding the separate runtime_memory.py
# subsystem into this service boundary.
runtime_memory = _RuntimeMemoryCompat()


def list_memory_entries(workspace_id: str, *, agent_install_id: str | None = None) -> List[Dict[str, Any]]:
    return _workspace_memory_store._list_memory_entries(
        _normalize_workspace_id(workspace_id),
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def save_memory(
    workspace_id: str,
    key: str,
    content: str,
    *,
    sync_memory_md: bool = True,
    agent_install_id: str | None = None,
) -> None:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    _enforce_memory_state_decision(
        operation="upsert_workspace_memory",
        workspace_id=normalized_workspace_id,
        payload={
            "key": str(key or "").strip(),
            "content": str(content or ""),
            "agent_install_id": str(agent_install_id or "").strip() or None,
        },
    )
    _workspace_memory_store._save_memory(
        normalized_workspace_id,
        key,
        content,
        sync_memory_md=sync_memory_md,
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def get_memory(workspace_id: str, *, agent_install_id: str | None = None) -> str:
    return _workspace_memory_store._get_memory(
        _normalize_workspace_id(workspace_id),
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def semantic_search(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    *,
    agent_install_id: str | None = None,
) -> List[Dict[str, Any]]:
    return _workspace_memory_store._semantic_search(
        _normalize_workspace_id(workspace_id),
        query,
        top_k=top_k,
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def delete_memory(workspace_id: str, key: str, *, agent_install_id: str | None = None) -> bool:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    _enforce_memory_state_decision(
        operation="delete_workspace_memory",
        workspace_id=normalized_workspace_id,
        payload={
            "key": str(key or "").strip(),
            "agent_install_id": str(agent_install_id or "").strip() or None,
        },
        status="archived",
    )
    return _workspace_memory_store._delete_memory(
        normalized_workspace_id,
        key,
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def save_daily_log(workspace_id: str, content: str, *, agent_install_id: str | None = None) -> None:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    _enforce_memory_state_decision(
        operation="append_workspace_daily_log",
        workspace_id=normalized_workspace_id,
        payload={
            "content": str(content or ""),
            "agent_install_id": str(agent_install_id or "").strip() or None,
        },
    )
    _workspace_memory_store._save_daily_log(
        normalized_workspace_id,
        content,
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def get_recent_logs(workspace_id: str, days: int = 7, *, agent_install_id: str | None = None) -> str:
    return _workspace_memory_store._get_recent_logs(
        _normalize_workspace_id(workspace_id),
        days=days,
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def list_memory_notebook_files(workspace_id: str, *, agent_install_id: str | None = None) -> List[Dict[str, Any]]:
    return _workspace_memory_store._list_memory_notebook_files(
        _normalize_workspace_id(workspace_id),
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def search_memory_notebook(
    workspace_id: str,
    query: str,
    *,
    max_results: int = 5,
    agent_install_id: str | None = None,
) -> List[Dict[str, Any]]:
    return _workspace_memory_store._search_memory_notebook(
        _normalize_workspace_id(workspace_id),
        query,
        max_results=max_results,
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def get_memory_notebook_excerpt(
    workspace_id: str,
    rel_path: str,
    *,
    from_line: int | None = None,
    line_count: int | None = None,
    agent_install_id: str | None = None,
) -> Dict[str, Any]:
    return _workspace_memory_store._get_memory_notebook_excerpt(
        _normalize_workspace_id(workspace_id),
        rel_path,
        from_line=from_line,
        line_count=line_count,
        agent_install_id=str(agent_install_id or "").strip() or None,
    )


def update_memory_context_file(
    workspace_id: str,
    filename: str,
    content: str,
    *,
    agent_install_id: str | None = None,
    actor: str = _MEMORY_DEFAULT_ACTOR,
    reason: str = "memory_update",
    run_id: str | None = None,
    audit_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_agent_install_id = str(agent_install_id or "").strip() or None
    normalized_filename = normalize_workspace_context_filename(filename)
    old_content = read_workspace_context_file(
        normalized_filename,
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    _enforce_memory_state_decision(
        operation="update_workspace_context_file",
        workspace_id=normalized_workspace_id,
        actor_id=actor,
        payload={
            "filename": normalized_filename,
            "content": str(content or ""),
            "agent_install_id": normalized_agent_install_id,
            "run_id": run_id,
        },
    )
    saved = write_workspace_context_file(
        normalized_filename,
        str(content or ""),
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    version_record = _append_memory_file_version_record(
        normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
        actor=actor,
        filename=normalized_filename,
        old_content=old_content,
        new_content=str(saved.get("content") or ""),
        reason=reason,
        run_id=run_id,
        metadata=audit_metadata,
    )
    return {
        "workspace_id": normalized_workspace_id,
        "agent_install_id": normalized_agent_install_id,
        "filename": saved.get("filename"),
        "content": saved.get("content", ""),
        "old_hash": version_record.get("old_hash"),
        "new_hash": version_record.get("new_hash"),
        "version_id": version_record.get("version_id"),
    }



def memory_read_file(
    workspace_id: str,
    filename: str,
    *,
    agent_install_id: str | None = None,
) -> Dict[str, Any]:
    """Read a memory file on demand. Used by Sage when MEMORY.md is not in bootstrap context.

    Returns {file, content, chars} so Sage can consume the file contents.
    """
    from server_modules.workspace_context import (
        read_workspace_context_file,
        normalize_workspace_context_filename,
    )
    normalized_filename = normalize_workspace_context_filename(filename)
    content = read_workspace_context_file(
        normalized_filename,
        workspace_id=_normalize_workspace_id(workspace_id),
        agent_install_id=str(agent_install_id or '').strip() or None,
    )
    return {
        'file': normalized_filename,
        'content': str(content or ''),
        'chars': len(str(content or '')),
    }


def memory_write_file(
    workspace_id: str,
    filename: str,
    content: str,
    *,
    mode: str = 'replace',
    agent_install_id: str | None = None,
    actor: str = _MEMORY_DEFAULT_ACTOR,
    reason: str = 'memory_write',
    run_id: str | None = None,
) -> Dict[str, Any]:
    """Write to a memory file. Used by Sage to update MEMORY.md (append new facts)
    or edit bootstrap files (SOUL.md, AGENTS.md, TOOLS.md, IDENTITY.md) via replace.

    mode='append': adds content to end of file (for MEMORY.md facts)
    mode='replace': overwrites entire file (for bootstrap file edits)
    """
    from server_modules.workspace_context import (
        read_workspace_context_file,
        write_workspace_context_file,
        normalize_workspace_context_filename,
    )
    normalized_filename = normalize_workspace_context_filename(filename)
    normalized_mode = str(mode or 'replace').strip().lower() or 'replace'

    if normalized_mode == 'append':
        existing = read_workspace_context_file(
            normalized_filename,
            workspace_id=_normalize_workspace_id(workspace_id),
            agent_install_id=str(agent_install_id or '').strip() or None,
        )
        combined = str(existing or '').rstrip() + '\n' + str(content or '').strip()
        final_content = combined.strip()
    else:
        final_content = str(content or '')

    _enforce_memory_state_decision(
        operation='update_workspace_context_file',
        workspace_id=_normalize_workspace_id(workspace_id),
        actor_id=actor,
        payload={
            'filename': normalized_filename,
            'content': final_content,
            'agent_install_id': str(agent_install_id or '').strip() or None,
            'run_id': run_id,
        },
    )
    saved = write_workspace_context_file(
        normalized_filename,
        final_content,
        workspace_id=_normalize_workspace_id(workspace_id),
        agent_install_id=str(agent_install_id or '').strip() or None,
    )
    _append_memory_file_version_record(
        _normalize_workspace_id(workspace_id),
        agent_install_id=str(agent_install_id or '').strip() or None,
        actor=actor,
        filename=normalized_filename,
        old_content='',
        new_content=str(saved.get('content') or ''),
        reason=reason,
        run_id=run_id,
        metadata={'mode': normalized_mode},
    )
    return {
        'file': saved.get('filename'),
        'chars_written': len(str(saved.get('content', ''))),
        'mode': normalized_mode,
    }

def memory_append_daily_note(
    workspace_id: str,
    note: str,
    *,
    agent_install_id: str | None = None,
    actor: str = _MEMORY_DEFAULT_ACTOR,
    run_id: str | None = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_agent_install_id = str(agent_install_id or "").strip() or None
    note_date = str(_append_note_now() or "").strip()
    expected_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if note_date != expected_today:
        raise ValueError("Daily memory note writes are restricted to today's UTC note file.")
    filename = f"memory/{note_date}.md"
    entry = _build_daily_note_entry(note)
    existing = read_workspace_context_file(
        filename,
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    entry_body = _daily_entry_body(entry)
    is_useful, usefulness_reason = _classify_daily_note_usefulness(entry_body)
    if not is_useful:
        raise ValueError("Daily memory note rejected by usefulness gate; include durable facts, preferences, decisions, or project context.")
    existing_bodies: List[str] = []
    for line in str(existing or "").splitlines():
        body = _daily_entry_body(line)
        if not body:
            continue
        existing_bodies.append(body)
    normalized_entry_body = _normalize_daily_similarity_text(entry_body)
    for body in existing_bodies:
        candidate = _normalize_daily_similarity_text(body)
        if not candidate:
            continue
        similarity = _daily_note_similarity(normalized_entry_body, candidate)
        if candidate == normalized_entry_body or similarity >= 0.8:
            return {
                "workspace_id": normalized_workspace_id,
                "agent_install_id": normalized_agent_install_id,
                "filename": filename,
                "saved": False,
                "duplicate_of": body,
                "duplicate_similarity": round(similarity, 3),
                "usefulness": usefulness_reason,
            }
    payload = f"{entry}\n"
    if str(existing or "").strip():
        payload = f"{str(existing).rstrip()}\n{entry}\n"
    _enforce_memory_state_decision(
        operation="append_workspace_daily_log",
        workspace_id=normalized_workspace_id,
        actor_id=actor,
        payload={
            "filename": filename,
            "content": entry,
            "agent_install_id": normalized_agent_install_id,
            "run_id": run_id,
        },
    )
    saved = write_workspace_context_file(
        filename,
        payload,
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    version_record = _append_memory_file_version_record(
        normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
        actor=actor,
        filename=filename,
        old_content=existing,
        new_content=str(saved.get("content") or ""),
        reason="memory_append_daily_note",
        run_id=run_id,
        metadata={"usefulness": usefulness_reason},
    )
    return {
        "workspace_id": normalized_workspace_id,
        "agent_install_id": normalized_agent_install_id,
        "filename": saved.get("filename"),
        "appended_entry": entry,
        "saved": True,
        "usefulness": usefulness_reason,
        "old_hash": version_record.get("old_hash"),
        "new_hash": version_record.get("new_hash"),
        "version_id": version_record.get("version_id"),
    }


def create_memory_consolidation_staging_file(
    workspace_id: str,
    proposal: str,
    *,
    source_refs: Optional[List[str]] = None,
    target_files: Optional[List[str]] = None,
    agent_install_id: str | None = None,
    actor: str = _MEMORY_DEFAULT_ACTOR,
    run_id: str | None = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_agent_install_id = str(agent_install_id or "").strip() or None
    proposal_text = _strip_private_note_noise(_redact_daily_note_payload(str(proposal or "")))
    if not proposal_text:
        raise ValueError("Consolidation proposal cannot be empty.")
    if len(proposal_text) < 32:
        raise ValueError("Consolidation proposal must include durable context, not a short placeholder.")
    if len(proposal_text) > 8000:
        raise ValueError("Consolidation proposal exceeds maximum length.")
    if _looks_like_chat_transcript(proposal_text) or _looks_like_raw_chat_history(proposal_text):
        raise ValueError("Consolidation proposal appears to include raw chat transcript content.")

    normalized_targets: List[str] = []
    for item in list(target_files or []):
        normalized = normalize_workspace_context_filename(str(item or "").strip())
        if normalized not in ALLOWED_CONTEXT_FILENAMES:
            raise ValueError(f"Consolidation targets must be root context files. Unsupported target: {normalized}")
        if normalized in normalized_targets:
            continue
        normalized_targets.append(normalized)

    normalized_refs: List[str] = []
    for item in list(source_refs or []):
        token = str(item or "").strip()
        if not token:
            continue
        normalized_refs.append(token[:256])

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    proposal_hash = hashlib.sha1(proposal_text.encode("utf-8")).hexdigest()[:10]
    filename = f"memory/.dreams/{timestamp}-{proposal_hash}.md"
    markdown_lines: List[str] = [
        "# Memory Consolidation Proposal",
        "",
        f"- created_at_utc: {timestamp}",
        f"- workspace_id: {normalized_workspace_id}",
        "",
        "## Proposed Consolidation",
        proposal_text,
        "",
    ]
    if normalized_targets:
        markdown_lines.extend(["## Proposed Target Files", *[f"- {name}" for name in normalized_targets], ""])
    if normalized_refs:
        markdown_lines.extend(["## Source References", *[f"- {ref}" for ref in normalized_refs], ""])
    markdown_lines.extend(
        [
            "## Merge Policy",
            "- Staging proposal only. No root-file merge without user approval or policy allowance.",
            "",
        ]
    )
    payload = "\n".join(markdown_lines).rstrip() + "\n"
    old_content = read_workspace_context_file(
        filename,
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    saved = write_workspace_context_file(
        filename,
        payload,
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    version_record = _append_memory_file_version_record(
        normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
        actor=actor,
        filename=filename,
        old_content=old_content,
        new_content=str(saved.get("content") or ""),
        reason="memory_stage_consolidation",
        run_id=run_id,
        metadata={"target_files": normalized_targets, "source_refs_count": len(normalized_refs)},
    )
    return {
        "workspace_id": normalized_workspace_id,
        "agent_install_id": normalized_agent_install_id,
        "filename": saved.get("filename"),
        "target_files": normalized_targets,
        "source_refs": normalized_refs,
        "old_hash": version_record.get("old_hash"),
        "new_hash": version_record.get("new_hash"),
        "version_id": version_record.get("version_id"),
    }


def apply_memory_consolidation_staging(
    workspace_id: str,
    staging_filename: str,
    merged_files: Dict[str, str],
    *,
    user_approved: bool = False,
    policy_allows: bool = False,
    agent_install_id: str | None = None,
    actor: str = _MEMORY_DEFAULT_ACTOR,
    run_id: str | None = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_agent_install_id = str(agent_install_id or "").strip() or None
    if not (bool(user_approved) or bool(policy_allows)):
        raise ValueError("Consolidation merge requires explicit user approval or policy allowance.")
    normalized_staging_filename = normalize_workspace_context_filename(staging_filename)
    if not normalized_staging_filename.startswith("memory/.dreams/"):
        raise ValueError("Consolidation staging filename must be under memory/.dreams/.")
    staging_content = read_workspace_context_file(
        normalized_staging_filename,
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    if not str(staging_content or "").strip():
        raise ValueError("Consolidation staging file is missing or empty.")
    if not isinstance(merged_files, dict) or not merged_files:
        raise ValueError("Consolidation merge requires at least one root file update.")
    applied_files: List[str] = []
    versions: List[Dict[str, Any]] = []
    for filename, content in merged_files.items():
        normalized = normalize_workspace_context_filename(str(filename or "").strip())
        if normalized not in ALLOWED_CONTEXT_FILENAMES:
            raise ValueError(f"Consolidation merge can only write root context files. Unsupported target: {normalized}")
        old_content = read_workspace_context_file(
            normalized,
            workspace_id=normalized_workspace_id,
            agent_install_id=normalized_agent_install_id,
        )
        write_workspace_context_file(
            normalized,
            str(content or ""),
            workspace_id=normalized_workspace_id,
            agent_install_id=normalized_agent_install_id,
        )
        version_record = _append_memory_file_version_record(
            normalized_workspace_id,
            agent_install_id=normalized_agent_install_id,
            actor=actor,
            filename=normalized,
            old_content=old_content,
            new_content=str(content or ""),
            reason="memory_apply_consolidation",
            run_id=run_id,
            metadata={"staging_filename": normalized_staging_filename},
        )
        applied_files.append(normalized)
        versions.append(
            {
                "filename": normalized,
                "old_hash": version_record.get("old_hash"),
                "new_hash": version_record.get("new_hash"),
                "version_id": version_record.get("version_id"),
            }
        )
    return {
        "workspace_id": normalized_workspace_id,
        "agent_install_id": normalized_agent_install_id,
        "staging_filename": normalized_staging_filename,
        "applied_files": applied_files,
        "approved": bool(user_approved),
        "policy_allowed": bool(policy_allows),
        "versions": versions,
    }


def consolidate_daily_memory_notes(
    workspace_id: str,
    *,
    target_files: Optional[List[str]] = None,
    max_notes: int = 30,
    apply_merge: bool = False,
    compact_mode: str = "none",
    user_approved: bool = False,
    policy_allows: bool = False,
    agent_install_id: str | None = None,
    run_id: str | None = None,
    actor: str = _MEMORY_DEFAULT_ACTOR,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_agent_install_id = str(agent_install_id or "").strip() or None
    allowed_targets = _normalize_root_file_set(target_files)
    safe_max_notes = max(1, min(int(max_notes or 30), 120))
    normalized_compact_mode = str(compact_mode or "none").strip().lower() or "none"
    if normalized_compact_mode not in {"none", "archive", "compact"}:
        raise ValueError("compact_mode must be one of: none, archive, compact.")

    context_files = read_workspace_context_files(
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    daily_filenames = sorted(
        [
            str(name)
            for name in context_files.keys()
            if str(name).startswith("memory/") and str(name).endswith(".md") and not str(name).startswith("memory/.dreams/")
        ]
    )[:safe_max_notes]

    proposals_by_target: Dict[str, List[str]] = {name: [] for name in sorted(allowed_targets)}
    preserved_by_source: Dict[str, List[str]] = {}
    for filename in daily_filenames:
        daily_content = str(context_files.get(filename) or "")
        captured_for_source: List[str] = []
        for body in _iter_daily_note_bodies(daily_content):
            useful, _reason = _classify_daily_note_usefulness(body)
            if not useful:
                continue
            target = _safe_consolidation_target_for_text(body)
            if target not in allowed_targets:
                continue
            existing = proposals_by_target.setdefault(target, [])
            if any(_daily_note_similarity(body, candidate) >= 0.85 for candidate in existing):
                continue
            existing.append(body)
            captured_for_source.append(body)
        if captured_for_source:
            preserved_by_source[filename] = captured_for_source

    proposed_updates: Dict[str, str] = {}
    merge_targets: List[str] = []
    for target_file in sorted(allowed_targets):
        additions = proposals_by_target.get(target_file) or []
        if not additions:
            continue
        existing_content = read_workspace_context_file(
            target_file,
            workspace_id=normalized_workspace_id,
            agent_install_id=normalized_agent_install_id,
        )
        existing_lines = str(existing_content or "").splitlines()
        normalized_existing_lines = {_normalize_daily_similarity_text(line) for line in existing_lines if line.strip()}
        pending_lines = [line for line in additions if _normalize_daily_similarity_text(line) not in normalized_existing_lines]
        if not pending_lines:
            continue
        section_header = f"## Consolidated Daily Notes ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})"
        append_block = "\n".join([section_header, *[f"- {line}" for line in pending_lines]]).rstrip()
        merged_content = str(existing_content or "").rstrip()
        if merged_content:
            merged_content = f"{merged_content}\n\n{append_block}\n"
        else:
            merged_content = f"{append_block}\n"
        proposed_updates[target_file] = merged_content
        merge_targets.append(target_file)

    proposal_id = hashlib.sha1(
        json.dumps(
            {
                "workspace_id": normalized_workspace_id,
                "sources": daily_filenames,
                "targets": sorted(proposed_updates.keys()),
                "entries": sum(len(values) for values in proposals_by_target.values()),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]

    response: Dict[str, Any] = {
        "workspace_id": normalized_workspace_id,
        "agent_install_id": normalized_agent_install_id,
        "proposal_id": proposal_id,
        "source_daily_notes": daily_filenames,
        "preserved_by_source": preserved_by_source,
        "proposed_updates": proposed_updates,
        "merge_targets": merge_targets,
        "apply_merge": bool(apply_merge),
        "compact_mode": normalized_compact_mode,
        "approved": bool(user_approved),
        "policy_allowed": bool(policy_allows),
        "merged": False,
        "compacted_daily_notes": [],
        "audit_id": None,
    }

    if not apply_merge:
        return response
    if not (bool(user_approved) or bool(policy_allows)):
        raise ValueError("Safe consolidation merge requires explicit user approval or policy allowance.")
    if not proposed_updates:
        response["merged"] = True
        response["audit_id"] = _append_consolidation_audit_record(
            normalized_workspace_id,
            {
                "kind": "memory_safe_consolidation_merge",
                "run_id": str(run_id or "").strip() or None,
                "proposal_id": proposal_id,
                "merged_files": [],
                "source_daily_notes": daily_filenames,
                "preserved_entry_count": 0,
                "compacted_daily_notes": [],
                "compact_mode": normalized_compact_mode,
                "approved": bool(user_approved),
                "policy_allowed": bool(policy_allows),
            },
        )["audit_id"]
        return response

    merged_files: List[str] = []
    merged_versions: List[Dict[str, Any]] = []
    for target_file, content in proposed_updates.items():
        old_content = read_workspace_context_file(
            target_file,
            workspace_id=normalized_workspace_id,
            agent_install_id=normalized_agent_install_id,
        )
        write_workspace_context_file(
            target_file,
            content,
            workspace_id=normalized_workspace_id,
            agent_install_id=normalized_agent_install_id,
        )
        version_record = _append_memory_file_version_record(
            normalized_workspace_id,
            agent_install_id=normalized_agent_install_id,
            actor=actor,
            filename=target_file,
            old_content=old_content,
            new_content=str(content or ""),
            reason="memory_safe_consolidation_merge",
            run_id=run_id,
            metadata={"proposal_id": proposal_id},
        )
        merged_files.append(target_file)
        merged_versions.append(
            {
                "filename": target_file,
                "old_hash": version_record.get("old_hash"),
                "new_hash": version_record.get("new_hash"),
                "version_id": version_record.get("version_id"),
            }
        )

    compacted_daily_notes: List[str] = []
    compacted_versions: List[Dict[str, Any]] = []
    if normalized_compact_mode in {"archive", "compact"}:
        for source_filename in daily_filenames:
            if source_filename not in preserved_by_source:
                continue
            if normalized_compact_mode == "archive":
                archive_payload = (
                    "# Daily Note Archived\n\n"
                    f"- consolidated_at_utc: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}\n"
                    f"- proposal_id: {proposal_id}\n"
                    f"- preserved_entries: {len(preserved_by_source.get(source_filename) or [])}\n"
                )
            else:
                archive_payload = (
                    "# Daily Note Compacted\n\n"
                    f"- consolidated_at_utc: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}\n"
                    f"- proposal_id: {proposal_id}\n"
                    "- content_removed_after_preservation: true\n"
                )
            old_content = read_workspace_context_file(
                source_filename,
                workspace_id=normalized_workspace_id,
                agent_install_id=normalized_agent_install_id,
            )
            write_workspace_context_file(
                source_filename,
                archive_payload,
                workspace_id=normalized_workspace_id,
                agent_install_id=normalized_agent_install_id,
            )
            version_record = _append_memory_file_version_record(
                normalized_workspace_id,
                agent_install_id=normalized_agent_install_id,
                actor=actor,
                filename=source_filename,
                old_content=old_content,
                new_content=archive_payload,
                reason="memory_daily_note_compaction",
                run_id=run_id,
                metadata={"proposal_id": proposal_id, "compact_mode": normalized_compact_mode},
            )
            compacted_daily_notes.append(source_filename)
            compacted_versions.append(
                {
                    "filename": source_filename,
                    "old_hash": version_record.get("old_hash"),
                    "new_hash": version_record.get("new_hash"),
                    "version_id": version_record.get("version_id"),
                }
            )

    audit_record = _append_consolidation_audit_record(
        normalized_workspace_id,
        {
            "kind": "memory_safe_consolidation_merge",
            "run_id": str(run_id or "").strip() or None,
            "proposal_id": proposal_id,
            "merged_files": merged_files,
            "source_daily_notes": daily_filenames,
            "preserved_entry_count": sum(len(values) for values in preserved_by_source.values()),
            "compacted_daily_notes": compacted_daily_notes,
            "compact_mode": normalized_compact_mode,
            "approved": bool(user_approved),
            "policy_allowed": bool(policy_allows),
            "actor": _normalize_memory_actor(actor),
            "merged_versions": merged_versions,
            "compacted_versions": compacted_versions,
        },
    )
    response["merged"] = True
    response["compacted_daily_notes"] = compacted_daily_notes
    response["audit_id"] = audit_record.get("audit_id")
    response["merged_versions"] = merged_versions
    response["compacted_versions"] = compacted_versions
    return response


def workspace_memory_snapshot(workspace_id: str, *, agent_install_id: str | None = None) -> WorkspaceMemorySnapshot:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_agent_install_id = str(agent_install_id or "").strip()
    entries = list_memory_entries(normalized_workspace_id, agent_install_id=normalized_agent_install_id or None)
    return WorkspaceMemorySnapshot(
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
        entries=entries,
        text=get_memory(normalized_workspace_id, agent_install_id=normalized_agent_install_id or None),
    )


def workspace_sidecar_dir(workspace_id: str, sidecar_name: str) -> Path:
    root = Path(_workspace_memory_store._MEMORY_DIR)
    root.mkdir(parents=True, exist_ok=True)
    workspace_token = _workspace_memory_store._normalize_workspace_token(_normalize_workspace_id(workspace_id))
    normalized_sidecar = str(sidecar_name or "").strip().lower() or "sidecar"
    path = root / workspace_token / normalized_sidecar
    path.mkdir(parents=True, exist_ok=True)
    return path


def _memory_file_versioning_dir(workspace_id: str) -> Path:
    return workspace_sidecar_dir(workspace_id, "memory_file_versions")


def _memory_file_version_log_path(workspace_id: str) -> Path:
    return _memory_file_versioning_dir(workspace_id) / "changes.jsonl"


def _append_memory_file_version_record(
    workspace_id: str,
    *,
    agent_install_id: str | None,
    actor: str,
    filename: str,
    old_content: str,
    new_content: str,
    reason: str,
    run_id: str | None = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_actor = _normalize_memory_actor(actor)
    normalized_filename = normalize_workspace_context_filename(filename)
    normalized_reason = " ".join(str(reason or "").split()).strip() or "memory_write"
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    old_text = str(old_content or "")
    new_text = str(new_content or "")
    old_hash = _sha256_text(old_text)
    new_hash = _sha256_text(new_text)
    version_id = hashlib.sha1(
        json.dumps(
            {
                "workspace_id": normalized_workspace_id,
                "agent_install_id": str(agent_install_id or "").strip() or None,
                "filename": normalized_filename,
                "old_hash": old_hash,
                "new_hash": new_hash,
                "reason": normalized_reason,
                "actor": normalized_actor,
                "created_at": created_at,
                "run_id": str(run_id or "").strip() or None,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:20]
    record = {
        "version_id": version_id,
        "workspace_id": normalized_workspace_id,
        "agent_install_id": str(agent_install_id or "").strip() or None,
        "actor": normalized_actor,
        "filename": normalized_filename,
        "old_hash": old_hash,
        "new_hash": new_hash,
        "reason": normalized_reason,
        "timestamp": created_at,
        "run_id": str(run_id or "").strip() or None,
        "metadata": dict(metadata or {}),
        "snapshot": new_text,
    }
    log_path = _memory_file_version_log_path(normalized_workspace_id)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return record


def list_memory_file_versions(
    workspace_id: str,
    filename: str,
    *,
    agent_install_id: str | None = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_agent_install_id = str(agent_install_id or "").strip() or None
    normalized_filename = normalize_workspace_context_filename(filename)
    log_path = _memory_file_version_log_path(normalized_workspace_id)
    if not log_path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in reversed(lines):
        if not str(line or "").strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("filename") or "").strip() != normalized_filename:
            continue
        if normalized_agent_install_id is not None and str(payload.get("agent_install_id") or "").strip() != normalized_agent_install_id:
            continue
        records.append(
            {
                "version_id": payload.get("version_id"),
                "workspace_id": payload.get("workspace_id"),
                "agent_install_id": payload.get("agent_install_id"),
                "actor": payload.get("actor"),
                "filename": payload.get("filename"),
                "old_hash": payload.get("old_hash"),
                "new_hash": payload.get("new_hash"),
                "reason": payload.get("reason"),
                "timestamp": payload.get("timestamp"),
                "run_id": payload.get("run_id"),
                "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            }
        )
        if len(records) >= max(1, min(int(limit or 20), 200)):
            break
    return records


def rollback_memory_file_version(
    workspace_id: str,
    filename: str,
    *,
    version_id: str,
    actor: str = _MEMORY_DEFAULT_ACTOR,
    reason: str = "rollback",
    run_id: str | None = None,
    agent_install_id: str | None = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_filename = normalize_workspace_context_filename(filename)
    normalized_agent_install_id = str(agent_install_id or "").strip() or None
    target_version_id = str(version_id or "").strip()
    if not target_version_id:
        raise ValueError("rollback version_id is required.")
    log_path = _memory_file_version_log_path(normalized_workspace_id)
    if not log_path.exists():
        raise ValueError("No memory version history found.")
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        raise ValueError("Unable to read memory version history.") from exc

    target_payload: Optional[Dict[str, Any]] = None
    for line in reversed(lines):
        if not str(line or "").strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("version_id") or "").strip() != target_version_id:
            continue
        if str(payload.get("filename") or "").strip() != normalized_filename:
            continue
        if normalized_agent_install_id is not None and str(payload.get("agent_install_id") or "").strip() != normalized_agent_install_id:
            continue
        target_payload = payload
        break
    if not target_payload:
        raise ValueError("Requested memory version was not found.")
    snapshot = str(target_payload.get("snapshot") or "")
    old_content = read_workspace_context_file(
        normalized_filename,
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    saved = write_workspace_context_file(
        normalized_filename,
        snapshot,
        workspace_id=normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
    )
    record = _append_memory_file_version_record(
        normalized_workspace_id,
        agent_install_id=normalized_agent_install_id,
        actor=actor,
        filename=normalized_filename,
        old_content=old_content,
        new_content=str(saved.get("content") or ""),
        reason=reason,
        run_id=run_id,
        metadata={"rollback_from_version_id": target_version_id},
    )
    return {
        "workspace_id": normalized_workspace_id,
        "agent_install_id": normalized_agent_install_id,
        "filename": normalized_filename,
        "rolled_back_to_version_id": target_version_id,
        "new_version_id": record.get("version_id"),
        "new_hash": record.get("new_hash"),
    }


def _summary_bridge_dir(workspace_id: str) -> Path:
    return workspace_sidecar_dir(workspace_id, "hybrid_summary_bridge")


def _summary_bridge_log_path(workspace_id: str) -> Path:
    return _summary_bridge_dir(workspace_id) / "payloads.jsonl"


def _consolidation_audit_dir(workspace_id: str) -> Path:
    return workspace_sidecar_dir(workspace_id, "memory_consolidation_audit")


def _consolidation_audit_log_path(workspace_id: str) -> Path:
    return _consolidation_audit_dir(workspace_id) / "merges.jsonl"


def _append_consolidation_audit_record(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record_payload = dict(payload or {})
    audit_id = str(record_payload.get("audit_id") or "").strip()
    if not audit_id:
        audit_id = hashlib.sha1(
            json.dumps(
                {
                    "workspace_id": normalized_workspace_id,
                    "created_at": created_at,
                    "payload": record_payload,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:16]
    record = {
        "audit_id": audit_id,
        "workspace_id": normalized_workspace_id,
        "created_at": created_at,
        **record_payload,
    }
    log_path = _consolidation_audit_log_path(normalized_workspace_id)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return record


def _read_summary_bridge_records(workspace_id: str) -> List[Dict[str, Any]]:
    path = _summary_bridge_log_path(workspace_id)
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in raw_lines:
        if not str(line or "").strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(dict(payload))
    return items


def publish_hybrid_summary_bridge_payload(
    workspace_id: str,
    *,
    payload_class: str,
    summary: str,
    items: Optional[List[Dict[str, Any]]] = None,
    memory_layer: Optional[str] = None,
    agent_install_id: str | None = None,
    run_id: str | None = None,
    source_runtime_mode: str = "local_secure",
    last_safe: bool = False,
    allowed_payload_classes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from server_modules import hybrid_policy_service

    record_payload = hybrid_policy_service.build_summary_bridge_payload_record(
        payload_class=payload_class,
        summary=summary,
        items=items,
        memory_layer=memory_layer,
        agent_install_id=agent_install_id,
        run_id=run_id,
        source_runtime_mode=source_runtime_mode,
        last_safe=last_safe,
        allowed_payload_classes=allowed_payload_classes,
    )
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record_id = hashlib.sha1(
        json.dumps(
            {
                "workspace_id": _normalize_workspace_id(workspace_id),
                "payload_class": record_payload["payload_class"],
                "summary": record_payload["summary"],
                "agent_install_id": record_payload.get("agent_install_id"),
                "run_id": record_payload.get("run_id"),
                "created_at": created_at,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    record = {
        "record_id": record_id,
        "workspace_id": _normalize_workspace_id(workspace_id),
        "created_at": created_at,
        **record_payload,
    }
    path = _summary_bridge_log_path(workspace_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def list_hybrid_summary_bridge_payloads(
    workspace_id: str,
    *,
    payload_classes: Optional[List[str]] = None,
    agent_install_id: str | None = None,
    last_safe_only: bool = False,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    from server_modules import hybrid_policy_service

    records = _read_summary_bridge_records(workspace_id)
    allowed = set(hybrid_policy_service.normalize_summary_bridge_payload_classes(payload_classes or []))
    normalized_agent_install_id = str(agent_install_id or "").strip() or None
    items: List[Dict[str, Any]] = []
    for record in records:
        payload_class = str(record.get("payload_class") or "").strip()
        if payload_class not in hybrid_policy_service.SUMMARY_BRIDGE_ALLOWED_PAYLOADS:
            continue
        if allowed and payload_class not in allowed:
            continue
        record_agent_install_id = str(record.get("agent_install_id") or "").strip() or None
        if normalized_agent_install_id is not None and record_agent_install_id != normalized_agent_install_id:
            continue
        if last_safe_only and not bool(record.get("last_safe")):
            continue
        items.append(dict(record))
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    safe_limit = max(1, min(int(limit or 10), 50))
    return items[:safe_limit]


def hybrid_summary_bridge_status(
    workspace_id: str,
    *,
    payload_classes: Optional[List[str]] = None,
    agent_install_id: str | None = None,
) -> Dict[str, Any]:
    from server_modules import hybrid_policy_service

    records = list_hybrid_summary_bridge_payloads(
        workspace_id,
        payload_classes=payload_classes,
        agent_install_id=agent_install_id,
        limit=100,
    )
    return hybrid_policy_service.summarize_summary_bridge_records(
        records,
        payload_classes=payload_classes,
    )


def shared_operational_board_access_contract() -> Dict[str, Any]:
    return {
        "default_access": "permissioned_shared_read",
        "permission_tiers": ["read", "propose_update", "publish_update"],
        "published_entries_visible_by_default": True,
        "private_memory_import_allowed": False,
    }


def artifacts_history_access_contract() -> Dict[str, Any]:
    return {
        "default_access": "explicit_artifact_exchange",
        "cross_install_exchange_mode": "artifacts_only",
        "raw_private_memory_embeds_allowed": False,
        "shared_board_publish_required_for_workspace_visibility": True,
    }


def list_shared_operational_board_entries(
    workspace_id: str,
    *,
    actor_permission: str = "read",
    include_proposed: bool = False,
    entry_kind: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    from server_modules import shared_operational_board_service

    return shared_operational_board_service.list_shared_operational_board_entries(
        workspace_id,
        actor_permission=actor_permission,
        include_proposed=include_proposed,
        entry_kind=entry_kind,
        limit=limit,
    )


def get_shared_operational_board_history(
    workspace_id: str,
    entry_id: str,
    *,
    actor_permission: str = "read",
    include_proposed: bool = False,
) -> List[Dict[str, Any]]:
    from server_modules import shared_operational_board_service

    return shared_operational_board_service.get_shared_operational_board_history(
        workspace_id,
        entry_id,
        actor_permission=actor_permission,
        include_proposed=include_proposed,
    )


def query_memory(query: MemoryQuery) -> MemoryResult:
    normalized_workspace_id = _normalize_workspace_id(query.workspace_id)
    normalized_agent_install_id = str(query.agent_install_id or "").strip()
    safe_limit = max(1, min(int(query.limit or 5), 20))
    normalized_query_text = str(query.text or "").strip()
    tracer = get_tracer("server_modules.memory_service")
    with tracer.start_as_current_span("memory_service.query_memory") as span:
        set_span_attributes(
            span,
            {
                "memory_type": "workspace_query",
                "workspace_id": normalized_workspace_id,
                "agent_install_id": normalized_agent_install_id or None,
                "tenant_id": str(query.tenant_id or "").strip() or "default",
                "actor_type": str(query.metadata.get("actor_type") or "").strip() or "runtime",
                "run_id": str(query.metadata.get("run_id") or "").strip() or None,
                "memory_limit": safe_limit,
            },
        )
        items: List[MemoryItem] = []

        if normalized_query_text:
            for entry in semantic_search(
                normalized_workspace_id,
                normalized_query_text,
                top_k=safe_limit,
                agent_install_id=normalized_agent_install_id or None,
            ):
                key = str(entry.get("key") or "").strip()
                content = str(entry.get("content") or "").strip()
                if not content:
                    continue
                item_text = f"{key}: {content}" if key else content
                items.append(
                    MemoryItem(
                        source="workspace_memory",
                        text=item_text,
                        score=float(entry.get("score") or 0.0),
                        metadata={
                            "key": key,
                            "content": content,
                            "created_at": float(entry.get("created_at") or 0.0),
                            "updated_at": float(entry.get("updated_at") or 0.0),
                        },
                    )
                )

        context_blocks: List[str] = []
        if query.include_workspace_context:
            memory_text = get_memory(normalized_workspace_id, agent_install_id=normalized_agent_install_id or None)
            if memory_text:
                context_blocks.append(f"Runtime Memory Facts\n{memory_text}")
            recent_logs = get_recent_logs(
                normalized_workspace_id,
                days=7,
                agent_install_id=normalized_agent_install_id or None,
            )
            if recent_logs:
                context_blocks.append(f"Recent Daily Logs\n{recent_logs[:6000].rstrip()}")

        set_span_attributes(span, {"memory_result_count": len(items), "context_block_count": len(context_blocks)})
        return MemoryResult(items=items, context_blocks=context_blocks)


def direct_chat_memory_context_message(
    workspace_id: str,
    *,
    system_prefix: str,
    agent_install_id: str | None = None,
) -> Dict[str, str] | None:
    from server_modules.conversation_memory_policy import DIRECT_CHAT_PROFILE, get_memory_policy_profile

    payload = workspace_context_memory_adapter.load_workspace_context_payload(
        workspace_id=workspace_id,
        memory_query="",
        agent_install_id=agent_install_id,
        policy_profile=get_memory_policy_profile(DIRECT_CHAT_PROFILE),
        include_memory_context=True,
    )
    return workspace_context_memory_adapter.build_workspace_memory_context_message(
        system_prefix=system_prefix,
        contextual_blocks=list(payload.get("contextual_blocks") or []),
    )


def direct_chat_workspace_context_text(
    workspace_id: str,
    *,
    memory_query: str = "",
    agent_install_id: str | None = None,
) -> str:
    from server_modules.conversation_memory_policy import DIRECT_CHAT_PROFILE, get_memory_policy_profile

    payload = workspace_context_memory_adapter.load_workspace_context_payload(
        workspace_id=workspace_id,
        memory_query=memory_query,
        agent_install_id=agent_install_id,
        policy_profile=get_memory_policy_profile(DIRECT_CHAT_PROFILE),
    )
    return workspace_context_memory_adapter.render_workspace_context_text(
        list(payload.get("contextual_blocks") or []),
    )


def store_direct_chat_memory_fact(workspace_id: str, fact: str) -> None:
    normalized_fact = re.sub(r"\s+", " ", str(fact or "").strip())
    if not normalized_fact:
        return
    memory_key = f"fact-{hashlib.sha1(normalized_fact.encode('utf-8')).hexdigest()[:16]}"
    save_memory(workspace_id, memory_key, normalized_fact)


def build_direct_chat_daily_log_summary(*, user_message: str, assistant_reply: str) -> str:
    return memory_summary_service.build_direct_chat_daily_log_summary(
        user_message=user_message,
        assistant_reply=assistant_reply,
    )


def save_direct_chat_daily_log_summary(*, workspace_id: str, user_message: str, assistant_reply: str) -> str:
    summary = build_direct_chat_daily_log_summary(
        user_message=user_message,
        assistant_reply=assistant_reply,
    )
    if summary:
        save_daily_log(workspace_id, summary)
    return summary


def parse_direct_chat_memory_facts(raw_text: str) -> List[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    candidate = text
    fenced = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text, re.IGNORECASE)
    if fenced:
        candidate = str(fenced.group(1) or "").strip()
    else:
        array_match = re.search(r"\[[\s\S]*\]", text)
        if array_match:
            candidate = str(array_match.group(0) or "").strip()
    parsed: Any = None
    try:
        parsed = json.loads(candidate)
    except Exception:
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
    if isinstance(parsed, dict):
        facts_value = parsed.get("facts")
        if isinstance(facts_value, list):
            parsed = facts_value
    if not isinstance(parsed, list):
        return []
    normalized: List[str] = []
    seen: set[str] = set()
    for item in parsed:
        fact = re.sub(r"\s+", " ", str(item or "").strip())
        if not fact:
            continue
        normalized_key = fact.lower()
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        normalized.append(fact)
    return normalized


def persist_direct_chat_memory_best_effort(
    *,
    workspace_id: str,
    provider: str | None,
    model: str | None,
    credentials: Dict[str, Any] | None,
    reasoning_effort: str,
    prior_messages: List[Dict[str, str]],
    user_message: str,
    assistant_reply: str,
    generate_reply: Any,
    extraction_prompt: str,
    extraction_system_prompt: str,
) -> None:
    normalized_user_message = str(user_message or "").strip()
    normalized_assistant_reply = str(assistant_reply or "").strip()
    if not normalized_user_message or not normalized_assistant_reply:
        return
    try:
        save_direct_chat_daily_log_summary(
            workspace_id=workspace_id,
            user_message=normalized_user_message,
            assistant_reply=normalized_assistant_reply,
        )
    except Exception:
        pass
    extraction_prior_messages: List[Dict[str, str]] = []
    for item in list(prior_messages or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = workspace_context_memory_adapter.strip_red_facts_from_external_context(item.get("content") or "")
        if content:
            extraction_prior_messages.append({"role": role, "content": content})
    safe_user_message = workspace_context_memory_adapter.strip_red_facts_from_external_context(normalized_user_message)
    safe_assistant_reply = workspace_context_memory_adapter.strip_red_facts_from_external_context(normalized_assistant_reply)
    if safe_user_message:
        extraction_prior_messages.append({"role": "user", "content": safe_user_message})
    if safe_assistant_reply:
        extraction_prior_messages.append({"role": "assistant", "content": safe_assistant_reply})
    if not extraction_prior_messages:
        return
    extraction_context = {
        "workspace_id": workspace_id,
        "provider": provider,
        "model": model,
        "source": "chat_direct_memory_extract",
        "reasoning_effort": reasoning_effort,
        "tools": [],
    }
    extraction_metadata: Dict[str, Any] = {
        "provider": provider,
        "model": model,
        "source": "chat_direct_memory_extract",
        "reasoning_effort": reasoning_effort,
        "tools": [],
    }
    if isinstance(credentials, dict) and credentials:
        extraction_metadata["credentials"] = credentials
    try:
        extraction_reply, _usage, _attempted, extraction_error = generate_reply(
            context=extraction_context,
            metadata=extraction_metadata,
            user_goal=extraction_prompt,
            system_prompt=extraction_system_prompt,
            prior_messages=extraction_prior_messages,
        )
        if extraction_error or not extraction_reply:
            return
        extracted_facts = parse_direct_chat_memory_facts(extraction_reply)
        for fact in extracted_facts:
            store_direct_chat_memory_fact(workspace_id, fact)
    except Exception:
        return


def find_workspace_memory_entry(workspace_id: str, query: str) -> Dict[str, Any] | None:
    normalized_query = _normalize_memory_lookup_key(query)
    if not normalized_query:
        return None
    entries = list_memory_entries(workspace_id)
    for entry in entries:
        entry_key = _normalize_memory_lookup_key(str(entry.get("key") or ""))
        if entry_key == normalized_query:
            return entry
    for entry in entries:
        entry_key = _normalize_memory_lookup_key(str(entry.get("key") or ""))
        entry_content = str(entry.get("content") or "").strip().lower()
        if normalized_query in entry_key or normalized_query.replace("_", " ") in entry_content:
            return entry
    return None


def memory_suggestion_prompts(workspace_id: str, *, limit: int = 2) -> List[str]:
    prompts: List[str] = []
    for entry in list_memory_entries(workspace_id)[: max(1, int(limit or 2))]:
        fact = re.sub(r"\s+", " ", str(entry.get("content") or "").strip())
        if not fact:
            continue
        prompts.append(f"Use my saved context: {fact[:120].rstrip()}")
    return prompts


def parse_no_provider_memory_write(message: str) -> Dict[str, str] | None:
    text = str(message or "").strip()
    if not text:
        return None
    match = re.search(r"remember(?:\s+that)?\s+my\s+name\s+is\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        value = _trim_memory_value(match.group(1))
        if value:
            return {"key": "name", "value": value, "display_key": "name"}
    match = re.search(r"remember\s*:\s*([a-z0-9_. -]+?)\s*=\s*(.+)$", text, flags=re.IGNORECASE)
    if match:
        key = _normalize_memory_lookup_key(match.group(1))
        value = _trim_memory_value(match.group(2))
        if key and value:
            return {"key": key, "value": value, "display_key": str(match.group(1) or "").strip()}
    match = re.search(r"remember\s+that\s+([a-z0-9_. -]+?)\s*=\s*(.+)$", text, flags=re.IGNORECASE)
    if match:
        key = _normalize_memory_lookup_key(match.group(1))
        value = _trim_memory_value(match.group(2))
        if key and value:
            return {"key": key, "value": value, "display_key": str(match.group(1) or "").strip()}
    match = re.search(r"remember\s+that\s+([a-z0-9_. -]+?)\s+is\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        raw_key = str(match.group(1) or "").strip()
        key = _normalize_memory_lookup_key(raw_key)
        value = _trim_memory_value(match.group(2))
        if key and value:
            return {"key": key, "value": value, "display_key": raw_key}
    return None


def parse_no_provider_memory_read(message: str) -> str | None:
    text = str(message or "").strip()
    compact = re.sub(r"\s+", " ", text.lower()).strip()
    compact = compact.rstrip("?.! ")
    if not text:
        return None
    if compact in {"what is my name", "what's my name", "recall my name"}:
        return "name"
    question_text = text.rstrip("?.! ").strip()
    for pattern in (
        r"what\s+is\s+([a-z0-9_. -]+)$",
        r"what'?s\s+([a-z0-9_. -]+)$",
        r"recall\s+([a-z0-9_. -]+)$",
        r"what\s+did\s+i\s+say\s+about\s+([a-z0-9_. -]+)$",
    ):
        match = re.search(pattern, question_text, flags=re.IGNORECASE)
        if match:
            return str(match.group(1) or "").strip()
    return None


def handle_no_provider_memory_request(workspace_id: str, message: str) -> str | None:
    memory_write = parse_no_provider_memory_write(message)
    if memory_write is not None:
        save_memory(workspace_id, memory_write["key"], memory_write["value"])
        return None
    memory_read = parse_no_provider_memory_read(message)
    if memory_read is not None:
        entry = find_workspace_memory_entry(workspace_id, memory_read)
        if entry is None:
            return None
        key = str(entry.get("key") or "").strip()
        content = str(entry.get("content") or "").strip()
        if not key or not content:
            return None
        return f"{key} = {content}"
    return None


def runtime_memory_search(
    *,
    query: str,
    bucket: str | None = None,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
    profile_id: str | None = None,
    project_id: str | None = None,
    session_key: str | None = None,
    k: int = 5,
) -> Dict[str, Any]:
    tracer = get_tracer("server_modules.memory_service")
    with tracer.start_as_current_span("memory_service.runtime_memory_search") as span:
        _memory_manager_or_503()
        normalized_bucket = _normalize_memory_bucket(bucket, required=False)
        normalized_workspace_id = _runtime_workspace_id(workspace_id)
        set_span_attributes(
            span,
            {
                "memory_type": normalized_bucket or "scoped_search",
                "workspace_id": normalized_workspace_id,
                "agent_install_id": str(agent_install_id or "").strip() or None,
                "tenant_id": "default",
                "actor_type": "runtime",
                "memory_limit": int(k),
            },
        )
        items = _memory_search_scoped(
            query=str(query or "").strip(),
            bucket=normalized_bucket,
            workspace_id=normalized_workspace_id,
            agent_install_id=str(agent_install_id or "").strip() or None,
            profile_id=str(profile_id or "").strip() or None,
            project_id=str(project_id or "").strip() or None,
            session_key=str(session_key or "").strip() or None,
            k=int(k),
        )
        set_span_attributes(span, {"memory_result_count": len(items)})
        return {
            "ok": True,
            "query": str(query or "").strip(),
            "bucket": normalized_bucket,
            "workspace_id": normalized_workspace_id,
            "agent_install_id": str(agent_install_id or "").strip() or None,
            "count": len(items),
            "items": items,
        }


def runtime_memory_upsert(
    *,
    text: str,
    bucket: str,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
    profile_id: str | None = None,
    project_id: str | None = None,
    session_key: str | None = None,
    source: str | None = None,
    retention_days: int | None = None,
    metadata: Dict[str, Any] | None = None,
    memory_id: str | None = None,
) -> Dict[str, Any]:
    manager = _memory_manager_or_503()
    normalized_bucket = _normalize_memory_bucket(bucket, required=True) or "session"
    normalized_workspace_id = _runtime_workspace_id(workspace_id)
    normalized_retention_days = int(retention_days or runtime_memory.ORION_MEMORY_RETENTION_DAYS_DEFAULT)
    expires_at = (_runtime_utc_now() + timedelta(days=normalized_retention_days)).isoformat().replace("+00:00", "Z")
    record_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    record_metadata.update(
        {
            "bucket": normalized_bucket,
            "workspace_id": normalized_workspace_id,
            "agent_install_id": str(agent_install_id or "").strip(),
            "profile_id": str(profile_id or "").strip(),
            "project_id": str(project_id or "").strip(),
            "session_key": str(session_key or "").strip(),
            "source": str(source or "api").strip().lower() or "api",
            "retention_days": normalized_retention_days,
            "expires_at": expires_at,
        }
    )
    if isinstance(memory_id, str) and memory_id.strip():
        record_metadata["id"] = memory_id.strip()
    stored_id = manager.upsert_memory(str(text or "").strip(), record_metadata)
    return {
        "ok": True,
        "id": stored_id,
        "bucket": normalized_bucket,
        "workspace_id": normalized_workspace_id,
        "agent_install_id": str(agent_install_id or "").strip() or None,
        "retention_days": normalized_retention_days,
        "expires_at": expires_at,
    }


def configure_runtime_memory(**kwargs: Any) -> None:
    runtime_memory._configure_runtime_memory(**kwargs)


def _runtime_workspace_id(value: Any) -> str:
    normalized = runtime_memory._normalize_workspace_id(value)
    return str(normalized or "default").strip() or "default"


def _runtime_utc_now() -> Any:
    return runtime_memory._utc_now()


def _runtime_utc_now_iso() -> str:
    return runtime_memory._utc_now_iso()


def _memory_manager_or_503() -> Any:
    return runtime_memory._memory_manager_or_503()


def _normalize_memory_bucket(value: Any, *, required: bool = True) -> Optional[str]:
    return runtime_memory._normalize_memory_bucket(value, required=required)


def _memory_item_matches_scope(
    metadata: Dict[str, Any],
    *,
    bucket: Optional[str],
    workspace_id: Optional[str],
    agent_install_id: Optional[str],
    profile_id: Optional[str],
    project_id: Optional[str],
    session_key: Optional[str],
) -> bool:
    if bucket and str(metadata.get("bucket") or "").strip().lower() != bucket:
        return False
    if workspace_id and str(metadata.get("workspace_id") or "").strip() != workspace_id:
        return False
    item_agent_install_id = str(metadata.get("agent_install_id") or "").strip()
    if agent_install_id:
        if item_agent_install_id and item_agent_install_id != agent_install_id:
            return False
        if not item_agent_install_id:
            pass
    if profile_id and str(metadata.get("profile_id") or "").strip() != profile_id:
        return False
    if project_id and str(metadata.get("project_id") or "").strip() != project_id:
        return False
    if session_key and str(metadata.get("session_key") or "").strip() != session_key:
        return False
    expires_value = metadata.get("expires_at")
    expires_at = runtime_memory._parse_utc_ts(expires_value) if expires_value else None
    if expires_at and expires_at <= _runtime_utc_now():
        return False
    return True


def _memory_search_scoped(
    query: str,
    *,
    bucket: Optional[str] = None,
    workspace_id: Optional[str] = None,
    agent_install_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    project_id: Optional[str] = None,
    session_key: Optional[str] = None,
    k: int = 5,
) -> List[Dict[str, Any]]:
    tracer = get_tracer("server_modules.memory_service")
    with tracer.start_as_current_span("memory_service.search_scoped") as span:
        set_span_attributes(
            span,
            {
                "memory_type": str(bucket or "scoped_search").strip() or "scoped_search",
                "workspace_id": str(workspace_id or "default").strip() or "default",
                "agent_install_id": str(agent_install_id or "").strip() or None,
                "tenant_id": "default",
                "actor_type": "runtime",
                "run_id": None,
                "memory_limit": int(k),
            },
        )
        manager = runtime_memory._memory_manager()
        if manager is None:
            set_span_attributes(span, {"memory_result_count": 0})
            return []
        try:
            fetch_limit = max(int(k), 1)
            fetch_limit = min(max(fetch_limit * 8, 24), 120)
            raw_results = manager.search_memory(query, fetch_limit)
        except Exception as exc:
            try:
                span.record_exception(exc)
            except Exception:
                pass
            return []
        if not isinstance(raw_results, list):
            set_span_attributes(span, {"memory_result_count": 0})
            return []

        out: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            mem_id = str(item.get("id") or "").strip()
            if not mem_id or mem_id in seen_ids:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if not _memory_item_matches_scope(
                metadata,
                bucket=bucket,
                workspace_id=workspace_id,
                agent_install_id=agent_install_id,
                profile_id=profile_id,
                project_id=project_id,
                session_key=session_key,
            ):
                continue
            seen_ids.add(mem_id)
            out.append(
                {
                    "id": mem_id,
                    "text": str(item.get("text") or ""),
                    "score": item.get("score"),
                    "metadata": metadata,
                }
            )
            if len(out) >= int(k):
                break
        set_span_attributes(span, {"memory_result_count": len(out)})
        return out


def memory_search_scoped(
    query: str,
    *,
    bucket: Optional[str] = None,
    workspace_id: Optional[str] = None,
    agent_install_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    project_id: Optional[str] = None,
    session_key: Optional[str] = None,
    k: int = 5,
) -> List[Dict[str, Any]]:
    return _memory_search_scoped(
        query,
        bucket=bucket,
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
        profile_id=profile_id,
        project_id=project_id,
        session_key=session_key,
        k=k,
    )


def _memory_scope_from_context(context: Dict[str, Any]) -> Dict[str, str]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    workspace_id = _runtime_workspace_id(context.get("workspace_id") or metadata.get("workspace_id"))
    # Memory namespace must be bound to the currently resolved active install.
    # Do not fall back to workspace install metadata, which can be inherited from
    # parent orchestration context and leak cross-install scope.
    agent_install_id = str(metadata.get("active_agent_install_id") or "").strip()
    session_key = str(metadata.get("session_key") or "").strip()
    if not session_key:
        chat_id = str(metadata.get("chat_id") or "").strip()
        if chat_id:
            session_key = f"telegram:{chat_id}"
    return {
        "workspace_id": workspace_id,
        "agent_install_id": agent_install_id,
        "profile_id": str(metadata.get("profile_id") or metadata.get("user_id") or "").strip(),
        "project_id": str(metadata.get("project_id") or context.get("workflow_id") or "").strip(),
        "session_key": session_key,
    }


def _trim_memory_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    reads = trace.get("reads") if isinstance(trace.get("reads"), list) else []
    writes = trace.get("writes") if isinstance(trace.get("writes"), list) else []
    return {
        "enabled": bool(trace.get("enabled")),
        "reads": [runtime_memory._json_safe(item) for item in reads[-20:]],
        "writes": [runtime_memory._json_safe(item) for item in writes[-20:]],
        "last_error": str(trace.get("last_error") or "").strip() or None,
        "updated_at": str(trace.get("updated_at") or "").strip() or None,
    }


def trim_memory_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    return _trim_memory_trace(trace)


def _memory_prompt_context_block(context: Dict[str, Any], max_items: int = 6) -> str:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    memory_ctx = metadata.get("memory_context") if isinstance(metadata.get("memory_context"), dict) else {}
    items = memory_ctx.get("items") if isinstance(memory_ctx.get("items"), list) else []
    if not items:
        return "Memory Context:\n- none"
    lines: List[str] = []
    for item in items[: max(1, max_items)]:
        if not isinstance(item, dict):
            continue
        item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        bucket = str(item_metadata.get("bucket") or "unknown").strip().lower()
        text = runtime_memory._compact_event_text(item.get("text"), limit=220)
        if text:
            lines.append(f"- ({bucket}) {text}")
    if not lines:
        return "Memory Context:\n- none"
    return "Memory Context:\n" + "\n".join(lines)


def memory_prompt_context_block(context: Dict[str, Any], max_items: int = 6) -> str:
    return _memory_prompt_context_block(context, max_items=max_items)


def _run_result_summary(run: Dict[str, Any]) -> str:
    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
    if isinstance(run.get("result"), str) and str(run.get("result")).strip():
        return str(run.get("result")).strip()
    if isinstance(result_data.get("summary"), str) and str(result_data.get("summary")).strip():
        return str(result_data.get("summary")).strip()
    return ""


def _memory_health_snapshot() -> Dict[str, Any]:
    db_path = Path(runtime_memory.ORION_MEMORY_DB_PATH)
    rows = 0
    sqlite_error: Optional[str] = None
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM fallback_memory")
            row = cur.fetchone()
            rows = int(row[0] if row and row[0] is not None else 0)
            conn.close()
        except Exception as exc:
            sqlite_error = str(exc)
    manager = runtime_memory._memory_manager()
    lancedb_initialized = bool(getattr(getattr(manager, "lancedb", None), "_initialized", False)) if manager else False
    return {
        "enabled": runtime_memory.ORION_MEMORY_ENABLED,
        "manager_ready": manager is not None,
        "manager_error": runtime_memory.MEMORY_MANAGER_ERROR,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "db_size_bytes": int(db_path.stat().st_size) if db_path.exists() else 0,
        "sqlite_rows": rows,
        "sqlite_error": sqlite_error,
        "lancedb_uri": runtime_memory.ORION_MEMORY_LANCEDB_URI,
        "lancedb_initialized": lancedb_initialized,
    }


def memory_health_snapshot() -> Dict[str, Any]:
    return _memory_health_snapshot()


def _hydrate_run_memory_context(run_id: str, run: Dict[str, Any]) -> None:
    trace = run.setdefault(
        "memory_trace",
        {"enabled": runtime_memory.ORION_MEMORY_ENABLED, "reads": [], "writes": [], "last_error": None, "updated_at": None},
    )
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    tracer = get_tracer("server_modules.memory_service")
    with tracer.start_as_current_span("memory_service.hydrate_run_context") as span:
        set_span_attributes(
            span,
            {
                "memory_type": "run_context",
                "workspace_id": _runtime_workspace_id(context.get("workspace_id") or metadata.get("workspace_id")),
                "tenant_id": str(context.get("tenant_id") or metadata.get("tenant_id") or "default").strip() or "default",
                "actor_type": str(metadata.get("request_actor_type") or "").strip() or "runtime",
                "run_id": str(run_id or "").strip() or None,
            },
        )
        if not runtime_memory.ORION_MEMORY_ENABLED:
            trace["enabled"] = False
            trace["updated_at"] = _runtime_utc_now_iso()
            return
        manager = runtime_memory._memory_manager()
        if manager is None:
            trace["last_error"] = runtime_memory.MEMORY_MANAGER_ERROR or "memory_unavailable"
            trace["updated_at"] = _runtime_utc_now_iso()
            return

        if str(metadata.get("memory_read_enabled") or "1").strip().lower() in {"0", "false", "no", "off"}:
            trace["updated_at"] = _runtime_utc_now_iso()
            return

        user_goal = str(context.get("user_goal") or "").strip()
        business_plan = str(context.get("business_plan") or "").strip()
        query = "\n".join([part for part in [user_goal, business_plan] if part]).strip() or "recent context"
        try:
            read_k = max(1, min(int(metadata.get("memory_read_k") or runtime_memory.ORION_MEMORY_READ_K), 20))
        except Exception:
            read_k = runtime_memory.ORION_MEMORY_READ_K

        scope = _memory_scope_from_context(context)
        bucket_queries: List[tuple[str, Dict[str, Optional[str]]]] = []
        if scope.get("profile_id"):
            bucket_queries.append(("profile", {"profile_id": scope.get("profile_id")}))
        if scope.get("project_id"):
            bucket_queries.append(("project", {"project_id": scope.get("project_id")}))
        if scope.get("session_key"):
            bucket_queries.append(("session", {"session_key": scope.get("session_key")}))
        if not bucket_queries:
            bucket_queries.append(("session", {"session_key": f"run:{run_id}"}))

        aggregated: List[Dict[str, Any]] = []
        read_records: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()
        for bucket, bucket_scope in bucket_queries:
            items = _memory_search_scoped(
                query,
                bucket=bucket,
                workspace_id=scope.get("workspace_id"),
                agent_install_id=scope.get("agent_install_id"),
                profile_id=bucket_scope.get("profile_id"),
                project_id=bucket_scope.get("project_id"),
                session_key=bucket_scope.get("session_key"),
                k=read_k,
            )
            read_records.append({"bucket": bucket, "count": len(items), "k": read_k})
            for item in items:
                mem_id = str(item.get("id") or "").strip()
                if not mem_id or mem_id in seen_ids:
                    continue
                seen_ids.add(mem_id)
                aggregated.append(item)

        memory_context = {
            "query": runtime_memory._compact_event_text(query, limit=500),
            "scope": scope,
            "items": aggregated[: max(1, read_k * 2)],
            "count": len(aggregated),
        }
        metadata["memory_context"] = memory_context
        context["metadata"] = metadata
        run["context"] = context
        trace_reads = trace.get("reads") if isinstance(trace.get("reads"), list) else []
        trace_reads.extend(read_records)
        trace["reads"] = trace_reads[-20:]
        trace["updated_at"] = _runtime_utc_now_iso()
        run["memory_trace"] = trace

        runtime_memory._emit_log(
            run["logs"],
            "info",
            f"Memory context loaded: {len(aggregated)} item(s).",
            event="memory_context",
            data={"query": memory_context["query"], "scope": scope, "count": len(aggregated)},
        )
        set_span_attributes(span, {"memory_result_count": len(aggregated)})


def hydrate_run_memory_context(run_id: str, run: Dict[str, Any]) -> None:
    _hydrate_run_memory_context(run_id, run)


@contextmanager
def _suspend_run_persistence_notifications(run: Dict[str, Any]):
    payload = getattr(getattr(run, "record", None), "payload", None)
    if payload is None or not hasattr(payload, "_suspend_notifications"):
        yield
        return
    previous = bool(getattr(payload, "_suspend_notifications", False))
    payload._suspend_notifications = True
    try:
        yield
    finally:
        payload._suspend_notifications = previous


def _persist_run_memory(run_id: str, run: Dict[str, Any]) -> None:
    existing_trace = run.get("memory_trace") if isinstance(run.get("memory_trace"), dict) else {}
    trace: Dict[str, Any] = {
        "enabled": bool(existing_trace.get("enabled", runtime_memory.ORION_MEMORY_ENABLED)),
        "reads": list(existing_trace.get("reads") or []),
        "writes": list(existing_trace.get("writes") or []),
        "last_error": existing_trace.get("last_error"),
        "updated_at": existing_trace.get("updated_at"),
    }
    if not runtime_memory.ORION_MEMORY_ENABLED:
        trace["enabled"] = False
        trace["updated_at"] = _runtime_utc_now_iso()
        with _suspend_run_persistence_notifications(run):
            run["memory_trace"] = trace
        return
    manager = runtime_memory._memory_manager()
    if manager is None:
        trace["last_error"] = runtime_memory.MEMORY_MANAGER_ERROR or "memory_unavailable"
        trace["updated_at"] = _runtime_utc_now_iso()
        with _suspend_run_persistence_notifications(run):
            run["memory_trace"] = trace
        return
    if str(run.get("status") or "").strip().lower() != "completed":
        trace["updated_at"] = _runtime_utc_now_iso()
        with _suspend_run_persistence_notifications(run):
            run["memory_trace"] = trace
        return

    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    if str(metadata.get("memory_write_enabled") or "1").strip().lower() in {"0", "false", "no", "off"}:
        trace["updated_at"] = _runtime_utc_now_iso()
        with _suspend_run_persistence_notifications(run):
            run["memory_trace"] = trace
        return

    scope = _memory_scope_from_context(context)
    goal = runtime_memory._compact_event_text(context.get("user_goal"), limit=700)
    summary = runtime_memory._compact_event_text(_run_result_summary(run), limit=1300)
    if not summary:
        trace["updated_at"] = _runtime_utc_now_iso()
        return
    memory_text = f"Goal: {goal or 'n/a'}\nResult: {summary}"[: runtime_memory.ORION_MEMORY_MAX_TEXT_CHARS]

    try:
        retention_days = max(1, min(int(metadata.get("memory_retention_days") or runtime_memory.ORION_MEMORY_RETENTION_DAYS_DEFAULT), 3650))
    except Exception:
        retention_days = runtime_memory.ORION_MEMORY_RETENTION_DAYS_DEFAULT
    expires_at = (_runtime_utc_now() + timedelta(days=retention_days)).isoformat().replace("+00:00", "Z")

    targets: List[Dict[str, str]] = []
    session_key = scope.get("session_key") or f"run:{run_id}"
    targets.append({"bucket": "session", "session_key": session_key})
    if scope.get("project_id"):
        targets.append({"bucket": "project", "project_id": scope["project_id"]})
    if scope.get("profile_id"):
        targets.append({"bucket": "profile", "profile_id": scope["profile_id"]})

    writes = list(trace.get("writes") or [])
    for target in targets:
        bucket = str(target.get("bucket") or "").strip().lower()
        if bucket not in runtime_memory.MEMORY_BUCKETS:
            continue
        record_metadata = {
            "bucket": bucket,
            "workspace_id": scope.get("workspace_id") or "default",
            "agent_install_id": scope.get("agent_install_id") or "",
            "profile_id": target.get("profile_id") or "",
            "project_id": target.get("project_id") or "",
            "session_key": target.get("session_key") or "",
            "source": "run_completion",
            "run_id": run_id,
            "engine": str(run.get("engine") or "").strip().lower(),
            "retention_days": retention_days,
            "expires_at": expires_at,
        }
        try:
            memory_id = manager.upsert_memory(memory_text, record_metadata)
            writes.append({"bucket": bucket, "id": memory_id, "scope": runtime_memory._json_safe(target)})
        except Exception as exc:
            trace["last_error"] = f"memory_write_failed:{exc}"

    trace["writes"] = writes[-20:]
    trace["updated_at"] = _runtime_utc_now_iso()
    with _suspend_run_persistence_notifications(run):
        run["memory_trace"] = trace
    runtime_memory._refresh_archived_run_snapshot(run_id, run)
    if writes:
        runtime_memory._emit_log(
            run["logs"],
            "info",
            f"Memory write completed: {len(writes)} item(s).",
            event="memory_write",
            data={"writes": writes[-len(targets):]},
        )


def persist_run_memory(run_id: str, run: Dict[str, Any]) -> None:
    _persist_run_memory(run_id, run)
