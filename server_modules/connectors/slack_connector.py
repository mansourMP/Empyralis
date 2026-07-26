from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from server_modules import mention_gating_service

SlackHttpRequest = Callable[..., Dict[str, Any]]

SLACK_API_BASE = "https://slack.com/api"
DEFAULT_SLACK_BOT_SCOPES = [
    "app_mentions:read",
    "channels:history",
    "channels:read",
    "chat:write",
    "files:write",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "im:write",
    "mpim:history",
    "reactions:read",
    "users:read",
]
DEFAULT_SLACK_USER_SCOPES = [
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "im:history",
    "mpim:history",
    "reactions:read",
    "users:read",
]
_TOKEN_REFRESH_SKEW_SECONDS = 60

# ── Deduplication guard ─────────────────────────────────────────────────────
# Slack's Events API retries a delivery (same event_id) up to 3x within a few
# seconds when it doesn't receive a fast enough ack (X-Slack-Retry-Num is set
# on the retried requests) — see slack_events_webhook in connectors_actions.py,
# which runs a full synchronous agent turn per matching connector row and, up
# through this fix, had no dedup guard, so each retry posted its own reply.
# Mirrors discord_connector._is_duplicate_discord_message exactly. Keys are
# "event_id:channel_origin" strings; entries older than 300 s are evicted on
# access (lazy TTL).
_DEDUP_CACHE: dict[str, float] = {}
_DEDUP_MAX_SIZE = 2000
_DEDUP_TTL_SECONDS = 300  # Slack's retry window is a few seconds; generous


def _is_duplicate_slack_event(event_id: str, channel_origin: str) -> bool:
    """Return True if this (event_id, channel_origin) was seen recently."""
    import time as _time

    if not event_id or not channel_origin:
        return False
    key = f"{event_id}:{channel_origin}"
    now = _time.time()

    # Lazy eviction — clean stale entries when the cache grows
    if len(_DEDUP_CACHE) > _DEDUP_MAX_SIZE:
        stale = [k for k, ts in _DEDUP_CACHE.items() if now - ts > _DEDUP_TTL_SECONDS]
        for k in stale:
            _DEDUP_CACHE.pop(k, None)

    if key in _DEDUP_CACHE:
        age = now - _DEDUP_CACHE[key]
        if age < _DEDUP_TTL_SECONDS:
            return True
        # TTL expired — allow reprocessing
        _DEDUP_CACHE.pop(key, None)

    _DEDUP_CACHE[key] = now
    return False


def _clear_slack_dedup_cache() -> None:
    """Test helper — clears the dedup cache."""
    _DEDUP_CACHE.clear()


def _env_first(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _normalize_scope_list(value: Optional[Iterable[str] | str], fallback: List[str]) -> List[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace(" ", ",").split(",")]
    elif value is None:
        raw_items = []
    else:
        raw_items = [str(item or "").strip() for item in value]
    seen: set[str] = set()
    out: List[str] = []
    for item in raw_items or fallback:
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def oauth_authorize_url(
    redirect_uri: str,
    *,
    state: str = "",
    client_id: Optional[str] = None,
    bot_scopes: Optional[Iterable[str] | str] = None,
    user_scopes: Optional[Iterable[str] | str] = None,
) -> str:
    normalized_client_id = str(client_id or _env_first("SLACK_CLIENT_ID", "NEXT_PUBLIC_SLACK_CLIENT_ID")).strip()
    if not normalized_client_id:
        raise RuntimeError("Slack client_id is not configured.")
    normalized_redirect = str(redirect_uri or "").strip()
    if not normalized_redirect:
        raise RuntimeError("Slack redirect_uri is required.")
    query = {
        "client_id": normalized_client_id,
        "scope": ",".join(
            _normalize_scope_list(
                bot_scopes or _env_first("SLACK_BOT_SCOPES"),
                DEFAULT_SLACK_BOT_SCOPES,
            )
        ),
        "user_scope": ",".join(
            _normalize_scope_list(
                user_scopes or _env_first("SLACK_USER_SCOPES"),
                DEFAULT_SLACK_USER_SCOPES,
            )
        ),
        "redirect_uri": normalized_redirect,
    }
    if str(state or "").strip():
        query["state"] = str(state).strip()
    return f"https://slack.com/oauth/v2/authorize?{urlparse.urlencode(query)}"


from server_modules.channel_sdk import _http_json_request


def _ensure_ok_response(response: Dict[str, Any], *, fallback_error: str) -> Dict[str, Any]:
    status = int(response.get("status") or 0)
    body = response.get("json") if isinstance(response.get("json"), dict) else {}
    if status == 200 and bool(body.get("ok")):
        return body
    detail = str(body.get("error") or body.get("message") or response.get("text") or "").strip()
    raise RuntimeError(detail or fallback_error)


def _oauth_basic_headers(client_id: str, client_secret: str) -> Dict[str, str]:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return {
        "Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _coerce_epoch(raw: Any) -> Optional[int]:
    if raw in {None, ""}:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _future_epoch(seconds: Any) -> Optional[int]:
    normalized = _coerce_epoch(seconds)
    if normalized is None or normalized <= 0:
        return None
    return int(time.time()) + normalized


def _normalize_team(payload: Dict[str, Any]) -> Tuple[str, str]:
    team = payload.get("team") if isinstance(payload.get("team"), dict) else {}
    team_id = str(team.get("id") or "").strip()
    team_name = str(team.get("name") or "").strip()
    return team_id, team_name


def _token_expired(credentials: Dict[str, Any], *, kind: str) -> bool:
    expires_key = "bot_token_expires_at" if kind == "bot" else "user_token_expires_at"
    expires_at = _coerce_epoch(credentials.get(expires_key))
    if expires_at is None:
        return False
    return expires_at <= int(time.time()) + _TOKEN_REFRESH_SKEW_SECONDS


def _update_credentials_from_refresh(
    credentials: Dict[str, Any],
    refreshed: Dict[str, Any],
    *,
    kind: str,
) -> Dict[str, Any]:
    token_key = "bot_token" if kind == "bot" else "user_token"
    refresh_key = "bot_refresh_token" if kind == "bot" else "user_refresh_token"
    expires_key = "bot_token_expires_at" if kind == "bot" else "user_token_expires_at"
    updated = dict(credentials)
    access_token = str(refreshed.get("access_token") or "").strip()
    refresh_token = str(refreshed.get("refresh_token") or "").strip()
    if access_token:
        updated[token_key] = access_token
    if refresh_token:
        updated[refresh_key] = refresh_token
    expires_at = _future_epoch(refreshed.get("expires_in"))
    if expires_at:
        updated[expires_key] = expires_at
    return updated


def refresh_oauth_token(
    refresh_token: str,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_refresh = str(refresh_token or "").strip()
    if not normalized_refresh:
        raise RuntimeError("Slack refresh_token is required.")
    normalized_client_id = str(client_id or _env_first("SLACK_CLIENT_ID")).strip()
    normalized_client_secret = str(client_secret or _env_first("SLACK_CLIENT_SECRET")).strip()
    if not normalized_client_id or not normalized_client_secret:
        raise RuntimeError("Slack OAuth client credentials are not configured.")
    request_fn = http_json_request or _http_json_request
    response = request_fn(
        f"{SLACK_API_BASE}/oauth.v2.access",
        method="POST",
        headers=_oauth_basic_headers(normalized_client_id, normalized_client_secret),
        payload={
            "grant_type": "refresh_token",
            "refresh_token": normalized_refresh,
        },
    )
    return _ensure_ok_response(response, fallback_error="Slack token refresh failed.")


def exchange_oauth_code(
    code: str,
    redirect_uri: str,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_code = str(code or "").strip()
    normalized_redirect = str(redirect_uri or "").strip()
    if not normalized_code:
        raise RuntimeError("Slack OAuth code is required.")
    if not normalized_redirect:
        raise RuntimeError("Slack redirect_uri is required.")
    normalized_client_id = str(client_id or _env_first("SLACK_CLIENT_ID")).strip()
    normalized_client_secret = str(client_secret or _env_first("SLACK_CLIENT_SECRET")).strip()
    if not normalized_client_id or not normalized_client_secret:
        raise RuntimeError("Slack OAuth client credentials are not configured.")
    request_fn = http_json_request or _http_json_request
    response = request_fn(
        f"{SLACK_API_BASE}/oauth.v2.access",
        method="POST",
        headers=_oauth_basic_headers(normalized_client_id, normalized_client_secret),
        payload={
            "code": normalized_code,
            "redirect_uri": normalized_redirect,
        },
    )
    body = _ensure_ok_response(response, fallback_error="Slack OAuth exchange failed.")
    team_id, team_name = _normalize_team(body)
    authed_user = body.get("authed_user") if isinstance(body.get("authed_user"), dict) else {}
    incoming_webhook = body.get("incoming_webhook") if isinstance(body.get("incoming_webhook"), dict) else {}
    credentials = {
        "bot_token": str(body.get("access_token") or "").strip(),
        "bot_refresh_token": str(body.get("refresh_token") or "").strip(),
        "bot_token_expires_at": _future_epoch(body.get("expires_in")),
        "bot_scope": str(body.get("scope") or "").strip(),
        "bot_user_id": str(body.get("bot_user_id") or "").strip(),
        "bot_token_type": str(body.get("token_type") or "bot").strip() or "bot",
        "user_token": str(authed_user.get("access_token") or "").strip(),
        "user_refresh_token": str(authed_user.get("refresh_token") or "").strip(),
        "user_token_expires_at": _future_epoch(authed_user.get("expires_in")),
        "user_scope": str(authed_user.get("scope") or "").strip(),
        "authed_user_id": str(authed_user.get("id") or "").strip(),
        "team_id": team_id,
        "team_name": team_name,
        "app_id": str(body.get("app_id") or "").strip(),
        "incoming_webhook_url": str(incoming_webhook.get("url") or "").strip(),
        "incoming_webhook_channel_id": str(incoming_webhook.get("channel_id") or "").strip(),
        "incoming_webhook_channel": str(incoming_webhook.get("channel") or "").strip(),
        "incoming_webhook_configuration_url": str(incoming_webhook.get("configuration_url") or "").strip(),
    }
    return {
        "ok": True,
        "credentials": {key: value for key, value in credentials.items() if value not in {"", None}},
        "team": {"id": team_id or None, "name": team_name or None},
        "bot_user_id": credentials.get("bot_user_id") or None,
        "authed_user_id": credentials.get("authed_user_id") or None,
    }


def _maybe_refresh_token(
    credentials: Dict[str, Any],
    *,
    kind: str,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    token_key = "bot_token" if kind == "bot" else "user_token"
    refresh_key = "bot_refresh_token" if kind == "bot" else "user_refresh_token"
    current_token = str(credentials.get(token_key) or "").strip()
    if current_token and not _token_expired(credentials, kind=kind):
        return dict(credentials)
    refresh_token = str(credentials.get(refresh_key) or "").strip()
    if not refresh_token:
        return dict(credentials)
    refreshed = refresh_oauth_token(refresh_token, http_json_request=http_json_request)
    return _update_credentials_from_refresh(credentials, refreshed, kind=kind)


def _slack_api_call(
    endpoint: str,
    *,
    token: str,
    payload: Optional[Dict[str, Any]] = None,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    request_fn = http_json_request or _http_json_request
    response = request_fn(
        f"{SLACK_API_BASE}/{endpoint}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        payload=payload or {},
    )
    return _ensure_ok_response(response, fallback_error=f"Slack API call failed: {endpoint}")


def _bot_token(credentials: Dict[str, Any], *, http_json_request: Optional[SlackHttpRequest] = None) -> Tuple[str, Dict[str, Any]]:
    updated = _maybe_refresh_token(credentials, kind="bot", http_json_request=http_json_request)
    token = str(updated.get("bot_token") or "").strip()
    if not token:
        raise RuntimeError("Slack bot token is missing.")
    return token, updated


def _user_token(credentials: Dict[str, Any], *, http_json_request: Optional[SlackHttpRequest] = None) -> Tuple[str, Dict[str, Any]]:
    updated = _maybe_refresh_token(credentials, kind="user", http_json_request=http_json_request)
    token = str(updated.get("user_token") or "").strip()
    if not token:
        raise RuntimeError("Slack user token is missing.")
    return token, updated


def validate_slack_oauth_credentials(
    credentials: Dict[str, Any],
    *,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    token, updated = _bot_token(credentials, http_json_request=http_json_request)
    auth = _slack_api_call("auth.test", token=token, http_json_request=http_json_request)
    team_id = str(auth.get("team_id") or updated.get("team_id") or "").strip()
    team_name = str(auth.get("team") or updated.get("team_name") or "").strip()
    user_validation: Dict[str, Any] | None = None
    user_token = str(updated.get("user_token") or "").strip()
    if user_token:
        try:
            user_token, updated = _user_token(updated, http_json_request=http_json_request)
            user_validation = _slack_api_call("auth.test", token=user_token, http_json_request=http_json_request)
        except Exception as exc:
            user_validation = {"error": str(exc)}
    return {
        "ok": True,
        "status": 200,
        "message": "Slack connector is valid.",
        "team": {
            "id": team_id or None,
            "name": team_name or None,
            "url": str(auth.get("url") or "").strip() or None,
        },
        "bot": {
            "user_id": str(auth.get("user_id") or updated.get("bot_user_id") or "").strip() or None,
            "bot_id": str(auth.get("bot_id") or "").strip() or None,
            "bot_status": "active",
        },
        "authed_user": {
            "id": str((user_validation or {}).get("user_id") or updated.get("authed_user_id") or "").strip() or None,
            "status": "active" if user_validation and not user_validation.get("error") else ("missing" if not user_token else "degraded"),
        },
        "credentials": updated,
    }


def send_message(
    credentials: Dict[str, Any],
    channel: str,
    text: str,
    *,
    blocks: Optional[List[Dict[str, Any]]] = None,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_channel = str(channel or "").strip()
    normalized_text = str(text or "").strip()
    if not normalized_channel:
        raise RuntimeError("Slack channel is required.")
    if not normalized_text:
        raise RuntimeError("Slack text is required.")
    token, _ = _bot_token(credentials, http_json_request=http_json_request)
    payload: Dict[str, Any] = {"channel": normalized_channel, "text": normalized_text}
    if isinstance(blocks, list) and blocks:
        payload["blocks"] = blocks
    return _slack_api_call("chat.postMessage", token=token, payload=payload, http_json_request=http_json_request)


def send_dm(
    credentials: Dict[str, Any],
    user_id: str,
    text: str,
    *,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_user = str(user_id or "").strip()
    if not normalized_user:
        raise RuntimeError("Slack user_id is required.")
    token, _ = _bot_token(credentials, http_json_request=http_json_request)
    convo = _slack_api_call(
        "conversations.open",
        token=token,
        payload={"users": normalized_user},
        http_json_request=http_json_request,
    )
    channel = convo.get("channel") if isinstance(convo.get("channel"), dict) else {}
    channel_id = str(channel.get("id") or "").strip()
    if not channel_id:
        raise RuntimeError("Slack DM channel could not be opened.")
    return send_message(credentials, channel_id, text, http_json_request=http_json_request)


def list_channels(
    credentials: Dict[str, Any],
    *,
    limit: int = 100,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 100), 200))
    token, _ = _bot_token(credentials, http_json_request=http_json_request)
    response = _slack_api_call(
        "conversations.list",
        token=token,
        payload={
            "exclude_archived": True,
            "limit": safe_limit,
            "types": "public_channel,private_channel",
        },
        http_json_request=http_json_request,
    )
    channels = response.get("channels") if isinstance(response.get("channels"), list) else []
    return [item for item in channels if isinstance(item, dict)]


def list_users(
    credentials: Dict[str, Any],
    *,
    limit: int = 100,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 100), 200))
    token, _ = _bot_token(credentials, http_json_request=http_json_request)
    response = _slack_api_call(
        "users.list",
        token=token,
        payload={"limit": safe_limit},
        http_json_request=http_json_request,
    )
    members = response.get("members") if isinstance(response.get("members"), list) else []
    return [item for item in members if isinstance(item, dict)]


def get_channel_history(
    credentials: Dict[str, Any],
    channel: str,
    *,
    limit: int = 20,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> List[Dict[str, Any]]:
    normalized_channel = str(channel or "").strip()
    if not normalized_channel:
        raise RuntimeError("Slack channel is required.")
    safe_limit = max(1, min(int(limit or 20), 200))
    token, _ = _bot_token(credentials, http_json_request=http_json_request)
    response = _slack_api_call(
        "conversations.history",
        token=token,
        payload={"channel": normalized_channel, "limit": safe_limit},
        http_json_request=http_json_request,
    )
    messages = response.get("messages") if isinstance(response.get("messages"), list) else []
    return [item for item in messages if isinstance(item, dict)]


def post_reply(
    credentials: Dict[str, Any],
    channel: str,
    thread_ts: str,
    text: str,
    *,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_thread = str(thread_ts or "").strip()
    if not normalized_thread:
        raise RuntimeError("Slack thread_ts is required.")
    token, _ = _bot_token(credentials, http_json_request=http_json_request)
    return _slack_api_call(
        "chat.postMessage",
        token=token,
        payload={
            "channel": str(channel or "").strip(),
            "thread_ts": normalized_thread,
            "text": str(text or "").strip(),
        },
        http_json_request=http_json_request,
    )


def _upload_external_file(upload_url: str, file_bytes: bytes, content_type: str) -> None:
    request_obj = urlrequest.Request(
        upload_url,
        data=file_bytes,
        method="POST",
        headers={"Content-Type": content_type or "application/octet-stream"},
    )
    with urlrequest.urlopen(request_obj, timeout=60):
        return


def upload_file(
    credentials: Dict[str, Any],
    channel: str,
    file_path: str,
    title: str,
    *,
    http_json_request: Optional[SlackHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_channel = str(channel or "").strip()
    target_path = Path(str(file_path or "").strip())
    if not normalized_channel:
        raise RuntimeError("Slack channel is required.")
    if not target_path.exists():
        raise RuntimeError("Slack upload file_path does not exist.")
    file_bytes = target_path.read_bytes()
    token, _ = _bot_token(credentials, http_json_request=http_json_request)
    prep = _slack_api_call(
        "files.getUploadURLExternal",
        token=token,
        payload={
            "filename": target_path.name,
            "length": len(file_bytes),
        },
        http_json_request=http_json_request,
    )
    upload_url = str(prep.get("upload_url") or "").strip()
    file_id = str(prep.get("file_id") or "").strip()
    if not upload_url or not file_id:
        raise RuntimeError("Slack did not return an upload URL.")
    content_type = mimetypes.guess_type(target_path.name)[0] or "application/octet-stream"
    _upload_external_file(upload_url, file_bytes, content_type)
    return _slack_api_call(
        "files.completeUploadExternal",
        token=token,
        payload={
            "channel_id": normalized_channel,
            "files": [{"id": file_id, "title": str(title or target_path.name).strip() or target_path.name}],
        },
        http_json_request=http_json_request,
    )


def verify_request_signature(
    headers: Dict[str, Any],
    raw_body: bytes,
    *,
    signing_secret: Optional[str] = None,
    now_ts: Optional[int] = None,
) -> bool:
    secret = str(signing_secret or _env_first("SLACK_SIGNING_SECRET")).strip()
    if not secret:
        raise RuntimeError("Slack signing secret is not configured.")
    normalized_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    request_ts = str(
        normalized_headers.get("x-slack-request-timestamp")
        or normalized_headers.get("x-slack-request-timestamp".lower())
        or ""
    ).strip()
    slack_signature = str(normalized_headers.get("x-slack-signature") or "").strip()
    if not request_ts or not slack_signature:
        return False
    try:
        ts_value = int(request_ts)
    except Exception:
        return False
    current_ts = int(now_ts if now_ts is not None else time.time())
    if abs(current_ts - ts_value) > 60 * 5:
        return False
    base = f"v0:{request_ts}:{raw_body.decode('utf-8')}".encode("utf-8")
    expected = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, slack_signature)


def parse_inbound_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("Slack inbound payload must be an object.")
    if str(payload.get("type") or "").strip() == "url_verification":
        return {
            "kind": "url_verification",
            "challenge": str(payload.get("challenge") or "").strip(),
        }
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_type = str(event.get("type") or "").strip()
    subtype = str(event.get("subtype") or "").strip()
    base = {
        "kind": "event",
        "event_id": str(payload.get("event_id") or "").strip() or None,
        "event_time": payload.get("event_time"),
        "team_id": str(payload.get("team_id") or "").strip() or None,
        "api_app_id": str(payload.get("api_app_id") or "").strip() or None,
        "event_type": event_type or None,
        "subtype": subtype or None,
    }
    if event_type in {"message", "app_mention"}:
        return {
            **base,
            "message_type": "mention" if event_type == "app_mention" else "message",
            "channel": str(event.get("channel") or "").strip() or None,
            "user_id": str(event.get("user") or "").strip() or None,
            "text": str(event.get("text") or "").strip(),
            "ts": str(event.get("ts") or "").strip() or None,
            "thread_ts": str(event.get("thread_ts") or event.get("ts") or "").strip() or None,
            "message_ts": str(event.get("client_msg_id") or event.get("ts") or "").strip() or None,
            "channel_type": str(event.get("channel_type") or "").strip() or None,
            # Slack stamps `bot_id` on every bot/app-authored message
            # (modern Bot-User apps posting via chat.postMessage included —
            # see should_trigger_agent_run's bot_id check), independent of
            # the legacy subtype:"bot_message" marker below.
            "bot_id": str(event.get("bot_id") or "").strip() or None,
        }
    if event_type == "reaction_added":
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        return {
            **base,
            "message_type": "reaction",
            "channel": str(item.get("channel") or "").strip() or None,
            "user_id": str(event.get("user") or "").strip() or None,
            "reaction": str(event.get("reaction") or "").strip() or None,
            "item_user": str(event.get("item_user") or "").strip() or None,
            "ts": str(event.get("event_ts") or "").strip() or None,
            "thread_ts": str(item.get("ts") or "").strip() or None,
        }
    return {
        **base,
        "message_type": "ignored",
        "raw_event": event,
    }


def event_matches_connector(
    parsed: Dict[str, Any],
    credentials: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return whether a verified Slack event belongs to this connector install."""
    if not isinstance(parsed, dict) or str(parsed.get("kind") or "").strip() != "event":
        return False
    metadata = metadata if isinstance(metadata, dict) else {}

    parsed_team_id = str(parsed.get("team_id") or "").strip()
    parsed_channel = str(parsed.get("channel") or "").strip()
    parsed_app_id = str(parsed.get("api_app_id") or "").strip()

    team_candidates = (
        credentials.get("team_id"),
        metadata.get("team_id"),
        metadata.get("slack_team_id"),
    )
    configured_team = next((str(value or "").strip() for value in team_candidates if str(value or "").strip()), "")
    if parsed_team_id and configured_team and parsed_team_id != configured_team:
        return False

    app_candidates = (
        credentials.get("api_app_id"),
        credentials.get("app_id"),
        metadata.get("api_app_id"),
        metadata.get("slack_app_id"),
    )
    configured_app = next((str(value or "").strip() for value in app_candidates if str(value or "").strip()), "")
    if parsed_app_id and configured_app and parsed_app_id != configured_app:
        return False

    channel_candidates = (
        credentials.get("channel_id"),
        metadata.get("channel_id"),
        metadata.get("slack_channel_id"),
    )
    configured_channel = next((str(value or "").strip() for value in channel_candidates if str(value or "").strip()), "")
    if configured_channel and parsed_channel and parsed_channel != configured_channel:
        return False

    return True


def should_trigger_agent_run(
    parsed: Dict[str, Any],
    credentials: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    if not event_matches_connector(parsed, credentials, metadata):
        return False
    if str(parsed.get("message_type") or "").strip() not in {"mention", "message"}:
        return False
    text = str(parsed.get("text") or "").strip()
    if not text:
        return False
    user_id = str(parsed.get("user_id") or "").strip()
    bot = credentials.get("bot") if isinstance(credentials.get("bot"), dict) else {}
    bot_user_id = str(credentials.get("bot_user_id") or bot.get("user_id") or "").strip()
    if user_id and bot_user_id and user_id == bot_user_id:
        return False
    # FIX (bot-loop): any event carrying Slack's own `bot_id` field was
    # posted by an app/bot (ours or another app's), not a human. Modern
    # Bot-User apps posting via chat.postMessage don't reliably get the
    # legacy subtype:"bot_message" marker checked just below, but Slack
    # always stamps bot_id on bot-authored messages regardless of subtype —
    # this is the primary, most robust self-loop guard. The user_id==
    # bot_user_id check above and the subtype check below are additional
    # belt-and-suspenders signals that don't depend on our own credentials
    # having the right bot_user_id recorded.
    if str(parsed.get("bot_id") or "").strip():
        return False
    subtype = str(parsed.get("subtype") or "").strip()
    if subtype in {"bot_message", "message_changed", "message_deleted"}:
        return False
    metadata = metadata if isinstance(metadata, dict) else {}
    channel_type = str(parsed.get("channel_type") or "").strip()
    # A DM is never a group — always trigger, matching every other channel's
    # dm_policy-is-separate-from-group_policy split (personal_channels_
    # service._enforce_group_policy's own is_group short-circuit).
    if channel_type == "im":
        return True
    # trigger_on_all_messages remains a valid explicit override (unaffected
    # by the mention_gating_service default below — it always triggers).
    if bool(metadata.get("trigger_on_all_messages")):
        return True
    # FIX (MAN-117): this used to hard-gate every non-mention channel message
    # (return False unless trigger_on_all_messages was explicitly set) — the
    # opposite of the locked "see-and-decide by default" policy every other
    # channel (WhatsApp/Telegram/local-bridge/cloud) already follows via
    # mention_gating_service.resolve_inbound_mention_decision with
    # require_mention=False. Slack now goes through the SAME shared resolver
    # with the SAME default: an unaddressed channel message is no longer
    # dropped here — it reaches the agent, which decides for itself whether
    # to reply (see inbound_envelope.render_envelope_header's [SILENT]
    # instruction, driven by the envelope's `addressed` field this function's
    # caller — connectors_actions.slack_events_webhook — already threads
    # through). require_mention=True remains an explicit per-connector
    # opt-in (metadata["require_mention"]) back to the old mention-only gate.
    was_mentioned = str(parsed.get("message_type") or "").strip() == "mention"
    decision = mention_gating_service.resolve_inbound_mention_decision(
        facts={"can_detect_mention": True, "was_mentioned": was_mentioned},
        policy={"is_group": True, "require_mention": bool(metadata.get("require_mention"))},
    )
    return not decision["should_skip"]


def build_run_goal_from_event(parsed: Dict[str, Any]) -> str:
    text = str(parsed.get("text") or "").strip()
    if not text:
        return ""
    return text
