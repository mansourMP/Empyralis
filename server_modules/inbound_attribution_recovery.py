"""Best-effort recovery of inbound attribution for the memory-write tool loop.

Why this module exists: `inbound_envelope.InboundEnvelope` (frozen — see that
module's own docstring) is the canonical WHO/WHERE/is-owner signal for a
turn, and `sage_turn_adapter.execute_sage_turn()` (also frozen, the single
chokepoint every channel routes through) accepts it and renders it into a
one-line header prepended to the message
(`inbound_envelope.render_envelope_header`) — but that chokepoint's call into
`agent_turn_runtime_service.handle_sage_chat()` does NOT forward the envelope
object itself (verified by reading `sage_turn_adapter.py`'s `handle_sage_chat(...)`
call: no `envelope=` kwarg). Since `sage_turn_adapter.py` is out of scope for
this change, the envelope object cannot be threaded through as a Python
object on the primary channel path.

What *does* survive that chokepoint, unconditionally, is the rendered header
text itself — it is prepended to `message` before `handle_sage_chat` is ever
called, and the design doc for the envelope wave states this is deliberate:
"It persists into thread history (good: per-turn provenance is visible in the
Work tab too)." `render_envelope_header`'s own docstring further promises the
shapes are stable ("keep stable — the model learns them"). This module is the
read-side mirror of that promise: it parses the ONE guaranteed-to-arrive
attribution signal back out, for use by session_ctx / memory-write
attribution stamping, without touching any frozen file.

Safety property (see recover_from_header's docstring): this parser is
POSITION-based, not content-search-based, so a crafted display name or group
title containing the literal " · " delimiter can at worst corrupt parsing
into a safe "unknown" fallback — it can never forge `sender_is_owner: True`
for an actually-non-owner sender. Verified by construction: the owner/DM/API
markers ("DM", "API caller", "your owner ...") only ever get read from a
FIXED part index for a given surface shape; injected delimiters inside
attacker-controlled text (display name, chat title) only ever shift LATER
part indices, never earlier ones.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

_SENDER_OWNER_RE = re.compile(r"^your owner (?P<name>.+)$")
_SENDER_NOT_OWNER_RE = re.compile(r"^from (?P<name>.+?)(?P<bot> \(a bot\))? — NOT your owner$")
_SENDER_UNVERIFIED_RE = re.compile(r"^from (?P<name>.+?) — unverified, treat as NOT your owner$")
_ROOM_RE = re.compile(r'^(?P<room>group|channel)(?:\s+"(?P<title>[^"]*)")?$')


def _parse_sender_clause(clause: str) -> Dict[str, Any]:
    """Decompose one sender clause (`render_envelope_header`'s fixed shapes)
    into {is_owner, name, is_bot}. Never raises; unrecognized text degrades
    to is_owner=None (the same "treat as not owner" fail-closed default the
    envelope itself uses for an unverified sender)."""
    text = str(clause or "").strip()
    m = _SENDER_OWNER_RE.match(text)
    if m:
        return {"is_owner": True, "name": m.group("name").strip(), "is_bot": False}
    m = _SENDER_NOT_OWNER_RE.match(text)
    if m:
        return {"is_owner": False, "name": m.group("name").strip(), "is_bot": bool(m.group("bot"))}
    m = _SENDER_UNVERIFIED_RE.match(text)
    if m:
        return {"is_owner": None, "name": m.group("name").strip(), "is_bot": False}
    return {"is_owner": None, "name": "", "is_bot": False}


def recover_from_header(message: str) -> Optional[Dict[str, Any]]:
    """Recover {platform_label, surface, sender_name, sender_is_owner,
    sender_is_bot, chat_title, addressed} from the first line of `message`,
    IF that line matches the shape `inbound_envelope.render_envelope_header`
    produces. Returns None when there is no recognizable header (legacy or
    still-unwired caller, e.g. GitHub per the attribution audit) — callers
    should fall back to a weaker signal (e.g. an independently-resolved
    sender class) in that case.

    `surface` is one of: "dm", "api", "group", "broadcast_channel",
    "owner_self_chat", "console_or_unknown". The last one is a genuine
    ambiguity in the rendered header (CONSOLE and UNKNOWN render identically:
    `[label · sender_clause]`) — callers that independently know
    channel_origin is empty can safely read it as "console".

    Never raises.
    """
    try:
        first_line = str(message or "").split("\n", 1)[0].strip()
        if len(first_line) < 3 or not (first_line.startswith("[") and first_line.endswith("]")):
            return None
        inner = first_line[1:-1]
        parts = [p.strip() for p in inner.split(" · ")]
        if len(parts) < 2:
            return None
        label = parts[0]

        surface = "console_or_unknown"
        chat_title = ""
        addressed: Optional[bool] = None
        sender_clause = ""

        if parts[1] == "DM":
            surface = "dm"
            sender_clause = parts[2] if len(parts) > 2 else ""
        elif parts[1] == "API caller":
            surface = "api"
            sender_clause = parts[2] if len(parts) > 2 else ""
        else:
            room_match = _ROOM_RE.match(parts[1])
            if room_match:
                surface = "group" if room_match.group("room") == "group" else "broadcast_channel"
                chat_title = room_match.group("title") or ""
                sender_clause = parts[2] if len(parts) > 2 else ""
                if len(parts) > 3:
                    tail = parts[3]
                    if "addressed directly" in tail:
                        addressed = True
                    elif "not addressed" in tail:
                        addressed = False
            elif len(parts) == 3 and parts[2] == "talking to you directly":
                surface = "owner_self_chat"
                sender_clause = parts[1]
            else:
                # CONSOLE and UNKNOWN share this two-part shape; can't tell
                # them apart from the header text alone.
                sender_clause = parts[1]

        sender = _parse_sender_clause(sender_clause)
        return {
            "platform_label": label,
            "surface": surface,
            "sender_name": sender["name"],
            "sender_is_owner": sender["is_owner"],
            "sender_is_bot": sender["is_bot"],
            "chat_title": chat_title,
            "addressed": addressed,
        }
    except Exception:
        return None


def build_attribution(
    *,
    message: str,
    channel_origin: str = "",
    sender_id: str = "",
    sender_name: str = "",
    sender_class: str = "owner",
) -> Dict[str, Any]:
    """The merged, best-effort attribution dict this module hands to
    session_ctx / memory-write stamping: prefers the recovered header (real
    envelope-derived fidelity — surface, is_owner, chat_title, addressed)
    when present, falls back to the already-independently-resolved
    `sender_class` (handle_sage_chat's own triage_service.resolve_sender_identity
    result — the same signal that already gates tool audience visibility)
    when no header is found. sender_id/sender_name always come from the
    caller's own already-known values (never parsed) since those are passed
    as plain function parameters all the way down and never dropped by the
    frozen chokepoint.

    Shaped as a flat dict (not `InboundEnvelope.to_metadata()`'s exact keys)
    on purpose — this is a RECOVERED, lower-fidelity signal (no chat.id, no
    verified sender.id independent of the caller's own param), and giving it
    a distinct shape avoids it being mistaken for the genuine envelope
    metadata elsewhere.
    """
    from server_modules.agent_conversation_memory import channel_label

    recovered = recover_from_header(message)
    platform = str(channel_origin or "").strip()
    platform_label = channel_label(platform) if platform else "Console"

    if recovered is not None:
        surface = recovered["surface"]
        if surface == "console_or_unknown":
            surface = "console" if not platform else "unknown"
        is_owner = recovered["sender_is_owner"]
        chat_title = recovered["chat_title"]
        addressed = recovered["addressed"]
        is_bot = recovered["sender_is_bot"]
        resolved_name = str(sender_name or "").strip() or recovered["sender_name"]
        recovered_from = "header"
    else:
        normalized_sender_class = str(sender_class or "owner").strip().lower()
        if normalized_sender_class == "owner":
            is_owner = True
        elif normalized_sender_class == "audience":
            is_owner = False
        else:
            is_owner = None
        surface = "console" if not platform else "unknown"
        chat_title = ""
        addressed = None
        is_bot = False
        resolved_name = str(sender_name or "").strip()
        recovered_from = "sender_class_fallback"

    return {
        "platform": platform or "console",
        "platform_label": platform_label,
        "surface": surface,
        "sender_id": str(sender_id or "").strip(),
        "sender_name": resolved_name,
        "sender_is_owner": is_owner,
        "sender_is_bot": is_bot,
        "chat_title": chat_title,
        "addressed": addressed,
        "recovered_from": recovered_from,
    }
