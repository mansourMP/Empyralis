from __future__ import annotations

from typing import Any, Dict, Optional

from server_modules.capability_registry import resolve_capability


def browser_checkpoint_from_run(run: Any) -> Dict[str, Any]:
    checkpoint = run.get("browser_checkpoint") if isinstance(run, dict) else None
    if isinstance(checkpoint, dict) and checkpoint:
        return dict(checkpoint)
    return {}


def checkpoint_next_action_index(checkpoint: Any) -> Optional[int]:
    if not isinstance(checkpoint, dict):
        return None
    value = checkpoint.get("next_action_index")
    return value if isinstance(value, int) else None


def checkpoint_session_profile(checkpoint: Any) -> Optional[str]:
    if not isinstance(checkpoint, dict):
        return None
    token = str(checkpoint.get("session_profile") or "").strip()
    return token or None


def checkpoint_summary(checkpoint: Any) -> Dict[str, Any]:
    return {
        "next_action_index": checkpoint_next_action_index(checkpoint),
        "session_profile": checkpoint_session_profile(checkpoint),
    }


def attach_browser_checkpoint(
    run: Dict[str, Any],
    checkpoint: Any,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = browser_checkpoint_from_run({"browser_checkpoint": checkpoint})
    if not normalized:
        return {}
    run["browser_checkpoint"] = dict(normalized)
    target_metadata: Dict[str, Any]
    if isinstance(metadata, dict):
        target_metadata = metadata
    else:
        context = run.get("context") if isinstance(run.get("context"), dict) else {}
        target_metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        context["metadata"] = target_metadata
        run["context"] = context
    target_metadata["browser_checkpoint"] = dict(normalized)
    target_metadata["browser_resume_supported"] = True
    return dict(normalized)


def browser_resume_supported(source: Any, metadata: Optional[Dict[str, Any]] = None) -> bool:
    return bool(browser_checkpoint_from_run(source) or (metadata or {}).get("browser_resume_supported"))


def browser_action_from_result_data(result_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    outputs = result_data.get("outputs") if isinstance(result_data, dict) and isinstance(result_data.get("outputs"), dict) else {}
    actions = outputs.get("actions") if isinstance(outputs.get("actions"), list) else []
    for item in reversed(actions):
        if not isinstance(item, dict):
            continue
        raw_tool = str(item.get("tool") or "").strip().lower()
        contract = resolve_capability(raw_tool)
        tool_id = str(contract.capability_id).strip().lower() if contract is not None else raw_tool
        if tool_id == "browser_automation.interactive":
            return item
    return None


def browser_introspection_snapshot(
    run: Dict[str, Any],
    result_data: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    checkpoint = browser_checkpoint_from_run(run)
    browser_action = browser_action_from_result_data(result_data)
    if not browser_action and not checkpoint:
        return None
    return {
        "tabs": (
            browser_action.get("tabs")
            if isinstance(browser_action, dict) and isinstance(browser_action.get("tabs"), list)
            else checkpoint.get("tabs")
            if isinstance(checkpoint.get("tabs"), list)
            else []
        ),
        "console_entries": (
            browser_action.get("console_entries")
            if isinstance(browser_action, dict) and isinstance(browser_action.get("console_entries"), list)
            else []
        ),
        "network_failures": (
            browser_action.get("network_failures")
            if isinstance(browser_action, dict) and isinstance(browser_action.get("network_failures"), list)
            else []
        ),
        "role_snapshot": (
            browser_action.get("role_snapshot")
            if isinstance(browser_action, dict) and isinstance(browser_action.get("role_snapshot"), list)
            else checkpoint.get("role_snapshot")
            if isinstance(checkpoint.get("role_snapshot"), list)
            else []
        ),
        "accessibility_snapshot": (
            browser_action.get("accessibility_snapshot")
            if isinstance(browser_action, dict) and isinstance(browser_action.get("accessibility_snapshot"), dict)
            else checkpoint.get("accessibility_snapshot")
            if isinstance(checkpoint.get("accessibility_snapshot"), dict)
            else {}
        ),
    }
