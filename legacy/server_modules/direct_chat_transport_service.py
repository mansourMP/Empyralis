from __future__ import annotations

import hashlib
import json
from typing import Any


def direct_chat_request_signature(body: dict) -> str:
    payload = {
        "workspace_id": str(body.get("workspace_id") or "").strip(),
        "thread_id": str(body.get("thread_id") or "").strip(),
        "client_request_id": str(body.get("client_request_id") or "").strip(),
        "message": str(body.get("message") or "").strip(),
        "provider": str(body.get("provider") or "").strip(),
        "model": str(body.get("model") or "").strip(),
        "reasoning_effort": str(body.get("reasoning_effort") or "").strip(),
        "prior_messages": body.get("prior_messages") if isinstance(body.get("prior_messages"), list) else [],
        "approved_action": body.get("approved_action") if isinstance(body.get("approved_action"), dict) else None,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def request_user_token(current_user: Any) -> str:
    return (
        str((current_user or {}).get("user_id") or "").strip()
        or str((current_user or {}).get("email") or "").strip().lower()
        or str((current_user or {}).get("auth_type") or "").strip()
        or "anonymous"
    )


def direct_chat_stream_key(
    current_user: Any,
    body: dict,
    *,
    request_signature_fn=direct_chat_request_signature,
) -> tuple[str, str, str]:
    owner = request_user_token(current_user)
    workspace_id = str(body.get("workspace_id") or "default").strip() or "default"
    thread_id = str(body.get("thread_id") or "").strip() or "direct-chat"
    client_request_id = str(body.get("client_request_id") or "").strip() or request_signature_fn(body)
    return f"{owner}:{workspace_id}:{thread_id}:{client_request_id}", thread_id, client_request_id


def direct_chat_actor_key_for_user(user_id: str, workspace_id: str, thread_id: str) -> str:
    owner = str(user_id or "").strip() or "anonymous"
    return f"{owner}:{str(workspace_id or 'default').strip() or 'default'}:{str(thread_id or 'direct-chat').strip() or 'direct-chat'}"


def direct_chat_actor_key(current_user: Any, workspace_id: str, thread_id: str) -> str:
    return direct_chat_actor_key_for_user(
        request_user_token(current_user),
        workspace_id,
        thread_id,
    )
