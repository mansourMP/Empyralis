from __future__ import annotations
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from server_modules import security_audit_service
from server_modules import deployed_agent_virtual_runtime_service
from server_modules import skills_service
from server_modules import secret_redaction_service
from server_modules import agent_action_metering_service, tool_broker_guard_service
from server_modules import rust_runtime_kernel_client
from server_modules import hardware_runtime_target_resolver

_BROWSER_CAPTURE_ACTIONS = {"screenshot", "pdf"}
_BROWSER_MUTATION_ACTIONS = {"click", "fill", "execute_js", "download_file"}
_BROWSER_NAVIGATION_ACTIONS = {"navigate", "new_tab", "switch_tab", "start_intercept", "stop_intercept"}
_BROWSER_READ_ACTIONS = {"observe", "extract_text", "get_page_state"}
_FILE_DELETE_ACTIONS = {"delete", "remove", "unlink", "trash"}
_FILE_WRITE_ACTIONS = {"write", "append", "rename", "move", "copy", "mkdir", "touch"}
_CHANNEL_CONNECTORS = {"discord", "email", "gmail", "imsg", "mail", "signal", "slack", "telegram", "whatsapp"}
_HTTP_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_CLOUD_COMPUTER_RUNTIME_CONNECTORS = {"browser", "computer", "shell"}


def _execution_environment_for_direct_tool(
    connector_id: str,
    action_id: str,
    arguments: Dict[str, Any],
    result_payload: Optional[Dict[str, Any]] = None,
) -> str:
    payload = arguments if isinstance(arguments, dict) else {}
    result = result_payload if isinstance(result_payload, dict) else {}
    runtime_session = result.get("runtime_session") if isinstance(result.get("runtime_session"), dict) else {}
    runtime_target = (
        str(result.get("runtime_target") or "").strip()
        or str(runtime_session.get("canonical_runtime_target") or "").strip()
        or str(runtime_session.get("runtime_target") or "").strip()
        or str(payload.get("runtime_target") or "").strip()
    )
    if runtime_target:
        return hardware_runtime_target_resolver.execution_environment_for_runtime_target(
            hardware_runtime_target_resolver.resolve_runtime_target(runtime_target).canonical_runtime_target
        )
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    if normalized_connector in {"file", "shell", "screenshot", "computer"}:
        return "local_gateway"
    if normalized_connector == "hardware" and hardware_runtime_target_resolver.action_requires_local_hardware(
        payload.get("capability_id") or payload.get("action") or normalized_action
    ):
        return "local_gateway"
    if normalized_connector == "browser":
        return "cloud_browser"
    return "cloud_provider"


class DirectToolExecutionRustGateError(RuntimeError):
    pass


def _payload_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        return len(str(value or "").encode("utf-8"))


def _enforce_direct_tool_execution_transition(
    *,
    operation: str,
    tenant_id: Optional[str],
    workspace_id: str,
    run_id: Optional[str],
    thread_id: str,
    tool_call_id: str,
    tool_name: str,
    connector_id: str,
    action_id: str,
    status: str,
    governance_metadata: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "tenant_id": str(tenant_id or "default").strip() or "default",
        "workspace_id": str(workspace_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "thread_id": str(thread_id or "").strip(),
        "tool_call_id": str(tool_call_id or "").strip(),
        "tool_name": str(tool_name or "").strip(),
        "connector_id": str(connector_id or "").strip().lower(),
        "action_id": str(action_id or "").strip().lower(),
        "status": str(status or "").strip(),
        "governance": dict(governance_metadata or {}),
        "metadata": dict(metadata or {}),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation=operation,
            state_class="direct_tool_execution",
            tenant_id=str(payload["tenant_id"]),
            workspace_id=str(payload["workspace_id"]),
            run_id=str(payload["run_id"]),
            actor_id="system",
            status=str(status or "active").strip() or "active",
            payload=payload,
            payload_bytes=_payload_size_bytes(payload),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != str(operation or "").strip():
            raise DirectToolExecutionRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise DirectToolExecutionRustGateError(exc.reason) from exc


def _default_update_memory_context_file(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import update_memory_context_file

    return update_memory_context_file(*args, **kwargs)


def _default_memory_append_daily_note(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import memory_append_daily_note

    return memory_append_daily_note(*args, **kwargs)


def _default_create_memory_consolidation_staging_file(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import create_memory_consolidation_staging_file

    return create_memory_consolidation_staging_file(*args, **kwargs)


def _default_consolidate_daily_memory_notes(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import consolidate_daily_memory_notes

    return consolidate_daily_memory_notes(*args, **kwargs)


def _default_apply_memory_consolidation_staging(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import apply_memory_consolidation_staging

    return apply_memory_consolidation_staging(*args, **kwargs)


def _default_list_memory_file_versions(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import list_memory_file_versions

    return list_memory_file_versions(*args, **kwargs)


def _default_rollback_memory_file_version(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import rollback_memory_file_version

    return rollback_memory_file_version(*args, **kwargs)


def _default_memory_read_file(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import memory_read_file

    return memory_read_file(*args, **kwargs)


def _default_memory_write_file(*args: Any, **kwargs: Any) -> Any:
    from server_modules.memory_service import memory_write_file

    return memory_write_file(*args, **kwargs)


@dataclass(frozen=True)
class DirectToolExecutionCallbacks:
    compact_step_detail: Callable[[Any], Optional[str]]
    titleize_direct_step_token: Callable[[str], str]
    run_async_tool_call: Callable[[Any], Any]
    parse_tool_name: Callable[[str], tuple[str, str]]
    tool_arguments_payload: Callable[[Any], Dict[str, Any]]
    parse_json_object_loose: Callable[[str], Any]
    safe_positive_int: Callable[[Any, int], int]
    normalize_reasoning_effort: Callable[[str], Optional[str]]
    build_direct_local_tool_config: Callable[[str, str, Dict[str, Any]], tuple[str, Dict[str, Any]]]
    format_direct_local_tool_result: Callable[[Any], str]
    build_direct_tool_config: Callable[[str, str, str], Dict[str, Any]]
    format_direct_tool_result: Callable[[Any], str]
    llm_task: Callable[..., Any]
    web_search: Callable[[str], List[Dict[str, Any]]]
    web_fetch: Callable[[str], str]
    search_memory_notebook: Callable[..., Any]
    get_memory_notebook_excerpt: Callable[..., Any]
    update_memory_context_file: Callable[..., Any] = _default_update_memory_context_file
    memory_append_daily_note: Callable[..., Any] = _default_memory_append_daily_note
    create_memory_consolidation_staging_file: Callable[..., Any] = _default_create_memory_consolidation_staging_file
    consolidate_daily_memory_notes: Callable[..., Any] = _default_consolidate_daily_memory_notes
    apply_memory_consolidation_staging: Callable[..., Any] = _default_apply_memory_consolidation_staging
    list_memory_file_versions: Callable[..., Any] = _default_list_memory_file_versions
    rollback_memory_file_version: Callable[..., Any] = _default_rollback_memory_file_version
    memory_read_file: Callable[..., Any] = _default_memory_read_file
    memory_write_file: Callable[..., Any] = _default_memory_write_file


def direct_tool_step_payload(
    connector_id: str,
    action_id: str,
    arguments: Dict[str, Any],
    *,
    step_id: str,
    status: str,
    detail_override: Optional[str] = None,
    callbacks: DirectToolExecutionCallbacks,
) -> Dict[str, Any]:
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    label = "Running tool"
    kind = "connector"
    detail = callbacks.compact_step_detail(detail_override)

    if normalized_connector == "file" and normalized_action == "read":
        label = "Reading file"
        kind = "file"
        detail = detail or callbacks.compact_step_detail(arguments.get("path") or arguments.get("file_path"))
    elif normalized_connector == "file" and normalized_action == "write":
        label = "Writing file"
        kind = "file"
        detail = detail or callbacks.compact_step_detail(arguments.get("path") or arguments.get("file_path"))
    elif normalized_connector == "shell" and normalized_action == "exec":
        label = "Running command"
        kind = "shell"
        detail = detail or callbacks.compact_step_detail(arguments.get("command"))
    elif normalized_connector == "browser":
        kind = "browser"
        selector = (
            arguments.get("selector")
            or arguments.get("url")
            or arguments.get("url_pattern")
            or arguments.get("tab_id")
            or arguments.get("save_path")
            or arguments.get("output_path")
        )
        if normalized_action in _BROWSER_READ_ACTIONS:
            label = "Reading browser page"
        elif normalized_action in _BROWSER_CAPTURE_ACTIONS:
            label = "Capturing browser artifact"
        elif normalized_action in _BROWSER_NAVIGATION_ACTIONS:
            label = "Navigating browser"
        elif normalized_action == "click":
            label = "Clicking browser"
        elif normalized_action == "fill":
            label = "Typing in browser"
            selector = selector or arguments.get("value")
        elif normalized_action == "download_file":
            label = "Downloading from browser"
        elif normalized_action == "execute_js":
            label = "Running browser script"
        else:
            label = "Using browser"
        detail = detail or callbacks.compact_step_detail(selector)
    elif normalized_connector == "screenshot" and normalized_action == "capture":
        label = "Capturing screenshot"
        kind = "screenshot"
        detail = detail or callbacks.compact_step_detail(arguments.get("path") or arguments.get("file_path") or "Current screen")
    elif normalized_connector == "hardware":
        hardware_action = str(arguments.get("action") or arguments.get("capability_id") or "").strip()
        if hardware_action == "screenshot.capture":
            label = "Capturing screen"
            kind = "screenshot"
        else:
            label = "Using hardware runtime"
            kind = "computer"
        detail = detail or callbacks.compact_step_detail(
            arguments.get("action")
            or arguments.get("capability_id")
            or arguments.get("runtime_target")
            or "Hardware action"
        )
    elif normalized_connector == "computer":
        kind = "computer"
        if normalized_action == "ocr":
            label = "Reading screen"
        elif normalized_action == "click":
            label = "Clicking screen"
            detail = detail or callbacks.compact_step_detail(arguments.get("text") or f"{arguments.get('x')}, {arguments.get('y')}")
        elif normalized_action == "type":
            label = "Typing text"
            detail = detail or callbacks.compact_step_detail(arguments.get("text"))
        elif normalized_action == "applescript":
            label = "Running AppleScript"
        elif normalized_action == "clipboard_read":
            label = "Reading clipboard"
        elif normalized_action == "clipboard_write":
            label = "Writing clipboard"
            detail = detail or callbacks.compact_step_detail(arguments.get("text"))
        elif normalized_action == "notify":
            label = "Sending notification"
            detail = detail or callbacks.compact_step_detail(arguments.get("title"))
        elif normalized_action == "list_apps":
            label = "Listing apps"
        elif normalized_action == "launch_app":
            label = "Launching app"
            detail = detail or callbacks.compact_step_detail(arguments.get("name_or_path"))
        elif normalized_action == "speak":
            label = "Speaking text"
            detail = detail or callbacks.compact_step_detail(arguments.get("text"))
        else:
            label = "Computer control"
    elif normalized_connector == "sage_service":
        kind = "service"
        service_label = callbacks.compact_step_detail(
            arguments.get("service_id")
            or arguments.get("service")
            or arguments.get("name")
        )
        if normalized_action == "list_state":
            label = "Reading service state"
        elif normalized_action == "update_profile":
            label = "Updating service profile"
        elif normalized_action == "create_entry":
            label = "Saving service entry"
        else:
            label = "Updating Sage service"
        detail = detail or service_label
    else:
        action_label = callbacks.titleize_direct_step_token(normalized_action) or "Connector action"
        connector_label = callbacks.titleize_direct_step_token(normalized_connector) or normalized_connector
        label = action_label
        kind = "connector"
        detail = detail or connector_label

    return {
        "type": "step",
        "id": step_id,
        "kind": kind,
        "label": label,
        "detail": detail,
        "status": status,
    }


def thinking_step_payload(iteration: int, status: str, detail: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": "step",
        "id": f"thinking:{iteration}",
        "kind": "thinking",
        "label": "Thinking",
        "detail": detail or ("Planning the response" if iteration <= 1 else "Planning the next step"),
        "status": status,
    }


def extract_first_url(value: str) -> str:
    match = re.search(r"https?://[^\s)>\]}]+", str(value or ""), flags=re.IGNORECASE)
    if match:
        return str(match.group(0) or "").rstrip(".,!?;:")
    bare_match = re.search(
        r"\b((?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s)>\]}]+)?)",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not bare_match:
        return ""
    token = str(bare_match.group(1) or "").rstrip(".,!?;:")
    if "." not in token:
        return ""
    return f"https://{token}"


def extract_first_path_reference(value: str) -> str:
    matches = re.findall(
        r"(^|\s)(/|~/|\./|\.\./|[a-z]:[/\\])([^\s,;:]+)",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not matches:
        bare_match = re.search(
            r"\b((?:[a-z0-9_.-]+/)+[a-z0-9_.-]+(?:\.[a-z0-9_.-]+)?)\b",
            str(value or ""),
            flags=re.IGNORECASE,
        )
        if not bare_match:
            return ""
        candidate = str(bare_match.group(1) or "").rstrip(".,!?;:")
        if "://" in candidate:
            return ""
        return candidate
    _prefix, root, remainder = matches[0]
    return f"{root}{remainder}".strip()


def resolve_chat_local_path(raw_path: str) -> Path:
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _compact_trace_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def _redact_audit_summary(value: Any) -> str:
    text = _compact_trace_text(secret_redaction_service.redact_text(value))
    if not text:
        return ""
    return text.replace("[redacted]", "[redacted-secret]")


def _infer_trace_capability_id(connector_id: str, action_id: str) -> Optional[str]:
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    if not normalized_connector:
        return None
    if normalized_connector == "web" and normalized_action == "search":
        return "web_search"
    if normalized_connector == "shell" and normalized_action == "exec":
        return "shell.execute"
    if normalized_connector == "screenshot" and normalized_action == "capture":
        return "screenshot.capture"
    if normalized_connector == "computer" and normalized_action:
        return f"computer_control.{normalized_action}"
    if normalized_connector == "hardware":
        return str(
            action_id
            if action_id and action_id != "action"
            else ""
        ).strip() or None
    if normalized_action:
        return f"{normalized_connector}.{normalized_action}"
    return normalized_connector


def _argument_target_summary(arguments: Dict[str, Any]) -> str:
    payload = arguments if isinstance(arguments, dict) else {}
    for key in (
        "command",
        "path",
        "file_path",
        "url",
        "query",
        "selector",
        "prompt",
        "service_id",
        "remote_jid",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return _redact_audit_summary(value)
    return ""


def _looks_like_credential_change(connector_id: str, action_id: str, arguments: Dict[str, Any]) -> bool:
    combined = " ".join(
        [
            str(connector_id or "").strip().lower(),
            str(action_id or "").strip().lower(),
            " ".join(str(key or "").strip().lower() for key in arguments.keys()),
        ]
    )
    return any(token in combined for token in ("credential", "credentials", "password", "secret", "token", "api_key", "apikey", "auth"))


def _direct_tool_governance_metadata(
    connector_id: str,
    action_id: str,
    arguments: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    payload = arguments if isinstance(arguments, dict) else {}
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()

    action_class = "tool_action"
    risk_level = "low"
    governance_boundary = "cloud"
    requires_approval = False
    destructive = False
    external_side_effect = False
    approval_reason: Optional[str] = None

    if normalized_connector == "web" and normalized_action == "search":
        action_class = "search"
        governance_boundary = "web_search"
    elif normalized_connector == "http" and normalized_action == "request":
        method = str(payload.get("method") or "GET").strip().upper() or "GET"
        action_class = "external_request"
        governance_boundary = "outbound_network"
        external_side_effect = method not in _HTTP_READ_METHODS
        requires_approval = external_side_effect
        risk_level = "high" if external_side_effect else "moderate"
        approval_reason = "External network write" if external_side_effect else None
    elif normalized_connector == "browser":
        governance_boundary = "browser_session"
        if normalized_action in _BROWSER_CAPTURE_ACTIONS:
            action_class = "browser_capture"
            risk_level = "moderate"
        elif normalized_action in _BROWSER_MUTATION_ACTIONS:
            action_class = "browser_mutation"
            risk_level = "high"
            requires_approval = True
            approval_reason = "Browser mutation"
        elif normalized_action in _BROWSER_NAVIGATION_ACTIONS:
            action_class = "browser_navigation"
            risk_level = "moderate"
        else:
            action_class = "browser_read"
    elif normalized_connector == "file":
        governance_boundary = "local_filesystem"
        if normalized_action in _FILE_DELETE_ACTIONS:
            action_class = "local_delete"
            risk_level = "critical"
            requires_approval = True
            destructive = True
            approval_reason = "Destructive file change"
        elif normalized_action in _FILE_WRITE_ACTIONS:
            action_class = "local_write"
            risk_level = "high"
            requires_approval = True
            approval_reason = "Local file write"
        else:
            action_class = "file_read"
    elif normalized_connector == "shell":
        action_class = "shell_execute"
        governance_boundary = "local_shell"
        risk_level = "high"
        requires_approval = True
        approval_reason = "Shell execution"
    elif normalized_connector == "computer":
        action_class = "device_control"
        governance_boundary = "paired_device"
        risk_level = "high"
        requires_approval = True
        approval_reason = "Device control"
    elif normalized_connector == "sage_service":
        action_class = "service_state_update" if normalized_action in {"create_entry", "update_profile"} else "service_state_read"
        governance_boundary = "sage_profile"
        risk_level = "moderate" if action_class.endswith("update") else "low"
    elif normalized_connector == "memory":
        action_class = "workspace_memory_update" if normalized_action in {"update", "append_daily_note", "stage_edit", "apply_edit", "stage_consolidation", "consolidate_daily_notes", "rollback_version"} else "workspace_memory_read"
        governance_boundary = "workspace_memory"
        risk_level = "moderate" if normalized_action in {"update", "append_daily_note", "stage_edit", "apply_edit", "stage_consolidation", "consolidate_daily_notes", "rollback_version"} else "low"
    elif normalized_connector in _CHANNEL_CONNECTORS or normalized_action in {"send", "reply", "post", "dispatch"}:
        action_class = "channel_send"
        governance_boundary = "external_channel"
        risk_level = "critical"
        requires_approval = True
        external_side_effect = True
        approval_reason = "External send"

    if _looks_like_credential_change(normalized_connector, normalized_action, payload):
        action_class = "credential_change"
        governance_boundary = governance_boundary if governance_boundary != "cloud" else "credential_store"
        risk_level = "critical"
        requires_approval = True
        approval_reason = "Credential change"

    return {
        "action_class": action_class,
        "risk_level": risk_level,
        "governance_boundary": governance_boundary,
        "requires_approval": requires_approval,
        "approval_reason": approval_reason,
        "external_side_effect": external_side_effect,
        "destructive": destructive,
        "argument_target": _argument_target_summary(payload) or None,
    }


def _broker_action_class_for_direct_tool(governance: Dict[str, Any]) -> str:
    action_class = str(governance.get("action_class") or "").strip().lower()
    risk_level = str(governance.get("risk_level") or "").strip().lower()
    if action_class in {"shell_execute", "device_control", "credential_change", "local_delete"}:
        return "execute"
    if risk_level in {"critical", "high"}:
        return "execute"
    if bool(governance.get("external_side_effect")) or action_class in {"local_write", "browser_mutation", "channel_send", "workspace_memory_update"}:
        return "write"
    return "read"


def _parse_web_search_results(result_text: str) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    for block in re.split(r"\n\s*\n", str(result_text or "").strip()):
        lines = [line.strip() for line in block.splitlines() if str(line or "").strip()]
        if not lines:
            continue
        title = re.sub(r"^\d+\.\s*", "", lines[0]).strip()
        url = ""
        snippet_parts: List[str] = []
        for line in lines[1:]:
            lower = line.lower()
            if lower.startswith("url:"):
                url = line.split(":", 1)[1].strip()
                continue
            if lower.startswith("snippet:"):
                snippet_parts.append(line.split(":", 1)[1].strip())
                continue
            snippet_parts.append(line)
        if title or url or snippet_parts:
            results.append(
                {
                    "title": title or url or "Result",
                    "url": url,
                    "snippet": " ".join(part for part in snippet_parts if part).strip(),
                }
            )
    return results[:5]


def _parse_result_json_object(result_text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(str(result_text or "").strip())
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _first_artifact_id_from_result_payload(payload: Dict[str, Any]) -> str:
    containers: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        containers.append(payload)
        execution = payload.get("execution")
        if isinstance(execution, dict):
            containers.append(execution)
            execution_result = execution.get("result")
            if isinstance(execution_result, dict):
                containers.append(execution_result)
        result = payload.get("result")
        if isinstance(result, dict):
            containers.append(result)

    for container in containers:
        for key in ("artifact_id", "screenshot_artifact_id", "id"):
            candidate = str(container.get(key) or "").strip()
            if candidate:
                return candidate
        artifacts = container.get("artifacts")
        if isinstance(artifacts, list):
            for item in artifacts:
                if isinstance(item, dict):
                    candidate = str(item.get("artifact_id") or item.get("id") or "").strip()
                else:
                    candidate = str(item or "").strip()
                if candidate:
                    return candidate
    return ""


def _hardware_result_summary(
    *,
    capability_id: str,
    result_payload: Dict[str, Any],
    fallback: str,
) -> str:
    result = result_payload.get("result")
    if isinstance(result, dict):
        summary = str(result.get("summary") or "").strip()
        if summary:
            return summary

    status = str(result_payload.get("status") or "").strip().lower()
    reason = str(result_payload.get("reason") or "").strip()
    if status == "completed":
        if capability_id == "screenshot.capture":
            return "Captured screenshot from Agent Computer."
        return f"{capability_id or 'Hardware action'} completed."
    if status == "waiting_approval":
        return f"Waiting for approval to run {capability_id or 'hardware action'}."
    if status in {"offline", "degraded"}:
        return "Agent Computer is not connected. Open Hardware to connect this Mac."
    if status == "failed":
        return reason or "Hardware action failed."
    return fallback


def build_direct_tool_trace_metadata(
    connector_id: str,
    action_id: str,
    arguments: Optional[Dict[str, Any]],
    *,
    result_text: str = "",
) -> Dict[str, Any]:
    payload = arguments if isinstance(arguments, dict) else {}
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    search_query = ""
    search_results: List[Dict[str, str]] = []
    browser_action: Optional[Dict[str, Any]] = None
    browser_screenshot: Optional[Dict[str, Any]] = None
    result_summary = _compact_trace_text(result_text or payload.get("query") or payload.get("url"))
    result_payload: Dict[str, Any] = {}

    if normalized_connector == "web" and normalized_action == "search":
        search_query = str(payload.get("query") or payload.get("input") or "").strip()
        search_results = _parse_web_search_results(result_text)

    if normalized_connector == "browser":
        target_summary = (
            str(payload.get("selector") or "").strip()
            or str(payload.get("url") or "").strip()
            or str(payload.get("value") or "").strip()
            or str(payload.get("text") or "").strip()
            or str(payload.get("output_path") or "").strip()
            or str(payload.get("save_path") or "").strip()
            or str(payload.get("url_pattern") or "").strip()
            or str(payload.get("tab_id") or "").strip()
        )
        browser_action = {
            "action": normalized_action,
            "target_summary": _compact_trace_text(target_summary or normalized_action.replace("_", " ")),
            "url": str(payload.get("url") or "").strip() or extract_first_url(result_text) or None,
        }
        if normalized_action == "screenshot":
            artifact_ref = (
                str(payload.get("output_path") or "").strip()
                or str(payload.get("save_path") or "").strip()
                or extract_first_path_reference(result_text)
            )
            browser_screenshot = {
                "artifact_id": artifact_ref or f"browser_screenshot:{normalized_action}",
                "caption": _compact_trace_text(artifact_ref or "Browser screenshot"),
                "width": 0,
                "height": 0,
            }

    if normalized_connector == "hardware":
        result_payload = _parse_result_json_object(result_text)
        capability_id = str(payload.get("action") or payload.get("capability_id") or normalized_action or "action").strip()
        target_summary = (
            capability_id
            or str(payload.get("runtime_target") or "").strip()
        )
        browser_action = {
            "action": capability_id or "action",
            "target_summary": _compact_trace_text(target_summary or "hardware action"),
            "url": None,
        }
        result_summary = _compact_trace_text(
            _hardware_result_summary(
                capability_id=capability_id,
                result_payload=result_payload,
                fallback=result_summary,
            )
        )
        if capability_id == "screenshot.capture":
            artifact_ref = _first_artifact_id_from_result_payload(result_payload)
            if artifact_ref:
                browser_screenshot = {
                    "artifact_id": artifact_ref,
                    "caption": "Agent Computer screenshot",
                    "width": 0,
                    "height": 0,
                }

    return {
        "capability_id": (
            str(payload.get("capability_id") or payload.get("action") or "").strip()
            if normalized_connector == "hardware"
            else _infer_trace_capability_id(normalized_connector, normalized_action)
        ),
        "search_query": search_query,
        "search_results": search_results,
        "browser_action": browser_action,
        "browser_screenshot": browser_screenshot,
        "result_summary": result_summary,
        "execution_environment": _execution_environment_for_direct_tool(
            normalized_connector,
            normalized_action,
            payload,
            result_payload,
        ),
    }


def direct_tool_followup_message(tool_name: str, result_text: str) -> str:
    cleaned_result = str(result_text or "").strip() or "No result."
    return (
        f"Tool result for {tool_name}:\n{cleaned_result}\n\n"
        "Continue until the task is complete. If another tool is needed, call it now. "
        "Otherwise provide the final answer to the user."
    )


def execute_single_direct_tool_call(
    *,
    tool_call: Dict[str, Any],
    workspace_id: str,
    thread_id: str,
    index: int = 1,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    credentials: Optional[Dict[str, Any]] = None,
    reasoning_effort: str = "",
    session_ctx: Optional[Dict[str, Any]] = None,
    callbacks: DirectToolExecutionCallbacks,
) -> str:
    session_metadata = session_ctx if isinstance(session_ctx, dict) else {}
    turn_request_payload = (
        session_metadata.get("agent_turn_request")
        if isinstance(session_metadata.get("agent_turn_request"), dict)
        else {}
    )
    context_hints_payload = (
        turn_request_payload.get("context_hints")
        if isinstance(turn_request_payload.get("context_hints"), dict)
        else {}
    )
    context_metadata_payload = (
        context_hints_payload.get("metadata")
        if isinstance(context_hints_payload.get("metadata"), dict)
        else {}
    )
    tool_name = str(tool_call.get("name") or "").strip()
    tool_call_id = str(
        tool_call.get("id")
        or tool_call.get("tool_call_id")
        or tool_call.get("call_id")
        or ""
    ).strip() or f"direct_tool_{uuid.uuid4().hex}"
    try:
        connector_id, action_id = callbacks.parse_tool_name(tool_name)
    except Exception:
        connector_id, action_id = "", ""
    try:
        arguments = callbacks.tool_arguments_payload(tool_call.get("arguments"))
    except Exception:
        arguments = {}
    argument_payload = arguments if isinstance(arguments, dict) else {}
    tenant_id = str(
        session_metadata.get("tenant_id")
        or (
            session_metadata.get("agent_turn_request", {}).get("tenant_id")
            if isinstance(session_metadata.get("agent_turn_request"), dict)
            else ""
        )
        or ""
    ).strip() or None
    run_id = str(
        session_metadata.get("request_id")
        or session_metadata.get("client_request_id")
        or session_metadata.get("run_id")
        or ""
    ).strip() or None
    surface = str(session_metadata.get("surface") or session_metadata.get("product_surface") or "sage").strip().lower() or "sage"
    if surface not in {"sage", "studio", "mini_app"}:
        surface = "sage"
    source_surface = str(session_metadata.get("source_surface") or "sage_direct_tool").strip().lower() or "sage_direct_tool"
    user_id = str(session_metadata.get("user_id") or "").strip() or None
    agent_id = str(
        context_metadata_payload.get("deployed_agent_id")
        or session_metadata.get("deployed_agent_id")
        or session_metadata.get("agent_id")
        or session_metadata.get("agent_install_id")
        or session_metadata.get("active_agent_install_id")
        or ""
    ).strip() or None
    app_id = str(session_metadata.get("app_id") or "").strip() or None
    trace_id = str(session_metadata.get("trace_id") or "").strip() or None
    governance_metadata = _direct_tool_governance_metadata(connector_id, action_id, argument_payload)
    audit_metadata = {
        "tool_name": tool_name or None,
        "connector_id": str(connector_id or "").strip() or None,
        "action_id": str(action_id or "").strip() or None,
        "thread_id": str(thread_id or "").strip() or None,
        "provider": str(provider or "").strip() or None,
        "model": str(model or "").strip() or None,
        "argument_keys": sorted(str(key) for key in argument_payload.keys()),
        "argument_summary": _redact_audit_summary(
            argument_payload.get("command")
            or argument_payload.get("path")
            or argument_payload.get("file_path")
            or argument_payload.get("url")
            or argument_payload.get("query")
            or argument_payload.get("prompt")
            or ""
        )
        or None,
        **governance_metadata,
    }
    broker_decision = tool_broker_guard_service.evaluate_tool_call(
        claims={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "manifest_id": session_metadata.get("manifest_id") or session_metadata.get("agent_manifest_id") or "direct_chat",
            "agent_install_id": session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or "sage",
            "grant_id": session_metadata.get("grant_id") or run_id or thread_id,
        },
        tool_key=tool_name or _infer_trace_capability_id(connector_id, action_id),
        action_class=_broker_action_class_for_direct_tool(governance_metadata),
        connector_scope=str(connector_id or "").strip().lower(),
        surface="connector" if str(connector_id or "").strip().lower() in _CHANNEL_CONNECTORS else "direct_tool",
    )
    if not broker_decision.allowed:
        _enforce_direct_tool_execution_transition(
            operation="block_direct_tool_execution",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            connector_id=connector_id,
            action_id=action_id,
            status="blocked",
            governance_metadata=governance_metadata,
            metadata={"broker_code": broker_decision.code},
        )
        agent_action_metering_service.record_blocked_sync(
            tenant_id=tenant_id or "default",
            workspace_id=workspace_id,
            surface=surface,
            source_surface=source_surface,
            action_domain="tool",
            action_type=_broker_action_class_for_direct_tool(governance_metadata),
            action_name=tool_name or _infer_trace_capability_id(connector_id, action_id),
            tool_kind="direct_tool",
            tool_id=tool_name or None,
            connector_id=str(connector_id or "").strip() or None,
            capability_id=_infer_trace_capability_id(connector_id, action_id),
            risk_level=str(governance_metadata.get("risk_level") or "").strip() or None,
            policy_decision="blocked",
            error_code=broker_decision.code,
            payer=str(session_metadata.get("payer") or "platform_credits").strip() or "platform_credits",
            billing_mode="none",
            user_id=user_id,
            thread_id=str(thread_id or "").strip() or None,
            run_id=run_id,
            agent_id=agent_id,
            app_id=app_id,
            trace_id=trace_id,
            input_summary=audit_metadata.get("argument_summary") or tool_name,
            metadata={"broker_guard": broker_decision.snapshot, "broker_code": broker_decision.code},
            source_table="direct_tool_calls",
            source_event_id=agent_action_metering_service.build_source_event_id(
                source_surface=source_surface,
                run_id=run_id,
                thread_id=thread_id,
                tool_call_id=tool_call_id or index,
                action_name=tool_name,
            ),
        )
        security_audit_service.emit_security_audit_event(
            action="direct_tool.blocked",
            status="blocked",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            detail=broker_decision.detail,
            metadata={**audit_metadata, "broker_guard": broker_decision.snapshot, "broker_code": broker_decision.code},
            idempotency_key=f"direct_tool.blocked:{workspace_id}:{run_id or thread_id}:{index}:{tool_name}:{broker_decision.code}",
        )
        raise RuntimeError(broker_decision.detail or "Direct tool call was blocked by broker guard.")
    _enforce_direct_tool_execution_transition(
        operation="start_direct_tool_execution",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        connector_id=connector_id,
        action_id=action_id,
        status="started",
        governance_metadata=governance_metadata,
        metadata={"source_surface": source_surface},
    )
    security_audit_service.emit_security_audit_event(
        action="direct_tool.started",
        status="started",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        detail=tool_name or None,
        metadata=audit_metadata,
        idempotency_key=f"direct_tool.started:{workspace_id}:{run_id or thread_id}:{index}:{tool_name}",
    )
    source_event_id = agent_action_metering_service.build_source_event_id(
        source_surface=source_surface,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=tool_call_id or index,
        action_name=tool_name,
    )
    common_action_metering = {
        "tenant_id": tenant_id or "default",
        "workspace_id": workspace_id,
        "surface": surface,
        "source_surface": source_surface,
        "action_domain": "tool",
        "action_type": _broker_action_class_for_direct_tool(governance_metadata),
        "action_name": tool_name or _infer_trace_capability_id(connector_id, action_id),
        "tool_kind": "direct_tool",
        "tool_id": tool_name or None,
        "connector_id": str(connector_id or "").strip() or None,
        "capability_id": _infer_trace_capability_id(connector_id, action_id),
        "risk_level": str(governance_metadata.get("risk_level") or "").strip() or None,
        "policy_decision": "allowed",
        "payer": str(session_metadata.get("payer") or "platform_credits").strip() or "platform_credits",
        "billing_mode": "transparency",
        "user_id": user_id,
        "thread_id": str(thread_id or "").strip() or None,
        "run_id": run_id,
        "agent_id": agent_id,
        "app_id": app_id,
        "trace_id": trace_id,
        "input_summary": audit_metadata.get("argument_summary") or tool_name,
        "source_table": "direct_tool_calls",
        "source_event_id": source_event_id,
        "metadata": {
            "argument_keys": audit_metadata.get("argument_keys") or [],
            "broker_guard": broker_decision.snapshot,
        },
    }
    agent_action_metering_service.record_started_sync(**common_action_metering)
    try:
        result: Optional[str] = None
        if connector_id in _CLOUD_COMPUTER_RUNTIME_CONNECTORS:
            has_cloud_binding = deployed_agent_virtual_runtime_service.has_cloud_runtime_session_binding(session_ctx)
            has_self_hosted_binding = deployed_agent_virtual_runtime_service.has_self_hosted_runtime_session_binding(session_ctx)
            has_deployed_agent_context = bool(
                str(
                    context_metadata_payload.get("deployed_agent_id")
                    or session_metadata.get("deployed_agent_id")
                    or ""
                )
            )
            should_check_self_hosted = has_self_hosted_binding or (has_deployed_agent_context and not has_cloud_binding)
            if should_check_self_hosted:
                self_hosted_result = callbacks.run_async_tool_call(
                    deployed_agent_virtual_runtime_service.execute_bound_self_hosted_runtime_tool_call(
                        connector_id=connector_id,
                        action_id=action_id,
                        argument_payload=argument_payload,
                        workspace_id=workspace_id,
                        thread_id=thread_id,
                        session_ctx=session_ctx,
                    )
                )
                if self_hosted_result is not None:
                    result = self_hosted_result
                elif has_self_hosted_binding:
                    raise RuntimeError(
                        f"self_hosted_agent runtime action '{connector_id}__{action_id}' must execute through bound node runtime."
                    )
        if result is None and connector_id in _CLOUD_COMPUTER_RUNTIME_CONNECTORS:
            if deployed_agent_virtual_runtime_service.has_cloud_runtime_session_binding(session_ctx):
                cloud_result = callbacks.run_async_tool_call(
                    deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call(
                        connector_id=connector_id,
                        action_id=action_id,
                        argument_payload=argument_payload,
                        workspace_id=workspace_id,
                        thread_id=thread_id,
                        session_ctx=session_ctx,
                    )
                )
                if cloud_result is not None:
                    result = cloud_result
        if result is None:
            result = skills_service.execute_single_direct_tool_call(
                tool_call=tool_call,
                workspace_id=workspace_id,
                thread_id=thread_id,
                index=index,
                provider=provider,
                model=model,
                credentials=credentials,
                reasoning_effort=reasoning_effort,
                session_ctx=session_ctx,
                callbacks=callbacks,
            )
    except Exception as exc:
        _enforce_direct_tool_execution_transition(
            operation="fail_direct_tool_execution",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            connector_id=connector_id,
            action_id=action_id,
            status="failed",
            governance_metadata=governance_metadata,
            metadata={"error_type": type(exc).__name__},
        )
        agent_action_metering_service.record_failed_sync(
            **common_action_metering,
            error_code=type(exc).__name__,
            output_summary=str(exc),
        )
        security_audit_service.emit_security_audit_event(
            action="direct_tool.failed",
            status="failed",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            detail=type(exc).__name__,
            metadata={**audit_metadata, "error_type": type(exc).__name__},
            idempotency_key=f"direct_tool.failed:{workspace_id}:{run_id or thread_id}:{index}:{tool_name}",
        )
        raise
    _enforce_direct_tool_execution_transition(
        operation="complete_direct_tool_execution",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        connector_id=connector_id,
        action_id=action_id,
        status="completed",
        governance_metadata=governance_metadata,
        metadata={"result_summary": _redact_audit_summary(result)},
    )
    security_audit_service.emit_security_audit_event(
        action="direct_tool.completed",
        status="success",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        detail=tool_name or None,
        metadata={**audit_metadata, "result_summary": _redact_audit_summary(result)},
        idempotency_key=f"direct_tool.completed:{workspace_id}:{run_id or thread_id}:{index}:{tool_name}",
    )
    agent_action_metering_service.record_completed_sync(
        **common_action_metering,
        output_summary=_redact_audit_summary(result),
    )
    return result


def execute_direct_tool_calls(
    *,
    tool_calls: List[Dict[str, Any]],
    workspace_id: str,
    thread_id: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    credentials: Optional[Dict[str, Any]] = None,
    reasoning_effort: str = "",
    session_ctx: Optional[Dict[str, Any]] = None,
    execute_single_tool_call: Callable[..., str],
) -> str:
    if not tool_calls:
        return ""
    replies: List[str] = []
    for index, call in enumerate(tool_calls, start=1):
        replies.append(
            execute_single_tool_call(
                tool_call=call,
                workspace_id=workspace_id,
                thread_id=thread_id,
                index=index,
                provider=provider,
                model=model,
                credentials=credentials,
                reasoning_effort=reasoning_effort,
                session_ctx=session_ctx,
            )
        )
    return "\n\n".join(part for part in replies if part).strip()
