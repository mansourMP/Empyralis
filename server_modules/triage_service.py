"""Phase P: Per-agent inbound triage — scope + identity gates before reasoning.

Two layers run BEFORE the main LLM loop:
  Layer 1 (scope): one cheap LLM call → {in_scope: yes|no|uncertain}
  Layer 2 (identity): sender identity vs channel binding → behavior rule

Every decision is ledgered. Triaging is opt-in per agent (install_metadata.triage.enabled).
Uncertain or any error = fail-open into the main reasoning loop.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ── Triage config schema ─────────────────────────────────────────────────────

def resolve_triage_config(agent_install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read the triage config from an agent's install_metadata.

    Returns the config dict with defaults. If triage.enabled is not True,
    triaging is skipped entirely (zero behavior change for existing agents).
    """
    if not agent_install or not isinstance(agent_install, dict):
        return _default_triage_config()

    meta = dict(agent_install.get("install_metadata") or agent_install.get("metadata") or {})
    triage = dict(meta.get("triage") or {})

    return {
        "enabled": bool(triage.get("enabled", False)),
        "scope_description": str(triage.get("scope_description") or "").strip(),
        "out_of_scope_behavior": str(triage.get("out_of_scope_behavior") or "polite_decline").strip().lower(),
        "identity_rules": _normalize_identity_rules(triage.get("identity_rules")),
        "uncertain_goes_to_full_loop": True,  # hard default, not configurable
    }


def _default_triage_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "scope_description": "",
        "out_of_scope_behavior": "polite_decline",
        "identity_rules": [
            {"match": "owner", "behavior": "full"},
            {"match": "audience", "behavior": "restricted"},
            {"match": "unknown", "behavior": "restricted"},
        ],
        "uncertain_goes_to_full_loop": True,
    }


def _normalize_identity_rules(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return [
            {"match": "owner", "behavior": "full"},
            {"match": "audience", "behavior": "restricted"},
            {"match": "unknown", "behavior": "restricted"},
        ]
    rules: List[Dict[str, str]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        match = str(r.get("match") or "").strip().lower()
        behavior = str(r.get("behavior") or "full").strip().lower()
        if match in ("owner", "audience", "unknown") and behavior in ("full", "restricted", "silent"):
            rules.append({"match": match, "behavior": behavior})
    if not rules:
        return [
            {"match": "owner", "behavior": "full"},
            {"match": "audience", "behavior": "restricted"},
            {"match": "unknown", "behavior": "restricted"},
        ]
    return rules


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


# ── Layer 1: Scope check ─────────────────────────────────────────────────────

async def run_scope_check(
    *,
    scope_description: str,
    message: str,
    agent_id: str,
    workspace_id: str,
    provider: str,
    credentials: Dict[str, Any],
    model: str = "",
) -> Dict[str, Any]:
    """Run a cheap scope-classification LLM call using the agent's OWN provider.

    Prompt: scope_description + message → strict JSON {in_scope: yes|no|uncertain}.

    Returns {verdict, raw_response, error}.
    On any error → verdict="uncertain" (fail-open into full loop).
    """
    if not scope_description or not str(scope_description).strip():
        return {"verdict": "uncertain", "reason": "no_scope_description"}

    if not str(message or "").strip():
        return {"verdict": "uncertain", "reason": "empty_message"}

    prompt = (
        "You are a message classifier. Your ONLY job is to decide whether an "
        "inbound message falls within a defined scope.\n\n"
        f"AGENT SCOPE: {scope_description}\n\n"
        f"INBOUND MESSAGE: {message}\n\n"
        "Respond with EXACTLY this JSON and nothing else:\n"
        '{"in_scope": "yes"|"no"|"uncertain"}\n\n'
        "RULES:\n"
        '- "yes" — the message clearly fits the scope\n'
        '- "no" — the message is clearly out of scope\n'
        '- "uncertain" — you are not sure (err toward uncertain)\n'
        "Do NOT answer the message. Do NOT add commentary. ONLY the JSON."
    )

    try:
        from server_modules.direct_chat_provider_service import (
            generate_direct_chat_completion,
        )

        # Use the agent's own provider for scope check
        resolved_model = str(model or "").strip() or "deepseek-chat"  # cheap default
        result = await generate_direct_chat_completion(
            provider=provider,
            model=resolved_model,
            credentials=credentials,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=32,
            temperature=0.0,
        )

        raw = str(result.get("content") or result.get("text") or "").strip()
        # Parse the JSON
        verdict = "uncertain"
        try:
            # Extract JSON from possible markdown wrapping
            json_match = re.search(r'\{[^}]+\}', raw)
            if json_match:
                parsed = json.loads(json_match.group(0))
                v = str(parsed.get("in_scope") or "").strip().lower()
                if v in ("yes", "no", "uncertain"):
                    verdict = v
        except (json.JSONDecodeError, ValueError):
            pass

        return {"verdict": verdict, "raw_response": raw[:200]}

    except Exception as exc:
        # Provider error → fail-open
        return {"verdict": "uncertain", "reason": f"provider_error: {str(exc)[:100]}"}


# ── Ledger ───────────────────────────────────────────────────────────────────


async def _ledger_triage(
    *,
    workspace_id: str,
    agent_id: str,
    layer: str,
    verdict: str,
    detail: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort triage ledger event."""
    try:
        from server_modules import activity_ledger_service

        await activity_ledger_service.append_activity_event(
            tenant_id="system",
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(agent_id or "").strip() or "unknown",
            event_class="triage",
            detail_level="audit_reference",
            action=layer,
            title=f"Triage {layer}: {verdict}",
            summary=(
                f"Agent {agent_id} triage layer '{layer}' returned "
                f"verdict '{verdict}'."
                + (f" Detail: {detail}" if detail else "")
            ),
            status="logged",
            metadata=dict(metadata or {}),
        )
    except Exception:
        pass


# ── Out-of-scope behavior dispatcher ─────────────────────────────────────────


async def dispatch_out_of_scope(
    *,
    behavior: str,
    agent_label: str,
    sender_summary: str,
    workspace_id: str,
    agent_id: str,
) -> Dict[str, Any]:
    """Execute the out-of-scope behavior.

    Returns {action: "silent"|"polite_decline"|"escalate", reply: str|None}.
    """
    if behavior == "silent":
        await _ledger_triage(
            workspace_id=workspace_id,
            agent_id=agent_id,
            layer="out_of_scope",
            verdict="silent",
            detail="Out-of-scope message — silent (no reply).",
        )
        return {"action": "silent", "reply": None}

    if behavior == "escalate_to_owner":
        # Enqueue a notification to the owner's Sage
        await _enqueue_owner_notification(
            workspace_id=workspace_id,
            agent_id=agent_id,
            agent_label=agent_label,
            sender_summary=sender_summary,
        )
        await _ledger_triage(
            workspace_id=workspace_id,
            agent_id=agent_id,
            layer="out_of_scope",
            verdict="escalate_to_owner",
            detail="Out-of-scope message — escalated to owner.",
        )
        return {"action": "escalate_to_owner", "reply": None}

    # polite_decline (default)
    # polite_decline (default) — platform voice, never agent first-person
    reply = (
        f"Heads up: this request is outside {agent_label}'s configured scope. "
        f"The workspace owner can adjust the scope settings if this is a mistake."
    )
    await _ledger_triage(
        workspace_id=workspace_id,
        agent_id=agent_id,
        layer="out_of_scope",
        verdict="polite_decline",
        detail="Out-of-scope message — polite decline sent.",
    )
    return {"action": "polite_decline", "reply": reply}


async def _enqueue_owner_notification(
    *,
    workspace_id: str,
    agent_id: str,
    agent_label: str,
    sender_summary: str,
) -> None:
    """Enqueue a notification turn to the workspace owner's Sage.

    Best-effort; failures are logged but never block the triage flow.
    """
    try:
        from server_modules import agent_registry_repository as _repo

        sage_install = await _repo.get_workspace_master_agent_install(
            tenant_id="system",
            workspace_id=workspace_id,
        )
        if not sage_install:
            return

        sage_id = str(sage_install.get("id") or "").strip()
        if not sage_id:
            return

        # Write notification into Sage's fleet inbox
        meta = dict(
            sage_install.get("install_metadata")
            or sage_install.get("metadata")
            or {}
        )
        inbox: List[Dict[str, Any]] = list(meta.get("fleet_inbox") or [])
        inbox.append({
            "from_agent_id": agent_id,
            "notification_type": "out_of_scope_escalation",
            "summary": sender_summary[:120],
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "message_id": f"triage_esc_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        })
        meta["fleet_inbox"] = inbox[-20:]

        await _repo.update_workspace_agent_install(
            sage_id,
            tenant_id="system",
            workspace_id=workspace_id,
            metadata=meta,
        )
    except Exception:
        pass


# ── Main triage gate ─────────────────────────────────────────────────────────


async def execute_triage_gate(
    *,
    workspace_id: str,
    agent_install_id: str,
    message: str,
    channel_origin: str = "",
    sender_id: str = "",
    sender_name: str = "",
    agent_label: str = "Agent",
    provider: str = "deepseek",
    credentials: Optional[Dict[str, Any]] = None,
    model: str = "",
    channel_bindings: Optional[List[Dict[str, Any]]] = None,
    audience_sender_ids: Optional[List[str]] = None,
    audience_enabled: bool = False,
) -> Dict[str, Any]:
    """Run the full triage gate before the main LLM loop.

    Returns:
      {blocked: bool, reply: str|None, layer1_verdict: str, layer2_identity: str,
       layer2_behavior: str, triage_applied: bool}

    If blocked=True, the caller should return the reply immediately (no LLM).
    If blocked=False, the caller should proceed to the main LLM loop.
    triage_applied indicates whether triage was actually run (vs skipped).
    """
    # Resolve agent install to check triage config
    try:
        from server_modules import agent_registry_repository as _repo
        from server_modules.fleet_tools import resolve_agent_role, resolve_model_config

        install = await _repo.get_workspace_agent_install_bundle(
            agent_install_id,
            tenant_id="system",
            workspace_id=workspace_id,
        )
    except Exception:
        return {"blocked": False, "triage_applied": False}

    triage_config = resolve_triage_config(install)
    if not triage_config["enabled"]:
        return {"blocked": False, "triage_applied": False}

    creds = dict(credentials or {})

    # ── Layer 1: Scope check ──────────────────────────────────────────
    scope_result = await run_scope_check(
        scope_description=triage_config["scope_description"],
        message=message,
        agent_id=agent_install_id,
        workspace_id=workspace_id,
        provider=provider,
        credentials=creds,
        model=model,
    )
    verdict_l1 = scope_result["verdict"]

    await _ledger_triage(
        workspace_id=workspace_id,
        agent_id=agent_install_id,
        layer="scope_check",
        verdict=verdict_l1,
        detail=str(scope_result.get("reason") or scope_result.get("raw_response") or ""),
        metadata={
            "scope_description": triage_config["scope_description"][:120],
            "message_length": len(str(message)),
        },
    )

    # uncertain → fail-open into full loop
    if verdict_l1 == "uncertain":
        return {
            "blocked": False,
            "triage_applied": True,
            "layer1_verdict": "uncertain",
        }

    # in_scope → proceed to full loop (Layer 2 still runs for context)
    if verdict_l1 == "yes":
        l2 = await _run_layer2_identity(
            triage_config=triage_config,
            sender_id=sender_id,
            channel_origin=channel_origin,
            channel_bindings=channel_bindings,
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
            audience_sender_ids=audience_sender_ids,
            audience_enabled=audience_enabled,
        )
        return {
            "blocked": False,
            "triage_applied": True,
            "layer1_verdict": "yes",
            **l2,
        }

    # verdict_l1 == "no" — out of scope
    # Run Layer 2 identity for ledger context
    l2 = await _run_layer2_identity(
        triage_config=triage_config,
        sender_id=sender_id,
        channel_origin=channel_origin,
        channel_bindings=channel_bindings,
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
    )

    # If identity rule says "silent" for this sender, override with silent
    if l2.get("layer2_behavior") == "silent":
        behavior = "silent"
    else:
        behavior = triage_config["out_of_scope_behavior"]

    dispatch = await dispatch_out_of_scope(
        behavior=behavior,
        agent_label=agent_label,
        sender_summary=_redact_sender(sender_id, sender_name, channel_origin),
        workspace_id=workspace_id,
        agent_id=agent_install_id,
    )

    return {
        "blocked": True,
        "triage_applied": True,
        "layer1_verdict": "no",
        "reply": dispatch.get("reply"),
        "out_of_scope_action": dispatch["action"],
        **l2,
    }


async def _run_layer2_identity(
    *,
    triage_config: Dict[str, Any],
    sender_id: str,
    channel_origin: str,
    channel_bindings: Optional[List[Dict[str, Any]]],
    workspace_id: str,
    agent_install_id: str,
    audience_sender_ids: Optional[List[str]] = None,
    audience_enabled: bool = False,
) -> Dict[str, Any]:
    """Run Layer 2 identity check. Returns identity + behavior."""
    identity = resolve_sender_identity(
        sender_id=sender_id,
        channel_origin=channel_origin,
        channel_bindings=channel_bindings,
        audience_sender_ids=audience_sender_ids,
        audience_enabled=audience_enabled,
    )

    # Find matching identity rule
    rules = triage_config.get("identity_rules", [])
    behavior = "full"  # default
    for rule in rules:
        if rule.get("match") == identity:
            behavior = rule.get("behavior", "full")
            break

    await _ledger_triage(
        workspace_id=workspace_id,
        agent_id=agent_install_id,
        layer="identity_check",
        verdict=f"{identity}:{behavior}",
        metadata={
            "sender_identity": identity,
            "behavior": behavior,
        },
    )

    return {
        "layer2_identity": identity,
        "layer2_behavior": behavior,
    }


def _redact_sender(sender_id: str, sender_name: str, channel_origin: str) -> str:
    """Redacted sender summary for escalation notifications."""
    if sender_id:
        h = hashlib.sha256(str(sender_id).encode()).hexdigest()[:12]
        name = str(sender_name or "").strip() or "unknown"
        channel = str(channel_origin or "").strip() or "unknown"
        return f"{name} on {channel} (hash={h})"
    return "unknown sender"
