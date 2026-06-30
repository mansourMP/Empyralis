from __future__ import annotations

from typing import Any, Dict, List

from server_modules.runtime_config import CONNECTOR_CATALOG


def _manifest(
    connector_id: str,
    *,
    triggers: List[Dict[str, Any]],
    actions: List[Dict[str, Any]],
    resources: List[Dict[str, Any]],
    supported_targets: List[str],
    notes: List[str] | None = None,
    future_capabilities: List[str] | None = None,
) -> Dict[str, Any]:
    catalog_entry = CONNECTOR_CATALOG.get(connector_id, {})
    return {
        "id": connector_id,
        "label": str(catalog_entry.get("label") or connector_id).strip() or connector_id,
        "auth": {
            "required_fields": list(catalog_entry.get("auth") or []),
        },
        "triggers": triggers,
        "actions": actions,
        "resources": resources,
        "runtime_constraints": {
            "supported_targets": supported_targets,
        },
        "notes": notes or [],
        "future_capabilities": future_capabilities or [],
    }


CONNECTOR_MANIFESTS: List[Dict[str, Any]] = [
    _manifest(
        "google_workspace",
        triggers=[
            {"id": "gmail_new_thread", "label": "New Gmail thread", "description": "Starts when a new Gmail thread arrives."},
            {"id": "calendar_event_created", "label": "Calendar event created", "description": "Starts when a new event is created."},
            {"id": "drive_file_changed", "label": "Drive file changed", "description": "Starts when a tracked Drive file changes."},
        ],
        actions=[
            {"id": "fetch_emails", "label": "Fetch emails"},
            {"id": "list_calendar_events", "label": "List calendar events"},
            {"id": "list_drive_files", "label": "List Drive files"},
            {"id": "draft_email", "label": "Draft email"},
            {"id": "send_email", "label": "Send email"},
            {"id": "create_calendar_event", "label": "Create calendar event"},
            {"id": "create_doc", "label": "Create Google Doc"},
            {"id": "create_sheet", "label": "Create Google Sheet"},
        ],
        resources=[
            {"id": "gmail_threads", "label": "Gmail threads", "access": ["read", "write"]},
            {"id": "calendar_events", "label": "Calendar events", "access": ["read", "write"]},
            {"id": "drive_files", "label": "Drive files", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
        notes=["Local Google Workspace CLI auth remains supported through connector bindings."],
    ),
    _manifest(
        "smtp",
        triggers=[],
        actions=[
            {"id": "send_email", "label": "Send email"},
            {"id": "fetch_emails", "label": "Fetch emails"},
        ],
        resources=[
            {"id": "mailboxes", "label": "Mailboxes", "access": ["read"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
        notes=["Uses generic SMTP for outbound mail. IMAP fetch is enabled when the server supports matching inbox access."],
    ),
    _manifest(
        "telegram_bot",
        triggers=[
            {"id": "inbound_message", "label": "Inbound message", "description": "Starts when a new inbound message arrives."},
            {"id": "command", "label": "Command", "description": "Starts when a bot command is received."},
            {"id": "callback_button", "label": "Callback button", "description": "Starts when a Telegram callback button is pressed."},
        ],
        actions=[
            {"id": "send_message", "label": "Send message"},
            {"id": "send_media", "label": "Send media"},
            {"id": "update_message", "label": "Update message"},
        ],
        resources=[
            {"id": "chats", "label": "Telegram chats", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
    ),
    _manifest(
        "microsoft_365",
        triggers=[
            {"id": "mail_received", "label": "Mail received", "description": "Starts when a new mail thread arrives."},
            {"id": "calendar_event_created", "label": "Calendar event created", "description": "Starts when a new Outlook calendar event is created."},
            {"id": "drive_file_changed", "label": "OneDrive file changed", "description": "Starts when a tracked OneDrive file changes."},
        ],
        actions=[
            {"id": "draft_email", "label": "Draft email"},
            {"id": "send_email", "label": "Send email"},
            {"id": "create_calendar_event", "label": "Create calendar event"},
            {"id": "upload_drive_file", "label": "Upload OneDrive file"},
        ],
        resources=[
            {"id": "mail", "label": "Outlook mail", "access": ["read", "write"]},
            {"id": "calendar", "label": "Outlook calendar", "access": ["read", "write"]},
            {"id": "onedrive", "label": "OneDrive files", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
    ),
    _manifest(
        "discord_bot",
        triggers=[
            {"id": "channel_message", "label": "Channel message", "description": "Starts when a new Discord channel message arrives."},
            {"id": "mention", "label": "Mention", "description": "Starts when the bot is mentioned."},
            {"id": "slash_command", "label": "Slash command", "description": "Starts when a Discord application command is invoked."},
            {"id": "reaction_added", "label": "Reaction added", "description": "Starts when a tracked Discord reaction is added."},
        ],
        actions=[
            {"id": "send_message", "label": "Send message"},
            {"id": "send_embed", "label": "Send embed"},
            {"id": "send_dm", "label": "Send DM"},
            {"id": "edit_message", "label": "Edit message"},
            {"id": "delete_message", "label": "Delete message"},
            {"id": "list_guilds", "label": "List guilds"},
            {"id": "list_channels", "label": "List channels"},
            {"id": "list_members", "label": "List members"},
            {"id": "get_message_history", "label": "Get message history"},
            {"id": "create_thread", "label": "Create thread"},
            {"id": "add_reaction", "label": "Add reaction"},
        ],
        resources=[
            {"id": "guild_channels", "label": "Guild channels", "access": ["read", "write"]},
            {"id": "guilds", "label": "Guilds", "access": ["read"]},
            {"id": "guild_members", "label": "Guild members", "access": ["read"]},
            {"id": "messages", "label": "Messages", "access": ["read", "write"]},
            {"id": "threads", "label": "Threads", "access": ["write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
        notes=["Supports Discord bot-token automation, inbound interaction webhooks, and optional gateway listeners when discord.py is available."],
    ),
    _manifest(
        "slack",
        triggers=[
            {"id": "inbound_message", "label": "Inbound message", "description": "Starts when a Slack message event arrives."},
            {"id": "mention", "label": "Mention", "description": "Starts when the Slack app is mentioned."},
            {"id": "reaction_added", "label": "Reaction added", "description": "Starts when a tracked Slack reaction is added."},
        ],
        actions=[
            {"id": "send_message", "label": "Send message"},
            {"id": "send_dm", "label": "Send DM"},
            {"id": "post_reply", "label": "Post threaded reply"},
            {"id": "upload_file", "label": "Upload file"},
            {"id": "list_channels", "label": "List channels"},
            {"id": "get_history", "label": "Get channel history"},
        ],
        resources=[
            {"id": "channels", "label": "Slack channels", "access": ["read", "write"]},
            {"id": "users", "label": "Slack users", "access": ["read"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
    ),
    _manifest(
        "github",
        triggers=[
            {"id": "push", "label": "Push", "description": "Starts when a repository push event is received."},
            {"id": "pull_request", "label": "Pull request", "description": "Starts when a pull request event is received."},
            {"id": "issues", "label": "Issue", "description": "Starts when an issue event is received."},
        ],
        actions=[
            {"id": "list_repos", "label": "List repositories"},
            {"id": "get_repo", "label": "Get repository"},
            {"id": "list_issues", "label": "List issues"},
            {"id": "create_issue", "label": "Create issue"},
            {"id": "comment_on_issue", "label": "Comment on issue"},
            {"id": "list_pull_requests", "label": "List pull requests"},
            {"id": "create_pull_request", "label": "Create pull request"},
            {"id": "get_file_content", "label": "Get file content"},
            {"id": "create_or_update_file", "label": "Create or update file"},
            {"id": "list_commits", "label": "List commits"},
        ],
        resources=[
            {"id": "repositories", "label": "Repositories", "access": ["read", "write"]},
            {"id": "issues", "label": "Issues", "access": ["read", "write"]},
            {"id": "pull_requests", "label": "Pull requests", "access": ["read", "write"]},
            {"id": "repository_contents", "label": "Repository contents", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
        notes=["Supports Personal Access Token or GitHub App installation credentials."],
    ),
    _manifest(
        "dropbox",
        triggers=[],
        actions=[
            {"id": "list_folder", "label": "List folder"},
            {"id": "upload_file", "label": "Upload file"},
            {"id": "download_file", "label": "Download file"},
            {"id": "delete", "label": "Delete path"},
            {"id": "move", "label": "Move path"},
            {"id": "get_shared_link", "label": "Get shared link"},
            {"id": "search", "label": "Search"},
        ],
        resources=[
            {"id": "files", "label": "Files", "access": ["read", "write"]},
            {"id": "folders", "label": "Folders", "access": ["read", "write"]},
            {"id": "shared_links", "label": "Shared links", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
        notes=["Supports Dropbox OAuth access tokens."],
    ),
    _manifest(
        "s3",
        triggers=[],
        actions=[
            {"id": "list_buckets", "label": "List buckets"},
            {"id": "list_objects", "label": "List objects"},
            {"id": "upload_file", "label": "Upload file"},
            {"id": "download_file", "label": "Download file"},
            {"id": "delete_object", "label": "Delete object"},
            {"id": "get_presigned_url", "label": "Get presigned URL"},
            {"id": "create_bucket", "label": "Create bucket"},
        ],
        resources=[
            {"id": "buckets", "label": "Buckets", "access": ["read", "write"]},
            {"id": "objects", "label": "Objects", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
        notes=["Supports AWS access key credentials for Amazon S3."],
    ),
    _manifest(
        "notion",
        triggers=[],
        actions=[
            {"id": "search", "label": "Search"},
            {"id": "get_page", "label": "Get page"},
            {"id": "create_page", "label": "Create page"},
            {"id": "update_page", "label": "Update page"},
            {"id": "append_blocks", "label": "Append blocks"},
            {"id": "query_database", "label": "Query database"},
            {"id": "create_database_item", "label": "Create database item"},
        ],
        resources=[
            {"id": "pages", "label": "Pages", "access": ["read", "write"]},
            {"id": "databases", "label": "Databases", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
        notes=["Supports Notion integration tokens and OAuth access tokens."],
    ),
    _manifest(
        "linear",
        triggers=[],
        actions=[
            {"id": "list_teams", "label": "List teams"},
            {"id": "list_issues", "label": "List issues"},
            {"id": "get_issue", "label": "Get issue"},
            {"id": "create_issue", "label": "Create issue"},
            {"id": "update_issue", "label": "Update issue"},
            {"id": "list_projects", "label": "List projects"},
            {"id": "add_comment", "label": "Add comment"},
        ],
        resources=[
            {"id": "teams", "label": "Teams", "access": ["read"]},
            {"id": "issues", "label": "Issues", "access": ["read", "write"]},
            {"id": "projects", "label": "Projects", "access": ["read"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
        notes=["Supports Linear personal API keys and OAuth access tokens."],
    ),
    _manifest(
        "whatsapp_twilio",
        triggers=[
            {"id": "inbound_message", "label": "Inbound message", "description": "Starts when a WhatsApp message arrives through Twilio."},
        ],
        actions=[
            {"id": "send_message", "label": "Send message"},
        ],
        resources=[
            {"id": "whatsapp_conversations", "label": "WhatsApp conversations", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
    ),
    _manifest(
        "wechat_work",
        triggers=[
            {"id": "webhook_event", "label": "Webhook event", "description": "Starts when a WeChat Work webhook event is received."},
        ],
        actions=[
            {"id": "send_message", "label": "Send message"},
        ],
        resources=[
            {"id": "webhook_channel", "label": "Webhook channel", "access": ["write"]},
        ],
        supported_targets=["auto", "cloud", "local_companion"],
    ),
    _manifest(
        "instagram_business",
        triggers=[
            {"id": "comment_created", "label": "Comment created", "description": "Starts when a tracked Instagram comment is created."},
            {"id": "dm_received", "label": "DM received", "description": "Starts when an Instagram DM is received."},
        ],
        actions=[
            {"id": "publish_reply", "label": "Publish reply"},
            {"id": "send_dm", "label": "Send DM"},
        ],
        resources=[
            {"id": "comments", "label": "Comments", "access": ["read", "write"]},
            {"id": "direct_messages", "label": "Direct messages", "access": ["read", "write"]},
        ],
        supported_targets=["auto", "cloud"],
        future_capabilities=["Publishing media workflows from the builder inspector."],
    ),
    {
        "id": "custom_api",
        "label": "Custom API / Webhook",
        "auth": {"required_fields": []},
        "triggers": [
            {"id": "webhook", "label": "Webhook", "description": "Starts when an inbound HTTP webhook is received."},
        ],
        "actions": [
            {"id": "http_request", "label": "HTTP request"},
            {"id": "signed_webhook", "label": "Signed webhook"},
        ],
        "resources": [
            {"id": "http_endpoints", "label": "HTTP endpoints", "access": ["read", "write"]},
        ],
        "runtime_constraints": {
            "supported_targets": ["auto", "cloud", "local_companion"],
        },
        "notes": ["Use this when no first-party connector exists yet."],
        "future_capabilities": [],
    },
]


def list_connector_manifests() -> Dict[str, Any]:
    return {"items": CONNECTOR_MANIFESTS}
