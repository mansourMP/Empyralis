from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from server_modules import marketplace_distribution_service, mini_apps_service


DISCOVERY_FEED_VERSION = 1
DISCOVERY_FILTERS = {"all", "apps", "agents", "tools", "skills", "mcp", "bundles"}
DISCOVERY_MARKETPLACE_KINDS = {"agent_template", "skill", "connector", "bundle"}


def _read_record(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _read_string(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _read_bool(value: Any) -> bool:
    return value is True


def _read_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item or "").strip()
        for item in value
        if str(item or "").strip()
    ]


def _humanize(value: Any, fallback: str = "") -> str:
    token = _read_string(value, fallback).replace("-", "_")
    if not token:
        return ""
    return " ".join(part.capitalize() for part in token.split("_") if part)


def _normalize_filter(value: Any) -> str:
    token = _read_string(value, "all").lower().replace("-", "_")
    aliases = {
        "agent_template": "agents",
        "agent_templates": "agents",
        "template": "agents",
        "templates": "agents",
        "tool": "tools",
        "skill": "skills",
        "connector": "mcp",
        "connectors": "mcp",
        "bundle": "bundles",
        "app": "apps",
        "public_app": "apps",
    }
    normalized = aliases.get(token, token)
    return normalized if normalized in DISCOVERY_FILTERS else "all"


def _feed_id(kind: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{kind}:{source_id}".encode("utf-8")).hexdigest()[:24]
    return f"disc_{digest}"


def _strip_private_source(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if not key.startswith("_")
    }


def _app_feed_item(workspace_id: str, app_card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(app_card.get("visibility") or "").strip().lower() != "public" and not app_card.get("clone_available"):
        return None
    app_id = _read_string(app_card.get("app_id"))
    public_app_id = _read_string(app_card.get("public_app_id")) or app_id
    if not public_app_id:
        return None
    creator = _read_record(app_card.get("creator"))
    source = _read_record(app_card.get("source"))
    open_target = _read_record(app_card.get("open"))
    blueprint = _read_record(app_card.get("blueprint"))
    provenance = _read_record(app_card.get("provenance"))
    permission_summary = _read_string_list(app_card.get("permission_summary"))
    installed = app_card.get("installed") is not False
    clone_available = app_card.get("clone_available") is True
    action_kind = "clone_app" if clone_available else "open_app"
    action_label = "Clone Blueprint" if clone_available else "Open App"
    source_label = _read_string(source.get("label"), "Workspace app")
    return {
        "id": _feed_id("public_app", public_app_id),
        "type": "public_app",
        "title": _read_string(app_card.get("name"), app_id or "App"),
        "description": _read_string(app_card.get("description"), "A public app shared on Empyralis."),
        "actor": {
            "label": _read_string(creator.get("label"), "this workspace"),
            "byline": _read_string(creator.get("byline"), "by this workspace"),
        },
        "summary": "Clone this app blueprint into your workspace." if clone_available else "Open this app from your workspace.",
        "components": [source_label],
        "badges": ["App Blueprint", "Cloneable" if clone_available else "Installed"],
        "blueprint": blueprint,
        "provenance": provenance,
        "permission_summary": permission_summary,
        "installed": installed,
        "action": {
            "kind": action_kind,
            "label": action_label,
            "enabled": bool(clone_available or open_target.get("url")),
        },
        "updated_at": _read_string(app_card.get("updated_at")),
        "_source": {
            "kind": "public_app",
            "public_app_id": public_app_id,
            "open_url": _read_string(open_target.get("url")),
            "workspace_id": workspace_id,
        },
    }


def _marketplace_public_payloads(workspace_id: str) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    preview_packages = getattr(marketplace_distribution_service, "_preview_marketplace_packages")()
    public_payload = getattr(marketplace_distribution_service, "_public_package_payload")
    for package in preview_packages.values():
        if not isinstance(package, dict):
            continue
        package_id = _read_string(package.get("package_id"))
        if not package_id:
            continue
        merged[package_id] = public_payload(workspace_id, package, None)

    listed = marketplace_distribution_service.list_marketplace_packages(workspace_id)
    for item in list(listed.get("items") or []):
        if not isinstance(item, dict):
            continue
        package_id = _read_string(item.get("package_id"))
        if not package_id:
            continue
        merged[package_id] = item

    return list(merged.values())


def _marketplace_type(kind: str) -> str:
    if kind == "agent_template":
        return "agent_template"
    if kind == "connector":
        return "mcp"
    if kind == "skill":
        return "skill"
    if kind == "bundle":
        return "bundle"
    return kind or "capability"


def _marketplace_action(kind: str, installed: bool, preview_only: bool, install_eligible: bool, open_url: str) -> Dict[str, Any]:
    if kind == "agent_template":
        return {
            "kind": "copy_to_studio",
            "label": "Copy to Studio",
            "enabled": bool(open_url),
        }
    if installed:
        return {
            "kind": "open_capability",
            "label": "Open",
            "enabled": bool(open_url),
        }
    label = {
        "connector": "Add MCP",
        "skill": "Add Skill",
        "bundle": "Add Bundle",
    }.get(kind, "Add Capability")
    return {
        "kind": "add_capability",
        "label": label,
        "enabled": bool(install_eligible and not preview_only),
    }


def _marketplace_feed_item(workspace_id: str, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    kind = _read_string(item.get("kind")).lower()
    if kind not in DISCOVERY_MARKETPLACE_KINDS:
        return None
    package_id = _read_string(item.get("package_id"))
    if not package_id:
        return None
    package_payload = _read_record(item.get("package"))
    runtime_truth = _read_record(item.get("runtime_truth"))
    publisher = _read_record(item.get("publisher"))
    installed = _read_bool(item.get("installed"))
    preview_only = _read_bool(item.get("preview_only"))
    install_eligible = _read_bool(item.get("install_eligible")) or installed
    open_url = _read_string(runtime_truth.get("open_href"))
    type_name = _marketplace_type(kind)
    title = _read_string(item.get("label"), _read_string(package_payload.get("template_id"), "Capability"))
    specific_payload = _read_record(item.get(kind))
    components = (
        _read_string_list(specific_payload.get("required_connectors"))
        or _read_string_list(specific_payload.get("suggested_tools"))
        or [_humanize(type_name)]
    )
    summary = {
        "agent_template": "Copy this template into Agent Studio as a draft agent.",
        "connector": "Add this MCP capability to the workspace.",
        "skill": "Add this skill so Sage and eligible agents can use it.",
        "bundle": "Add a packaged set of capabilities to the workspace.",
    }.get(kind, "Add this capability to the workspace.")
    badges = [_humanize(type_name)]
    if installed:
        badges.append("Added")
    elif preview_only:
        badges.append("Preview")
    else:
        badges.append("Ready")
    return {
        "id": _feed_id("marketplace", package_id),
        "type": type_name,
        "title": title,
        "description": _read_string(item.get("description"), "A shared Empyralis capability."),
        "actor": {
            "label": _read_string(publisher.get("label"), "Empyralis"),
            "byline": f"by {_read_string(publisher.get('label'), 'Empyralis')}",
        },
        "summary": summary,
        "components": components[:4],
        "badges": badges,
        "installed": installed,
        "action": _marketplace_action(kind, installed, preview_only, install_eligible, open_url),
        "updated_at": _read_string(item.get("updated_at")),
        "_source": {
            "kind": "marketplace",
            "marketplace_kind": kind,
            "package_id": package_id,
            "open_url": open_url,
            "preview_only": preview_only,
            "install_eligible": install_eligible,
            "workspace_id": workspace_id,
        },
    }


def _build_feed_items(workspace_id: str, feed_filter: str) -> List[Dict[str, Any]]:
    normalized_workspace_id = mini_apps_service._normalize_workspace_id(workspace_id)
    normalized_filter = _normalize_filter(feed_filter)
    items: List[Dict[str, Any]] = []

    if normalized_filter in {"all", "apps"}:
        app_payload = mini_apps_service.list_apps(normalized_workspace_id, include_public_catalog=True)
        for app_card in list(app_payload.get("items") or []):
            if not isinstance(app_card, dict):
                continue
            feed_item = _app_feed_item(normalized_workspace_id, app_card)
            if feed_item:
                items.append(feed_item)

    if normalized_filter in {"all", "agents", "tools", "skills", "mcp", "bundles"}:
        for marketplace_item in _marketplace_public_payloads(normalized_workspace_id):
            feed_item = _marketplace_feed_item(normalized_workspace_id, marketplace_item)
            if not feed_item:
                continue
            if normalized_filter == "agents" and feed_item["type"] != "agent_template":
                continue
            if normalized_filter == "tools" and feed_item["type"] == "agent_template":
                continue
            if normalized_filter == "skills" and feed_item["type"] != "skill":
                continue
            if normalized_filter == "mcp" and feed_item["type"] != "mcp":
                continue
            if normalized_filter == "bundles" and feed_item["type"] != "bundle":
                continue
            items.append(feed_item)

    return sorted(
        items,
        key=lambda item: (
            0 if item.get("type") == "public_app" else 1 if item.get("type") == "agent_template" else 2,
            str(item.get("title") or "").lower(),
        ),
    )


def list_discovery_feed(workspace_id: str, *, feed_filter: str = "all") -> Dict[str, Any]:
    normalized_workspace_id = mini_apps_service._normalize_workspace_id(workspace_id)
    normalized_filter = _normalize_filter(feed_filter)
    items = [_strip_private_source(item) for item in _build_feed_items(normalized_workspace_id, normalized_filter)]
    return {
        "workspace_id": normalized_workspace_id,
        "version": DISCOVERY_FEED_VERSION,
        "filter": normalized_filter,
        "count": len(items),
        "items": items,
    }


def adopt_discovery_item(
    workspace_id: str,
    feed_item_id: str,
    *,
    actor_user_id: Optional[str] = None,
    actor_label: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_workspace_id = mini_apps_service._normalize_workspace_id(workspace_id)
    target = next(
        (item for item in _build_feed_items(normalized_workspace_id, "all") if item.get("id") == feed_item_id),
        None,
    )
    if not target:
        raise KeyError(f"Discovery item '{feed_item_id}' was not found.")
    action = _read_record(target.get("action"))
    if action.get("enabled") is not True:
        raise ValueError("This Discovery item is not available to add yet.")
    source = _read_record(target.get("_source"))
    source_kind = _read_string(source.get("kind"))

    if source_kind == "public_app":
        public_app_id = _read_string(source.get("public_app_id"))
        if action.get("kind") == "open_app":
            return {
                "feed_item_id": feed_item_id,
                "adopted": True,
                "target_kind": "app",
                "open_url": _read_string(source.get("open_url")),
                "item": _strip_private_source(target),
            }
        cloned = mini_apps_service.clone_public_app(
            normalized_workspace_id,
            public_app_id=public_app_id,
            creator_label=actor_label,
            creator_id=actor_user_id,
        )
        open_target = _read_record(cloned.get("open"))
        return {
            "feed_item_id": feed_item_id,
            "adopted": True,
            "target_kind": "app",
            "open_url": _read_string(open_target.get("url")),
            "item": cloned,
        }

    if source_kind != "marketplace":
        raise ValueError("This Discovery item cannot be added yet.")

    package_id = _read_string(source.get("package_id"))
    marketplace_kind = _read_string(source.get("marketplace_kind"))
    if marketplace_kind == "agent_template":
        return {
            "feed_item_id": feed_item_id,
            "adopted": True,
            "target_kind": "studio",
            "open_url": _read_string(source.get("open_url")),
            "item": _strip_private_source(target),
        }

    if source.get("preview_only") is True or source.get("install_eligible") is not True:
        raise ValueError("This Discovery item is preview only.")

    installed = marketplace_distribution_service.install_marketplace_package(
        normalized_workspace_id,
        package_id=package_id,
        actor_user_id=actor_user_id,
    )
    runtime_truth = _read_record(installed.get("runtime_truth"))
    return {
        "feed_item_id": feed_item_id,
        "adopted": True,
        "target_kind": marketplace_kind or "capability",
        "open_url": _read_string(runtime_truth.get("open_href")),
        "item": _strip_private_source(target),
    }
