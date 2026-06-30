"""
Connector metadata helpers (public fields, identity signature, dedupe).

Extracted from server.py to reduce hotspot size.
All function signatures and behaviour are unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

_server = None  # populated by _init()


def _init():
    """Late-bind references to server.py globals. Called on first use."""
    global _server
    if _server is not None:
        return
    import server as _s
    _server = _s


def _sanitize_connector_metadata(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    _init()
    metadata = dict(raw) if isinstance(raw, dict) else {}
    if "agent_role" in metadata:
        normalized_role = _server.normalize_agent_role(metadata.get("agent_role"))
        if normalized_role:
            metadata["agent_role"] = normalized_role
        else:
            metadata.pop("agent_role", None)
    if "paused" in metadata:
        paused_value = metadata.get("paused")
        if paused_value in {None, "", 0, "0", False, "false", "False", "no", "off"}:
            metadata["paused"] = False
        else:
            metadata["paused"] = True
    return metadata


def _connector_public_metadata(connector: str, credentials: Dict[str, Any]) -> Dict[str, Any]:
    connector_id = str(connector or "").strip().lower()
    public: Dict[str, Any] = {}
    if connector_id == "telegram_bot":
        chat_id = str(credentials.get("chat_id") or "").strip()
        if chat_id:
            public["chat_id"] = chat_id
    elif connector_id == "wechat_work":
        webhook_url = str(credentials.get("webhook_url") or "").strip()
        if webhook_url:
            public["webhook_url"] = webhook_url
    elif connector_id == "whatsapp_twilio":
        from_number = str(credentials.get("from_number") or "").strip()
        to_number = str(credentials.get("to_number") or "").strip()
        if from_number:
            public["from_number"] = from_number
        if to_number:
            public["to_number"] = to_number
    elif connector_id == "google_workspace":
        auth_mode = str(credentials.get("auth_mode") or credentials.get("authMode") or "").strip()
        calendar_id = str(credentials.get("calendar_id") or "").strip()
        timezone_value = str(credentials.get("timezone") or "").strip()
        if auth_mode:
            public["auth_mode"] = auth_mode
        if calendar_id:
            public["calendar_id"] = calendar_id
        if timezone_value:
            public["timezone"] = timezone_value
    elif connector_id == "microsoft_365":
        for key in ("displayName", "mail", "userPrincipalName", "drive_id", "drive_type"):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id == "smtp":
        host = str(credentials.get("host") or "").strip()
        username = str(credentials.get("username") or "").strip()
        port = str(credentials.get("port") or "").strip()
        if host:
            public["host"] = host
        if username:
            public["username"] = username
        if port:
            public["port"] = port
        public["use_tls"] = bool(credentials.get("use_tls"))
    elif connector_id == "discord_bot":
        for key in ("channel_id", "guild_id", "channel_name", "guild_name", "bot_id", "bot_username", "bot_status", "application_id"):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id == "slack":
        for key in ("team_id", "team_name", "bot_user_id", "authed_user_id", "bot_status"):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id == "github":
        for key in ("auth_mode", "username", "installation_id"):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id == "dropbox":
        for key in ("auth_mode", "display_name", "email", "account_id"):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id in {
        "figma",
        "todoist",
        "airtable",
        "canva",
        "asana",
        "hubspot",
        "zoom",
        "calendly",
        "clickup",
        "jira",
        "stripe",
        "salesforce",
        "webflow",
        "monday",
        "box",
        "gitlab",
        "bitbucket",
        "confluence",
        "miro",
        "mailchimp",
        "pipedrive",
        "intercom",
        "docusign",
        "square",
        "typeform",
        "quickbooks",
        "xero",
        "freshbooks",
        "vercel",
    }:
        for key in (
            "auth_mode",
            "id",
            "user_id",
            "user_id_string",
            "email",
            "handle",
            "name",
            "display_name",
            "stripe_user_id",
            "instance_url",
            "account_id",
            "workspace_id",
            "team_id",
            "organization_id",
        ):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id == "s3":
        for key in ("auth_mode", "region", "access_key_hint", "bucket_count"):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id == "notion":
        for key in ("auth_mode", "workspace_name", "workspace_id"):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id == "linear":
        for key in ("auth_mode", "organization_name", "organization_id", "username"):
            value = str(credentials.get(key) or "").strip()
            if value:
                public[key] = value
    elif connector_id == "instagram_business":
        instagram_account_id = str(
            credentials.get("instagram_account_id")
            or credentials.get("business_account_id")
            or credentials.get("account_id")
            or ""
        ).strip()
        page_id = str(credentials.get("page_id") or "").strip()
        if instagram_account_id:
            public["instagram_account_id"] = instagram_account_id
        if page_id:
            public["page_id"] = page_id
    return public


def _provider_public_metadata(provider: str, credentials: Dict[str, Any]) -> Dict[str, Any]:
    _init()
    provider_id = _server.normalize_provider_id(provider)
    public: Dict[str, Any] = {}
    auth_mode = str(credentials.get("auth_mode") or credentials.get("authMode") or "").strip().lower()
    if auth_mode:
        public["auth_mode"] = auth_mode
    for key in ("api_key", "oauth_token", "access_token", "token"):
        raw_secret = str(credentials.get(key) or "").strip()
        if not raw_secret:
            continue
        if raw_secret.lower().startswith("bearer "):
            raw_secret = raw_secret[7:].strip()
        if raw_secret:
            public["credential_last4"] = raw_secret[-4:] if len(raw_secret) >= 4 else raw_secret
            break
    if provider_id == "vertex":
        project_id = str(credentials.get("project_id") or "").strip()
        location = str(credentials.get("location") or "").strip()
        if project_id:
            public["project_id"] = project_id
        if location:
            public["location"] = location
    return public


def _connector_identity_signature(connector: str, credentials: Dict[str, Any]) -> Optional[str]:
    connector_id = str(connector or "").strip().lower()
    if connector_id == "telegram_bot":
        bot_token = str(credentials.get("bot_token") or "").strip()
        chat_id = str(credentials.get("chat_id") or "").strip()
        if bot_token and chat_id:
            return f"telegram_bot:{bot_token}:{chat_id}"
    if connector_id == "wechat_work":
        webhook_url = str(credentials.get("webhook_url") or "").strip()
        if webhook_url:
            return f"wechat_work:{webhook_url}"
    if connector_id == "whatsapp_twilio":
        account_sid = str(credentials.get("account_sid") or "").strip()
        from_number = str(credentials.get("from_number") or "").strip()
        to_number = str(credentials.get("to_number") or "").strip()
        if account_sid and from_number and to_number:
            return f"whatsapp_twilio:{account_sid}:{from_number}:{to_number}"
    if connector_id == "discord_bot":
        bot_token = str(credentials.get("bot_token") or "").strip()
        channel_id = str(credentials.get("channel_id") or "").strip()
        guild_id = str(credentials.get("guild_id") or "").strip()
        if bot_token and channel_id:
            return f"discord_bot:{bot_token}:{guild_id}:{channel_id}"
    if connector_id == "smtp":
        host = str(credentials.get("host") or "").strip().lower()
        username = str(credentials.get("username") or "").strip().lower()
        port = str(credentials.get("port") or "").strip()
        if host and username:
            return f"smtp:{host}:{port}:{username}"
    if connector_id == "slack":
        team_id = str(credentials.get("team_id") or "").strip().lower()
        bot_user_id = str(credentials.get("bot_user_id") or "").strip().lower()
        app_id = str(credentials.get("app_id") or "").strip().lower()
        bot_token = str(credentials.get("bot_token") or "").strip()
        if team_id and (bot_user_id or app_id):
            return f"slack:{team_id}:{app_id}:{bot_user_id}"
        if bot_token and team_id:
            return f"slack:{team_id}:{bot_token}"
    if connector_id == "github":
        auth_mode = str(credentials.get("auth_mode") or "").strip().lower()
        username = str(credentials.get("username") or "").strip().lower()
        app_id = str(credentials.get("app_id") or "").strip()
        installation_id = str(credentials.get("installation_id") or "").strip()
        token = str(
            credentials.get("personal_access_token")
            or credentials.get("pat")
            or credentials.get("token")
            or credentials.get("access_token")
            or ""
        ).strip()
        if auth_mode == "app" and app_id and installation_id:
            return f"github:app:{app_id}:{installation_id}"
        if username:
            return f"github:pat:{username}"
        if token:
            return f"github:pat:{token}"
    if connector_id == "dropbox":
        account_id = str(credentials.get("account_id") or "").strip()
        token = str(credentials.get("access_token") or credentials.get("oauth_access_token") or credentials.get("token") or "").strip()
        if account_id:
            return f"dropbox:{account_id}"
        if token:
            return f"dropbox:{token}"
    if connector_id in {
        "figma",
        "todoist",
        "airtable",
        "canva",
        "asana",
        "hubspot",
        "zoom",
        "calendly",
        "clickup",
        "jira",
        "stripe",
        "salesforce",
        "webflow",
        "monday",
        "box",
        "gitlab",
        "bitbucket",
        "confluence",
        "miro",
        "mailchimp",
        "pipedrive",
        "intercom",
        "docusign",
        "square",
        "typeform",
        "quickbooks",
        "xero",
        "freshbooks",
        "vercel",
    }:
        account_id = str(
            credentials.get("id")
            or credentials.get("stripe_user_id")
            or credentials.get("account_id")
            or credentials.get("workspace_id")
            or credentials.get("team_id")
            or credentials.get("organization_id")
            or credentials.get("user_id_string")
            or credentials.get("user_id")
            or credentials.get("email")
            or ""
        ).strip()
        token = str(credentials.get("access_token") or credentials.get("oauth_access_token") or credentials.get("token") or "").strip()
        if account_id:
            return f"{connector_id}:{account_id}"
        if token:
            return f"{connector_id}:{token}"
    if connector_id == "s3":
        access_key_id = str(
            credentials.get("aws_access_key_id")
            or credentials.get("access_key_id")
            or credentials.get("access_key")
            or ""
        ).strip()
        region = str(credentials.get("region") or credentials.get("aws_region") or credentials.get("region_name") or "").strip()
        if access_key_id:
            return f"s3:{region}:{access_key_id}"
    if connector_id == "notion":
        workspace_id = str(credentials.get("workspace_id") or "").strip()
        token = str(
            credentials.get("integration_token")
            or credentials.get("access_token")
            or credentials.get("oauth_access_token")
            or credentials.get("token")
            or ""
        ).strip()
        if workspace_id:
            return f"notion:{workspace_id}"
        if token:
            return f"notion:{token}"
    if connector_id == "linear":
        organization_id = str(credentials.get("organization_id") or "").strip()
        username = str(credentials.get("username") or "").strip().lower()
        token = str(
            credentials.get("api_key")
            or credentials.get("personal_api_key")
            or credentials.get("access_token")
            or credentials.get("oauth_access_token")
            or credentials.get("token")
            or ""
        ).strip()
        if organization_id and username:
            return f"linear:{organization_id}:{username}"
        if token:
            return f"linear:{token}"
    if connector_id == "google_workspace":
        auth_mode = str(credentials.get("auth_mode") or credentials.get("authMode") or "").strip().lower()
        calendar_id = str(credentials.get("calendar_id") or "").strip()
        timezone_value = str(credentials.get("timezone") or "").strip()
        config_dir = str(credentials.get("gws_config_dir") or "").strip()
        access_token = str(credentials.get("access_token") or "").strip()
        if auth_mode in {"gws_local", "local_gws", "google_workspace_cli", "gws"} or config_dir:
            return f"google_workspace:gws_local:{config_dir or '.gws-config'}:{calendar_id}:{timezone_value}"
        if access_token:
            return f"google_workspace:token:{access_token}:{calendar_id}:{timezone_value}"
    if connector_id == "microsoft_365":
        account_id = str(credentials.get("account_id") or "").strip()
        user_principal_name = str(credentials.get("userPrincipalName") or credentials.get("user_principal_name") or "").strip().lower()
        drive_id = str(credentials.get("drive_id") or "").strip()
        access_token = str(credentials.get("access_token") or "").strip()
        if account_id or user_principal_name:
            return f"microsoft_365:{account_id}:{user_principal_name}:{drive_id}"
        if access_token:
            return f"microsoft_365:token:{access_token}"
    if connector_id == "instagram_business":
        instagram_account_id = str(
            credentials.get("instagram_account_id")
            or credentials.get("business_account_id")
            or credentials.get("account_id")
            or ""
        ).strip()
        page_id = str(credentials.get("page_id") or "").strip()
        if instagram_account_id:
            return f"instagram_business:{instagram_account_id}:{page_id}"
    return None


def _find_duplicate_connector_entry(
    connector: str,
    credentials: Dict[str, Any],
    workspace_id: Optional[str],
    *,
    exclude_id: str = "",
) -> Optional[Dict[str, Any]]:
    _init()
    signature = _connector_identity_signature(connector, credentials)
    if not signature:
        return None
    requested_workspace = _server._normalize_workspace_id(workspace_id)
    vault = _server.load_vault()
    existing = vault.get("credentials", [])
    if not isinstance(existing, list):
        return None
    for item in existing:
        if not isinstance(item, dict):
            continue
        if str(item.get("mode") or "").strip().lower() != "connector":
            continue
        if str(item.get("provider") or "").strip().lower() != str(connector or "").strip().lower():
            continue
        item_id = str(item.get("id") or "").strip()
        if exclude_id and item_id == exclude_id:
            continue
        if not _server._workspace_visible(item.get("workspace_id"), requested_workspace):
            continue
        try:
            secret = _server.resolve_vault_credential(item_id, _server._normalize_workspace_id(item.get("workspace_id")))
        except Exception:
            continue
        existing_signature = _connector_identity_signature(connector, secret if isinstance(secret, dict) else {})
        if existing_signature and existing_signature == signature:
            return item
    return None
