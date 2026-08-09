"""Channel setup from the browser: read three states, write one credential.

The cloud half of `openclaw.channel_setup` (see
`empyralis-gateway/src/openclaw/provisioning/openclaw-channel-setup.ts`).

WHY THIS IS SEPARATE FROM `openclaw_provisioning_service`
--------------------------------------------------------
Provisioning writes the POLICY Empyralis owns, and REGENERATES it from our
database on every run — that is the whole point of "OpenClaw's config is a
derived artifact of Empyralis policy". A credential is the one value in that
config the OWNER supplies and Empyralis must never regenerate. Fusing them
gives you one of two bugs: a reprovision that wipes a credential, or a
credential save that drags a full policy render behind it and fails for a
reason that has nothing to do with the credential.

    provision           policy   ── Empyralis is authoritative, regenerated
    channel_setup       credential ── the OWNER is authoritative, never touched
                                      by a reconcile (config patch merges, and
                                      the generator never writes these keys)

THE CREDENTIAL IS NOT STORED HERE, AND THAT IS THE DESIGN
---------------------------------------------------------
It is a pass-through. The value goes browser -> this process -> the gateway
socket -> `openclaw config patch` on the owner's own machine, and this process
keeps nothing.

    NOT written to `vault_credentials`.  NOT written to any table.
    NOT logged.  NOT journaled (the device journals FIELD NAMES only).
    NOT returned by any read, because no read can produce it —
      `openclaw config get --json` redacts every secret-typed value at the
      source, so the plaintext never reaches this process even once it is
      stored.

Three reasons that beats putting it in the vault. **Execution locality**:
identity lives in the workspace, execution happens where the agent is placed,
and the channel credential is a fact about the box that runs the transport —
it has to be in OpenClaw's config to work at all, so a cloud copy is a second
copy of a secret with no reader. **It adds no unscoped read**:
`vault_credentials` has no `tenant_id` and `vault_repository.list_all()` is a
full-table select with the boundary applied in Python afterwards; growing that
table with one credential per channel per gateway makes a known-weak scoping
story worse for no gain. And **a secret you never hold cannot leak** — there is
no "remember not to return this" rule for a future route to forget.

The cost is stated plainly rather than hidden: rebuild the box and the owner
re-enters the credential. That is the honest trade, and it is the same one the
gateway token already makes.

WHAT MAY BE WRITTEN IS CLOSED, AND CHECKED ON BOTH SIDES
--------------------------------------------------------
`channels.<id>.<field>` where `<id>` is an OpenClaw-transported channel and
`<field>` is in that channel's DERIVED credential shape. Validated here so a
bad request fails with a useful message, and validated AGAIN on the device,
because the device is the side where a mistake would write into
`gateway.auth` or `plugins.allow` and walk through the lockdown. Neither check
is a hand-written list: both read the same generated manifest the setup form
is rendered from.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from server_modules import gateway_execution_service, openclaw_channel_registry
from server_modules.openclaw_provisioning_service import OpenClawProvisioningError

OPENCLAW_CHANNEL_SETUP_CAPABILITY = "openclaw.channel_setup"

# A read shells out to `channels list --all --json` plus `config get --json`
# against a 2.5MB schema; a write adds one `config patch` and a re-read. Well
# inside the gateway default, but named so the number is a decision.
DEFAULT_CHANNEL_SETUP_TIMEOUT_SECONDS = 90


def credential_shape_for_channel_key(channel_key: str) -> Dict[str, Any]:
    """The generated setup form for one channel.

    Raises `ValueError` for a key OpenClaw does not carry — the same posture as
    `openclaw_channel_id`, and for the same reason: a bare prefix strip would
    happily produce an id that fails at the far end of the transport, or not at
    all.
    """
    channel = openclaw_channel_registry.channel_for_key(channel_key)
    if channel is None:
        raise ValueError(f"{channel_key!r} is not an OpenClaw-transported channel key.")
    return channel.credential_shape


def _validate_credential_values(channel_key: str, values: Any) -> Dict[str, str]:
    """Narrow an untrusted body to the fields OpenClaw declares for the channel."""
    shape = credential_shape_for_channel_key(channel_key)
    fields = {field["name"]: field for field in shape.get("fields") or []}
    if not fields:
        raise OpenClawProvisioningError(
            f"{channel_key} does not take a pasted credential: this channel has no credential "
            "field to set, so it connects another way (QR, local pairing, or an inbound webhook).",
            status_code=400,
        )
    if not isinstance(values, dict) or not values:
        raise OpenClawProvisioningError("No credential fields were supplied.", status_code=400)

    cleaned: Dict[str, str] = {}
    for name, value in values.items():
        if name not in fields:
            # Named rather than dropped: a silently ignored field is a save that
            # reports success and changes nothing.
            raise OpenClawProvisioningError(
                f"{name!r} is not a credential field for {channel_key} "
                f"({', '.join(sorted(fields))}).",
                status_code=400,
            )
        if not isinstance(value, str) or not value.strip():
            raise OpenClawProvisioningError(
                f"{name!r} must be a non-empty string. Omit a field to leave it unchanged.",
                status_code=400,
            )
        cleaned[name] = value.strip()
    return cleaned


async def _invoke(
    *,
    gateway_id: str,
    workspace_id: str,
    actor_id: Optional[str],
    arguments: Dict[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    try:
        execution = await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=OPENCLAW_CHANNEL_SETUP_CAPABILITY,
            arguments=arguments,
            run_id=run_id,
            trace_id=run_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            agent_scope="sage",
            timeout_seconds=DEFAULT_CHANNEL_SETUP_TIMEOUT_SECONDS,
        )
    except (ValueError, PermissionError) as exc:
        raise OpenClawProvisioningError(
            str(exc), status_code=403 if isinstance(exc, PermissionError) else 409
        ) from exc
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {"gateway_id": gateway_id, "run_id": run_id, **result}


async def read_channel_setup_state(
    *,
    gateway_id: str,
    workspace_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The three states per channel, OBSERVED on the box.

    Never derived from what a previous save reported. A channel is not
    "connected" because a credential was accepted — it is installed because
    OpenClaw's registry says the plugin is there, configured because the
    effective config carries the field, and enabled because the config says so.
    """
    return await _invoke(
        gateway_id=gateway_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        arguments={"action": "read"},
        run_id=f"openclaw-channel-read-{uuid4().hex[:12]}",
    )


async def write_channel_credential(
    *,
    gateway_id: str,
    workspace_id: str,
    channel_key: str,
    values: Any,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Pass an owner-supplied credential straight through to their own box."""
    channel_id = openclaw_channel_registry.openclaw_channel_id(channel_key)
    cleaned = _validate_credential_values(channel_key, values)
    return await _invoke(
        gateway_id=gateway_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        arguments={
            "action": "write_credential",
            "channel_id": channel_id,
            "values": cleaned,
        },
        run_id=f"openclaw-channel-write-{uuid4().hex[:12]}",
    )


def openclaw_channel_setup_catalog() -> List[Dict[str, Any]]:
    """Every channel the transport carries, with its GENERATED setup form.

    Servable without touching a gateway: the form is a property of the pinned
    OpenClaw version, which is exactly why it is a checked-in manifest. The
    observed state comes from `read_channel_setup_state`, and the UI joins the
    two — so a box that is offline still renders the right form and says the
    state is unknown, rather than rendering nothing.
    """
    from server_modules import channel_lane_contract_service

    active_ids = {channel.id for channel in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS}
    catalog: List[Dict[str, Any]] = []
    for channel in openclaw_channel_registry.CHANNELS:
        if channel.id not in active_ids:
            continue
        shape = channel.credential_shape
        catalog.append(
            {
                "channel_key": channel.channel_key,
                "channel_id": channel.id,
                "label": channel.label,
                "connect_method": shape.get("connect_method"),
                "selection_label": shape.get("selection_label"),
                "docs_path": shape.get("docs_path"),
                "fields": list(shape.get("fields") or []),
                "requires_plugin": bool((channel.plugin_install or {}).get("required")),
                "plugin_id": (channel.plugin_install or {}).get("plugin_id"),
            }
        )
    catalog.sort(key=lambda entry: entry["label"].lower())
    return catalog
