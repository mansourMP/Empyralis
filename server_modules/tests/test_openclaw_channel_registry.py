"""The OpenClaw channel set is DERIVED — these are the checks that keep it so.

WHAT REPLACED THE OLD DRIFT CHECK, AND WHY IT IS STRONGER

The old guard was a set-equality assertion between two hand-written five-entry
maps (`personal_channels_service.OPENCLAW_PERSONAL_CHANNELS` vs
`channel_lane_contract_service.OPENCLAW_PERSONAL_CHANNEL_SPECS`). It could only
ever answer "do my two copies agree with each other" — never "are either of
them right". Both were wrong together: five of OpenClaw's twenty-seven
channels, one carrying an id (`qq`) OpenClaw does not have. And it could not
have caught the worst failure at all, because two EMPTY sets are equal.

Five checks now stand where that one did:

  1. the generated manifest still matches an installed OpenClaw   (drift)
  2. the two generated artifacts (Python JSON / gateway TS) agree (cross-lang)
  3. the derived set is never silently empty or shrunken          (vacuity)
  4. every channel_key is `openclaw_` + their id, verbatim        (invariant)
  5. active and superseded partition the set, and no overlapping
     platform is ever live on both transports                     (overlap)

Only (1) needs an OpenClaw install. The other four run everywhere, including
in the cloud, which is exactly where the manifest has to be trustworthy
without one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server_modules import (
    channel_lane_contract_service,
    openclaw_channel_registry,
    personal_channels_service,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_openclaw_channel_manifest.py"
TYPESCRIPT_MANIFEST = (
    REPO_ROOT / "empyralis-gateway" / "src" / "openclaw" / "generated-openclaw-channels.ts"
)

# The pinned build ships 27. A regeneration that produced materially fewer is a
# broken parse wearing a plausible answer. Raise when upstream genuinely grows;
# never lower it to make a run green.
MINIMUM_EXPECTED_CHANNELS = 20


def _typescript_array(source: str, name: str) -> list[str]:
    """Pull a generated `readonly string[]` literal out of the TS artifact.

    A regex rather than an import because the test process is Python; the
    artifact is generated from a JSON dump, so its shape is machine-stable.
    """
    match = re.search(rf"{name}:\s*readonly string\[\]\s*=\s*(\[[^\]]*\])", source)
    if not match:
        raise AssertionError(f"{name} is missing from {TYPESCRIPT_MANIFEST}.")
    return json.loads(match.group(1))


class OpenClawChannelSetIsDerivedTests(unittest.TestCase):
    def test_the_derived_set_is_never_silently_empty(self) -> None:
        """A broken parse must be a loud failure, never 'no channels available'.

        An empty channel set is indistinguishable from a working one from the
        outside: nothing errors, nothing logs, and every OpenClaw inbound is
        refused as an unknown channel_key. That is weeks of silence.
        """
        self.assertGreaterEqual(len(openclaw_channel_registry.CHANNELS), MINIMUM_EXPECTED_CHANNELS)
        self.assertTrue(channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS)
        self.assertTrue(channel_lane_contract_service.OPENCLAW_PERSONAL_CHANNEL_SPECS)
        self.assertTrue(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS)

        # Every one of those must be reachable through the lane contract; a
        # spec the contract rejects is a channel that can never take a turn.
        for channel_key in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS:
            spec = channel_lane_contract_service.assert_personal_gateway_channel(channel_key)
            self.assertEqual(spec["provider"], channel_lane_contract_service.OPENCLAW_TRANSPORT_PROVIDER)

    def test_a_broken_manifest_refuses_to_load_rather_than_resolving_to_nothing(self) -> None:
        """The vacuity guard lives in the LOADER, not only in this file — a
        test cannot protect a production boot, and 'no channels available' is
        the failure that hides for weeks.

        Each of these is a real way the artifact can break: a truncated write,
        a schema rename, a corrupt file, a missing path.
        """
        original = openclaw_channel_registry.MANIFEST_PATH
        broken_documents = [
            {"schema": openclaw_channel_registry.MANIFEST_SCHEMA, "openclaw_version": "x", "channels": []},
            {"schema": openclaw_channel_registry.MANIFEST_SCHEMA, "openclaw_version": "x", "channels": [{"id": "a", "channel_key": "openclaw_a"}]},
            {"schema": "something.else.v9", "openclaw_version": "x", "channels": []},
        ]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "manifest.json"
                for document in broken_documents:
                    path.write_text(json.dumps(document))
                    openclaw_channel_registry.MANIFEST_PATH = path
                    with self.assertRaises(RuntimeError):
                        openclaw_channel_registry._load_manifest()

                path.write_text("{ not json")
                openclaw_channel_registry.MANIFEST_PATH = path
                with self.assertRaises(RuntimeError):
                    openclaw_channel_registry._load_manifest()

                openclaw_channel_registry.MANIFEST_PATH = Path(tmp) / "does-not-exist.json"
                with self.assertRaises(RuntimeError):
                    openclaw_channel_registry._load_manifest()
        finally:
            openclaw_channel_registry.MANIFEST_PATH = original

    def test_channel_key_is_their_id_verbatim(self) -> None:
        """The `openclaw_qq` bug, made structurally impossible.

        Nobody types a suffix any more: every key is `openclaw_` + an id that
        came out of OpenClaw's own registry. This asserts the construction
        rather than a list of expected suffixes, so it keeps holding for
        channels that do not exist yet.
        """
        for channel in openclaw_channel_registry.CHANNELS:
            self.assertEqual(
                channel.channel_key,
                f"{openclaw_channel_registry.CHANNEL_KEY_PREFIX}{channel.id}",
            )
            self.assertEqual(openclaw_channel_registry.openclaw_channel_id(channel.channel_key), channel.id)
            # Their ids are plugin ids; anything outside this alphabet would be
            # rejected by the gateway's CHANNEL_ID_PATTERN on both legs, so a
            # key we could never route is a generation bug, not a new channel.
            self.assertRegex(channel.id, r"^[a-z0-9][a-z0-9_-]{0,63}$")

    def test_labels_come_from_openclaws_own_display_names(self) -> None:
        """Not a parallel hand-written map. Spot-checked against names only
        OpenClaw's own catalog uses — `QQ Bot`, not the `QQ` the deleted map
        said; `Zalo Personal`, which no Empyralis file ever spelled out."""
        labels = {channel.id: channel.label for channel in openclaw_channel_registry.CHANNELS}
        self.assertEqual(labels["qqbot"], "QQ Bot")
        self.assertEqual(labels["zalouser"], "Zalo Personal")
        self.assertEqual(labels["msteams"], "Microsoft Teams")
        self.assertEqual(labels["openclaw-weixin"], "Weixin")
        # No label may be a silent fallback to the raw id — that is what the
        # generator emits when a channel's metadata could not be read, and it
        # would put "openclaw-zaloclawbot" in front of a customer.
        self.assertEqual([cid for cid, label in labels.items() if label == cid], [])

    def test_adding_a_channel_upstream_needs_no_empyralis_code_change(self) -> None:
        """The whole point of the change, asserted rather than claimed.

        A channel that exists only in the manifest — no Empyralis file mentions
        it — must flow all the way to a usable lane spec, a catalog entry, and
        a handler, purely from the manifest record.
        """
        invented = {
            "id": "notarealchannel",
            "channel_key": "openclaw_notarealchannel",
            "label": "Not A Real Channel",
            "origin": "installable",
            "config_schema_present": True,
            "policy_shape": {
                "dm_policy_modes": ["open"],
                "group_policy_modes": ["allowlist"],
                "channel_require_mention": False,
                "per_chat_map_key": "groups",
                "per_chat_map_keyed_on_chat_id": True,
                "config_writes": False,
                "plugin_hook_flags": [],
                "unhandled_plugin_hook_flags": [],
            },
            # Emitted by the generator for every channel, from the same
            # `openclaw config schema` parse as the policy shape above. Present
            # here because the claim under test is that an upstream channel
            # reaches a usable lane — and "usable" now includes a setup form
            # somebody can actually fill in, which is generated too.
            "credential_shape": {
                "connect_method": "credential",
                "selection_label": "Not A Real Channel (Bot API)",
                "docs_path": "/channels/notarealchannel",
                "fields": [
                    {"name": "botToken", "secret": True, "type": "string", "file_alternative": None}
                ],
                "file_alternatives": [],
            },
        }
        channel = openclaw_channel_registry.OpenClawChannel(invented)
        self.assertEqual(channel.channel_key, "openclaw_notarealchannel")
        self.assertEqual(channel.label, "Not A Real Channel")
        # The form for a channel nobody wrote code for exists, and it is the
        # channel's own — not a default, not empty.
        self.assertEqual(
            [field["name"] for field in channel.credential_shape["fields"]], ["botToken"]
        )

        # It is not a first-party platform, so ownership resolution hands it to
        # the transport with no edit anywhere.
        ownership = openclaw_channel_registry.resolve_transport_ownership(
            channel_lane_contract_service.FIRST_PARTY_PLATFORM_TOKENS
        )
        self.assertNotIn(channel.id, ownership, "the invented channel is not in the real manifest")
        for real in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS:
            self.assertEqual(ownership[real.id], openclaw_channel_registry.OWNER_OPENCLAW)

        # A hand-edited manifest that breaks the invariant is refused outright.
        with self.assertRaises(RuntimeError):
            openclaw_channel_registry.OpenClawChannel({**invented, "channel_key": "openclaw_wrong"})


class OpenClawOverlapResolutionTests(unittest.TestCase):
    def test_active_and_superseded_partition_the_manifest(self) -> None:
        active = {channel.id for channel in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS}
        superseded = {
            channel.id for channel in channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS
        }
        self.assertFalse(active & superseded)
        self.assertEqual(active | superseded, set(openclaw_channel_registry.CHANNELS_BY_ID))

    def test_no_platform_has_two_live_implementations(self) -> None:
        """Declaring all 27 must never put two runtimes on one account.

        Every OpenClaw channel whose platform Empyralis already implements is
        excluded from the LIVE maps — the lane specs, the handler registry, and
        (via the generated active list) the gateway's capability advertisement.
        It stays declared and visible in the platform catalog so the UI can say
        why, which is the honest version of "not available", never a hole.
        """
        superseded = channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS
        self.assertTrue(superseded, "the overlap set must not be empty while we still own these")

        catalog_by_key = {
            entry["channel_key"]: entry
            for entry in channel_lane_contract_service.platform_channel_catalog()
        }
        for channel in superseded:
            self.assertNotIn(channel.channel_key, channel_lane_contract_service.PERSONAL_CHANNEL_SPECS)
            self.assertNotIn(channel.channel_key, personal_channels_service.OPENCLAW_PERSONAL_CHANNELS)
            self.assertNotIn(channel.channel_key, personal_channels_service.LOCAL_BRIDGE_PERSONAL_CHANNELS)
            with self.assertRaises(ValueError):
                channel_lane_contract_service.assert_personal_gateway_channel(channel.channel_key)

            # Declared and explained, not hidden.
            entry = catalog_by_key[channel.channel_key]
            self.assertEqual(entry["status"], "superseded_by_first_party")
            self.assertFalse(entry["live_capable"])
            owner = entry["superseded_by"]
            self.assertTrue(owner, f"{channel.id} is superseded by nothing — that is a derivation bug")
            self.assertIn(
                owner,
                set(channel_lane_contract_service.PERSONAL_CHANNEL_SPECS)
                | {item["channel_key"] for item in channel_lane_contract_service.STUDIO_CHANNEL_ROADMAP},
            )

    def test_the_eight_platforms_we_overlap_resolve_correctly_post_cutover(self) -> None:
        """2026-08-14 full OpenClaw channel cutover. Of the 8 platforms that
        overlap an existing Empyralis implementation, 5 are now OpenClaw-owned
        (their first-party runtime is deleted in the same change) and 3 stay
        first-party because they are Studio business-connector channels with
        no Agent Computer requirement today — see
        openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS's own comment
        for why forcing discord/slack/sms onto a hardware-bound transport
        would be a regression, not an improvement."""
        active = {channel.id for channel in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS}
        superseded = {
            channel.id for channel in channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS
        }
        self.assertEqual(
            active & {"telegram", "whatsapp", "signal", "imessage", "openclaw-weixin", "discord", "slack", "sms"},
            {"telegram", "whatsapp", "signal", "imessage", "openclaw-weixin"},
        )
        self.assertEqual(superseded, {"discord", "slack", "sms"})
        # WeChat Work is NOT the same product as consumer WeChat and is not
        # superseded by anything of ours.
        self.assertIn("wecom", active)

    def test_cut_over_list_names_exactly_the_five_platforms_retired_2026_08_14(self) -> None:
        """CHANNEL-ADOPTION-PLAN.md step 6: port -> verify -> swap -> delete.

        Moving an id into OPENCLAW_CUT_OVER_CHANNEL_IDS is the swap, and the
        first-party implementation for every id in it must be gone in the
        same change — asserted here structurally (no first-party owner left)
        rather than merely by the commit message.
        """
        self.assertEqual(
            openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS,
            frozenset({"whatsapp", "signal", "imessage", "openclaw-weixin", "telegram"}),
        )
        for channel_id in openclaw_channel_registry.OPENCLAW_CUT_OVER_CHANNEL_IDS:
            owner = channel_lane_contract_service._OPENCLAW_FIRST_PARTY_OWNER_BY_ID.get(channel_id)
            self.assertIsNone(
                owner,
                f"{channel_id} was cut over to OpenClaw while {owner} still exists — "
                "two live implementations of one platform.",
            )


class GeneratedArtifactsAgreeTests(unittest.TestCase):
    """The cross-language duplicate is real; the drift assertion is what makes
    it acceptable (CLAUDE.md). Both files are generated in one pass from one
    parse, so this can only fail if somebody hand-edited a generated file or
    regenerated one without the other."""

    def setUp(self) -> None:
        self.source = TYPESCRIPT_MANIFEST.read_text()

    def test_gateway_manifest_matches_the_python_manifest(self) -> None:
        embedded = re.search(
            r"GENERATED_OPENCLAW_MANIFEST: GeneratedOpenClawManifest = (\{.*?\n\}) as const;",
            self.source,
            re.DOTALL,
        )
        self.assertIsNotNone(embedded, "the gateway manifest literal is missing")
        gateway_manifest = json.loads(embedded.group(1))  # type: ignore[union-attr]
        python_manifest = json.loads(openclaw_channel_registry.MANIFEST_PATH.read_text())
        self.assertEqual(gateway_manifest, python_manifest)

    def test_gateway_active_set_matches_pythons_ownership_resolution(self) -> None:
        active = _typescript_array(self.source, "GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS")
        superseded = _typescript_array(self.source, "GENERATED_OPENCLAW_SUPERSEDED_CHANNEL_IDS")
        self.assertTrue(active)
        self.assertEqual(
            set(active),
            {channel.id for channel in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS},
        )
        self.assertEqual(
            set(superseded),
            {channel.id for channel in channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS},
        )
        self.assertFalse(set(active) & set(superseded))


class ManifestMatchesTheInstalledOpenClawTests(unittest.TestCase):
    """The only check that needs an OpenClaw install, and the reason the
    checked-in manifest can be trusted in a cloud that has none.

    Skipped without the CLI rather than silently passing — but the skip
    REASON says so, because a skip nobody reads is how a pinned artifact goes
    stale (the MAN-306 shape: a dependency that is not source, cannot be
    grepped for callers, and degrades in silence).
    """

    def test_checked_in_manifest_has_not_drifted_from_the_installed_cli(self) -> None:
        if not shutil.which("openclaw"):
            self.skipTest(
                "openclaw is not installed on this machine, so the pinned channel manifest "
                "cannot be re-derived here. Run "
                "`python3 scripts/generate_openclaw_channel_manifest.py --check` on a machine "
                "with the pinned OpenClaw before trusting the checked-in manifest."
            )
        env = dict(os.environ)
        env.setdefault("DATABASE_URL", "")
        with tempfile.TemporaryDirectory():
            completed = subprocess.run(
                [sys.executable, str(GENERATOR), "--check"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            "The checked-in OpenClaw channel manifest no longer matches the installed CLI.\n"
            f"{completed.stdout}\n{completed.stderr}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
