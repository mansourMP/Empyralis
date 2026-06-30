from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


TRANSCRIPT_EVENT_LIMIT = 200
TRANSCRIPT_EVENT_SCHEMA_VERSION = 1

_STREAM_EVENT_TYPES = {"trace", "step"}
_BLOCKED_KEYS = {
    "args",
    "args_preview",
    "arguments",
    "audit",
    "audit_event",
    "audit_event_id",
    "audit_event_type",
    "chain_of_thought",
    "context",
    "debug",
    "filters",
    "input",
    "messages",
    "model",
    "model_id",
    "modelId",
    "output",
    "parameters",
    "policy",
    "policy_metadata",
    "prompt",
    "provider",
    "provider_id",
    "providerId",
    "raw",
    "raw_input",
    "raw_output",
    "reasoning",
    "result",
    "request_id",
    "requestId",
    "specialist_id",
    "system_prompt",
    "text",
    "trace_id",
    "traceId",
    "tool_args",
}
_SAFE_DATA_KEYS = {
    "action",
    "action_id",
    "actor",
    "approval_id",
    "artifact_id",
    "artifact_ids",
    "artifactId",
    "caption",
    "capability_id",
    "code",
    "decision",
    "delivery_transport",
    "description",
    "detail",
    "event",
    "exit_code",
    "filename",
    "height",
    "id",
    "kind",
    "label",
    "message",
    "metadata",
    "mime_type",
    "mimeType",
    "name",
    "normalized_approval",
    "path",
    "query",
    "result_summary",
    "resolution",
    "runtime_access_mode",
    "runtime_session_id",
    "runtime_target",
    "specialist_name",
    "state",
    "status",
    "summary",
    "target_summary",
    "task_summary",
    "title",
    "tool_call_id",
    "tool_name",
    "url",
    "width",
}
_SAFE_TOP_LEVEL_KEYS = {
    "approval_id",
    "artifact_id",
    "data",
    "event_type",
    "item_id",
    "metadata",
    "seq",
    "tool_call_id",
    "ts",
}
_SAFE_METADATA_KEYS = {
    "action_id",
    "approval_id",
    "artifact_id",
    "code",
    "label",
    "runtime_access_mode",
    "runtime_session_id",
    "runtime_target",
    "status",
    "summary",
    "title",
}
_STEP_BLOCKED_KINDS = {"thinking", "reasoning", "assistant", "message", "final", "chunk"}
_STEP_SAFE_KINDS = {
    "approval",
    "artifact",
    "browser",
    "browser-observation",
    "computer",
    "connector",
    "delegation",
    "error",
    "file",
    "interrupted",
    "runtime",
    "screenshot",
    "search",
    "shell",
    "status",
    "tool",
}
_TRACE_EVENT_EXACT = {
    "artifact.created",
    "approval.requested",
    "approval.resolved",
    "browser.action",
    "browser.screenshot",
    "computer.browser.action",
    "delegation.finished",
    "delegation.started",
    "screenshot.captured",
    "search.query",
    "search.results",
    "tool.result",
    "tool.started",
    "trace.completed",
    "trace.failed",
}
_TRACE_EVENT_PREFIXES = (
    "approval.",
    "artifact.",
    "browser.",
    "computer.",
    "delegation.",
    "file.",
    "gateway.",
    "hardware.",
    "runtime.",
    "screenshot.",
    "search.",
    "shell.",
    "tool.",
)
_SENDISH_TRACE_TOKENS = ("telegram", "whatsapp", "gmail", "email", "mail", "message.sent", "message.queued")
_TRACE_EVENT_BLOCKED = {
    "assistant.message.delta",
    "final",
    "message.delta",
    "reasoning.summary.delta",
    "trace.started",
}
_TRACE_EVENT_BLOCKED_TOKENS = (
    ".delta",
    "audit",
    "debug",
    "internal",
    "model",
    "policy",
    "prompt",
    "provider",
    "raw",
    "reasoning",
)
_INTERNAL_TEXT_TOKENS = (
    "activity_event_id",
    "arguments",
    "chain of thought",
    "chain_of_thought",
    "deepseek",
    "model id",
    "model_id",
    "policy metadata",
    "policy_metadata",
    "provider id",
    "provider_id",
    "raw output",
    "raw_output",
    "request id",
    "request_id",
    "stacktrace",
    "system prompt",
    "system_prompt",
    "trace id",
    "trace_id",
)


def _read_object(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _read_string(value: Any) -> str:
    return str(value or "").strip()


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.replace("\x00", "").strip()
        return cleaned[:500]
    return None


def _looks_internal_text(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    if normalized.startswith("{") or normalized.startswith("["):
        return True
    lowered = normalized.lower()
    return any(token in lowered for token in _INTERNAL_TEXT_TOKENS)


def _sanitize_list(value: Any, *, safe_keys: set[str], depth: int) -> List[Any]:
    if not isinstance(value, list) or depth > 2:
        return []
    sanitized: List[Any] = []
    for item in value[:20]:
        next_value = _sanitize_value(item, key="", safe_keys=safe_keys, depth=depth + 1)
        if next_value is not None:
            sanitized.append(next_value)
    return sanitized


def _sanitize_dict(value: Any, *, safe_keys: set[str], depth: int) -> Dict[str, Any]:
    if not isinstance(value, dict) or depth > 2:
        return {}
    sanitized: Dict[str, Any] = {}
    for key, item in value.items():
        key_token = str(key or "").strip()
        if not key_token or key_token in _BLOCKED_KEYS:
            continue
        if safe_keys and key_token not in safe_keys:
            continue
        nested_safe_keys = _SAFE_METADATA_KEYS if key_token == "metadata" else safe_keys
        next_value = _sanitize_value(item, key=key_token, safe_keys=nested_safe_keys, depth=depth + 1)
        if next_value is None:
            continue
        if isinstance(next_value, (dict, list)) and not next_value:
            continue
        sanitized[key_token] = next_value
    return sanitized


def _sanitize_value(value: Any, *, key: str, safe_keys: set[str], depth: int) -> Any:
    if key in _BLOCKED_KEYS:
        return None
    if isinstance(value, str) and key in {"caption", "description", "detail", "label", "message", "summary", "title"}:
        cleaned = value.replace("\x00", "").strip()
        if _looks_internal_text(cleaned):
            return None
        return cleaned[:500]
    scalar = _safe_scalar(value)
    if scalar is not None or value is None:
        return scalar
    if isinstance(value, list):
        return _sanitize_list(value, safe_keys=safe_keys, depth=depth)
    if isinstance(value, dict):
        return _sanitize_dict(value, safe_keys=safe_keys, depth=depth)
    return None


def _sanitize_trace_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in payload.items():
        key_token = str(key or "").strip()
        if key_token not in _SAFE_TOP_LEVEL_KEYS:
            continue
        if key_token == "data":
            next_value = _sanitize_dict(value, safe_keys=_SAFE_DATA_KEYS, depth=0)
        elif key_token == "metadata":
            next_value = _sanitize_dict(value, safe_keys=_SAFE_METADATA_KEYS, depth=0)
        else:
            next_value = _sanitize_value(value, key=key_token, safe_keys=set(), depth=0)
        if next_value is None:
            continue
        if isinstance(next_value, (dict, list)) and not next_value:
            continue
        sanitized[key_token] = next_value
    return sanitized


def _sanitize_step_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    safe_step_keys = {
        "detail",
        "id",
        "kind",
        "label",
        "metadata",
        "status",
    }
    return {
        key: value
        for key, value in _sanitize_dict(payload, safe_keys=safe_step_keys, depth=0).items()
        if value is not None
    }


def _is_transcript_trace_event(payload: Dict[str, Any]) -> bool:
    event_type = _read_string(payload.get("event_type")).lower()
    if not event_type or event_type in _TRACE_EVENT_BLOCKED:
        return False
    if any(token in event_type for token in _TRACE_EVENT_BLOCKED_TOKENS):
        return False
    if event_type in _TRACE_EVENT_EXACT:
        return True
    if event_type.startswith(_TRACE_EVENT_PREFIXES):
        return True
    return any(token in event_type for token in _SENDISH_TRACE_TOKENS)


def _is_transcript_step_event(payload: Dict[str, Any]) -> bool:
    kind = _read_string(payload.get("kind")).lower()
    if kind in _STEP_BLOCKED_KINDS:
        return False
    if kind:
        return kind in _STEP_SAFE_KINDS
    return False


def build_transcript_event(event_name: Any, payload: Any) -> Optional[Dict[str, Any]]:
    event = _read_string(event_name).lower()
    source = _read_object(payload)
    if event not in _STREAM_EVENT_TYPES or not source:
        return None
    if event == "trace":
        if not _is_transcript_trace_event(source):
            return None
        sanitized_payload = _sanitize_trace_payload(source)
    else:
        if not _is_transcript_step_event(source):
            return None
        sanitized_payload = _sanitize_step_payload(source)
    if not sanitized_payload:
        return None
    return {
        "event": event,
        "schema_version": TRANSCRIPT_EVENT_SCHEMA_VERSION,
        "payload": sanitized_payload,
    }


def transcript_events_from_stream_records(records: Iterable[Any]) -> List[Dict[str, Any]]:
    transcript_events: List[Dict[str, Any]] = []
    for record in records:
        item = _read_object(record)
        event = build_transcript_event(item.get("event") or item.get("type"), item.get("payload"))
        if event is not None:
            transcript_events.append(event)
    return transcript_events[-TRANSCRIPT_EVENT_LIMIT:]


def transcript_events_from_stream_events(events: Iterable[Any]) -> List[Dict[str, Any]]:
    transcript_events: List[Dict[str, Any]] = []
    for item in events:
        record = _read_object(item)
        event = build_transcript_event(record.get("event") or record.get("type"), record.get("payload"))
        if event is not None:
            transcript_events.append(event)
    return transcript_events[-TRANSCRIPT_EVENT_LIMIT:]


def append_transcript_event(existing: Any, event: Dict[str, Any]) -> List[Dict[str, Any]]:
    current = [item for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    current.append(event)
    return current[-TRANSCRIPT_EVENT_LIMIT:]
