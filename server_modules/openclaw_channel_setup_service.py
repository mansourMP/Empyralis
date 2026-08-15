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

from server_modules import gateway_execution_service, gateway_reason_messages, openclaw_channel_registry
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
    # THREE STATES, and two of them used to share this one refusal.
    #
    #   pairing        the manifest's positive answer that there is nothing to
    #                  paste. Refuse — a form here would be a dead control.
    #   plugin_absent  the manifest ADMITS IT CANNOT KNOW: this channel's
    #                  plugin contributes its `channels.<id>` node only once
    #                  installed, and it was not installed on the machine the
    #                  manifest was generated from. Refusing here would refuse
    #                  a credential the owner is looking at a real, live-derived
    #                  form for — the device reads its own schema and narrows
    #                  the write against THAT (see openclaw-channel-setup.ts's
    #                  parseCredentialWrite), which is the only side that can
    #                  answer. The cloud's narrowing is a nicer error message;
    #                  the device's is the boundary.
    if not fields and shape.get("connect_method") != "plugin_absent":
        raise OpenClawProvisioningError(
            f"{channel_key} does not take a pasted credential: this channel has no credential "
            "field to set, so it connects another way (QR, local pairing, or an inbound webhook).",
            status_code=400,
        )
    if not isinstance(values, dict) or not values:
        raise OpenClawProvisioningError("No credential fields were supplied.", status_code=400)

    cleaned: Dict[str, str] = {}
    for name, value in values.items():
        if fields and name not in fields:
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
        # See openclaw_provisioning_service's identical comment: str(exc) is
        # either already-human prose or one of gateway_execution_service's
        # raw readiness tokens raised verbatim — only the second kind gets
        # translated. This is the ONE call site both read_channel_setup_state
        # and write_channel_credential funnel through, so fixing it here
        # fixes the leak on both the channel-grid banner and the credential
        # form's error text at once.
        raw_reason = str(exc)
        raise OpenClawProvisioningError(
            gateway_reason_messages.humanize_if_reason_token(
                raw_reason, capability_id=OPENCLAW_CHANNEL_SETUP_CAPABILITY
            ),
            status_code=403 if isinstance(exc, PermissionError) else 409,
            reason_code=raw_reason if raw_reason in gateway_reason_messages.KNOWN_REASON_TOKENS else None,
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


async def link_channel(
    *,
    gateway_id: str,
    workspace_id: str,
    channel_key: str,
    action: str,
    current_qr_data_url: Optional[str] = None,
    force: bool = False,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Start, or keep waiting on, a channel link running on the owner's box.

    THE CODE IS A PASS-THROUGH AND IS NEVER STORED HERE
    ---------------------------------------------------
    A pairing code is a credential in flight: it decodes to a pairing
    reference plus key material, and whoever scans it links THEIR account.
    So it takes the same posture the channel credential already takes — it
    exists in this process only as a value being relayed, and reaches no
    table, no log line and no audit row. `current_qr_data_url` travels the
    other way for the same reason it exists at all: OpenClaw compares it to
    decide "still the same code" from "it rotated, here is the new one",
    which is what makes showing an expired square impossible rather than
    merely unlikely.

    Two actions, because they are two different questions:
      link_start  ask for a code (and enable the channel, on the box, first)
      link_wait   block until that code is scanned OR rotates
    """
    normalized_action = str(action or "").strip()
    if normalized_action not in {"link_start", "link_wait"}:
        raise OpenClawProvisioningError(
            f"{action!r} is not a link action (link_start, link_wait).",
            status_code=400,
        )
    channel_id = openclaw_channel_registry.openclaw_channel_id(channel_key)
    arguments: Dict[str, Any] = {"action": normalized_action, "channel_id": channel_id}
    if normalized_action == "link_start":
        if force:
            arguments["force"] = True
    elif isinstance(current_qr_data_url, str) and current_qr_data_url.strip():
        arguments["current_qr_data_url"] = current_qr_data_url
    return await _invoke(
        gateway_id=gateway_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        arguments=arguments,
        run_id=f"openclaw-channel-link-{uuid4().hex[:12]}",
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
                # The instructions for OBTAINING what `fields` asks for, in the
                # transport's own words. Served with the form rather than looked
                # up separately: they are two halves of one screen, and a second
                # round trip would let a form render before its own instructions.
                "setup_wizard": channel.setup_wizard,
            }
        )
    catalog.sort(key=lambda entry: entry["label"].lower())
    return catalog


def openclaw_registry_channel_plugin_catalog() -> List[Dict[str, Any]]:
    """Every channel-capable plugin OpenClaw's registry publishes that this
    pinned build does not already carry.

    Servable without touching a gateway for the same reason the catalog above
    is: it is a property of the derived manifest, not of any one box.

    Two things are deliberately different from a resolved channel, and the UI
    has to render both honestly rather than smoothing them over:

      * `channel_key` is null. Not "unknown yet" as a placeholder — a
        third-party plugin registers its channel at runtime, so there is no id
        to key on until the plugin is installed. A card for one of these is an
        OFFER TO INSTALL, never a channel you can paste a credential into.

      * `trust` is carried through verbatim, including their scanner's own
        `scan_status`. A channel plugin runs third-party code beside the
        owner's messages and contributes its own `channels.<id>.tools.*`
        surface that the global `tools.*` lockdown does not reach, so who
        published it is a fact the owner is entitled to before installing —
        never a reason to hide it from them.
    """
    return [
        plugin.as_payload() for plugin in openclaw_channel_registry.registry_channel_plugins()
    ]
