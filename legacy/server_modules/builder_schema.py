from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException

from server_modules.builder_runtime_mapping import (
    normalize_file_mount_grants,
    normalize_remembered_grants,
    normalize_trust_preset,
    validate_file_mount_grants,
)
from server_modules.capability_registry import workflow_node_capability_id
from server_modules.connector_manifests import CONNECTOR_MANIFESTS
from server_modules.runtime_policy import normalize_execution_target, normalize_trust_mode
from server_modules.url_security import obvious_private_url_reason


EMPYRALIST_WORKFLOW_SCHEMA_VERSION = "empyralist.workflow.v2"

WORKFLOW_NODE_TYPES: Set[str] = {"trigger", "agent", "tool", "decision", "human", "data", "subflow", "loop"}
TRIGGER_VARIANTS: Set[str] = {
    "connector_event",
    "schedule",
    "webhook",
    "workflow",
    "file_watch",
    "manual",
}
TOOL_VARIANTS: Set[str] = {
    "connector_action",
    "http",
    "browser",
    "file",
    "shell",
    "document",
    "spreadsheet",
    "code",
}
DECISION_VARIANTS: Set[str] = {"if_else", "classifier", "field_router"}
HUMAN_VARIANTS: Set[str] = {"approval", "review", "wait_for_reply"}
DATA_VARIANTS: Set[str] = {"transform", "compose", "validate"}
SUBFLOW_VARIANTS: Set[str] = {"call_workflow"}
LOOP_VARIANTS: Set[str] = {"for_each", "while", "repeat"}
_BROWSER_INTERACTIVE_ACTIONS: Set[str] = {
    "type",
    "click",
    "select",
    "upload",
    "download",
    "open_popup",
    "open_tab",
    "switch_tab",
    "close_tab",
    "navigate",
}

CONNECTOR_ALIAS_MAP = {
    "google workspace": "google_workspace",
    "gmail": "google_workspace",
    "google drive": "google_workspace",
    "drive": "google_workspace",
    "calendar": "google_workspace",
    "telegram": "telegram_bot",
    "telegram bot": "telegram_bot",
    "microsoft 365": "microsoft_365",
    "office 365": "microsoft_365",
    "outlook": "microsoft_365",
    "onedrive": "microsoft_365",
    "discord": "discord_bot",
    "whatsapp": "whatsapp_twilio",
    "twilio": "whatsapp_twilio",
    "wechat": "wechat_work",
    "wechat work": "wechat_work",
    "instagram": "instagram_business",
    "custom api": "custom_api",
    "api": "custom_api",
    "webhook": "custom_api",
}


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _clean_text(value: Any, max_chars: int = 500) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _dedupe_strings(value: Any) -> List[str]:
    items = value if isinstance(value, list) else []
    out: List[str] = []
    seen: Set[str] = set()
    for item in items:
        token = _clean_text(item, 160)
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _browser_actions(value: Any) -> List[Dict[str, Any]]:
    items = value if isinstance(value, list) else []
    return [item for item in items if isinstance(item, dict)]


def _text_blob(*values: Any) -> str:
    return " ".join(_clean_text(value, 1200) for value in values if _clean_text(value, 1200)).strip().lower()


def _connector_manifest_by_id(connector_id: str) -> Optional[Dict[str, Any]]:
    token = str(connector_id or "").strip().lower()
    if not token:
        return None
    for item in CONNECTOR_MANIFESTS:
        if str(item.get("id") or "").strip().lower() == token:
            return item
    return None


def _infer_connector_manifest(text: str) -> Optional[Dict[str, Any]]:
    blob = str(text or "").strip().lower()
    if not blob:
        return None
    for alias, connector_id in CONNECTOR_ALIAS_MAP.items():
        if alias in blob:
            return _connector_manifest_by_id(connector_id)
    for item in CONNECTOR_MANIFESTS:
        connector_id = str(item.get("id") or "").strip().lower()
        label = str(item.get("label") or "").strip().lower()
        if connector_id and connector_id in blob:
            return item
        if label and label in blob:
            return item
    return None


def _find_manifest_entry(manifest: Optional[Dict[str, Any]], key: str, text: str) -> Optional[Dict[str, Any]]:
    if not manifest:
        return None
    items = manifest.get(key)
    if not isinstance(items, list):
        return None
    blob = str(text or "").strip().lower()
    if not blob:
        return items[0] if items else None
    scored: List[tuple[int, Dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        score = 0
        item_id = str(item.get("id") or "").strip().lower()
        label = str(item.get("label") or "").strip().lower()
        description = str(item.get("description") or "").strip().lower()
        if item_id and item_id in blob:
            score += 3
        if label and label in blob:
            score += 3
        if description:
            for token in label.split():
                if token and token in blob:
                    score += 1
            for token in item_id.replace("_", " ").split():
                if token and token in blob:
                    score += 1
        scored.append((score, item))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1] if scored[0][0] > 0 else scored[0][1]


def _infer_trigger_variant(raw_node: Dict[str, Any], prompt: str) -> str:
    blob = _text_blob(prompt, raw_node.get("label"), raw_node.get("subtitle"))
    if any(token in blob for token in ["schedule", "every day", "every morning", "daily", "weekly", "cron"]):
        return "schedule"
    if any(token in blob for token in ["webhook", "http post", "http get", "endpoint", "api callback"]):
        return "webhook"
    if any(token in blob for token in ["watch file", "watch folder", "folder change", "file change", "watch path"]):
        return "file_watch"
    if any(token in blob for token in ["another workflow", "called by workflow", "workflow trigger"]):
        return "workflow"
    if _infer_connector_manifest(blob):
        return "connector_event"
    return "manual"


def _infer_tool_variant(raw_node: Dict[str, Any], prompt: str) -> str:
    blob = _text_blob(prompt, raw_node.get("label"), raw_node.get("subtitle"))
    if any(token in blob for token in ["http", "api request", "fetch ", "rest "]):
        return "http"
    if any(token in blob for token in ["browser", "open page", "visit site", "scrape", "screenshot"]):
        return "browser"
    if any(token in blob for token in ["shell", "terminal", "command line", "bash ", "zsh "]):
        return "shell"
    if any(token in blob for token in ["spreadsheet", "sheet", "excel", "csv row"]):
        return "spreadsheet"
    if any(token in blob for token in ["doc", "document", "slides", "presentation"]):
        return "document"
    if any(token in blob for token in ["file", "folder", "write path", "save to disk"]):
        return "file"
    if any(token in blob for token in ["code", "python", "javascript", "typescript"]):
        return "code"
    return "connector_action"


def _default_schedule_config(prompt: str, raw_node: Dict[str, Any]) -> Dict[str, Any]:
    blob = _text_blob(prompt, raw_node.get("label"), raw_node.get("subtitle"))
    if "weekly" in blob or "every week" in blob:
        return {"cron": "0 9 * * 1"}
    if "hourly" in blob or "every hour" in blob:
        return {"cron": "0 * * * *"}
    return {"cron": "0 9 * * *"}


def _default_webhook_config(raw_node: Dict[str, Any]) -> Dict[str, Any]:
    label = _clean_text(raw_node.get("label") or "workflow", 80).lower().replace(" ", "-")
    label = "".join(ch for ch in label if ch.isalnum() or ch == "-").strip("-") or "workflow"
    return {"path": f"/hooks/{label}", "method": "POST"}


def default_agent_config(raw_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = raw_config if isinstance(raw_config, dict) else {}
    runtime = source.get("runtime") if isinstance(source.get("runtime"), dict) else {}
    execution_target = normalize_execution_target(runtime.get("execution_target") or "auto")
    permissions = source.get("permissions") if isinstance(source.get("permissions"), dict) else {}

    return {
        "identity": {
            "name": _clean_text(source.get("identity", {}).get("name") if isinstance(source.get("identity"), dict) else source.get("name") or "Agent", 120) or "Agent",
            "role": _clean_text(source.get("identity", {}).get("role") if isinstance(source.get("identity"), dict) else source.get("role") or "Agent", 120) or "Agent",
            "goal": _clean_text(source.get("identity", {}).get("goal") if isinstance(source.get("identity"), dict) else source.get("goal"), 1000),
            "success_condition": _clean_text(source.get("identity", {}).get("success_condition") if isinstance(source.get("identity"), dict) else source.get("success_condition"), 400),
            "output_contract": _clean_text(source.get("identity", {}).get("output_contract") if isinstance(source.get("identity"), dict) else source.get("output_contract"), 400),
        },
        "runtime": {
            "provider_profile_id": _clean_text(runtime.get("provider_profile_id"), 160) or None,
            "model": _clean_text(runtime.get("model"), 120) or None,
            "provider": _clean_text(runtime.get("provider"), 80) or None,
            "execution_target": execution_target,
            "timeout_seconds": int(runtime.get("timeout_seconds") or 300),
            "token_budget": runtime.get("token_budget"),
            "retry_policy": runtime.get("retry_policy") if isinstance(runtime.get("retry_policy"), dict) else {"max_attempts": 1},
        },
        "skills": {
            "skill_bundle_ids": _dedupe_strings(source.get("skills", {}).get("skill_bundle_ids") if isinstance(source.get("skills"), dict) else source.get("skill_bundle_ids")),
            "overrides": source.get("skills", {}).get("overrides") if isinstance(source.get("skills"), dict) and isinstance(source.get("skills", {}).get("overrides"), dict) else {},
            "prompt_append": _clean_text(source.get("skills", {}).get("prompt_append") if isinstance(source.get("skills"), dict) else source.get("prompt_append"), 4000),
        },
        "tools": {
            "dynamic_allowed": _dedupe_strings(source.get("tools", {}).get("dynamic_allowed") if isinstance(source.get("tools"), dict) else source.get("dynamic_allowed")),
            "explicit_required": _dedupe_strings(source.get("tools", {}).get("explicit_required") if isinstance(source.get("tools"), dict) else source.get("explicit_required")),
        },
        "memory": {
            "read_scopes": _dedupe_strings(source.get("memory", {}).get("read_scopes") if isinstance(source.get("memory"), dict) else source.get("read_scopes")) or ["session"],
            "write_scopes": _dedupe_strings(source.get("memory", {}).get("write_scopes") if isinstance(source.get("memory"), dict) else source.get("write_scopes")) or ["session"],
            "retrieval_policy": _clean_text(source.get("memory", {}).get("retrieval_policy") if isinstance(source.get("memory"), dict) else source.get("retrieval_policy") or "recent", 80) or "recent",
            "retention": source.get("memory", {}).get("retention") if isinstance(source.get("memory"), dict) and isinstance(source.get("memory", {}).get("retention"), dict) else {},
        },
        "connectors": {
            "bindings": source.get("connectors", {}).get("bindings") if isinstance(source.get("connectors"), dict) and isinstance(source.get("connectors", {}).get("bindings"), list) else [],
        },
        "permissions": {
            "trust_preset": normalize_trust_preset(permissions.get("trust_preset")),
            "action_policy": normalize_trust_mode(permissions.get("action_policy") or "guarded"),
            "connector_permissions": _dedupe_strings(permissions.get("connector_permissions") or permissions.get("connectors")),
            "browser_permissions": permissions.get("browser_permissions") if isinstance(permissions.get("browser_permissions"), dict) else {"allow": False},
            "file_mount_grants": normalize_file_mount_grants(permissions.get("file_mount_grants"), execution_target),
            "remembered_grants": normalize_remembered_grants(permissions.get("remembered_grants")),
        },
    }


def _default_node_config(node_type: str, variant: str, raw_node: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    if node_type == "trigger":
        raw_config = raw_node.get("config") if _is_record(raw_node.get("config")) else {}
        blob = _text_blob(prompt, raw_node.get("label"), raw_node.get("subtitle"))
        manifest = _infer_connector_manifest(blob)
        inferred_trigger = _find_manifest_entry(manifest, "triggers", blob) if variant == "connector_event" else None
        return {
            "connector": _clean_text(raw_config.get("connector"), 120) or (str(manifest.get("id") or "") if manifest else ""),
            "event": _clean_text(raw_config.get("event"), 120) or (str(inferred_trigger.get("id") or "") if inferred_trigger else ""),
            "schedule": raw_config.get("schedule") if _is_record(raw_config.get("schedule")) else (_default_schedule_config(prompt, raw_node) if variant == "schedule" else {}),
            "webhook": raw_config.get("webhook") if _is_record(raw_config.get("webhook")) else (_default_webhook_config(raw_node) if variant == "webhook" else {}),
            "workflow_id": _clean_text(raw_config.get("workflow_id"), 160),
            "file_watch": raw_config.get("file_watch") if _is_record(raw_config.get("file_watch")) else ({"path": "/watch/path"} if variant == "file_watch" else {}),
            "test_only": variant == "manual",
        }
    if node_type == "agent":
        seed = raw_node.get("config") if _is_record(raw_node.get("config")) else {}
        if not seed and prompt:
            seed = {
                "identity": {
                    "goal": _clean_text(prompt, 1000),
                    "name": _clean_text(raw_node.get("label") or "Agent", 120) or "Agent",
                }
            }
        return default_agent_config(seed)
    if node_type == "tool":
        raw_config = raw_node.get("config") if _is_record(raw_node.get("config")) else {}
        permissions = raw_config.get("permissions") if isinstance(raw_config.get("permissions"), dict) else {}
        blob = _text_blob(prompt, raw_node.get("label"), raw_node.get("subtitle"))
        manifest = _infer_connector_manifest(blob)
        inferred_action = _find_manifest_entry(manifest, "actions", blob) if variant == "connector_action" else None
        execution_target = _clean_text(raw_config.get("execution_target"), 40) or ("local_companion" if variant in {"shell", "file", "browser", "code"} else "auto")
        return {
            "action_id": _clean_text(raw_config.get("action_id"), 160) or (str(inferred_action.get("id") or "") if inferred_action else _clean_text(raw_node.get("label"), 160)),
            "connector": _clean_text(raw_config.get("connector"), 120) or (str(manifest.get("id") or "") if manifest else ""),
            "method": _clean_text(raw_config.get("method"), 12) or ("POST" if "post" in blob else "GET"),
            "url": _clean_text(raw_config.get("url"), 600),
            "headers": raw_config.get("headers") if _is_record(raw_config.get("headers")) else {},
            "payload": raw_config.get("payload") if _is_record(raw_config.get("payload")) or isinstance(raw_config.get("payload"), list) else {},
            "summary": _clean_text(raw_config.get("summary") or raw_node.get("subtitle"), 600),
            "command": _clean_text(raw_config.get("command"), 4000),
            "argv": [_clean_text(item, 400) for item in raw_config.get("argv")] if isinstance(raw_config.get("argv"), list) else [],
            "cwd": _clean_text(raw_config.get("cwd"), 600),
            "timeout_seconds": raw_config.get("timeout_seconds"),
            "path": _clean_text(raw_config.get("path") or raw_config.get("file_path"), 600),
            "file_path": _clean_text(raw_config.get("file_path") or raw_config.get("path"), 600),
            "content": raw_config.get("content"),
            "content_type": _clean_text(raw_config.get("content_type"), 120),
            "signing_secret": _clean_text(raw_config.get("signing_secret"), 300),
            "code": _clean_text(raw_config.get("code"), 8000),
            "capability": _clean_text(raw_config.get("capability"), 160),
            "mode": _clean_text(raw_config.get("mode") or raw_config.get("operation"), 80),
            "title": _clean_text(raw_config.get("title"), 160),
            "to_email": _clean_text(raw_config.get("to_email") or raw_config.get("to") or raw_config.get("email") or raw_config.get("recipient"), 200),
            "subject": _clean_text(raw_config.get("subject"), 240),
            "chat_id": _clean_text(raw_config.get("chat_id"), 160),
            "channel_id": _clean_text(raw_config.get("channel_id"), 160),
            "from_number": _clean_text(raw_config.get("from_number"), 80),
            "to_number": _clean_text(raw_config.get("to_number") or raw_config.get("recipient"), 80),
            "webhook_url": _clean_text(raw_config.get("webhook_url"), 500),
            "page_id": _clean_text(raw_config.get("page_id"), 120),
            "recipient_id": _clean_text(raw_config.get("recipient_id") or raw_config.get("recipient") or raw_config.get("user_id") or raw_config.get("instagram_user_id"), 160),
            "comment_id": _clean_text(raw_config.get("comment_id") or raw_config.get("media_comment_id"), 160),
            "session_profile": _clean_text(raw_config.get("session_profile"), 160),
            "browser_actions": _browser_actions(raw_config.get("browser_actions")),
            "permissions": {
                "trust_preset": normalize_trust_preset(permissions.get("trust_preset")),
                "action_policy": normalize_trust_mode(permissions.get("action_policy") or "guarded"),
                "connector_permissions": _dedupe_strings(permissions.get("connector_permissions") or permissions.get("connectors")),
                "browser_permissions": permissions.get("browser_permissions") if isinstance(permissions.get("browser_permissions"), dict) else {"allow": False},
                "file_mount_grants": normalize_file_mount_grants(permissions.get("file_mount_grants"), execution_target),
                "remembered_grants": normalize_remembered_grants(permissions.get("remembered_grants")),
            },
            "execution_target": execution_target,
        }
    if node_type == "decision":
        return {
            "expression": _clean_text(raw_node.get("config", {}).get("expression") if _is_record(raw_node.get("config")) else raw_node.get("subtitle"), 1000),
            "field": _clean_text(raw_node.get("config", {}).get("field") if _is_record(raw_node.get("config")) else "", 160),
            "routes": raw_node.get("config", {}).get("routes") if _is_record(raw_node.get("config")) and isinstance(raw_node.get("config", {}).get("routes"), list) else [],
        }
    if node_type == "human":
        return {
            "title": _clean_text(raw_node.get("config", {}).get("title") if _is_record(raw_node.get("config")) else raw_node.get("label") or "Approval", 160) or "Approval",
            "instructions": _clean_text(raw_node.get("config", {}).get("instructions") if _is_record(raw_node.get("config")) else raw_node.get("subtitle"), 1200),
            "decision_options": _dedupe_strings(raw_node.get("config", {}).get("decision_options") if _is_record(raw_node.get("config")) else []) or ["approve", "reject"],
            "timeout_seconds": raw_node.get("config", {}).get("timeout_seconds") if _is_record(raw_node.get("config")) else None,
        }
    if node_type == "data":
        return {
            "mapping": _clean_text(raw_node.get("config", {}).get("mapping") if _is_record(raw_node.get("config")) else raw_node.get("subtitle"), 1200),
            "template": _clean_text(raw_node.get("config", {}).get("template") if _is_record(raw_node.get("config")) else "", 2000),
            "schema": raw_node.get("config", {}).get("schema") if _is_record(raw_node.get("config")) and _is_record(raw_node.get("config", {}).get("schema")) else {},
        }
    if node_type == "subflow":
        return {
            "workflow_id": _clean_text(raw_node.get("config", {}).get("workflow_id") if _is_record(raw_node.get("config")) else "", 160),
            "mode": _clean_text(raw_node.get("config", {}).get("mode") if _is_record(raw_node.get("config")) else "sync", 40) or "sync",
            "input_mapping": raw_node.get("config", {}).get("input_mapping") if _is_record(raw_node.get("config")) and _is_record(raw_node.get("config", {}).get("input_mapping")) else {},
        }
    if node_type == "loop":
        raw_config = raw_node.get("config") if _is_record(raw_node.get("config")) else {}
        body = raw_config.get("body") if _is_record(raw_config.get("body")) else {}
        normalized_body = normalize_builder_generated_workflow(
            body if isinstance(body.get("nodes"), list) else {
                "version": EMPYRALIST_WORKFLOW_SCHEMA_VERSION,
                "nodes": [
                    {
                        "id": "loop_body_data_1",
                        "type": "data",
                        "variant": "transform",
                        "config": {
                            "mapping": "Transform the current loop input into the next step payload.",
                            "template": "",
                        },
                    }
                ],
                "edges": [],
            },
            prompt=prompt,
            for_publish=False,
        )
        return {
            "array_source": _clean_text(raw_config.get("array_source"), 600),
            "item_variable_name": _clean_text(raw_config.get("item_variable_name"), 80) or "item",
            "parallel": bool(raw_config.get("parallel")),
            "max_iterations": raw_config.get("max_iterations"),
            "continue_on_error": bool(raw_config.get("continue_on_error")),
            "condition": raw_config.get("condition") if _is_record(raw_config.get("condition")) else {},
            "expression": _clean_text(raw_config.get("expression"), 1000),
            "count": raw_config.get("count"),
            "count_source": _clean_text(raw_config.get("count_source"), 600),
            "body": normalized_body,
        }
    return {}


def _compatibility_fields(node_type: str, variant: str, config: Dict[str, Any], raw_node: Dict[str, Any], index: int) -> Dict[str, Any]:
    x = raw_node.get("x")
    y = raw_node.get("y")
    label = _clean_text(raw_node.get("label"), 120)
    subtitle = _clean_text(raw_node.get("subtitle"), 160)
    if node_type == "agent":
        identity = config.get("identity") if isinstance(config.get("identity"), dict) else {}
        label = label or _clean_text(identity.get("name") or identity.get("role") or "Agent", 120) or "Agent"
        subtitle = subtitle or _clean_text(identity.get("goal"), 160)
    elif node_type == "trigger":
        label = label or ("Manual trigger" if variant == "manual" else variant.replace("_", " ").title())
        subtitle = subtitle or ("Test only" if variant == "manual" else "Workflow trigger")
    elif node_type == "loop":
        label = label or variant.replace("_", " ").title() or "Loop"
        subtitle = subtitle or "Repeat a sub-workflow"
    else:
        label = label or node_type.replace("_", " ").title()
    return {
        "label": label,
        "subtitle": subtitle,
        "x": x if isinstance(x, (int, float)) else 120 + index * 220,
        "y": y if isinstance(y, (int, float)) else 120,
    }


def _validate_node(node: Dict[str, Any], *, for_publish: bool) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    node_type = str(node.get("type") or "").strip()
    variant = str(node.get("variant") or "").strip()
    config = node.get("config") if isinstance(node.get("config"), dict) else {}

    if node_type == "trigger":
        if variant not in TRIGGER_VARIANTS:
            issues.append({"code": "trigger_variant_invalid", "message": f"Trigger variant '{variant or 'unknown'}' is not supported."})
        if variant == "connector_event":
            if not _clean_text(config.get("connector"), 120):
                issues.append({"code": "trigger_connector_missing", "message": "Connector event triggers require a connector id."})
            if not _clean_text(config.get("event"), 160):
                issues.append({"code": "trigger_event_missing", "message": "Connector event triggers require an event id."})
        if variant == "schedule":
            schedule = config.get("schedule") if isinstance(config.get("schedule"), dict) else {}
            if not _clean_text(schedule.get("cron") or config.get("cron"), 200):
                issues.append({"code": "trigger_schedule_missing", "message": "Schedule triggers require a cron expression."})
        if variant == "webhook":
            webhook = config.get("webhook") if isinstance(config.get("webhook"), dict) else {}
            if not _clean_text(webhook.get("path") or config.get("path") or webhook.get("url") or config.get("url"), 500):
                issues.append({"code": "trigger_webhook_path_missing", "message": "Webhook triggers require a path or URL."})
        if variant == "workflow" and not _clean_text(config.get("workflow_id"), 160):
            issues.append({"code": "trigger_workflow_id_missing", "message": "Workflow triggers require workflow_id."})
        if variant == "file_watch":
            file_watch = config.get("file_watch") if isinstance(config.get("file_watch"), dict) else {}
            if not _clean_text(file_watch.get("path") or config.get("path"), 600):
                issues.append({"code": "trigger_file_watch_path_missing", "message": "file_watch triggers require a path."})
        if variant == "file_watch":
            issues.append(
                {
                    "code": "file_watch_not_executable_yet",
                    "message": "file_watch triggers are schema-valid but cannot be published until local trigger runtime support is added."
                    if for_publish
                    else "file_watch triggers are not executable yet.",
                }
            )
    elif node_type == "agent":
        identity = config.get("identity") if isinstance(config.get("identity"), dict) else {}
        runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
        permissions = config.get("permissions") if isinstance(config.get("permissions"), dict) else {}
        if not _clean_text(identity.get("name") or identity.get("role"), 120):
            issues.append({"code": "agent_identity_missing", "message": "Agent identity requires a name or role."})
        issues.extend(validate_file_mount_grants(permissions.get("file_mount_grants"), runtime.get("execution_target") or "auto"))
    elif node_type == "tool":
        if variant not in TOOL_VARIANTS:
            issues.append({"code": "tool_variant_invalid", "message": f"Tool variant '{variant or 'unknown'}' is not supported."})
        issues.extend(
            validate_file_mount_grants(
                config.get("permissions", {}).get("file_mount_grants") if isinstance(config.get("permissions"), dict) else [],
                config.get("execution_target") or "auto",
            )
        )
        target = normalize_execution_target(config.get("execution_target") or "auto")
        if variant == "shell" and target == "cloud":
            issues.append({"code": "shell_requires_local_companion_or_auto", "message": "Shell tool nodes cannot target cloud directly; use local_companion or auto."})
        if variant == "code" and target == "cloud":
            issues.append({"code": "code_requires_local_companion_or_auto", "message": "Code tool nodes cannot target cloud directly; use local_companion or auto."})
        if variant == "code":
            command = _clean_text(config.get("command"), 2000)
            argv = config.get("argv") if isinstance(config.get("argv"), list) else []
            capability = _clean_text(config.get("capability"), 160)
            if command or any(_clean_text(item, 400) for item in argv) or capability:
                issues.append({"code": "code_shell_fields_disallowed", "message": "Code tool nodes cannot use command, argv, or capability in the current computer mode."})
            issues.append({"code": "code_reviewed_execution_path_required", "message": "Code tool nodes are not executable through Gateway yet; they require a reviewed higher-trust execution path."})
        if variant == "browser" and target == "cloud":
            issues.append({"code": "browser_requires_local_companion_or_auto", "message": "Browser tool nodes cannot target cloud directly; use Gateway or automatic routing."})
        if variant == "shell":
            command = _clean_text(config.get("command"), 2000)
            argv = config.get("argv") if isinstance(config.get("argv"), list) else []
            capability = _clean_text(config.get("capability"), 160)
            if not command and not any(_clean_text(item, 400) for item in argv):
                issues.append({"code": f"{variant}_command_missing", "message": f"{variant} tool nodes require command or argv."})
            if (command or any(_clean_text(item, 400) for item in argv)) and not capability:
                issues.append({"code": f"{variant}_raw_command_high_trust", "message": f"{variant} tool nodes using raw command or argv are blocked by default policy. Prefer capability-based local actions."})
            if capability and (command or any(_clean_text(item, 400) for item in argv)):
                issues.append({"code": f"{variant}_capability_conflict", "message": f"{variant} tool nodes cannot mix capability with command or argv."})
        if variant == "browser" and not _clean_text(config.get("url"), 1200):
            issues.append({"code": "browser_url_missing", "message": "Browser tool nodes require a URL."})
        if variant == "browser":
            browser_url = _clean_text(config.get("url"), 1200)
            private_url_reason = obvious_private_url_reason(browser_url) if browser_url else None
            if private_url_reason:
                issues.append({"code": "browser_private_url_disallowed", "message": f"Browser tool nodes cannot target private or unsupported hosts ({private_url_reason})."})
            browser_permissions = config.get("permissions", {}).get("browser_permissions") if isinstance(config.get("permissions"), dict) and isinstance(config.get("permissions", {}).get("browser_permissions"), dict) else {}
            browser_allowed = bool(browser_permissions.get("allow"))
            session_profile = _clean_text(config.get("session_profile"), 160)
            browser_actions = _browser_actions(config.get("browser_actions"))
            if session_profile:
                issues.append({"code": "browser_session_requires_approval", "message": "Session-backed browser automation requires approval in guarded or strict policy modes."})
            if (session_profile or browser_actions) and not browser_allowed:
                issues.append({"code": "browser_permission_required", "message": "Browser tool nodes with session_profile or browser_actions require browser_permissions.allow = true."})
            interactive = {
                _clean_text(item.get("action"), 80).lower()
                for item in browser_actions
                if _clean_text(item.get("action"), 80)
            } & _BROWSER_INTERACTIVE_ACTIONS
            if session_profile and interactive:
                issues.append({"code": "browser_reviewed_approval_required", "message": "Session-backed interactive browser automation requires reviewed approval and a resumable browser checkpoint path."})
        if variant == "http":
            http_url = _clean_text(config.get("url"), 1200)
            if not http_url:
                issues.append({"code": "http_url_missing", "message": "HTTP tool nodes require a URL."})
            private_url_reason = obvious_private_url_reason(http_url) if http_url else None
            if private_url_reason:
                issues.append({"code": "http_private_url_disallowed", "message": f"HTTP tool nodes cannot target private or unsupported hosts ({private_url_reason})."})
        if variant == "file" and not _clean_text(config.get("path") or config.get("file_path"), 1200):
            issues.append({"code": "file_path_missing", "message": "File tool nodes require path or file_path."})
        if variant == "connector_action":
            connector = _clean_text(config.get("connector"), 120)
            action_id = _clean_text(config.get("action_id"), 160)
            if not connector:
                issues.append({"code": "tool_connector_missing", "message": "Connector action nodes require a connector id."})
            if not action_id:
                issues.append({"code": "tool_action_missing", "message": "Connector action nodes require an action_id."})
            if connector == "custom_api":
                custom_api_url = _clean_text(config.get("url"), 600)
                if action_id in {"http_request", "signed_webhook"} and not custom_api_url:
                    issues.append({"code": "custom_api_url_missing", "message": "Custom API connector actions require a URL."})
                private_url_reason = obvious_private_url_reason(custom_api_url) if custom_api_url else None
                if action_id in {"http_request", "signed_webhook"} and private_url_reason:
                    issues.append({"code": "custom_api_private_url_disallowed", "message": f"Custom API connector actions cannot target private or unsupported hosts ({private_url_reason})."})
                if action_id == "signed_webhook" and not _clean_text(config.get("signing_secret"), 300):
                    issues.append({"code": "custom_api_signing_secret_missing", "message": "signed_webhook actions require signing_secret."})
            if connector == "microsoft_365" and action_id == "upload_drive_file" and not _clean_text(config.get("path") or config.get("file_path"), 600):
                issues.append({"code": "upload_drive_file_path_missing", "message": "upload_drive_file requires a path or file_path."})
            if connector == "instagram_business":
                if action_id == "publish_reply" and not _clean_text(config.get("comment_id") or config.get("media_comment_id"), 160):
                    issues.append({"code": "instagram_comment_id_missing", "message": "instagram_business.publish_reply requires comment_id."})
                if action_id == "send_dm" and not _clean_text(config.get("recipient_id") or config.get("recipient") or config.get("user_id") or config.get("instagram_user_id"), 160):
                    issues.append({"code": "instagram_recipient_missing", "message": "instagram_business.send_dm requires recipient_id."})
    elif node_type == "human":
        if variant not in HUMAN_VARIANTS:
            issues.append({"code": "human_variant_invalid", "message": f"Human node variant '{variant or 'unknown'}' is not supported."})
        if variant == "approval":
            decision_options = _dedupe_strings(config.get("decision_options"))
            if not decision_options:
                issues.append({"code": "approval_options_missing", "message": "Approval nodes require at least one decision option."})
    elif node_type == "subflow":
        if variant not in SUBFLOW_VARIANTS:
            issues.append({"code": "subflow_variant_invalid", "message": f"Subflow variant '{variant or 'unknown'}' is not supported."})
        if not _clean_text(config.get("workflow_id"), 160):
            issues.append({"code": "subflow_target_missing", "message": "Subflow nodes require a target workflow_id."})
    elif node_type == "loop":
        if variant not in LOOP_VARIANTS:
            issues.append({"code": "loop_variant_invalid", "message": f"Loop variant '{variant or 'unknown'}' is not supported."})
        body = config.get("body") if isinstance(config.get("body"), dict) else {}
        body_nodes = body.get("nodes") if isinstance(body.get("nodes"), list) else []
        if not body_nodes:
            issues.append({"code": "loop_body_missing", "message": "Loop nodes require a non-empty body workflow."})
        max_iterations = config.get("max_iterations")
        if max_iterations is not None:
            try:
                if int(max_iterations) <= 0:
                    issues.append({"code": "loop_max_iterations_invalid", "message": "Loop nodes require max_iterations greater than zero."})
            except (TypeError, ValueError):
                issues.append({"code": "loop_max_iterations_invalid", "message": "Loop nodes require a numeric max_iterations value."})
        if variant == "for_each" and not _clean_text(config.get("array_source"), 600):
            issues.append({"code": "loop_array_source_missing", "message": "FOR_EACH loop nodes require array_source."})
        if variant == "while":
            has_expression = bool(_clean_text(config.get("expression"), 1000))
            has_condition = isinstance(config.get("condition"), dict) and bool(config.get("condition"))
            if not has_expression and not has_condition:
                issues.append({"code": "loop_condition_missing", "message": "WHILE loop nodes require expression or condition."})
        if variant == "repeat":
            if config.get("count") in {None, ""} and not _clean_text(config.get("count_source"), 600):
                issues.append({"code": "loop_count_missing", "message": "REPEAT loop nodes require count or count_source."})
    elif node_type == "decision" and variant not in DECISION_VARIANTS:
        issues.append({"code": "decision_variant_invalid", "message": f"Decision variant '{variant or 'unknown'}' is not supported."})
    elif node_type == "data" and variant not in DATA_VARIANTS:
        issues.append({"code": "data_variant_invalid", "message": f"Data variant '{variant or 'unknown'}' is not supported."})
    return issues


def normalize_builder_generated_workflow(payload: Dict[str, Any], *, prompt: str = "", for_publish: bool = False) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Builder returned an invalid workflow payload.")

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise HTTPException(status_code=502, detail="Builder workflow is missing a nodes array.")

    nodes: List[Dict[str, Any]] = []
    node_ids: Set[str] = set()
    issues: List[Dict[str, str]] = []
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        node_id = _clean_text(item.get("id"), 120) or f"node_{index + 1}"
        node_type = _clean_text(item.get("type"), 40).lower()
        if node_type not in WORKFLOW_NODE_TYPES or node_id in node_ids:
            continue
        variant = _clean_text(item.get("variant"), 40).lower()
        if node_type == "trigger" and variant not in TRIGGER_VARIANTS:
            variant = _infer_trigger_variant(item, prompt)
        elif node_type == "tool" and variant not in TOOL_VARIANTS:
            variant = _infer_tool_variant(item, prompt)
        elif node_type == "decision" and variant not in DECISION_VARIANTS:
            variant = "if_else"
        elif node_type == "human" and variant not in HUMAN_VARIANTS:
            variant = "approval"
        elif node_type == "data" and variant not in DATA_VARIANTS:
            variant = "transform"
        elif node_type == "subflow" and variant not in SUBFLOW_VARIANTS:
            variant = "call_workflow"
        elif node_type == "loop" and variant not in LOOP_VARIANTS:
            variant = "for_each"

        config = _default_node_config(node_type, variant, item, prompt)
        compatibility = _compatibility_fields(node_type, variant, config, item, index)
        raw_policy = item.get("policy") if isinstance(item.get("policy"), dict) else {}
        capability_id = workflow_node_capability_id(
            node_type,
            variant=variant,
            config=config,
            policy=raw_policy,
        )
        node = {
            "id": node_id,
            "type": node_type,
            "variant": variant if variant else None,
            "config": config,
            "resources": item.get("resources") if isinstance(item.get("resources"), dict) else {},
            "policy": {
                **raw_policy,
                "capability_id": capability_id,
            },
            "position": {
                "x": compatibility["x"],
                "y": compatibility["y"],
            },
            **compatibility,
        }
        node_ids.add(node_id)
        nodes.append(node)
        issues.extend({**issue, "node_id": node_id} for issue in _validate_node(node, for_publish=for_publish))

    if not nodes:
        raise HTTPException(status_code=502, detail="Builder returned no usable workflow nodes.")

    raw_edges = payload.get("edges")
    edges: List[Dict[str, Any]] = []
    if isinstance(raw_edges, list):
        for index, item in enumerate(raw_edges):
            if not isinstance(item, dict):
                continue
            source = _clean_text(item.get("source"), 120)
            target = _clean_text(item.get("target"), 120)
            if source and target and source in node_ids and target in node_ids:
                edges.append(
                    {
                        "id": _clean_text(item.get("id"), 160) or f"edge-{source}-{target}-{index + 1}",
                        "source": source,
                        "target": target,
                        "sourceHandle": _clean_text(item.get("sourceHandle"), 80) or None,
                        "targetHandle": _clean_text(item.get("targetHandle"), 80) or None,
                    }
                )

    if for_publish:
        triggers = [node for node in nodes if node.get("type") == "trigger"]
        if not triggers or all(str(node.get("variant") or "").strip() == "manual" for node in triggers):
            issues.append(
                {
                    "code": "manual_trigger_only",
                    "message": "Published workflows require at least one non-manual trigger.",
                }
            )

    blocking_codes = {
        "manual_trigger_only",
        "trigger_variant_invalid",
        "tool_variant_invalid",
        "decision_variant_invalid",
        "human_variant_invalid",
        "data_variant_invalid",
        "subflow_variant_invalid",
        "subflow_target_missing",
        "loop_variant_invalid",
        "loop_body_missing",
        "loop_max_iterations_invalid",
        "loop_array_source_missing",
        "loop_condition_missing",
        "loop_count_missing",
        "approval_options_missing",
        "local_root_requires_local_companion",
        "shell_requires_local_companion",
    }
    if for_publish:
        blocking_codes.add("file_watch_not_executable_yet")
    blocking = [issue for issue in issues if issue.get("code") in blocking_codes]
    if blocking:
        raise HTTPException(status_code=502, detail=" ".join(str(issue.get("message") or "").strip() for issue in blocking if str(issue.get("message") or "").strip()))

    return {
        "version": EMPYRALIST_WORKFLOW_SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
        "defaults": {},
        "resources": payload.get("resources") if isinstance(payload.get("resources"), dict) else {},
        "policy": payload.get("policy") if isinstance(payload.get("policy"), dict) else {},
        "issues": issues,
    }
