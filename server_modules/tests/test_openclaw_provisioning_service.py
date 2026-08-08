"""Cloud half of CHANNEL-ADOPTION-PLAN.md step 4.

The thing these tests exist to protect is narrow and specific: the policy that
reaches the box must be the policy the LIVE INBOUND GATES read, for EVERY
OpenClaw channel, every time. Any divergence — a second reader, a skipped
channel, a coerced default — reintroduces exactly the failure step 4 exists to
close, and does it silently, because a message OpenClaw drops before our tap is
a message nothing anywhere can report.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from server_modules import (
    channel_lane_contract_service,
    openclaw_channel_registry,
    openclaw_provisioning_service,
    personal_channels_service,
)


def test_openclaw_channel_key_suffix_is_openclaws_own_channel_id():
    """The invariant that `openclaw_qq` violated (their id is `qqbot`), broken
    silently in both directions until step 4's provisioning had to write
    `channels.<id>` into a real config.

    This used to assert a hand-typed set of five suffixes, which could only
    ever confirm that two hand-written lists agreed with a third. The suffixes
    are now GENERATED from OpenClaw's own registry, so the assertion that
    matters is structural: every key round-trips to an id OpenClaw actually
    has, and nothing anywhere invents one.
    """
    assert personal_channels_service.OPENCLAW_PERSONAL_CHANNELS, (
        "The OpenClaw channel set is empty — every assertion in the loop below "
        "would pass vacuously while the transport carries nothing."
    )

    for channel_key in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS:
        channel_id = openclaw_provisioning_service.openclaw_channel_id(channel_key)
        # The suffix IS their id, verbatim — never a normalization of it.
        assert channel_key == f"openclaw_{channel_id}"
        # And that id is one OpenClaw declares, not one we invented. This is
        # the half a bare prefix strip could never check.
        assert channel_id in openclaw_channel_registry.CHANNELS_BY_ID

    assert set(channel_lane_contract_service.OPENCLAW_PERSONAL_CHANNEL_SPECS) == set(
        personal_channels_service.OPENCLAW_PERSONAL_CHANNELS
    )


def test_openclaw_uses_their_qqbot_id_and_never_the_qq_that_broke_the_lane():
    """A regression pin on the one id this codebase actually got wrong.

    Independently sourced: `qqbot` is what openclaw@2026.6.10's own config
    schema (`channels.qqbot`) and dist/message-channel-constants-*.js's
    NATIVE_APPROVAL_CHANNELS call it. Kept as a named fact rather than a full
    list, so it cannot go stale the day OpenClaw adds a channel.
    """
    assert "qqbot" in openclaw_channel_registry.CHANNELS_BY_ID
    assert "qq" not in openclaw_channel_registry.CHANNELS_BY_ID
    assert "openclaw_qqbot" in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS
    assert "openclaw_qq" not in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS


def test_openclaw_channel_id_refuses_a_non_openclaw_key():
    with pytest.raises(ValueError):
        openclaw_provisioning_service.openclaw_channel_id("telegram_personal")


def _run(coro):
    """`asyncio.run`, not `get_event_loop().run_until_complete` — the latter
    picks up (and can be broken by) a loop another test in the same session
    already created or closed, which made these pass alone and fail in a full
    run."""
    return asyncio.run(coro)


def test_policy_payload_is_read_through_the_live_gate_loaders(monkeypatch):
    """Not through a second reader.

    The loaders carry the fail-closed fallbacks, the normalization, and the
    defaults. A separate reader here would be a second opinion about what a
    policy means — and the two would disagree exactly where it matters, since
    one decides what reaches the box and the other decides what the box lets
    through.
    """
    seen: List[Dict[str, Any]] = []

    async def fake_dm(*, tenant_id, workspace_id, agent_id, channel_key):
        seen.append({"loader": "dm", "channel_key": channel_key})
        return {"mode": "allowlist", "allowlist": ["sender-1"], "pending_pairing": {}}

    async def fake_group(*, tenant_id, workspace_id, agent_id, channel_key):
        seen.append({"loader": "group", "channel_key": channel_key})
        return {"mode": "allowlist", "allowlist": ["chat-1"], "require_mention": False}

    monkeypatch.setattr(personal_channels_service, "_load_agent_dm_policy_config", fake_dm)
    monkeypatch.setattr(personal_channels_service, "_load_agent_group_policy_config", fake_group)

    policies = _run(
        openclaw_provisioning_service.build_openclaw_channel_policies(
            tenant_id="t", workspace_id="w", agent_id="a"
        )
    )

    # EVERY channel, always — a channel omitted from the payload would keep
    # whatever policy the previous provisioning run left in OpenClaw's config.
    assert {policy["channel_key"] for policy in policies} == set(
        personal_channels_service.OPENCLAW_PERSONAL_CHANNELS
    )
    assert len([entry for entry in seen if entry["loader"] == "dm"]) == len(
        personal_channels_service.OPENCLAW_PERSONAL_CHANNELS
    )

    feishu = next(policy for policy in policies if policy["channel_id"] == "feishu")
    assert feishu["dm_policy"] == {"mode": "allowlist", "allowlist": ["sender-1"]}
    assert feishu["group_policy"] == {
        "mode": "allowlist",
        "allowlist": ["chat-1"],
        "require_mention": False,
    }


def test_provision_dispatches_the_capability_with_the_policy_and_trusts_the_box(monkeypatch):
    captured: Dict[str, Any] = {}

    async def fake_load_dm(*, tenant_id, workspace_id, agent_id, channel_key):
        return {"mode": "open", "allowlist": [], "pending_pairing": {}}

    async def fake_load_group(*, tenant_id, workspace_id, agent_id, channel_key):
        return {"mode": "disabled", "allowlist": [], "require_mention": True}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return {
            "result": {
                "status": "provisioned",
                "config_fingerprint": "abc",
                "disabled_channels": [],
            }
        }

    monkeypatch.setattr(personal_channels_service, "_load_agent_dm_policy_config", fake_load_dm)
    monkeypatch.setattr(personal_channels_service, "_load_agent_group_policy_config", fake_load_group)
    monkeypatch.setattr(
        openclaw_provisioning_service.gateway_execution_service,
        "execute_tool_via_gateway",
        fake_execute,
    )

    result = _run(
        openclaw_provisioning_service.provision_openclaw_gateway(
            gateway_id="gw-1", tenant_id="t", workspace_id="w", agent_id="a"
        )
    )

    assert captured["capability_id"] == "openclaw.provision"
    assert captured["gateway_id"] == "gw-1"
    channels = captured["arguments"]["channels"]
    assert len(channels) == len(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS)
    # Policy only. No rendered OpenClaw config, and no secrets: the box holds
    # both tokens already and is the only party that knows which OpenClaw is
    # installed.
    for channel in channels:
        assert set(channel) == {"channel_id", "channel_key", "enabled", "dm_policy", "group_policy"}
    payload = str(captured["arguments"])
    assert "token" not in payload

    # The box's answer is returned verbatim, never re-interpreted.
    assert result["status"] == "provisioned"
    assert result["config_fingerprint"] == "abc"


def test_reconcile_is_best_effort_and_never_raises(monkeypatch):
    """A saved setting must not fail because the box is offline — it is already
    in Postgres, and the box re-asserts it from its own provisioning record at
    next boot."""

    async def boom(**kwargs):
        raise openclaw_provisioning_service.OpenClawProvisioningError("gateway offline", status_code=409)

    monkeypatch.setattr(openclaw_provisioning_service, "provision_openclaw_gateway", boom)
    result = _run(
        openclaw_provisioning_service.reconcile_openclaw_policy_best_effort(
            channel_key="openclaw_feishu",
            gateway_id="gw-1",
            tenant_id="t",
            workspace_id="w",
            agent_id="a",
        )
    )
    assert result is None


def test_reconcile_is_a_no_op_for_a_non_openclaw_channel(monkeypatch):
    async def should_not_run(**kwargs):  # pragma: no cover - must never execute
        raise AssertionError("a first-party channel must never trigger OpenClaw provisioning")

    monkeypatch.setattr(openclaw_provisioning_service, "provision_openclaw_gateway", should_not_run)
    for channel_key in ("telegram_personal", "whatsapp_personal", "signal_personal"):
        assert (
            _run(
                openclaw_provisioning_service.reconcile_openclaw_policy_best_effort(
                    channel_key=channel_key,
                    gateway_id="gw-1",
                    tenant_id="t",
                    workspace_id="w",
                    agent_id="a",
                )
            )
            is None
        )
