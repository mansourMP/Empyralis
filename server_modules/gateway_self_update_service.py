"""Gateway self-update: trigger + version-check for the `gateway.self_update`
capability (empyralis-gateway/src/update/gateway-self-update-runtime.ts).

Dispatch shape mirrors cli_setup_service.install_cli_runtime() (routes_gateway.
py:2826-2846) and personal_channels_service.install_imessage_imsg_gateway()
(server_modules/personal_channels_service.py:2961-3016) — a member-gated,
mutating gateway action routed through gateway_execution_service.
execute_tool_via_gateway() over the gateway's existing outbound WS/tool-invoke
transport. No SSH, no separate control channel.

HONESTY NOTE, corrected 2026-08-13 (gateway-artifact-staleness incident) — a
publish pipeline DOES now exist: .github/workflows/release-gateway-linux.yml
builds the tarball and pushes it to Cloudflare R2 (bucket
empyralis-agent-computer-releases), served back out at
https://empyralis.ai/releases/agent-computer/<version>/ by a Cloudflare
Worker route (not an nginx location block — Cloudflare fronts this domain
and intercepts /releases/* before it ever reaches the origin; confirmed by
curling the origin directly, which 404s on that path). It went unrun for 15
days across the entire OpenClaw channel-transport build because it was
workflow_dispatch-only — fixed by adding a `push` trigger on
empyralis-gateway/** so a merge publishes itself; see that workflow's own
comment for the incident.

What's still true, and still means `resolve_latest_gateway_version()` below
stays operator-configured rather than auto-discovered: nothing writes
EMPYRALIS_GATEWAY_LATEST_VERSION anywhere (confirmed absent from production's
env and systemd units, 2026-08-13), so the `gateway.self_update` capability
this module drives is dormant in production today — every
gateway_update_status() call reads update_available=False, unconditionally.
The one-time boot-install path (scripts/install-agent-computer.sh) is the
live consumer of the R2-published artifact, not this module. Wiring
EMPYRALIS_GATEWAY_LATEST_VERSION to something real (e.g. read off the R2
"latest" pointer this workflow now keeps current) is future work, not done
here — setting it to nothing (the default) makes "update available" read
false everywhere rather than pointing at a URL that would 404.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional
from uuid import uuid4

from server_modules import gateway_execution_service

# Must match GATEWAY_SELF_UPDATE_CAPABILITY in
# empyralis-gateway/src/update/gateway-self-update-runtime.ts.
SELF_UPDATE_CAPABILITY = "gateway.self_update"

# A cold download + tar extraction of a full gateway build (dist/ +
# node_modules/) over a residential/VPS link can run well past the platform's
# normal ~120s interactive tool-invoke window — same reasoning as
# personal_channels_service.install_imessage_imsg_gateway's 280s override for
# a Homebrew install (server_modules/personal_channels_service.py:3009).
DEFAULT_SELF_UPDATE_TIMEOUT_SECONDS = 600


class GatewaySelfUpdateError(RuntimeError):
    """Raised by trigger_gateway_self_update(); the route translates
    status_code + this message directly into an HTTPException, same pattern
    as cli_setup_service.CliSetupError (server_modules/cli_setup_service.py:93)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _status_code_for_reason(reason: str) -> int:
    r = str(reason or "").strip().lower()
    if "offline" in r or "heartbeat_stale" in r or "unhealthy" in r or "not currently connected" in r:
        return 409
    return 400


def resolve_latest_gateway_version(*, platform: str = "linux", arch: str = "x64") -> Dict[str, Optional[str]]:
    """Operator-configured "latest published gateway build" — see module
    docstring for why this isn't auto-discovered from a manifest today.

    EMPYRALIS_GATEWAY_LATEST_VERSION: version string an operator sets by hand
    after publishing a new gateway tarball to the /releases host (or, once a
    real publish pipeline exists, whatever sets this env var on deploy).
    Absent -> no known "latest": update_available reads false everywhere
    instead of guessing.

    EMPYRALIS_GATEWAY_ARTIFACT_BASE_URL: same convention as install-agent-
    computer.sh's ARTIFACT_BASE_URL (scripts/install-agent-computer.sh:26),
    defaulting to the same host/path shape that script already assumes.
    """
    version = str(os.environ.get("EMPYRALIS_GATEWAY_LATEST_VERSION") or "").strip() or None
    if not version:
        return {"latest_version": None, "artifact_url": None}
    base_url = str(
        os.environ.get("EMPYRALIS_GATEWAY_ARTIFACT_BASE_URL")
        or "https://empyralis.ai/releases/agent-computer"
    ).strip().rstrip("/")
    platform_token = str(platform or "linux").strip().lower() or "linux"
    arch_token = str(arch or "x64").strip().lower() or "x64"
    artifact_url = f"{base_url}/{version}/empyralis-gateway-{platform_token}-{arch_token}.tar.gz"
    return {"latest_version": version, "artifact_url": artifact_url}


def _version_parts(value: str) -> Optional[list]:
    cleaned = str(value or "").strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:]
    if not cleaned:
        return None
    parts: list = []
    for segment in cleaned.split("."):
        digits = "".join(ch for ch in segment if ch.isdigit())
        if not digits:
            return None
        parts.append(int(digits))
    return parts or None


def is_newer_gateway_version(current: str, candidate: str) -> bool:
    """True when `candidate` is a strictly newer dotted-numeric version than
    `current`. Mirrors compareGatewayVersions in gateway-version-check.ts —
    keep the two in sync if this logic changes. A malformed string on either
    side is treated as "cannot tell" (returns False), which is the safe
    default: it reads as no update available rather than a false positive
    that dispatches a self-update against garbage input."""
    current_parts = _version_parts(current)
    candidate_parts = _version_parts(candidate)
    if current_parts is None or candidate_parts is None:
        return False
    length = max(len(current_parts), len(candidate_parts))
    current_parts = current_parts + [0] * (length - len(current_parts))
    candidate_parts = candidate_parts + [0] * (length - len(candidate_parts))
    return candidate_parts > current_parts


def _split_platform(registration_platform: str) -> tuple:
    # registration["platform"] is "<os>-<arch>" (empyralis-gateway/src/
    # runtime/runtime-metadata.ts:44, e.g. "linux-x64" / "darwin-arm64"),
    # persisted verbatim onto the registration row at pairing time.
    raw = str(registration_platform or "").strip().lower()
    platform, _, arch = raw.partition("-")
    return (platform or "linux", arch or "x64")


def gateway_update_status(registration: Dict[str, Any]) -> Dict[str, Any]:
    """Folded into gateway_registry_service.gateway_registration_public_payload()
    so the Hardware page's existing gateway-list fetch (frontend/lib/
    workspace/fleet/gateway-box-picker.tsx:227-228) carries current/latest/
    update_available for free — no extra round trip.

    gateway_version reads off registration.metadata (persisted at
    gateway_protocol_service.py's connect handler, ~line 2405) rather than
    the ephemeral session row, so it survives a disconnect."""
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    current_version = str(metadata.get("gateway_version") or "").strip() or None
    platform, arch = _split_platform(str(registration.get("platform") or ""))
    latest = resolve_latest_gateway_version(platform=platform, arch=arch)
    latest_version = latest["latest_version"]
    update_available = bool(
        current_version and latest_version and is_newer_gateway_version(current_version, latest_version)
    )
    return {
        "gateway_version": current_version,
        "latest_gateway_version": latest_version,
        "gateway_update_available": update_available,
        "latest_gateway_artifact_url": latest["artifact_url"] if update_available else None,
    }


async def trigger_gateway_self_update(
    *,
    gateway_id: str,
    workspace_id: str,
    registration: Dict[str, Any],
    target_version: str = "",
    artifact_url: str = "",
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatches gateway.self_update over the existing tool-invoke transport.

    No target_version/artifact_url given -> resolves the latest published
    build server-side (resolve_latest_gateway_version) so the gateway process
    never has to know the release-manifest convention itself; it only ever
    executes "download exactly this URL, verify it, swap to it" (see the
    gateway-side runtime's doc comment for why that split is deliberate).
    """
    resolved_target_version = str(target_version or "").strip()
    resolved_artifact_url = str(artifact_url or "").strip()
    if not resolved_target_version or not resolved_artifact_url:
        platform, arch = _split_platform(str(registration.get("platform") or ""))
        latest = resolve_latest_gateway_version(platform=platform, arch=arch)
        resolved_target_version = resolved_target_version or str(latest["latest_version"] or "").strip()
        resolved_artifact_url = resolved_artifact_url or str(latest["artifact_url"] or "").strip()
    if not resolved_target_version or not resolved_artifact_url:
        raise GatewaySelfUpdateError(
            "No published gateway build is configured to update to yet. "
            "Set EMPYRALIS_GATEWAY_LATEST_VERSION on the backend once a build is published, "
            "or pass target_version/artifact_url explicitly.",
            status_code=409,
        )

    run_id = f"gateway-self-update-{uuid4().hex[:12]}"
    trace_id = run_id
    try:
        execution = await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=SELF_UPDATE_CAPABILITY,
            arguments={
                "target_version": resolved_target_version,
                "artifact_url": resolved_artifact_url,
            },
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            agent_scope="sage",
            timeout_seconds=DEFAULT_SELF_UPDATE_TIMEOUT_SECONDS,
        )
    except (ValueError, PermissionError) as exc:
        reason = str(exc)
        raise GatewaySelfUpdateError(
            reason,
            status_code=403 if isinstance(exc, PermissionError) else _status_code_for_reason(reason),
        ) from exc

    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": gateway_id,
        "run_id": run_id,
        "target_version": resolved_target_version,
        "artifact_url": resolved_artifact_url,
        **result,
    }
