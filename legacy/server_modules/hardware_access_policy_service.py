from __future__ import annotations

from typing import Any, Dict, Optional
import re

from server_modules import execution_mode_policy
from server_modules.capability_registry import canonical_capability_id, resolve_capability


DEFAULT_GUARDED_RUNTIME_ACCESS_MODE = execution_mode_policy.GUARDED_RUNTIME_ACCESS_MODE
FULL_RUNTIME_ACCESS_MODE = execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
CUSTOM_RUNTIME_ACCESS_MODE = execution_mode_policy.CUSTOM_RUNTIME_ACCESS_MODE

CAPABILITY_ALIASES = {
    "browser": "browser_automation.interactive",
    "browser.open": "browser_automation.interactive",
    "browser.navigate": "browser_automation.interactive",
    "browser.interactive": "browser_automation.interactive",
    "browser__open": "browser_automation.interactive",
    "browser__navigate": "browser_automation.interactive",
    "file": "filesystem.read_write",
    "file.read": "filesystem.read",
    "file.write": "filesystem.write",
    "file.list": "filesystem.read",
    "file__read": "filesystem.read",
    "file__write": "filesystem.write",
    "file__list": "filesystem.read",
    "filesystem": "filesystem.read_write",
    "screenshot": "screenshot.capture",
    "screenshot.capture": "screenshot.capture",
    "screenshot__capture": "screenshot.capture",
    "shell": "shell.execute",
    "shell.exec": "shell.execute",
    "shell.execute": "shell.execute",
    "shell__exec": "shell.execute",
    "shell__execute": "shell.execute",
    "run": "shell.execute",
    "run.command": "shell.execute",
    "run__command": "shell.execute",
    "command": "shell.execute",
}

DEFAULT_GUARDED_APPROVAL_CAPABILITIES = {
    "computer_control.click",
    "computer_control.type",
    "computer_control.key",
    "computer_control.move",
    "computer_control.clipboard_read",
    "computer_control.clipboard_write",
    "computer_control.launch",
    "computer_control.launch_app",
    "computer_control.applescript",
    "computer_control.notify",
    "computer_control.speak",
    "connector.action.write",
    "connector.action.execute",
    "send_message",
    "payment.charge",
    "payment.refund",
    "payment.transfer",
}

DESTRUCTIVE_SHELL_MARKERS = (
    "rm -rf",
    "rm -r ",
    "rm -f ",
    "sudo rm",
    "del /f",
    "del /q",
    "rmdir /s",
    "format ",
    "mkfs",
    "diskutil erase",
    "shred ",
    "dd if=",
    "chmod -r",
    "chown -r",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_hardware_capability_id(action_id: Any, capability_id: Any = None) -> str:
    candidate = _text(capability_id) or _text(action_id)
    if not candidate:
        return ""
    lowered = candidate.lower().replace(" ", "_")
    resolved = CAPABILITY_ALIASES.get(lowered) or CAPABILITY_ALIASES.get(lowered.replace("__", "."))
    return canonical_capability_id(resolved or lowered)


def normalize_runtime_access_mode(value: Any = None, *, execution_mode: Any = None) -> str:
    return execution_mode_policy.normalize_runtime_access_mode(value, execution_mode=execution_mode)


def runtime_access_metadata(runtime_access_mode: str, execution_mode: Optional[str]) -> Dict[str, Any]:
    mode = normalize_runtime_access_mode(runtime_access_mode, execution_mode=execution_mode)
    if mode == FULL_RUNTIME_ACCESS_MODE:
        default_execution_mode = "full_access"
        approval_mode = "no_empyralis_action_approvals"
    elif mode == CUSTOM_RUNTIME_ACCESS_MODE:
        default_execution_mode = "custom"
        approval_mode = "custom_policy_guarded"
    else:
        default_execution_mode = "default"
        approval_mode = "default_guarded"
    return {
        "runtime_access_mode": mode,
        "execution_mode": _text(execution_mode) or default_execution_mode,
        "permission_mode": mode,
        "approval_mode": approval_mode,
        "empyralis_action_approvals_enabled": mode != FULL_RUNTIME_ACCESS_MODE,
    }


def _compact_command_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def shell_command_requires_guarded_approval(command: Any) -> bool:
    compact = _compact_command_text(command)
    if not compact:
        return False
    return any(marker in compact for marker in DESTRUCTIVE_SHELL_MARKERS)


def file_action_requires_guarded_approval(action_id: str, arguments: Dict[str, Any]) -> bool:
    mode = _text(arguments.get("mode") or arguments.get("operation") or action_id).lower().replace("__", ".")
    if "delete" in mode or mode in {"remove", "unlink", "trash"}:
        return True
    if "write" in mode or "append" in mode or action_id in {"file.write", "file__write"}:
        return True
    if action_id in {"file.read", "file.list", "file__read", "file__list"}:
        return False
    return False


def action_requests_external_send(action_id: str, capability_id: str, arguments: Dict[str, Any]) -> bool:
    tokens = " ".join(
        [
            _text(action_id).lower(),
            _text(capability_id).lower(),
            _text(arguments.get("action")).lower(),
            _text(arguments.get("operation")).lower(),
            _text(arguments.get("tool")).lower(),
        ]
    )
    return any(
        marker in tokens
        for marker in (
            "send",
            "post",
            "reply",
            "publish",
            "purchase",
            "payment",
            "charge",
            "refund",
            "transfer",
        )
    )


def default_guarded_action_requires_approval(
    *,
    capability_id: str,
    action_id: str,
    arguments: Dict[str, Any],
    virtual_action: Optional[str] = None,
) -> bool:
    normalized_action = _text(action_id).lower().replace("__", ".")
    normalized_capability = canonical_capability_id(capability_id)
    if normalized_capability == "shell.execute":
        return shell_command_requires_guarded_approval(arguments.get("command") or arguments.get("script"))
    if normalized_capability == "filesystem.read":
        return False
    if normalized_capability == "filesystem.write":
        return True
    if normalized_capability == "filesystem.read_write":
        return file_action_requires_guarded_approval(normalized_action, arguments)
    if normalized_capability == "screenshot.capture":
        return False
    if normalized_capability == "browser_automation.interactive":
        browser_action = _text(arguments.get("action") or virtual_action or normalized_action).lower().replace("__", ".")
        return browser_action in {"click", "type", "fill", "download", "download_file", "execute_js"}
    if normalized_capability.startswith("computer_control."):
        return True
    if normalized_capability in DEFAULT_GUARDED_APPROVAL_CAPABILITIES:
        return True
    if action_requests_external_send(normalized_action, normalized_capability, arguments):
        return True
    contract = resolve_capability(normalized_capability, enforce_kill_switch=False) if normalized_capability else None
    if contract is None:
        return True
    return bool(contract.requires_approval) or str(contract.risk_level or "").strip().lower() in {"high", "critical"}


def hardware_action_requires_software_approval(
    *,
    runtime_access_mode: str,
    capability_id: str,
    action_id: str,
    arguments: Dict[str, Any],
    require_approval: Optional[bool],
    virtual_action: Optional[str] = None,
) -> bool:
    # Permission escalation must come from the stored Agent Computer mode/policy,
    # not from natural-language text inside tool arguments.
    if normalize_runtime_access_mode(runtime_access_mode) == FULL_RUNTIME_ACCESS_MODE:
        return False
    if require_approval is not None:
        return bool(require_approval)
    return default_guarded_action_requires_approval(
        capability_id=capability_id,
        action_id=action_id,
        arguments=arguments,
        virtual_action=virtual_action,
    )
