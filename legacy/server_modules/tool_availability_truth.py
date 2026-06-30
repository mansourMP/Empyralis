from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


_CONNECTOR_MANIFESTS: Dict[str, Dict[str, Any]] = {
    "google_workspace": {
        "label": "Google Workspace",
        "resources": [{"id": "gmail_threads", "access": ["read", "write"]}, {"id": "calendar_events", "access": ["read", "write"]}, {"id": "drive_files", "access": ["read", "write"]}],
        "actions": [{"id": "fetch_emails"}, {"id": "list_calendar_events"}, {"id": "list_drive_files"}, {"id": "draft_email"}, {"id": "send_email"}, {"id": "create_calendar_event"}, {"id": "create_doc"}, {"id": "create_sheet"}],
    },
    "smtp": {
        "label": "SMTP Email",
        "resources": [{"id": "mailboxes", "access": ["read"]}],
        "actions": [{"id": "send_email"}, {"id": "fetch_emails"}],
    },
    "telegram_bot": {
        "label": "Telegram Bot",
        "resources": [{"id": "chats", "access": ["read", "write"]}],
        "actions": [{"id": "send_message"}, {"id": "send_media"}, {"id": "update_message"}],
    },
    "microsoft_365": {
        "label": "Microsoft 365",
        "resources": [{"id": "mail", "access": ["read", "write"]}, {"id": "calendar", "access": ["read", "write"]}, {"id": "onedrive", "access": ["read", "write"]}],
        "actions": [{"id": "draft_email"}, {"id": "send_email"}, {"id": "create_calendar_event"}, {"id": "upload_drive_file"}],
    },
    "discord_bot": {
        "label": "Discord Bot",
        "resources": [
            {"id": "guilds", "access": ["read"]},
            {"id": "guild_channels", "access": ["read", "write"]},
            {"id": "guild_members", "access": ["read"]},
            {"id": "messages", "access": ["read", "write"]},
            {"id": "threads", "access": ["write"]},
        ],
        "actions": [
            {"id": "send_message"},
            {"id": "send_embed"},
            {"id": "send_dm"},
            {"id": "edit_message"},
            {"id": "delete_message"},
            {"id": "list_guilds"},
            {"id": "list_channels"},
            {"id": "list_members"},
            {"id": "get_message_history"},
            {"id": "create_thread"},
            {"id": "add_reaction"},
        ],
    },
    "slack": {
        "label": "Slack",
        "resources": [{"id": "channels", "access": ["read", "write"]}, {"id": "users", "access": ["read"]}],
        "actions": [{"id": "send_message"}, {"id": "send_dm"}, {"id": "post_reply"}, {"id": "upload_file"}, {"id": "list_channels"}, {"id": "get_history"}],
    },
    "github": {
        "label": "GitHub",
        "resources": [{"id": "repositories", "access": ["read", "write"]}, {"id": "issues", "access": ["read", "write"]}, {"id": "pull_requests", "access": ["read", "write"]}, {"id": "repository_contents", "access": ["read", "write"]}],
        "actions": [{"id": "list_repos"}, {"id": "get_repo"}, {"id": "list_issues"}, {"id": "create_issue"}, {"id": "comment_on_issue"}, {"id": "list_pull_requests"}, {"id": "create_pull_request"}, {"id": "get_file_content"}, {"id": "create_or_update_file"}, {"id": "list_commits"}],
    },
    "dropbox": {
        "label": "Dropbox",
        "resources": [{"id": "files", "access": ["read", "write"]}, {"id": "folders", "access": ["read", "write"]}, {"id": "shared_links", "access": ["read", "write"]}],
        "actions": [{"id": "list_folder"}, {"id": "upload_file"}, {"id": "download_file"}, {"id": "delete"}, {"id": "move"}, {"id": "get_shared_link"}, {"id": "search"}],
    },
    "s3": {
        "label": "Amazon S3",
        "resources": [{"id": "buckets", "access": ["read", "write"]}, {"id": "objects", "access": ["read", "write"]}],
        "actions": [{"id": "list_buckets"}, {"id": "list_objects"}, {"id": "upload_file"}, {"id": "download_file"}, {"id": "delete_object"}, {"id": "get_presigned_url"}, {"id": "create_bucket"}],
    },
    "notion": {
        "label": "Notion",
        "resources": [{"id": "pages", "access": ["read", "write"]}, {"id": "databases", "access": ["read", "write"]}],
        "actions": [{"id": "search"}, {"id": "get_page"}, {"id": "create_page"}, {"id": "update_page"}, {"id": "append_blocks"}, {"id": "query_database"}, {"id": "create_database_item"}],
    },
    "linear": {
        "label": "Linear",
        "resources": [{"id": "teams", "access": ["read"]}, {"id": "issues", "access": ["read", "write"]}, {"id": "projects", "access": ["read"]}],
        "actions": [{"id": "list_teams"}, {"id": "list_issues"}, {"id": "get_issue"}, {"id": "create_issue"}, {"id": "update_issue"}, {"id": "list_projects"}, {"id": "add_comment"}],
    },
    "whatsapp_twilio": {
        "label": "WhatsApp (Twilio)",
        "resources": [{"id": "whatsapp_conversations", "access": ["read", "write"]}],
        "actions": [{"id": "send_message"}],
    },
    "wechat_work": {
        "label": "WeChat Work",
        "resources": [{"id": "webhook_channel", "access": ["write"]}],
        "actions": [{"id": "send_message"}],
    },
    "instagram_business": {
        "label": "Instagram Business",
        "resources": [{"id": "comments", "access": ["read", "write"]}, {"id": "direct_messages", "access": ["read", "write"]}],
        "actions": [{"id": "publish_reply"}, {"id": "send_dm"}],
    },
    "irc": {
        "label": "IRC",
        "resources": [{"id": "channels", "access": ["write"]}],
        "actions": [{"id": "send_message"}],
    },
}

_APPROVAL_ACTIONS = {
    "send_message",
    "draft_email",
    "create_calendar_event",
    "publish_content",
    "spreadsheet_update",
    "spreadsheet_append",
    "spreadsheet_create",
    "document_create",
    "document_update",
    "presentation_create",
    "presentation_update",
    "issue_write",
    "pr_write",
    "repo_write",
    "notion_write",
    "storage_write",
}

_ACTION_TOOL_MAP = {
    "send_email": "send_message",
    "fetch_emails": "fetch_emails",
    "list_calendar_events": "list_calendar_events",
    "list_drive_files": "list_drive_files",
    "send_message": "send_message",
    "send_embed": "send_message",
    "send_dm": "send_message",
    "delete_message": "send_message",
    "post_reply": "send_message",
    "publish_reply": "send_message",
    "send_media": "send_message",
    "update_message": "send_message",
    "upload_file": "send_message",
    "delete": "storage_write",
    "move": "storage_write",
    "delete_object": "storage_write",
    "create_bucket": "storage_write",
    "create_issue": "issue_write",
    "comment_on_issue": "issue_write",
    "create_pull_request": "pr_write",
    "create_or_update_file": "repo_write",
    "create_page": "notion_write",
    "update_page": "notion_write",
    "create_database_item": "notion_write",
    "update_issue": "issue_write",
    "add_comment": "issue_write",
    "draft_email": "draft_email",
    "create_calendar_event": "create_calendar_event",
    "create_doc": "document_create",
    "create_document": "document_create",
    "create_sheet": "spreadsheet_create",
    "create_spreadsheet": "spreadsheet_create",
    "upload_drive_file": "filesystem.read_write",
}


def _normalize_action_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _approval_actions(write_actions: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for action_id in write_actions:
        tool_id = _ACTION_TOOL_MAP.get(str(action_id or "").strip().lower(), str(action_id or "").strip().lower())
        if tool_id not in _APPROVAL_ACTIONS or action_id in seen:
            continue
        seen.add(action_id)
        out.append(action_id)
    return out


def _resource_actions(manifest: Dict[str, Any], access: str) -> List[str]:
    out: List[str] = []
    resources = manifest.get("resources") if isinstance(manifest.get("resources"), list) else []
    for item in resources:
        if not isinstance(item, dict):
            continue
        resource_id = str(item.get("id") or "").strip()
        levels = [str(level or "").strip().lower() for level in (item.get("access") if isinstance(item.get("access"), list) else [])]
        if resource_id and access in levels:
            out.append(f"{resource_id}.{access}")
    return out


def _connector_write_actions(connector_id: str, manifest: Dict[str, Any]) -> List[str]:
    actions = manifest.get("actions") if isinstance(manifest.get("actions"), list) else []
    out = [
        str(item.get("id") or "").strip()
        for item in actions
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    if connector_id == "google_workspace":
        return [action_id for action_id in out if action_id in {"fetch_emails", "list_calendar_events", "list_drive_files", "draft_email", "send_email", "create_calendar_event", "create_doc", "create_sheet"}]
    if connector_id == "microsoft_365":
        return [action_id for action_id in out if action_id in {"draft_email", "send_email", "create_calendar_event", "upload_drive_file"}]
    return out


def capability_verification_from_test_result(connector_id: str, test_result: Any) -> Dict[str, Any]:
    normalized_connector_id = str(connector_id or "").strip().lower()
    manifest = _CONNECTOR_MANIFESTS.get(normalized_connector_id, {})
    if not isinstance(test_result, dict) or not bool(test_result.get("ok")):
        return {
            "authenticated": False,
            "runtime_usable": False,
            "read_actions": [],
            "write_actions": [],
            "approval_required_actions": [],
        }

    read_actions: List[str] = []
    write_actions: List[str] = []
    if normalized_connector_id == "google_workspace":
        read_actions.append("gmail_threads.read")
        write_actions.extend(["fetch_emails", "draft_email", "send_email"])
        if bool(test_result.get("calendar_access")):
            read_actions.append("calendar_events.read")
            write_actions.extend(["list_calendar_events", "create_calendar_event"])
        if bool(test_result.get("files_access")):
            read_actions.append("drive_files.read")
            write_actions.extend(["list_drive_files", "create_doc", "create_sheet"])
    elif normalized_connector_id == "smtp":
        if bool(test_result.get("smtp_access")):
            write_actions.append("send_email")
        if bool(test_result.get("imap_access")):
            read_actions.append("mailboxes.read")
            write_actions.append("fetch_emails")
    elif normalized_connector_id == "microsoft_365":
        if bool(test_result.get("mail_access")):
            read_actions.append("mail.read")
            write_actions.extend(["draft_email", "send_email"])
        if bool(test_result.get("calendar_access")):
            read_actions.append("calendar.read")
            write_actions.append("create_calendar_event")
        if bool(test_result.get("files_access")):
            read_actions.append("onedrive.read")
            write_actions.append("upload_drive_file")
    else:
        read_actions.extend(_resource_actions(manifest, "read"))
        write_actions.extend(_connector_write_actions(normalized_connector_id, manifest))

    runtime_usable = bool(read_actions or write_actions)
    return {
        "authenticated": True,
        "runtime_usable": runtime_usable,
        "read_actions": read_actions,
        "write_actions": write_actions,
        "approval_required_actions": _approval_actions(write_actions),
    }


def capability_verification_metadata(connector_id: str, test_result: Any) -> Dict[str, Any]:
    payload = capability_verification_from_test_result(connector_id, test_result)
    payload["verified_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return payload


def _merge_bool(values: List[Optional[bool]]) -> Optional[bool]:
    normalized = [value for value in values if isinstance(value, bool)]
    if not normalized:
        return None
    if any(value is True for value in normalized):
        return True
    if all(value is False for value in normalized):
        return False
    return None


def resolve_workspace_tool_capabilities(
    workspace_id: str,
    *,
    connector_filter: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    from server_modules.runtime_common import list_vault_connectors, resolve_vault_credential

    requested_workspace = str(workspace_id or "").strip() or "default"
    normalized_filter = {
        str(item or "").strip().lower()
        for item in (connector_filter or [])
        if str(item or "").strip()
    }
    try:
        rows = list_vault_connectors(requested_workspace)
    except Exception:
        rows = []
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        connector_id = str(row.get("connector") or row.get("provider") or "").strip().lower()
        if not connector_id:
            continue
        if normalized_filter and connector_id not in normalized_filter:
            continue
        manifest = _CONNECTOR_MANIFESTS.get(connector_id, {})
        group = grouped.setdefault(
            connector_id,
            {
                "id": connector_id,
                "label": str(row.get("label") or manifest.get("label") or connector_id).strip() or connector_id,
                "connected": True,
                "authenticated_values": [],
                "runtime_usable_values": [],
                "read_actions": [],
                "write_actions": [],
                "approval_required_actions": [],
            },
        )
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        verification = metadata.get("capability_verification") if isinstance(metadata.get("capability_verification"), dict) else {}
        authenticated_value: Optional[bool] = verification.get("authenticated") if isinstance(verification.get("authenticated"), bool) else None
        runtime_usable_value: Optional[bool] = verification.get("runtime_usable") if isinstance(verification.get("runtime_usable"), bool) else None
        if authenticated_value is None or runtime_usable_value is None:
            connector_credential_id = str(row.get("id") or "").strip()
            if connector_credential_id:
                try:
                    resolve_vault_credential(connector_credential_id, requested_workspace)
                except Exception:
                    authenticated_value = False
                    runtime_usable_value = False
        group["authenticated_values"].append(authenticated_value)
        group["runtime_usable_values"].append(runtime_usable_value)
        for key in ("read_actions", "write_actions", "approval_required_actions"):
            for action_id in _normalize_action_list(verification.get(key)):
                if action_id not in group[key]:
                    group[key].append(action_id)

    out: List[Dict[str, Any]] = []
    for connector_id, group in grouped.items():
        out.append(
            {
                "id": connector_id,
                "label": str(group.get("label") or connector_id).strip() or connector_id,
                "connected": True,
                "authenticated": _merge_bool(group.get("authenticated_values") if isinstance(group.get("authenticated_values"), list) else []),
                "runtime_usable": _merge_bool(group.get("runtime_usable_values") if isinstance(group.get("runtime_usable_values"), list) else []),
                "read_actions": _normalize_action_list(group.get("read_actions")),
                "write_actions": _normalize_action_list(group.get("write_actions")),
                "approval_required_actions": _normalize_action_list(group.get("approval_required_actions")),
            }
        )
    out.sort(key=lambda item: (str(item.get("label") or ""), str(item.get("id") or "")))
    return out
