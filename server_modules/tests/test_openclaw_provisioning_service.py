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
        assert set(channel) == {
            "channel_id",
            "channel_key",
            "enabled",
            "dm_policy",
            "group_policy",
            # Not policy: "does this box need this channel's third-party plugin
            # package on disk". A separate axis, and the only per-channel field
            # that is ever narrowed — policy is still pushed for every channel.
            "install_plugin",
        }
        # Fail-closed: no enabled binding and no stored policy key for any
        # channel here, so nothing is fetched. Acquiring third-party code onto
        # a customer's machine is never what an absent signal means.
        assert channel["install_plugin"] is False
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


# ── which channels get their PLUGIN installed (step 5) ────────────────────
#
# Twenty of OpenClaw's twenty-seven channels are separate npm packages. Policy
# is still pushed for all of them; only "does this box need this channel's
# code" is narrowed. These tests protect the two properties that decision has
# to have: it must break the connect/install deadlock, and it must never grow
# the set by accident.


def _stub_channel_in_use_sources(monkeypatch, *, bindings, install_metadata):
    async def fake_bindings(*, tenant_id, workspace_id, agent_install_id, enabled_only):
        assert enabled_only is True, "a disabled binding is not a channel in use"
        return [{"key": key} for key in bindings]

    async def fake_install(install_id, *, tenant_id=None, workspace_id=None):
        return {"install_metadata": install_metadata}

    monkeypatch.setattr(
        openclaw_provisioning_service.agent_bindings_repository,
        "list_agent_channel_bindings",
        fake_bindings,
    )
    monkeypatch.setattr(
        openclaw_provisioning_service.agent_registry_repository,
        "get_workspace_agent_install_bundle",
        fake_install,
    )


def test_a_stored_policy_key_counts_as_in_use_even_with_a_default_value(monkeypatch):
    """The deadlock-breaker, and the reason PRESENCE is the test.

    An enabled binding is written only once a session reaches `connected`, and
    a session cannot connect before the plugin exists. Bindings alone therefore
    deadlock: no plugin -> no connection -> no binding -> no plugin. A stored
    policy key is written the moment the owner touches the channel at all.

    It must be key presence and never the value: the loaders normalize a
    MISSING entry into a full default document, so a value comparison cannot
    tell "never configured" from "configured, and happens to match the
    default".
    """
    _stub_channel_in_use_sources(
        monkeypatch,
        bindings=[],
        install_metadata={
            "group_policy": {
                # Present, and holding exactly what an unconfigured channel
                # would normalize to. Still counts.
                "openclaw_feishu": {
                    "mode": personal_channels_service.DEFAULT_GROUP_POLICY_MODE,
                    "allowlist": [],
                    "require_mention": personal_channels_service.DEFAULT_REQUIRE_MENTION,
                },
            },
        },
    )
    in_use = _run(
        openclaw_provisioning_service.channels_in_use(
            tenant_id="t", workspace_id="w", agent_id="a"
        )
    )
    assert in_use == {"openclaw_feishu"}


def test_an_enabled_binding_counts_as_in_use(monkeypatch):
    _stub_channel_in_use_sources(
        monkeypatch,
        bindings=["openclaw_line", "telegram_personal"],
        install_metadata={},
    )
    in_use = _run(
        openclaw_provisioning_service.channels_in_use(
            tenant_id="t", workspace_id="w", agent_id="a"
        )
    )
    # A first-party channel_key is never an OpenClaw plugin request.
    assert in_use == {"openclaw_line"}


def test_channels_in_use_fails_closed_when_a_source_is_unreadable(monkeypatch):
    """Fewer channels, never more. A false negative is a reported
    `installed: false` the owner can act on; a false positive is third-party
    code fetched onto a machine we do not own."""

    async def boom(**kwargs):
        raise RuntimeError("control plane unavailable")

    async def boom_install(install_id, *, tenant_id=None, workspace_id=None):
        raise RuntimeError("install bundle unreadable")

    monkeypatch.setattr(
        openclaw_provisioning_service.agent_bindings_repository,
        "list_agent_channel_bindings",
        boom,
    )
    monkeypatch.setattr(
        openclaw_provisioning_service.agent_registry_repository,
        "get_workspace_agent_install_bundle",
        boom_install,
    )
    in_use = _run(
        openclaw_provisioning_service.channels_in_use(
            tenant_id="t", workspace_id="w", agent_id="a"
        )
    )
    assert in_use == set()


def test_an_explicit_install_request_brings_a_channel_up_and_cannot_invent_one(monkeypatch):
    """`install_channels` is the setup lever — the request a "Connect Feishu"
    action makes before any binding or stored policy can exist. It is filtered
    against the generated OpenClaw channel set, so it can never introduce a
    channel_key the transport does not carry."""

    async def fake_load_dm(*, tenant_id, workspace_id, agent_id, channel_key):
        return {"mode": "open", "allowlist": [], "pending_pairing": {}}

    async def fake_load_group(*, tenant_id, workspace_id, agent_id, channel_key):
        return {"mode": "disabled", "allowlist": [], "require_mention": True}

    monkeypatch.setattr(personal_channels_service, "_load_agent_dm_policy_config", fake_load_dm)
    monkeypatch.setattr(personal_channels_service, "_load_agent_group_policy_config", fake_load_group)
    _stub_channel_in_use_sources(monkeypatch, bindings=[], install_metadata={})

    channels = _run(
        openclaw_provisioning_service.build_openclaw_channel_policies(
            tenant_id="t",
            workspace_id="w",
            agent_id="a",
            install_channel_keys=[
                "openclaw_feishu",
                "openclaw_not_a_real_channel",
                "telegram_personal",
                "",
            ],
        )
    )
    requested = {entry["channel_key"] for entry in channels if entry["install_plugin"]}
    assert requested == {"openclaw_feishu"}
    # Every channel still receives a policy — narrowing installs must never
    # narrow policy, or a channel omitted from the config keeps whatever the
    # last run left.
    assert len(channels) == len(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS)


def test_the_plugin_install_descriptor_exists_for_every_transported_channel():
    """The manifest half of the same guarantee the gateway asserts: a channel
    is bundled (`plugin_install: null`) or installable, never neither. A
    channel in neither bucket would be advertised by Empyralis and impossible
    to bring up — which is precisely the state Feishu was in."""
    channels = openclaw_channel_registry.CHANNELS
    assert channels, "an empty registry would pass every assertion below vacuously"
    bundled = 0
    installable = 0
    for channel in channels:
        descriptor = channel.plugin_install
        if descriptor is None:
            bundled += 1
            continue
        installable += 1
        assert descriptor["required"] is True
        assert descriptor["plugin_id"]
        assert descriptor["npm_package"]
        assert descriptor["npm_spec"].startswith(descriptor["npm_package"])
    assert bundled > 0 and installable > 0
    assert bundled + installable == len(channels)
    # Read from their catalog, never guessed: `@openclaw/<id>` is wrong for
    # all four external packages.
    by_id = openclaw_channel_registry.CHANNELS_BY_ID
    assert by_id["feishu"].plugin_install["npm_package"] == "@openclaw/feishu"
    assert by_id["wecom"].plugin_install["npm_package"] == "@wecom/wecom-openclaw-plugin"
    assert by_id["wecom"].plugin_install["plugin_id"] == "wecom-openclaw-plugin"
    assert by_id["telegram"].plugin_install is None
