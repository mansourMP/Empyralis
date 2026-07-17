from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

try:  # pragma: no cover - optional runtime dependency
    import discord as discord_lib
except Exception:  # pragma: no cover
    discord_lib = None  # type: ignore[assignment]

try:  # pragma: no cover - optional runtime dependency
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey
except Exception:  # pragma: no cover
    BadSignatureError = Exception  # type: ignore[assignment]
    VerifyKey = None  # type: ignore[assignment]


DiscordHttpRequest = Callable[..., Dict[str, Any]]
DiscordAppendEvent = Callable[..., Dict[str, Any]]
DiscordRunStartRequestFactory = Callable[..., Any]
DiscordStartRunRequest = Callable[[Any], Dict[str, Any]]

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_ALLOWED_WRITE_APPROVAL_ACTIONS = {"send_message", "send_dm", "delete_message"}
_MENTION_RE = re.compile(r"<@!?(\d+)>")

# ── Deduplication guard ─────────────────────────────────────────────────────
# Prevents the same Discord message from being processed more than once across
# the Gateway WebSocket (Path C/A) and HTTP Interactions endpoint (Path B).
# Keys are "message_id:channel_origin" strings.  Entries older than 300 s are
# evicted on access (lazy TTL).
_DEDUP_CACHE: dict[str, float] = {}
_DEDUP_MAX_SIZE = 2000
_DEDUP_TTL_SECONDS = 300  # Discord retry window is ~5 s; 300 s is generous


def _is_duplicate_discord_message(message_id: str, channel_origin: str) -> bool:
    """Return True if this (message_id, channel_origin) was seen recently."""
    import time as _time

    if not message_id or not channel_origin:
        return False
    key = f"{message_id}:{channel_origin}"
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


def _clear_discord_dedup_cache() -> None:
    """Test helper — clears the dedup cache."""
    _DEDUP_CACHE.clear()


def _env_first(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


from server_modules.channel_sdk import _http_json_request


def _multipart_request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    payload_json: Optional[Dict[str, Any]] = None,
    files: Sequence[Any],
    timeout: int = 30,
) -> Dict[str, Any]:
    request_headers = dict(headers or {})
    boundary = f"----empyralis-discord-{uuid.uuid4().hex}"
    request_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    lines: List[bytes] = []

    def _push(chunk: str | bytes) -> None:
        if isinstance(chunk, bytes):
            lines.append(chunk)
        else:
            lines.append(chunk.encode("utf-8"))

    if isinstance(payload_json, dict):
        _push(f"--{boundary}\r\n")
        _push('Content-Disposition: form-data; name="payload_json"\r\n\r\n')
        _push(json.dumps(payload_json, ensure_ascii=False))
        _push("\r\n")

    for index, file_item in enumerate(files):
        file_path = ""
        file_name = ""
        if isinstance(file_item, dict):
            file_path = str(file_item.get("path") or file_item.get("file_path") or "").strip()
            file_name = str(file_item.get("name") or "").strip()
        else:
            file_path = str(file_item or "").strip()
        if not file_path:
            continue
        path = Path(file_path).expanduser()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        _push(f"--{boundary}\r\n")
        _push(
            f'Content-Disposition: form-data; name="files[{index}]"; filename="{file_name or path.name}"\r\n'
        )
        _push(f"Content-Type: {mime_type}\r\n\r\n")
        _push(path.read_bytes())
        _push("\r\n")
    _push(f"--{boundary}--\r\n")

    req = urlrequest.Request(url, data=b"".join(lines), headers=request_headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text else None
            except Exception:
                parsed = None
            return {
                "status": getattr(resp, "status", 200),
                "json": parsed,
                "text": text,
                "content": raw,
                "headers": dict(getattr(resp, "headers", {}) or {}),
            }
    except urlerror.HTTPError as exc:
        raw = exc.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except Exception:
            parsed = None
        return {
            "status": int(getattr(exc, "code", 500) or 500),
            "json": parsed,
            "text": text,
            "content": raw,
            "headers": dict(getattr(exc, "headers", {}) or {}),
        }


def _bot_token(credentials: Dict[str, Any]) -> str:
    token = str(credentials.get("bot_token") or "").strip()
    if not token:
        raise RuntimeError("Discord bot_token is required.")
    if any(ch.isspace() for ch in token):
        raise RuntimeError("Discord bot_token is invalid.")
    return token


def _quoted(value: Any) -> str:
    return urlparse.quote(str(value or "").strip(), safe="")


def _discord_api_call(
    path: str,
    *,
    token: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    expected_statuses: Optional[Iterable[int]] = None,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    request_fn = http_json_request or _http_json_request
    response = request_fn(
        f"{DISCORD_API_BASE}{path}",
        method=method,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
        payload=payload,
    )
    allowed = set(expected_statuses or {200})
    status = int(response.get("status") or 0)
    if status in allowed:
        body = response.get("json")
        if isinstance(body, dict):
            return body
        if isinstance(body, list):
            return {"items": body}
        return {}
    body = response.get("json") if isinstance(response.get("json"), dict) else {}
    raw_text = str(response.get("text") or "")[:200]
    detail = str(body.get("message") or "").strip()
    discord_code = body.get("code", "")
    raise RuntimeError(
        f"Discord API {method} {path} → HTTP {status}"
        + (f" (Discord code {discord_code})" if discord_code else "")
        + (f": {detail}" if detail else "")
        + (f"\n  body: {raw_text}" if raw_text and raw_text != detail else "")
    )


def _normalize_file_list(files: Optional[Sequence[Any]]) -> List[Any]:
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes, bytearray)):
        return []
    return [item for item in files if item not in {None, ""}]


def _message_payload(
    *,
    content: str,
    embeds: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if str(content or "").strip():
        payload["content"] = str(content).strip()
    if isinstance(embeds, list) and embeds:
        payload["embeds"] = [item for item in embeds if isinstance(item, dict)]
    if not payload:
        raise RuntimeError("Discord message requires content or embeds.")
    return payload


def validate_discord_bot_credentials(
    credentials: Dict[str, Any],
    *,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    token = _bot_token(credentials)
    channel_id = str(credentials.get("channel_id") or "").strip()
    guild_id = str(credentials.get("guild_id") or "").strip()
    application_id = str(credentials.get("application_id") or "").strip()

    if channel_id and not channel_id.isdigit():
        raise RuntimeError("Discord channel_id must be numeric when provided.")
    if guild_id and not guild_id.isdigit():
        raise RuntimeError("Discord guild_id must be numeric when provided.")
    if application_id and not application_id.isdigit():
        raise RuntimeError("Discord application_id must be numeric when provided.")

    me = _discord_api_call("/users/@me", token=token, http_json_request=http_json_request)
    channel: Dict[str, Any] | None = None
    resolved_guild_id = guild_id
    if channel_id:
        channel = _discord_api_call(
            f"/channels/{_quoted(channel_id)}",
            token=token,
            http_json_request=http_json_request,
        )
        resolved_guild_id = str(channel.get("guild_id") or guild_id or "").strip()
    guild: Dict[str, Any] | None = None
    if resolved_guild_id:
        guild = _discord_api_call(
            f"/guilds/{_quoted(resolved_guild_id)}",
            token=token,
            http_json_request=http_json_request,
        )

    normalized_credentials = dict(credentials)
    if resolved_guild_id:
        normalized_credentials["guild_id"] = resolved_guild_id

    return {
        "ok": True,
        "status": 200,
        "message": "Discord connector is valid.",
        "bot": {
            "id": me.get("id"),
            "username": me.get("username"),
            "global_name": me.get("global_name"),
            "discriminator": me.get("discriminator"),
            "bot_status": "active",
        },
        "channel": (
            {
                "id": channel.get("id"),
                "name": channel.get("name"),
                "type": channel.get("type"),
            }
            if isinstance(channel, dict)
            else None
        ),
        "guild": (
            {
                "id": guild.get("id"),
                "name": guild.get("name"),
            }
            if isinstance(guild, dict)
            else None
        ),
        "application": {"id": application_id or None},
        "channel_id": channel_id or None,
        "guild_id": resolved_guild_id or None,
        "credentials": normalized_credentials,
    }


def send_message(
    credentials: Dict[str, Any],
    channel_id: str,
    content: str,
    *,
    embeds: Optional[List[Dict[str, Any]]] = None,
    files: Optional[Sequence[Any]] = None,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_channel = str(channel_id or credentials.get("channel_id") or "").strip()
    if not normalized_channel:
        raise RuntimeError("Discord channel_id is required.")
    token = _bot_token(credentials)
    payload = _message_payload(content=str(content or ""), embeds=embeds)
    normalized_files = _normalize_file_list(files)
    if not normalized_files:
        return _discord_api_call(
            f"/channels/{_quoted(normalized_channel)}/messages",
            token=token,
            method="POST",
            payload=payload,
            expected_statuses={200, 201},
            http_json_request=http_json_request,
        )

    response = _multipart_request(
        f"{DISCORD_API_BASE}/channels/{_quoted(normalized_channel)}/messages",
        headers={"Authorization": f"Bot {token}"},
        payload_json=payload,
        files=normalized_files,
    )
    status = int(response.get("status") or 0)
    if status in {200, 201}:
        body = response.get("json")
        return body if isinstance(body, dict) else {}
    body = response.get("json") if isinstance(response.get("json"), dict) else {}
    detail = str(body.get("message") or response.get("text") or "").strip()
    raise RuntimeError(detail or f"Discord send_message failed with status {status}.")


def send_embed(
    credentials: Dict[str, Any],
    channel_id: str,
    *,
    title: str = "",
    description: str = "",
    embeds: Optional[List[Dict[str, Any]]] = None,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_embeds = embeds if isinstance(embeds, list) and embeds else [
        {
            "title": str(title or "").strip() or "Empyralist",
            "description": str(description or "").strip(),
        }
    ]
    return send_message(
        credentials,
        channel_id,
        "",
        embeds=normalized_embeds,
        http_json_request=http_json_request,
    )


def send_dm(
    credentials: Dict[str, Any],
    user_id: str,
    content: str,
    *,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_user = str(user_id or "").strip()
    if not normalized_user:
        raise RuntimeError("Discord user_id is required.")
    token = _bot_token(credentials)
    dm_channel = _discord_api_call(
        "/users/@me/channels",
        token=token,
        method="POST",
        payload={"recipient_id": normalized_user},
        expected_statuses={200, 201},
        http_json_request=http_json_request,
    )
    channel_id = str(dm_channel.get("id") or "").strip()
    if not channel_id:
        raise RuntimeError("Discord DM channel could not be opened.")
    return send_message(credentials, channel_id, content, http_json_request=http_json_request)


def edit_message(
    credentials: Dict[str, Any],
    channel_id: str,
    message_id: str,
    content: str,
    *,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_channel = str(channel_id or credentials.get("channel_id") or "").strip()
    normalized_message = str(message_id or "").strip()
    if not normalized_channel or not normalized_message:
        raise RuntimeError("Discord edit_message requires channel_id and message_id.")
    return _discord_api_call(
        f"/channels/{_quoted(normalized_channel)}/messages/{_quoted(normalized_message)}",
        token=_bot_token(credentials),
        method="PATCH",
        payload={"content": str(content or "").strip()},
        expected_statuses={200},
        http_json_request=http_json_request,
    )


def delete_message(
    credentials: Dict[str, Any],
    channel_id: str,
    message_id: str,
    *,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_channel = str(channel_id or credentials.get("channel_id") or "").strip()
    normalized_message = str(message_id or "").strip()
    if not normalized_channel or not normalized_message:
        raise RuntimeError("Discord delete_message requires channel_id and message_id.")
    _discord_api_call(
        f"/channels/{_quoted(normalized_channel)}/messages/{_quoted(normalized_message)}",
        token=_bot_token(credentials),
        method="DELETE",
        expected_statuses={200, 202, 204},
        http_json_request=http_json_request,
    )
    return {"ok": True, "deleted": True, "channel_id": normalized_channel, "message_id": normalized_message}


def list_guilds(
    credentials: Dict[str, Any],
    *,
    limit: int = 100,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 100), 100))
    response = _discord_api_call(
        f"/users/@me/guilds?limit={safe_limit}",
        token=_bot_token(credentials),
        expected_statuses={200},
        http_json_request=http_json_request,
    )
    items = response.get("items") if isinstance(response.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def list_channels(
    credentials: Dict[str, Any],
    guild_id: str,
    *,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> List[Dict[str, Any]]:
    normalized_guild = str(guild_id or credentials.get("guild_id") or "").strip()
    if not normalized_guild:
        raise RuntimeError("Discord list_channels requires guild_id.")
    response = _discord_api_call(
        f"/guilds/{_quoted(normalized_guild)}/channels",
        token=_bot_token(credentials),
        expected_statuses={200},
        http_json_request=http_json_request,
    )
    items = response.get("items") if isinstance(response.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def list_members(
    credentials: Dict[str, Any],
    guild_id: str,
    *,
    limit: int = 100,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> List[Dict[str, Any]]:
    normalized_guild = str(guild_id or credentials.get("guild_id") or "").strip()
    if not normalized_guild:
        raise RuntimeError("Discord list_members requires guild_id.")
    safe_limit = max(1, min(int(limit or 100), 1000))
    response = _discord_api_call(
        f"/guilds/{_quoted(normalized_guild)}/members?limit={safe_limit}",
        token=_bot_token(credentials),
        expected_statuses={200},
        http_json_request=http_json_request,
    )
    items = response.get("items") if isinstance(response.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def get_message_history(
    credentials: Dict[str, Any],
    channel_id: str,
    *,
    limit: int = 20,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> List[Dict[str, Any]]:
    normalized_channel = str(channel_id or credentials.get("channel_id") or "").strip()
    if not normalized_channel:
        raise RuntimeError("Discord get_message_history requires channel_id.")
    safe_limit = max(1, min(int(limit or 20), 100))
    response = _discord_api_call(
        f"/channels/{_quoted(normalized_channel)}/messages?limit={safe_limit}",
        token=_bot_token(credentials),
        expected_statuses={200},
        http_json_request=http_json_request,
    )
    items = response.get("items") if isinstance(response.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def create_thread(
    credentials: Dict[str, Any],
    channel_id: str,
    name: str,
    *,
    message_id: Optional[str] = None,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_channel = str(channel_id or credentials.get("channel_id") or "").strip()
    normalized_name = str(name or "").strip()
    normalized_message = str(message_id or "").strip()
    if not normalized_channel:
        raise RuntimeError("Discord create_thread requires channel_id.")
    if not normalized_name:
        raise RuntimeError("Discord create_thread requires a name.")
    if normalized_message:
        path = f"/channels/{_quoted(normalized_channel)}/messages/{_quoted(normalized_message)}/threads"
        payload = {"name": normalized_name, "auto_archive_duration": 1440}
    else:
        path = f"/channels/{_quoted(normalized_channel)}/threads"
        payload = {
            "name": normalized_name,
            "auto_archive_duration": 1440,
            "type": 11,
        }
    return _discord_api_call(
        path,
        token=_bot_token(credentials),
        method="POST",
        payload=payload,
        expected_statuses={200, 201},
        http_json_request=http_json_request,
    )


def add_reaction(
    credentials: Dict[str, Any],
    channel_id: str,
    message_id: str,
    emoji: str,
    *,
    http_json_request: Optional[DiscordHttpRequest] = None,
) -> Dict[str, Any]:
    normalized_channel = str(channel_id or credentials.get("channel_id") or "").strip()
    normalized_message = str(message_id or "").strip()
    normalized_emoji = str(emoji or "").strip()
    if not normalized_channel or not normalized_message or not normalized_emoji:
        raise RuntimeError("Discord add_reaction requires channel_id, message_id, and emoji.")
    _discord_api_call(
        f"/channels/{_quoted(normalized_channel)}/messages/{_quoted(normalized_message)}/reactions/{_quoted(normalized_emoji)}/@me",
        token=_bot_token(credentials),
        method="PUT",
        payload=None,
        expected_statuses={200, 204},
        http_json_request=http_json_request,
    )
    return {
        "ok": True,
        "channel_id": normalized_channel,
        "message_id": normalized_message,
        "emoji": normalized_emoji,
    }


def interaction_signature_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    raw_headers = headers if isinstance(headers, dict) else dict(headers or {})
    return {
        "signature": str(raw_headers.get("x-signature-ed25519") or raw_headers.get("X-Signature-Ed25519") or "").strip(),
        "timestamp": str(raw_headers.get("x-signature-timestamp") or raw_headers.get("X-Signature-Timestamp") or "").strip(),
    }


def verify_interaction_signature(headers: Dict[str, Any], raw_body: bytes, public_key: str) -> bool:
    verification_headers = interaction_signature_headers(headers)
    signature = verification_headers["signature"]
    timestamp = verification_headers["timestamp"]
    normalized_key = str(public_key or "").strip()
    if not signature or not timestamp or not normalized_key:
        return False
    if VerifyKey is None:  # pragma: no cover - depends on optional PyNaCl runtime
        raise RuntimeError("PyNaCl is required to verify Discord interaction signatures.")
    try:  # pragma: no branch
        verify_key = VerifyKey(bytes.fromhex(normalized_key))
        verify_key.verify(timestamp.encode("utf-8") + bytes(raw_body), bytes.fromhex(signature))
        return True
    except (BadSignatureError, TypeError, ValueError):
        return False


def _interaction_option_text(options: Any) -> str:
    if not isinstance(options, list):
        return ""
    parts: List[str] = []
    for item in options:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = item.get("value")
        if value not in {None, ""}:
            parts.append(f"{name}={value}" if name else str(value))
        nested = _interaction_option_text(item.get("options"))
        if nested:
            parts.append(nested)
    return " ".join(part for part in parts if part).strip()


def _mention_ids_from_payload(payload: Dict[str, Any]) -> List[str]:
    mentions = payload.get("mentions") if isinstance(payload.get("mentions"), list) else []
    out: List[str] = []
    for item in mentions:
        if not isinstance(item, dict):
            continue
        value = str(item.get("id") or "").strip()
        if value:
            out.append(value)
    return out


def parse_inbound_event(payload: Dict[str, Any], *, event_type: str = "") -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("Discord inbound payload must be an object.")

    interaction_type = payload.get("type")
    if interaction_type == 1:
        return {"kind": "ping"}

    if interaction_type in {2, 3, 5}:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        member = payload.get("member") if isinstance(payload.get("member"), dict) else {}
        member_user = member.get("user") if isinstance(member.get("user"), dict) else {}
        fallback_user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        user = member_user or fallback_user
        channel_id = str(payload.get("channel_id") or "").strip()
        guild_id = str(payload.get("guild_id") or "").strip()
        command_name = str(data.get("name") or "").strip()
        option_text = _interaction_option_text(data.get("options"))
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        custom_id = str(data.get("custom_id") or "").strip()
        return {
            "kind": "interaction",
            "event_type": "slash_command" if interaction_type == 2 else ("message_component" if interaction_type == 3 else "interaction"),
            "interaction_id": str(payload.get("id") or "").strip() or None,
            "application_id": str(payload.get("application_id") or "").strip() or None,
            "channel_id": channel_id or None,
            "guild_id": guild_id or None,
            "user_id": str(user.get("id") or "").strip() or None,
            "username": str(user.get("username") or user.get("global_name") or "").strip() or None,
            "command_name": command_name or None,
            "message_id": str(message.get("id") or "").strip() or None,
            "thread_id": str((message.get("thread") or {}).get("id") or "").strip() if isinstance(message.get("thread"), dict) else None,
            "custom_id": custom_id or None,
            "text": " ".join(part for part in [f"/{command_name}" if command_name else "", option_text or custom_id] if part).strip(),
            "raw_event": payload,
        }

    gateway_event = str(event_type or payload.get("t") or "").strip().upper()
    data = payload.get("d") if isinstance(payload.get("d"), dict) else payload
    author = data.get("author") if isinstance(data.get("author"), dict) else {}

    if gateway_event in {"MESSAGE_CREATE", ""} and str(data.get("content") or "").strip():
        message_type = "message"
        if _mention_ids_from_payload(data):
            message_type = "mention"
        if str((data.get("channel_id") or "")).strip() and str(data.get("guild_id") or "").strip() == "":
            message_type = "direct_message"
        return {
            "kind": "event",
            "event_type": "message_create",
            "message_type": message_type,
            "channel_id": str(data.get("channel_id") or "").strip() or None,
            "guild_id": str(data.get("guild_id") or "").strip() or None,
            "message_id": str(data.get("id") or "").strip() or None,
            "thread_id": str((data.get("thread") or {}).get("id") or "").strip() if isinstance(data.get("thread"), dict) else None,
            "user_id": str(author.get("id") or "").strip() or None,
            "username": str(author.get("username") or author.get("global_name") or "").strip() or None,
            "text": str(data.get("content") or "").strip(),
            "mention_ids": _mention_ids_from_payload(data),
            "raw_event": data,
        }

    if gateway_event == "MESSAGE_REACTION_ADD":
        member = data.get("member") if isinstance(data.get("member"), dict) else {}
        user = member.get("user") if isinstance(member.get("user"), dict) else {}
        emoji = data.get("emoji") if isinstance(data.get("emoji"), dict) else {}
        emoji_name = str(emoji.get("name") or "").strip()
        emoji_id = str(emoji.get("id") or "").strip()
        return {
            "kind": "event",
            "event_type": "reaction_add",
            "message_type": "reaction",
            "channel_id": str(data.get("channel_id") or "").strip() or None,
            "guild_id": str(data.get("guild_id") or "").strip() or None,
            "message_id": str(data.get("message_id") or "").strip() or None,
            "user_id": str(user.get("id") or data.get("user_id") or "").strip() or None,
            "username": str(user.get("username") or "").strip() or None,
            "reaction": f"{emoji_name}:{emoji_id}" if emoji_id else (emoji_name or None),
            "raw_event": data,
        }

    return {
        "kind": "event",
        "event_type": gateway_event.lower() if gateway_event else "unknown",
        "message_type": "ignored",
        "raw_event": data,
    }


def event_matches_connector(
    parsed: Dict[str, Any],
    credentials: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    metadata_value = metadata if isinstance(metadata, dict) else {}
    # OAuth identity match — if this credential is linked to a specific Discord user,
    # only match events from that user (used for one-click "Connect Discord" OAuth flow).
    configured_discord_user_id = str(credentials.get("discord_user_id") or "").strip()
    if configured_discord_user_id:
        event_user_id = str(parsed.get("user_id") or "").strip()
        return event_user_id == configured_discord_user_id
    # Channel/guild match — legacy manual credential flow.
    channel_id = str(parsed.get("channel_id") or "").strip()
    guild_id = str(parsed.get("guild_id") or "").strip()
    configured_channel = str(credentials.get("channel_id") or "").strip()
    configured_guild = str(credentials.get("guild_id") or "").strip()
    allowed_channels = str(
        metadata_value.get("allowed_channel_ids")
        or credentials.get("allowed_channel_ids")
        or configured_channel
        or ""
    ).strip()
    allowed_channel_ids = [item.strip() for item in allowed_channels.split(",") if item.strip()]
    if allowed_channel_ids and channel_id and channel_id not in allowed_channel_ids:
        return False
    if configured_guild and guild_id and configured_guild != guild_id:
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
    kind = str(parsed.get("kind") or "").strip().lower()
    if kind == "interaction":
        return True
    if str(parsed.get("message_type") or "").strip().lower() == "reaction":
        return False

    metadata_value = metadata if isinstance(metadata, dict) else {}
    trigger_pattern = str(
        metadata_value.get("discord_trigger_pattern")
        or metadata_value.get("trigger_pattern")
        or credentials.get("trigger_pattern")
        or ""
    ).strip()
    text = str(parsed.get("text") or "").strip()
    if trigger_pattern:
        try:
            return bool(re.search(trigger_pattern, text, flags=re.IGNORECASE))
        except re.error:
            return trigger_pattern.lower() in text.lower()
    message_type = str(parsed.get("message_type") or "").strip().lower()
    return message_type in {"mention", "direct_message"}


def build_run_goal_from_event(parsed: Dict[str, Any]) -> str:
    kind = str(parsed.get("kind") or "").strip().lower()
    if kind == "interaction":
        command_name = str(parsed.get("command_name") or "").strip()
        text = str(parsed.get("text") or "").strip()
        return text or (f"/{command_name}" if command_name else "")
    text = str(parsed.get("text") or "").strip()
    if text:
        text = _MENTION_RE.sub("", text).strip()
    return text


def dispatch_inbound_event(
    parsed: Dict[str, Any],
    *,
    connector_entry: Dict[str, Any],
    credentials: Dict[str, Any],
    append_event_fn: Optional[DiscordAppendEvent] = None,
    execute_agent_turn_request: Optional[Callable[..., Dict[str, Any]]] = None,
    run_start_request_class: Optional[DiscordRunStartRequestFactory] = None,
    start_run_request: Optional[DiscordStartRunRequest] = None,
    resolve_workspace_scope_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
) -> Dict[str, Any]:
    from server_modules.agent_turn import bind_agent_turn_metadata, build_discord_turn_request
    from server_modules.run_service import build_run_start_request_from_turn

    metadata = connector_entry.get("metadata") if isinstance(connector_entry.get("metadata"), dict) else {}
    if callable(resolve_workspace_scope_fn):
        workspace_id = str(resolve_workspace_scope_fn(connector_entry) or "").strip() or "default"
    else:
        workspace_id = str(connector_entry.get("workspace_id") or "default").strip() or "default"
    trace_id = (
        str(parsed.get("interaction_id") or parsed.get("message_id") or "").strip()
        or uuid.uuid4().hex
    )
    session_key = (
        str(parsed.get("channel_id") or "").strip()
        or str(parsed.get("guild_id") or "").strip()
        or "discord"
    )
    if callable(append_event_fn):
        append_event_fn(
            channel="discord",
            direction="inbound",
            event_type=str(parsed.get("event_type") or parsed.get("message_type") or "event"),
            text=str(parsed.get("text") or parsed.get("reaction") or "").strip() or None,
            workspace_id=workspace_id,
            session_key=session_key,
            message_id=str(parsed.get("message_id") or parsed.get("interaction_id") or "").strip() or None,
            trace_id=f"discord:{trace_id}",
            metadata=parsed,
        )

    if not should_trigger_agent_run(parsed, credentials, metadata=metadata):
        return {"ok": True, "triggered": False}

    goal = build_run_goal_from_event(parsed)
    if not goal:
        return {"ok": True, "triggered": False}

    turn_request = build_discord_turn_request(
        parsed=parsed,
        connector_entry=connector_entry,
        goal=goal,
        trace_id=trace_id,
    )
    context = {
        "workflow_id": None,
        "workspace_id": workspace_id,
        "user_goal": goal,
        "business_plan": None,
        "provider": None,
        "model": None,
        "credential_id": None,
        "agents": [],
        "metadata": {
            "source": "discord_connector",
            "channel": "discord",
            "source_channel": "discord",
            "connector_credential_id": str(connector_entry.get("id") or "").strip() or None,
            "source_connector_credential_id": str(connector_entry.get("id") or "").strip() or None,
            "trace_id": f"discord:{trace_id}",
            "source_event_id": str(parsed.get("message_id") or parsed.get("interaction_id") or "").strip() or None,
            "delivery_status": "pending",
            "discord": {
                "channel_id": str(parsed.get("channel_id") or "").strip() or None,
                "guild_id": str(parsed.get("guild_id") or "").strip() or None,
                "user_id": str(parsed.get("user_id") or "").strip() or None,
                "message_id": str(parsed.get("message_id") or "").strip() or None,
                "interaction_id": str(parsed.get("interaction_id") or "").strip() or None,
                "event_type": str(parsed.get("event_type") or "").strip() or None,
            },
        },
    }
    context["metadata"] = bind_agent_turn_metadata(
        context["metadata"],
        turn_request,
        source="discord_connector",
    )
    from server_modules import runtime_config as _runtime_config

    owner_user_id = _runtime_config.agent_machine_inherited_owner_user_id()
    if owner_user_id:
        context["metadata"]["owner_user_id"] = owner_user_id

    run_id = ""
    if callable(execute_agent_turn_request):
        created = execute_agent_turn_request(turn_request=turn_request)
        if isinstance(created, dict):
            run_id = str(created.get("run_id") or "").strip()
        elif created is not None:
            run_id = str(created or "").strip()

    if not run_id and callable(start_run_request) and callable(run_start_request_class):
        base_request = run_start_request_class(
            engine="orion",
            workspace_id=workspace_id,
            user_goal=goal,
            metadata=context["metadata"],
        )
        request = build_run_start_request_from_turn(turn_request, base_request=base_request)
        if owner_user_id:
            request.metadata["owner_user_id"] = owner_user_id
        created = start_run_request(request)
        if isinstance(created, dict):
            run_id = str(created.get("run_id") or "").strip()
        elif created is not None:
            run_id = str(created or "").strip()

    if not run_id:
        raise RuntimeError(
            "Discord inbound dispatch requires canonical ingress callbacks. "
            "Configure execute_agent_turn_request or start_run_request."
        )

    if callable(append_event_fn) and run_id:
        append_event_fn(
            channel="discord",
            direction="system",
            event_type="run_started",
            text=f"Run started for Discord session {session_key}",
            workspace_id=workspace_id,
            session_key=session_key,
            run_id=run_id,
            trace_id=f"discord:{trace_id}",
            metadata={
                "run_id": run_id,
                "channel_id": str(parsed.get("channel_id") or "").strip() or None,
                "guild_id": str(parsed.get("guild_id") or "").strip() or None,
            },
        )
    return {"ok": True, "triggered": bool(run_id), "run_id": run_id or None}


async def _handle_dm_via_gateway(message: Any) -> None:
    """CANONICAL Discord DM handler (Path C).

    Processes a direct message through Sage and replies via the Gateway
    WebSocket.  Uses ``message.channel.send()`` (the native discord.py method)
    instead of the REST API.

    This is the *single source of truth* for Discord DM text messages.
    All DMs arrive through the Gateway WebSocket and are intercepted by
    ``DiscordGatewayListener.on_message`` BEFORE the ``_on_event`` callback
    fires — so the DM handler in ``DiscordBotRuntimeService.handle_parsed_event``
    (Path A) is never reached for DMs.

    Slash commands / interactions in DMs are handled separately by the
    HTTP Interactions endpoint in ``connectors_actions.discord_webhook``
    (Path B).
    """
    import logging as _hdlr_log

    _text = str(getattr(message, "content", "") or "").strip()
    _author_id = str(getattr(getattr(message, "author", None), "id", "") or "").strip()
    _author_name = str(getattr(getattr(message, "author", None), "name", "") or "").strip()

    if not _text or not _author_id:
        return

    _reply: str = ""
    _workspace_id: str = ""
    try:
        # ── /pair <code> — link Discord user to workspace ──
        _pair_match = re.match(r"^/pair\s+(\S+)", _text)
        if _pair_match:
            _code = _pair_match.group(1).strip()
            from server_modules.sage_telegram_hosted_service import consume_pairing_code
            from server_modules.discord_pairing_service import (
                pair_discord_workspace,
            )
            _wid = consume_pairing_code(_code)
            if _wid:
                pair_discord_workspace(_author_id, _wid)
                _reply = (
                    "✅ Connected to Empyralis! Your workspace is now linked. "
                    "Try sending a message."
                )
            else:
                _reply = (
                    "Invalid or expired pairing code. "
                    "Generate a new one at empyralis.ai → Connections → Discord."
                )
            if _reply:
                await message.channel.send(_reply)
            return

        # ── Workspace lookup ──
        from server_modules.discord_pairing_service import (
            get_workspace_for_discord_user,
        )
        _workspace_id = get_workspace_for_discord_user(_author_id)
        if not _workspace_id:
            await message.channel.send(
                "Send `/pair CODE` to connect your workspace. "
                "Get a code at empyralis.ai → Connections → Discord."
            )
            return

        # ── Command dispatch ──
        _msg_id = str(getattr(message, "id", "") or "").strip()
        if _msg_id and _is_duplicate_discord_message(_msg_id, "discord_personal"):
            _hdlr_log.getLogger("discord_bot").info(
                "DM dedup: skipping duplicate message_id=%s", _msg_id
            )
            return

        from server_modules.sage_command_dispatcher import dispatch_command as _dc
        _cmd_reply = await _dc(
            command=_text,
            workspace_id=_workspace_id,
            thread_id="sage-main",
            channel_origin="discord_personal",
            sender_id=_author_id or None,
        )
        if _cmd_reply is not None:
            _reply = _cmd_reply
        else:
            # ── Sage ingress ──
            from server_modules.sage_turn_adapter import execute_sage_turn as _est
            _result = await _est(
                workspace_id=_workspace_id,
                message=_text,
                channel_origin="discord_personal",
                channel_sender_id=_author_id,
                channel_sender_name=_author_name or None,
                thread_id="sage-main",
            )
            _raw = str(_result.message or "").strip()
            if _raw:
                # ABSOLUTE RULE: no hardcoded platform status/error message
                # may EVER be sent into a channel. filter_channel_outbound_reply
                # (not the bare filter_outbound_reply) also catches a
                # status/error string smuggled in as a "successful" reply
                # (e.g. TOOLS_LIMITED_NO_REPLY, a cli_subscription failure).
                from server_modules.channel_adapter import filter_channel_outbound_reply as _filt
                _reply = _filt(_raw) or ""
    except Exception as _exc:
        _hdlr_log.getLogger("discord_bot").warning(
            "DM gateway handler failed for workspace=%s: %s", _workspace_id, _exc
        )
        # ABSOLUTE RULE: no hardcoded status/error message may EVER be sent
        # into a channel — log + surface on the dashboard/activity feed,
        # but the DM gets nothing (never "Something went wrong...").
        try:
            from server_modules import durability_signal

            durability_signal.capture_durability_failure(
                f"discord DM turn error suppressed for workspace={_workspace_id}",
                _exc,
                workspace_id=_workspace_id,
                channel="discord_personal",
                event_class="channel_error_suppressed",
                action="turn_error",
                summary=str(_exc)[:500],
            )
        except Exception:
            pass
        _reply = ""

    if _reply:
        try:
            await message.channel.send(_reply)
            _hdlr_log.getLogger("discord_bot").info(
                "DM reply sent via gateway: %s", _reply[:80]
            )
        except Exception as _send_exc:
            _hdlr_log.getLogger("discord_bot").warning(
                "DM gateway send failed: %s", _send_exc
            )


class DiscordGatewayListener:
    def __init__(
        self,
        credentials: Dict[str, Any],
        *,
        allowed_channel_ids: Optional[Sequence[str]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        if discord_lib is None:  # pragma: no cover - depends on optional runtime dependency
            raise RuntimeError("discord.py is required for live Discord gateway listening.")
        self._credentials = dict(credentials)
        self._allowed_channel_ids = {str(item).strip() for item in (allowed_channel_ids or []) if str(item).strip()}
        self._on_event = on_event
        intents = discord_lib.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        intents.members = True
        self._client = discord_lib.Client(intents=intents)
        self._register_handlers()

    def _register_handlers(self) -> None:
        import logging as _disc_log

        @self._client.event
        async def on_ready() -> None:
            _disc_log.getLogger("discord_bot").warning(
                "DISCORD_READY user=%s id=%s guilds=%s",
                str(getattr(self._client, "user", None)),
                str(getattr(getattr(self._client, "user", None), "id", "") or ""),
                [str(g.id) for g in getattr(self._client, "guilds", []) or []],
            )

        @self._client.event
        async def on_message(message: Any) -> None:
            _disc_log.getLogger("discord_bot").warning(
                "DISCORD_ON_MESSAGE author=%s author_id=%s channel=%s guild=%s content=%s bot=%s",
                getattr(getattr(message, "author", None), "name", "?"),
                str(getattr(getattr(message, "author", None), "id", "") or ""),
                str(getattr(message.channel, "id", "") or ""),
                str(getattr(getattr(message, "guild", None), "id", "") or ""),
                str(getattr(message, "content", "") or "")[:80],
                getattr(message.author, "bot", False),
            )
            if getattr(message.author, "bot", False):
                return
            channel_id = str(getattr(message.channel, "id", "") or "").strip()
            if self._allowed_channel_ids and channel_id not in self._allowed_channel_ids:
                return

            # ── DM: reply directly via gateway WebSocket (no REST API) ──
            is_dm = getattr(message, "guild", None) is None
            if is_dm:
                await _handle_dm_via_gateway(message)
                return

            parsed = parse_inbound_event(
                {
                    "t": "MESSAGE_CREATE",
                    "d": {
                        "id": str(getattr(message, "id", "") or ""),
                        "channel_id": channel_id,
                        "guild_id": str(getattr(getattr(message, "guild", None), "id", "") or ""),
                        "content": str(getattr(message, "content", "") or ""),
                        "author": {
                            "id": str(getattr(getattr(message, "author", None), "id", "") or ""),
                            "username": str(getattr(getattr(message, "author", None), "name", "") or ""),
                        },
                        "mentions": [
                            {"id": str(getattr(item, "id", "") or "")}
                            for item in list(getattr(message, "mentions", []) or [])
                        ],
                    },
                }
            )
            if callable(self._on_event):
                self._on_event(parsed)

    def run_forever(self) -> None:  # pragma: no cover - optional runtime dependency
        self._client.run(_bot_token(self._credentials))


__all__ = [
    "DISCORD_ALLOWED_WRITE_APPROVAL_ACTIONS",
    "DiscordGatewayListener",
    "add_reaction",
    "build_run_goal_from_event",
    "create_thread",
    "delete_message",
    "dispatch_inbound_event",
    "edit_message",
    "event_matches_connector",
    "get_message_history",
    "list_channels",
    "list_guilds",
    "list_members",
    "parse_inbound_event",
    "send_dm",
    "send_embed",
    "send_message",
    "should_trigger_agent_run",
    "validate_discord_bot_credentials",
    "verify_interaction_signature",
]
