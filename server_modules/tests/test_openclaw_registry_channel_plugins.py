"""The registry half of the OpenClaw channel manifest (source 8).

Everything in `test_openclaw_channel_registry.py` covers channels the pinned
OpenClaw build already knows about. This file covers the ones it does not: the
channel-capable plugins OpenClaw's own plugin registry publishes, which
Empyralis carried NONE of until 2026-08-15 because every manifest source
described only what was bundled or already installed.

The rule these tests defend is the founder's, stated many times: whatever
OpenClaw offers as a channel, Empyralis offers. So the assertions here are
mostly about what must NOT happen — a curated subset, a guessed channel id, a
trust filter, or an install path that accepts an arbitrary npm package.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from server_modules import openclaw_channel_registry, openclaw_provisioning_service

MANIFEST_PATH = Path(openclaw_channel_registry.MANIFEST_PATH)
GENERATOR_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_openclaw_channel_manifest.py"
)


class RegistryChannelPluginShapeTests:
    """Structural facts about the derived set."""


def test_registry_channel_plugins_clear_the_floor():
    """A truncated derivation must be a loud failure, not a short catalog.

    The whole defect this feature fixes was a channel surface that looked
    complete while carrying a fraction of what upstream offers. An empty or
    tiny `registry_channel_plugins` would recreate exactly that, silently.
    """
    plugins = openclaw_channel_registry.registry_channel_plugins()
    assert len(plugins) >= openclaw_channel_registry.MINIMUM_EXPECTED_REGISTRY_CHANNEL_PLUGINS


def test_no_registry_plugin_claims_a_channel_id():
    """A plugin that is not installed cannot have a known channel id.

    A third-party plugin registers its channel at runtime, and the id it
    claims is demonstrably not its package or plugin id
    (`openclaw-plugin-yuanbao` publishes channel `yuanbao`). Minting one would
    rebuild the `openclaw_qq`-vs-`openclaw_qqbot` defect: a policy written to a
    `channels.<id>` node that does not exist, and an outbound send answering
    "unsupported channel".
    """
    for plugin in openclaw_channel_registry.registry_channel_plugins():
        assert plugin.channel_id is None, plugin.npm_package
        assert plugin.channel_key is None, plugin.npm_package
        assert plugin.config_schema_present is False, plugin.npm_package
        assert plugin.connect_method == "plugin_absent", plugin.npm_package


def test_a_guessed_channel_id_is_refused_at_load():
    """The guard above is enforced, not merely satisfied by today's data."""
    record = json.loads(MANIFEST_PATH.read_text())["registry_channel_plugins"][0]
    with pytest.raises(RuntimeError, match="cannot have a known channel id"):
        openclaw_channel_registry.OpenClawRegistryChannelPlugin(
            {**record, "channel_id": "guessed"}
        )


def test_every_registry_plugin_carries_trust():
    """Trust is not optional: installing one runs third-party code beside the
    owner's messages, and contributes a `channels.<id>.tools.*` surface the
    global `tools.*` lockdown does not reach."""
    for plugin in openclaw_channel_registry.registry_channel_plugins():
        assert isinstance(plugin.trust, dict) and plugin.trust, plugin.npm_package
        assert "is_official" in plugin.trust, plugin.npm_package
        assert "verification_tier" in plugin.trust, plugin.npm_package


def test_a_trustless_registry_plugin_is_refused_at_load():
    record = json.loads(MANIFEST_PATH.read_text())["registry_channel_plugins"][0]
    stripped = {key: value for key, value in record.items() if key != "trust"}
    with pytest.raises(RuntimeError, match="no trust block"):
        openclaw_channel_registry.OpenClawRegistryChannelPlugin(stripped)


def test_community_plugins_are_carried_not_filtered_out():
    """The founder's instruction is to carry what OpenClaw carries, community
    plugins included. A derivation that quietly kept only the official ones
    would be the curated subset this whole feature removes."""
    plugins = openclaw_channel_registry.registry_channel_plugins()
    community = [p for p in plugins if not p.is_official]
    official = [p for p in plugins if p.is_official]
    assert community, "the registry set carries no community plugins at all"
    # Both kinds present — never one filtered away.
    assert official, "the registry set carries no official plugins at all"


def test_personal_telegram_is_offered():
    """`telegram-userbot` is the example that exposed the defect: OpenClaw has
    personal-account Telegram over MTProto and Empyralis offered no way to
    reach it, because the generator only ever read the bundled catalog.

    Asserted by PACKAGE, which is the derivation's own key, so this stays a
    real regression test rather than a hand-maintained channel list.
    """
    assert openclaw_channel_registry.is_registry_channel_package("telegram-userbot")


def test_registry_offers_never_duplicate_a_carried_channel():
    """One platform, one card. A package that is already a resolved channel
    must not also appear as an offer to install."""
    carried = {
        (channel.plugin_install or {}).get("npm_package")
        for channel in openclaw_channel_registry.CHANNELS
        if channel.plugin_install
    }
    offered = {p.npm_package for p in openclaw_channel_registry.registry_channel_plugins()}
    assert not (carried & offered)


def test_every_registry_plugin_records_how_it_was_confirmed():
    """The category tag is a candidate filter; the plugin's own artifact is the
    decision. `confirmed_by` is that evidence, kept so a regeneration can be
    audited rather than trusted."""
    for plugin in openclaw_channel_registry.registry_channel_plugins():
        assert plugin.confirmed_by, plugin.npm_package


def test_npm_spec_is_pinned_to_a_version():
    """A bare `<name>@<version>` resolves against npm and fails for a package
    published only to ClawHub — measured, not assumed. And "newest compatible"
    moves, so two boxes provisioned a month apart would
    silently run different code — the same reason every other install spec in
    this manifest is pinned."""
    for plugin in openclaw_channel_registry.registry_channel_plugins():
        assert plugin.install_spec.startswith("clawhub:"), plugin.install_spec
        assert plugin.install_spec.endswith(plugin.version), plugin.install_spec


# ── The install allowlist ────────────────────────────────────────────────


def test_install_specs_reject_a_package_the_manifest_never_vouched_for():
    """The value ends up as the argument to `openclaw plugins install` on a
    customer's own machine. Nothing but the derived manifest may put a name
    there."""
    specs = openclaw_provisioning_service.build_registry_plugin_specs(
        ["definitely-not-a-real-openclaw-channel-plugin", "left-pad"]
    )
    assert specs == []


def test_install_specs_resolve_a_known_package_to_its_pinned_spec():
    specs = openclaw_provisioning_service.build_registry_plugin_specs(["telegram-userbot"])
    assert len(specs) == 1
    plugin = openclaw_channel_registry.registry_channel_plugin_for_package("telegram-userbot")
    assert specs[0] == {
        "npm_package": "telegram-userbot",
        "install_spec": plugin.install_spec,
        "plugin_id": plugin.plugin_id,
    }
    # The caller's string is never forwarded verbatim — the manifest's own
    # pinned spec is, which is what makes two boxes run the same code.
    assert specs[0]["install_spec"] != "telegram-userbot"


def test_install_specs_deduplicate_and_tolerate_blanks():
    specs = openclaw_provisioning_service.build_registry_plugin_specs(
        ["telegram-userbot", "telegram-userbot", "", "   ", None]  # type: ignore[list-item]
    )
    assert len(specs) == 1


def test_install_specs_are_empty_when_nothing_was_asked_for():
    assert openclaw_provisioning_service.build_registry_plugin_specs(None) == []
    assert openclaw_provisioning_service.build_registry_plugin_specs([]) == []


# ── The generator's own discipline ───────────────────────────────────────


def test_generator_never_hardcodes_a_channel_search_term():
    """A hand-written set of search terms would be a channel list wearing a
    derivation's clothes — the exact defect this script exists to remove.

    Enumeration goes through `/api/v1/packages` (a cursor-paginated listing of
    every published package), never `plugins search <term>`. A structural
    check, because a term list would type-check and run perfectly.
    """
    source = GENERATOR_PATH.read_text()
    assert "/api/v1/packages" in source
    assert "packages/search" not in source, (
        "the generator reached for the query-only search endpoint; enumeration "
        "must use the full listing, or the channel set becomes a keyword list"
    )


def test_generator_confirms_channels_structurally_not_by_category_alone():
    """Stage 1 (the publisher's `channels` category) is over-broad — measured,
    it admits 326 packages of which 196 register no channel at all. The
    decision must come from the plugin's own artifact."""
    source = GENERATOR_PATH.read_text()
    assert "registerChannel" in source
    assert "openclaw" in source and "channel" in source


def test_manifest_records_candidates_it_could_not_classify():
    """A candidate whose artifact is broken upstream must be VISIBLE, not
    missing — a silently absent package is indistinguishable from curation."""
    document = json.loads(MANIFEST_PATH.read_text())
    assert "unresolved_candidates" in document["registry"]
    assert isinstance(document["registry"]["unresolved_candidates"], list)


def test_registry_provenance_names_its_source():
    document = json.loads(MANIFEST_PATH.read_text())
    registry = document["registry"]
    assert registry["endpoint"] == "/api/v1/packages"
    assert registry["candidate_category"] == "channels"
    assert registry["source"].startswith("http")


def test_generator_raises_on_a_transient_read_failure_but_records_a_permanent_one():
    """The two reasons a candidate cannot be read are different facts.

    Transient (rate limit, blip) -> raise, because accepting it silently
    shortens the offered surface and a re-run fixes it. Permanent (the
    package's own artifact is broken upstream) -> record, because failing
    would let one broken publisher block every future regeneration.

    Asserted against the classifier directly rather than by driving the
    network.
    """
    module = _load_generator_module()
    import urllib.error

    permanent = urllib.error.HTTPError("u", 404, "gone", None, None)  # type: ignore[arg-type]
    redirect_loop = urllib.error.HTTPError("u", 307, "loop", None, None)  # type: ignore[arg-type]
    transient = urllib.error.HTTPError("u", 503, "busy", None, None)  # type: ignore[arg-type]
    assert module._is_permanent_artifact_failure(permanent) is True
    assert module._is_permanent_artifact_failure(redirect_loop) is True
    assert module._is_permanent_artifact_failure(transient) is False
    assert module._is_permanent_artifact_failure(TimeoutError()) is False


def _load_generator_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_openclaw_manifest_generator", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_generator_source_parses():
    """Cheap canary: the structural assertions above read this file as text."""
    ast.parse(GENERATOR_PATH.read_text())
