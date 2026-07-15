"""Per-agent media store for personal-channel attachments (Feature B).

THE INBOUND MEDIA CONTRACT (server side of the gateway<->server boundary):
each ``channel.inbound`` event may carry an optional ``media`` array on the
message payload; every item is
``{kind: "image"|"voice"|"audio"|"video"|"file", media_id: str, mime_type: str,
filename?: str, size_bytes?: int, duration_sec?: float}``.

This module resolves ``media_id`` to actual bytes via
``gateway_protocol_service.fetch_channel_media`` (see that function's
docstring for the exact fetch RPC — a ``channel.media_fetch`` request frame
over the same live gateway WebSocket connection, since the gateway dials out
to the server and the server can never reach back in with a plain HTTP GET),
then:

  1. Writes a canonical copy under this workspace+agent's own media
     directory (``workspace_context.agent_workspace_context_dir(...)/media``)
     — the durable, per-agent-scoped store. An empty/missing agent_id is
     refused outright rather than silently falling back to a shared
     workspace-root location; see ``agent_workspace_context_dir``'s own
     docstring for why that fallback is a real cross-agent leak vector
     (the exact class of bug fixed 2026-07-14 per
     docs/PLATFORM-MAP.md Part 27).
  2. For kind in {"image", "file"}: additionally mirrors the bytes into
     ``workspace_context.workspace_attachments_dir(workspace_id)`` under a
     globally-unique, agent-namespaced filename, and returns an
     ``{filename, safe_filename, content_type}`` dict in the exact shape
     ``sage_agent_runtime_service._load_attachment_context`` /
     ``execute_sage_turn(attachments=...)`` already expects — this lets the
     model see the attachment through the SAME, already-audited pipeline
     used by the web-chat attachment upload flow, with zero changes to
     ``sage_agent_runtime_service.py``.
  3. For kind in {"voice", "audio"}: returns the fetched bytes to the
     caller (personal_channels_service), which runs them through
     ``personal_channel_transcription_service`` and splices the transcript
     into the message text — this module does not transcribe.
  4. For kind == "video": stored canonically (step 1) only; there is no
     video-understanding pipeline to hand it to yet, matching
     ``_load_attachment_context``'s existing "binary file, not supported
     for direct reading" fallback for unhandled types.

Never raises out of its public entry points: one bad/unfetchable attachment
comes back as ``{"ok": False, "error": ...}`` in its own record so the rest
of the turn (and any other attachments) proceeds normally.
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

IMAGE_ATTACHMENT_KINDS = {"image", "file"}
VOICE_MEDIA_KINDS = {"voice", "audio"}
KNOWN_MEDIA_KINDS = IMAGE_ATTACHMENT_KINDS | VOICE_MEDIA_KINDS | {"video"}

# Independent of whatever cap the gateway-fetch/frame layer already applies —
# belt-and-suspenders against a misbehaving gateway reporting a huge
# size_bytes/data_base64 for one item.
MAX_STORED_MEDIA_BYTES = 20 * 1024 * 1024


def _safe_token(value: Any, *, default: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return token or default


def _extension_for(*, filename: str, mime_type: str) -> str:
    name = str(filename or "").strip()
    if "." in name:
        ext = name.rsplit(".", 1)[-1].strip().lower()
        if ext and re.fullmatch(r"[a-z0-9]{1,8}", ext):
            return ext
    guess = str(mime_type or "").split(";")[0].split("/")[-1].strip().lower()
    if guess and re.fullmatch(r"[a-z0-9]{1,8}", guess):
        return guess
    return "bin"


def agent_media_dir(*, workspace_id: str, agent_id: str) -> Path:
    """Canonical per-agent media store root.

    Built on ``workspace_context.agent_workspace_context_dir`` — the same
    already-audited per-agent scoping helper the memory/knowledge stores
    use — so personal-channel media inherits the same isolation guarantees
    (and the same "never call this with a blank agent_id" rule).
    """
    from server_modules import workspace_context

    path = workspace_context.agent_workspace_context_dir(
        workspace_id=workspace_id,
        agent_install_id=agent_id,
    ) / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def fetch_and_store_media_item(
    *,
    gateway_id: str,
    channel_key: str,
    provider: str,
    workspace_id: str,
    agent_id: str,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve+store ONE ``media`` array item. Returns a normalized record.

    On success: ``{"ok": True, "kind", "media_id", "mime_type", "filename",
    "size_bytes", "duration_sec", "local_path", "attachment"?}`` — the
    ``attachment`` key is present only for image/file kinds.
    On failure: ``{"ok": False, "kind", "media_id", "error"}``.
    """
    kind = str((item or {}).get("kind") or "").strip().lower()
    media_id = str((item or {}).get("media_id") or "").strip()
    if not media_id:
        return {"ok": False, "kind": kind, "media_id": "", "error": "missing_media_id"}
    if kind not in KNOWN_MEDIA_KINDS:
        return {"ok": False, "kind": kind, "media_id": media_id, "error": f"unsupported_media_kind:{kind or 'unknown'}"}
    if not str(agent_id or "").strip():
        # See module docstring — refuse rather than fall back to a shared
        # workspace-root store for an unresolved agent.
        return {"ok": False, "kind": kind, "media_id": media_id, "error": "agent_id_required"}

    try:
        from server_modules import gateway_protocol_service

        fetched = await gateway_protocol_service.fetch_channel_media(
            gateway_id=gateway_id,
            channel_key=channel_key,
            provider=provider,
            media_id=media_id,
        )
    except Exception as exc:
        logger.warning(
            "media_store: fetch failed gateway=%s channel=%s media_id=%s: %s",
            gateway_id, channel_key, media_id, exc,
        )
        return {"ok": False, "kind": kind, "media_id": media_id, "error": f"fetch_failed: {exc}"}

    data_b64 = str((fetched or {}).get("data_base64") or "").strip()
    if not data_b64:
        return {"ok": False, "kind": kind, "media_id": media_id, "error": "empty_media_payload"}
    try:
        raw_bytes = base64.b64decode(data_b64, validate=False)
    except Exception:
        return {"ok": False, "kind": kind, "media_id": media_id, "error": "invalid_base64_payload"}
    if not raw_bytes:
        return {"ok": False, "kind": kind, "media_id": media_id, "error": "empty_media_payload"}
    if len(raw_bytes) > MAX_STORED_MEDIA_BYTES:
        return {"ok": False, "kind": kind, "media_id": media_id, "error": "media_too_large"}

    mime_type = str((fetched or {}).get("mime_type") or item.get("mime_type") or "application/octet-stream").strip()
    original_filename = str((fetched or {}).get("filename") or item.get("filename") or "").strip()
    size_bytes = int((fetched or {}).get("size_bytes") or len(raw_bytes) or 0)
    duration_sec = item.get("duration_sec")

    agent_token = _safe_token(agent_id, default="agent")
    media_token = _safe_token(media_id, default=uuid4().hex[:12])
    ext = _extension_for(filename=original_filename, mime_type=mime_type)
    unique_name = f"{agent_token}__{media_token}.{ext}"

    record: Dict[str, Any] = {
        "ok": True,
        "kind": kind,
        "media_id": media_id,
        "mime_type": mime_type,
        "filename": original_filename or unique_name,
        "size_bytes": size_bytes,
        "duration_sec": duration_sec,
        "channel_key": str(channel_key or "").strip(),
        "raw_bytes": raw_bytes,
    }

    try:
        canonical_path = agent_media_dir(workspace_id=workspace_id, agent_id=agent_id) / unique_name
        canonical_path.write_bytes(raw_bytes)
        record["local_path"] = str(canonical_path)
    except Exception as exc:
        logger.warning("media_store: failed to persist media_id=%s to per-agent store: %s", media_id, exc)
        return {"ok": False, "kind": kind, "media_id": media_id, "error": f"store_failed: {exc}"}

    if kind in IMAGE_ATTACHMENT_KINDS:
        try:
            from server_modules import workspace_context

            attachments_dir = workspace_context.workspace_attachments_dir(workspace_id)
            (attachments_dir / unique_name).write_bytes(raw_bytes)
            record["attachment"] = {
                "filename": record["filename"],
                "safe_filename": unique_name,
                "content_type": mime_type,
            }
        except Exception as exc:
            # Non-fatal: the canonical per-agent copy above already
            # succeeded — the turn just won't see this one as a visual
            # attachment this time.
            logger.warning("media_store: failed to mirror media_id=%s into attachment context: %s", media_id, exc)

    return record


async def process_inbound_media(
    *,
    gateway_id: str,
    channel_key: str,
    provider: str,
    workspace_id: str,
    agent_id: str,
    media: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Top-level entrypoint the inbound handlers call.

    Returns ``{"records": [...], "attachments": [...], "voice_records": [...]}``:
      - ``attachments``: ready to pass straight into
        ``build_*_personal_reply(attachments=...)`` / ``execute_sage_turn``.
      - ``voice_records``: fetched (not yet transcribed) voice/audio
        records — the caller runs each through
        ``personal_channel_transcription_service.transcribe_voice_bytes``.
    Never raises.
    """
    items = [item for item in (media or []) if isinstance(item, dict)]
    records: List[Dict[str, Any]] = []
    attachments: List[Dict[str, Any]] = []
    voice_records: List[Dict[str, Any]] = []
    if not items:
        return {"records": records, "attachments": attachments, "voice_records": voice_records}

    for item in items:
        record = await fetch_and_store_media_item(
            gateway_id=gateway_id,
            channel_key=channel_key,
            provider=provider,
            workspace_id=workspace_id,
            agent_id=agent_id,
            item=item,
        )
        records.append(record)
        if not record.get("ok"):
            continue
        kind = record.get("kind")
        if kind in IMAGE_ATTACHMENT_KINDS and isinstance(record.get("attachment"), dict):
            attachments.append(dict(record["attachment"]))
        elif kind in VOICE_MEDIA_KINDS:
            voice_records.append(record)

    return {"records": records, "attachments": attachments, "voice_records": voice_records}
