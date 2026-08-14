from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import HTTPException
from fastapi import Request

from server_modules import (
    auth,
    execution_mode_policy,
    gateway_state_repository,
    session_service,
)


DEFAULT_GATEWAY_SESSION_TTL_SECONDS = 15 * 60
# 10s (was 20s). The gateway reads this from the backend on connect and drives
# its own heartbeat loop + dead-socket detection from it (2 missed beats ->
# terminate + reconnect). At 20s a half-open socket took ~60-80s to be noticed
# — longer than a dispatch deadline, so a turn arriving just as the socket
# silently died had no reconnect (and thus no durable-inbound flush) in time.
# At 10s that detection window roughly halves to ~40s, inside the deadline, and
# it also doubles how often the per-heartbeat pending-invoke flush runs. Still
# well under Cloudflare's ~100s idle cutoff.
DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS = 10
DEFAULT_GATEWAY_FRESH_HEARTBEAT_SECONDS = max(45, DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS * 2)


def _parse_utc_ts(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _heartbeat_age_seconds(value: Any) -> Optional[int]:
    parsed = _parse_utc_ts(value)
    if parsed is None:
        return None
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return max(int(age), 0)


def _gateway_connection_payload(registration: Dict[str, Any]) -> Dict[str, Any]:
    latest_session = gateway_state_repository.get_latest_gateway_session(
        str(registration.get("gateway_id") or "").strip(),
        include_revoked=True,
    )
    last_heartbeat_at = (
        (latest_session or {}).get("last_heartbeat_at")
        or registration.get("last_heartbeat_at")
    )
    heartbeat_age_seconds = _heartbeat_age_seconds(last_heartbeat_at)
    heartbeat_fresh = (
        heartbeat_age_seconds is not None
        and heartbeat_age_seconds <= DEFAULT_GATEWAY_FRESH_HEARTBEAT_SECONDS
    )
    registration_status = str(registration.get("status") or "").strip().lower()
    device_trust_state = str(registration.get("device_trust_state") or "").strip().lower()
    session_status = str((latest_session or {}).get("status") or "").strip().lower()
    latest_session_metadata = dict((latest_session or {}).get("metadata") or {})
    registration_metadata = dict(registration.get("metadata") or {})
    reported_health_state = str(
        latest_session_metadata.get("health_state")
        or registration_metadata.get("health_state")
        or ""
    ).strip().lower()
    if registration_status == "revoked" or device_trust_state == "revoked":
        connection_status = "revoked"
    elif session_status == "pending":
        connection_status = "reconnecting"
    elif reported_health_state == "reconnecting":
        recently_active = (
            heartbeat_age_seconds is None
            or heartbeat_age_seconds <= DEFAULT_GATEWAY_FRESH_HEARTBEAT_SECONDS * 4
        )
        connection_status = (
            "reconnecting"
            if session_status in {"connected", "disconnected", "pending"} and recently_active
            else "offline"
        )
    elif reported_health_state == "degraded":
        connection_status = "degraded"
    elif session_status == "connected" and heartbeat_fresh:
        connection_status = "online"
    elif session_status in {"connected", "pending"}:
        connection_status = "degraded"
    else:
        connection_status = "offline"
    # Live capability_readiness: gateway.heartbeat frames report this on
    # every heartbeat tick (empyralis-gateway/src/cloud/heartbeat-payload.ts),
    # but gateway_protocol_service.py's heartbeat handler only ever persists
    # it onto the CURRENT gateway_sessions row (touch_gateway_session) — the
    # gateway_registrations row's own metadata.capability_readiness is only
    # refreshed by the much rarer gateway.state.update frame. Surface the
    # live session's copy here (already fetched above for reported_health_
    # state, same "session metadata wins" precedence) so callers building
    # the public payload can prefer it over the registration's stale copy.
    live_capability_readiness = latest_session_metadata.get("capability_readiness")
    if not isinstance(live_capability_readiness, dict) or not live_capability_readiness:
        live_capability_readiness = None
    # Connectivity and execution readiness are two different facts, and
    # collapsing them into one "online" light is exactly the class of bug
    # this codebase keeps finding (see CLAUDE.md: a configured-but-
    # disconnected outbound socket once got reported with an inbound-blocking
    # status). A box can have a perfectly live WSS session and a fresh
    # heartbeat while its Docker sandbox — the only thing that makes
    # shell.execute/filesystem.read_write actually work — is not ready, e.g.
    # a box still on a pre-fix installer/image with no Docker at all. Only
    # demotes an otherwise-"online" box: degraded/reconnecting/revoked/
    # offline already carry a more urgent, already-honest reason for that
    # label, and folding a second fact in there would obscure which one is
    # true.
    if connection_status == "online" and live_capability_readiness is not None:
        blocked_capabilities = {
            str(item or "").strip().lower()
            for item in (live_capability_readiness.get("blocked") or [])
            if str(item or "").strip()
        }
        # requestedCapabilities() (empyralis-gateway/src/shell/runtime.ts)
        # always reports exactly these two — the founder's stated launch
        # bar for what a box must be able to do to be worth placing an
        # agent on.
        if blocked_capabilities & {"shell.execute", "filesystem.read_write"}:
            connection_status = "execution_blocked"
    # Same staleness gap as capability_readiness above, same fix: the
    # gateway's passive service_inventory (Docker, Ollama, the CLIs, GPU,
    # ...) is reported on every heartbeat tick but gateway_protocol_
    # service.py's heartbeat handler only ever persists it onto the live
    # gateway_sessions row, never onto the registration's own metadata
    # (which only the much rarer gateway.state.update frame refreshes).
    # Before this, _llm_runtime_summary and any per-box Docker/OpenClaw
    # readiness derived from registration.metadata.service_inventory read a
    # copy that — on a real box that has been up for more than one
    # gateway.state.update cycle — is usually empty or stale: a check
    # deriving its expectations from a field nothing in production keeps
    # populated, which reports "unknown" forever rather than the true
    # state. Surfaced here so gateway_registration_public_payload can
    # prefer the live copy the identical way it already does for
    # capability_readiness. Computed AFTER the execution_blocked demotion
    # above so the two additions stay independent — this one never reads
    # or changes connection_status.
    live_service_inventory = latest_session_metadata.get("service_inventory")
    if not isinstance(live_service_inventory, list) or not live_service_inventory:
        live_service_inventory = None
    return {
        "connection_status": connection_status,
        "reported_health_state": reported_health_state or None,
        "heartbeat_fresh": bool(heartbeat_fresh),
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "latest_session_id": str((latest_session or {}).get("session_id") or "").strip() or None,
        "latest_session_status": session_status or None,
        "latest_connected_at": (latest_session or {}).get("connected_at"),
        "latest_disconnected_at": (latest_session or {}).get("disconnected_at"),
        "live_capability_readiness": live_capability_readiness,
        "live_service_inventory": live_service_inventory,
    }


_CLOUD_PROVIDER_LABELS: Dict[str, str] = {
    "digitalocean": "DigitalOcean",
    "hetzner": "Hetzner",
    "vultr": "Vultr",
}

_CLOUD_PROVIDER_REGION_LABELS: Dict[str, Dict[str, str]] = {
    "digitalocean": {
        "nyc3": "New York 3",
        "sfo3": "San Francisco 3",
        "lon1": "London 1",
        "fra1": "Frankfurt 1",
        "sgp1": "Singapore 1",
        "blr1": "Bangalore 1",
    },
    "hetzner": {
        "nbg1": "Nuremberg, Germany",
        "fsn1": "Falkenstein, Germany",
        "hel1": "Helsinki, Finland",
        "ash": "Ashburn, USA",
        "hil": "Hillsboro, USA",
    },
    "vultr": {
        "ewr": "New York / New Jersey",
        "lhr": "London",
        "fra": "Frankfurt",
        "sgp": "Singapore",
        "syd": "Sydney",
    },
}


def _hardware_presentation(metadata: Dict[str, Any]) -> Dict[str, Any]:
    provider = str(metadata.get("provider") or "").strip().lower()
    is_cloud_vps = str(metadata.get("setup_source") or "").strip().lower() == "vps" and bool(provider)
    if not is_cloud_vps:
        return {"hardware_kind": "personal_device", "hardware_label": "This Device"}
    provider_label = _CLOUD_PROVIDER_LABELS.get(provider, provider.title() or "Cloud")
    region = str(metadata.get("region") or "").strip()
    region_label = _CLOUD_PROVIDER_REGION_LABELS.get(provider, {}).get(region, region)
    label_parts = [provider_label] + ([region_label] if region_label else [])
    return {
        "hardware_kind": "cloud_vps",
        "hardware_label": " · ".join(label_parts),
        "hardware_provider": provider,
        "hardware_region": region or None,
    }


def _llm_runtime_summary(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Distill the box's detected AI runtimes (Ollama + Claude/Codex CLIs) from
    the persisted heartbeat inventory, so the agent-creation box-picker can show
    per-box readiness instead of making users pick blind (BYO-brain Phase 1).
    Best effort: absent data yields unknown/false, never an error. Only
    presence + auth STATUS is surfaced — never any credential content."""
    inventory = metadata.get("service_inventory")
    items = inventory if isinstance(inventory, list) else []
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            by_id[str(item["id"]).strip()] = item

    def _runtime(entry_id: str) -> Dict[str, Any]:
        entry = by_id.get(entry_id) or {}
        meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        status = str(entry.get("status") or "unknown").strip().lower()
        return {
            "detected": bool(entry.get("detected")),
            "status": status,
            "installed": bool(meta.get("installed")) if "installed" in meta else (status in {"ready", "degraded"}),
            "authenticated": bool(meta.get("authenticated")),
        }

    readiness = metadata.get("capability_readiness")
    ready_ids: set[str] = set()
    if isinstance(readiness, dict) and isinstance(readiness.get("ready"), list):
        ready_ids = {str(x).strip().lower() for x in readiness["ready"]}
    ollama = _runtime("ollama")
    local_model_ready = ("llm.generate" in ready_ids) or (ollama.get("status") == "ready")
    return {
        "ollama": ollama,
        "claude_code": _runtime("claude_cli"),
        "codex": _runtime("codex_cli"),
        # xAI Grok Build / Cursor CLI addition (2026-07-24) — same shape,
        # sourced from service-inventory.ts's probeGrokBuildCli/probeCursorCli
        # ("grok_cli"/"cursor_cli" service_inventory ids).
        "grok_build": _runtime("grok_cli"),
        "cursor_cli": _runtime("cursor_cli"),
        "local_model_ready": bool(local_model_ready),
    }


def _hardware_execution_readiness_summary(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Docker + OpenClaw channel-transport readiness, distilled from the same
    live service_inventory _llm_runtime_summary already reads above — the
    fleet-wide answer to "which boxes lack Docker or OpenClaw" in ONE call
    (GET /gateway/registrations, which calls gateway_registration_public_
    payload per box via list_workspace_gateways) rather than an N+1 loop
    over each box's own /doctor endpoint.

    Item ids ("docker", "openclaw") are empyralis-gateway/src/health/
    service-inventory.ts's own, matching what gateway_health_service.
    gateway_doctor_payload's passive_service_inventory check already
    reports per-box — this is the same signal, just distilled to the two
    facts that decide whether shell.execute/filesystem.read_write (docker)
    and messaging channels (openclaw) can work at all, and surfaced at the
    list level instead of requiring a per-box detail fetch.

    A box that has never reported service_inventory (an older gateway
    build predating this field, or one that hasn't heartbeated yet) reads
    status "unknown" for both — never guessed as installed or missing,
    same discipline as _runtime() above.
    """
    inventory = metadata.get("service_inventory")
    items = inventory if isinstance(inventory, list) else []
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            by_id[str(item["id"]).strip()] = item

    def _readiness(entry_id: str) -> Dict[str, Any]:
        entry = by_id.get(entry_id) or {}
        status = str(entry.get("status") or "unknown").strip().lower()
        return {
            "detected": bool(entry.get("detected")),
            "status": status,
            "ready": status == "ready",
        }

    return {
        "docker": _readiness("docker"),
        "openclaw": _readiness("openclaw"),
    }


def gateway_registration_public_payload(registration: Dict[str, Any]) -> Dict[str, Any]:
    # Lazy import to break a module-load cycle: gateway_self_update_service pulls
    # in gateway_execution_service -> gateway_protocol_service, and protocol reads
    # a constant from THIS module at import time. Importing it here (call time)
    # instead of at module top keeps the update-status merge without the cycle.
    from server_modules import gateway_self_update_service

    metadata = dict(registration.get("metadata") or {})
    # MAN-313: registration.metadata.capability_readiness is only ever as
    # fresh as the last gateway.state.update frame (rare — restart-health-
    # check, supervisor-install, personal-channel state), NOT the continuous
    # gateway.heartbeat stream (every ~10s), which the protocol handler only
    # ever persists onto the live gateway_sessions row. Fold the live
    # session's copy in here — BEFORE any derivation below reads
    # metadata["capability_readiness"] — so a capability that only became
    # available mid-connection (Docker just started) is reflected without a
    # reconnect. connection_payload is computed once and reused for both this
    # merge and the **spread below, so this doesn't add a second gateway_
    # sessions lookup.
    connection_payload = _gateway_connection_payload(registration)
    live_capability_readiness = connection_payload.pop("live_capability_readiness", None)
    if isinstance(live_capability_readiness, dict) and live_capability_readiness:
        metadata["capability_readiness"] = live_capability_readiness
    # Same fix, same reason, for service_inventory (see
    # _gateway_connection_payload's own comment on live_service_inventory) —
    # BEFORE _llm_runtime_summary and _hardware_execution_readiness_summary
    # below read metadata["service_inventory"], so both derive from what the
    # box actually reported on its last heartbeat rather than a copy that is
    # usually empty in production.
    live_service_inventory = connection_payload.pop("live_service_inventory", None)
    if isinstance(live_service_inventory, list) and live_service_inventory:
        metadata["service_inventory"] = live_service_inventory
    runtime_access_mode = execution_mode_policy.normalize_runtime_access_mode(
        metadata.get("runtime_access_mode")
    )
    # Hardware-readiness gap: runtime_access_mode/runtime_access_label above are
    # only ever the SERVER's half of full_access — what this gateway was
    # authorized to do at pairing time. They say nothing about whether the box
    # operator's own local opt-in (EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED,
    # empyralis-gateway/src/config.ts's shellFullAccessLocallyEnabled) is
    # actually set right now — a customer could believe full_access is live
    # when the local half was never enabled, or vice versa. The gateway now
    # reports its own live value on every heartbeat (capability_readiness.
    # shell_full_access_locally_enabled — see gateway_inventory_service.
    # sanitize_capability_readiness's generic bool passthrough, and cloud/
    # heartbeat-payload.ts on the gateway side); surfaced here as a top-level
    # field, same treatment as runtime_access_mode, so callers never have to
    # reach into metadata.capability_readiness by hand. None (not False) means
    # "this gateway hasn't reported it yet" — an older build, or one that
    # hasn't heartbeated since this field was added — never guessed as false.
    capability_readiness = metadata.get("capability_readiness")
    capability_readiness = capability_readiness if isinstance(capability_readiness, dict) else {}
    raw_shell_full_access_locally_enabled = capability_readiness.get("shell_full_access_locally_enabled")
    shell_full_access_locally_enabled = (
        raw_shell_full_access_locally_enabled
        if isinstance(raw_shell_full_access_locally_enabled, bool)
        else None
    )
    return {
        "gateway_id": str(registration.get("gateway_id") or ""),
        "device_id": str(registration.get("device_id") or ""),
        "tenant_id": str(registration.get("tenant_id") or ""),
        "workspace_id": str(registration.get("workspace_id") or ""),
        "user_id": str(registration.get("user_id") or ""),
        "status": str(registration.get("status") or ""),
        "device_trust_state": str(registration.get("device_trust_state") or ""),
        "display_name": registration.get("display_name"),
        "platform": registration.get("platform"),
        "metadata": metadata,
        "agent_computer_policy_id": metadata.get("agent_computer_policy_id"),
        "agent_computer_emergency_stop": metadata.get("agent_computer_emergency_stop"),
        "runtime_access_mode": runtime_access_mode,
        "runtime_access_label": execution_mode_policy.public_runtime_access_label(runtime_access_mode),
        "runtime_access_setup_warning": execution_mode_policy.runtime_access_setup_warning(runtime_access_mode),
        "shell_full_access_locally_enabled": shell_full_access_locally_enabled,
        "autonomous_agent_setup_warning_acknowledged": bool(
            metadata.get("autonomous_agent_setup_warning_acknowledged")
        ),
        # CLAUDE.md's "Hardware attaches to its owner, never to the project"
        # per-machine opt-in — see gateway_state_repository.
        # gateway_project_sharing_opted_in (the actual enforcement gate) and
        # set_gateway_project_sharing_opt_in (the only writer, owner-only).
        # `is True` (not truthy) so a pre-existing registration with no key
        # at all reads as an honest False, never an accidental True from
        # some other truthy leftover value.
        "project_sharing_opt_in": metadata.get("project_sharing_opt_in") is True,
        "capabilities": list(registration.get("capabilities") or []),
        "llm_runtimes": _llm_runtime_summary(metadata),
        "service_readiness": _hardware_execution_readiness_summary(metadata),
        "journal_cursor": int(registration.get("journal_cursor") or 0),
        "checkpoint_cursor": int(registration.get("checkpoint_cursor") or 0),
        "created_at": registration.get("created_at"),
        "updated_at": registration.get("updated_at"),
        "last_seen_at": registration.get("last_seen_at"),
        "last_heartbeat_at": registration.get("last_heartbeat_at"),
        "token_rotated_at": registration.get("token_rotated_at"),
        "revoked_at": registration.get("revoked_at"),
        "revoked_reason": registration.get("revoked_reason"),
        **connection_payload,
        **_hardware_presentation(metadata),
        **gateway_self_update_service.gateway_update_status(registration),
    }


def gateway_scope_payload(registration: Dict[str, Any]) -> Dict[str, str]:
    return {
        "tenant_id": str(registration.get("tenant_id") or ""),
        "workspace_id": str(registration.get("workspace_id") or ""),
        "user_id": str(registration.get("user_id") or ""),
        "device_id": str(registration.get("device_id") or ""),
        "gateway_id": str(registration.get("gateway_id") or ""),
    }


def build_gateway_ws_url(
    request: Request,
    *,
    gateway_id: str,
    session_token: str,
) -> str:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    request_scheme = str(request.url.scheme or "").strip().lower()
    scheme = "wss" if (forwarded_proto or request_scheme) == "https" else "ws"
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    netloc = forwarded_host or request.headers.get("host") or request.url.netloc
    return (
        f"{scheme}://{netloc}/api/gateway/ws"
        f"?gateway_id={quote(str(gateway_id or '').strip())}"
    )


async def create_gateway_session(
    request: Request,
    *,
    gateway_id: str,
    gateway_token: str,
    session_ttl_seconds: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    try:
        device_link = auth.validate_local_gateway_device_link(
            user_id=str(registration.get("user_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            device_id=str(registration.get("device_id") or "").strip(),
            gateway_id=str(registration.get("gateway_id") or "").strip(),
        )
    except HTTPException as exc:
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=str(gateway_id or "").strip(),
            device_trust_state="revoked"
            if "revoked" in str(exc.detail or "").strip().lower()
            else str(registration.get("device_trust_state") or "verified").strip() or "verified",
            status="revoked"
            if "revoked" in str(exc.detail or "").strip().lower()
            else str(registration.get("status") or "active").strip() or "active",
            metadata={"last_identity_error": str(exc.detail)},
        )
        raise ValueError(str(exc.detail)) from exc
    session = gateway_state_repository.issue_gateway_session(
        gateway_id=gateway_id,
        gateway_token=gateway_token,
        ttl_seconds=int(session_ttl_seconds or DEFAULT_GATEWAY_SESSION_TTL_SECONDS),
        metadata=metadata,
    )
    session_metadata = {
        **dict(metadata or {}),
        "tenant_id": str(registration.get("tenant_id") or "").strip(),
        "workspace_id": str(registration.get("workspace_id") or "").strip(),
        "user_id": str(registration.get("user_id") or "").strip(),
        "device_id": str(registration.get("device_id") or "").strip(),
        "gateway_id": str(registration.get("gateway_id") or "").strip(),
        "device_trust_state": str(device_link.get("trust_state") or "verified").strip() or "verified",
    }
    auth.create_auth_session(
        str(registration.get("user_id") or "").strip(),
        channel="local_runtime_companion",
        device_id=str(registration.get("device_id") or "").strip() or None,
        runtime_id=str(registration.get("gateway_id") or "").strip() or None,
        trust_state=str(device_link.get("trust_state") or "verified").strip() or "verified",
        session_id=str(session.get("session_id") or "").strip() or None,
        session_family_id=str(registration.get("gateway_id") or "").strip() or None,
        metadata=session_metadata,
        ttl_seconds=int(session_ttl_seconds or DEFAULT_GATEWAY_SESSION_TTL_SECONDS),
    )
    await session_service.create_local_gateway_session(
        tenant_id=str(registration.get("tenant_id") or "").strip(),
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        user_id=str(registration.get("user_id") or "").strip(),
        device_id=str(registration.get("device_id") or "").strip(),
        gateway_id=str(registration.get("gateway_id") or "").strip(),
        display_name=str(registration.get("display_name") or "").strip() or None,
        metadata=session_metadata,
        session_id=str(session.get("session_id") or "").strip() or None,
        ttl_seconds=int(session_ttl_seconds or DEFAULT_GATEWAY_SESSION_TTL_SECONDS),
    )
    registration = gateway_state_repository.sync_gateway_registration_identity(
        gateway_id=str(gateway_id or "").strip(),
        tenant_id=str(registration.get("tenant_id") or "").strip() or None,
        workspace_id=str(registration.get("workspace_id") or "").strip() or None,
        user_id=str(registration.get("user_id") or "").strip() or None,
        device_id=str(registration.get("device_id") or "").strip() or None,
        device_trust_state=str(device_link.get("trust_state") or "verified").strip() or "verified",
        metadata={
            "auth_session_id": str(session.get("session_id") or "").strip() or None,
            "runtime_session_id": str(session.get("session_id") or "").strip() or None,
        },
    ) or registration
    return {
        "session_id": str(session.get("session_id") or ""),
        "gateway_id": str(gateway_id or ""),
        "session_token": str(session.get("session_token") or ""),
        "ws_url": build_gateway_ws_url(
            request,
            gateway_id=str(gateway_id or ""),
            session_token=str(session.get("session_token") or ""),
        ),
        "heartbeat_interval_seconds": DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS,
        "scope": gateway_scope_payload(registration),
        "gateway": gateway_registration_public_payload(registration),
        "created_at": session.get("created_at"),
        "expires_at": session.get("expires_at"),
    }


def list_workspace_gateways(*, workspace_id: str) -> Dict[str, Any]:
    resolved_workspace_id = str(workspace_id or "").strip() or "default"
    gateway_state_repository.dedupe_and_expire_workspace_gateway_registrations(resolved_workspace_id)
    items = [
        gateway_registration_public_payload(item)
        for item in gateway_state_repository.list_workspace_gateway_registrations(
            resolved_workspace_id, include_revoked=False
        )
    ]
    return {
        "workspace_id": resolved_workspace_id,
        "count": len(items),
        "items": items,
    }


def rotate_gateway_registration_token(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    try:
        device_link = auth.validate_local_gateway_device_link(
            user_id=str(registration.get("user_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            device_id=str(registration.get("device_id") or "").strip(),
            gateway_id=str(registration.get("gateway_id") or "").strip(),
        )
    except HTTPException as exc:
        gateway_state_repository.update_gateway_registration_state(
            gateway_id=str(gateway_id or "").strip(),
            device_trust_state="revoked"
            if "revoked" in str(exc.detail or "").strip().lower()
            else str(registration.get("device_trust_state") or "verified").strip() or "verified",
            status="revoked"
            if "revoked" in str(exc.detail or "").strip().lower()
            else str(registration.get("status") or "active").strip() or "active",
            metadata={"last_identity_error": str(exc.detail)},
        )
        raise ValueError(str(exc.detail)) from exc
    rotated = gateway_state_repository.rotate_gateway_token(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    fresh_gateway_token = str(rotated.get("gateway_token") or "")
    rotated = gateway_state_repository.sync_gateway_registration_identity(
        gateway_id=str(rotated.get("gateway_id") or "").strip(),
        tenant_id=str(rotated.get("tenant_id") or "").strip() or None,
        workspace_id=str(rotated.get("workspace_id") or "").strip() or None,
        user_id=str(rotated.get("user_id") or "").strip() or None,
        device_id=str(rotated.get("device_id") or "").strip() or None,
        device_trust_state=str(device_link.get("trust_state") or "verified").strip() or "verified",
        metadata={
            "device_link": {
                "device_id": str(device_link.get("device_id") or "").strip() or None,
                "workspace_id": str(device_link.get("workspace_id") or "").strip() or None,
                "status": str(device_link.get("status") or "").strip() or None,
                "trust_state": str(device_link.get("trust_state") or "").strip() or None,
            }
        },
    ) or rotated
    rotated["gateway_token"] = fresh_gateway_token
    return {
        "gateway": gateway_registration_public_payload(rotated),
        "gateway_token": str(rotated.get("gateway_token") or ""),
        "scope": gateway_scope_payload(rotated),
    }


def revoke_gateway_registration(
    *,
    gateway_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
    reason: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    if str(registration.get("tenant_id") or "").strip() != str(tenant_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    if str(registration.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    if user_id and str(registration.get("user_id") or "").strip() != str(user_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    resolved_reason = reason or "Gateway registration revoked."
    revoked = gateway_state_repository.revoke_gateway_registration(
        gateway_id=gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        reason=reason,
    )
    if not revoked:
        raise ValueError("Gateway registration was not found.")
    auth.revoke_local_gateway_device_link(
        user_id=str(revoked.get("user_id") or "").strip(),
        device_id=str(revoked.get("device_id") or "").strip(),
        reason=resolved_reason,
    )
    return {
        "gateway": gateway_registration_public_payload(revoked),
        "scope": gateway_scope_payload(revoked),
        "mutation_plan": {
            "shutdown_live_connection": True,
            "mark_dedicated_workstation_revoked": True,
            "revocation_reason": resolved_reason,
        },
    }


def rename_gateway_registration(
    *,
    gateway_id: str,
    display_name: str,
    tenant_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Owner-chosen nickname for a paired box — the Hardware page's rename
    affordance. Real gateway boxes and VPS-provisioned ones share the same
    registration row, so this isn't VPS-specific; gatewayLabel() on the
    frontend already prefers display_name over the derived hardware_label
    ("Provider · Region"), so setting this is the whole fix."""
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    if str(registration.get("tenant_id") or "").strip() != str(tenant_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    if str(registration.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    if user_id and str(registration.get("user_id") or "").strip() != str(user_id or "").strip():
        raise ValueError("Gateway registration scope mismatch.")
    clean_name = str(display_name or "").strip()
    if not clean_name:
        raise ValueError("display_name must not be empty.")
    renamed = gateway_state_repository.rename_gateway_registration(
        gateway_id=gateway_id,
        display_name=clean_name,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not renamed:
        raise ValueError("Gateway registration was not found.")
    return gateway_registration_public_payload(renamed)


def set_gateway_project_sharing_opt_in(
    *,
    gateway_id: str,
    opted_in: bool,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """The Hardware page's per-machine sharing toggle (CLAUDE.md: "Sharing a
    machine with a project is an explicit per-machine opt-in by the
    hardware's owner, default off"). Unlike rename/rotate/revoke above --
    which a workspace `owner`-ROLE admin may do on any box in the workspace
    -- this specifically requires the caller to be the box's actual paired
    user_id, checked by gateway_state_repository.set_gateway_project_sharing_
    opt_in's scope match. A workspace admin who is not this machine's owner
    gets the same "not found" ValueError a stranger would -- deliberately
    indistinguishable from 404, so this never leaks whether a gateway_id is
    real to someone who has no claim on it."""
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        raise ValueError("A signed-in user is required to change sharing settings.")
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")
    updated = gateway_state_repository.set_gateway_project_sharing_opt_in(
        gateway_id=gateway_id,
        opted_in=opted_in,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=clean_user_id,
    )
    if not updated:
        raise ValueError("Gateway registration was not found, or you are not this machine's owner.")
    return gateway_registration_public_payload(updated)
