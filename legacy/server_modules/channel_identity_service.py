from __future__ import annotations

import re
from typing import Any, Dict, Optional


SUPPORTED_CHANNEL_KEYS = frozenset({"telegram", "whatsapp", "slack", "discord", "email", "github", "phone", "web_chat"})
SUPPORTED_FULL_SHELL_KEYS = frozenset({"mobile", "web", "desktop"})
WEB_CHAT_DEFAULT_ENDPOINT_KEY = "workspace-default"


def normalize_channel_key(value: Any) -> str:
    token = str(value or "").strip().lower()
    aliases = {
        "web": "web_chat",
        "webchat": "web_chat",
        "chat": "web_chat",
    }
    return aliases.get(token, token)


def normalize_endpoint_key(channel_key: str, endpoint_key: Any) -> Optional[str]:
    token = str(endpoint_key or "").strip().lower()
    if not token and channel_key == "web_chat":
        return WEB_CHAT_DEFAULT_ENDPOINT_KEY
    return token or None


def safe_slug(value: Any, *, fallback: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return token or fallback


def build_session_key(
    *,
    channel_key: str,
    endpoint_key: str,
    session_key: Optional[str],
    actor_id: Optional[str],
    message_id: Optional[str],
) -> str:
    explicit = str(session_key or "").strip()
    if explicit:
        return explicit
    actor_token = safe_slug(actor_id or message_id or "customer", fallback="customer")
    endpoint_token = safe_slug(endpoint_key, fallback="endpoint")
    return f"{channel_key}:{endpoint_token}:{actor_token}"


def build_thread_id(
    *,
    workspace_id: str,
    channel_key: str,
    endpoint_key: str,
    session_key: str,
) -> str:
    workspace_token = safe_slug(workspace_id, fallback="workspace")
    channel_token = safe_slug(channel_key, fallback="channel")
    endpoint_token = safe_slug(endpoint_key, fallback="endpoint")
    session_token = safe_slug(session_key, fallback="session")
    return f"thread-{workspace_token}-{channel_token}-{endpoint_token}-{session_token}"[:180]


def build_inbound_identity(
    *,
    workspace_id: str,
    channel_key: str,
    endpoint_key: str,
    session_key: Optional[str],
    message_id: Optional[str],
    actor_id: Optional[str],
    actor_display_name: Optional[str],
) -> Dict[str, str]:
    resolved_actor_id = str(actor_id or "").strip() or f"{channel_key}-customer"
    resolved_actor_display_name = str(actor_display_name or "").strip() or resolved_actor_id
    resolved_session_key = build_session_key(
        channel_key=channel_key,
        endpoint_key=endpoint_key,
        session_key=session_key,
        actor_id=resolved_actor_id,
        message_id=message_id,
    )
    resolved_thread_id = build_thread_id(
        workspace_id=workspace_id,
        channel_key=channel_key,
        endpoint_key=endpoint_key,
        session_key=resolved_session_key,
    )
    return {
        "actor_id": resolved_actor_id,
        "actor_display_name": resolved_actor_display_name,
        "session_key": resolved_session_key,
        "thread_id": resolved_thread_id,
    }
