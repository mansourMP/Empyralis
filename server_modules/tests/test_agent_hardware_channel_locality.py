"""Execution locality applied to channels (founder, 2026-08-14): "If X agent
is connected to Z hardware, gateway and channel must run there as well."

Before this build, every write that binds an agent's channel state to a
gateway_id -- configure_whatsapp_personal_gateway, configure_telegram_
personal_gateway, recheck/install_imessage_*, and openclaw_provisioning_
service.provision_openclaw_gateway -- accepted ANY gateway_id the caller's
OWNER role could reach in the workspace, never checked against which box the
named agent is actually placed on (install_metadata.preferred_gateway_id).
A gateway's per-channel software CAPABILITY (advertised unconditionally by
every gateway process that bundles the runtime) was the only thing that
looked like a gate, and it says nothing about binding -- three gateways in
one workspace can all legitimately advertise `telegram: True` while only one
of them is where a given agent's tools actually run.

These tests prove three things:
  A. assert_agent_placed_on_gateway is the one gate every write path now
     calls, and it fails closed (wrong box, no box at all, unreadable
     install) with an honest, owner-facing message.
  B. Multi-agent-per-box is unaffected: an agent that IS legitimately placed
     on a gateway can still configure/provision there, including a second
     agent sharing the same box on a different channel.
  C. Moving an agent's hardware (fleet_configure_agent's preferred_gateway_id
     patch) does something coherent with channels that were already paired
     on the OLD box, rather than silently leaving them there -- and reports
     what it did, per this codebase's own outcome-honesty law.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from server_modules import (
    fleet_tools,
    openclaw_provisioning_service,
    personal_channels_service,
)


def _run(coro):
    return asyncio.run(coro)


def _bundle(agent_id: str, *, preferred_gateway_id: str = "", label: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"id": agent_id, "metadata": {}}
    if preferred_gateway_id:
        payload["metadata"]["preferred_gateway_id"] = preferred_gateway_id
    if label:
        payload["label"] = label
    return payload


def _patch_bundle(agent_id: str, *, preferred_gateway_id: str = "", label: Optional[str] = None, found: bool = True):
    async def fake(install_id, *, tenant_id=None, workspace_id=None):
        assert install_id == agent_id
        if not found:
            return None
        return _bundle(agent_id, preferred_gateway_id=preferred_gateway_id, label=label)

    return patch(
        "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
        new=AsyncMock(side_effect=fake),
    )


# ── A. assert_agent_placed_on_gateway itself ───────────────────────────────


def test_placement_matches_is_a_silent_no_op():
    with _patch_bundle("agent-a", preferred_gateway_id="gw-1"):
        _run(
            personal_channels_service.assert_agent_placed_on_gateway(
                tenant_id="t", workspace_id="w", agent_id="agent-a", gateway_id="gw-1",
            )
        )  # must not raise


def test_placement_mismatch_raises_with_an_honest_no_mechanism_message():
    with _patch_bundle("agent-a", preferred_gateway_id="gw-1", label="Sales Bot"):
        with pytest.raises(personal_channels_service.AgentNotPlacedOnGatewayError) as excinfo:
            _run(
                personal_channels_service.assert_agent_placed_on_gateway(
                    tenant_id="t", workspace_id="w", agent_id="agent-a", gateway_id="gw-2",
                )
            )
    message = str(excinfo.value)
    assert "Sales Bot" in message
    for mechanism_word in ("gateway_id", "install_metadata", "preferred_gateway_id"):
        assert mechanism_word not in message


def test_agent_with_no_placement_at_all_gets_a_distinct_honest_message():
    with _patch_bundle("agent-a", preferred_gateway_id="", label="Sales Bot"):
        with pytest.raises(personal_channels_service.AgentNotPlacedOnGatewayError) as excinfo:
            _run(
                personal_channels_service.assert_agent_placed_on_gateway(
                    tenant_id="t", workspace_id="w", agent_id="agent-a", gateway_id="gw-2",
                )
            )
    assert "isn't placed on any computer yet" in str(excinfo.value)


def test_unreadable_install_fails_closed_not_open():
    with _patch_bundle("agent-a", found=False):
        with pytest.raises(personal_channels_service.AgentNotPlacedOnGatewayError):
            _run(
                personal_channels_service.assert_agent_placed_on_gateway(
                    tenant_id="t", workspace_id="w", agent_id="agent-a", gateway_id="gw-2",
                )
            )


def test_blank_agent_id_is_a_no_op_matching_the_pre_existing_unscoped_path():
    """No agent_id -> nothing to enforce, same as _claim_agent_channel_state's
    own "caller is using the pre-existing unscoped path" contract. Proven by
    NOT mocking the install lookup at all and still succeeding."""
    _run(
        personal_channels_service.assert_agent_placed_on_gateway(
            tenant_id="t", workspace_id="w", agent_id="", gateway_id="gw-2",
        )
    )


# ── A continued (WhatsApp/Telegram/iMessage-specific write entrypoints) ────
#
# configure_whatsapp_personal_gateway, configure_telegram_personal_gateway,
# recheck_imessage_personal_gateway and install_imessage_imsg_gateway — the
# four functions this section used to test the placement guard through —
# were DELETED 2026-08-14 (full OpenClaw channel cutover) along with the
# Baileys/gramjs/imsg-RPC runtimes they configured. WhatsApp, Telegram and
# iMessage are now OpenClaw-transported, with no first-party Empyralis
# write entrypoint of their own — configuration happens inside OpenClaw
# itself. The placement guard these tests exercised, assert_agent_placed_on_
# gateway, is still tested directly above (section A) and through the path
# every personal channel now actually configures through,
# provision_openclaw_gateway, below.


def _registration(**overrides) -> Dict[str, Any]:
    base = {"gateway_id": "gw-2", "tenant_id": "t", "workspace_id": "w", "user_id": "u"}
    base.update(overrides)
    return base


# ── A continued: the OpenClaw provisioning entrypoint ──────────────────────


def test_provision_openclaw_gateway_refuses_on_the_wrong_gateway():
    exploding = AsyncMock(side_effect=AssertionError("must not reach the gateway on a placement mismatch"))
    with (
        _patch_bundle("agent-a", preferred_gateway_id="gw-1"),
        patch.object(openclaw_provisioning_service.gateway_execution_service, "execute_tool_via_gateway", exploding),
    ):
        with pytest.raises(openclaw_provisioning_service.OpenClawProvisioningError) as excinfo:
            _run(
                openclaw_provisioning_service.provision_openclaw_gateway(
                    gateway_id="gw-2", tenant_id="t", workspace_id="w", agent_id="agent-a",
                )
            )
    exploding.assert_not_awaited()
    assert excinfo.value.status_code == 403
    assert excinfo.value.reason_code == "agent_not_placed_on_gateway"


def test_reconcile_reports_wrong_hardware_as_its_own_distinct_outcome_not_unreachable():
    """The fourth outcome, never collapsed into "unreachable" -- this is a
    PERMANENT mismatch (the caller passed a gateway_id that isn't this
    agent's placement), not a transient offline box that resolves itself at
    next boot. Telling an owner to "wait, it'll pick it up" for a setting
    that can never take effect on its own would be the exact lie
    CLAUDE.md's outcome-honesty law exists to prevent."""
    with _patch_bundle("agent-a", preferred_gateway_id="gw-1"):
        result = _run(
            openclaw_provisioning_service.reconcile_openclaw_policy_best_effort(
                channel_key="openclaw_feishu",
                gateway_id="gw-2",
                tenant_id="t",
                workspace_id="w",
                agent_id="agent-a",
            )
        )
    assert result["status"] == "wrong_hardware"
    assert result["status"] != "unreachable"
    assert result["channel_key"] == "openclaw_feishu"


# ── B. multi-agent-per-box is unaffected ───────────────────────────────────


def test_two_agents_sharing_one_gateway_can_each_provision_their_own_channel():
    """The exact scenario CLAUDE.md's multi-agent-per-box section already
    proved works (agent A on Telegram, agent B on Feishu, one box) -- this
    guard must not break it. Both calls target the SAME gateway_id both
    agents are genuinely placed on."""

    async def fake_dm(*, tenant_id, workspace_id, agent_id, channel_key):
        return {"mode": "owner_only", "allowlist": [], "pending_pairing": {}}

    async def fake_group(*, tenant_id, workspace_id, agent_id, channel_key):
        return {"mode": "disabled", "allowlist": [], "require_mention": True}

    captured: List[str] = []

    async def fake_execute(**kwargs):
        captured.append(kwargs["gateway_id"])
        return {"result": {"status": "provisioned"}}

    installs = [
        {"id": "agent-a", "enabled": True, "metadata": {"preferred_gateway_id": "gw-shared"}},
        {"id": "agent-b", "enabled": True, "metadata": {"preferred_gateway_id": "gw-shared"}},
    ]

    async def fake_installs(*, tenant_id, workspace_id):
        return installs

    async def fake_bindings(*, tenant_id, workspace_id, agent_install_id, enabled_only):
        return []

    async def fake_bundle(install_id, *, tenant_id=None, workspace_id=None):
        return next(i for i in installs if i["id"] == install_id)

    with (
        patch.object(personal_channels_service, "_load_agent_dm_policy_config", fake_dm),
        patch.object(personal_channels_service, "_load_agent_group_policy_config", fake_group),
        patch.object(openclaw_provisioning_service.gateway_execution_service, "execute_tool_via_gateway", fake_execute),
        patch(
            "server_modules.agent_registry_repository.list_workspace_agent_installs",
            new=AsyncMock(side_effect=fake_installs),
        ),
        patch.object(
            openclaw_provisioning_service.agent_bindings_repository,
            "list_agent_channel_bindings",
            fake_bindings,
        ),
        patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(side_effect=fake_bundle),
        ),
    ):
        for agent_id in ("agent-a", "agent-b"):
            result = _run(
                openclaw_provisioning_service.provision_openclaw_gateway(
                    gateway_id="gw-shared", tenant_id="t", workspace_id="w", agent_id=agent_id,
                )
            )
            assert result["status"] == "provisioned"
    assert captured == ["gw-shared", "gw-shared"]


# ── C. moving hardware acts on the OLD box's channels, honestly ───────────


# test_handle_agent_hardware_relocated_disconnects_a_live_whatsapp_claim_on_
# the_old_box DELETED 2026-08-14 (full OpenClaw channel cutover):
# disconnect_whatsapp_personal_gateway and the WhatsApp/Telegram-specific
# disconnect loop it tested inside handle_agent_hardware_relocated are both
# gone — WhatsApp/Telegram are OpenClaw-transported now and fall entirely
# under the re-provisioning path the tests below already cover.


def test_handle_agent_hardware_relocated_never_touches_another_agents_session():
    """The claimant on the old gateway is a DIFFERENT agent -- re-provisioning
    must still list that other agent as remaining, never explode or drop
    them."""
    with (
        patch.object(
            personal_channels_service.gateway_state_repository,
            "get_gateway_registration",
            return_value={"gateway_id": "gw-old", "tenant_id": "t", "workspace_id": "w"},
        ),
        patch.object(
            personal_channels_service,
            "agents_sharing_gateway",
            new=AsyncMock(return_value=["agent-b"]),
        ),
        patch.object(
            openclaw_provisioning_service,
            "provision_openclaw_gateway",
            new=AsyncMock(return_value={"status": "provisioned"}),
        ) as provision_mock,
    ):
        report = _run(
            personal_channels_service.handle_agent_hardware_relocated(
                tenant_id="t", workspace_id="w", agent_id="agent-a",
                old_gateway_id="gw-old", new_gateway_id="gw-new",
            )
        )
    assert report["released_channels"] == []
    provision_mock.assert_awaited_once()
    assert provision_mock.await_args.kwargs.get("agent_id") == "agent-b"


def test_handle_agent_hardware_relocated_reprovisions_the_old_box_for_a_remaining_agent():
    reprovisioned: Dict[str, Any] = {}

    async def fake_provision(**kwargs):
        reprovisioned.update(kwargs)
        return {"status": "provisioned"}

    with (
        patch.object(
            personal_channels_service.gateway_state_repository,
            "get_gateway_registration",
            return_value={"gateway_id": "gw-old", "tenant_id": "t", "workspace_id": "w"},
        ),
        patch.object(personal_channels_service, "_personal_channel_state", return_value=None),
        patch.object(personal_channels_service, "_resolve_agent_id_for_inbound", return_value=""),
        patch.object(
            personal_channels_service,
            "agents_sharing_gateway",
            new=AsyncMock(return_value=["agent-b"]),
        ),
        patch.object(openclaw_provisioning_service, "provision_openclaw_gateway", fake_provision),
    ):
        report = _run(
            personal_channels_service.handle_agent_hardware_relocated(
                tenant_id="t", workspace_id="w", agent_id="agent-a",
                old_gateway_id="gw-old", new_gateway_id="gw-new",
            )
        )
    assert reprovisioned["gateway_id"] == "gw-old"
    assert reprovisioned["agent_id"] == "agent-b"
    assert any("re-provisioned" in note for note in report["notes"])


def test_handle_agent_hardware_relocated_says_so_honestly_when_no_agent_remains():
    with (
        patch.object(
            personal_channels_service.gateway_state_repository,
            "get_gateway_registration",
            return_value={"gateway_id": "gw-old", "tenant_id": "t", "workspace_id": "w"},
        ),
        patch.object(personal_channels_service, "_personal_channel_state", return_value=None),
        patch.object(personal_channels_service, "_resolve_agent_id_for_inbound", return_value=""),
        patch.object(
            personal_channels_service,
            "agents_sharing_gateway",
            new=AsyncMock(return_value=[]),
        ),
    ):
        report = _run(
            personal_channels_service.handle_agent_hardware_relocated(
                tenant_id="t", workspace_id="w", agent_id="agent-a",
                old_gateway_id="gw-old", new_gateway_id="gw-new",
            )
        )
    assert any("No other agent" in note for note in report["notes"])


def test_handle_agent_hardware_relocated_is_a_no_op_on_the_very_first_placement():
    """No prior gateway (agent had none before) -- nothing to relocate.
    Proven by NOT mocking get_gateway_registration at all and still
    succeeding with an empty report."""
    report = _run(
        personal_channels_service.handle_agent_hardware_relocated(
            tenant_id="t", workspace_id="w", agent_id="agent-a",
            old_gateway_id="", new_gateway_id="gw-new",
        )
    )
    assert report == {"old_gateway_id": None, "new_gateway_id": "gw-new", "released_channels": [], "notes": []}


# ── fleet_configure_agent wiring ────────────────────────────────────────────


def _agent_bundle_with_prior_gateway(agent_id: str, prior_gateway_id: str) -> Dict[str, Any]:
    return {"id": agent_id, "install_metadata": {"preferred_gateway_id": prior_gateway_id}}


def test_fleet_configure_agent_triggers_relocation_report_on_a_real_move():
    relocated_call: Dict[str, Any] = {}

    async def fake_relocate(**kwargs):
        relocated_call.update(kwargs)
        return {"old_gateway_id": "gw-old", "new_gateway_id": "gw-new", "released_channels": ["whatsapp_personal"], "notes": ["moved"]}

    with (
        patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=_agent_bundle_with_prior_gateway("agent-x", "gw-old")),
        ),
        patch(
            "server_modules.agent_registry_repository.update_workspace_agent_install",
            new=AsyncMock(return_value=_agent_bundle_with_prior_gateway("agent-x", "gw-new")),
        ),
        patch.object(fleet_tools, "recommended_model_config_for_gateway", return_value=None),
        patch.object(personal_channels_service, "handle_agent_hardware_relocated", fake_relocate),
    ):
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                patch={"preferred_gateway_id": "gw-new"},
            )
        )
    assert result["ok"] is True
    assert relocated_call["old_gateway_id"] == "gw-old"
    assert relocated_call["new_gateway_id"] == "gw-new"
    assert relocated_call["agent_id"] == "agent-x"
    assert result["hardware_relocated"]["released_channels"] == ["whatsapp_personal"]


def test_fleet_configure_agent_never_relocates_on_the_first_placement():
    """No PRIOR gateway (the agent had none) -- this is a first bind, not a
    move, and must not fire the relocation side effect at all."""
    exploding = AsyncMock(side_effect=AssertionError("must not run relocation cleanup on a first placement"))
    with (
        patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={"id": "agent-x", "install_metadata": {}}),
        ),
        patch(
            "server_modules.agent_registry_repository.update_workspace_agent_install",
            new=AsyncMock(return_value=_agent_bundle_with_prior_gateway("agent-x", "gw-new")),
        ),
        patch.object(fleet_tools, "recommended_model_config_for_gateway", return_value=None),
        patch.object(personal_channels_service, "handle_agent_hardware_relocated", exploding),
    ):
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                patch={"preferred_gateway_id": "gw-new"},
            )
        )
    assert result["ok"] is True
    assert "hardware_relocated" not in result
    exploding.assert_not_awaited()


def test_fleet_configure_agent_relocation_failure_never_fails_the_save():
    """The metadata write already committed by the time relocation cleanup
    runs -- a cleanup failure must be reported, never turned into a failed
    save the owner has to retry (which would re-apply an already-applied
    patch, or worse, look like the hardware change itself failed)."""

    async def boom(**kwargs):
        raise RuntimeError("old gateway offline")

    with (
        patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=_agent_bundle_with_prior_gateway("agent-x", "gw-old")),
        ),
        patch(
            "server_modules.agent_registry_repository.update_workspace_agent_install",
            new=AsyncMock(return_value=_agent_bundle_with_prior_gateway("agent-x", "gw-new")),
        ),
        patch.object(fleet_tools, "recommended_model_config_for_gateway", return_value=None),
        patch.object(personal_channels_service, "handle_agent_hardware_relocated", boom),
    ):
        result = _run(
            fleet_tools.fleet_configure_agent(
                actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                patch={"preferred_gateway_id": "gw-new"},
            )
        )
    assert result["ok"] is True
    assert "old gateway offline" in result["hardware_relocated"]["notes"][0]
