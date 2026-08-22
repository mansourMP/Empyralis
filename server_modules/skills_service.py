from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional
import uuid

from server_modules.capability_registry import resolve_capability, workflow_tool_capability_id
from server_modules import execution_mode_policy
from server_modules import local_tool_executor
from server_modules import authority_mandate_service

# MAN-356: this module carried no logging at all. The one call site is the
# gateway resolver dropping a model-supplied `gateway_id` that is not the
# agent's placement — a decision that is otherwise completely silent, and
# CLAUDE.md's rule is that a swallowed decision must name what it lost.
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CapabilityDescriptor:
    capability_id: str
    label: str
    risk_level: str = "medium"
    requires_approval: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillDescriptor:
    skill_id: str
    label: str
    capabilities: List[CapabilityDescriptor] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolDescriptor:
    tool_name: str
    label: str
    connector_id: str
    action_id: str
    description: str
    capability_id: str = ""
    risk_level: str = "medium"
    requires_approval: bool = False
    parameters: Dict[str, Any] = field(default_factory=dict)
    requires_runtime: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


def _normalize_action_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized: List[str] = []
    seen = set()
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_capability_id(value: Any) -> str:
    token = re.sub(r"[^a-z0-9_. -]+", " ", str(value or "").strip().lower())
    return re.sub(r"\s+", "_", token).strip("_")


def capability_descriptor_from_payload(item: Any) -> CapabilityDescriptor | None:
    if not isinstance(item, dict):
        return None
    capability_id = _normalize_capability_id(
        item.get("id") or item.get("capability_id") or item.get("label")
    )
    if not capability_id:
        return None
    approval_actions = _normalize_action_list(item.get("approval_required_actions"))
    connected = bool(item.get("connected"))
    authenticated = item.get("authenticated") if isinstance(item.get("authenticated"), bool) else None
    runtime_usable = item.get("runtime_usable") if isinstance(item.get("runtime_usable"), bool) else None
    contract = resolve_capability(capability_id)
    return CapabilityDescriptor(
        capability_id=capability_id,
        label=str(item.get("label") or capability_id).strip() or capability_id,
        risk_level=(contract.risk_level if contract is not None else "medium"),
        requires_approval=bool(approval_actions) or bool(contract and contract.requires_approval),
        metadata={
            "connected": connected,
            "authenticated": authenticated,
            "runtime_usable": runtime_usable,
            "read_actions": _normalize_action_list(item.get("read_actions")),
            "write_actions": _normalize_action_list(item.get("write_actions")),
            "approval_required_actions": approval_actions,
        },
    )


def capability_payload_from_descriptor(descriptor: CapabilityDescriptor) -> Dict[str, Any]:
    metadata = descriptor.metadata if isinstance(descriptor.metadata, dict) else {}
    return {
        "id": descriptor.capability_id,
        "label": str(descriptor.label or descriptor.capability_id).strip() or descriptor.capability_id,
        "risk_level": str(descriptor.risk_level or "medium").strip() or "medium",
        "requires_approval": bool(descriptor.requires_approval),
        "connected": bool(metadata.get("connected")),
        "authenticated": metadata.get("authenticated") if isinstance(metadata.get("authenticated"), bool) else None,
        "runtime_usable": metadata.get("runtime_usable") if isinstance(metadata.get("runtime_usable"), bool) else None,
        "read_actions": _normalize_action_list(metadata.get("read_actions")),
        "write_actions": _normalize_action_list(metadata.get("write_actions")),
        "approval_required_actions": _normalize_action_list(metadata.get("approval_required_actions")),
    }


def normalize_capability_payloads(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in items:
        descriptor = capability_descriptor_from_payload(item)
        if descriptor is None:
            continue
        normalized.append(capability_payload_from_descriptor(descriptor))
    return normalized


def normalize_availability_capability_payloads(availability: Any) -> List[Dict[str, Any]]:
    items = availability.get("tool_capabilities") if isinstance(availability, dict) else []
    return normalize_capability_payloads(items)


def availability_capability(availability: Any, capability_id: str) -> Dict[str, Any] | None:
    token = str(capability_id or "").strip().lower()
    if not token:
        return None
    for item in normalize_availability_capability_payloads(availability):
        if str(item.get("id") or "").strip().lower() == token:
            return item
    return None


def availability_capability_connected(availability: Any, capability_id: str) -> bool:
    item = availability_capability(availability, capability_id)
    return bool(item and item.get("connected"))


def availability_capability_runtime_usable(availability: Any, capability_id: str) -> bool | None:
    item = availability_capability(availability, capability_id)
    return capability_payload_runtime_usable(item)


def capability_payload_connected(item: Any) -> bool:
    return bool(isinstance(item, dict) and item.get("connected"))


def capability_payload_runtime_usable(item: Any) -> bool | None:
    if not isinstance(item, dict):
        return None
    return item.get("runtime_usable") if isinstance(item.get("runtime_usable"), bool) else None


def capability_payload_write_actions(item: Any) -> List[str]:
    if not isinstance(item, dict):
        return []
    return _normalize_action_list(item.get("write_actions"))


def capability_payload_approval_required_actions(item: Any) -> List[str]:
    if not isinstance(item, dict):
        return []
    return _normalize_action_list(item.get("approval_required_actions"))


def capability_payload_supports_write_action(item: Any, action_id: str) -> bool:
    normalized_action_id = str(action_id or "").strip()
    if not normalized_action_id:
        return False
    return normalized_action_id in set(capability_payload_write_actions(item))


def capability_payload_requires_approval_for_action(item: Any, action_id: str) -> bool:
    normalized_action_id = str(action_id or "").strip()
    if not normalized_action_id:
        return False
    return normalized_action_id in set(capability_payload_approval_required_actions(item))


def availability_capability_write_actions(availability: Any, capability_id: str) -> List[str]:
    item = availability_capability(availability, capability_id)
    return capability_payload_write_actions(item)


def availability_capability_approval_required_actions(availability: Any, capability_id: str) -> List[str]:
    item = availability_capability(availability, capability_id)
    return capability_payload_approval_required_actions(item)


def availability_capability_supports_write_action(availability: Any, capability_id: str, action_id: str) -> bool:
    item = availability_capability(availability, capability_id)
    return capability_payload_connected(item) and capability_payload_supports_write_action(item, action_id)


def availability_capability_requires_approval_for_action(availability: Any, capability_id: str, action_id: str) -> bool:
    item = availability_capability(availability, capability_id)
    return capability_payload_requires_approval_for_action(item, action_id)


def connected_availability_capabilities(availability: Any) -> List[Dict[str, Any]]:
    return [
        item
        for item in normalize_availability_capability_payloads(availability)
        if item.get("connected")
    ]


def connected_availability_labels(availability: Any) -> List[str]:
    return [str(item.get("label") or "").strip() for item in connected_availability_capabilities(availability)]


def unavailable_connected_availability_labels(availability: Any) -> List[str]:
    return [
        str(item.get("label") or "").strip()
        for item in connected_availability_capabilities(availability)
        if item.get("runtime_usable") is False
    ]


def unverified_connected_availability_labels(availability: Any) -> List[str]:
    return [
        str(item.get("label") or "").strip()
        for item in connected_availability_capabilities(availability)
        if item.get("runtime_usable") is None
    ]


def context_availability_capabilities(
    availability: Any,
    *,
    max_context_tool_actions: int,
    max_context_tool_capabilities: int,
) -> List[Dict[str, Any]]:
    trimmed: List[Dict[str, Any]] = []
    for item in connected_availability_capabilities(availability):
        trimmed.append(
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "connected": True,
                "authenticated": item.get("authenticated") if isinstance(item.get("authenticated"), bool) else None,
                "runtime_usable": item.get("runtime_usable") if isinstance(item.get("runtime_usable"), bool) else None,
                "read_actions": (item.get("read_actions") or [])[:max_context_tool_actions],
                "write_actions": (item.get("write_actions") or [])[:max_context_tool_actions],
                "approval_required_actions": (item.get("approval_required_actions") or [])[:max_context_tool_actions],
            }
        )
        if len(trimmed) >= max_context_tool_capabilities:
            break
    return trimmed


def availability_label_summary(availability: Any) -> Dict[str, List[str]]:
    connected_labels = connected_availability_labels(availability)
    unavailable_labels = unavailable_connected_availability_labels(availability)
    unverified_labels = unverified_connected_availability_labels(availability)
    usable_labels = [
        str(item.get("label") or "").strip()
        for item in connected_availability_capabilities(availability)
        if item.get("runtime_usable") is True
    ]
    return {
        "connected": connected_labels,
        "usable": usable_labels,
        "unavailable": unavailable_labels,
        "unverified": unverified_labels,
    }


def resolve_workspace_capability_payloads(
    workspace_id: str,
    *,
    resolve_workspace_tool_capabilities_fn: Any,
) -> List[Dict[str, Any]]:
    raw_items = resolve_workspace_tool_capabilities_fn(str(workspace_id or "default").strip() or "default")
    if not isinstance(raw_items, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        descriptor = capability_descriptor_from_payload(item)
        if descriptor is None:
            continue
        payload = dict(item)
        payload["id"] = descriptor.capability_id
        if "label" in payload or descriptor.label != descriptor.capability_id:
            payload["label"] = descriptor.label
        if "read_actions" in payload:
            payload["read_actions"] = _normalize_action_list(payload.get("read_actions"))
        if "write_actions" in payload:
            payload["write_actions"] = _normalize_action_list(payload.get("write_actions"))
        if "approval_required_actions" in payload:
            payload["approval_required_actions"] = _normalize_action_list(payload.get("approval_required_actions"))
        normalized.append(payload)
    return normalized


def _tool_payload_from_descriptor(descriptor: ToolDescriptor) -> Dict[str, Any]:
    contract = resolve_capability(descriptor.capability_id)
    risk_level = contract.risk_level if contract is not None else str(descriptor.risk_level or "medium").strip() or "medium"
    requires_approval = bool(contract.requires_approval) if contract is not None else bool(descriptor.requires_approval)
    permission_manifest = _permission_manifest_for_tool(
        connector_id=descriptor.connector_id,
        action_id=descriptor.action_id,
        capability_id=descriptor.capability_id,
        risk_level=risk_level,
        requires_approval=requires_approval,
        requires_runtime=descriptor.requires_runtime,
        contract=contract,
    )
    return {
        "name": descriptor.tool_name,
        "description": descriptor.description,
        "label": descriptor.label,
        "connector_id": descriptor.connector_id,
        "action_id": descriptor.action_id,
        "capability_id": descriptor.capability_id or None,
        "risk_level": risk_level,
        "requires_approval": requires_approval,
        "action_class": permission_manifest["action_class"],
        "allowed_runtime_modes": permission_manifest["allowed_runtime_modes"],
        "cost_class": permission_manifest["cost_class"],
        "audit_event_type": permission_manifest["audit_event_type"],
        "permission_manifest": permission_manifest,
        "parameters": descriptor.parameters if isinstance(descriptor.parameters, dict) else {},
    }


def _action_class_for_tool(connector_id: str, action_id: str) -> str:
    connector = str(connector_id or "").strip().lower()
    action = str(action_id or "").strip().lower()
    read_actions = {
        "capture",
        "fetch",
        "get",
        "list",
        "list_state",
        "ocr",
        "read",
        "search",
        "extract_text",
        "extract_dom",
    }
    write_actions = {
        "append",
        "click",
        "create",
        "create_entry",
        "generate",
        "move",
        "post",
        "send",
        "speak",
        "type",
        "update",
        "update_profile",
        "upload",
        "write",
    }
    execute_actions = {"applescript", "exec", "execute", "hotkey", "key", "request"}
    if action in execute_actions or connector == "shell":
        return "execute"
    if action in write_actions:
        return "write"
    if action in read_actions:
        return "read"
    return "write" if connector in {"telegram_bot", "smtp", "slack", "discord_bot"} else "read"


def _cost_class_for_tool(connector_id: str, action_id: str) -> str:
    connector = str(connector_id or "").strip().lower()
    if connector in {"image", "llm"}:
        return "metered"
    if connector in {"web", "http", "browser"}:
        return "external"
    if connector in {"telegram_bot", "smtp", "google_workspace", "microsoft_365", "slack", "discord_bot", "dropbox", "s3"}:
        return "external"
    return "standard"


def _runtime_modes_for_tool(
    *,
    requires_runtime: bool,
    risk_level: str,
    contract: Any,
) -> List[str]:
    allowed_environments = list(getattr(contract, "allowed_environments", []) or [])
    if not allowed_environments:
        allowed_environments = ["local_companion"] if requires_runtime else ["hosted", "local_companion"]
    modes: List[str] = []
    for environment in allowed_environments:
        token = str(environment or "").strip().lower()
        if token in {"hosted", "cloud", "cloud_computer"} and "hosted_secure" not in modes:
            modes.append("hosted_secure")
        if token == "local_companion":
            local_mode = "privileged_device" if str(risk_level or "").strip().lower() == "critical" else "local_secure"
            if local_mode not in modes:
                modes.append(local_mode)
    return modes or ["hosted_secure", "local_secure"]


def _permission_manifest_for_tool(
    *,
    connector_id: str,
    action_id: str,
    capability_id: Any,
    extra_scopes: List[str] | None = None,
    risk_level: str,
    requires_approval: bool,
    requires_runtime: bool,
    contract: Any,
) -> Dict[str, Any]:
    connector = str(connector_id or "").strip()
    action = str(action_id or "").strip()
    capability_token = str(capability_id or "").strip()
    scopes = [capability_token] if capability_token else []
    scopes.extend(str(scope or "").strip() for scope in list(extra_scopes or []))
    if not scopes:
        scopes.append(f"{connector}:{action}")
    return {
        "action_class": _action_class_for_tool(connector, action),
        "risk_level": str(risk_level or "medium").strip() or "medium",
        "scopes": [scope for scope in scopes if scope],
        "requires_approval": bool(requires_approval),
        "allowed_runtime_modes": _runtime_modes_for_tool(
            requires_runtime=requires_runtime,
            risk_level=risk_level,
            contract=contract,
        ),
        "cost_class": _cost_class_for_tool(connector, action),
        "audit_event_type": f"direct_tool.{connector}.{action}".strip("."),
    }


def _local_tool_descriptors() -> List[ToolDescriptor]:
    return [
        ToolDescriptor(
            tool_name="file__read",
            label="Local file read",
            connector_id="file",
            action_id="read",
            description="Read a file from the local machine",
            capability_id="filesystem.read",
            requires_runtime=True,
            parameters={"type": "object", "properties": {"path": {"type": "string", "description": "File path to read"}}, "required": ["path"]},
        ),
        ToolDescriptor(
            tool_name="file__write",
            label="Local file write",
            connector_id="file",
            action_id="write",
            description="Write content to a file on the local machine",
            capability_id="filesystem.write",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        ),
        ToolDescriptor(
            tool_name="shell__exec",
            label="Local shell exec",
            connector_id="shell",
            action_id="exec",
            description=(
                "Execute one or more shell commands on the local machine. Running on this "
                "machine is a slow network round trip (the machine may be far from where this "
                "runs) — if you already know you need several commands (e.g. cd into a "
                "directory then run a build, or check three things in sequence), pass them ALL "
                "at once in `commands` instead of calling this tool once per command. Commands "
                "in one `commands` call run in order, in ONE shared session: `cd`, `export`, "
                "and anything else that changes the shell's own state in one command carries "
                "into the next, exactly like typing them one after another in the same "
                "terminal. By default the batch stops at the first command that fails and the "
                "rest are reported as not run (set `stop_on_failure` to false to run every "
                "command regardless). Use `command` for a single, standalone command."
            ),
            capability_id="shell.execute",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "A single shell command to run. Omit if using `commands`."},
                    "commands": {
                        "type": "array",
                        "description": (
                            "Two or more commands to run in order, in one round trip, sharing one "
                            "session (cwd/env carry from one command to the next). Omit if using "
                            "`command`. Provide either a plain string per command, or an object "
                            "with `command` and an optional per-command `timeout_seconds`."
                        ),
                        "items": {
                            "anyOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "command": {"type": "string"},
                                        "timeout_seconds": {"type": "integer", "description": "Budget for this one command, in seconds."},
                                    },
                                    "required": ["command"],
                                },
                            ]
                        },
                        "minItems": 1,
                    },
                    "stop_on_failure": {
                        "type": "boolean",
                        "description": "Only used with `commands`. Default true: stop the batch at the first failing command. Set false to run every command regardless of earlier failures.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout in seconds. With `command`, the timeout for that command. With `commands`, the default applied to any entry that doesn't set its own timeout_seconds.",
                    },
                },
            },
        ),
        ToolDescriptor(
            tool_name="screenshot__capture",
            label="Local screenshot",
            connector_id="screenshot",
            action_id="capture",
            description="Take a screenshot of the current screen",
            capability_id="screenshot.capture",
            requires_runtime=True,
            parameters={"type": "object", "properties": {}},
        ),
        ToolDescriptor(
            tool_name="computer__ocr",
            label="Computer OCR",
            connector_id="computer",
            action_id="ocr",
            description=(
                "Read the text currently visible on the screen/display via OCR (optical "
                "character recognition). Use this when the user asks what's on their "
                "screen, or to see/read text in an image or window that isn't otherwise "
                "accessible as plain text. Optionally scope to a rectangular region; omit "
                "it to OCR the entire screen."
            ),
            capability_id="computer_control.ocr",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {
                    "region": {
                        "type": "object",
                        "description": "Optional pixel region to limit OCR to. Omit to scan the whole screen.",
                        "properties": {
                            "x": {"type": "integer", "description": "Left edge of the region, in pixels."},
                            "y": {"type": "integer", "description": "Top edge of the region, in pixels."},
                            "width": {"type": "integer", "description": "Region width, in pixels."},
                            "height": {"type": "integer", "description": "Region height, in pixels."},
                        },
                    },
                },
            },
        ),
        ToolDescriptor(
            tool_name="computer__click",
            label="Computer click",
            connector_id="computer",
            action_id="click",
            description="Click on the screen by coordinates or visible text",
            capability_id="computer_control.click",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X pixel coordinate to click. Provide with y, or use text instead."},
                    "y": {"type": "integer", "description": "Y pixel coordinate to click. Provide with x, or use text instead."},
                    "text": {"type": "string", "description": "Visible on-screen text to click, as an alternative to x/y coordinates."},
                },
            },
        ),
        ToolDescriptor(
            tool_name="computer__type",
            label="Computer type",
            connector_id="computer",
            action_id="type",
            description="Type text into the active application",
            capability_id="computer_control.type",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "The text to type into the currently focused field/application."}},
                "required": ["text"],
            },
        ),
        ToolDescriptor(
            tool_name="computer__applescript",
            label="Run Script",
            connector_id="computer",
            action_id="applescript",
            description="Execute a system script on your computer",
            capability_id="computer_control.applescript",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {"script": {"type": "string", "description": "The AppleScript source code to execute."}},
                "required": ["script"],
            },
        ),
        ToolDescriptor(
            tool_name="computer__clipboard_read",
            label="Computer clipboard read",
            connector_id="computer",
            action_id="clipboard_read",
            description="Read the current system clipboard",
            capability_id="computer_control.clipboard_read",
            requires_runtime=True,
            parameters={"type": "object", "properties": {}},
        ),
        ToolDescriptor(
            tool_name="computer__clipboard_write",
            label="Computer clipboard write",
            connector_id="computer",
            action_id="clipboard_write",
            description="Write text to the system clipboard",
            capability_id="computer_control.clipboard_write",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "The text to write to the system clipboard."}},
                "required": ["text"],
            },
        ),
        ToolDescriptor(
            tool_name="computer__notify",
            label="Computer notify",
            connector_id="computer",
            action_id="notify",
            description="Send a system notification",
            capability_id="computer_control.notify",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Notification title."},
                    "message": {"type": "string", "description": "Notification body text."},
                },
                "required": ["title", "message"],
            },
        ),
        ToolDescriptor(
            tool_name="computer__list_apps",
            label="Computer list apps",
            connector_id="computer",
            action_id="list_apps",
            description="List running applications and processes",
            capability_id="computer_control.list_apps",
            requires_runtime=True,
            parameters={"type": "object", "properties": {}},
        ),
        ToolDescriptor(
            tool_name="computer__launch_app",
            label="Computer launch app",
            connector_id="computer",
            action_id="launch_app",
            description="Launch an application by name or path",
            capability_id="computer_control.launch_app",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {"name_or_path": {"type": "string", "description": "Application name (e.g. 'Safari') or full path to launch."}},
                "required": ["name_or_path"],
            },
        ),
        ToolDescriptor(
            tool_name="computer__speak",
            label="Computer speak",
            connector_id="computer",
            action_id="speak",
            description="Speak text aloud using the local system voice",
            capability_id="computer_control.speak",
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to speak aloud."},
                    "voice": {"type": "string", "description": "Optional system voice name to use. Omit to use the default voice."},
                },
                "required": ["text"],
            },
        ),
    ]


# The `priority` parameter shared by project_task__create and
# project_task__update. Written once, deliberately: an agent can only ever
# set a field its tool schema advertises, so the two schemas drifting apart
# would silently make priority settable on one path and not the other.
#
# Advertised as the raw integer rather than a name enum because that is what
# the column stores and what the Linear MCP API accepts — a name enum here
# would put a translation layer back exactly where matching Linear's
# encoding was meant to remove one. The inversion (1 = MOST urgent) is
# spelled out in full because it is the one thing a model is likely to get
# backwards from intuition; project_tasks_service._normalize_priority also
# accepts the names as a safety net if it does.
_PROJECT_TASK_PRIORITY_SCHEMA = {
    "type": "integer",
    "enum": [0, 1, 2, 3, 4],
    "description": (
        "Priority, on Linear's scale: 0 = none (no priority set / untriaged), "
        "1 = urgent, 2 = high, 3 = medium, 4 = low. NOTE the direction: 1 is the "
        "MOST urgent and 4 the least — a LOWER number means MORE urgent. Use 0 to "
        "clear a priority back to untriaged."
    ),
}

# The `parent_task_id` parameter shared by project_task__create and
# project_task__set_parent, written once for the same reason the priority
# schema above is: an agent can only ever set a field its tool schema
# advertises, so two copies drifting apart would silently make sub-tasks
# creatable on one path and not the other.
#
# The one-level constraint is spelled out IN THE DESCRIPTION, not left to
# the error path. A model that only discovers the rule by being rejected
# burns a turn and often retries the same shape; a model that reads it up
# front simply does the right thing. The rule is enforced for real in
# project_tasks_service._resolve_parent_task either way.
_PROJECT_TASK_PARENT_SCHEMA = {
    "type": "string",
    "description": (
        "Optional. The id of an existing task in this project to file this one "
        "under as a SUB-TASK. IMPORTANT: this board allows exactly ONE level of "
        "nesting — the parent must be a top-level task. Pointing at a task that is "
        "itself already a sub-task is rejected; attach it to that sub-task's own "
        "parent instead. Parent and sub-task must be in the same project. Omit for "
        "a normal top-level task."
    ),
}


def _builtin_tool_descriptors() -> List[ToolDescriptor]:
    return [
        ToolDescriptor(
            tool_name="task_complete",
            label="Task complete",
            connector_id="sage",
            action_id="task_complete",
            description=(
                "Call this tool when you have finished the user's task. "
                "Provide a short summary of what was accomplished. "
                "Calling this tool signals that the work is complete and no further "
                "tool calls are needed. The platform will end the run cleanly."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "A short summary of what was accomplished.",
                    },
                },
                "required": ["summary"],
            },
        ),
        ToolDescriptor(
            tool_name="update_plan",
            label="Update plan",
            connector_id="sage",
            action_id="update_plan",
            description=(
                "For a multi-step task, call this first to lay out the steps as a short "
                "task list, then call it again to mark a task 'active' when you start it "
                "and 'done' when finished. Each call REPLACES the current plan — always "
                "pass the full, current list of tasks (not just the one that changed). "
                "For a simple single-step request, don't use this tool — just do the work."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "The full current list of tasks for this turn, in order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Short description of the task.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "active", "done", "skipped"],
                                    "description": "Task status. Defaults to 'pending' if omitted.",
                                },
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["tasks"],
            },
        ),
        ToolDescriptor(
            tool_name="hardware__action",
            label="Hardware action",
            connector_id="hardware",
            action_id="action",
            description=(
                "Run a browser, file, shell, screenshot, or app/window action through an Empyralis runtime target. "
                "Use runtime_target user_device_gateway for screenshots, screen inspection, keyboard, mouse, "
                "and app/window control on the user's paired computer. Use cloud_default only for cloud-only chat."
            ),
            requires_runtime=True,
            parameters={
                "type": "object",
                "properties": {
                    "runtime_target": {
                        "type": "string",
                        "enum": [
                            "cloud_default",
                            "user_device_gateway",
                            "empyralis_cloud_computer",
                            "self_hosted_node",
                        ],
                        "description": "Target runtime. Omit to use the selected hardware runtime when available.",
                    },
                    "action": {
                        "type": "string",
                        "description": "Capability/action such as file.read, shell.execute, screenshot.capture, browser.open, computer_control.click, or computer_control.launch_app.",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Action-specific arguments, for example path, command, url, selector, text, x/y coordinates, or app name.",
                    },
                },
                "required": ["action"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_search",
            label="Memory search",
            connector_id="memory",
            action_id="search",
            description=(
                "Mandatory recall step before answering about prior work, decisions, dates, people, "
                "preferences, or todos. Search MEMORY.md and memory/*.md and return matching snippets "
                "with paths and line numbers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The memory query to search for."},
                    "max_results": {"type": "integer", "description": "Optional maximum number of snippets to return."},
                },
                "required": ["query"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_write",
            label="Memory write",
            connector_id="memory",
            action_id="write",
            description=(
                "Write or append content to a file in the agent's memory directory. "
                "Use path='MEMORY.md' to save to the main memory file. "
                "Use mode='append' to add to existing content, or mode='overwrite' to replace. "
                "MUST call this tool to persist facts — text replies alone do not save anything. "
                "If this fact came from someone other than your owner (or you could not verify "
                "they are the owner), you MUST also set attribution_reason explaining why it's "
                "worth remembering — the saved line will be visibly marked as non-owner-sourced "
                "and is never treated as owner-grade fact. "
                "Writing to a memory/files/*.md topic file (a file for one topic that has earned "
                "its own file, e.g. memory/files/customers/acme.md) REQUIRES description — a short "
                "summary of what the file is about. It is used to create or refresh that file's "
                "one-line entry in MEMORY.md's index automatically, so a future session can find "
                "it; MEMORY.md itself never needs manual upkeep for this."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path within memory directory (e.g., 'MEMORY.md')."},
                    "content": {"type": "string", "description": "Text content to write or append."},
                    "mode": {"type": "string", "enum": ["append", "overwrite"], "description": "Write mode: 'append' (default) or 'overwrite'."},
                    "description": {
                        "type": "string",
                        "description": (
                            "Required only when path is a memory/files/*.md topic file: a short "
                            "description of what this file is about (e.g. 'Acme account: contract "
                            "terms, contacts, open issues'). Auto-upserted as that file's one-line "
                            "entry in MEMORY.md's index. Not used, and not required, for other files."
                        ),
                    },
                    "attribution_reason": {
                        "type": "string",
                        "description": (
                            "Required only when this fact came from a non-owner or unverified "
                            "sender: a short explanation of why it's worth saving. Omit entirely "
                            "for facts the owner told you directly."
                        ),
                    },
                },
                "required": ["path", "content"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_write_private",
            label="Memory write (private)",
            connector_id="memory",
            action_id="write_private",
            description=(
                "Save a note about how THIS specific person likes to be worked with — their own "
                "preferences, communication style, or working habits. This is NEVER visible to, "
                "and never shaped by, any other person who talks to this agent — it is not the "
                "same store memory_write saves to. Use this for 'how I like things done', not for "
                "facts about the company, project, or work itself — those belong in memory_write "
                "(shared with every other person on this project) instead. Each call REPLACES the "
                "full note (there is no filename/path — there is exactly one private note per "
                "person per agent), so include everything still worth keeping, not just the new part."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The full private preference note (replaces the previous one for this person).",
                    },
                },
                "required": ["content"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_get_private",
            label="Memory get (private)",
            connector_id="memory",
            action_id="get_private",
            description=(
                "Read back the private preference note previously saved for THIS specific person "
                "with memory_write_private — never another person's. Returns empty if none was saved."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            # Note (was an audience_safe=False rationale until 2026-08-21,
            # when the audience tier was deleted): an external caller has no
            # internal user_id to scope to, so this tool simply returns empty
            # for them. It is no longer hidden from them — _resolve_session_
            # user_id's own refusal is the honest answer, and hiding a tool
            # was never what made that true.
        ),
        ToolDescriptor(
            tool_name="memory_read",
            label="Memory read",
            connector_id="memory",
            action_id="read",
            description="Read a file from the agent's memory directory. Use to recall previously saved facts.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path within memory directory (e.g., 'MEMORY.md')."},
                },
                "required": ["path"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_get",
            label="Memory get",
            connector_id="memory",
            action_id="get",
            description="Read a small excerpt from MEMORY.md or memory/*.md after memory_search identifies the file and lines.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative notebook path such as MEMORY.md or memory/2026-04-02.md."},
                    "from": {"type": "integer", "description": "Starting line number (1-based)."},
                    "lines": {"type": "integer", "description": "Maximum number of lines to read."},
                },
                "required": ["path"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_update",
            label="Memory update",
            connector_id="memory",
            action_id="update",
            description=(
                "Update one workspace memory context file. Use only when the user explicitly asks the agent to "
                "remember, correct, or update durable memory. Read the current file first with memory_get, then "
                "write the complete revised file content. If any of the content you're incorporating came from "
                "someone other than your owner (or an unverified sender), you MUST also set attribution_reason. "
                "If filename is a memory/files/*.md topic file, description is REQUIRED — see memory_write."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Allowed context filename such as MEMORY.md, PROCEDURES.md, REFLECTION.md, or a memory/files/*.md topic file."},
                    "content": {"type": "string", "description": "Complete revised Markdown content for the file."},
                    "description": {
                        "type": "string",
                        "description": (
                            "Required only when filename is a memory/files/*.md topic file: a short "
                            "description of what this file is about, auto-upserted into MEMORY.md's "
                            "index as that file's one-line entry."
                        ),
                    },
                    "attribution_reason": {
                        "type": "string",
                        "description": (
                            "Required only when incorporating a non-owner or unverified sender's "
                            "content into this file: a short explanation of why it's worth keeping."
                        ),
                    },
                },
                "required": ["filename", "content"],
            },
            requires_approval=True,
        ),
        ToolDescriptor(
            tool_name="memory_stage_edit",
            label="Memory stage edit",
            connector_id="memory",
            action_id="stage_edit",
            description=(
                "Stage a proposed root memory file edit under memory/.dreams/. Use when the user asks to "
                "change durable behavior, procedures, or reflection files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Root context filename such as MEMORY.md, PROCEDURES.md, or REFLECTION.md."},
                    "content": {"type": "string", "description": "Complete proposed Markdown content for the target file."},
                    "reason": {"type": "string", "description": "Short reason for staging this memory edit."},
                    "source_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional source references supporting the edit.",
                    },
                },
                "required": ["filename", "content"],
            },
            requires_approval=True,
        ),
        ToolDescriptor(
            tool_name="memory_apply_edit",
            label="Memory apply edit",
            connector_id="memory",
            action_id="apply_edit",
            description="Apply a staged root memory edit after explicit user approval or policy allowance.",
            parameters={
                "type": "object",
                "properties": {
                    "staging_filename": {"type": "string", "description": "Staging file under memory/.dreams/ created by memory_stage_edit or memory_stage_consolidation."},
                    "merged_files": {
                        "type": "object",
                        "description": "Map of root context filename to complete approved Markdown content.",
                    },
                    "user_approved": {"type": "boolean", "description": "Explicit user approval flag."},
                    "policy_allows": {"type": "boolean", "description": "Policy allowance flag."},
                },
                "required": ["staging_filename", "merged_files"],
            },
            requires_approval=True,
        ),
        ToolDescriptor(
            tool_name="memory_append_daily_note",
            label="Memory append daily note",
            connector_id="memory",
            action_id="append_daily_note",
            description=(
                "Append one durable note to today's daily memory file only. "
                "Use for stable facts, decisions, preferences, or project context. "
                "A usefulness gate and dedupe filter are enforced. Do not include secrets, "
                "full chat transcripts, or temporary noise. If this note came from someone "
                "other than your owner (or you could not verify they are the owner), you MUST "
                "also set attribution_reason — the saved note will be visibly marked."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Durable note text to append to today's daily memory note file."},
                    "attribution_reason": {
                        "type": "string",
                        "description": (
                            "Required only when this note came from a non-owner or unverified "
                            "sender: a short explanation of why it's worth saving."
                        ),
                    },
                },
                "required": ["note"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_stage_consolidation",
            label="Memory stage consolidation",
            connector_id="memory",
            action_id="stage_consolidation",
            description=(
                "Create a proposed memory consolidation file under memory/.dreams/. "
                "Staging files are temporary and not merged into root files unless user approval or policy allows."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "proposal": {"type": "string", "description": "Durable consolidation proposal summary."},
                    "target_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional root context files to update if approved later.",
                    },
                    "source_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional references for provenance.",
                    },
                },
                "required": ["proposal"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_consolidate_daily_notes",
            label="Memory consolidate daily notes",
            connector_id="memory",
            action_id="consolidate_daily_notes",
            description=(
                "Read daily memory notes and produce safe consolidation proposals for curated targets "
                "(MEMORY.md, PROCEDURES.md, REFLECTION.md, or the memory/files/goals.md topic file). Can apply "
                "merge only when explicitly approved or policy allows; supports optional post-merge compaction "
                "with audit metadata."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional subset of curated root files.",
                    },
                    "max_notes": {"type": "integer", "description": "Maximum number of daily notes to scan."},
                    "apply_merge": {"type": "boolean", "description": "Apply merge now. Defaults to false."},
                    "compact_mode": {
                        "type": "string",
                        "enum": ["none", "archive", "compact"],
                        "description": "Optional compaction behavior after successful merge.",
                    },
                    "user_approved": {"type": "boolean", "description": "Explicit user approval flag."},
                    "policy_allows": {"type": "boolean", "description": "Policy allowance flag."},
                    "run_id": {"type": "string", "description": "Optional run id for merge audit metadata."},
                },
            },
        ),
        ToolDescriptor(
            tool_name="memory_list_versions",
            label="Memory list versions",
            connector_id="memory",
            action_id="list_versions",
            description="List recent file version records for a memory/context file.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Context file path such as MEMORY.md or memory/2026-05-11.md."},
                    "limit": {"type": "integer", "description": "Maximum versions to return."},
                },
                "required": ["filename"],
            },
        ),
        ToolDescriptor(
            tool_name="memory_rollback_version",
            label="Memory rollback version",
            connector_id="memory",
            action_id="rollback_version",
            description="Rollback a memory/context file to a previous version id.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Context file path to rollback."},
                    "version_id": {"type": "string", "description": "Version id to restore."},
                    "reason": {"type": "string", "description": "Optional rollback reason."},
                    "run_id": {"type": "string", "description": "Optional run id for audit."},
                },
                "required": ["filename", "version_id"],
            },
        ),
        ToolDescriptor(
            tool_name="web__search",
            label="Web search",
            connector_id="web",
            action_id="search",
            description="Search the web and return the top 5 results with titles, URLs, and snippets.",
            parameters={"type": "object", "properties": {"query": {"type": "string", "description": "The search query to run."}}, "required": ["query"]},
        ),
        ToolDescriptor(
            tool_name="web__fetch",
            label="Web fetch",
            connector_id="web",
            action_id="fetch",
            description="Fetch a webpage and extract readable text from it.",
            parameters={"type": "object", "properties": {"url": {"type": "string", "description": "The URL to fetch."}}, "required": ["url"]},
        ),
        ToolDescriptor(
            tool_name="llm__task",
            label="LLM task",
            connector_id="llm",
            action_id="task",
            description="Run a focused sub-task with no tools. Optionally require JSON output with a schema.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The sub-task prompt."},
                    "schema": {"type": "object", "description": "Optional JSON schema for the required output."},
                },
                "required": ["prompt"],
            },
        ),
        ToolDescriptor(
            tool_name="http_request",
            label="HTTP request",
            connector_id="http",
            action_id="request",
            description="Make a generic HTTP request and return status, headers, and body.",
            capability_id="http_request",
            parameters={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method to use for the request."},
                    "url": {"type": "string", "description": "The target URL."},
                    "headers": {"type": "object", "description": "Optional request headers."},
                    "body": {"description": "Optional request body as a string or JSON object."},
                    "params": {"type": "object", "description": "Optional query parameters."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds."},
                    "auth_type": {"type": "string", "enum": ["none", "bearer", "basic"], "description": "Authentication scheme to apply, if any. 'bearer' sends auth_value as a Bearer token; 'basic' sends auth_value as 'user:pass' Basic auth."},
                    "auth_value": {"type": "string", "description": "Token or user:pass credentials."},
                },
                "required": ["method", "url"],
            },
        ),
        ToolDescriptor(
            tool_name="generate_image",
            label="Generate image",
            connector_id="image",
            action_id="generate",
            description="Generate one or more images from a prompt and save them locally.",
            # Capability-gated (agent_capability_service.py): this tool only
            # reaches a specialist's toolset when image_generation resolves
            # to a working provider for THAT agent — see
            # agent_turn_runtime_service._resolve_specialist_toolset /
            # _specialist_tool_allowed, which check this id against
            # resolved_capability_ids(). capability_id also feeds
            # capability_registry's risk/approval metadata as it does for
            # http_request/computer_control.* below.
            capability_id="image_generation",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The image prompt."},
                    "model": {"type": "string", "enum": ["dall-e-3", "dall-e-2", "stable-diffusion"]},
                    "size": {"type": "string", "enum": ["256x256", "512x512", "1024x1024"]},
                    "quality": {"type": "string", "enum": ["standard", "hd"]},
                    "n": {"type": "integer", "minimum": 1, "maximum": 4},
                    "save_to": {"type": "string", "description": "Optional local output path or directory."},
                },
                "required": ["prompt"],
            },
        ),
        ToolDescriptor(
            tool_name="sage_service__list_state",
            label="Personal service state",
            connector_id="sage_service",
            action_id="list_state",
            description="Read the saved state for Flashcards, Language Coach, or Nutrition Log.",
            parameters={
                "type": "object",
                "properties": {
                    "service_id": {
                        "type": "string",
                        "enum": ["flashcards", "language_coach", "nutrition_log"],
                        "description": "Which service to inspect.",
                    },
                },
                "required": ["service_id"],
            },
        ),
        ToolDescriptor(
            tool_name="sage_service__update_profile",
            label="Personal service profile",
            connector_id="sage_service",
            action_id="update_profile",
            description="Update saved profile settings for a personal service, such as study focus or nutrition targets.",
            parameters={
                "type": "object",
                "properties": {
                    "service_id": {
                        "type": "string",
                        "enum": ["flashcards", "language_coach", "nutrition_log"],
                        "description": "Which service to update.",
                    },
                    "profile": {
                        "type": "object",
                        "description": "Service-specific profile fields to store.",
                    },
                    "explicit_user_intent": {
                        "type": "boolean",
                        "description": "Set true when the user explicitly asked to save profile changes.",
                    },
                    "approval_granted": {
                        "type": "boolean",
                        "description": "Set true when this write was approved by policy/runtime controls.",
                    },
                    "approval_id": {
                        "type": "string",
                        "description": "Optional approval reference for audit.",
                    },
                },
                "required": ["service_id", "profile"],
            },
        ),
        ToolDescriptor(
            tool_name="sage_service__create_entry",
            label="Personal service entry",
            connector_id="sage_service",
            action_id="create_entry",
            description="Create a new flashcard, language practice item, or nutrition log entry.",
            parameters={
                "type": "object",
                "properties": {
                    "service_id": {
                        "type": "string",
                        "enum": ["flashcards", "language_coach", "nutrition_log"],
                        "description": "Which service to write to.",
                    },
                    "entry": {
                        "type": "object",
                        "description": "The service-specific entry payload to save.",
                    },
                    "explicit_user_intent": {
                        "type": "boolean",
                        "description": "Set true when the user explicitly asked to save this entry.",
                    },
                    "approval_granted": {
                        "type": "boolean",
                        "description": "Set true when this write was approved by policy/runtime controls.",
                    },
                    "approval_id": {
                        "type": "string",
                        "description": "Optional approval reference for audit.",
                    },
                },
                "required": ["service_id", "entry"],
            },
        ),
        # project_task__* (2026-07-25): the platform-agent side of the
        # project task board (docs/design/tasks-to-agents-research.md
        # Section 4.6) — closes the loop the "task_assigned" wakeup opens.
        # Distinct namespace from task_complete/update_plan above on
        # purpose: those are per-turn conversational-plan tools (connector
        # "sage"), these are the durable, cross-turn project board a human
        # and every agent in the project both see (connector "project_task",
        # backed by project_tasks_service.py). Every action here is scoped
        # to the CALLING agent's own project (skills_service._project_task_
        # scope resolves it from session identity) — an agent cannot read or
        # edit another project's tasks by guessing an id, matching the
        # locked "project is the collaboration boundary" ruling.
        ToolDescriptor(
            tool_name="project_task__create",
            label="Create task",
            connector_id="project_task",
            action_id="create",
            description=(
                "Create a new task on this project's shared task board — work "
                "a human or any agent in this project can pick up. Starts unassigned "
                "and 'todo'; use project_task__assign to hand it to an agent (yourself "
                "or a teammate in this project). Set priority if you already know how "
                "urgent the work is — it defaults to 0 (no priority set), which means "
                "nobody has triaged it yet. Pass parent_task_id to create this as a "
                "SUB-TASK of an existing task — the right move when you are breaking a "
                "big piece of work into steps, so the parent card shows real progress "
                "(\"1/3 done\") instead of the steps scattering across the board as "
                "unrelated cards."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short task title."},
                    "description": {"type": "string", "description": "What needs doing, and what does done look like."},
                    "due_at": {"type": "string", "description": "Optional ISO 8601 due date/time."},
                    "priority": _PROJECT_TASK_PRIORITY_SCHEMA,
                    "parent_task_id": _PROJECT_TASK_PARENT_SCHEMA,
                },
                "required": ["title"],
            },
        ),
        ToolDescriptor(
            tool_name="project_task__list",
            label="List project tasks",
            connector_id="project_task",
            action_id="list",
            description=(
                "List tasks on this project's board: tasks assigned to you, plus "
                "unassigned tasks anyone in the project can pick up. Use "
                "before starting new work to see what's already tracked. Every task "
                "comes back with its priority (0 = none, 1 = urgent, 2 = high, "
                "3 = medium, 4 = low — lower is more urgent) and a plain-English "
                "priority_label; pass sort='priority' to get the most urgent work "
                "first, which is how you decide what to pick up next."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "backlog", "todo", "in_progress", "awaiting_input",
                            "blocked", "in_review", "done",
                        ],
                        "description": "Optional status filter.",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["created_at", "priority"],
                        "description": (
                            "Ordering. 'priority' puts the most urgent work first and "
                            "untriaged (priority 0) work last; 'created_at' (the default) "
                            "puts the newest first."
                        ),
                    },
                },
                "required": [],
            },
        ),
        ToolDescriptor(
            tool_name="project_task__get",
            label="Get project task",
            connector_id="project_task",
            action_id="get",
            description=(
                "Get one task by id, including its comment history — must belong to your "
                "own project. Also returns the task's SUB-TASK ROLLUP (subtask_count and "
                "subtask_done_count — the \"1/3 done\" progress on the card), the full list "
                "of its sub-tasks, its parent_task_id if it is itself a sub-task, and the "
                "labels attached to it. Read this before reporting a task complete: a "
                "parent whose sub-tasks are not all done is not done."
            ),
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string", "description": "The task id."}},
                "required": ["task_id"],
            },
        ),
        ToolDescriptor(
            tool_name="project_task__set_parent",
            label="Set task parent",
            connector_id="project_task",
            action_id="set_parent",
            description=(
                "File an EXISTING task under another as a sub-task, or detach it back to "
                "top-level by omitting parent_task_id. Use when you realize a task you or "
                "somebody else already created is really a step of a bigger one. This board "
                "allows exactly ONE level of nesting: the parent must be a top-level task, "
                "and a task that already has sub-tasks of its own cannot become one. Both "
                "tasks must be in your project."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task to re-file."},
                    "parent_task_id": _PROJECT_TASK_PARENT_SCHEMA,
                },
                "required": ["task_id"],
            },
        ),
        ToolDescriptor(
            tool_name="project_task__update",
            label="Update project task",
            connector_id="project_task",
            action_id="update",
            description=(
                "Edit a task on your project's board — title, description, due date, priority, "
                "and/or status (backlog | todo | in_progress | awaiting_input | blocked | "
                "in_review | done). Call this with status='in_review' when you finish the work "
                "— that is how you hand it back for a human to check, and nothing else closes "
                "the loop for you. Only use status='done' for work that genuinely needs no "
                "human sign-off; when in doubt, 'in_review' is the right call. Use "
                "status='blocked' or 'awaiting_input' the moment you are stuck, so a human or "
                "teammate sees it on the board instead of the task silently going quiet. Set "
                "priority when you learn how urgent something really is — triaging the board "
                "is part of your job, not just the human's."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task id."},
                    "title": {"type": "string", "description": "New title, if changing it."},
                    "description": {"type": "string", "description": "New description, if changing it."},
                    "status": {
                        "type": "string",
                        "enum": [
                            "backlog", "todo", "in_progress", "awaiting_input",
                            "blocked", "in_review", "done",
                        ],
                        "description": (
                            "New status. Use 'in_review' when you have finished the work "
                            "and a human should check it before it is closed."
                        ),
                    },
                    "priority": _PROJECT_TASK_PRIORITY_SCHEMA,
                    "due_at": {"type": "string", "description": "New ISO 8601 due date/time."},
                    "clear_due_at": {"type": "boolean", "description": "Set true to remove the due date."},
                },
                "required": ["task_id"],
            },
        ),
        ToolDescriptor(
            tool_name="project_task__comment",
            label="Comment on project task",
            connector_id="project_task",
            action_id="comment",
            description=(
                "Post a progress note on a task — visible to the human owner and any other "
                "agent in the project who reads it afterward. Use for status updates that "
                "don't warrant a full field edit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task id."},
                    "body": {"type": "string", "description": "The comment text."},
                },
                "required": ["task_id", "body"],
            },
        ),
        ToolDescriptor(
            tool_name="project_task__assign",
            label="Assign project task",
            connector_id="project_task",
            action_id="assign",
            description=(
                "Hand a task on your project's board to an agent in the SAME project — "
                "yourself, to pick up backlog work, or a teammate, to delegate it. Wakes "
                "the target agent so it actually starts (subject to its own quiet-hours/"
                "device policy). Cannot assign across projects or to an agent outside "
                "this one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task id."},
                    "agent_id": {"type": "string", "description": "The agent install id to assign it to — must be in your project."},
                },
                "required": ["task_id", "agent_id"],
            },
        ),
        # Labels. The vocabulary is per-WORKSPACE (shared across every
        # project) while attach/detach is scoped to the calling agent's own
        # project like everything else in this namespace. An agent can READ
        # the vocabulary and put labels on/off its own tasks; it deliberately
        # cannot CREATE labels — that stays a human decision, because a
        # vocabulary any model can extend on a guessed word degrades into
        # "bug"/"Bugs"/"bugfix" within a week. project_task__list_labels
        # exists precisely so attaching is a choice from a real list rather
        # than a guess.
        ToolDescriptor(
            tool_name="project_task__list_labels",
            label="List workspace labels",
            connector_id="project_task",
            action_id="list_labels",
            description=(
                "List every label available in this workspace, with its colour and how "
                "many tasks currently carry it. Labels are shared across all projects in "
                "the workspace. Call this before project_task__add_label so you attach a "
                "label that actually exists — you cannot create new ones."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDescriptor(
            tool_name="project_task__add_label",
            label="Add label to task",
            connector_id="project_task",
            action_id="add_label",
            description=(
                "Attach an existing workspace label to a task on your project's board — "
                "how you categorize work so a human can filter for it later (e.g. tagging "
                "something you hit as 'bug'). Accepts the label's name or its id; names are "
                "matched case-insensitively. Already attached is not an error. You cannot "
                "create a new label this way — if the one you want does not exist, say so "
                "and ask an owner to add it. Use project_task__list_labels to see what "
                "exists."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task id."},
                    "label": {"type": "string", "description": "Label name (case-insensitive) or label id."},
                },
                "required": ["task_id", "label"],
            },
        ),
        ToolDescriptor(
            tool_name="project_task__remove_label",
            label="Remove label from task",
            connector_id="project_task",
            action_id="remove_label",
            description=(
                "Take a label off a task on your project's board. Removes only the link — "
                "the label itself stays in the workspace vocabulary for other tasks. "
                "Accepts the label's name or its id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task id."},
                    "label": {"type": "string", "description": "Label name (case-insensitive) or label id."},
                },
                "required": ["task_id", "label"],
            },
        ),
        # document__* (feat/document-agent-tools): the agent-facing side of a
        # project's owned markdown knowledge (project_documents_repository.py
        # -- "the owned-context layer for a team, with execution attached,"
        # CLAUDE.md). Same shape as project_task__* immediately above:
        # connector_id="document" is granted by PROJECT MEMBERSHIP, not a
        # connector binding (see agent_turn_runtime_service.py's
        # _PROJECT_SCOPED_CONNECTOR_IDS), every action below is scoped to the
        # CALLING agent's own project (resolved server-side from session
        # identity via project_tasks_service.agent_project_id -- the same
        # resolver project_task__* uses, since "which project is this agent
        # in" is one fact, not two), and every write stamps updated_by with
        # the acting agent's identity so the Documents UI can show who
        # changed what.
        #
        # document__edit is the one that matters: an exact-string,
        # unique-match replace modeled on Claude Code's own Edit tool
        # (server_modules/skills_service.py's connector_id == "document"
        # dispatch below does the actual uniqueness check) -- it is what lets
        # an agent change one line of a document instead of regenerating the
        # whole body, the founder's stated objection to Linear's own
        # agent-document story.
        ToolDescriptor(
            tool_name="document__list",
            label="List project documents",
            connector_id="document",
            action_id="list",
            description=(
                "List this project's documents: a table of contents (title, slug, "
                "who last touched it, when) -- NOT the bodies. Use this first to see "
                "what already exists before creating something that might duplicate "
                "it, or to find the slug of the document you want to read or edit. "
                "Call document__read on a specific slug to get its full content."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDescriptor(
            tool_name="document__read",
            label="Read project document",
            connector_id="document",
            action_id="read",
            description=(
                "Read one document's full markdown body, by path or id -- must belong "
                "to your own project. Provide whichever you have; path is what "
                "document__list returns and is the more common case. Read the current "
                "body before calling document__edit, since old_string must match the "
                "text EXACTLY as it stands right now."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The document's path, e.g. specs/api/auth.md (from document__list)."},
                    "id": {"type": "string", "description": "The document's id, if you already have it instead of a path."},
                },
                "required": [],
            },
        ),
        ToolDescriptor(
            tool_name="document__edit",
            label="Edit project document",
            connector_id="document",
            action_id="edit",
            description=(
                "Replace one exact passage in a document with new text -- a targeted, "
                "line-level edit, never a whole-body rewrite. old_string must appear "
                "in the document's CURRENT body EXACTLY ONCE: if it appears zero times "
                "(no match -- often a stale copy of the text, re-read the document with "
                "document__read first) or more than once (ambiguous -- include more "
                "surrounding context, e.g. a preceding heading or line, to make the "
                "match unique), this FAILS LOUDLY with an error and changes nothing. "
                "It never guesses which occurrence you meant and never falls back to a "
                "full rewrite. old_string and new_string must differ -- a no-op edit is "
                "rejected rather than silently 'succeeding'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The document's path, e.g. specs/api/auth.md (from document__list)."},
                    "old_string": {
                        "type": "string",
                        "description": (
                            "The exact text to replace. Must match the document's current "
                            "body character-for-character, and must occur EXACTLY ONCE."
                        ),
                    },
                    "new_string": {"type": "string", "description": "The text to replace it with."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        ),
        ToolDescriptor(
            tool_name="document__write",
            label="Create project document",
            connector_id="document",
            action_id="write",
            description=(
                "Create a NEW document in your project. CREATE ONLY -- this fails if a "
                "document already exists at the slug your title would produce (the "
                "error names the existing document); it never overwrites or appends to "
                "an existing one. To change an existing document, use document__edit "
                "(a targeted passage replace) instead -- if you truly need to replace "
                "the entire body, read it first with document__read, then use "
                "document__edit with the whole current body as old_string. Call "
                "document__list first if you are not sure whether this document "
                "already exists."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The document's title."},
                    "body": {"type": "string", "description": "Initial markdown body. Optional -- defaults to empty."},
                },
                "required": ["title"],
            },
        ),
        # goal__* (feat/agent-goals): "agent, go to this person and
        # negotiate ... and if the person says no, either try again, or
        # offer something different" (the founder's own framing). A goal is
        # a durable object -- distinct from a task -- that keeps waking its
        # agent on a bounded, backing-off cadence until it resolves (done),
        # is deliberately abandoned (cancelled), or its attempt/lifetime
        # budget runs out (exhausted -- see bounded_scheduler_service.py's
        # GOAL_STATUS_ORDER for the full vocabulary and why it extends
        # project_task's). Same shape as project_task__*/document__*
        # immediately above: connector_id="goal" is granted by PROJECT
        # MEMBERSHIP, not a connector binding (see
        # agent_turn_runtime_service.py's _PROJECT_SCOPED_CONNECTOR_IDS),
        # and every action is scoped to the CALLING agent's own project
        # (resolved server-side via project_tasks_service.agent_project_id,
        # the same resolver project_task__*/document__* use). goal__* is one
        # of the two families the machine-administration floor still reserves
        # to the owner (authority_mandate_service) — a goal SCHEDULES future
        # turns, which is our "cron".
        ToolDescriptor(
            tool_name="goal__create",
            label="Create goal",
            connector_id="goal",
            action_id="create",
            description=(
                "Create a durable goal for yourself (or a teammate in your project) and "
                "start working it immediately -- 'go negotiate with this supplier and come "
                "back with a result', not a one-off task. Unlike a task, a goal keeps waking "
                "its agent on a bounded, backing-off schedule (roughly hourly at first, "
                "slower over time) until it is resolved, is explicitly abandoned, or its "
                "attempt/lifetime budget runs out. Write `instruction` yourself -- this is "
                "the rule that shapes every retry, e.g. 'if rejected, offer a 10% discount "
                "instead; escalate to a human after 3 failed attempts.' Leaving it blank "
                "falls back to a generic default, which will not know your specific "
                "escalation rule."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal_text": {"type": "string", "description": "The outcome to work toward, in plain language."},
                    "title": {"type": "string", "description": "Short label. Defaults to the start of goal_text."},
                    "instruction": {
                        "type": "string",
                        "description": (
                            "How to adapt on each retry and when to stop -- e.g. 'try a "
                            "different offer if rejected; escalate after 3 attempts.' This is "
                            "injected into every turn you work this goal. Strongly recommended; "
                            "falls back to a generic default if omitted."
                        ),
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Who should work this goal -- an agent install id in your project. Defaults to yourself.",
                    },
                    "max_attempts": {
                        "type": "integer",
                        "description": "Maximum number of retry attempts before the goal stops itself (default 5, max 50).",
                    },
                    "lifetime_days": {
                        "type": "integer",
                        "description": "Maximum days this goal may stay active before it stops itself (default 14, max 90).",
                    },
                },
                "required": ["goal_text"],
            },
        ),
        ToolDescriptor(
            tool_name="goal__list",
            label="List project goals",
            connector_id="goal",
            action_id="list",
            description=(
                "List goals on your project: durable, retried-until-resolved outcomes, as "
                "opposed to project_task__list's one-shot task board. Each entry reports "
                "real attempt/status data -- how many attempts have actually run, out of "
                "the max, and why a finished goal stopped -- never a self-reported summary."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "todo", "in_progress", "awaiting_input", "blocked", "in_review",
                            "done", "cancelled", "exhausted",
                        ],
                        "description": "Optional status filter.",
                    },
                },
                "required": [],
            },
        ),
        ToolDescriptor(
            tool_name="goal__get",
            label="Get goal",
            connector_id="goal",
            action_id="get",
            description=(
                "Get one goal by id -- must belong to your own project. Returns its full "
                "instruction text, attempt_count/max_attempts, next_fire_at, and (once "
                "resolved) last_outcome_reason -- the honest record of what actually "
                "happened, not a narrative."
            ),
            parameters={
                "type": "object",
                "properties": {"goal_id": {"type": "string", "description": "The goal id."}},
                "required": ["goal_id"],
            },
        ),
        ToolDescriptor(
            tool_name="goal__update",
            label="Update goal",
            connector_id="goal",
            action_id="update",
            description=(
                "Update a goal on your project: its status, title, goal text, instruction, "
                "and/or a short note explaining why. Call this every time you learn "
                "something that changes the plan -- set status='blocked' or "
                "'awaiting_input' the moment you are stuck (a blocked goal KEEPS RETRYING "
                "on its normal schedule, so use these to signal what kind of stuck you "
                "are, not to pause it). Set status='done' once the outcome is achieved, or "
                "'cancelled' if you conclude it genuinely cannot be achieved -- both stop "
                "the goal for good, so always include `note` explaining the outcome. You "
                "cannot mark a goal 'exhausted' -- that status is reserved for when the "
                "system's own attempt/lifetime ceiling is hit without you resolving it "
                "either way."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string", "description": "The goal id."},
                    "status": {
                        "type": "string",
                        "enum": ["todo", "in_progress", "awaiting_input", "blocked", "in_review", "done", "cancelled"],
                        "description": "New status.",
                    },
                    "title": {"type": "string", "description": "New title, if changing it."},
                    "goal_text": {"type": "string", "description": "New goal text, if the outcome you're working toward changed."},
                    "instruction": {"type": "string", "description": "New escalation instruction, if you're refining it."},
                    "note": {"type": "string", "description": "A short note on why -- especially important for 'done'/'cancelled'."},
                },
                "required": ["goal_id"],
            },
        ),
        ToolDescriptor(
            tool_name="browser__navigate",
            label="Browser navigate",
            connector_id="browser",
            action_id="navigate",
            description="Render a URL in a headless browser (for JS-rendered pages curl can't read). Follow with browser__extract_text or browser__extract_dom to read its content.",
            capability_id="browser_automation.interactive",
            parameters={"type": "object", "properties": {"url": {"type": "string", "description": "The URL to open."}}, "required": ["url"]},
        ),
        ToolDescriptor(
            tool_name="browser__extract_text",
            label="Browser extract text",
            connector_id="browser",
            action_id="extract_text",
            description="Extract readable text from the current rendered page or a selected element.",
            capability_id="browser_automation.interactive",
            parameters={"type": "object", "properties": {"selector": {"type": "string"}}},
        ),
        ToolDescriptor(
            tool_name="browser__extract_dom",
            label="Browser extract DOM",
            connector_id="browser",
            action_id="extract_dom",
            description="Extract the rendered HTML of the current page or a selected element.",
            capability_id="browser_automation.interactive",
            parameters={"type": "object", "properties": {"selector": {"type": "string"}}},
        ),
        ToolDescriptor(
            tool_name="send_image",
            label="Send image",
            connector_id="messaging",
            action_id="send_image",
            description=(
                "Attach an image or file to your reply in the current messaging channel "
                "(Telegram, WhatsApp). Use this to share a screenshot, a generate_image "
                "output, a document, or any other local file — no need to publish it "
                "anywhere first. Note: generate_image already auto-attaches its own "
                "output when you're replying in a channel, so you only need this tool "
                "for a file that isn't already the direct result of generate_image "
                "(e.g. a screenshot, an existing document, or a public URL). "
                "path_or_url accepts either a local file path or a public URL. "
                "caption is optional text to send alongside it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path_or_url": {
                        "type": "string",
                        "description": "A local file path (e.g. generate_image's output path) or a public URL of the image/file to send.",
                    },
                    "caption": {"type": "string", "description": "Optional caption text for the image."},
                },
                "required": ["path_or_url"],
            },
        ),
        ToolDescriptor(
            tool_name="query_tool_registry",
            label="Query tool registry",
            connector_id="sage_service",
            action_id="query_tool_registry",
            description=(
                "Search for available tools and capabilities that are not in your "
                "default tool set. Call this when you need to perform an action "
                "(send email, manage calendar, control browser, edit files, etc.) "
                "but do not see the relevant tool in your available tools. "
                "Returns the 3-5 most relevant tools with their full schemas so "
                "you can call them in subsequent steps."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": (
                            "Describe what you need to do in plain language, e.g. "
                            "'send an email to client', 'search Gmail for invoices', "
                            "'create a calendar event', 'take a screenshot', "
                            "'read a file from the computer'. Be specific about the "
                            "service and action."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of tools to return (default 5, max 10).",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["task_description"],
            },
        ),
        # ── Fleet management tools (operator-only) ──────────────────────────
        ToolDescriptor(
            tool_name="fleet__create_agent",
            label="Create Agent",
            connector_id="fleet",
            action_id="create_agent",
            description=(
                "Create a new specialist agent in the workspace. "
                "Requires operator role. The new agent starts with the "
                "fleet-specialist definition and the given name, instructions, "
                "purpose preset, and capability preset."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Display name for the new agent (e.g. 'Support Bot')."},
                    "instructions": {"type": "string", "description": "Optional system instructions / persona for the agent."},
                    "purpose_preset": {
                        "type": "string",
                        "enum": ["customer_facing", "internal_assistant"],
                        "description": "What kind of work this agent is for (default: internal_assistant).",
                    },
                    "capability_preset": {
                        "type": "string",
                        "enum": ["knowledge", "standard"],
                        "description": "Capability tier: 'knowledge' for read-only research, 'standard' for full tools (default: standard).",
                    },
                },
                "required": ["name"],
            },
            risk_level="high",
            requires_approval=False,
        ),
        ToolDescriptor(
            tool_name="fleet__list_agents",
            label="List Agents",
            connector_id="fleet",
            action_id="list_agents",
            description="List all agents in the workspace with their roles, status, and project assignments.",
            parameters={
                "type": "object",
                "properties": {},
            },
            risk_level="low",
        ),
        ToolDescriptor(
            tool_name="fleet__get_agent_activity",
            label="Agent Activity",
            connector_id="fleet",
            action_id="get_agent_activity",
            description="Read recent ledger activity events for a specific agent.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "The agent install id to query activity for."},
                },
                "required": ["agent_id"],
            },
            risk_level="low",
        ),
        ToolDescriptor(
            tool_name="fleet__get_project_activity",
            label="Project Activity",
            connector_id="fleet",
            action_id="get_project_activity",
            description="Read recent ledger activity events for a specific project.",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "The project id to query activity for."},
                },
                "required": ["project_id"],
            },
            risk_level="low",
        ),
        ToolDescriptor(
            tool_name="fleet__configure_agent",
            label="Configure Agent",
            connector_id="fleet",
            action_id="configure_agent",
            description=(
                "Update an agent's configuration: connectors, channel bindings, "
                "hardware access, subagents toggle, model config, or instructions. "
                "There is no per-tool enable/disable switch — an agent has every "
                "tool the platform provides, gated only by a real connector "
                "binding or a resolved capability. Requires operator role."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "The agent install id to configure."},
                    "patch": {
                        "type": "object",
                        "description": (
                            "Fields to update. Supported keys: "
                            "connectors (list of connector ids), channel_bindings (object), "
                            "subagents_enabled (bool), hardware_access (none|gateway|vps|all), "
                            "instructions (string), model_config (object with mode/provider/model)."
                        ),
                    },
                },
                "required": ["agent_id", "patch"],
            },
            risk_level="high",
            requires_approval=False,
        ),
        ToolDescriptor(
            tool_name="fleet__schedule_task",
            label="Schedule Task",
            connector_id="fleet",
            action_id="schedule_task",
            description=(
                "Schedule ONE future wake-up so nobody has to re-prompt you — including for "
                "yourself. Omit agent_id to wake YOURSELF at the given time and run the "
                "instruction (a delayed follow-up, a one-off reminder). Pass a different "
                "agent's install id to schedule that agent instead (fleet management). "
                "'when' accepts 'in N minutes/hours' or an ISO-8601 datetime — NOT a cron "
                "expression. For a RECURRING schedule ('every morning at 9am', 'every "
                "Monday'), use fleet__schedule_recurring_task instead. Underlying mechanism: "
                "propose_self_wakeup — a self-scheduling primitive, not a queue you're "
                "borrowing for this."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": (
                            "The agent install id to wake and run this instruction. Omit this "
                            "field entirely to schedule YOURSELF instead of another agent."
                        ),
                    },
                    "when": {"type": "string", "description": "e.g. 'in 30 minutes', 'in 2 hours', or '2026-07-04T09:00:00Z'."},
                    "instruction": {"type": "string", "description": "What the agent should do when it wakes."},
                },
                "required": ["when", "instruction"],
            },
            risk_level="moderate",
            # Owner-only, permanently and by family rather than by flag:
            # scheduling future work is a standing instruction with no live
            # sender to re-check against when it executes. fleet__* is one of
            # the two prefixes authority_mandate_service reserves to the
            # owner — there is no per-agent opt-in that hands it to an end
            # customer any more, and there should not be one.
        ),
        ToolDescriptor(
            tool_name="fleet__schedule_recurring_task",
            label="Schedule Recurring Task",
            connector_id="fleet",
            action_id="schedule_recurring_task",
            description=(
                "Schedule a RECURRING wake-up — 'every morning at 9am', 'every Monday', "
                "'every 15 minutes' — for yourself (omit agent_id) or another agent "
                "(fleet management). 'cron' is a standard 5-field cron expression (minute "
                "hour day month weekday, e.g. '0 9 * * *' for 9am daily), evaluated in the "
                "WORKSPACE's configured timezone, not UTC. An invalid cron expression "
                "returns a clear error, never a silent no-op. Bounded by default: expires "
                "after 90 days unless max_occurrences or expires_at is set. Every fire "
                "still passes through the same quiet-hours and rate-cap policy as "
                "fleet__schedule_task — this cannot bypass those by firing more often. "
                "Use fleet__list_recurring_tasks / fleet__cancel_recurring_task to see or "
                "stop what's scheduled."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": (
                            "The agent install id to wake and run this instruction each time. "
                            "Omit this field entirely to schedule YOURSELF instead of another agent."
                        ),
                    },
                    "cron": {"type": "string", "description": "Standard 5-field cron expression, e.g. '0 9 * * *' for every morning at 9am."},
                    "instruction": {"type": "string", "description": "What the agent should do each time it wakes."},
                    "max_occurrences": {"type": "integer", "description": "Optional: stop after this many fires."},
                    "expires_at": {"type": "string", "description": "Optional ISO-8601 datetime: stop firing after this time. Defaults to 90 days out if neither this nor max_occurrences is set."},
                },
                "required": ["cron", "instruction"],
            },
            risk_level="moderate",
        ),
        ToolDescriptor(
            tool_name="fleet__list_recurring_tasks",
            label="List Recurring Tasks",
            connector_id="fleet",
            action_id="list_recurring_tasks",
            description="List active recurring schedules for yourself (omit agent_id) or another agent.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Omit to list your own recurring schedules."},
                },
                "required": [],
            },
            risk_level="low",
        ),
        ToolDescriptor(
            tool_name="fleet__cancel_recurring_task",
            label="Cancel Recurring Task",
            connector_id="fleet",
            action_id="cancel_recurring_task",
            description=(
                "Cancel a recurring schedule by id (from fleet__list_recurring_tasks) so it "
                "stops firing. A status change, not a delete — cancelled schedules keep "
                "their history."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Omit to cancel your own recurring schedule."},
                    "schedule_id": {"type": "string", "description": "The recurring schedule id to cancel."},
                },
                "required": ["schedule_id"],
            },
            risk_level="moderate",
        ),
        # ── Skills: Level-2 progressive disclosure (docs/design/audit-skills.md §3.4) ──
        # The unified skill catalog (skill_registry.list_skill_definitions,
        # rendered into the system prompt as name+description-only entries by
        # sage_skills_api._skill_capability_records) is Level 1. This tool is
        # the single Level-2 entry point every one of those entries points
        # at: the model never gets a per-skill tool, it gets one dispatcher
        # that loads/executes the named skill on demand — mirroring Claude
        # Code's "cat SKILL.md when the description matches" mechanic, just
        # implemented as a tool call instead of a filesystem read.
        ToolDescriptor(
            tool_name="skill_invoke",
            label="Invoke skill",
            connector_id="skill",
            action_id="invoke",
            description=(
                "Run a registered skill by id — the Level-2 step after the skill catalog's "
                "name+description listing (see the Callable Tools section for available "
                "skill_id values, e.g. 'memory-manager', 'code-runner', 'file-manager', "
                "'telegram-bot', 'vision-monitor', 'inventory-tool'). Loads that skill's full "
                "procedure and executes it; for a documentation-only skill with no live "
                "executor, returns its instructions as context instead of a fabricated result."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": (
                            "The id of the skill to run, exactly as shown in the skill "
                            "catalog listing (e.g. 'memory-manager', 'code-runner')."
                        ),
                    },
                    "args": {
                        "anyOf": [{"type": "string"}, {"type": "object"}],
                        "description": (
                            "Optional arguments for the skill: either a free-text goal string "
                            "describing what to do, or an object with a 'goal' key. Omit for "
                            "skills that don't need input (e.g. a memory snapshot)."
                        ),
                    },
                },
                "required": ["skill_id"],
            },
            risk_level="high",
        ),
        ToolDescriptor(
            tool_name="skill_write",
            label="Author skill",
            connector_id="skill",
            action_id="write",
            description=(
                "Author or update a workspace skill from a name, description, and Markdown "
                "procedure body. Use when the user asks you to save a repeated procedure as a "
                "reusable skill, or to codify a pattern you just used. The skill is security-"
                "scanned and installed for real, but starts DISABLED, pending the workspace "
                "owner's review — it will not appear in the skill catalog or be callable via "
                "skill_invoke until the owner reviews and enables it. Do not tell the user the "
                "skill is 'ready' or 'active' — say it is saved and awaiting their review."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Human-readable skill name; becomes the skill id (lowercased, hyphenated).",
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "Third-person description of what the skill does and when to use "
                            "it. This is the only text shown to the agent before the skill is "
                            "invoked (Level 1) — be specific."
                        ),
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "The Markdown procedure body: what this skill does, the numbered "
                            "steps to follow when it's invoked, when NOT to use it, and any "
                            "safety notes."
                        ),
                    },
                    "skill_class": {
                        "type": "string",
                        "enum": ["business", "specialist_local"],
                        "description": "Defaults to 'business'. Use 'specialist_local' for a skill scoped to one specialist agent.",
                    },
                    "connector_scopes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional connector ids this skill touches (e.g. 'crm', 'email').",
                    },
                    "trigger_terms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional trigger phrases that suggest this skill applies.",
                    },
                },
                "required": ["name", "description", "body"],
            },
            risk_level="medium",
        ),
    ]


def build_local_direct_chat_tools(
    availability: Dict[str, Any],
    *,
    local_worker_available: Any,
) -> List[Dict[str, Any]]:
    if not local_worker_available(availability):
        return []
    return [_tool_payload_from_descriptor(item) for item in _local_tool_descriptors()]


def _email_send_tool_parameters(*, with_cc: bool) -> Dict[str, Any]:
    """Shared to/subject/body(/cc) schema for send_email/draft_email-shaped
    actions. Field names match exactly what build_direct_tool_config /
    runs_execution._workflow_execute_connector_action read off the resulting
    config dict (to_email/to, subject, body/text) — see the connector_id ==
    "google_workspace" / "microsoft_365" / "smtp" branches below and in
    runs_execution.py's shared send_email/draft_email handler."""
    properties: Dict[str, Any] = {
        "to": {
            "type": "string",
            "description": "Recipient email address, e.g. 'jane@example.com'.",
        },
        "subject": {
            "type": "string",
            "description": "Email subject line.",
        },
        "body": {
            "type": "string",
            "description": "Plain-text email body.",
        },
    }
    required = ["to", "subject", "body"]
    if with_cc:
        properties["cc"] = {
            "type": "string",
            "description": "Optional comma-separated list of additional email addresses to CC.",
        }
    return {"type": "object", "properties": properties, "required": required}


def _calendar_event_tool_parameters() -> Dict[str, Any]:
    """Field names match config keys read by build_direct_tool_config's
    create_calendar_event branch and runs_execution.py's shared
    {google_workspace, microsoft_365} create_calendar_event handler."""
    return {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Event title/summary.",
            },
            "start": {
                "type": "string",
                "description": (
                    "Event start time as an ISO 8601 datetime, e.g. "
                    "'2026-07-25T14:00:00-07:00'."
                ),
            },
            "end": {
                "type": "string",
                "description": "Event end time, same ISO 8601 format as start.",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of attendee email addresses to invite.",
            },
            "description": {
                "type": "string",
                "description": "Optional event description/notes.",
            },
            "timezone": {
                "type": "string",
                "description": (
                    "IANA timezone for start/end, e.g. 'America/Los_Angeles'. "
                    "Defaults to UTC if omitted."
                ),
            },
            "calendar_id": {
                "type": "string",
                "description": "Which calendar to create the event on. Defaults to the primary calendar.",
            },
        },
        "required": ["title", "start", "end"],
    }


# Real per-connector-action JSON schemas for the highest-traffic dynamically
# generated connector tools (build_direct_chat_tools, below). Before this,
# every one of these tools got one opaque `{"input": string}` param and a
# description of `f"Execute {action} on {label}"` — the model had to guess a
# free-text blob, and build_direct_tool_config's regex heuristics guessed
# back at what it meant (only wired for 5 connectors). Connectors/actions not
# in this map keep that legacy shape (build_direct_tool_config's tool_input
# string-parsing path is kept as the fallback for exactly that reason).
#
# Field names were chosen by reading what build_direct_tool_config /
# runs_execution._workflow_execute_connector_action actually consume for
# each action — not invented — so the schema describes real executor inputs.
def _structured_connector_tool_schema(
    connector_id: str,
    action_id: str,
    label: str,
) -> Optional[Dict[str, Any]]:
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip()
    display_label = str(label or normalized_connector).strip() or normalized_connector

    if normalized_connector in {"google_workspace", "microsoft_365"} and normalized_action == "send_email":
        return {
            "description": (
                f"Send an email immediately through the connected {display_label} account. "
                "Use this when the user asks you to email someone right now — e.g. "
                "'email Sarah the report' or 'send a note to support@acme.com'. Requires a "
                "recipient (to), a subject, and a plain-text body; add cc for additional "
                "recipients who should be copied. Use the draft_email action instead if the "
                "user wants the message prepared for their own review before sending, and do "
                "not use this to search or read existing mail."
            ),
            "parameters": _email_send_tool_parameters(with_cc=True),
        }
    if normalized_connector in {"google_workspace", "microsoft_365"} and normalized_action == "draft_email":
        return {
            "description": (
                f"Create a draft email in the connected {display_label} account without "
                "sending it. Use this when the user wants an email prepared for their own "
                "review and later send — e.g. 'draft a reply to this' or 'write up an email "
                "but don't send it yet'. Takes the same to/subject/body/cc fields as the "
                "send_email action, but the message is only saved as a draft, never "
                "delivered. Use send_email instead if the user actually wants it sent now."
            ),
            "parameters": _email_send_tool_parameters(with_cc=True),
        }
    if normalized_connector in {"google_workspace", "microsoft_365"} and normalized_action == "create_calendar_event":
        return {
            "description": (
                f"Create a new event on the connected {display_label} calendar. Use this "
                "when the user asks to schedule a meeting, block time, or add something to "
                "their calendar — e.g. 'schedule a call with the client tomorrow at 2pm' or "
                "'put a reminder on my calendar for Friday'. Requires a title and ISO 8601 "
                "start/end times; optionally add attendees (their emails, to send them an "
                "invite), a description, a timezone (defaults to UTC), and a calendar_id "
                "(defaults to the primary calendar). This only creates events — it does not "
                "check what's already scheduled."
            ),
            "parameters": _calendar_event_tool_parameters(),
        }
    if normalized_connector == "smtp" and normalized_action == "send_email":
        return {
            "description": (
                "Send an email through the connected SMTP mail account. Use this for "
                "workspaces where email is configured via raw SMTP credentials rather than "
                "Google Workspace or Microsoft 365 — e.g. 'email the customer at their "
                "support address' or 'notify them by email'. Requires a recipient (to), a "
                "subject, and a plain-text body. If a Google Workspace or Microsoft 365 "
                "connector is also available, prefer whichever one the user's account is "
                "actually set up on."
            ),
            "parameters": _email_send_tool_parameters(with_cc=False),
        }
    if normalized_connector == "slack" and normalized_action == "send_message":
        return {
            "description": (
                "Post a message to a Slack channel through the connected Slack workspace. "
                "Use this when the user asks you to tell the team, post an update, or notify "
                "a channel — e.g. 'let the team know on Slack' or 'post this in #general'. "
                "Requires the target channel (an ID like 'C0123456789' or a name like "
                "'#general') and the message text. Use send_dm instead if the message is for "
                "one specific person rather than a channel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": (
                            "Slack channel to post to — a channel ID (e.g. 'C0123456789') or "
                            "name (e.g. '#general' or 'general')."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to post.",
                    },
                },
                "required": ["channel", "text"],
            },
        }
    if normalized_connector == "slack" and normalized_action == "send_dm":
        return {
            "description": (
                "Send a direct message to a single Slack user through the connected Slack "
                "workspace. Use this when the user asks you to message someone privately on "
                "Slack rather than post to a channel — e.g. 'DM Alex about the deadline'. "
                "Requires the recipient's Slack user_id (e.g. 'U0123456789') and the message "
                "text. Use send_message instead if the message should go to a channel "
                "multiple people can see."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Slack user ID of the DM recipient, e.g. 'U0123456789'.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to send.",
                    },
                },
                "required": ["user_id", "text"],
            },
        }
    if normalized_connector == "telegram_bot" and normalized_action == "send_message":
        return {
            "description": (
                "Send a message through the connected Telegram bot. Use this when the user "
                "asks you to message someone on Telegram or reply in a Telegram conversation "
                "— e.g. 'tell them on Telegram' or 'send a Telegram reminder'. Requires the "
                "message text; chat_id is optional and only needed to target a specific chat "
                "other than the one this conversation is already bound to. Use the matching "
                "connector's send_message tool instead for Slack, Discord, or email."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": (
                            "Telegram chat ID to send to. Optional — omit to reply in the "
                            "chat this conversation is already bound to."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to send.",
                    },
                },
                "required": ["text"],
            },
        }
    if normalized_connector == "discord_bot" and normalized_action == "send_message":
        return {
            "description": (
                "Post a message to a Discord channel through the connected Discord bot. Use "
                "this when the user asks you to post an update or notify people in a Discord "
                "server — e.g. 'drop this in the #announcements channel'. Requires the "
                "target channel_id (the numeric Discord channel ID, not a channel name) and "
                "the message text. Use a different discord_bot action for DMs, embeds, or "
                "reactions — this tool only sends plain channel messages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": (
                            "Discord channel ID (snowflake) to post in, e.g. "
                            "'123456789012345678'."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to post.",
                    },
                },
                "required": ["channel_id", "text"],
            },
        }
    if normalized_connector == "whatsapp_twilio" and normalized_action == "send_message":
        return {
            "description": (
                "Send a WhatsApp message through the connected Twilio WhatsApp number. Use "
                "this when the user asks you to message someone on WhatsApp — e.g. 'text "
                "them on WhatsApp about the delay'. Requires the message text; to_number "
                "(E.164 format, e.g. '+14155551234') is optional only if this connector has "
                "a single default recipient configured — otherwise supply it explicitly. Use "
                "a different connector's send_message tool for SMS, Slack, or Telegram."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to_number": {
                        "type": "string",
                        "description": (
                            "Recipient WhatsApp phone number in E.164 format, e.g. "
                            "'+14155551234'. Optional if this connector has a default "
                            "recipient configured."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to send.",
                    },
                },
                "required": ["text"],
            },
        }
    return None


def build_direct_chat_tools(tool_capabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for cap in tool_capabilities:
        if not isinstance(cap, dict):
            continue
        if capability_payload_runtime_usable(cap) is not True:
            continue
        connector_id = str(cap.get("id") or "").strip().lower()
        label = str(cap.get("label") or connector_id).strip() or connector_id
        if not connector_id:
            continue
        for action in capability_payload_write_actions(cap):
            tool_name = tool_name_for_action(connector_id, action)
            if not tool_name or tool_name in seen:
                continue
            seen.add(tool_name)
            capability_id = workflow_tool_capability_id(
                "connector_action",
                {
                    "connector": connector_id,
                    "action_id": action,
                },
            )
            contract = resolve_capability(capability_id)
            risk_level = contract.risk_level if contract is not None else str(cap.get("risk_level") or "medium").strip() or "medium"
            requires_approval = (
                bool(contract.requires_approval)
                if contract is not None
                else capability_payload_requires_approval_for_action(cap, action)
            )
            permission_manifest = _permission_manifest_for_tool(
                connector_id=connector_id,
                action_id=action,
                capability_id=capability_id,
                extra_scopes=[f"{connector_id}:{action}"],
                risk_level=risk_level,
                requires_approval=requires_approval,
                requires_runtime=False,
                contract=contract,
            )
            structured_schema = _structured_connector_tool_schema(connector_id, action, label)
            if structured_schema is not None:
                description = structured_schema["description"]
                parameters = structured_schema["parameters"]
            else:
                # Legacy fallback for actions not yet given a real schema —
                # the model gets one opaque `input` string and
                # build_direct_tool_config regex-guesses at its meaning.
                description = f"Execute {action} on {label}"
                parameters = {
                    "type": "object",
                    "properties": {"input": {"type": "string", "description": "The input for this action"}},
                    "required": ["input"],
                }
            tools.append(
                {
                    "name": tool_name,
                    "description": description,
                    "label": f"{label} {action.replace('_', ' ')}",
                    "connector_id": connector_id,
                    "action_id": action,
                    "capability_id": capability_id,
                    "risk_level": risk_level,
                    "requires_approval": requires_approval,
                    "action_class": permission_manifest["action_class"],
                    "allowed_runtime_modes": permission_manifest["allowed_runtime_modes"],
                    "cost_class": permission_manifest["cost_class"],
                    "audit_event_type": permission_manifest["audit_event_type"],
                    "permission_manifest": permission_manifest,
                    "parameters": parameters,
                }
            )
    return tools


def build_builtin_direct_chat_tools() -> List[Dict[str, Any]]:
    tools = [_tool_payload_from_descriptor(item) for item in _builtin_tool_descriptors()]
    # ARCHIVED (Phase U1): agent machine mode supervisor tool injection removed.
    # The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.
    # Local hardware tools (desktop control) are OUT of scope.
    return tools


def registered_direct_chat_tool_names_for_logging() -> List[str]:
    tool_names = {
        str(item.get("name") or "").strip()
        for item in (
            build_builtin_direct_chat_tools()
            + build_local_direct_chat_tools({"runtime_ok": True}, local_worker_available=lambda availability: True)
        )
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    return sorted(tool_names)


def extract_first_email(text: str) -> str:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", str(text or ""), flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def extract_subject_text(text: str) -> str:
    raw = str(text or "").strip()
    for pattern in (
        r"subject\s*[:=]\s*([^\n]+)",
        r"subject\s+(.+?)(?:(?:\s+(?:body|message|content)\s*:?)|$)",
    ):
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return str(match.group(1) or "").strip(" \"'")
    return ""


def extract_body_text(text: str) -> str:
    raw = str(text or "").strip()
    for pattern in (r"(?:body|message|content|saying)\s*[:=]?\s+(.+)$",):
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if match:
            body = str(match.group(1) or "").strip()
            if body:
                return body
    return raw


def first_non_empty_line(text: str) -> str:
    for line in str(text or "").splitlines():
        token = line.strip()
        if token:
            return token
    return ""


def tool_descriptor_for_name(tool_name: str) -> ToolDescriptor | None:
    """Look up a local/builtin ToolDescriptor by its literal tool-call name —
    the same canonical enforcement id skill_registry.enforcement_tool_name
    produces. For callers outside the dispatch path (e.g.
    fleet_get_agent_tools) that need manifest fields without executing
    anything. Returns None for connector/MCP actions, which have no
    ToolDescriptor at all — only local/builtin tools do."""
    clean_name = str(tool_name or "").strip()
    if not clean_name:
        return None
    for descriptor in list(_local_tool_descriptors()) + list(_builtin_tool_descriptors()):
        if descriptor.tool_name == clean_name:
            return descriptor
    return None


def _authority_mandate_gate(
    connector_id: str,
    action_id: str,
    session_ctx: Dict[str, Any] | None,
    tool_name: str = "",
) -> tuple[bool, Optional[str], bool]:
    """Is this tool call inside the caller's authority?

    2026-08-21: this is now the ONLY tool-authority enforcement on this path.
    The visibility filter that used to run ahead of it (audience_tool_filter,
    which stripped every non-audience_safe tool from a non-owner's tool list
    mid-turn) is deleted, and so is the per-agent Tools tab that handed tools
    back one at a time. The rule this gate applies is
    authority_mandate_service.is_tool_call_allowed — allow everything except
    the named machine-administration set (fleet__*, goal__*,
    empyralis_configure_agent). See that module's docstring for the founder
    decision and for the consequence, which is real and is not softened here:
    anyone who can message an agent can now make it do anything that agent
    can do.

    The gate is kept — rather than deleted with the tier — because it is the
    only place that can hold when a tool reaches the model some other way (a
    stale prompt, a durable run resuming outside the turn that built its tool
    list). A boundary that exists only in the tool list is not a boundary.

    FAIL-CLOSED on the TIER: a session_ctx with no "authority_tier" key is
    treated as audience, not owner — normalize_tier(None) already fails safe
    the same way, so this just stops short-circuiting before that fail-safe
    engages. Note that failing closed on the tier now costs a caller only the
    machine-administration family, not its whole toolset.

    Returns (allowed, tier, unattributed). unattributed=True means the key
    was absent — the tier shown is the audience default, not something the
    caller actually declared. Callers ledger this (mandate_unattributed) as a
    distinct, non-blocking observability signal from an actual mandate_blocked
    event, so a producer that still isn't stamping a tier stays visible
    instead of silently defaulting forever.
    """
    session_metadata = session_ctx if isinstance(session_ctx, dict) else {}
    unattributed = "authority_tier" not in session_metadata
    tier = authority_mandate_service.normalize_tier(session_metadata.get("authority_tier"))
    allowed = authority_mandate_service.is_tool_call_allowed(
        tier,
        tool_name=tool_name,
        connector_id=connector_id,
        action_id=action_id,
    )
    return allowed, tier, unattributed


def _authority_mandate_blocked_ledger_kwargs(
    *,
    connector_id: str,
    action_id: str,
    tool_name: str,
    tier: str,
    workspace_id: str,
    thread_id: str,
    session_ctx: Dict[str, Any] | None,
) -> Dict[str, Any]:
    session_metadata = session_ctx if isinstance(session_ctx, dict) else {}
    return dict(
        tenant_id=_tenant_id_from_direct_tool_context(session_ctx),
        workspace_id=str(workspace_id or "default").strip() or "default",
        actor_type="agent",
        actor_id=str(session_metadata.get("agent_id") or session_metadata.get("user_id") or "sage").strip() or "sage",
        event_class=authority_mandate_service.MANDATE_BLOCKED_EVENT_CLASS,
        detail_level="audit_reference",
        action="mandate_blocked",
        thread_id=str(thread_id or "").strip() or None,
        title=f"Blocked: {tool_name or connector_id} requires the workspace owner",
        summary=(
            f"Tier '{tier}' attempted '{tool_name or f'{connector_id}.{action_id}'}', "
            "which is machine administration and is reserved to the workspace "
            "owner. Blocked at the execution choke point."
        ),
        status="blocked",
        metadata={
            "connector_id": connector_id or None,
            "action_id": action_id or None,
            "tool_name": tool_name or None,
            "authority_tier": tier,
            "owner_only": True,
        },
    )


def _authority_mandate_unattributed_ledger_kwargs(
    *,
    connector_id: str,
    action_id: str,
    tool_name: str,
    workspace_id: str,
    thread_id: str,
    session_ctx: Dict[str, Any] | None,
) -> Dict[str, Any]:
    session_metadata = session_ctx if isinstance(session_ctx, dict) else {}
    return dict(
        tenant_id=_tenant_id_from_direct_tool_context(session_ctx),
        workspace_id=str(workspace_id or "default").strip() or "default",
        actor_type="agent",
        actor_id=str(session_metadata.get("agent_id") or session_metadata.get("user_id") or "sage").strip() or "sage",
        event_class=authority_mandate_service.MANDATE_UNATTRIBUTED_EVENT_CLASS,
        detail_level="audit_reference",
        action="tool_call_tier_unattributed",
        thread_id=str(thread_id or "").strip() or None,
        title=f"Tool call reached the mandate gate with no authority_tier: {tool_name or connector_id}",
        summary=(
            f"'{tool_name or f'{connector_id}.{action_id}'}' reached the mandate gate with no authority_tier "
            "stamped on session_ctx. Defaulted to audience (fail-closed)."
        ),
        status="logged",
        metadata={
            "connector_id": connector_id or None,
            "action_id": action_id or None,
            "tool_name": tool_name or None,
        },
    )


def _approval_path_tool_descriptors() -> List[ToolDescriptor]:
    return _local_tool_descriptors() + [
        descriptor
        for descriptor in _builtin_tool_descriptors()
        if descriptor.tool_name == "http_request"
    ]


def tool_descriptor_for_action(
    connector_id: str,
    action_id: str,
    *,
    include_builtin: bool = True,
) -> ToolDescriptor | None:
    tool_name = tool_name_for_action(connector_id, action_id)
    if not tool_name:
        return None
    descriptor_sets: List[ToolDescriptor] = list(_local_tool_descriptors())
    if include_builtin:
        descriptor_sets.extend(_builtin_tool_descriptors())
    for descriptor in descriptor_sets:
        if descriptor.tool_name == tool_name:
            return descriptor
    return None


def approval_path_tool_descriptor_for_action(
    connector_id: str,
    action_id: str,
) -> ToolDescriptor | None:
    tool_name = tool_name_for_action(connector_id, action_id)
    if not tool_name:
        return None
    for descriptor in _approval_path_tool_descriptors():
        if descriptor.tool_name == tool_name:
            return descriptor
    return None


def tool_name_for_action(connector_id: str, action_id: str) -> str:
    normalized_connector_id = str(connector_id or "").strip().lower()
    normalized_action_id = str(action_id or "").strip()
    if normalized_connector_id == "http" and normalized_action_id == "request":
        return "http_request"
    if not normalized_connector_id or not normalized_action_id:
        return ""
    return f"{normalized_connector_id}__{normalized_action_id}"


def capability_action_metadata(
    tool_capabilities: List[Dict[str, Any]],
    connector_id: str,
    action_id: str,
) -> Dict[str, Any]:
    availability = {"tool_capabilities": tool_capabilities}
    normalized_connector_id = str(connector_id or "").strip().lower()
    normalized_action_id = str(action_id or "").strip()
    item = availability_capability(availability, normalized_connector_id)
    return {
        "capability": dict(item) if isinstance(item, dict) else None,
        "connected": capability_payload_connected(item),
        "runtime_usable": capability_payload_runtime_usable(item),
        "supports_write_action": availability_capability_supports_write_action(
            availability,
            normalized_connector_id,
            normalized_action_id,
        ),
        "requires_approval": availability_capability_requires_approval_for_action(
            availability,
            normalized_connector_id,
            normalized_action_id,
        ),
    }


def tool_write_action_available(
    connector_id: str,
    action_id: str,
    tool_capabilities: List[Dict[str, Any]],
) -> bool:
    normalized_connector_id = str(connector_id or "").strip().lower()
    normalized_action_id = str(action_id or "").strip()
    tool_name = tool_name_for_action(normalized_connector_id, normalized_action_id)
    if tool_name and approval_path_tool_descriptor_for_action(normalized_connector_id, normalized_action_id):
        return True
    return capability_action_metadata(tool_capabilities, normalized_connector_id, normalized_action_id).get(
        "supports_write_action",
        False,
    )


def tool_action_requires_approval(
    connector_id: str,
    action_id: str,
    tool_capabilities: List[Dict[str, Any]],
) -> bool:
    return bool(
        capability_action_metadata(tool_capabilities, connector_id, action_id).get(
            "requires_approval",
            False,
        )
    )


def approved_action_to_tool_call(
    approved_action: Dict[str, str],
    *,
    parse_json_object_loose: Any,
) -> Dict[str, Any]:
    connector_id = str(approved_action.get("connector") or "").strip().lower()
    raw_input = str(approved_action.get("input") or "").strip()
    if approval_path_tool_descriptor_for_action(connector_id, approved_action.get("action") or ""):
        parsed_input = parse_json_object_loose(raw_input)
        arguments = (
            parsed_input
            if isinstance(parsed_input, dict)
            else ({} if connector_id == "screenshot" else {"input": raw_input})
        )
    else:
        arguments = {"input": raw_input}
    return {
        "name": tool_name_for_action(approved_action.get("connector") or "", approved_action.get("action") or ""),
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def _normalize_attendee_emails(value: Any) -> List[str]:
    """Normalize a calendar event's `attendees` field into a flat list of
    email strings. Accepts a list of email strings, a list of
    {"email": ...} dicts (matching Google Calendar/Graph attendee objects),
    or a single comma/semicolon-separated string — whatever shape a
    structured tool call or a loosely-parsed input blob happens to supply."""
    candidates: List[Any]
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, str):
        candidates = re.split(r"[,;]", value)
    else:
        return []
    emails: List[str] = []
    for item in candidates:
        if isinstance(item, dict):
            email = str(item.get("email") or item.get("address") or "").strip()
        else:
            email = str(item or "").strip()
        if email and email not in emails:
            emails.append(email)
    return emails


def build_direct_tool_config(
    connector_id: str,
    action_id: str,
    tool_input: str,
    *,
    parse_json_object_loose: Any,
    structured_args: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the executor-facing config dict for a connector-action tool call.

    `tool_input` is the legacy free-text/JSON-blob path: a single string the
    model put everything into, which gets loosely parsed and then
    regex-guessed apart (extract_first_email/extract_subject_text/
    extract_body_text) when a field can't be found by name.

    `structured_args`, when provided (non-empty dict), is the model's ACTUAL
    named tool-call arguments from one of _structured_connector_tool_schema's
    real per-connector-action schemas (build_direct_chat_tools, above) — e.g.
    {"to": "...", "subject": "...", "body": "..."} instead of
    {"input": "<blob the model had to compose>"}. When present it is used
    DIRECTLY as parsed_input below, so every branch's `parsed_input.get(...)`
    lookups resolve from the real structured fields first and the
    tool_input-based regex fallbacks (extract_first_email et al.) never run
    for these calls at all. Callers that still only pass a tool_input string
    (unconverted actions, and the human-approval-confirmation path, which
    only ever has a free-text "input") get identical behavior to before.
    """
    config: Dict[str, Any] = {
        "connector": connector_id,
        "action_id": action_id,
    }
    if isinstance(structured_args, dict) and structured_args:
        parsed_input = structured_args
    else:
        parsed_input = parse_json_object_loose(tool_input) or {}

    if connector_id == "telegram_bot":
        for key in ("chat_id", "session_key"):
            value = str(parsed_input.get(key) or "").strip()
            if value:
                config[key] = value
        config["text"] = str(
            parsed_input.get("text")
            or parsed_input.get("body")
            or parsed_input.get("message")
            or parsed_input.get("content")
            or tool_input
        ).strip()
        return config

    if connector_id == "slack":
        for key in ("channel", "channel_id", "user_id", "recipient_id", "thread_ts", "title", "file_path", "path"):
            value = str(parsed_input.get(key) or "").strip()
            if value:
                config[key] = value
        if action_id in {"send_message", "send_dm", "post_reply"}:
            config["text"] = str(
                parsed_input.get("text")
                or parsed_input.get("body")
                or parsed_input.get("message")
                or parsed_input.get("content")
                or tool_input
            ).strip()
        if action_id in {"list_channels", "get_history"}:
            try:
                limit = int(parsed_input.get("limit") or 20)
            except Exception:
                limit = 20
            config["limit"] = max(1, min(limit, 200))
        return config

    if connector_id == "discord_bot":
        for key in ("channel_id", "guild_id", "user_id", "message_id", "emoji", "name", "title", "file_path", "path"):
            value = str(parsed_input.get(key) or "").strip()
            if value:
                config[key] = value
        files = parsed_input.get("files")
        if isinstance(files, list) and files:
            config["files"] = files
        embeds = parsed_input.get("embeds")
        if isinstance(embeds, list) and embeds:
            config["embeds"] = embeds
        if action_id in {"send_message", "send_dm", "edit_message", "send_embed"}:
            config["text"] = str(
                parsed_input.get("text")
                or parsed_input.get("body")
                or parsed_input.get("message")
                or parsed_input.get("content")
                or tool_input
            ).strip()
        if action_id in {"list_guilds", "list_members", "get_message_history"}:
            try:
                limit = int(parsed_input.get("limit") or 20)
            except Exception:
                limit = 20
            config["limit"] = max(1, min(limit, 100))
        return config

    if connector_id == "whatsapp_twilio" and action_id == "send_message":
        to_number = str(
            parsed_input.get("to_number")
            or parsed_input.get("recipient")
            or parsed_input.get("to")
            or ""
        ).strip()
        if to_number:
            config["to_number"] = to_number
        from_number = str(parsed_input.get("from_number") or "").strip()
        if from_number:
            config["from_number"] = from_number
        config["text"] = str(
            parsed_input.get("text")
            or parsed_input.get("body")
            or parsed_input.get("message")
            or parsed_input.get("content")
            or tool_input
        ).strip()
        return config

    if connector_id == "smtp" and action_id in {"send_email", "send_message"}:
        to_email = str(
            parsed_input.get("to_email")
            or parsed_input.get("to")
            or parsed_input.get("email")
            or parsed_input.get("recipient")
            or extract_first_email(tool_input)
            or ""
        ).strip()
        subject = str(parsed_input.get("subject") or extract_subject_text(tool_input) or "").strip()
        body_text = str(
            parsed_input.get("body")
            or parsed_input.get("text")
            or parsed_input.get("message")
            or parsed_input.get("content")
            or extract_body_text(tool_input)
            or ""
        ).strip()
        if to_email:
            config["to_email"] = to_email
        if subject:
            config["subject"] = subject
        if body_text:
            config["text"] = body_text
        return config

    if connector_id == "smtp" and action_id == "fetch_emails":
        folder = str(parsed_input.get("folder") or "INBOX").strip() or "INBOX"
        try:
            limit = int(parsed_input.get("limit") or 10)
        except Exception:
            limit = 10
        config["folder"] = folder
        config["limit"] = max(1, min(limit, 50))
        if parsed_input.get("unread_only") is not None:
            config["unread_only"] = bool(parsed_input.get("unread_only"))
        return config

    # google_workspace and microsoft_365 share one executor handler for
    # send_email/draft_email and create_calendar_event
    # (runs_execution._workflow_execute_connector_action checks
    # `connector_id in {"google_workspace", "microsoft_365"}` for both) — mirror
    # that here. Previously only google_workspace was handled and
    # microsoft_365 fell through to the generic `config["text"] = tool_input`
    # catch-all at the bottom of this function, which the shared executor
    # handler can't extract a recipient/subject from at all.
    if connector_id in {"google_workspace", "microsoft_365"} and action_id in {"send_email", "send_message", "draft_email"}:
        to_email = str(
            parsed_input.get("to_email")
            or parsed_input.get("to")
            or parsed_input.get("email")
            or parsed_input.get("recipient")
            or extract_first_email(tool_input)
            or ""
        ).strip()
        subject = str(parsed_input.get("subject") or extract_subject_text(tool_input) or "").strip()
        body_text = str(
            parsed_input.get("body")
            or parsed_input.get("text")
            or parsed_input.get("message")
            or parsed_input.get("content")
            or extract_body_text(tool_input)
            or ""
        ).strip()
        cc_email = str(parsed_input.get("cc_email") or parsed_input.get("cc") or "").strip()
        if to_email:
            config["to_email"] = to_email
        if subject:
            config["subject"] = subject
        if body_text:
            config["text"] = body_text
        if cc_email:
            config["cc_email"] = cc_email
        return config

    if connector_id == "google_workspace" and action_id == "fetch_emails":
        try:
            limit = int(parsed_input.get("limit") or 10)
        except Exception:
            limit = 10
        config["limit"] = max(1, min(limit, 10))
        return config

    if connector_id == "google_workspace" and action_id == "list_calendar_events":
        try:
            top = int(parsed_input.get("top") or parsed_input.get("limit") or 10)
        except Exception:
            top = 10
        config["top"] = max(1, min(top, 20))
        for key in ("calendar_id", "time_min", "time_max"):
            value = parsed_input.get(key)
            if value is None:
                continue
            token = str(value).strip()
            if token:
                config[key] = token
        return config

    if connector_id == "google_workspace" and action_id == "list_drive_files":
        try:
            top = int(parsed_input.get("top") or parsed_input.get("limit") or 20)
        except Exception:
            top = 20
        config["top"] = max(1, min(top, 50))
        path = str(parsed_input.get("path") or parsed_input.get("folder") or "gdrive:/").strip() or "gdrive:/"
        config["path"] = path
        return config

    if connector_id in {"google_workspace", "microsoft_365"} and action_id == "create_calendar_event":
        payload = parsed_input.get("payload") if isinstance(parsed_input.get("payload"), dict) else None
        if payload:
            config["payload"] = payload
        for key in ("title", "description", "start", "end", "timezone", "calendar_id"):
            value = parsed_input.get(key)
            if value is None:
                continue
            token = str(value).strip()
            if token:
                config[key] = token
        attendees = _normalize_attendee_emails(parsed_input.get("attendees"))
        if attendees:
            config["attendees"] = attendees
        if "description" not in config and tool_input.strip():
            config["description"] = tool_input.strip()
        return config

    if connector_id == "google_workspace" and action_id in {"create_doc", "create_document", "create_sheet", "create_spreadsheet"}:
        title = str(
            parsed_input.get("title")
            or parsed_input.get("name")
            or first_non_empty_line(tool_input)
            or ""
        ).strip()
        if title:
            config["title"] = title[:180]
        return config

    if tool_input.strip():
        config["text"] = tool_input.strip()
    return config


def build_direct_local_tool_config(
    connector_id: str,
    action_id: str,
    arguments: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    if connector_id == "file" and action_id == "read":
        path = str(arguments.get("path") or arguments.get("file_path") or "").strip()
        if not path:
            raise RuntimeError("Tool 'file__read' requires a file path.")
        return "file", {
            "path": path,
            "mode": "read",
            "summary": f"Read local file: {path}",
        }
    if connector_id == "file" and action_id == "write":
        path = str(arguments.get("path") or arguments.get("file_path") or "").strip()
        content = str(arguments.get("content") or "").strip()
        if not path or not content:
            raise RuntimeError("Tool 'file__write' requires path and content.")
        return "file", {
            "path": path,
            "content": content,
            "mode": "write",
            "summary": f"Write local file: {path}",
        }
    if connector_id == "shell" and action_id == "exec":
        command = str(arguments.get("command") or "").strip()
        if not command:
            raise RuntimeError("Tool 'shell__exec' requires a command.")
        return "shell", {
            "command": command,
            "summary": f"Execute shell command: {command}",
        }
    if connector_id == "screenshot" and action_id == "capture":
        return "screenshot", {
            "summary": "Capture screenshot of the current screen.",
        }
    if connector_id == "computer":
        if action_id == "ocr":
            return "computer", {
                "action": "ocr",
                "region": arguments.get("region"),
                "summary": "Read screen text with OCR.",
            }
        if action_id == "click":
            has_text = bool(str(arguments.get("text") or "").strip())
            has_coords = arguments.get("x") is not None and arguments.get("y") is not None
            if not has_text and not has_coords:
                raise RuntimeError("Tool 'computer__click' requires x/y or text.")
            return "computer", {
                "action": "click",
                "x": arguments.get("x"),
                "y": arguments.get("y"),
                "text": str(arguments.get("text") or "").strip() or None,
                "summary": "Click on the screen.",
            }
        if action_id == "type":
            text = str(arguments.get("text") or arguments.get("input") or "")
            if not text:
                raise RuntimeError("Tool 'computer__type' requires text.")
            return "computer", {
                "action": "type",
                "text": text,
                "summary": "Type into the active application.",
            }
        if action_id == "applescript":
            script = str(arguments.get("script") or arguments.get("input") or "").strip()
            if not script:
                raise RuntimeError("Tool 'computer__applescript' requires a script.")
            return "computer", {
                "action": "applescript",
                "script": script,
                "summary": "Run script.",
            }
        if action_id == "clipboard_read":
            return "computer", {
                "action": "clipboard_read",
                "summary": "Read the system clipboard.",
            }
        if action_id == "clipboard_write":
            text = str(arguments.get("text") or arguments.get("input") or "")
            if not text:
                raise RuntimeError("Tool 'computer__clipboard_write' requires text.")
            return "computer", {
                "action": "clipboard_write",
                "text": text,
                "summary": "Write to the system clipboard.",
            }
        if action_id == "notify":
            title = str(arguments.get("title") or "").strip()
            message = str(arguments.get("message") or arguments.get("text") or "").strip()
            if not title or not message:
                raise RuntimeError("Tool 'computer__notify' requires title and message.")
            return "computer", {
                "action": "notify",
                "title": title,
                "message": message,
                "summary": "Send a system notification.",
            }
        if action_id == "list_apps":
            return "computer", {
                "action": "list_apps",
                "summary": "List running applications.",
            }
        if action_id == "launch_app":
            name_or_path = str(arguments.get("name_or_path") or arguments.get("input") or "").strip()
            if not name_or_path:
                raise RuntimeError("Tool 'computer__launch_app' requires name_or_path.")
            return "computer", {
                "action": "launch_app",
                "name_or_path": name_or_path,
                "summary": f"Launch application: {name_or_path}",
            }
        if action_id == "speak":
            text = str(arguments.get("text") or arguments.get("input") or "").strip()
            if not text:
                raise RuntimeError("Tool 'computer__speak' requires text.")
            voice = str(arguments.get("voice") or "").strip()
            return "computer", {
                "action": "speak",
                "text": text,
                "voice": voice or None,
                "summary": "Speak text aloud.",
            }
    raise RuntimeError(f"Unsupported direct local tool '{connector_id}__{action_id}'.")


def _is_authorized_browser_adapter(browser: Any) -> bool:
    return bool(getattr(browser, "__empyralis_browser_adapter__", False))


def _resolve_direct_tool_browser_adapter(session_ctx: Any) -> Any:
    context = session_ctx if isinstance(session_ctx, dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    runtime_handle = context.get("runtime_handle")
    browser = getattr(runtime_handle, "browser", None)
    if browser is None:
        browser = context.get("browser")
    if not _is_authorized_browser_adapter(browser):
        from server_modules.execution_router import get_browser_adapter

        browser = get_browser_adapter(metadata, target="local_companion")
        if runtime_handle is not None:
            try:
                runtime_handle.browser = browser
            except Exception:
                pass
        if isinstance(context, dict):
            context["browser"] = browser
    return browser


DIRECT_CHAT_TOOL_EXECUTION_BLOCKED = "direct_chat_tool_execution_blocked"


def _raise_direct_chat_tool_execution_blocked() -> None:
    raise RuntimeError(DIRECT_CHAT_TOOL_EXECUTION_BLOCKED)


def _direct_tool_session_metadata(session_ctx: Dict[str, Any] | None) -> Dict[str, Any]:
    session_payload = session_ctx if isinstance(session_ctx, dict) else {}
    raw_agent_turn_request = session_payload.get("agent_turn_request")
    if isinstance(raw_agent_turn_request, dict):
        agent_turn_request = raw_agent_turn_request
    elif raw_agent_turn_request is not None:
        agent_turn_request = {
            "tenant_id": getattr(raw_agent_turn_request, "tenant_id", None),
            "workspace_id": getattr(raw_agent_turn_request, "workspace_id", None),
            "thread_id": getattr(raw_agent_turn_request, "thread_id", None),
            "session_id": getattr(raw_agent_turn_request, "session_id", None),
            "machine_target": getattr(raw_agent_turn_request, "machine_target", None),
            "context_hints": getattr(raw_agent_turn_request, "context_hints", None),
            "policy_context": getattr(raw_agent_turn_request, "policy_context", None),
        }
    else:
        agent_turn_request = {}
    context_hints = agent_turn_request.get("context_hints") if isinstance(agent_turn_request.get("context_hints"), dict) else {}
    policy_context = agent_turn_request.get("policy_context") if isinstance(agent_turn_request.get("policy_context"), dict) else {}
    metadata: Dict[str, Any] = {}
    if isinstance(context_hints.get("metadata"), dict):
        metadata.update(context_hints.get("metadata") or {})
    if isinstance(agent_turn_request.get("metadata"), dict):
        metadata.update(agent_turn_request.get("metadata") or {})
    if isinstance(session_payload.get("metadata"), dict):
        metadata.update(session_payload.get("metadata") or {})
    for source in (policy_context, context_hints, agent_turn_request, session_payload):
        if not isinstance(source, dict):
            continue
        for key in (
            "connection_mode",
            "tenant_id",
            "workspace_id",
            "thread_id",
            "session_id",
            "request_id",
            "client_request_id",
            "trace_id",
            "runtime_target",
            "canonical_runtime_target",
            "runtime_fabric_target",
            "execution_target_runtime_target",
            "runtime_attachment_id",
            "runtime_id",
            "gateway_id",
            "selected_gateway_id",
            "verified_user_device_gateway",
            "machine_target",
            "root_folder_uri",
            "folder_grants",
            "file_mount_grants",
            "execution_target",
            "execution_target_selected",
            "execution_target_requested",
            "execution_target_matching_runtime_ids",
            "execution_target_preferred_runtime_id",
            "execution_target_preferred_runtime_label",
            "runtime_access_mode",
            "permission_mode",
            "execution_mode",
            "runtime_mode",
        ):
            value = source.get(key)
            if value is not None and metadata.get(key) is None:
                metadata[key] = value
    metadata["source"] = "chat_direct_local_read"
    return metadata


_DIRECT_TOOL_HOME_ALIAS_DIRS = {
    "desktop": "Desktop",
    "documents": "Documents",
    "downloads": "Downloads",
}


def _normalize_direct_local_path_argument(raw_path: Any) -> str:
    token = str(raw_path or "").strip()
    if not token:
        return ""
    normalized = token.replace("\\", "/").strip()
    lowered = normalized.lower()
    home = Path.home()
    for alias, folder_name in _DIRECT_TOOL_HOME_ALIAS_DIRS.items():
        canonical = home / folder_name
        if lowered in {alias, f"~/{alias}", f"/root/{alias}", f"/home/user/{alias}"}:
            return str(canonical)
        for prefix in (f"/root/{alias}/", f"/home/user/{alias}/"):
            if lowered.startswith(prefix):
                suffix = normalized[len(prefix) :].lstrip("/")
                return str(canonical / suffix) if suffix else str(canonical)
    if normalized.startswith("~/"):
        return str(home / normalized[2:])
    if normalized.startswith("/root/"):
        return str(home / normalized[len("/root/") :])
    if normalized.startswith("/home/user/"):
        return str(home / normalized[len("/home/user/") :])
    return token


def _gateway_capability_for_direct_local_tool(connector_id: str, action_id: str) -> str:
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    if normalized_connector == "file":
        return "filesystem.read_write"
    if normalized_connector == "shell":
        return "shell.execute"
    if normalized_connector == "screenshot":
        return "screenshot.capture"
    if normalized_connector == "computer" and normalized_action:
        if normalized_action in {"launch", "launch_app"}:
            return "computer_control.launch_app"
        return f"computer_control.{normalized_action}"
    return ""


def _runtime_target_from_direct_tool_context(
    *,
    explicit_target: Any = None,
    gateway_id: Optional[str] = None,
    session_ctx: Dict[str, Any] | None = None,
) -> str:
    explicit = str(explicit_target or "").strip()
    if explicit:
        return explicit
    metadata = _direct_tool_session_metadata(session_ctx)
    for key in (
        "runtime_target",
        "canonical_runtime_target",
        "runtime_fabric_target",
        "execution_target_runtime_target",
        "execution_target",
    ):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    if str(gateway_id or "").strip():
        return "user_device_gateway"
    return "cloud_default"


def _direct_tool_targets_agent_computer(runtime_target: Any, metadata: Dict[str, Any]) -> bool:
    token = str(runtime_target or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"local", "local_companion", "user_device", "user_device_gateway", "gateway", "empyralis_gateway"}:
        return True
    try:
        from server_modules import runtime_attachment_service

        return runtime_attachment_service.canonical_runtime_target_id(token) == "user_device_gateway"
    except Exception:
        return False


def _hardware_action_requires_local_gateway(action_id: Any, capability_id: Any = None) -> bool:
    from server_modules import hardware_runtime_target_resolver

    return hardware_runtime_target_resolver.action_requires_local_hardware(capability_id or action_id)


_AGENT_PLACEMENT_STATUS_UNPLACED = "unplaced"
_AGENT_PLACEMENT_STATUS_REVOKED = "revoked"
_AGENT_PLACEMENT_STATUS_OFFLINE = "offline"

AGENT_PLACEMENT_REVOKED_ERROR = (
    "This agent's paired computer was revoked and its pairing cannot come "
    "back on its own. Open Hardware and place this agent on a different "
    "computer."
)


def _agent_placement_gateway_status(session_ctx: Dict[str, Any] | None) -> str:
    """Classifies the agent's OWN explicit placement — never the owner-scoped
    fallback box — into one of three FACTS a caller whose gateway resolution
    came back empty needs to tell apart:

      "unplaced" — the agent carries no preferred_gateway_id at all. Nothing
                    to report as broken; there was never a machine here.
      "revoked"  — the placed gateway's pairing is gone for good (deleted, or
                    device_trust_state/status says so). Reconnecting the
                    computer can never fix this — it needs a NEW pairing, or
                    the agent needs to be placed on a different machine.
      "offline"  — the placement is a real, still-valid pairing that simply
                    isn't live right now. Reconnecting genuinely might help.

    This is read-only and reporting-only: it re-derives the SAME first
    candidate `_resolve_direct_tool_gateway_id` already tried (session_ctx's
    own stamped `metadata.gateway_id`), it never re-resolves or substitutes a
    different box, and it changes no decision that function already made —
    it only lets a caller say WHY that decision came back empty. Found live
    2026-08-19: three placed agents in one workspace pointed at gateways
    whose `status`/`device_trust_state` were `revoked`, and nothing anywhere
    told anyone — every one of them read as an ordinary, retriable "offline"
    to both the product and the person, exactly like a machine that had
    merely gone to sleep.
    """
    metadata = _direct_tool_session_metadata(session_ctx)
    gateway_id = str(metadata.get("gateway_id") or "").strip()
    if not gateway_id:
        return _AGENT_PLACEMENT_STATUS_UNPLACED
    from server_modules import gateway_state_repository

    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        # The paired row is simply gone (deleted/never existed) — permanent,
        # same bucket as an explicit revocation.
        return _AGENT_PLACEMENT_STATUS_REVOKED
    if str(registration.get("device_trust_state") or "").strip().lower() == "revoked":
        return _AGENT_PLACEMENT_STATUS_REVOKED
    if str(registration.get("status") or "").strip().lower() != "active":
        # Any non-active status (today: only "revoked" is written, but a
        # future status this function doesn't know about is treated the same
        # conservative way — inactive is inactive, and promising a retry on
        # a status this code cannot name would be a guess) is permanent from
        # this seam's point of view: nothing here can make it active again.
        return _AGENT_PLACEMENT_STATUS_REVOKED
    return _AGENT_PLACEMENT_STATUS_OFFLINE


def _hardware_action_offline_result(
    action_id: Any, gateway_id: Any = None, *, placement_status: str = ""
) -> str:
    from server_modules import hardware_runtime_target_resolver

    if placement_status == _AGENT_PLACEMENT_STATUS_REVOKED:
        reason = "agent_placement_revoked"
        summary = AGENT_PLACEMENT_REVOKED_ERROR
    else:
        reason = "agent_computer_offline"
        summary = hardware_runtime_target_resolver.AGENT_COMPUTER_OFFLINE_ERROR

    return json.dumps(
        {
            "status": "offline",
            "reason": reason,
            "summary": summary,
            "runtime_target": "user_device_gateway",
            "execution_environment": "local_gateway",
            "runtime_session": {
                "state": "offline",
                "canonical_runtime_target": "user_device_gateway",
                "runtime_target": "user_device_gateway",
                "gateway_id": str(gateway_id or "").strip() or None,
            },
            "action_id": str(action_id or "").strip() or None,
        },
        ensure_ascii=False,
    )


def _runtime_access_mode_from_direct_tool_context(
    *,
    explicit_mode: Any = None,
    explicit_target: Any = None,
    gateway_id: Optional[str] = None,
    session_ctx: Dict[str, Any] | None = None,
) -> str:
    explicit = str(explicit_mode or "").strip()
    if explicit:
        return execution_mode_policy.normalize_runtime_access_mode(explicit)
    metadata = _direct_tool_session_metadata(session_ctx)
    for key in (
        "runtime_access_mode",
        "permission_mode",
        "execution_mode",
        "runtime_mode",
    ):
        value = str(metadata.get(key) or "").strip()
        if value:
            return execution_mode_policy.normalize_runtime_access_mode(value)
    resolved_gateway_id = str(
        gateway_id or metadata.get("gateway_id") or metadata.get("selected_gateway_id") or ""
    ).strip()
    if resolved_gateway_id:
        # A bound gateway_id is not itself authorization for full_access —
        # the paired registration's OWN configured runtime_access_mode is
        # the source of truth, exactly like gateway_execution_service.
        # _runtime_access_mode_for_dispatch's `explicit_mode or
        # metadata.get("runtime_access_mode")` already does for a real
        # dispatch. That downstream fallback can't be relied on to resolve
        # this itself, though: whatever this function returns is passed as
        # an explicit runtime_access_mode= kwarg into hardware_action_
        # broker_service.execute_hardware_action, which immediately runs it
        # through execution_mode_policy.normalize_runtime_access_mode (never
        # empty/None out) before forwarding it on as _runtime_access_mode_
        # for_dispatch's `explicit_mode` — permanently short-circuiting that
        # function's own registration fallback. The registration has to be
        # consulted HERE, at the point where a concrete value is produced,
        # or its real mode is unreachable no matter how it's phrased
        # downstream. On any lookup failure this falls through to the safe
        # guarded default below, never to full_access.
        try:
            from server_modules import gateway_state_repository

            registration = gateway_state_repository.get_gateway_registration(resolved_gateway_id)
        except Exception:
            registration = None
        registration_metadata = (
            registration.get("metadata")
            if isinstance(registration, dict) and isinstance(registration.get("metadata"), dict)
            else {}
        )
        return execution_mode_policy.normalize_runtime_access_mode(
            registration_metadata.get("runtime_access_mode")
        )
    runtime_target = _runtime_target_from_direct_tool_context(
        explicit_target=explicit_target,
        gateway_id=resolved_gateway_id,
        session_ctx=session_ctx,
    )
    if _direct_tool_targets_agent_computer(runtime_target, metadata):
        return execution_mode_policy.FULL_RUNTIME_ACCESS_MODE
    return execution_mode_policy.GUARDED_RUNTIME_ACCESS_MODE


def _agent_scope_from_direct_tool_context(session_ctx: Dict[str, Any] | None) -> str:
    metadata = _direct_tool_session_metadata(session_ctx)
    for key in ("agent_scope", "scope"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return "studio_agent"


def _agent_install_id_from_direct_tool_context(session_ctx: Dict[str, Any] | None) -> str:
    """Resolves the CALLING agent's install id from verified session
    identity — the same resolution every memory__* tool dispatch in this
    file already uses (see e.g. the agent_install_id= kwargs throughout the
    memory tool handlers below). Reads session_ctx directly rather than
    _direct_tool_session_metadata, which does not carry agent_install_id/
    active_agent_install_id at all. Used to scope the Gateway file/shell
    connector's on-box mount to the calling agent — see
    _execute_direct_tool_via_gateway_async's file/shell dispatch and
    docs/design/memory-placement-scope.md's "gateway seam" isolation gap."""
    session_payload = session_ctx if isinstance(session_ctx, dict) else {}
    return str(
        session_payload.get("agent_install_id")
        or session_payload.get("active_agent_install_id")
        or ""
    ).strip()


def _tenant_id_from_direct_tool_context(session_ctx: Dict[str, Any] | None) -> str:
    session_payload = session_ctx if isinstance(session_ctx, dict) else {}
    agent_turn_request = session_payload.get("agent_turn_request") if isinstance(session_payload.get("agent_turn_request"), dict) else {}
    metadata = _direct_tool_session_metadata(session_ctx)
    return str(
        session_payload.get("tenant_id")
        or agent_turn_request.get("tenant_id")
        or metadata.get("tenant_id")
        or "default"
    ).strip() or "default"


def _request_id_from_direct_tool_context(session_ctx: Dict[str, Any] | None) -> str:
    session_payload = session_ctx if isinstance(session_ctx, dict) else {}
    agent_turn_request = session_payload.get("agent_turn_request") if isinstance(session_payload.get("agent_turn_request"), dict) else {}
    context_hints = agent_turn_request.get("context_hints") if isinstance(agent_turn_request.get("context_hints"), dict) else {}
    context_metadata = context_hints.get("metadata") if isinstance(context_hints.get("metadata"), dict) else {}
    metadata = _direct_tool_session_metadata(session_ctx)
    for value in (
        session_payload.get("request_id"),
        session_payload.get("client_request_id"),
        agent_turn_request.get("request_id"),
        agent_turn_request.get("client_request_id"),
        context_hints.get("request_id"),
        context_hints.get("client_request_id"),
        context_metadata.get("request_id"),
        context_metadata.get("client_request_id"),
        metadata.get("request_id"),
        metadata.get("client_request_id"),
    ):
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _gateway_arguments_for_direct_local_tool(
    connector_id: str,
    action_id: str,
    argument_payload: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    payload = dict(argument_payload or {})
    if normalized_connector == "file":
        normalized_path = _normalize_direct_local_path_argument(
            payload.get("path") or payload.get("file_path")
        )
        if normalized_path:
            payload["path"] = normalized_path
        payload.setdefault("mode", normalized_action or "read")
        return payload
    if normalized_connector == "shell":
        def _aliased(text_value: str) -> str:
            text_value = text_value.replace("/root/Desktop", str(Path.home() / "Desktop"))
            text_value = text_value.replace("/root/Documents", str(Path.home() / "Documents"))
            text_value = text_value.replace("/root/Downloads", str(Path.home() / "Downloads"))
            return text_value

        command = str(payload.get("command") or "").strip()
        if command:
            payload["command"] = _aliased(command)
        # Batch dispatch: a `commands` array means "run these in one round
        # trip" (see batch-shell.ts / GatewayShellRuntime.executeShellBatch
        # on the gateway side, which is what actually detects and handles
        # this — this function only needs to apply the same home-directory
        # alias rewrite per entry that the single-command path already
        # does). Deliberately NOT set a `command` key here even when empty:
        # gateway_execution_service._normalize_gateway_capability only
        # touches `args["command"]` when a truthy `command` is present, so
        # leaving it absent is what keeps `commands`/`stop_on_failure`
        # passed through untouched.
        raw_commands = payload.get("commands")
        if isinstance(raw_commands, list) and raw_commands:
            normalized_commands: List[Any] = []
            for entry in raw_commands:
                if isinstance(entry, str):
                    normalized_commands.append(_aliased(entry))
                elif isinstance(entry, dict):
                    entry_command = str(entry.get("command") or "")
                    normalized_entry = dict(entry)
                    normalized_entry["command"] = _aliased(entry_command)
                    normalized_commands.append(normalized_entry)
                else:
                    normalized_commands.append(entry)
            payload["commands"] = normalized_commands
        return payload
    if normalized_connector == "computer":
        normalized_path = _normalize_direct_local_path_argument(
            payload.get("path") or payload.get("file_path")
        )
        if normalized_path:
            payload["path"] = normalized_path
        return payload
    return payload


_LOCAL_DIRECT_TOOL_GATEWAY_FALLBACK_ENVS = {"dev", "development", "local", "test", "testing"}


def _local_direct_tool_gateway_fallback_enabled() -> bool:
    token = (
        os.getenv("ORION_ENV")
        or os.getenv("ENV")
        or os.getenv("NODE_ENV")
        or ""
    ).strip().lower()
    return token in _LOCAL_DIRECT_TOOL_GATEWAY_FALLBACK_ENVS


def _resolve_live_gateway_owned_by(
    workspace_id: str,
    owner_user_id: str,
    *,
    gateway_state_repository: Any,
    gateway_protocol_service: Any,
    capability_id: str = "",
) -> str | None:
    """A live gateway in this workspace that `owner_user_id` OWNS, or None.

    MAN-356. This replaced `_resolve_live_gateway_from_workspace`, which
    returned ANY live active gateway registered to the workspace and had no
    parameter that could express whose machine it was. CLAUDE.md's law is
    "Hardware attaches to its owner, never to the project… a project invite
    is not physical access" — and the old function made that law structurally
    inexpressible, so a workspace member reaching any agent could land shell
    and filesystem execution on the founder's own paired Mac.

    `gateway_registrations.user_id` is the person who paired the box (see
    gateway_state_repository's schema: user_id NOT NULL, and the
    workspace_id+user_id+device_id index). Requiring it to equal the asking
    human is the whole gate: you can still reach your OWN machine through an
    agent that has no explicit placement, and you can never reach anybody
    else's.

    An empty `owner_user_id` matches nothing. That is deliberate and is the
    fail-CLOSED direction: an identity-less turn (a channel/system principal)
    has no person whose machine it could borrow, and `""` must never behave
    like a wildcard — the same "a scope column with a default is a loaded
    gun" rule this codebase already applies to agent_id on inbound writes.

    `capability_id`, when given, additionally requires the candidate to
    DECLARE that capability. Found live 2026-08-19: a founder who owns two
    boxes in one workspace (his own Mac, which has shell/docker/computer;
    a production Linux gateway, which structurally never will) could have a
    turn silently land on the SECOND box the instant the FIRST one's own
    placement went momentarily unusable (offline, briefly inactive) — this
    function picked "whichever owned box is live", with zero regard for
    whether that box could do anything the turn actually needed. The
    resulting report was a byte-for-byte "gateway_capability_missing" about
    a machine that was never going to satisfy it, while the real, actionable
    fact (the agent's OWN placement is momentarily unreachable) never
    surfaced. Filtering here — not in the caller's PLACEMENT candidate — is
    deliberate: this is the "reach your own box through an agent you have
    not explicitly placed" convenience fallback, so it is allowed to keep
    looking for a box that can actually help; the agent's own explicit
    placement is never filtered this way; see _resolve_direct_tool_gateway_id.
    """
    clean_owner_id = str(owner_user_id or "").strip()
    if not clean_owner_id:
        return None
    clean_capability_id = str(capability_id or "").strip()
    for registration in gateway_state_repository.list_workspace_gateway_registrations(
        str(workspace_id or "default").strip() or "default",
        include_revoked=False,
    ):
        gateway_id = str(registration.get("gateway_id") or "").strip()
        if not gateway_id:
            continue
        if str(registration.get("status") or "").strip().lower() != "active":
            continue
        if str(registration.get("user_id") or "").strip() != clean_owner_id:
            continue
        if clean_capability_id:
            from server_modules import gateway_inventory_service as _gw_inventory

            if not _gw_inventory.registration_has_execution_capability(
                registration, clean_capability_id
            ):
                continue
        if gateway_protocol_service.gateway_connection_is_live(gateway_id):
            return gateway_id
    return None


def _resolve_direct_tool_gateway_id(
    workspace_id: str,
    *,
    session_ctx: Dict[str, Any] | None,
    requested_gateway_id: Any = None,
    capability_id: str = "",
) -> str | None:
    """Which machine may this turn's tools execute on? None = no machine.

    MAN-356. EXECUTION FOLLOWS THE AGENT'S PLACEMENT. The order is:

      1. hardware_access == "none"  -> None, always. The agent is cloud-only.
      2. the agent's PLACEMENT       -> that box, if usable + live.
      3. no placement                -> only a box the ASKING PERSON owns
                                         THAT CAN ACTUALLY DO THIS.
      4. otherwise                   -> None.

    `capability_id` (e.g. "shell.execute") is OPTIONAL and used ONLY in step
    3 — never to filter the agent's own explicit placement in step 2. Found
    live 2026-08-19 on a workspace with two boxes owned by the same person
    (a Mac with shell/docker/computer, a production Linux gateway with
    neither): the moment the agent's OWN placement went momentarily
    unreachable, step 3 picked the OTHER owned box purely because it was
    live, with no regard for whether it could run shell.execute at all —
    reporting a "gateway_capability_missing" that read as a config problem
    on the wrong machine, while the real fact (the real placement was
    briefly unreachable) never surfaced. Step 2 stays capability-blind on
    purpose: if the box the owner explicitly chose lacks a capability, that
    is a real, honest fact the readiness check below should report —
    silently swapping to a different box would hide it instead.

    PLACEMENT IS THE CONSENT MOMENT, and that is why no per-person hardware
    permission exists anywhere in this path. A box reaches an agent because
    the HARDWARE'S OWNER put it there — either directly (the Hardware tab /
    the create-agent wizard's Placement step, both of which write
    install_metadata.preferred_gateway_id) or through a project default that
    specialist_runtime_context re-checks against the machine owner's own live
    opt-in on every single resolution. Nobody reaches a machine THROUGH an
    agent; the agent reaches the machine its owner gave it.

    Returning None is a CLEAN DEGRADATION, never an error, and the callers
    already expect it: `_hardware_action_offline_result` is the honest
    "that machine isn't reachable" answer, and the gateway branches are all
    guarded by `if ... and gateway_id`. An agent with no reachable box runs
    cloud-side with fewer capabilities, which is exactly what CLAUDE.md's
    hardware law asks for — "a clean degradation, never an error and never a
    silent borrow."

    `requested_gateway_id` is the model's own `hardware__action` argument. It
    is VALIDATED here, never trusted: it may only select a box this agent was
    already placed on. Before this, callers did
    `payload.get("gateway_id") or _resolve_direct_tool_gateway_id(...)`, so a
    model that simply named a box bypassed placement entirely.
    """
    from server_modules import gateway_protocol_service, gateway_state_repository
    from server_modules import workspace_scope as _ws
    from server_modules.hardware_runtime_adapters import gateway_adapter as _gateway_adapter

    normalized_workspace_id = _ws.resolve_workspace(
        workspace_id, site="skills_service:resolve_gateway_for_workspace"
    )

    metadata = _direct_tool_session_metadata(session_ctx)

    # (1) The agent's own hardware bucket. An explicit "none" ends it here —
    # this is the setting the product has rendered on every agent's Hardware
    # tab since stage_4b while enforcing it nowhere (resolve_hardware_access
    # had exactly one non-test caller, building a list payload). An ABSENT
    # key is "unknown", not "none": Sage's own turns resolve no specialist
    # context and stamp nothing, and defaulting those to "none" would take
    # the workspace operator's hardware away on an inference rather than on a
    # setting anyone chose. Unknown falls through to (3), where the caller
    # can still only ever reach their own machine.
    if str(metadata.get("agent_hardware_access") or "").strip().lower() == "none":
        return None

    candidate_ids: List[str] = []
    for key in (
        "gateway_id",
        "execution_target_preferred_runtime_id",
        "runtime_id",
    ):
        value = str(metadata.get(key) or "").strip()
        if value and value not in candidate_ids:
            candidate_ids.append(value)
    matching_runtime_ids = metadata.get("execution_target_matching_runtime_ids")
    if isinstance(matching_runtime_ids, list):
        for item in matching_runtime_ids:
            value = str(item or "").strip()
            if value and value not in candidate_ids:
                candidate_ids.append(value)

    # (2a) A model-supplied `gateway_id` is a HINT, never an authorization.
    # It is honoured only when it names a box this agent was already placed
    # on; otherwise it is dropped and resolution continues, so the turn lands
    # on the agent's real placement (or the caller's own machine) instead of
    # wherever the model pointed. Dropped rather than refused because the
    # model usually just echoes an id back from an earlier tool result — the
    # safe outcome is "the right box", not "no box".
    requested = str(requested_gateway_id or "").strip()
    if requested:
        if requested in candidate_ids:
            candidate_ids = [requested] + [c for c in candidate_ids if c != requested]
        else:
            logger.info(
                "direct tool gateway: ignoring requested gateway_id %s — not this agent's placement",
                requested,
            )
    for gateway_id in candidate_ids:
        registration = gateway_state_repository.get_gateway_registration(gateway_id)
        if not registration:
            continue
        # A candidate sourced from session metadata (gateway_id / runtime_id /
        # execution_target_matching_runtime_ids) must actually belong to THIS
        # workspace before it's trusted — otherwise a stale or cross-workspace
        # runtime id surviving in metadata could hand this turn's shell/
        # filesystem/browser tools to another workspace's gateway. Mirrors
        # machine_lease_service._worker_runtime_scope_allows_run, which already
        # enforces this for the async run-queue claim path.
        usable, _unusable_reason = _gateway_adapter.registration_is_usable(
            registration, workspace_id=normalized_workspace_id
        )
        if not usable:
            continue
        if gateway_protocol_service.gateway_connection_is_live(gateway_id):
            return gateway_id
    # (3) No placement resolved — either the agent was never placed on a box,
    # or the box it was placed on is offline/unusable. Fall back ONLY to a
    # machine the ASKING PERSON owns.
    #
    # MAN-356: this used to be `_resolve_live_gateway_from_workspace`, i.e.
    # any live gateway in the workspace, with no notion of who was asking or
    # whose machine it was — the silent borrow that let a member run commands
    # on the founder's Mac. Scoping it to the caller's own registrations keeps
    # every legitimate case working (you reach your own box through an agent
    # you have not explicitly placed) while making the cross-person case
    # structurally impossible rather than merely unlikely.
    #
    # The identity comes from verified session context, NEVER from the tool
    # call's own arguments — the same rule the gateway file/shell mount
    # already follows a few hundred lines below, and the same rule that makes
    # memory_write_private's partition unspoofable.
    caller_user_id = _resolve_session_user_id(session_ctx)
    resolved_gateway_id = _resolve_live_gateway_owned_by(
        normalized_workspace_id,
        caller_user_id,
        gateway_state_repository=gateway_state_repository,
        gateway_protocol_service=gateway_protocol_service,
        capability_id=capability_id,
    )
    if resolved_gateway_id:
        return resolved_gateway_id
    # No cross-workspace fallback to whatever is registered under the literal
    # "default" workspace: that used to fire whenever the backend considered
    # itself in a dev-like environment, regardless of which workspace was
    # asking — i.e. ANY workspace could reach an unscoped/"default" worker
    # just because the process was running in dev mode. A worker with no
    # workspace of its own must not be handed to an arbitrary workspace.
    return None


class _GatewayShellToolResultText(str):
    """`str` subclass returned for the shell/run_command gateway path only.

    MAN-125 residual gap: `_format_gateway_direct_local_tool_result` used to
    hardcode `"status": "completed"` regardless of `exit_code`, and even once
    that's fixed the honest status/exit_code never reach
    `tool_result_status.classify_tool_result` — `callbacks.format_direct_local_tool_result`
    (production: `direct_tool_config_service.format_direct_local_tool_result`
    -> `_format_shell_result_for_chat`) flattens the whole envelope to plain
    prose that never mentions status or exit_code at all, and even the
    pre-flattening envelope nests the action three levels under
    `result_data.child_result.outputs.actions[0]` — too deep for that
    classifier's bounded (depth-2) unwrap, and under a `result_data` key it
    doesn't even look for.

    Rather than change what the model/human reads (real blast radius: this
    return value also flows to Sage's daily-operator loop via
    tool_runtime_bindings.execute_single_direct_tool_call, and to every other
    execute_single_direct_tool_call caller — see skills_service's callers),
    this carries the flat `{"status", "exit_code"}` the formatter used
    ALONGSIDE the exact same prose string, as an attribute nobody else looks
    for:
      - `isinstance(x, str)` is True and every string operation on it is
        identical to a plain str with the same characters.
      - `str(x)` (used by sanitize_tool_result_for_context and everywhere
        else the return value gets touched) collapses it back to a genuine
        plain `str`, dropping the attribute — so nothing downstream of the
        model-facing text can observe this at all.
    Only direct_chat_generation_service.py's classify_tool_result call site
    opportunistically reads `gateway_tool_status` off the raw (unstringified)
    value, before that collapse happens.
    """

    gateway_tool_status: Dict[str, Any] = {}


def _with_gateway_tool_status(text: str, status: Dict[str, Any]) -> str:
    wrapped = _GatewayShellToolResultText(text)
    wrapped.gateway_tool_status = dict(status)
    return wrapped


def _format_gateway_direct_local_tool_result(
    *,
    connector_id: str,
    action_id: str,
    capability_id: str,
    gateway_response: Dict[str, Any],
    callbacks: Any,
) -> str:
    inner_result = gateway_response.get("result")
    if isinstance(inner_result, dict) and (
        "summary" in inner_result or "result_data" in inner_result
    ):
        return callbacks.format_direct_local_tool_result(inner_result)
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    if (
        normalized_connector == "shell"
        and isinstance(inner_result, dict)
        and isinstance(inner_result.get("commands"), list)
    ):
        # Batch result: GatewayShellRuntime.executeShellBatch's shape, one
        # entry per command with its own status/exit_code/stdout/stderr/
        # reason. This MUST be checked, and formatted, before the
        # single-command branch below — that branch reads `command`/
        # `exit_code` off the TOP of inner_result, which a batch result
        # doesn't have, and would silently read as "completed" with an
        # empty command (exit_code missing -> `not exit_code` is True).
        batch_commands = inner_result.get("commands") or []
        stop_on_failure = bool(inner_result.get("stop_on_failure"))
        stopped_early = bool(inner_result.get("stopped_early"))
        batch_timed_out = bool(inner_result.get("batch_timed_out"))
        action_entries: List[Dict[str, Any]] = []
        worst_status = "completed"
        for entry in batch_commands:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "").strip().lower()
            command_text = str(entry.get("command") or "").strip()
            exit_code = entry.get("exit_code")
            stdout = str(entry.get("stdout") or "").strip()
            stderr = str(entry.get("stderr") or "").strip()
            reason = str(entry.get("reason") or "").strip()
            # Never collapse the gateway's five-way outcome into a single
            # completed/failed bit — "failed" (ran, bad exit), "skipped"/
            # "not_run" (never ran) and "timed_out" (ran, unknown exit) are
            # three different facts and stay distinguishable in the prose
            # below even after this flattens to a chat-visible string.
            if status == "success":
                action_status = "completed"
            elif status in ("skipped", "not_run"):
                action_status = "not_run"
            elif status == "timed_out":
                action_status = "timed_out"
            else:
                action_status = "failed"
            if action_status != "completed":
                worst_status = "failed"
            preview_parts = [part for part in [stdout, f"stderr:\n{stderr}" if stderr else "", reason] if part]
            output_preview = "\n".join(preview_parts).strip() or f"[{status or 'unknown'}]"
            action_entries.append(
                {
                    "tool": "run_command",
                    "command": command_text,
                    "status": action_status,
                    "exit_code": exit_code,
                    "output_preview": output_preview,
                    "stdout_preview": stdout,
                    "stderr_preview": stderr,
                }
            )
        summary_bits = [f"Ran a batch of {len(batch_commands)} command(s) on this device"]
        if stopped_early and stop_on_failure:
            summary_bits.append("— stopped early after a failure")
        if batch_timed_out:
            summary_bits.append("— the batch's shared time budget ran out before every command finished")
        result = {
            "summary": " ".join(summary_bits).strip() + ".",
            "result_data": {
                "tool_variant": "run_command_batch",
                "child_result": {
                    "outputs": {
                        "actions": action_entries,
                        "artifacts": [],
                    }
                },
            },
        }
        formatted = callbacks.format_direct_local_tool_result(result)
        return _with_gateway_tool_status(
            formatted,
            {
                "status": worst_status,
                "commands": [
                    {"command": e.get("command"), "status": e.get("status"), "exit_code": e.get("exit_code")}
                    for e in action_entries
                ],
            },
        )
    if normalized_connector == "shell" and isinstance(inner_result, dict):
        command = str(inner_result.get("command") or "").strip()
        stdout = str(inner_result.get("stdout") or "").strip()
        stderr = str(inner_result.get("stderr") or "").strip()
        exit_code = inner_result.get("exit_code")
        output_preview = "\n".join(part for part in [stdout, f"stderr:\n{stderr}" if stderr else ""] if part).strip()
        # Honest regardless of who reads it: a nonzero exit is not
        # "completed". Previously hardcoded to "completed" no matter what
        # exit_code said (MAN-125 residual gap).
        action_status = "completed" if not exit_code else "failed"
        action_entry = {
            "tool": "run_command",
            "command": command,
            "status": action_status,
            "exit_code": exit_code,
            "output_preview": output_preview or (f"Exit code: {exit_code}" if exit_code is not None else ""),
            "stdout_preview": stdout,
            "stderr_preview": stderr,
        }
        result = {
            "summary": "Checked this device.",
            "result_data": {
                "tool_variant": "run_command",
                "child_result": {
                    "outputs": {
                        "actions": [action_entry],
                        "artifacts": [],
                    }
                },
            },
        }
        formatted = callbacks.format_direct_local_tool_result(result)
        return _with_gateway_tool_status(
            formatted,
            {"status": action_status, "exit_code": exit_code},
        )
    if normalized_connector == "file" and isinstance(inner_result, dict):
        path = str(inner_result.get("path") or "").strip()
        mode = str(inner_result.get("mode") or normalized_action or "read").strip().lower()
        if mode == "read" and bool(inner_result.get("is_directory")):
            entries = inner_result.get("entries") if isinstance(inner_result.get("entries"), list) else []
            lines = [f"Listed directory: {path}" if path else "Listed directory:"]
            lines.extend(
                f"{index}. {str(item or '').strip()}"
                for index, item in enumerate(entries[:200], start=1)
            )
            return "\n".join(lines).strip()
        if mode == "read":
            content = str(inner_result.get("content") or "").strip()
            return "\n".join(
                part
                for part in [f"Read file: {path}" if path else "Read file:", content]
                if part
            ).strip()
        if mode in {"write", "append"}:
            return f"Wrote file: {path}" if path else "File write completed."
        if mode == "delete":
            return f"Deleted file: {path}" if path else "File delete completed."
    if isinstance(inner_result, dict):
        try:
            return json.dumps(inner_result, ensure_ascii=False)
        except Exception:
            return str(inner_result)
    if inner_result is not None:
        return str(inner_result).strip()
    capability_summary = str(capability_id or "").strip() or "local capability"
    return f"{capability_summary} completed."


def _execute_direct_tool_via_gateway(
    *,
    gateway_id: Optional[str],
    capability_id: str,
    arguments: Dict[str, Any],
    run_id: str,
    trace_id: str,
    workspace_id: str,
    runtime_target: str = "user_device_gateway",
    runtime_access_mode: str = "default_guarded",
    agent_scope: str = "studio_agent",
    tenant_id: str = "default",
    thread_id: str = "",
    request_id: str = "",
    session_ctx: Dict[str, Any] | None = None,
    require_approval: Optional[bool] = None,
    agent_install_id: Optional[str] = None,
    callbacks: Any,
) -> Dict[str, Any]:
    from server_modules import hardware_action_broker_service

    trace_context = session_ctx.get("trace_context") if isinstance(session_ctx, dict) else None
    response = callbacks.run_async_tool_call(
        hardware_action_broker_service.execute_hardware_action(
            tenant_id=str(tenant_id or "default").strip() or "default",
            workspace_id=str(workspace_id or "default").strip() or "default",
            action_id=capability_id,
            runtime_target=runtime_target,
            runtime_access_mode=runtime_access_mode,
            agent_scope=agent_scope,
            gateway_id=gateway_id,
            capability_id=capability_id,
            arguments=arguments,
            run_id=run_id,
            trace_id=trace_id,
            thread_id=thread_id,
            request_id=request_id,
            trace_context=trace_context,
            require_approval=require_approval,
            agent_install_id=agent_install_id,
        )
    )
    payload = dict(response) if isinstance(response, dict) else {"result": response}
    if isinstance(payload.get("execution"), dict):
        return dict(payload["execution"])
    status = str(payload.get("status") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if status in {"waiting_approval", "offline", "degraded", "failed"}:
        return {
            "gateway_id": str(gateway_id or "").strip(),
            "capability_id": str(capability_id or "").strip(),
            "run_id": str(run_id or "").strip(),
            "result": {
                "summary": reason or status or "Gateway hardware action did not complete.",
                "status": status,
            },
        }
    return payload

async def _execute_direct_tool_via_gateway_async(
    *,
    gateway_id: Optional[str],
    capability_id: str,
    arguments: Dict[str, Any],
    run_id: str,
    trace_id: str,
    workspace_id: str,
    runtime_target: str = "user_device_gateway",
    runtime_access_mode: str = "default_guarded",
    agent_scope: str = "studio_agent",
    tenant_id: str = "default",
    thread_id: str = "",
    request_id: str = "",
    session_ctx: Dict[str, Any] | None = None,
    require_approval: Optional[bool] = None,
    agent_install_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Async version of _execute_direct_tool_via_gateway.

    Directly awaits the hardware action broker instead of going through
    callbacks.run_async_tool_call's thread.join() which deadlocks the
    Uvicorn event loop when called from within an async context.
    """
    from server_modules import hardware_action_broker_service

    trace_context = session_ctx.get("trace_context") if isinstance(session_ctx, dict) else None
    response = await hardware_action_broker_service.execute_hardware_action(
        tenant_id=str(tenant_id or "default").strip() or "default",
        workspace_id=str(workspace_id or "default").strip() or "default",
        action_id=capability_id,
        runtime_target=runtime_target,
        runtime_access_mode=runtime_access_mode,
        agent_scope=agent_scope,
        gateway_id=gateway_id,
        capability_id=capability_id,
        arguments=arguments,
        run_id=run_id,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        trace_context=trace_context,
        require_approval=require_approval,
        agent_install_id=agent_install_id,
    )
    payload = dict(response) if isinstance(response, dict) else {"result": response}
    if isinstance(payload.get("execution"), dict):
        return dict(payload["execution"])
    status = str(payload.get("status") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if status in {"waiting_approval", "offline", "degraded", "failed"}:
        return {
            "gateway_id": str(gateway_id or "").strip(),
            "capability_id": str(capability_id or "").strip(),
            "run_id": str(run_id or "").strip(),
            "result": {
                "summary": reason or status or "Gateway hardware action did not complete.",
                "status": status,
            },
        }
    return payload


def _format_hardware_action_result(payload: Dict[str, Any]) -> str:
    from server_modules import gateway_reason_messages, gateway_registry_service, gateway_state_repository

    runtime_session = payload.get("runtime_session") if isinstance(payload.get("runtime_session"), dict) else {}
    reason = str(payload.get("reason") or "").strip() or None
    # Same evidence gate gateway_adapter.py's own emit_tool_result call
    # site uses (see gateway_reason_messages._docker_confirmed_not_ready):
    # without a real registration lookup here, a Docker-gated capability_
    # missing/not_ready reason would default to the honest generic message
    # rather than guessing — this makes it match the specific, actionable
    # sentence whenever the gateway's own last-reported status actually
    # confirms Docker is the cause. This is the JSON string a model reads
    # DIRECTLY as its tool result on BOTH engines (build_sdk_tools's
    # _handler calls the same generation_services.execute_single_direct_
    # tool_call this function's caller does), so a wrong diagnosis here is
    # not just a trace-view cosmetic — the model can relay it verbatim to
    # the person. Never raises: a missing/unreachable registration
    # degrades to {} (no evidence), the same safe default as no gateway_id
    # at all.
    gateway_id = str(runtime_session.get("gateway_id") or "").strip()
    service_statuses: Dict[str, str] = {}
    if gateway_id:
        try:
            registration = gateway_state_repository.get_gateway_registration(gateway_id)
            service_statuses = gateway_registry_service.capability_service_statuses(registration)
        except Exception:
            service_statuses = {}
    summary = {
        "status": str(payload.get("status") or "").strip(),
        "reason": reason,
        # Plain-language, actionable translation of `reason` — added so a
        # model reading this tool result directly (this JSON string IS the
        # tool's returned content on the direct-tool-call path) has real
        # English to relay instead of just a raw reason token like
        # "gateway_capability_missing" it has to guess the meaning of. See
        # gateway_reason_messages.py's module doc comment (MAN-295).
        "message": (
            gateway_reason_messages.gateway_reason_message(
                reason,
                capability_id=runtime_session.get("capability_id"),
                service_statuses=service_statuses,
            )
            if reason
            else None
        ),
        "runtime_target": str(runtime_session.get("canonical_runtime_target") or runtime_session.get("runtime_target") or "").strip() or None,
        "runtime_access_mode": str(runtime_session.get("runtime_access_mode") or "").strip() or None,
        "runtime_state": str(runtime_session.get("state") or "").strip() or None,
        "gateway_id": str(runtime_session.get("gateway_id") or "").strip() or None,
        "device_id": str(runtime_session.get("device_id") or "").strip() or None,
        "approval_id": str((payload.get("approval") if isinstance(payload.get("approval"), dict) else {}).get("approval_id") or "").strip() or None,
        "artifacts": list(payload.get("artifacts") or []),
    }
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    if isinstance(execution.get("result"), dict):
        summary["result"] = execution["result"]
    return json.dumps({key: value for key, value in summary.items() if value not in (None, "", [])}, ensure_ascii=False)


def _execute_hardware_action_tool_call(
    *,
    argument_payload: Dict[str, Any],
    workspace_id: str,
    thread_id: str,
    index: int,
    session_ctx: Dict[str, Any] | None,
    callbacks: Any,
) -> str:
    from server_modules import hardware_action_broker_service

    payload = dict(argument_payload or {})
    action_id = str(
        payload.get("action")
        or payload.get("capability_id")
        or payload.get("tool")
        or payload.get("operation")
        or ""
    ).strip()
    if not action_id:
        raise RuntimeError("Tool 'hardware__action' requires an action.")
    action_arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    action_data = payload.get("action_data")
    if isinstance(action_data, str) and action_data.strip():
        try:
            parsed_action_data = json.loads(action_data)
        except Exception:
            parsed_action_data = None
        action_data = parsed_action_data if isinstance(parsed_action_data, dict) else action_data
    if isinstance(action_data, dict):
        action_arguments = {**dict(action_data), **action_arguments}
    if not action_arguments:
        action_arguments = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "action",
                "capability_id",
                "tool",
                "operation",
                "action_data",
                "arguments",
                "runtime_target",
                "runtime_access_mode",
                "execution_mode",
                "permission_mode",
                "gateway_id",
                "device_id",
                "node_id",
                "request_id",
                "client_request_id",
            }
        }
    metadata = _direct_tool_session_metadata(session_ctx)
    # MAN-356: the model's own `gateway_id` argument no longer short-circuits
    # placement resolution — it is passed IN to be validated against the
    # agent's placement. `payload` here is the tool call's arguments, so the
    # old `payload.get("gateway_id") or _resolve(...)` let a model name any
    # box in the workspace and skip the resolver entirely.
    from server_modules import hardware_access_policy_service as _hw_policy

    hardware_capability_id = _hw_policy.normalize_hardware_capability_id(
        action_id, payload.get("capability_id")
    )
    gateway_id = _resolve_direct_tool_gateway_id(
        workspace_id,
        session_ctx=session_ctx,
        requested_gateway_id=payload.get("gateway_id"),
        capability_id=hardware_capability_id,
    )
    trace_context = session_ctx.get("trace_context") if isinstance(session_ctx, dict) else None
    trace_id = (
        str(getattr(trace_context, "trace_id", "") or "").strip()
        or str(metadata.get("trace_id") or "").strip()
        or f"trace_{uuid.uuid4().hex}"
    )
    run_id = str(payload.get("run_id") or "").strip() or (
        f"direct_chat:{str(thread_id or 'thread').strip() or 'thread'}:hardware:{index}:{uuid.uuid4().hex}"
    )
    request_id = (
        _request_id_from_direct_tool_context(session_ctx)
        or str(payload.get("request_id") or payload.get("client_request_id") or "").strip()
        or run_id
    )
    runtime_target = _runtime_target_from_direct_tool_context(
        explicit_target=payload.get("runtime_target"),
        gateway_id=gateway_id,
        session_ctx=session_ctx,
    )
    normalized_action_id = action_id.strip().lower()
    local_gateway_required = _hardware_action_requires_local_gateway(
        action_id,
        payload.get("capability_id"),
    )
    if local_gateway_required and not gateway_id:
        return _hardware_action_offline_result(
            action_id, placement_status=_agent_placement_gateway_status(session_ctx)
        )
    if gateway_id and (
        local_gateway_required
        or normalized_action_id in {
            "screenshot",
            "screenshot.capture",
            "screen.capture",
            "screen.ocr",
            "ocr",
            "mouse",
            "keyboard",
            "mouse.click",
            "mouse.move",
            "keyboard.type",
            "keyboard.press",
            "input.click",
            "input.type",
            "app.focus",
            "window.control",
        }
        or normalized_action_id.startswith("computer_control.")
        or normalized_action_id.startswith("screenshot.")
        or normalized_action_id.startswith("screen.")
    ):
        runtime_target = "user_device_gateway"
    result = callbacks.run_async_tool_call(
        hardware_action_broker_service.execute_hardware_action(
            tenant_id=_tenant_id_from_direct_tool_context(session_ctx),
            workspace_id=str(workspace_id or "default").strip() or "default",
            action_id=action_id,
            capability_id=payload.get("capability_id"),
            arguments=action_arguments,
            runtime_target=runtime_target,
            runtime_access_mode=_runtime_access_mode_from_direct_tool_context(
                explicit_mode=payload.get("runtime_access_mode") or payload.get("execution_mode") or payload.get("permission_mode"),
                explicit_target=runtime_target,
                gateway_id=gateway_id,
                session_ctx=session_ctx,
            ),
            agent_scope=_agent_scope_from_direct_tool_context(session_ctx),
            gateway_id=gateway_id,
            device_id=str(payload.get("device_id") or "").strip() or None,
            node_id=str(payload.get("node_id") or "").strip() or None,
            run_id=run_id,
            trace_id=trace_id,
            thread_id=str(thread_id or "").strip(),
            request_id=request_id,
            trace_context=trace_context,
            require_approval=False,
            file_mount_grants=metadata.get("file_mount_grants") if isinstance(metadata.get("file_mount_grants"), list) else None,
        )
    )
    return _format_hardware_action_result(dict(result) if isinstance(result, dict) else {"status": "completed", "execution": {"result": result}})

async def _execute_hardware_action_tool_call_async(
    *,
    argument_payload: Dict[str, Any],
    workspace_id: str,
    thread_id: str,
    index: int,
    session_ctx: Dict[str, Any] | None,
) -> str:
    """Async version of _execute_hardware_action_tool_call.

    Directly awaits the hardware action broker instead of going through
    run_async_tool_call's thread.join() which deadlocks the Uvicorn event
    loop when called from within an async context (e.g. Sage's action loop).
    """
    from server_modules import hardware_action_broker_service

    payload = dict(argument_payload or {})
    action_id = str(
        payload.get("action")
        or payload.get("capability_id")
        or payload.get("tool")
        or payload.get("operation")
        or ""
    ).strip()
    if not action_id:
        raise RuntimeError("Tool 'hardware__action' requires an action.")
    action_arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    action_data = payload.get("action_data")
    if isinstance(action_data, str) and action_data.strip():
        try:
            parsed_action_data = json.loads(action_data)
        except Exception:
            parsed_action_data = None
        action_data = parsed_action_data if isinstance(parsed_action_data, dict) else action_data
    if isinstance(action_data, dict):
        action_arguments = {**dict(action_data), **action_arguments}
    if not action_arguments:
        action_arguments = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "action",
                "capability_id",
                "tool",
                "operation",
                "action_data",
                "arguments",
                "runtime_target",
                "runtime_access_mode",
                "execution_mode",
                "permission_mode",
                "gateway_id",
                "device_id",
                "node_id",
                "request_id",
                "client_request_id",
            }
        }
    metadata = _direct_tool_session_metadata(session_ctx)
    # MAN-356: the model's own `gateway_id` argument no longer short-circuits
    # placement resolution — it is passed IN to be validated against the
    # agent's placement. `payload` here is the tool call's arguments, so the
    # old `payload.get("gateway_id") or _resolve(...)` let a model name any
    # box in the workspace and skip the resolver entirely.
    from server_modules import hardware_access_policy_service as _hw_policy

    hardware_capability_id = _hw_policy.normalize_hardware_capability_id(
        action_id, payload.get("capability_id")
    )
    gateway_id = _resolve_direct_tool_gateway_id(
        workspace_id,
        session_ctx=session_ctx,
        requested_gateway_id=payload.get("gateway_id"),
        capability_id=hardware_capability_id,
    )
    trace_context = session_ctx.get("trace_context") if isinstance(session_ctx, dict) else None
    trace_id = (
        str(getattr(trace_context, "trace_id", "") or "").strip()
        or str(metadata.get("trace_id") or "").strip()
        or f"trace_{uuid.uuid4().hex}"
    )
    run_id = str(payload.get("run_id") or "").strip() or (
        f"direct_chat:{str(thread_id or 'thread').strip() or 'thread'}:hardware:{index}:{uuid.uuid4().hex}"
    )
    request_id = (
        _request_id_from_direct_tool_context(session_ctx)
        or str(payload.get("request_id") or payload.get("client_request_id") or "").strip()
        or run_id
    )
    runtime_target = _runtime_target_from_direct_tool_context(
        explicit_target=payload.get("runtime_target"),
        gateway_id=gateway_id,
        session_ctx=session_ctx,
    )
    normalized_action_id = action_id.strip().lower()
    local_gateway_required = _hardware_action_requires_local_gateway(
        action_id,
        payload.get("capability_id"),
    )
    if local_gateway_required and not gateway_id:
        return _hardware_action_offline_result(
            action_id, placement_status=_agent_placement_gateway_status(session_ctx)
        )
    if gateway_id and (
        local_gateway_required
        or normalized_action_id in {
            "screenshot",
            "screenshot.capture",
            "screen.capture",
            "screen.ocr",
            "ocr",
            "mouse",
            "keyboard",
            "mouse.click",
            "mouse.move",
            "keyboard.type",
            "keyboard.press",
            "input.click",
            "input.type",
            "app.focus",
            "window.control",
        }
        or normalized_action_id.startswith("computer_control.")
        or normalized_action_id.startswith("screenshot.")
        or normalized_action_id.startswith("screen.")
    ):
        runtime_target = "user_device_gateway"
    result = await hardware_action_broker_service.execute_hardware_action(
        tenant_id=_tenant_id_from_direct_tool_context(session_ctx),
        workspace_id=str(workspace_id or "default").strip() or "default",
        action_id=action_id,
        capability_id=payload.get("capability_id"),
        arguments=action_arguments,
        runtime_target=runtime_target,
        runtime_access_mode=_runtime_access_mode_from_direct_tool_context(
            explicit_mode=payload.get("runtime_access_mode") or payload.get("execution_mode") or payload.get("permission_mode"),
            explicit_target=runtime_target,
            gateway_id=gateway_id,
            session_ctx=session_ctx,
        ),
        agent_scope=_agent_scope_from_direct_tool_context(session_ctx),
        gateway_id=gateway_id,
        device_id=str(payload.get("device_id") or "").strip() or None,
        node_id=str(payload.get("node_id") or "").strip() or None,
        run_id=run_id,
        trace_id=trace_id,
        thread_id=str(thread_id or "").strip(),
        request_id=request_id,
        trace_context=trace_context,
        require_approval=False,
        file_mount_grants=metadata.get("file_mount_grants") if isinstance(metadata.get("file_mount_grants"), list) else None,
    )
    return _format_hardware_action_result(dict(result) if isinstance(result, dict) else {"status": "completed", "execution": {"result": result}})


def _safe_direct_shell_command(command: str) -> bool:
    compact = re.sub(r"\s+", " ", str(command or "").strip()).lower()
    if not compact:
        return False
    from server_modules import no_provider_service

    known_local_system_probe = re.sub(
        r"\s+",
        " ",
        no_provider_service.LOCAL_SYSTEM_INFO_SHELL_COMMAND.strip(),
    ).lower()
    if compact == known_local_system_probe:
        return True
    if any(token in compact for token in ("&&", "||", ";", "|", ">", "<", "$(", "`")):
        return False
    if re.fullmatch(r"echo\s+[a-z0-9][a-z0-9 .,_:/=@+-]{0,200}", compact):
        return True
    if any(
        re.search(rf"(^|\s){re.escape(token)}(\s|$)", compact)
        for token in (
            "rm",
            "mv",
            "cp",
            "chmod",
            "chown",
            "mkdir",
            "touch",
            "tee",
            "python",
            "python3",
            "node",
            "bash",
            "zsh",
            "sh",
            "kill",
            "xargs",
            "perl",
            "ruby",
            "git",
            "curl",
            "wget",
            "scp",
            "rsync",
        )
    ):
        return False
    allowed_patterns = (
        r"^ls(\s|$)",
        r"^pwd(\s|$)",
        r"^find\s+",
        r"^head(\s|$)",
        r"^tail(\s|$)",
        r"^cat\s+",
        r"^wc(\s|$)",
        r"^stat(\s|$)",
        r"^file(\s|$)",
        r"^du(\s|$)",
        r"^mdls(\s|$)",
        r"^tree(\s|$)",
        r"^rg(\s|$)",
        r"^grep(\s|$)",
        r"^sed\s+-n\b",
        r"^readlink(\s|$)",
        r"^dirname(\s|$)",
        r"^basename(\s|$)",
        r"^whoami(\s|$)",
        r"^uname(\s|$)",
        r"^hostname(\s|$)",
        r"^id(\s|$)",
        r"^env(\s|$)",
        r"^printenv(\s|$)",
        r"^which\s+",
        r"^whereis\s+",
        r"^uptime(\s|$)",
        r"^df(\s|$)",
        r"^ps(\s|$)",
        r"^groups(\s|$)",
    )
    return any(re.search(pattern, compact) for pattern in allowed_patterns)


def _local_direct_shell_worker_online_exact(workspace_id: str) -> bool:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    try:
        from server_modules import local_queue

        payload = local_queue.handle_get_local_workers_status()
    except Exception:
        return False
    items = payload.get("items") if isinstance(payload, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not bool(item.get("online")):
            continue
        worker_workspace = str(item.get("workspace_id") or "default").strip() or "default"
        if worker_workspace != normalized_workspace_id:
            continue
        capabilities = {
            str(capability or "").strip().lower()
            for capability in (item.get("capabilities") if isinstance(item.get("capabilities"), list) else [])
            if str(capability or "").strip()
        }
        if "shell.execute" in capabilities:
            return True
    return False


def _local_direct_shell_worker_online(workspace_id: str) -> bool:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    # No cross-workspace fallback: a worker online for "default" must not be
    # treated as available to every workspace just because we're in a
    # dev-like environment — that was the exact hole that let an unrelated
    # workspace reach another workspace's (or an unscoped) local worker.
    return _local_direct_shell_worker_online_exact(normalized_workspace_id)


def _local_dev_direct_shell_fallback_enabled(workspace_id: str) -> bool:
    if str(os.getenv("DATABASE_URL") or "").strip():
        return False
    env_name = str(os.getenv("ORION_ENV") or os.getenv("APP_ENV") or "").strip().lower()
    if env_name not in _LOCAL_DIRECT_TOOL_GATEWAY_FALLBACK_ENVS:
        return False
    # Being a dev-like environment is necessary but NOT sufficient: this
    # workspace must also have its own online worker. The previous
    # unconditional `is_local_dev()` bypass here handed direct shell
    # execution to EVERY workspace in a dev-like environment regardless of
    # which (if any) workspace a local worker was actually registered to —
    # confirmed to let unrelated/fresh workspaces reach the developer's own
    # machine. Restoring the worker check this bypass had deliberately
    # skipped ("added unnecessary friction for development").
    return _local_direct_shell_worker_online(workspace_id)


def _execute_local_dev_direct_shell_command(
    *,
    command: str,
    timeout_seconds: int,
    workspace_id: str,
    thread_id: str,
    index: int,
    callbacks: Any,
) -> str:
    completed = subprocess.run(
        command,
        shell=True,
        executable=os.getenv("SHELL") or "/bin/zsh",
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        timeout=max(1, min(int(timeout_seconds or 15), 60)),
    )
    stdout = str(completed.stdout or "").strip()
    stderr = str(completed.stderr or "").strip()
    if completed.returncode != 0:
        raise RuntimeError(stderr or f"Command failed with exit code {completed.returncode}: {command}")
    output_preview = stdout or stderr
    result = {
        "summary": f"Command completed: {command}",
        "result_data": {
            "local_child_run_id": f"direct-chat-local-dev:{str(thread_id or 'thread').strip() or 'thread'}:{index}",
            "tool_variant": "shell",
            "child_result": {
                "outputs": {
                    "actions": [
                        {
                            "tool": "run_command",
                            "command": command,
                            "status": "completed",
                            "exit_code": int(completed.returncode),
                            "stdout_preview": stdout,
                            "stderr_preview": stderr,
                            "output_preview": output_preview,
                            "workspace_id": str(workspace_id or "default").strip() or "default",
                        }
                    ],
                    "artifacts": [],
                }
            },
        },
    }
    return callbacks.format_direct_local_tool_result(result)


def _execute_safe_direct_local_tool_call(
    *,
    connector_id: str,
    action_id: str,
    argument_payload: Dict[str, Any],
    workspace_id: str,
    provider: Any,
    model: Any,
    credentials: Dict[str, Any] | None,
    thread_id: str,
    index: int,
    session_ctx: Dict[str, Any] | None,
    callbacks: Any,
) -> str:
    normalized_connector = str(connector_id or "").strip().lower()
    normalized_action = str(action_id or "").strip().lower()
    if normalized_connector == "file" and normalized_action not in {"read", "write"}:
        _raise_direct_chat_tool_execution_blocked()
    # `is_local_dev()` alone is a process-wide check with no notion of which
    # workspace is asking — every workspace passes it identically. This
    # workspace must ALSO have its own online local worker before it's handed
    # the in-process shortcut, mirroring gateway_adapter.registration_is_usable
    # (a worker with no workspace of its own must not serve every workspace).
    normalized_local_dev_workspace_id = str(workspace_id or "default").strip() or "default"
    if local_tool_executor.is_local_dev() and _local_direct_shell_worker_online_exact(
        normalized_local_dev_workspace_id
    ):
        if normalized_connector == "shell" and normalized_action in ("exec", "execute", "run"):
            result = local_tool_executor.shell_execute(
                str(argument_payload.get("command") or "")
            )
            return json.dumps(result, ensure_ascii=False)
        if normalized_connector == "file":
            if normalized_action == "read":
                result = local_tool_executor.filesystem_read(
                    str(argument_payload.get("path") or "")
                )
                return json.dumps(result, ensure_ascii=False)
            if normalized_action == "write":
                result = local_tool_executor.filesystem_write(
                    str(argument_payload.get("path") or ""),
                    str(argument_payload.get("content") or "")
                )
                return json.dumps(result, ensure_ascii=False)
            if normalized_action in ("list", "ls"):
                result = local_tool_executor.filesystem_list(
                    str(argument_payload.get("path") or "")
                )
                return json.dumps(result, ensure_ascii=False)
        if normalized_connector == "screenshot":
            pass
    if normalized_connector == "file" and normalized_action not in {"read", "write"}:
        _raise_direct_chat_tool_execution_blocked()
    _raise_direct_chat_tool_execution_blocked()


_BUILTIN_DIRECT_TOOL_IDS: frozenset = frozenset(
    {
        "",
        "http",
        "llm",
        "file",
        "shell",
        "screenshot",
        "computer",
        "hardware",
        "memory",
        "web",
        "browser",
        "image",
        "sage_service",
        # send_image (messaging.send_image) needs session_ctx for the current
        # channel/gateway context and the shared pending_outbound_media
        # accumulator — both only available on the builtin sync path
        # (execute_single_direct_tool_call). Without this it falls through to
        # _execute_custom_connector_tool_call_sync's OAuth-connector lookup,
        # which has no "messaging" connector registered and would error.
        "messaging",
        # subagent__spawn (2026-07-24 ruling) needs session_ctx to read/write
        # the per-task spawn counter and depth stamp -- same reasoning as
        # "messaging" above. Not currently reachable via the async path (the
        # live loop calls the sync closure in
        # direct_chat_operator_binding_service.py directly -- see that
        # module's execute_single_direct_tool_call docstring), added here
        # only so this set stays authoritative if that ever changes.
        "subagent",
        # project_task__* (2026-07-25, closing the platform-agent side of the
        # task-board loop — see docs/design/tasks-to-agents-research.md
        # Section 4.6): needs session_ctx to resolve the calling agent's own
        # identity/project, same reasoning as "messaging"/"subagent" above.
        "project_task",
        # document__* (feat/document-agent-tools): a project's owned
        # documents, same reasoning as "project_task" immediately above —
        # needs session_ctx to resolve the calling agent's own identity/
        # project. Without this, execute_single_direct_tool_call_async
        # would route document__* into _execute_custom_connector_tool_call_
        # sync's OAuth-connector lookup below, which has no "document"
        # connector registered and would error every call.
        "document",
        # goal__* (feat/agent-goals): a project's durable goals, same
        # reasoning as "project_task"/"document" immediately above — needs
        # session_ctx to resolve the calling agent's own identity/project.
        # Without this, execute_single_direct_tool_call_async would route
        # goal__* into _execute_custom_connector_tool_call_sync's
        # OAuth-connector lookup below, which has no "goal" connector
        # registered and would error every call.
        "goal",
    }
)


def _execute_custom_connector_tool_call_sync(
    *,
    tool_call: Dict[str, Any],
    workspace_id: str,
    thread_id: str,
    index: int = 1,
    provider: Any = None,
    model: Any = None,
    credentials: Dict[str, Any] | None = None,
    reasoning_effort: str = "",
    session_ctx: Dict[str, Any] | None = None,
    callbacks: Any = None,
    connector_id: str = "",
    action_id: str = "",
) -> str:
    """Execute a custom OAuth connector tool call synchronously inside a thread.

    This mirrors direct_chat_operator_binding_service.execute_single_direct_tool_call's
    gate for connector_id NOT in the built-in set — dispatches to
    runs_execution._workflow_execute_connector_action which uses urllib.request.urlopen
    (sync blocking I/O). Called via asyncio.to_thread() so the Uvicorn event loop
    stays responsive.
    """
    import json as _json

    from server_modules import runs_execution

    if callbacks is None:
        from server_modules.direct_chat_operator_binding_service import _direct_tool_execution_callbacks as _get_cb
        callbacks = _get_cb()

    argument_payload = callbacks.tool_arguments_payload(tool_call.get("arguments"))
    # Structured-schema tools (_structured_connector_tool_schema /
    # build_direct_chat_tools, above) hand back real named fields — e.g.
    # {"to": "...", "subject": "...", "body": "..."} — instead of the legacy
    # single opaque {"input": "<blob>"}. When that's what we got,
    # argument_payload itself already IS the structured data: pass it to
    # build_direct_tool_config as structured_args so it's consumed directly,
    # bypassing the tool_input regex-guessing path entirely for these calls.
    # tool_input is still computed (as a JSON dump) so the legacy fallback
    # branches inside build_direct_tool_config keep working unchanged for
    # actions that haven't been converted to a real schema yet.
    structured_args: Optional[Dict[str, Any]] = None
    if isinstance(argument_payload, dict):
        tool_input = str(argument_payload.get("input") or "").strip()
        if not tool_input:
            if argument_payload:
                structured_args = argument_payload
            try:
                tool_input = _json.dumps(argument_payload, ensure_ascii=False)
            except Exception:
                tool_input = str(argument_payload)
    else:
        tool_input = str(argument_payload or "").strip()

    # Called directly (not via callbacks.build_direct_tool_config) because
    # that callback closure has a fixed 3-positional-argument signature
    # (direct_chat_operator_binding_service.build_direct_chat_tool_support_bindings)
    # with no way to carry structured_args through. build_direct_tool_config
    # lives in this same module, so this is just a normal sibling call.
    from scripts.orion_local_worker_llm import parse_json_object_loose as _parse_json_object_loose

    config = build_direct_tool_config(
        connector_id,
        action_id,
        tool_input,
        parse_json_object_loose=_parse_json_object_loose,
        structured_args=structured_args,
    )
    session_metadata = session_ctx if isinstance(session_ctx, dict) else {}
    # Local dev: bypass gateway entirely, execute directly on this machine —
    # but ONLY when this specific workspace has its own online local worker.
    # `is_local_dev()` alone has no notion of which workspace is asking, so
    # without this check every workspace in a dev-like environment could
    # reach whichever machine the backend process itself runs on.
    _normalized_local_dev_workspace_id = str(workspace_id or "default").strip() or "default"
    if local_tool_executor.is_local_dev() and _local_direct_shell_worker_online_exact(
        _normalized_local_dev_workspace_id
    ):
        if connector_id == "shell" and action_id == "exec":
            result = local_tool_executor.shell_execute(
                str(argument_payload.get("command") or "")
            )
            return json.dumps(result, ensure_ascii=False)
        if connector_id == "file":
            if action_id == "read":
                result = local_tool_executor.filesystem_read(
                    str(argument_payload.get("path") or "")
                )
                return json.dumps(result, ensure_ascii=False)
            if action_id == "write":
                result = local_tool_executor.filesystem_write(
                    str(argument_payload.get("path") or ""),
                    str(argument_payload.get("content") or "")
                )
                return json.dumps(result, ensure_ascii=False)
            if action_id in ("list", "ls"):
                result = local_tool_executor.filesystem_list(
                    str(argument_payload.get("path") or "")
                )
                return json.dumps(result, ensure_ascii=False)

    result = runs_execution._workflow_execute_connector_action(
        "direct-chat-tool-call",
        "direct_chat_tool_call",
        {
            "workspace_id": workspace_id,
            "tenant_id": str(
                session_metadata.get("tenant_id")
                or (
                    session_metadata.get("agent_turn_request", {}).get("tenant_id")
                    if isinstance(session_metadata.get("agent_turn_request"), dict)
                    else ""
                )
                or "default"
            ).strip()
            or "default",
            "provider": provider,
            "model": model,
            "credentials": credentials if isinstance(credentials, dict) else None,
            "metadata": {},
        },
        config,
        current_text=str(config.get("text") or tool_input or "").strip(),
    )
    return callbacks.format_direct_tool_result(result)


def _tool_timeout_seconds(connector_id: str, action_id: str = "") -> float:
    """Per-category tool timeout in seconds.

    Prevents a hung tool from blocking the agent forever.  The model
    receives a timeout error it can reason about and adapt to.
    """
    cid = str(connector_id or "").strip().lower()
    if cid == "shell":
        return 120.0
    if cid == "web":
        return 30.0
    if cid == "memory":
        return 10.0
    if cid in ("browser", "computer"):
        return 60.0
    if cid == "hardware":
        return 120.0
    if cid == "mcp":
        # mcp_registry_service._call_streamable_http_tool_async() already
        # retries transient failures up to 3x with a 60s-per-attempt inner
        # timeout — give this outer wrapper enough room for at least one
        # retry to land instead of racing it.
        return 90.0
    return 30.0


def _timeout_tool_result(*, tool_name: str, timeout: float) -> str:
    """Return a structured error result so the model can adapt to the timeout."""
    import json as _json
    return _json.dumps({
        "error": "timeout",
        "message": (
            f"The tool '{tool_name}' timed out after {timeout:.0f}s. "
            "Try a different approach — a smaller scope, a different tool, or "
            "ask the user for more specific guidance."
        ),
    })


async def execute_single_direct_tool_call_async(
    *,
    tool_call: Dict[str, Any],
    workspace_id: str,
    thread_id: str,
    index: int = 1,
    provider: Any = None,
    model: Any = None,
    credentials: Dict[str, Any] | None = None,
    reasoning_effort: str = "",
    session_ctx: Dict[str, Any] | None = None,
    callbacks: Any = None,
) -> str:
    """Async version of execute_single_direct_tool_call for hardware-bound connectors.

    Hardware connectors (hardware, file, shell, screenshot, computer) go through
    run_async_tool_call's thread.join() which deadlocks the Uvicorn event loop when
    called from within an async context. This function directly awaits the async
    operations, avoiding the deadlock.

    Custom OAuth connectors (GitHub, Notion, Linear, Dropbox, Google Workspace,
    etc.) are dispatched through asyncio.to_thread() to avoid blocking the Uvicorn
    event loop with urllib.request.urlopen. Built-in connectors (memory, web,
    browser, etc.) continue to use the sync path which is safe (callback-based).

    All tool executions are wrapped with asyncio.wait_for() using per-category
    timeouts so a hung tool never blocks the agent forever.
    """
    if callbacks is None:
        from server_modules.direct_chat_operator_binding_service import _direct_tool_execution_callbacks as _get_cb
        callbacks = _get_cb()

    connector_id, action_id = callbacks.parse_tool_name(str(tool_call.get("name") or ""))
    mandate_allowed, mandate_tier, mandate_unattributed = _authority_mandate_gate(
        connector_id, action_id, session_ctx, tool_name=str(tool_call.get("name") or "")
    )
    if mandate_unattributed:
        try:
            from server_modules import activity_ledger_service

            await activity_ledger_service.append_activity_event(
                **_authority_mandate_unattributed_ledger_kwargs(
                    connector_id=connector_id,
                    action_id=action_id,
                    tool_name=str(tool_call.get("name") or ""),
                    workspace_id=workspace_id,
                    thread_id=thread_id,
                    session_ctx=session_ctx,
                )
            )
        except Exception:
            pass
    if not mandate_allowed:
        try:
            from server_modules import activity_ledger_service

            await activity_ledger_service.append_activity_event(
                **_authority_mandate_blocked_ledger_kwargs(
                    connector_id=connector_id,
                    action_id=action_id,
                    tool_name=str(tool_call.get("name") or ""),
                    tier=mandate_tier or "",
                    workspace_id=workspace_id,
                    thread_id=thread_id,
                    session_ctx=session_ctx,
                )
            )
        except Exception:
            pass
        raise RuntimeError(authority_mandate_service.MANDATE_BLOCKED_MESSAGE)
    timeout = _tool_timeout_seconds(connector_id, action_id)

    import asyncio as _asyncio

    # MCP-namespaced tool calls (mcp__<server_id>__<tool_name>) — Phase A
    # wiring (docs/design/mcp-applications-plan.md). This intercepts BEFORE
    # the builtin/custom-connector branching below, because "mcp" is not in
    # _BUILTIN_DIRECT_TOOL_IDS and would otherwise silently fall into the
    # custom-OAuth-connector branch (_execute_custom_connector_tool_call_sync
    # -> runs_execution._workflow_execute_connector_action), which has no
    # notion of MCP servers/endpoints. The authority-mandate gate above
    # already ran for this call (mandate_allowed check, lines above) — this
    # branch only adds the MCP-specific approval gate on top, enforced
    # inside invoke_workspace_mcp_tool_async() itself
    # (_assert_tool_approved_for_execution), never bypassed here.
    if connector_id == "mcp":
        from server_modules import mcp_registry_service

        if not mcp_registry_service.mcp_tools_enabled():
            raise RuntimeError("MCP tools are disabled for this deployment.")
        parsed_mcp = mcp_registry_service.parse_mcp_tool_name(str(tool_call.get("name") or ""))
        if parsed_mcp is None:
            raise RuntimeError(f"Malformed MCP tool name '{tool_call.get('name')}'.")
        mcp_arguments = callbacks.tool_arguments_payload(tool_call.get("arguments"))
        session_metadata = session_ctx if isinstance(session_ctx, dict) else {}
        metadata = _direct_tool_session_metadata(session_ctx)
        try:
            mcp_result = await _asyncio.wait_for(
                mcp_registry_service.invoke_workspace_mcp_tool_async(
                    workspace_id=workspace_id,
                    server_id=parsed_mcp["server_id"],
                    tool_name=parsed_mcp["tool_name"],
                    arguments=mcp_arguments if isinstance(mcp_arguments, dict) else {},
                    agent_label=str(metadata.get("sage_agent_id") or metadata.get("agent_scope") or "Agent"),
                    tenant_id=_tenant_id_from_direct_tool_context(session_ctx),
                    thread_id=str(thread_id or "").strip() or None,
                    run_id=_request_id_from_direct_tool_context(session_ctx) or None,
                    user_id=str(session_metadata.get("sender_id") or "").strip() or None,
                    agent_id=str(session_metadata.get("agent_id") or metadata.get("agent_id") or "").strip() or None,
                ),
                timeout=timeout,
            )
        except _asyncio.TimeoutError:
            return _timeout_tool_result(
                tool_name=str(tool_call.get("name") or f"{connector_id}__{action_id}"),
                timeout=timeout,
            )
        return mcp_registry_service.format_mcp_tool_result(mcp_result)

    # Hardware-bound connectors: async path (handled below)
    if connector_id not in {"hardware", "file", "shell", "screenshot", "computer"}:
        if connector_id in _BUILTIN_DIRECT_TOOL_IDS:
            # Built-in connectors (memory, web, browser, image, etc.): execute in
            # a thread so we can apply a timeout.
            try:
                return await _asyncio.wait_for(
                    _asyncio.to_thread(
                        execute_single_direct_tool_call,
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
                    ),
                    timeout=timeout,
                )
            except _asyncio.TimeoutError:
                return _timeout_tool_result(
                    tool_name=str(tool_call.get("name") or f"{connector_id}__{action_id}"),
                    timeout=timeout,
                )
        # Custom OAuth connectors (GitHub, Notion, Linear, Dropbox, Google
        # Workspace, and any future connector): execute in a thread to avoid
        # blocking the Uvicorn event loop with urllib.request.urlopen.
        try:
            return await _asyncio.wait_for(
                _asyncio.to_thread(
                    _execute_custom_connector_tool_call_sync,
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
                    connector_id=connector_id,
                    action_id=action_id,
                ),
                timeout=timeout,
            )
        except _asyncio.TimeoutError:
            return _timeout_tool_result(
                tool_name=str(tool_call.get("name") or f"{connector_id}__{action_id}"),
                timeout=timeout,
            )

    argument_payload = callbacks.tool_arguments_payload(tool_call.get("arguments"))
    session_metadata = session_ctx if isinstance(session_ctx, dict) else {}
    try:
        from server_modules import activity_ledger_service

        await activity_ledger_service.append_execution_activity(
            tenant_id=_tenant_id_from_direct_tool_context(session_ctx),
            workspace_id=str(workspace_id or "default").strip() or "default",
            agent_id=str(session_metadata.get("agent_id") or session_metadata.get("user_id") or "sage").strip() or "sage",
            tool=f"{connector_id}.{action_id}".strip("."),
            args_summary=argument_payload if isinstance(argument_payload, dict) else {},
            result_status="started",
            execution_tier=str(session_metadata.get("execution_tier") or session_metadata.get("runtime_target") or "direct").strip() or "direct",
            thread_id=str(thread_id or "").strip() or None,
            metadata={"source": "execute_single_direct_tool_call_async"},
        )
    except Exception:
        pass

    if connector_id == "hardware" and action_id == "action":
        try:
            return await _asyncio.wait_for(
                _execute_hardware_action_tool_call_async(
                    argument_payload=argument_payload if isinstance(argument_payload, dict) else {},
                    workspace_id=workspace_id,
                    thread_id=thread_id,
                    index=index,
                    session_ctx=session_ctx,
                ),
                timeout=timeout,
            )
        except _asyncio.TimeoutError:
            return _timeout_tool_result(
                tool_name=str(tool_call.get("name") or f"{connector_id}__{action_id}"),
                timeout=timeout,
            )

    if connector_id in {"file", "shell", "screenshot", "computer"}:
        normalized_connector = str(connector_id or "").strip().lower()
        normalized_action = str(action_id or "").strip().lower()
        if normalized_connector == "file" and normalized_action not in {"read", "write"}:
            _raise_direct_chat_tool_execution_blocked()
        if normalized_connector == "shell":
            if normalized_action != "exec":
                _raise_direct_chat_tool_execution_blocked()
        elif normalized_connector not in {"file", "screenshot", "computer"}:
            _raise_direct_chat_tool_execution_blocked()

        # ARCHIVED (Phase U1): supervisor local-dev shortcut removed.
        # The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.
        # All tool execution routes through the gateway WebSocket path.

        gateway_capability_id = _gateway_capability_for_direct_local_tool(
            normalized_connector,
            normalized_action,
        )
        gateway_id = _resolve_direct_tool_gateway_id(
            workspace_id,
            session_ctx=session_ctx,
            capability_id=gateway_capability_id,
        )
        if gateway_capability_id and gateway_id:
            # Gateway path: use async to avoid deadlock
            session_payload = session_ctx if isinstance(session_ctx, dict) else {}
            metadata = _direct_tool_session_metadata(session_ctx)
            tenant_id = _tenant_id_from_direct_tool_context(session_ctx)
            trace_context = session_payload.get("trace_context")
            # SECURITY: filesystem.read_write and shell.execute on the
            # Gateway share one on-box directory per (mount, workspace_id) —
            # see docs/design/memory-placement-scope.md's "gateway seam"
            # section and PLATFORM-MAP.md Part 27.8 (the identical leak
            # class, for connector credentials). gateway_adapter now folds
            # the CALLING agent's own identity into that mount server-side
            # (never from anything the model/caller supplied, and never read
            # from the tool call's own `arguments` — only from verified
            # session_ctx) so two SPECIALIST agent installs sharing a
            # workspace + Gateway box can't reach each other's files through
            # this connector.
            #
            # Deliberately NOT a hard fail-closed gate on empty identity,
            # unlike commit 8cc8d69dd's skill_invoke-routed memory
            # executors: verified (agent_turn_runtime_service.py's
            # _run_sage_action_loop_v3, 3 call sites, all commented "empty
            # for Sage") that the owner-facing agent's OWN turn — the
            # primary, highest-volume caller of this exact connector when a
            # box is paired — never has active_agent_install_id/
            # agent_install_id set in session_ctx at all. A hard fail here
            # would break Sage's own file/shell tool use outright, not just
            # a specialist edge case. This mirrors PLATFORM-MAP.md's Part
            # 27.1 precedent for memory: "an empty agent_install_id does not
            # mean 'no scope' — it resolves to the WORKSPACE ROOT ...
            # intentional and correct for the owner-facing agent's own
            # turns." Empty here is a stable, server-controlled signal
            # ("this is Sage's own turn," never model-forgeable) rather than
            # a "we don't know who's asking" ambiguity — gateway_adapter's
            # _agent_scoped_mount leaves the mount unchanged (today's
            # existing, workspace-level bucket) when it's empty, and scopes
            # it per-agent whenever a real specialist identity resolves.
            resolved_agent_install_id = _agent_install_id_from_direct_tool_context(session_ctx)
            gateway_arguments = _gateway_arguments_for_direct_local_tool(
                normalized_connector,
                normalized_action,
                argument_payload if isinstance(argument_payload, dict) else {},
            )
            gateway_run_id = (
                f"direct_chat:{str(thread_id or 'thread').strip() or 'thread'}:{index}:{uuid.uuid4().hex}"
            )
            gateway_request_id = _request_id_from_direct_tool_context(session_ctx) or gateway_run_id
            gateway_trace_id = (
                str(getattr(trace_context, "trace_id", "") or "").strip()
                or str(metadata.get("trace_id") or "").strip()
                or f"trace_{uuid.uuid4().hex}"
            )
            approval_override = False
            try:
                gateway_response = await _asyncio.wait_for(
                    _execute_direct_tool_via_gateway_async(
                        gateway_id=gateway_id,
                        capability_id=gateway_capability_id,
                        arguments=gateway_arguments,
                        run_id=gateway_run_id,
                        trace_id=gateway_trace_id,
                        workspace_id=str(workspace_id or "default").strip() or "default",
                        runtime_target=_runtime_target_from_direct_tool_context(
                            gateway_id=gateway_id,
                            session_ctx=session_ctx,
                        ),
                        runtime_access_mode=_runtime_access_mode_from_direct_tool_context(
                            gateway_id=gateway_id,
                            session_ctx=session_ctx,
                        ),
                        agent_scope=_agent_scope_from_direct_tool_context(session_ctx),
                        agent_install_id=resolved_agent_install_id or None,
                        tenant_id=tenant_id,
                        thread_id=str(thread_id or "").strip(),
                        request_id=gateway_request_id,
                        session_ctx=session_ctx,
                        require_approval=approval_override,
                    ),
                    timeout=timeout,
                )
            except _asyncio.TimeoutError:
                return _timeout_tool_result(
                    tool_name=str(tool_call.get("name") or f"{connector_id}__{action_id}"),
                    timeout=timeout,
                )
            return _format_gateway_direct_local_tool_result(
                connector_id=normalized_connector,
                action_id=normalized_action,
                capability_id=gateway_capability_id,
                gateway_response=gateway_response,
                callbacks=callbacks,
            )

        # Gateway not available: fall through to the sync path
        # (Supervisor, local dev, runs_execution — none of these deadlock)
        return execute_single_direct_tool_call(
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

    _raise_direct_chat_tool_execution_blocked()

    if normalized_connector == "shell":
        if normalized_action != "exec":
            _raise_direct_chat_tool_execution_blocked()
    elif normalized_connector not in {"file", "screenshot", "computer"}:
        _raise_direct_chat_tool_execution_blocked()

    # ARCHIVED (Phase U1): supervisor local-dev shortcut removed.
    # The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.

    gateway_capability_id = _gateway_capability_for_direct_local_tool(
        normalized_connector,
        normalized_action,
    )
    gateway_id = _resolve_direct_tool_gateway_id(
        workspace_id,
        session_ctx=session_ctx,
        capability_id=gateway_capability_id,
    )
    if not _gateway_skip and gateway_capability_id and gateway_id:
        session_payload = session_ctx if isinstance(session_ctx, dict) else {}
        metadata = _direct_tool_session_metadata(session_ctx)
        tenant_id = _tenant_id_from_direct_tool_context(session_ctx)
        trace_context = session_payload.get("trace_context")
        gateway_arguments = _gateway_arguments_for_direct_local_tool(
            normalized_connector,
            normalized_action,
            argument_payload if isinstance(argument_payload, dict) else {},
        )
        gateway_run_id = (
            f"direct_chat:{str(thread_id or 'thread').strip() or 'thread'}:{index}:{uuid.uuid4().hex}"
        )
        gateway_request_id = _request_id_from_direct_tool_context(session_ctx) or gateway_run_id
        gateway_trace_id = (
            str(getattr(trace_context, "trace_id", "") or "").strip()
            or str(metadata.get("trace_id") or "").strip()
            or f"trace_{uuid.uuid4().hex}"
        )
        approval_override: Optional[bool] = False
        gateway_response = _execute_direct_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=gateway_capability_id,
            arguments=gateway_arguments,
            run_id=gateway_run_id,
            trace_id=gateway_trace_id,
            workspace_id=str(workspace_id or "default").strip() or "default",
            runtime_target=_runtime_target_from_direct_tool_context(
                gateway_id=gateway_id,
                session_ctx=session_ctx,
            ),
            runtime_access_mode=_runtime_access_mode_from_direct_tool_context(
                gateway_id=gateway_id,
                session_ctx=session_ctx,
            ),
            agent_scope=_agent_scope_from_direct_tool_context(session_ctx),
            tenant_id=tenant_id,
            thread_id=str(thread_id or "").strip(),
            request_id=gateway_request_id,
            session_ctx=session_ctx,
            require_approval=approval_override,
            callbacks=callbacks,
        )
        return _format_gateway_direct_local_tool_result(
            connector_id=normalized_connector,
            action_id=normalized_action,
            capability_id=gateway_capability_id,
            gateway_response=gateway_response,
            callbacks=callbacks,
        )

    # ARCHIVED (Phase U1): supervisor direct tool execution path removed.
    # The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.
    # Desktop control tools (computer/screenshot/clipboard/applescript) are OUT of scope.
    # Local file read/write and shell exec now go through the standard fallback path below.
    variant, config = callbacks.build_direct_local_tool_config(
        normalized_connector,
        normalized_action,
        argument_payload,
    )
    if not isinstance(config, dict):
        _raise_direct_chat_tool_execution_blocked()
    config = dict(config)
    config.setdefault("execution_target", "local_companion")

    if (
        normalized_connector == "shell"
        and normalized_action == "exec"
        and _safe_direct_shell_command(str(argument_payload.get("command") or ""))
        and _local_dev_direct_shell_fallback_enabled(workspace_id)
    ):
        return _execute_local_dev_direct_shell_command(
            command=str(argument_payload.get("command") or "").strip(),
            timeout_seconds=int(
                argument_payload.get("timeout_seconds")
                or argument_payload.get("timeout")
                or 15
            ),
            workspace_id=workspace_id,
            thread_id=thread_id,
            index=index,
            callbacks=callbacks,
        )

    session_payload = session_ctx if isinstance(session_ctx, dict) else {}
    agent_turn_request = session_payload.get("agent_turn_request") if isinstance(session_payload.get("agent_turn_request"), dict) else {}
    metadata = _direct_tool_session_metadata(session_ctx)
    tenant_id = str(
        session_payload.get("tenant_id")
        or agent_turn_request.get("tenant_id")
        or metadata.get("tenant_id")
        or "default"
    ).strip() or "default"

    from server_modules import runs_execution

    result = runs_execution._workflow_execute_local_tool(
        "direct-chat-local-tool",
        {
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "provider": provider,
            "model": model,
            "credentials": credentials if isinstance(credentials, dict) else None,
            "metadata": metadata,
        },
        config,
        label=f"{normalized_connector}__{normalized_action}",
        variant=str(variant or normalized_connector),
        current_text=str(config.get("path") or config.get("command") or "").strip(),
    )
    return callbacks.format_direct_local_tool_result(result)


# ── Outbound media (send_image / generate_image auto-attach) ──────────────
#
# Root directory under which locally-generated media is eligible for
# send_image's local-path acceptance. Deliberately the SAME root
# tools_image_gen.DEFAULT_OUTPUT_DIR's parent lives under: confining
# send_image to files inside it (rather than any path the model names) is
# what keeps "send a local file" from becoming an arbitrary-file-read /
# exfiltration primitive — a compromised or prompt-injected turn could
# otherwise ask send_image("~/.ssh/id_rsa") or send_image("/app/.env") and
# have the bytes read off disk and delivered to whoever is on the other end
# of the chat. generate_image's OWN auto-attach (below) does not need this
# check: it only ever attaches paths it just wrote itself, i.e. freshly
# generated image bytes, never a pre-existing file's original content.
_SEND_IMAGE_SAFE_ROOT_DIRNAME = ".orion-stack"
# Matches the WhatsApp/Telegram gateway outbound media caps
# (WHATSAPP_MEDIA_MAX_BYTES in empyralis-gateway/src/channels/whatsapp/runtime.ts).
_SEND_IMAGE_MAX_LOCAL_BYTES = 25 * 1024 * 1024
# Mirrors generate_image's own `n` parameter ceiling (ToolDescriptor:
# minimum 1, maximum 4) — caps how many images one generate_image call can
# queue for auto-attach, so a single tool call can't balloon the reply.
_MAX_AUTO_ATTACH_IMAGES = 4


def _send_image_safe_root() -> Path:
    root = Path.cwd() / _SEND_IMAGE_SAFE_ROOT_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_send_image_local_path(raw_path: str) -> Path:
    """Resolve+validate a local path for send_image, confined to the
    .orion-stack/ generated-content root. Raises ValueError for anything
    that isn't a plain, existing, non-empty, size-capped file inside that
    root — including a symlink that resolves outside it, since the
    containment check below runs AFTER Path.resolve() follows symlinks.
    Mirrors agent_memory_tools._resolve_safe_path's containment idiom.
    """
    candidate = str(raw_path or "").strip()
    if not candidate:
        raise ValueError("path_or_url is required")
    safe_root = _send_image_safe_root().resolve()
    resolved = Path(candidate).expanduser()
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved
    resolved = resolved.resolve()
    if not str(resolved).startswith(str(safe_root) + os.sep):
        raise ValueError(
            f"Local path must be inside {_SEND_IMAGE_SAFE_ROOT_DIRNAME}/ (e.g. "
            "generate_image's own output path) — arbitrary filesystem paths "
            "cannot be sent. Pass a public URL instead if the file lives elsewhere."
        )
    if not resolved.is_file():
        raise ValueError(f"Local file not found: {candidate}")
    size = resolved.stat().st_size
    if size <= 0:
        raise ValueError(f"Local file is empty: {candidate}")
    if size > _SEND_IMAGE_MAX_LOCAL_BYTES:
        raise ValueError(
            f"Local file too large to send ({size} bytes, max {_SEND_IMAGE_MAX_LOCAL_BYTES})."
        )
    return resolved


def _media_kind_for_mime(mime_type: str) -> str:
    """Maps a MIME type to the gateway's media-kind vocabulary (image, video,
    audio, file — see empyralis-gateway/src/protocol/types.ts's
    GatewayChannelMediaKind). "voice" is never inferred here — it's an
    explicit sender choice (as_voice), not derivable from a MIME type alone.
    """
    normalized = str(mime_type or "").split(";")[0].strip().lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    if normalized.startswith("audio/"):
        return "audio"
    return "file"


def _session_channel_origin(session_ctx: Optional[Dict[str, Any]]) -> str:
    """Returns the current turn's channel_origin (e.g. "whatsapp_personal",
    "telegram_personal") when this tool call is running inside a real
    messaging-channel turn, or "" for a web-chat/dashboard/no-channel turn
    (channel_origin defaults to the literal "sage" there — see
    _run_sage_action_loop_v3's session_ctx construction). Used to gate
    generate_image's auto-attach: there is no channel to attach a reply
    attachment TO outside a channel turn.
    """
    if not isinstance(session_ctx, dict):
        return ""
    metadata = session_ctx.get("metadata")
    if isinstance(metadata, dict):
        origin = str(metadata.get("channel_origin") or "").strip()
        if origin and origin != "sage":
            return origin
    return ""


def _queue_outbound_media(session_ctx: Optional[Dict[str, Any]], item: Dict[str, Any]) -> bool:
    """Appends one media item to the turn's shared pending_outbound_media
    accumulator (see _run_sage_action_loop_v3's session_ctx construction and
    handle_sage_chat's "media" response key). Returns False (does nothing)
    when session_ctx isn't a dict — there is no turn to attach to, which
    callers surface to the model rather than silently dropping the file.
    """
    if not isinstance(session_ctx, dict):
        return False
    session_ctx.setdefault("pending_outbound_media", []).append(item)
    return True


def _resolve_session_user_id(session_metadata: Any) -> str:
    """The turn's SERVER-RESOLVED human identity, or "".

    `session_metadata` is the whole `session_ctx` dict (every call site in
    this module does `session_metadata = session_ctx`), and production does
    NOT put `user_id` at its top level — `agent_turn_runtime_service`'s turn
    builder nests it:

        session_ctx = {
            "metadata": {"user_id": actor_user_id or None, ...},
            "sender_id": actor_user_id or "",
            ...
        }

    So a bare `session_metadata.get("user_id")` is None on every real turn.
    That is not hypothetical: it silently disabled BOTH private-memory tools
    the day they shipped (2026-08-12) — every live call raised "requires a
    resolved user identity", and the dispatch test passed only because it
    hand-built a flat `{"user_id": ...}` shape production never produces. A
    fixture that invents its own input cannot notice that the real input
    looks different; this helper exists so there is ONE answer to "who is
    this turn's human", used by every caller, instead of each one guessing a
    key.

    Order is authority, not convenience: the nested `metadata.user_id` is
    what the turn builder sets deliberately, `sender_id` is its top-level
    mirror, and the flat `user_id` is accepted last for callers that pass a
    pre-flattened context.

    Every source here is written by the platform. None of them is reachable
    from a tool call's own arguments, which is the property the private
    memory tools depend on — see their dispatch branches.
    """
    if not isinstance(session_metadata, dict):
        return ""
    nested = session_metadata.get("metadata")
    if isinstance(nested, dict):
        candidate = str(nested.get("user_id") or "").strip()
        if candidate:
            return candidate
    for key in ("sender_id", "user_id"):
        candidate = str(session_metadata.get(key) or "").strip()
        if candidate:
            return candidate
    return ""


def _memory_redaction_result_fields(saved: Any) -> Dict[str, Any]:
    """MAN-53: every native memory-writing tool result (memory_write,
    memory_update, memory_append_daily_note, memory_apply_edit) surfaces
    whether memory_service redacted a live secret out of the content before
    it touched disk — never silent. `saved` is whatever the memory_service
    call returned; a non-dict or a dict with no `redacted` key (an older
    caller/mock) is treated as "not redacted" rather than raising, so this
    is purely additive to every one of this function's call sites.
    The note text is model-facing wording, not hardcoded agent speech --
    the model reads it and decides how to phrase it to the user (e.g. "I
    saved that but removed the key")."""
    redacted = bool(saved.get("redacted")) if isinstance(saved, dict) else False
    fields: Dict[str, Any] = {"redacted": redacted}
    if redacted:
        fields["redaction_note"] = (
            "Part of this content looked like a live secret (an API key, "
            "token, password, or similar) and was replaced with a "
            "redaction placeholder before saving. Tell the user what you "
            "saved, but mention the secret itself was removed, not stored."
        )
    return fields


def execute_single_direct_tool_call(
    *,
    tool_call: Dict[str, Any],
    workspace_id: str,
    thread_id: str,
    index: int = 1,
    provider: Any = None,
    model: Any = None,
    credentials: Dict[str, Any] | None = None,
    reasoning_effort: str = "",
    session_ctx: Dict[str, Any] | None = None,
    callbacks: Any,
) -> str:
    from server_modules.tools_image_gen import generate_image as run_generate_image
    from server_modules import sage_services_service

    connector_id, action_id = callbacks.parse_tool_name(str(tool_call.get("name") or ""))
    mandate_allowed, mandate_tier, mandate_unattributed = _authority_mandate_gate(
        connector_id, action_id, session_ctx, tool_name=str(tool_call.get("name") or "")
    )
    if mandate_unattributed:
        try:
            from server_modules import activity_ledger_service

            callbacks.run_async_tool_call(
                activity_ledger_service.append_activity_event(
                    **_authority_mandate_unattributed_ledger_kwargs(
                        connector_id=connector_id,
                        action_id=action_id,
                        tool_name=str(tool_call.get("name") or ""),
                        workspace_id=workspace_id,
                        thread_id=thread_id,
                        session_ctx=session_ctx,
                    )
                )
            )
        except Exception:
            pass
    if not mandate_allowed:
        try:
            from server_modules import activity_ledger_service

            callbacks.run_async_tool_call(
                activity_ledger_service.append_activity_event(
                    **_authority_mandate_blocked_ledger_kwargs(
                        connector_id=connector_id,
                        action_id=action_id,
                        tool_name=str(tool_call.get("name") or ""),
                        tier=mandate_tier or "",
                        workspace_id=workspace_id,
                        thread_id=thread_id,
                        session_ctx=session_ctx,
                    )
                )
            )
        except Exception:
            pass
        raise RuntimeError(authority_mandate_service.MANDATE_BLOCKED_MESSAGE)
    argument_payload = callbacks.tool_arguments_payload(tool_call.get("arguments"))
    session_metadata = session_ctx if isinstance(session_ctx, dict) else {}
    tenant_id = str(
        session_metadata.get("tenant_id")
        or (
            session_metadata.get("agent_turn_request", {}).get("tenant_id")
            if isinstance(session_metadata.get("agent_turn_request"), dict)
            else ""
        )
        or "default"
    ).strip() or "default"
    if connector_id == "http" and action_id == "request":
        _raise_direct_chat_tool_execution_blocked()
    try:
        from server_modules import activity_ledger_service

        callbacks.run_async_tool_call(
            activity_ledger_service.append_execution_activity(
                tenant_id=tenant_id,
                workspace_id=str(workspace_id or "default").strip() or "default",
                agent_id=str(session_metadata.get("agent_id") or session_metadata.get("user_id") or "sage").strip() or "sage",
                tool=f"{connector_id}.{action_id}".strip("."),
                args_summary=argument_payload if isinstance(argument_payload, dict) else {},
                result_status="started",
                execution_tier=str(session_metadata.get("execution_tier") or session_metadata.get("runtime_target") or "direct").strip() or "direct",
                thread_id=str(thread_id or "").strip() or None,
                metadata={"source": "execute_single_direct_tool_call"},
            )
        )
    except Exception:
        pass
    if connector_id == "image" and action_id == "generate":
        from server_modules import agent_capability_service

        # Empty agent_id == master/Sage's own turn (same convention as
        # agent_turn_runtime_service._acting_install_id) — resolved against
        # Sage's own capability_config, not silently shared with specialists.
        capability_agent_id = str(session_metadata.get("agent_id") or "").strip()
        resolution = callbacks.run_async_tool_call(
            agent_capability_service.resolve_agent_capability_provider_by_id(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                agent_id=capability_agent_id,
                capability=agent_capability_service.IMAGE_GENERATION,
            )
        )
        if not resolution.available:
            raise RuntimeError(
                resolution.message
                or "Image generation isn't set up for this agent yet — add a provider on its Capabilities tab."
            )

        # The resolved provider is the source of truth for which backend
        # serves this call — a BYOK/platform key for "stability" must never
        # silently route to OpenAI just because the LLM's tool-call defaulted
        # to "dall-e-3" (the schema's enum default). Same-provider model
        # choice (dall-e-3 vs dall-e-2) still passes through untouched.
        requested_model = str(argument_payload.get("model") or "").strip().lower()
        if resolution.provider == "stability":
            effective_model = "stable-diffusion"
        else:
            effective_model = requested_model if requested_model in {"dall-e-3", "dall-e-2"} else "dall-e-3"

        saved_images = run_generate_image(
            prompt=argument_payload.get("prompt") or "",
            model=effective_model,
            size=argument_payload.get("size") or "1024x1024",
            quality=argument_payload.get("quality") or "standard",
            n=argument_payload.get("n") or 1,
            save_to=argument_payload.get("save_to"),
            api_key=str((resolution.credentials or {}).get("api_key") or "") or None,
        )
        if resolution.billing_mode == "platform_credits":
            try:
                callbacks.run_async_tool_call(
                    agent_capability_service.meter_platform_capability_usage(
                        tenant_id=tenant_id,
                        workspace_id=str(workspace_id or "default").strip() or "default",
                        agent_id=capability_agent_id,
                        capability=agent_capability_service.IMAGE_GENERATION,
                        provider=resolution.provider,
                        metadata={"n": len(saved_images), "model": effective_model},
                    )
                )
            except Exception:
                pass
        # Auto-attach: in a real messaging-channel turn (Telegram/WhatsApp —
        # see _session_channel_origin), a generated image auto-attaches to
        # the agent's reply, matching OpenClaw's model. Outside a channel
        # turn (web chat / dashboard) there is nothing to attach it TO, so
        # the reply just references the saved path as before. Capped at
        # _MAX_AUTO_ATTACH_IMAGES so a single call can't balloon the reply.
        _channel_origin = _session_channel_origin(session_ctx)
        _auto_attached = 0
        if _channel_origin and saved_images:
            for _image_path in saved_images[:_MAX_AUTO_ATTACH_IMAGES]:
                _mime_type = mimetypes.guess_type(str(_image_path))[0] or "image/png"
                if _queue_outbound_media(
                    session_ctx,
                    {"kind": "image", "source_path": str(_image_path), "mime_type": _mime_type},
                ):
                    _auto_attached += 1
        _summary_lines = [f"Generated {len(saved_images)} image(s):", *[f"{tool_index}. {path}" for tool_index, path in enumerate(saved_images, start=1)]]
        if _auto_attached:
            _summary_lines.append(
                f"({_auto_attached} image(s) queued to send with your reply on {_channel_origin} — "
                "no need to also call send_image for these.)"
            )
        return "\n".join(_summary_lines).strip()
    if connector_id == "messaging" and action_id == "send_image":
        raw_target = str(
            argument_payload.get("path_or_url") or argument_payload.get("url") or ""
        ).strip()
        if not raw_target:
            raise RuntimeError("send_image requires path_or_url.")
        caption = str(argument_payload.get("caption") or "").strip() or None
        is_url = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", raw_target))
        if is_url:
            guessed_mime, _ = mimetypes.guess_type(raw_target)
            media_item: Dict[str, Any] = {
                "kind": _media_kind_for_mime(guessed_mime) if guessed_mime else "file",
                "source_url": raw_target,
            }
            if guessed_mime:
                media_item["mime_type"] = guessed_mime
        else:
            resolved_path = _resolve_send_image_local_path(raw_target)
            mime_type = mimetypes.guess_type(str(resolved_path))[0] or "application/octet-stream"
            media_item = {
                "kind": _media_kind_for_mime(mime_type),
                "source_path": str(resolved_path),
                "mime_type": mime_type,
            }
        if caption:
            media_item["caption"] = caption
        if not _queue_outbound_media(session_ctx, media_item):
            raise RuntimeError(
                "send_image requires an active messaging-channel turn — there is no "
                "channel to send through right now."
            )
        return (
            f"Queued {media_item['kind']} to send with your reply: {raw_target}"
            + (f" (caption: {caption})" if caption else "")
        )
    if connector_id == "skill" and action_id == "invoke":
        # Level-2 dispatch: the model gets a skill_id (and optional args)
        # from the Level-1 catalog listing in the system prompt
        # (sage_skills_api._skill_capability_records, unified from
        # skill_registry.list_skill_definitions) and this is the ONE call
        # site that turns it into a real execution — mirrors memory_search's
        # pattern immediately above rather than inventing a new dispatch
        # shape. skill_registry.execute_skill already does the right thing
        # (executor -> handler subprocess -> MCP tool -> bundled-tool
        # dispatch -> SKILL.md body-injection fallback); this branch's only
        # job is argument plumbing and turning its structured result into a
        # tool-result string.
        skill_id = str(argument_payload.get("skill_id") or argument_payload.get("id") or "").strip()
        if not skill_id:
            raise RuntimeError("Tool 'skill_invoke' requires a skill_id.")
        raw_skill_args = argument_payload.get("args")
        if isinstance(raw_skill_args, dict):
            skill_goal = str(raw_skill_args.get("goal") or raw_skill_args.get("input") or "").strip()
            if not skill_goal and raw_skill_args:
                skill_goal = json.dumps(raw_skill_args, ensure_ascii=False)
        elif raw_skill_args is not None and str(raw_skill_args).strip():
            skill_goal = str(raw_skill_args).strip()
        else:
            skill_goal = str(argument_payload.get("goal") or "").strip()
        from server_modules import skill_registry

        skill_result = callbacks.run_async_tool_call(
            skill_registry.execute_skill(
                skill_id=skill_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                goal=skill_goal,
                agent_label=str(
                    session_metadata.get("sage_agent_id")
                    or session_metadata.get("agent_scope")
                    or "Agent"
                ).strip()
                or "Agent",
                hard_context="",
                operational_policy="",
                agent_id=str(session_metadata.get("agent_id") or "").strip(),
                agent_install_id=str(
                    session_metadata.get("agent_install_id")
                    or session_metadata.get("active_agent_install_id")
                    or ""
                ).strip(),
            )
        )
        if not isinstance(skill_result, dict):
            return str(skill_result or "").strip() or f"Skill '{skill_id}' returned no output."
        reply_text = str(skill_result.get("reply") or "").strip()
        artifact = skill_result.get("artifact") if isinstance(skill_result.get("artifact"), dict) else None
        result_parts = [reply_text] if reply_text else []
        if artifact:
            # This is the actual progressive-disclosure payoff: a
            # SKILL.md-backed skill's body only ever reaches context here,
            # on invoke — never as part of the Level-1 listing.
            preview = str(artifact.get("preview_content") or "").strip()
            if preview:
                artifact_label = str(artifact.get("label") or "Skill content").strip()
                result_parts.append(f"\n--- {artifact_label} ---\n{preview}")
        combined_reply = "\n".join(result_parts).strip()
        return combined_reply or f"Skill '{skill_id}' completed with status {skill_result.get('status')}."
    if connector_id == "skill" and action_id == "write":
        # Human-reviewed self-authoring (docs/design/audit-skills.md §1.4,
        # §3 item 9): reuses the existing, previously agent-unreachable
        # marketplace pipeline (skills_registry.install_marketplace_skill +
        # skill_scanner, already wired for the admin HTTP routes) rather
        # than a new bespoke write path, then immediately downgrades the
        # freshly installed skill to disabled/pending — see
        # skills_registry.author_pending_skill for why this is not silent
        # autonomy.
        skill_name = str(argument_payload.get("name") or "").strip()
        skill_description = str(argument_payload.get("description") or "").strip()
        skill_body = str(argument_payload.get("body") or "").strip()
        if not skill_name:
            raise RuntimeError("Tool 'skill_write' requires a name.")
        if not skill_description:
            raise RuntimeError("Tool 'skill_write' requires a description.")
        if not skill_body:
            raise RuntimeError("Tool 'skill_write' requires a body (the procedure).")
        from server_modules import skills_registry as skills_marketplace_registry

        authoring_agent = str(
            session_metadata.get("sage_agent_id")
            or session_metadata.get("agent_scope")
            or session_metadata.get("agent_id")
            or "agent"
        ).strip() or "agent"
        write_result = skills_marketplace_registry.author_pending_skill(
            name=skill_name,
            description=skill_description,
            body=skill_body,
            author=f"agent:{authoring_agent}",
            skill_class=str(argument_payload.get("skill_class") or "business").strip() or "business",
            connector_scopes=argument_payload.get("connector_scopes"),
            trigger_terms=argument_payload.get("trigger_terms"),
        )
        return json.dumps(write_result, ensure_ascii=False)
    if connector_id == "browser":
        browser = _resolve_direct_tool_browser_adapter(session_ctx)
        if action_id == "navigate":
            return json.dumps(browser.run_sync("navigate", argument_payload.get("url") or ""), ensure_ascii=False)
        if action_id == "extract_text":
            return str(browser.run_sync("extract_text", argument_payload.get("selector")))
        if action_id == "extract_dom":
            return str(browser.run_sync("extract_dom", argument_payload.get("selector")))
        raise RuntimeError(f"Unsupported browser direct tool '{action_id}'.")
    if connector_id == "web" and action_id == "search":
        query = str(argument_payload.get("query") or argument_payload.get("input") or "").strip()
        results = callbacks.web_search(query)
        if not results:
            return f"No web search results found for '{query}'."
        return "\n\n".join(
            f"{result_index}. {result['title']}\nURL: {result['url']}\nSnippet: {result['snippet']}"
            for result_index, result in enumerate(results, start=1)
        )
    if connector_id == "web" and action_id == "fetch":
        url = str(argument_payload.get("url") or argument_payload.get("input") or "").strip()
        return callbacks.web_fetch(url)
    if connector_id == "llm" and action_id == "task":
        _raise_direct_chat_tool_execution_blocked()
    if connector_id == "memory" and action_id == "search":
        query = str(argument_payload.get("query") or argument_payload.get("input") or "").strip()
        if not query:
            raise RuntimeError("Tool 'memory_search' requires a query.")
        results = callbacks.search_memory_notebook(
            workspace_id,
            query,
            max_results=callbacks.safe_positive_int(argument_payload.get("max_results"), 5),
            # SECURITY: every sibling memory_* action below scopes to the
            # calling agent via session_metadata — this one and memory_get
            # used to be the sole exceptions, silently falling back to the
            # workspace root (Sage's own notebook) for any specialist's turn
            # per agent_workspace_context_dir's documented fallback. Fixed
            # 2026-07-14.
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
        )
        # search_memory_notebook returns a self-describing envelope
        # ({results, files_searched, errors, status, message}) so the model can
        # tell "searched everything, confirmed nothing" from "the search never
        # ran" or "some files were unreadable and NOT searched". Pass it
        # through flat — re-wrapping would bury status/errors a level deeper.
        return json.dumps(results, ensure_ascii=False)
    if connector_id == "memory" and action_id == "get":
        rel_path = str(argument_payload.get("path") or argument_payload.get("input") or "").strip()
        if not rel_path:
            raise RuntimeError("Tool 'memory_get' requires a path.")
        excerpt = callbacks.get_memory_notebook_excerpt(
            workspace_id,
            rel_path,
            from_line=argument_payload.get("from"),
            line_count=argument_payload.get("lines"),
            # SECURITY: see memory_search above — same fix, same reason.
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
        )
        return json.dumps(excerpt, ensure_ascii=False)
    if connector_id == "memory" and action_id == "update":
        filename = str(argument_payload.get("filename") or argument_payload.get("path") or "").strip()
        content = str(argument_payload.get("content") or "")
        if not filename:
            raise RuntimeError("Tool 'memory_update' requires a filename.")
        if not content.strip():
            raise RuntimeError("Tool 'memory_update' requires non-empty content.")
        actor = str(
            session_metadata.get("user_id")
            or session_metadata.get("actor")
            or session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or "direct_tool"
        ).strip()
        saved = callbacks.update_memory_context_file(
            workspace_id,
            filename,
            content,
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
            actor=actor,
            reason="memory_update",
            run_id=str(session_metadata.get("run_id") or session_metadata.get("request_id") or "").strip() or None,
            audit_metadata={"source": "direct_tool"},
            # Attribution seam: same session_metadata["envelope"] snapshot
            # memory_write already threads through (see that branch below) --
            # a non-owner/unverified turn calling memory_update on a root
            # file (including MEMORY.md) is subject to the same write filter
            # as every other memory-writing tool, not a silent bypass.
            source=session_metadata.get("envelope") if isinstance(session_metadata.get("envelope"), dict) else None,
            attribution_reason=str(argument_payload.get("attribution_reason") or "").strip() or None,
            # Auto-maintained topic-file index (founder ruling): required
            # only when `filename` is a memory/files/*.md topic file --
            # no-op/ignored for every other file, so this is unchanged
            # behavior for root-file and daily-note updates.
            description=str(argument_payload.get("description") or "").strip() or None,
        )
        return json.dumps(
            {
                "ok": True,
                "filename": saved.get("filename") if isinstance(saved, dict) else filename,
                "workspace_id": saved.get("workspace_id") if isinstance(saved, dict) else workspace_id,
                "old_hash": saved.get("old_hash") if isinstance(saved, dict) else None,
                "new_hash": saved.get("new_hash") if isinstance(saved, dict) else None,
                "version_id": saved.get("version_id") if isinstance(saved, dict) else None,
                **_memory_redaction_result_fields(saved),
            },
            ensure_ascii=False,
        )
    if connector_id == "memory" and action_id == "read":
        filename = str(argument_payload.get("file") or argument_payload.get("path") or "").strip()
        if not filename:
            raise RuntimeError("Tool 'memory_read' requires a file path.")
        result = callbacks.memory_read_file(
            workspace_id,
            filename,
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
        )
        return json.dumps(result, ensure_ascii=False)
    if connector_id == "memory" and action_id == "write":
        filename = str(argument_payload.get("file") or argument_payload.get("path") or "").strip()
        content = str(argument_payload.get("content") or "")
        mode = str(argument_payload.get("mode") or "replace").strip().lower() or "replace"
        if not filename:
            raise RuntimeError("Tool 'memory_write' requires a file path.")
        if not content.strip():
            raise RuntimeError("Tool 'memory_write' requires non-empty content.")
        actor = str(
            session_metadata.get("user_id")
            or session_metadata.get("actor")
            or session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or "direct_tool"
        ).strip()
        saved = callbacks.memory_write_file(
            workspace_id,
            filename,
            content,
            mode=mode,
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
            actor=actor,
            reason="memory_write",
            run_id=str(session_metadata.get("run_id") or session_metadata.get("request_id") or "").strip() or None,
            # Attribution seam: session_metadata["envelope"] is stamped by
            # agent_turn_runtime_service.handle_sage_chat (see
            # inbound_attribution_recovery.build_attribution) -- the turn's
            # WHO/WHERE, best-effort recovered since the frozen
            # sage_turn_adapter chokepoint doesn't forward the canonical
            # InboundEnvelope object itself. None for any caller that
            # doesn't set it (unchanged behavior).
            source=session_metadata.get("envelope") if isinstance(session_metadata.get("envelope"), dict) else None,
            # Write filter (context-engineering-plan.md item 6): required
            # whenever the envelope above resolves to a non-owner/unverified
            # trust tier -- the model must state, in its own tool call, why
            # this non-owner content is worth saving. Optional/ignored
            # otherwise (see agent_memory.requires_attribution_reason).
            attribution_reason=str(argument_payload.get("attribution_reason") or "").strip() or None,
            # Auto-maintained topic-file index (founder ruling, 2026-07-23):
            # required only when `filename` resolves to a memory/files/*.md
            # topic file -- ignored/no-op for every other target (MEMORY.md
            # itself, bootstrap files, daily notes), so this is unchanged
            # behavior for every write this tool made before description existed.
            description=str(argument_payload.get("description") or "").strip() or None,
        )
        return json.dumps(
            {
                "ok": True,
                "file": saved.get("file"),
                "chars_written": saved.get("chars_written"),
                "mode": saved.get("mode"),
                **_memory_redaction_result_fields(saved),
            },
            ensure_ascii=False,
        )
    if connector_id == "memory" and action_id == "write_private":
        # SECURITY (feat/agent-memory-shared-vs-private): the private layer
        # is keyed on session_metadata["user_id"] ONLY -- there is no
        # user_id in argument_payload's schema at all (see the
        # ToolDescriptor above: its parameters object has no such
        # property), so there is no field here for a model to set to claim
        # someone else's identity. Whoever the platform actually resolved
        # as the caller for THIS turn is whose private note gets written --
        # never a value the tool call itself supplies. This mirrors
        # tool_honesty_guard/agent_goals.attempt_count's posture: the
        # partitioning decision is made by the firing code, never narrated
        # by the model.
        content = str(argument_payload.get("content") or "").strip()
        if not content:
            raise RuntimeError("Tool 'memory_write_private' requires non-empty content.")
        # Through the shared resolver, NOT a bare .get("user_id"): production
        # nests the id under session_ctx["metadata"], so the flat read returned
        # None on every real turn and silently disabled this tool from the day
        # it shipped. See _resolve_session_user_id.
        user_id = _resolve_session_user_id(session_metadata)
        if not user_id:
            raise RuntimeError(
                "Tool 'memory_write_private' requires a resolved user identity, which "
                "this channel/session did not provide -- private memory is only "
                "available to an authenticated internal workspace member."
            )
        from server_modules import agent_private_memory_service

        saved = agent_private_memory_service.write_private_memory_note(
            workspace_id,
            agent_install_id=agent_private_memory_service.resolve_agent_install_scope(
                session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id")
            ),
            user_id=user_id,
            content=content,
            reason="memory_write_private",
        )
        return json.dumps(
            {
                "ok": True,
                "chars_written": len(str(saved.get("content") or "")),
                "redacted": bool(saved.get("redacted")),
                "revision_recorded": bool(saved.get("revision_recorded", True)),
            },
            ensure_ascii=False,
        )
    if connector_id == "memory" and action_id == "get_private":
        # Same identity source as write_private above -- server-resolved
        # only, never model-supplied. A caller with no resolved user_id
        # (e.g. an anonymous external-channel turn) gets an honest "no
        # identity available" response rather than another person's note --
        # there is no fallback path here that reads without a real user_id.
        # Through the shared resolver, NOT a bare .get("user_id"): production
        # nests the id under session_ctx["metadata"], so the flat read returned
        # None on every real turn and silently disabled this tool from the day
        # it shipped. See _resolve_session_user_id.
        user_id = _resolve_session_user_id(session_metadata)
        if not user_id:
            return json.dumps(
                {"content": "", "exists": False, "reason": "no_resolved_user_identity"},
                ensure_ascii=False,
            )
        from server_modules import agent_private_memory_service

        note = agent_private_memory_service.get_private_memory_note(
            workspace_id,
            agent_install_id=agent_private_memory_service.resolve_agent_install_scope(
                session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id")
            ),
            user_id=user_id,
        )
        content = str((note or {}).get("content") or "")
        return json.dumps(
            {"content": content, "exists": bool(content)},
            ensure_ascii=False,
        )
    if connector_id == "memory" and action_id == "stage_edit":
        filename = str(argument_payload.get("filename") or argument_payload.get("path") or "").strip()
        content = str(argument_payload.get("content") or "")
        reason = str(argument_payload.get("reason") or "root memory edit").strip() or "root memory edit"
        if not filename:
            raise RuntimeError("Tool 'memory_stage_edit' requires a filename.")
        if not content.strip():
            raise RuntimeError("Tool 'memory_stage_edit' requires non-empty content.")
        source_refs = argument_payload.get("source_refs")
        actor = str(
            session_metadata.get("user_id")
            or session_metadata.get("actor")
            or session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or "direct_tool"
        ).strip()
        proposal = (
            f"Reason: {reason}\n\n"
            f"Target file: {filename}\n\n"
            "Complete proposed Markdown:\n\n"
            f"```markdown\n{content.rstrip()}\n```"
        )
        saved = callbacks.create_memory_consolidation_staging_file(
            workspace_id,
            proposal,
            source_refs=source_refs if isinstance(source_refs, list) else None,
            target_files=[filename],
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
            actor=actor,
            run_id=str(session_metadata.get("run_id") or session_metadata.get("request_id") or "").strip() or None,
        )
        return json.dumps(
            {
                "ok": True,
                "filename": saved.get("filename") if isinstance(saved, dict) else "",
                "workspace_id": saved.get("workspace_id") if isinstance(saved, dict) else workspace_id,
                "target_files": saved.get("target_files") if isinstance(saved, dict) else [filename],
                "staged_only": True,
                "approval_required": True,
                "old_hash": saved.get("old_hash") if isinstance(saved, dict) else None,
                "new_hash": saved.get("new_hash") if isinstance(saved, dict) else None,
                "version_id": saved.get("version_id") if isinstance(saved, dict) else None,
            },
            ensure_ascii=False,
        )
    if connector_id == "memory" and action_id == "apply_edit":
        staging_filename = str(argument_payload.get("staging_filename") or argument_payload.get("path") or "").strip()
        merged_files = argument_payload.get("merged_files")
        if not staging_filename:
            raise RuntimeError("Tool 'memory_apply_edit' requires staging_filename.")
        if not isinstance(merged_files, dict) or not merged_files:
            raise RuntimeError("Tool 'memory_apply_edit' requires merged_files.")
        actor = str(
            session_metadata.get("user_id")
            or session_metadata.get("actor")
            or session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or "direct_tool"
        ).strip()
        result = callbacks.apply_memory_consolidation_staging(
            workspace_id,
            staging_filename,
            {str(key): str(value or "") for key, value in merged_files.items()},
            user_approved=bool(argument_payload.get("user_approved")),
            policy_allows=bool(argument_payload.get("policy_allows")),
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
            actor=actor,
            run_id=str(session_metadata.get("run_id") or session_metadata.get("request_id") or "").strip() or None,
        )
        payload = dict(result) if isinstance(result, dict) else {"ok": True}
        payload.update(_memory_redaction_result_fields(payload))
        return json.dumps(payload, ensure_ascii=False)
    if connector_id == "memory" and action_id == "append_daily_note":
        note = str(argument_payload.get("note") or argument_payload.get("input") or "")
        if not note.strip():
            raise RuntimeError("Tool 'memory_append_daily_note' requires non-empty note text.")
        actor = str(
            session_metadata.get("user_id")
            or session_metadata.get("actor")
            or session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or "direct_tool"
        ).strip()
        saved = callbacks.memory_append_daily_note(
            workspace_id,
            note,
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
            actor=actor,
            run_id=str(session_metadata.get("run_id") or session_metadata.get("request_id") or "").strip() or None,
            # Attribution seam: same session_metadata["envelope"] snapshot
            # memory_write threads through -- daily notes are consolidated
            # into MEMORY.md/memory/files/goals.md/etc later (consolidate_
            # daily_memory_notes), so an unattributed daily note was a real
            # gap: a non-owner's statement could reach a root file with no
            # attribution trail at all. Fixed by threading the same seam here.
            source=session_metadata.get("envelope") if isinstance(session_metadata.get("envelope"), dict) else None,
            attribution_reason=str(argument_payload.get("attribution_reason") or "").strip() or None,
        )
        return json.dumps(
            {
                "ok": True,
                "filename": saved.get("filename") if isinstance(saved, dict) else "",
                "workspace_id": saved.get("workspace_id") if isinstance(saved, dict) else workspace_id,
                "appended_entry": saved.get("appended_entry") if isinstance(saved, dict) else "",
                "saved": bool(saved.get("saved", True)) if isinstance(saved, dict) else True,
                "usefulness": saved.get("usefulness") if isinstance(saved, dict) else None,
                "duplicate_of": saved.get("duplicate_of") if isinstance(saved, dict) else None,
                "old_hash": saved.get("old_hash") if isinstance(saved, dict) else None,
                "new_hash": saved.get("new_hash") if isinstance(saved, dict) else None,
                "version_id": saved.get("version_id") if isinstance(saved, dict) else None,
                **_memory_redaction_result_fields(saved),
            },
            ensure_ascii=False,
        )
    if connector_id == "memory" and action_id == "stage_consolidation":
        proposal = str(argument_payload.get("proposal") or argument_payload.get("input") or "")
        if not proposal.strip():
            raise RuntimeError("Tool 'memory_stage_consolidation' requires non-empty proposal text.")
        source_refs = argument_payload.get("source_refs")
        target_files = argument_payload.get("target_files")
        actor = str(
            session_metadata.get("user_id")
            or session_metadata.get("actor")
            or session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or "direct_tool"
        ).strip()
        saved = callbacks.create_memory_consolidation_staging_file(
            workspace_id,
            proposal,
            source_refs=source_refs if isinstance(source_refs, list) else None,
            target_files=target_files if isinstance(target_files, list) else None,
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
            actor=actor,
            run_id=str(session_metadata.get("run_id") or session_metadata.get("request_id") or "").strip() or None,
        )
        return json.dumps(
            {
                "ok": True,
                "filename": saved.get("filename") if isinstance(saved, dict) else "",
                "workspace_id": saved.get("workspace_id") if isinstance(saved, dict) else workspace_id,
                "target_files": saved.get("target_files") if isinstance(saved, dict) else [],
                "source_refs": saved.get("source_refs") if isinstance(saved, dict) else [],
                "staged_only": True,
                "old_hash": saved.get("old_hash") if isinstance(saved, dict) else None,
                "new_hash": saved.get("new_hash") if isinstance(saved, dict) else None,
                "version_id": saved.get("version_id") if isinstance(saved, dict) else None,
            },
            ensure_ascii=False,
        )
    if connector_id == "memory" and action_id == "consolidate_daily_notes":
        actor = str(
            session_metadata.get("user_id")
            or session_metadata.get("actor")
            or session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or "direct_tool"
        ).strip()
        result = callbacks.consolidate_daily_memory_notes(
            workspace_id,
            target_files=argument_payload.get("target_files") if isinstance(argument_payload.get("target_files"), list) else None,
            max_notes=callbacks.safe_positive_int(argument_payload.get("max_notes"), 30),
            apply_merge=bool(argument_payload.get("apply_merge")),
            compact_mode=str(argument_payload.get("compact_mode") or "none"),
            user_approved=bool(argument_payload.get("user_approved")),
            policy_allows=bool(argument_payload.get("policy_allows")),
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
            run_id=str(argument_payload.get("run_id") or "").strip() or None,
            actor=actor,
        )
        return json.dumps(result if isinstance(result, dict) else {"ok": True}, ensure_ascii=False)
    if connector_id == "memory" and action_id == "list_versions":
        filename = str(argument_payload.get("filename") or argument_payload.get("path") or "").strip()
        if not filename:
            raise RuntimeError("Tool 'memory_list_versions' requires filename.")
        versions = callbacks.list_memory_file_versions(
            workspace_id,
            filename,
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
            limit=callbacks.safe_positive_int(argument_payload.get("limit"), 20),
        )
        return json.dumps({"filename": filename, "versions": versions}, ensure_ascii=False)
    if connector_id == "memory" and action_id == "rollback_version":
        filename = str(argument_payload.get("filename") or argument_payload.get("path") or "").strip()
        version_id = str(argument_payload.get("version_id") or "").strip()
        reason = str(argument_payload.get("reason") or "memory_rollback").strip() or "memory_rollback"
        if not filename:
            raise RuntimeError("Tool 'memory_rollback_version' requires filename.")
        if not version_id:
            raise RuntimeError("Tool 'memory_rollback_version' requires version_id.")
        actor = str(
            session_metadata.get("user_id")
            or session_metadata.get("actor")
            or session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or "direct_tool"
        ).strip()
        result = callbacks.rollback_memory_file_version(
            workspace_id,
            filename,
            version_id=version_id,
            reason=reason,
            actor=actor,
            run_id=str(argument_payload.get("run_id") or session_metadata.get("run_id") or session_metadata.get("request_id") or "").strip() or None,
            agent_install_id=session_metadata.get("agent_install_id") or session_metadata.get("active_agent_install_id") or None,
        )
        return json.dumps(result if isinstance(result, dict) else {"ok": True}, ensure_ascii=False)
    if connector_id == "project_task":
        from server_modules import project_tasks_service as _project_tasks

        _caller_agent_id = _agent_install_id_from_direct_tool_context(session_ctx)
        _caller_tenant_id = _tenant_id_from_direct_tool_context(session_ctx)
        if not _caller_agent_id:
            raise RuntimeError(f"Tool 'project_task__{action_id}' requires a resolvable agent identity.")
        # feat/agent-context-grant: the boundary is this agent's CONTEXT
        # GRANT, not "the one project it happens to live in" (CLAUDE.md,
        # founder 2026-08-20 -- an agent built for someone else's business
        # must reach none of the owner's own context). An install with NO
        # grant recorded resolves to exactly its old home project, so every
        # pre-existing agent behaves identically; a read that fails resolves
        # to nothing at all, never back to the wider pre-grant reach.
        from server_modules import agent_context_grant_service as _grants

        _grant = callbacks.run_async_tool_call(
            _grants.resolve_agent_project_grant(
                tenant_id=_caller_tenant_id, workspace_id=workspace_id, agent_install_id=_caller_agent_id,
            )
        )
        _caller_project_ids = list(_grant.project_ids)
        _caller_project_id = _grant.write_project_id
        if not _caller_project_ids:
            raise RuntimeError(
                f"Tool 'project_task__{action_id}' is unavailable: "
                + _grants.ambiguous_write_target_message(_grant, noun="task board")
            )

        def _require_write_project(action: str) -> str:
            if not _caller_project_id:
                raise RuntimeError(
                    f"Tool 'project_task__{action}' is unavailable: "
                    + _grants.ambiguous_write_target_message(_grant, noun="task")
                )
            return _caller_project_id

        def _linked(task: Any) -> Any:
            """Attach the task's own URL + GEN-12 identifier before it reaches
            the model. This is the ONLY thing that makes "I created GEN-12 for
            you" tappable in Telegram/Slack: the agent's reply is written from
            this tool result, so the address has to be IN the result -- there
            is no channel-side rewriter, and there must not be one (a
            per-channel link builder is the "channels is ONE system" rule
            broken). deep_link_service returns no url at all when the
            deployment has not declared its public origin, so a link is never
            invented. See _deep_link_guidance() in agent_turn_runtime_service
            for the instruction that tells the model to pass it on."""
            from server_modules import deep_link_service as _deep_links

            return _deep_links.annotate_task(task, workspace_id=workspace_id)

        def _task_in_own_project(task: Optional[Dict[str, Any]], task_id: str) -> Dict[str, Any]:
            if task is None:
                raise RuntimeError(f"Task '{task_id}' not found in your project.")
            if str(task.get("project_id") or "") not in _caller_project_ids:
                raise RuntimeError(f"Task '{task_id}' belongs to a different project — not visible to this agent.")
            return task

        if action_id == "create":
            title = str(argument_payload.get("title") or "").strip()
            if not title:
                raise RuntimeError("Tool 'project_task__create' requires a title.")
            try:
                task = callbacks.run_async_tool_call(
                    _project_tasks.create_task(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        project_id=_require_write_project("create"),
                        title=title,
                        description=str(argument_payload.get("description") or ""),
                        due_at=argument_payload.get("due_at"),
                        priority=argument_payload.get("priority"),
                        parent_task_id=argument_payload.get("parent_task_id"),
                        created_by=_caller_agent_id,
                    )
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            return json.dumps({"ok": True, "task": _linked(task)}, ensure_ascii=False)

        if action_id == "list":
            status = str(argument_payload.get("status") or "").strip() or None
            # One call per GRANTED project, never one call with no project
            # filter: list_my_tasks' project_id is the scope, so widening it
            # to "unset" would be the fail-open `WHERE ($1 = '' OR ...)`
            # shape CLAUDE.md already records. A grant is a handful of ids.
            tasks_rows: List[Dict[str, Any]] = []
            for _scoped_project_id in _caller_project_ids:
                tasks_rows.extend(callbacks.run_async_tool_call(
                    _project_tasks.list_my_tasks(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        agent_id=_caller_agent_id,
                        project_id=_scoped_project_id,
                        status=status,
                        sort=argument_payload.get("sort"),
                    )
                ) or [])
            return json.dumps({"ok": True, "tasks": [_linked(t) for t in tasks_rows]}, ensure_ascii=False)

        if action_id == "get":
            task_id = str(argument_payload.get("task_id") or "").strip()
            if not task_id:
                raise RuntimeError("Tool 'project_task__get' requires task_id.")
            task = callbacks.run_async_tool_call(
                _project_tasks.get_task(tenant_id=_caller_tenant_id, workspace_id=workspace_id, task_id=task_id)
            )
            task = _task_in_own_project(task, task_id)
            # The sub-task rollup (subtask_count / subtask_done_count) rides
            # along on the task itself, already computed by the same query.
            # The CHILDREN are a second read, done only here on the
            # single-task path -- deliberately not on `list`, where it would
            # be one extra query per card.
            subtasks = callbacks.run_async_tool_call(
                _project_tasks.list_subtasks(
                    tenant_id=_caller_tenant_id, workspace_id=workspace_id, parent_task_id=task_id,
                )
            )
            return json.dumps(
                {"ok": True, "task": _linked(task), "subtasks": [_linked(s) for s in (subtasks or [])]},
                ensure_ascii=False,
            )

        if action_id == "set_parent":
            task_id = str(argument_payload.get("task_id") or "").strip()
            if not task_id:
                raise RuntimeError("Tool 'project_task__set_parent' requires task_id.")
            existing = callbacks.run_async_tool_call(
                _project_tasks.get_task(tenant_id=_caller_tenant_id, workspace_id=workspace_id, task_id=task_id)
            )
            _task_in_own_project(existing, task_id)
            new_parent_id = str(argument_payload.get("parent_task_id") or "").strip()
            if new_parent_id:
                # The proposed parent has to clear the SAME project boundary
                # the task itself did -- project_tasks_service checks that
                # parent and child share a project, but this check is what
                # makes the failure an honest "not visible to this agent"
                # rather than leaking whether some other project's task id
                # happens to exist.
                proposed = callbacks.run_async_tool_call(
                    _project_tasks.get_task(
                        tenant_id=_caller_tenant_id, workspace_id=workspace_id, task_id=new_parent_id,
                    )
                )
                _task_in_own_project(proposed, new_parent_id)
            try:
                task = callbacks.run_async_tool_call(
                    _project_tasks.set_task_parent(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        parent_task_id=new_parent_id or None,
                    )
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            return json.dumps({"ok": True, "task": _linked(task)}, ensure_ascii=False)

        if action_id in ("list_labels", "add_label", "remove_label"):
            from server_modules import workspace_labels_service as _labels

            if action_id == "list_labels":
                # Workspace-scoped by design (a label is shared across every
                # project), unlike every other action in this namespace --
                # reading the vocabulary is not reading another project's
                # work, and an agent that cannot see the list cannot attach
                # anything from it.
                rows = callbacks.run_async_tool_call(
                    _labels.list_labels(tenant_id=_caller_tenant_id, workspace_id=workspace_id)
                )
                return json.dumps({"ok": True, "labels": rows}, ensure_ascii=False)

            task_id = str(argument_payload.get("task_id") or "").strip()
            label_token = str(argument_payload.get("label") or "").strip()
            if not task_id:
                raise RuntimeError(f"Tool 'project_task__{action_id}' requires task_id.")
            if not label_token:
                raise RuntimeError(f"Tool 'project_task__{action_id}' requires label.")
            existing = callbacks.run_async_tool_call(
                _project_tasks.get_task(tenant_id=_caller_tenant_id, workspace_id=workspace_id, task_id=task_id)
            )
            _task_in_own_project(existing, task_id)
            try:
                if action_id == "add_label":
                    labels_now = callbacks.run_async_tool_call(
                        _labels.attach_label(
                            tenant_id=_caller_tenant_id,
                            workspace_id=workspace_id,
                            task_id=task_id,
                            label=label_token,
                            added_by=_caller_agent_id,
                        )
                    )
                else:
                    labels_now = callbacks.run_async_tool_call(
                        _labels.detach_label(
                            tenant_id=_caller_tenant_id,
                            workspace_id=workspace_id,
                            task_id=task_id,
                            label=label_token,
                        )
                    )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            return json.dumps({"ok": True, "task_id": task_id, "labels": labels_now}, ensure_ascii=False)

        if action_id == "update":
            task_id = str(argument_payload.get("task_id") or "").strip()
            if not task_id:
                raise RuntimeError("Tool 'project_task__update' requires task_id.")
            existing = callbacks.run_async_tool_call(
                _project_tasks.get_task(tenant_id=_caller_tenant_id, workspace_id=workspace_id, task_id=task_id)
            )
            _task_in_own_project(existing, task_id)
            try:
                task = callbacks.run_async_tool_call(
                    _project_tasks.update_task(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        title=argument_payload.get("title"),
                        description=argument_payload.get("description"),
                        status=argument_payload.get("status"),
                        priority=argument_payload.get("priority"),
                        due_at=argument_payload.get("due_at"),
                        clear_due_at=bool(argument_payload.get("clear_due_at")),
                        # Review attribution (pure stamp, never a gate): the
                        # agent tool path -- _caller_agent_id is already the
                        # resolved, validated calling agent's install id
                        # (see the connector_id == "project_task" block
                        # above).
                        actor_agent_id=_caller_agent_id,
                    )
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            return json.dumps({"ok": True, "task": _linked(task)}, ensure_ascii=False)

        if action_id == "comment":
            task_id = str(argument_payload.get("task_id") or "").strip()
            body = str(argument_payload.get("body") or "").strip()
            if not task_id:
                raise RuntimeError("Tool 'project_task__comment' requires task_id.")
            if not body:
                raise RuntimeError("Tool 'project_task__comment' requires body.")
            existing = callbacks.run_async_tool_call(
                _project_tasks.get_task(tenant_id=_caller_tenant_id, workspace_id=workspace_id, task_id=task_id)
            )
            _task_in_own_project(existing, task_id)
            try:
                task = callbacks.run_async_tool_call(
                    _project_tasks.add_task_comment(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        author_type="agent",
                        author_id=_caller_agent_id,
                        body=body,
                    )
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            return json.dumps({"ok": True, "task": _linked(task)}, ensure_ascii=False)

        if action_id == "assign":
            task_id = str(argument_payload.get("task_id") or "").strip()
            target_agent_id = str(argument_payload.get("agent_id") or "").strip()
            if not task_id:
                raise RuntimeError("Tool 'project_task__assign' requires task_id.")
            if not target_agent_id:
                raise RuntimeError("Tool 'project_task__assign' requires agent_id.")
            existing = callbacks.run_async_tool_call(
                _project_tasks.get_task(tenant_id=_caller_tenant_id, workspace_id=workspace_id, task_id=task_id)
            )
            _task_in_own_project(existing, task_id)
            # feat/agent-context-grant: the TARGET must be granted THIS
            # TASK'S project, not merely have a home project the caller can
            # also reach. Handing work to an agent that cannot open the
            # project it lives in produces a task nobody can ever work.
            _task_project_id = str((existing or {}).get("project_id") or "")
            _target_grant = callbacks.run_async_tool_call(
                _grants.resolve_agent_project_grant(
                    tenant_id=_caller_tenant_id, workspace_id=workspace_id, agent_install_id=target_agent_id,
                )
            )
            if _task_project_id not in _target_grant.project_ids:
                raise RuntimeError(
                    f"Agent '{target_agent_id}' is not in your project — cannot assign this task to it."
                )
            try:
                result = callbacks.run_async_tool_call(
                    _project_tasks.assign_task(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        agent_id=target_agent_id,
                        triggered_by=f"agent:{_caller_agent_id}",
                        # 2026-08-13: INHERIT this turn's own tier, never
                        # default to owner just because it called this tool
                        # -- an audience-tier turn delegating a task must not
                        # be able to mint an owner-tier wake for the agent it
                        # hands off to. See schedule_task_assigned_wakeup's
                        # own docstring for the full reasoning.
                        authority_tier=(session_ctx if isinstance(session_ctx, dict) else {}).get("authority_tier"),
                    )
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            if isinstance(result, dict) and isinstance(result.get("task"), dict):
                result = {**result, "task": _linked(result["task"])}
            return json.dumps({"ok": True, **result}, ensure_ascii=False)

        raise RuntimeError(f"Unsupported project_task direct tool '{action_id}'.")
    if connector_id == "document":
        # document__* (feat/document-agent-tools): agent-facing access to a
        # project's owned markdown documents (project_documents_repository.py).
        # Byte-for-byte the same identity/scoping shape as connector_id ==
        # "project_task" immediately above -- resolve the CALLING agent's own
        # project server-side (never trust a project id the model might pass
        # in) via project_tasks_service.agent_project_id, and reject anything
        # that resolves to a different project rather than silently 404ing,
        # so a denial reads as "not visible to this agent" instead of leaking
        # whether some other project's path/id happens to exist.
        from server_modules import project_documents_repository as _documents
        from server_modules import project_tasks_service as _project_tasks

        from server_modules import agent_document_scope_service as _doc_scope

        _caller_agent_id = _agent_install_id_from_direct_tool_context(session_ctx)
        _caller_tenant_id = _tenant_id_from_direct_tool_context(session_ctx)
        if not _caller_agent_id:
            raise RuntimeError(f"Tool 'document__{action_id}' requires a resolvable agent identity.")
        # ONE resolution, both answers -- see agent_document_scope_service.
        # READ SCOPE vs WRITE SCOPE are deliberately different, and the
        # asymmetry is the point of "connected by default":
        #
        #   reads (list/read)  -> every project in reach. An agent with no
        #     project of its own -- the workspace-level Operator, which
        #     carries no project_id and therefore could not read a single
        #     document on any turn, in any workspace, until now -- inherits
        #     the ASKING PERSON'S own reach and nothing wider.
        #   writes (write/edit) -> the agent's OWN project, unchanged. The
        #     widening is read-only on purpose: a read can be scoped to a
        #     set, but a write has to land in exactly one project, and
        #     choosing one out of several on the agent's behalf is a guess
        #     about intent.
        _scope = callbacks.run_async_tool_call(
            _doc_scope.resolve_agent_document_project_scope(
                tenant_id=_caller_tenant_id,
                workspace_id=workspace_id,
                agent_install_id=_caller_agent_id,
                user_id=_resolve_session_user_id(session_ctx),
            )
        )
        _caller_project_scope = list(_scope.project_ids)
        _caller_project_id = _scope.own_project_id
        if action_id in ("write", "edit"):
            if not _caller_project_id:
                raise RuntimeError(
                    f"Tool 'document__{action_id}' is unavailable: this agent has no project of "
                    "its own, so there is no single place to write. Ask a project's own agent."
                )
        elif not _caller_project_scope:
            raise RuntimeError(
                f"Tool 'document__{action_id}' is unavailable: no project's documents are in "
                "reach for this turn."
            )

        def _document_link(document: Any) -> Any:
            """The document's own URL, attached before the model sees it --
            same reasoning as the task side's _linked() above: an agent
            telling someone in Telegram that it wrote a document can only
            hand over the address if the address is in the tool result."""
            from server_modules import deep_link_service as _deep_links

            return _deep_links.annotate_document(document, workspace_id=workspace_id)

        def _document_summary(document: Dict[str, Any]) -> Dict[str, Any]:
            summary = {
                "id": document.get("id"),
                "title": document.get("title"),
                "path": document.get("path"),
                # Carried so a multi-project list (the Operator's) is
                # disambiguable at all -- a path is unique per project, not
                # per workspace, so a bare path list would be a set of
                # names the model cannot always act on.
                "project_id": document.get("project_id"),
                "created_at": document.get("created_at"),
                "updated_at": document.get("updated_at"),
                "updated_by": document.get("updated_by"),
            }
            return _document_link(summary)

        if action_id == "list":
            docs = callbacks.run_async_tool_call(
                _documents.list_documents(
                    tenant_id=_caller_tenant_id,
                    workspace_id=workspace_id,
                    project_ids=_caller_project_scope,
                    include_body=False,
                )
            )
            return json.dumps(
                {"ok": True, "documents": [_document_summary(d) for d in docs]}, ensure_ascii=False,
            )

        if action_id == "read":
            path = str(argument_payload.get("path") or "").strip()
            document_ref = str(argument_payload.get("id") or "").strip()
            if not path and not document_ref:
                raise RuntimeError("Tool 'document__read' requires path or id.")
            if document_ref:
                document = callbacks.run_async_tool_call(
                    _documents.get_document(
                        tenant_id=_caller_tenant_id, workspace_id=workspace_id, document_id=document_ref,
                    )
                )
                if document is None:
                    raise RuntimeError(f"Document '{document_ref}' not found.")
                if str(document.get("project_id") or "") not in _caller_project_scope:
                    raise RuntimeError(f"Document '{document_ref}' belongs to a project that is not in reach for this turn.")
            else:
                # One path can exist in several of the projects in reach
                # (UNIQUE is per project). Collect every hit rather than
                # taking the first: silently picking one would answer a
                # different question than the model asked, and this
                # codebase's own record of "silent misrouting beats loud
                # failure, and that is a bug" is what makes guessing the
                # wrong call. The scope is one project for a specialist, so
                # this loop is a single query in the ordinary case.
                _hits = []
                for _scope_project_id in _caller_project_scope:
                    _hit = callbacks.run_async_tool_call(
                        _documents.get_document_by_path(
                            tenant_id=_caller_tenant_id,
                            workspace_id=workspace_id,
                            project_id=_scope_project_id,
                            path=path,
                        )
                    )
                    if _hit is not None:
                        _hits.append(_hit)
                if not _hits:
                    raise RuntimeError(f"No document at path '{path}' in reach. Use document__list to see what exists.")
                if len(_hits) > 1:
                    _where = ", ".join(str(h.get("project_id") or "?") for h in _hits)
                    raise RuntimeError(
                        f"'{path}' exists in more than one project in reach ({_where}). "
                        "Read it by id instead -- document__list returns one per document."
                    )
                document = _hits[0]
            return json.dumps({"ok": True, "document": _document_link(document)}, ensure_ascii=False)

        if action_id == "edit":
            path = str(argument_payload.get("path") or "").strip()
            old_string = argument_payload.get("old_string")
            new_string = argument_payload.get("new_string")
            if not path:
                raise RuntimeError("Tool 'document__edit' requires path.")
            if not isinstance(old_string, str) or old_string == "":
                raise RuntimeError("Tool 'document__edit' requires a non-empty old_string.")
            if not isinstance(new_string, str):
                raise RuntimeError("Tool 'document__edit' requires new_string.")
            if old_string == new_string:
                raise RuntimeError(
                    "Tool 'document__edit' requires old_string and new_string to differ — there is nothing to change."
                )
            document = callbacks.run_async_tool_call(
                _documents.get_document_by_path(
                    tenant_id=_caller_tenant_id,
                    workspace_id=workspace_id,
                    project_id=_caller_project_id,
                    path=path,
                )
            )
            if document is None:
                raise RuntimeError(f"No document at path '{path}' in your project. Use document__list to see what exists.")
            body = str(document.get("body") or "")
            occurrences = body.count(old_string)
            # The core guarantee this tool exists for: never guess, never
            # silently overwrite. Zero matches and multiple matches both
            # fail loudly, with zero mutation -- see the ToolDescriptor's own
            # description above, which spells this out for the model so it
            # knows to re-read or narrow old_string rather than retry blind.
            if occurrences == 0:
                raise RuntimeError(
                    f"old_string not found in document '{path}'. No changes were made. Re-read the document with "
                    "document__read — the text may not match exactly, or may have changed since you last saw it."
                )
            if occurrences > 1:
                raise RuntimeError(
                    f"old_string appears {occurrences} times in document '{path}' — it must match exactly once. "
                    "No changes were made. Include more surrounding context (e.g. a nearby heading or line) so the "
                    "match is unique."
                )
            new_body = body.replace(old_string, new_string, 1)
            updated = callbacks.run_async_tool_call(
                _documents.update_document(
                    tenant_id=_caller_tenant_id,
                    workspace_id=workspace_id,
                    document_id=document["id"],
                    # The stale-write precondition (project_documents_
                    # repository.update_document): the state hash of the
                    # body THIS replacement was computed against. Without
                    # it, a person's autosave (or another agent's edit)
                    # landing between the read above and the write here is
                    # silently overwritten -- and this dispatch re-implements
                    # the read/replace/write itself rather than going through
                    # edit_document_by_replace, so it does not inherit that
                    # function's own precondition and has to carry its own.
                    expected_sha256=str(document.get("state_sha256") or "") or None,
                    body=new_body,
                    updated_by=_caller_agent_id,
                    # A platform agent's own tool call -- see project_documents_
                    # repository's changed_by_type vocabulary (human/agent/external_agent).
                    changed_by_type="agent",
                )
            )
            if updated is None:
                raise RuntimeError(f"Document '{path}' could not be updated — it may have just been deleted.")
            return json.dumps({"ok": True, "document": _document_summary(updated)}, ensure_ascii=False)

        if action_id == "write":
            title = str(argument_payload.get("title") or "").strip()
            body = argument_payload.get("body")
            if not title:
                raise RuntimeError("Tool 'document__write' requires a title.")
            # CREATE ONLY: check the path this title would produce BEFORE
            # inserting, and fail loudly if it is already taken, rather than
            # letting create_document's own _unique_path silently disambiguate
            # into 'title-2.md' -- see project_documents_repository.default_path_for_
            # title's own docstring for why that default is wrong for an
            # agent tool. (A concurrent create landing between this check and
            # the INSERT below is a benign, narrow race: worst case it lands
            # on an auto-suffixed path instead of erroring — it can never
            # overwrite the other write.)
            candidate_path = _documents.default_path_for_title(title)
            existing = callbacks.run_async_tool_call(
                _documents.get_document_by_path(
                    tenant_id=_caller_tenant_id,
                    workspace_id=workspace_id,
                    project_id=_caller_project_id,
                    path=candidate_path,
                )
            )
            if existing is not None:
                raise RuntimeError(
                    f"A document already exists at path '{candidate_path}' (title: '{existing.get('title')}'). "
                    "document__write only creates new documents — use document__edit to change the existing one."
                )
            try:
                created = callbacks.run_async_tool_call(
                    _documents.create_document(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        project_id=_caller_project_id,
                        title=title,
                        body=str(body or ""),
                        path=candidate_path,
                        created_by=_caller_agent_id,
                        changed_by_type="agent",
                    )
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            return json.dumps({"ok": True, "document": _document_summary(created)}, ensure_ascii=False)

        raise RuntimeError(f"Unsupported document direct tool '{action_id}'.")
    if connector_id == "goal":
        # goal__* (feat/agent-goals): byte-for-byte the same identity/
        # scoping shape as connector_id == "project_task"/"document" above
        # -- resolve the CALLING agent's own project server-side (never
        # trust a project id the model might pass in), and any goal that
        # resolves to a different project is "not visible to this agent",
        # never a leaked existence check. list/get/update read or touch any
        # goal in the CALLER's PROJECT (matching project_task__update's own
        # "any project member may update any project task" posture, not
        # restricted to the goal's own assignee) -- create always targets an
        # agent in that same project too.
        from server_modules import bounded_scheduler_service as _goals
        from server_modules import project_tasks_service as _project_tasks

        _caller_agent_id = _agent_install_id_from_direct_tool_context(session_ctx)
        _caller_tenant_id = _tenant_id_from_direct_tool_context(session_ctx)
        if not _caller_agent_id:
            raise RuntimeError(f"Tool 'goal__{action_id}' requires a resolvable agent identity.")
        # feat/agent-context-grant: same boundary as project_task__* above --
        # the agent's CONTEXT GRANT, resolved server-side, never a project
        # id the model supplies.
        from server_modules import agent_context_grant_service as _grants

        _grant = callbacks.run_async_tool_call(
            _grants.resolve_agent_project_grant(
                tenant_id=_caller_tenant_id, workspace_id=workspace_id, agent_install_id=_caller_agent_id,
            )
        )
        _caller_project_ids = list(_grant.project_ids)
        _caller_project_id = _grant.write_project_id
        if not _caller_project_ids:
            raise RuntimeError(
                f"Tool 'goal__{action_id}' is unavailable: "
                + _grants.ambiguous_write_target_message(_grant, noun="goal list")
            )

        def _require_write_project(action: str) -> str:
            if not _caller_project_id:
                raise RuntimeError(
                    f"Tool 'goal__{action}' is unavailable: "
                    + _grants.ambiguous_write_target_message(_grant, noun="goal")
                )
            return _caller_project_id

        def _goal_in_own_project(goal: Optional[Dict[str, Any]], goal_id: str) -> Dict[str, Any]:
            if goal is None:
                raise RuntimeError(f"Goal '{goal_id}' not found in your project.")
            if str(goal.get("project_id") or "") not in _caller_project_ids:
                raise RuntimeError(f"Goal '{goal_id}' belongs to a different project — not visible to this agent.")
            return goal

        if action_id == "create":
            goal_text = str(argument_payload.get("goal_text") or "").strip()
            if not goal_text:
                raise RuntimeError("Tool 'goal__create' requires goal_text.")
            target_agent_id = str(argument_payload.get("agent_id") or "").strip() or _caller_agent_id
            _goal_project_id = _require_write_project("create")
            if target_agent_id != _caller_agent_id:
                # Same rule as project_task__assign: the TARGET must be
                # granted the project this goal will live in.
                _target_grant = callbacks.run_async_tool_call(
                    _grants.resolve_agent_project_grant(
                        tenant_id=_caller_tenant_id, workspace_id=workspace_id, agent_install_id=target_agent_id,
                    )
                )
                if _goal_project_id not in _target_grant.project_ids:
                    raise RuntimeError(
                        f"Agent '{target_agent_id}' is not in your project — cannot create a goal for it."
                    )
            try:
                goal = callbacks.run_async_tool_call(
                    _goals.create_goal(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        project_id=_goal_project_id,
                        agent_id=target_agent_id,
                        goal_text=goal_text,
                        title=str(argument_payload.get("title") or ""),
                        instruction=str(argument_payload.get("instruction") or ""),
                        requested_by=f"agent:{_caller_agent_id}",
                        max_attempts=argument_payload.get("max_attempts"),
                        lifetime_days=argument_payload.get("lifetime_days"),
                    )
                )
            except _goals.SchedulerPolicyError as exc:
                raise RuntimeError(str(exc)) from exc
            return json.dumps({"ok": True, "goal": _goals.goal_view(goal)}, ensure_ascii=False)

        if action_id == "list":
            status = str(argument_payload.get("status") or "").strip() or None
            # One call per GRANTED project -- same reasoning as
            # project_task__list above.
            rows: List[Dict[str, Any]] = []
            for _scoped_project_id in _caller_project_ids:
                rows.extend(callbacks.run_async_tool_call(
                    _goals.list_goals(
                        tenant_id=_caller_tenant_id,
                        workspace_id=workspace_id,
                        project_id=_scoped_project_id,
                        status=status,
                    )
                ) or [])
            return json.dumps({"ok": True, "goals": [_goals.goal_view(row) for row in rows]}, ensure_ascii=False)

        if action_id == "get":
            goal_id = str(argument_payload.get("goal_id") or "").strip()
            if not goal_id:
                raise RuntimeError("Tool 'goal__get' requires goal_id.")
            goal = callbacks.run_async_tool_call(
                _goals.get_goal(tenant_id=_caller_tenant_id, workspace_id=workspace_id, goal_id=goal_id)
            )
            goal = _goal_in_own_project(goal, goal_id)
            return json.dumps({"ok": True, "goal": _goals.goal_view(goal)}, ensure_ascii=False)

        if action_id == "update":
            goal_id = str(argument_payload.get("goal_id") or "").strip()
            if not goal_id:
                raise RuntimeError("Tool 'goal__update' requires goal_id.")
            existing = callbacks.run_async_tool_call(
                _goals.get_goal(tenant_id=_caller_tenant_id, workspace_id=workspace_id, goal_id=goal_id)
            )
            _goal_in_own_project(existing, goal_id)
            result = callbacks.run_async_tool_call(
                _goals.update_goal(
                    tenant_id=_caller_tenant_id,
                    workspace_id=workspace_id,
                    goal_id=goal_id,
                    status=argument_payload.get("status"),
                    title=argument_payload.get("title"),
                    goal_text=argument_payload.get("goal_text"),
                    instruction=argument_payload.get("instruction"),
                    note=str(argument_payload.get("note") or ""),
                    actor=f"agent:{_caller_agent_id}",
                )
            )
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or f"Could not update goal {goal_id}."))
            return json.dumps(result, ensure_ascii=False)

        raise RuntimeError(f"Unsupported goal direct tool '{action_id}'.")
    if connector_id == "sage_service" and action_id == "list_state":
        service_id = str(argument_payload.get("service_id") or "").strip()
        if not service_id:
            raise RuntimeError("Tool 'sage_service__list_state' requires a service_id.")
        payload = sage_services_service.list_sage_services(workspace_id=workspace_id)
        items = payload.get("items") if isinstance(payload, dict) else []
        for item in items or []:
            if str(item.get("id") or "").strip() == service_id:
                return json.dumps(item, ensure_ascii=False)
        raise RuntimeError(f"Unknown service '{service_id}'.")
    if connector_id == "sage_service" and action_id == "update_profile":
        service_id = str(argument_payload.get("service_id") or "").strip()
        profile = argument_payload.get("profile")
        if not service_id or not isinstance(profile, dict):
            raise RuntimeError("Tool 'sage_service__update_profile' requires service_id and profile.")
        write_authorization = {
            "explicit_user_intent": bool(argument_payload.get("explicit_user_intent") or argument_payload.get("confirm_write")),
            "approval_granted": bool(argument_payload.get("approval_granted")),
            "approval_id": str(argument_payload.get("approval_id") or "").strip() or None,
            "approval_source": "direct_tool",
        }
        result = callbacks.run_async_tool_call(
            sage_services_service.update_service_profile(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                service_id=service_id,
                profile=profile,
                actor_user_id=None,
                write_authorization=write_authorization,
            )
        )
        service_payload = result.get("service") if isinstance(result, dict) else result
        return json.dumps(service_payload, ensure_ascii=False)
    if connector_id == "sage_service" and action_id == "create_entry":
        service_id = str(argument_payload.get("service_id") or "").strip()
        entry = argument_payload.get("entry")
        if not service_id or not isinstance(entry, dict):
            raise RuntimeError("Tool 'sage_service__create_entry' requires service_id and entry.")
        write_authorization = {
            "explicit_user_intent": bool(argument_payload.get("explicit_user_intent") or argument_payload.get("confirm_write")),
            "approval_granted": bool(argument_payload.get("approval_granted")),
            "approval_id": str(argument_payload.get("approval_id") or "").strip() or None,
            "approval_source": "direct_tool",
        }
        result = callbacks.run_async_tool_call(
            sage_services_service.create_service_entry(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                service_id=service_id,
                entry=entry,
                actor_user_id=None,
                write_authorization=write_authorization,
            )
        )
        service_payload = result.get("service") if isinstance(result, dict) else result
        return json.dumps(service_payload, ensure_ascii=False)
    if connector_id == "hardware" and action_id == "action":
        return _execute_hardware_action_tool_call(
            argument_payload=argument_payload if isinstance(argument_payload, dict) else {},
            workspace_id=workspace_id,
            thread_id=thread_id,
            index=index,
            session_ctx=session_ctx,
            callbacks=callbacks,
        )
    if connector_id in {"file", "shell", "screenshot", "computer"}:
        return _execute_safe_direct_local_tool_call(
            connector_id=connector_id,
            action_id=action_id,
            argument_payload=argument_payload if isinstance(argument_payload, dict) else {},
            workspace_id=workspace_id,
            provider=provider,
            model=model,
            credentials=credentials,
            thread_id=thread_id,
            index=index,
            session_ctx=session_ctx,
            callbacks=callbacks,
        )
    # ── Fleet management tools (operator-only) ──────────────────────────
    if connector_id == "fleet":
        from server_modules.fleet_tools import (
            fleet_create_agent,
            fleet_list_agents,
            fleet_get_agent_activity,
            fleet_get_project_activity,
            fleet_configure_agent,
            schedule_task,
            schedule_recurring_task,
            list_recurring_tasks,
            cancel_recurring_task,
            resolve_agent_role,
            OPERATOR_ROLE,
        )

        # Resolve the calling agent and enforce operator role.
        actor_install_id = str(
            session_metadata.get("agent_install_id")
            or session_metadata.get("active_agent_install_id")
            or session_metadata.get("agent_id")
            or ""
        ).strip()
        actor_id = actor_install_id or str(session_metadata.get("user_id") or "sage").strip() or "sage"

        if actor_install_id:
            try:
                from server_modules import agent_registry_repository as _reg
                install = callbacks.run_async_tool_call(
                    _reg.get_workspace_agent_install_bundle(
                        actor_install_id, tenant_id=tenant_id, workspace_id=workspace_id
                    )
                )
            except Exception:
                install = None

            role = resolve_agent_role(install) if install else "specialist"
            if role != OPERATOR_ROLE:
                return json.dumps({
                    "ok": False,
                    "error": (
                        f"Fleet tool '{action_id}' requires operator role. "
                        f"Current role: {role}. Only the workspace operator agent can manage the fleet."
                    ),
                }, ensure_ascii=False)

        if action_id == "create_agent":
            result = callbacks.run_async_tool_call(
                fleet_create_agent(
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    name=str(argument_payload.get("name") or "").strip(),
                    instructions=str(argument_payload.get("instructions") or "").strip(),
                    purpose_preset=str(argument_payload.get("purpose_preset") or "").strip(),
                    capability_preset=str(argument_payload.get("capability_preset") or "standard").strip(),
                )
            )
            return json.dumps(result, ensure_ascii=False)

        if action_id == "list_agents":
            result = callbacks.run_async_tool_call(
                fleet_list_agents(
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                )
            )
            return json.dumps(result, ensure_ascii=False)

        if action_id == "get_agent_activity":
            agent_id = str(argument_payload.get("agent_id") or "").strip()
            if not agent_id:
                raise RuntimeError("Tool 'fleet__get_agent_activity' requires agent_id.")
            result = callbacks.run_async_tool_call(
                fleet_get_agent_activity(
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                )
            )
            return json.dumps(result, ensure_ascii=False)

        if action_id == "get_project_activity":
            project_id = str(argument_payload.get("project_id") or "").strip()
            if not project_id:
                raise RuntimeError("Tool 'fleet__get_project_activity' requires project_id.")
            result = callbacks.run_async_tool_call(
                fleet_get_project_activity(
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
            )
            return json.dumps(result, ensure_ascii=False)

        if action_id == "configure_agent":
            agent_id = str(argument_payload.get("agent_id") or "").strip()
            patch = argument_payload.get("patch")
            if not agent_id or not isinstance(patch, dict):
                raise RuntimeError("Tool 'fleet__configure_agent' requires agent_id and patch (object).")
            result = callbacks.run_async_tool_call(
                fleet_configure_agent(
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    patch=patch,
                )
            )
            return json.dumps(result, ensure_ascii=False)

        if action_id == "schedule_task":
            # audit-system-prompt-doctrine.md's #1 ranked gap: the model was
            # never told a self-wakeup scheduler exists, and this dispatcher
            # required an agent_id even though fleet_tools.schedule_task ->
            # bounded_scheduler_service.propose_self_wakeup already resolves
            # the wake-up to the calling workspace's own master install
            # regardless of agent_id (propose_self_wakeup's signature takes
            # no agent_id parameter at all — it isn't used for routing).
            # agent_id is now optional: when the model omits it, schedule_task
            # falls back to actor_id (agent_id or actor_id, see fleet_tools.py)
            # — the calling agent's own identity — which is exactly "schedule
            # myself." An explicit agent_id still targets a different agent
            # (fleet management), unchanged.
            agent_id = str(argument_payload.get("agent_id") or "").strip()
            when = str(argument_payload.get("when") or "").strip()
            instruction = str(argument_payload.get("instruction") or "").strip()
            if not when or not instruction:
                raise RuntimeError("Tool 'fleet__schedule_task' requires when and instruction (agent_id is optional — omit it to schedule yourself).")
            # Mandate: the wake request must carry THIS turn's tier, not a
            # freshly-derived one — inherit_tier() (called inside
            # schedule_task) is the enforcement; passing the raw session_ctx
            # value through here is just plumbing. An audience-tier turn
            # scheduling a task must never result in owner-tier execution
            # later just because the wake-up has no live channel sender.
            result = callbacks.run_async_tool_call(
                schedule_task(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                    when=when,
                    instruction=instruction,
                    tenant_id=tenant_id,
                    authority_tier=session_metadata.get("authority_tier"),
                )
            )
            return json.dumps(result, ensure_ascii=False)

        if action_id == "schedule_recurring_task":
            # schedule_task's recurring twin -- same agent_id-optional self-
            # vs-fleet convention and same tier-inheritance reasoning as the
            # comment on schedule_task above.
            agent_id = str(argument_payload.get("agent_id") or "").strip()
            cron = str(argument_payload.get("cron") or "").strip()
            instruction = str(argument_payload.get("instruction") or "").strip()
            if not cron or not instruction:
                raise RuntimeError("Tool 'fleet__schedule_recurring_task' requires cron and instruction (agent_id is optional — omit it to schedule yourself).")
            max_occurrences = argument_payload.get("max_occurrences")
            expires_at = str(argument_payload.get("expires_at") or "").strip() or None
            result = callbacks.run_async_tool_call(
                schedule_recurring_task(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                    cron=cron,
                    instruction=instruction,
                    tenant_id=tenant_id,
                    authority_tier=session_metadata.get("authority_tier"),
                    max_occurrences=int(max_occurrences) if max_occurrences is not None else None,
                    expires_at=expires_at,
                )
            )
            return json.dumps(result, ensure_ascii=False)

        if action_id == "list_recurring_tasks":
            agent_id = str(argument_payload.get("agent_id") or "").strip()
            result = callbacks.run_async_tool_call(
                list_recurring_tasks(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                    tenant_id=tenant_id,
                )
            )
            return json.dumps(result, ensure_ascii=False)

        if action_id == "cancel_recurring_task":
            agent_id = str(argument_payload.get("agent_id") or "").strip()
            schedule_id = str(argument_payload.get("schedule_id") or "").strip()
            if not schedule_id:
                raise RuntimeError("Tool 'fleet__cancel_recurring_task' requires schedule_id.")
            result = callbacks.run_async_tool_call(
                cancel_recurring_task(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                    schedule_id=schedule_id,
                    tenant_id=tenant_id,
                )
            )
            return json.dumps(result, ensure_ascii=False)

        raise RuntimeError(f"Unknown fleet action '{action_id}'.")

    # ── Sub-agent spawn (2026-07-24 ruling) ──────────────────────────────
    if connector_id == "subagent" and action_id == "spawn":
        from server_modules import runtime_run_delegation_service as _subagent_bridge

        acting_install_id = str(
            session_metadata.get("active_agent_install_id")
            or session_metadata.get("agent_install_id")
            or ""
        ).strip()
        # Defense in depth: the tool is only ever added to the tool list when
        # agent_turn_runtime_service._direct_tool_bundle already resolved
        # subagents_enabled=True for this specialist (a stale/cached tool
        # list is the only way this branch could otherwise be reached with
        # the flag off) -- re-check here rather than trust the caller.
        specialist_guard = session_metadata.get("specialist_guard")
        subagents_enabled = bool(
            isinstance(specialist_guard, dict) and specialist_guard.get("subagents_enabled")
        )
        if not subagents_enabled:
            return json.dumps(
                {
                    "ok": False,
                    "error": "subagents_disabled",
                    "message": (
                        "Sub-agent delegation is disabled for this agent. An "
                        "operator can enable it via fleet_configure_agent."
                    ),
                },
                ensure_ascii=False,
            )
        owner_user_id = str(
            session_metadata.get("owner_user_id")
            or session_metadata.get("sender_id")
            or ((session_metadata.get("metadata") or {}).get("user_id") if isinstance(session_metadata.get("metadata"), dict) else "")
            or ""
        ).strip()
        result = _subagent_bridge.spawn_subagent_from_chat_turn(
            task_description=str(argument_payload.get("task_description") or "").strip(),
            role=str(argument_payload.get("role") or "").strip(),
            session_ctx=session_metadata,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            acting_agent_install_id=acting_install_id,
        )
        return json.dumps(result, ensure_ascii=False)

    _raise_direct_chat_tool_execution_blocked()
