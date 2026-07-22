"""Sender identity classification — owner vs audience vs unknown.

Formerly also housed "Phase P" triage: an LLM scope-classifier gate that ran
on every inbound message and, on a "no" verdict, blocked the message from
reaching the model and substituted a canned decline/silence/escalation
reply. That gate (execute_triage_gate, run_scope_check,
dispatch_out_of_scope, resolve_triage_config, and the Layer-2
identity-to-behavior dispatch built on top of resolve_sender_identity below)
was removed per founder ruling (2026-07-23): "Every single message goes to
the reasoning model, absolutely. We are not going to have filters that flag
a message and don't deliver it. No hardcoded outputs — everything is the
agent's own reasoning." See server_modules/sage_turn_adapter.py's
execute_sage_turn for the (now unconditional) call path.

resolve_sender_identity itself survives: it is a plain classifier (no
blocking, no reply substitution) consumed elsewhere for tool-visibility and
authority-tier decisions — e.g. sage_agent_runtime_service.py's per-turn
sender_class resolution and personal_channels_service.py's owner-self-chat
detection. Those are output/permission concerns (which tools an already-
in-scope turn may use), not input gating, and are outside this ruling.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ── Identity resolution ──────────────────────────────────────────────────────

def resolve_sender_identity(
    *,
    sender_id: str,
    channel_origin: str,
    channel_bindings: Optional[List[Dict[str, Any]]] = None,
    audience_sender_ids: Optional[List[str]] = None,
    audience_enabled: bool = False,
) -> str:
    """Resolve sender identity class: "owner" | "audience" | "unknown".

    "owner" = the sender matches the workspace owner's identity on this channel.
    "audience" = the sender is a known customer/audience member the agent serves.
        These senders can request service but NEVER command the agent.
    "unknown" = everyone else (no contact store exists as of Phase P).

    Channel bindings are a list of {channel_type, bot_token_hash, ...}.
    We compare the sender_id against the binding metadata to detect self-chat.

    Phase U2: audience class added. Audience senders get serve-only tools,
    no shell/hardware/fleet/memory_write/connector_write access.
    """
    if not sender_id or not str(sender_id).strip():
        return "unknown"

    normalized_sender = str(sender_id).strip()
    normalized_channel = str(channel_origin or "").strip().lower()

    # Check if the sender matches a channel binding (owner's own identity)
    for binding in (channel_bindings or []):
        if not isinstance(binding, dict):
            continue
        binding_channel = str(binding.get("channel_type") or "").strip().lower()
        if binding_channel != normalized_channel:
            continue
        # Check for owner-linked identifiers
        linked_id = str(binding.get("linked_user_id") or binding.get("bot_token_hash") or "").strip()
        if linked_id and linked_id == normalized_sender:
            return "owner"
        # Also check if the sender hash matches
        owner_hash = str(binding.get("owner_sender_hash") or "").strip()
        if owner_hash and owner_hash == normalized_sender:
            return "owner"

    # Check audience registry — known customer senders
    if audience_enabled:
        for audience_id in (audience_sender_ids or []):
            if str(audience_id or "").strip() == normalized_sender:
                return "audience"

    # If channel has audience enabled but sender isn't in the registry,
    # treat as audience anyway (stranger on an audience-facing channel)
    if audience_enabled and normalized_channel:
        return "audience"

    return "unknown"
