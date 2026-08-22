"""agent_capability_service — per-agent media-capability resolution.

Covers the resolver's contract (mirrors agent_turn_runtime_service.
_resolve_agent_cloud_provider one level down — capability instead of "the"
chat model):
  1. Default (no capability_config at all) resolves platform_credits and is
     exact-behavior-preserving with the pre-capability-system code (working
     whenever the platform env key is present, unavailable otherwise).
  2. byok_api with no stored secret is unavailable, never silently falls
     back to platform_credits (that would bill the workspace for something
     the owner thought was on their own key).
  3. byok_api with a real encrypted secret decrypts correctly and returns
     the exact key that was stored.
  4. Per-agent isolation: agent A's BYOK secret is never visible when
     resolving for agent B, even in the same workspace/provider.
  5. Stubbed capabilities (text_to_speech, video_generation) never resolve
     to available=True regardless of configuration — "register the
     modality, stub the adapter."
  6. platform_credits is gated by entitlements_service's existing hosted-AI
     cap check, and fails CLOSED (never available) if that check errors.
  7. Validation helpers (validate_capability_config_patch,
     store_capability_secret_patch) reject bad input without touching
     anything.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_capability_service as caps
from server_modules import vault_store


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# store_capability_secret_patch/_decrypt_capability_secret go through the
# REAL vault_store encryption (Fernet, PBKDF2-derived key — see
# vault_store._vault_encrypt_with_passphrase) so these tests exercise actual
# crypto, not a mock of it. The one thing patched out is _vault_passphrase's
# FIRST-EVER-KEY-GENERATION path, which — only on a machine with no existing
# vault key file (this test suite's isolated tmp_path state, per conftest.py's
# _isolate_empyralis_state) — routes through the compiled Rust runtime
# kernel's governance gate; that binary isn't built in this environment (see
# server_modules/tests/test_vault_store.py's own
# test_vault_passphrase_generates_local_key_when_env_is_local, which fails
# identically and unconditionally here — a pre-existing environment gap, not
# something this test file works around specially). Supplying an
# already-resolved passphrase skips key GENERATION entirely while leaving
# encrypt/decrypt themselves completely real.
_TEST_VAULT_PASSPHRASE = "test-fixed-passphrase-for-agent-capability-service-tests"


def setUpModule():
    _patcher = patch.object(vault_store, "_vault_passphrase", return_value=_TEST_VAULT_PASSPHRASE)
    _patcher.start()
    global _vault_passphrase_patcher
    _vault_passphrase_patcher = _patcher


def tearDownModule():
    _vault_passphrase_patcher.stop()


class DefaultResolutionTests(unittest.TestCase):
    """No capability_config at all == every agent before this system existed
    — must resolve exactly the way the old unconditional-platform-key code
    did: available iff the platform env var happens to be set."""

    def test_no_config_no_env_key_is_unavailable(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("OPENAI_API_KEY", None)
            r = caps.resolve_agent_capability_provider(
                workspace_id="ws-1", agent_id="a-1", capability="image_generation",
            )
        self.assertFalse(r.available)
        self.assertEqual(r.mode, "platform_credits")
        self.assertEqual(r.provider, "openai")
        self.assertEqual(r.reason, "platform_provider_not_configured")
        self.assertEqual(r.billing_mode, "")

    def test_no_config_with_env_key_and_entitlements_allowed_resolves(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-platform"}, clear=False), patch(
            "server_modules.entitlements_service.hosted_sage_ai_access_state_for_workspace_id",
            return_value={"allowed": True},
        ):
            r = caps.resolve_agent_capability_provider(
                workspace_id="ws-1", agent_id="a-1", capability="image_generation",
            )
        self.assertTrue(r.available)
        self.assertEqual(r.mode, "platform_credits")
        self.assertEqual(r.billing_mode, "platform_credits")
        self.assertEqual(r.credentials.get("api_key"), "sk-platform")

    def test_platform_credits_blocked_by_entitlements_cap_is_unavailable(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-platform"}, clear=False), patch(
            "server_modules.entitlements_service.hosted_sage_ai_access_state_for_workspace_id",
            return_value={"allowed": False, "reason": "cap_reached", "message": "Monthly cap reached."},
        ):
            r = caps.resolve_agent_capability_provider(
                workspace_id="ws-1", agent_id="a-1", capability="image_generation",
            )
        self.assertFalse(r.available)
        self.assertEqual(r.reason, "cap_reached")
        self.assertEqual(r.message, "Monthly cap reached.")

    def test_entitlements_lookup_failure_fails_closed(self):
        """A metering-check crash must never silently grant unmetered
        platform usage — this is the write-side counterpart of
        meter_platform_capability_usage's own best-effort contract."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-platform"}, clear=False), patch(
            "server_modules.entitlements_service.hosted_sage_ai_access_state_for_workspace_id",
            side_effect=RuntimeError("db down"),
        ):
            r = caps.resolve_agent_capability_provider(
                workspace_id="ws-1", agent_id="a-1", capability="image_generation",
            )
        self.assertFalse(r.available)
        self.assertEqual(r.reason, "entitlements_check_failed")


class ByokResolutionTests(unittest.TestCase):
    def test_byok_with_no_secret_is_unavailable_never_falls_back_to_platform(self):
        cfg = {"image_generation": {"mode": "byok_api", "provider": "openai"}}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-platform-should-not-be-used"}, clear=False):
            r = caps.resolve_agent_capability_provider(
                workspace_id="ws-1", agent_id="a-1", capability="image_generation",
                capability_config=cfg, capability_secrets={},
            )
        self.assertFalse(r.available)
        self.assertEqual(r.reason, "byok_key_missing")
        self.assertEqual(r.billing_mode, "")

    def test_byok_with_real_secret_decrypts_to_the_exact_stored_key(self):
        secret_patch = caps.store_capability_secret_patch(
            capability="image_generation", provider="openai", api_key="sk-agent-own-key",
        )
        cfg = {"image_generation": {"mode": "byok_api", "provider": "openai"}}
        secrets = {"image_generation": secret_patch}
        r = caps.resolve_agent_capability_provider(
            workspace_id="ws-1", agent_id="a-1", capability="image_generation",
            capability_config=cfg, capability_secrets=secrets,
        )
        self.assertTrue(r.available)
        self.assertEqual(r.mode, "byok_api")
        self.assertEqual(r.billing_mode, "byok_api")
        self.assertEqual(r.credentials.get("api_key"), "sk-agent-own-key")

    def test_secret_never_stores_plaintext(self):
        secret_patch = caps.store_capability_secret_patch(
            capability="image_generation", provider="openai", api_key="sk-super-secret-value",
        )
        self.assertNotIn("sk-super-secret-value", secret_patch["ciphertext"])
        self.assertNotIn("api_key", secret_patch)

    def test_secret_for_a_different_provider_than_configured_is_ignored(self):
        """A secret recorded under a stale/different provider than the one
        currently configured must not satisfy this capability's byok_api
        request — provider must match exactly. (Stability's own BYOK path is
        disabled per the platform's OpenAI/Anthropic-only key-paste rule —
        see CAPABILITY_PROVIDER_CATALOG — so this simulates a stale/mismatched
        secret record directly instead of going through
        store_capability_secret_patch, which now refuses to store a
        non-OpenAI secret for this capability at all.)"""
        secret_patch = dict(caps.store_capability_secret_patch(
            capability="image_generation", provider="openai", api_key="sk-openai-key",
        ))
        secret_patch["provider"] = "stability"  # simulate a stale/mismatched record
        cfg = {"image_generation": {"mode": "byok_api", "provider": "openai"}}
        secrets = {"image_generation": secret_patch}
        r = caps.resolve_agent_capability_provider(
            workspace_id="ws-1", agent_id="a-1", capability="image_generation",
            capability_config=cfg, capability_secrets=secrets,
        )
        self.assertFalse(r.available)
        self.assertEqual(r.reason, "byok_key_missing")

    def test_corrupt_ciphertext_is_unavailable_not_a_crash(self):
        cfg = {"image_generation": {"mode": "byok_api", "provider": "openai"}}
        secrets = {"image_generation": {"provider": "openai", "ciphertext": "not-valid-ciphertext"}}
        r = caps.resolve_agent_capability_provider(
            workspace_id="ws-1", agent_id="a-1", capability="image_generation",
            capability_config=cfg, capability_secrets=secrets,
        )
        self.assertFalse(r.available)
        self.assertEqual(r.reason, "byok_key_unreadable")


class PerAgentIsolationTests(unittest.TestCase):
    """The core promise: a capability enabled on agent A must not leak to
    agent B — proven structurally (agent B's resolution never even sees
    agent A's secrets dict) rather than by a runtime lookup that could have
    a filtering bug."""

    def test_agent_b_never_sees_agent_a_byok_secret(self):
        agent_a_secrets = {
            "image_generation": caps.store_capability_secret_patch(
                capability="image_generation", provider="openai", api_key="sk-AGENT-A-ONLY",
            )
        }
        agent_a_config = {"image_generation": {"mode": "byok_api", "provider": "openai"}}

        # Agent A resolves to its own key.
        r_a = caps.resolve_agent_capability_provider(
            workspace_id="ws-1", agent_id="agent-a", capability="image_generation",
            capability_config=agent_a_config, capability_secrets=agent_a_secrets,
        )
        self.assertTrue(r_a.available)
        self.assertEqual(r_a.credentials.get("api_key"), "sk-AGENT-A-ONLY")

        # Agent B, same workspace, same provider, its OWN (empty) config/secrets
        # — must never resolve agent A's key. Falls to the platform default
        # instead (unavailable here since no env key is set), never byok_api.
        import os
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            r_b = caps.resolve_agent_capability_provider(
                workspace_id="ws-1", agent_id="agent-b", capability="image_generation",
                capability_config={}, capability_secrets={},
            )
        self.assertNotEqual(r_b.credentials.get("api_key"), "sk-AGENT-A-ONLY")
        self.assertEqual(r_b.mode, "platform_credits")  # never inherited agent A's byok_api mode
        self.assertFalse(r_b.available)  # no platform key configured in this test

    def test_resolved_capability_ids_only_scans_the_requested_set(self):
        cfg = {
            "image_generation": {"mode": "byok_api", "provider": "openai"},
            "video_generation": {"mode": "byok_api", "provider": "runway"},
        }
        secrets = {
            "image_generation": caps.store_capability_secret_patch(capability="image_generation", provider="openai", api_key="sk-1"),
            # Runway no longer supports byok at all (see CAPABILITY_PROVIDER_CATALOG
            # — it's neither OpenAI/Anthropic nor OAuth-capable), so
            # store_capability_secret_patch now refuses to create this
            # secret. Hand-craft the record directly to simulate one already
            # sitting in storage (e.g. from before that rule existed) — the
            # point of this test is that live=False alone is enough to keep
            # it unresolved regardless of what's stored.
            "video_generation": {
                "provider": "runway",
                "ciphertext": caps._encrypt_capability_secret({"api_key": "sk-2"}),
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        }
        resolved = caps.resolved_capability_ids(
            workspace_id="ws-1", agent_id="a-1", capability_config=cfg, capability_secrets=secrets,
            only=caps.TOOL_GATED_CAPABILITIES,
        )
        # image_generation is live and byok-configured -> resolved.
        self.assertIn("image_generation", resolved)
        # video_generation is stubbed (live=False) -> never resolved even with a key.
        self.assertNotIn("video_generation", resolved)


class StubbedCapabilityTests(unittest.TestCase):
    """TTS and video generation are registered (visible in the catalog) but
    their adapters are deliberately stubbed for this pass — resolution must
    report unavailable/provider_not_live no matter how they're configured."""

    def test_text_to_speech_never_resolves_even_with_valid_byok_key(self):
        secret_patch = caps.store_capability_secret_patch(capability="text_to_speech", provider="openai", api_key="sk-tts")
        cfg = {"text_to_speech": {"mode": "byok_api", "provider": "openai"}}
        r = caps.resolve_agent_capability_provider(
            workspace_id="ws-1", agent_id="a-1", capability="text_to_speech",
            capability_config=cfg, capability_secrets={"text_to_speech": secret_patch},
        )
        self.assertFalse(r.available)
        self.assertEqual(r.reason, "provider_not_live")

    def test_video_generation_never_resolves_on_platform_credits_either(self):
        r = caps.resolve_agent_capability_provider(
            workspace_id="ws-1", agent_id="a-1", capability="video_generation",
        )
        self.assertFalse(r.available)
        # video_generation's only provider (runway) doesn't support
        # platform_credits at all AND is stubbed — either reason is a valid
        # "not available", but it must never be True.
        self.assertIn(r.reason, {"provider_not_live", "platform_credits_not_supported"})

    def test_catalog_still_lists_stubbed_capabilities(self):
        """'register the modalities' — the catalog and UI must still show
        video_generation/text_to_speech even though they can't resolve."""
        catalog = caps.capability_catalog_payload()
        ids = {c["id"] for c in catalog}
        self.assertEqual(ids, set(caps.ALL_CAPABILITIES))
        tts = next(c for c in catalog if c["id"] == "text_to_speech")
        self.assertTrue(all(p["live"] is False for p in tts["providers"]))


class ValidationTests(unittest.TestCase):
    def test_validate_capability_config_patch_rejects_unknown_capability(self):
        with self.assertRaises(ValueError):
            caps.validate_capability_config_patch({"not_a_real_capability": {"mode": "byok_api"}})

    def test_validate_capability_config_patch_rejects_bad_mode(self):
        with self.assertRaises(ValueError):
            caps.validate_capability_config_patch({"image_generation": {"mode": "cli_subscription"}})

    def test_validate_capability_config_patch_defaults_mode_and_provider(self):
        cleaned = caps.validate_capability_config_patch({"image_generation": {}})
        self.assertEqual(cleaned["image_generation"], {"mode": "platform_credits", "provider": "openai"})

    def test_validate_capability_config_patch_rejects_provider_not_in_catalog(self):
        with self.assertRaises(ValueError):
            caps.validate_capability_config_patch({"image_generation": {"provider": "midjourney"}})

    def test_store_capability_secret_patch_rejects_empty_key(self):
        with self.assertRaises(ValueError):
            caps.store_capability_secret_patch(capability="image_generation", provider="openai", api_key="   ")

    def test_store_capability_secret_patch_rejects_unknown_provider_for_capability(self):
        with self.assertRaises(ValueError):
            caps.store_capability_secret_patch(capability="image_generation", provider="elevenlabs", api_key="sk-x")

    # ── Founder's hard rule: BYOK is OpenAI/Anthropic-only ─────────────────
    # No raw key-paste for Stability, ElevenLabs, or Runway — enforced here
    # (store_capability_secret_patch / validate_capability_config_patch), not
    # just hidden in the UI. See module docstring's "BYOK IS
    # OPENAI/ANTHROPIC-ONLY" note for the researched provider list this
    # encodes.

    def test_store_capability_secret_patch_rejects_stability_byok(self):
        with self.assertRaises(ValueError):
            caps.store_capability_secret_patch(capability="image_generation", provider="stability", api_key="sk-x")

    def test_store_capability_secret_patch_rejects_elevenlabs_byok(self):
        with self.assertRaises(ValueError):
            caps.store_capability_secret_patch(capability="text_to_speech", provider="elevenlabs", api_key="sk-x")

    def test_store_capability_secret_patch_rejects_runway_byok(self):
        with self.assertRaises(ValueError):
            caps.store_capability_secret_patch(capability="video_generation", provider="runway", api_key="sk-x")

    def test_store_capability_secret_patch_still_accepts_openai_everywhere_it_appears(self):
        """The one allowed BYOK provider must keep working for every
        capability it's registered under (image_generation, speech_to_text,
        text_to_speech) — this rule subtracts non-OpenAI providers, it must
        never accidentally subtract OpenAI too."""
        for cap in ("image_generation", "speech_to_text", "text_to_speech"):
            patch = caps.store_capability_secret_patch(capability=cap, provider="openai", api_key="sk-openai")
            self.assertEqual(patch["provider"], "openai")

    def test_validate_capability_config_patch_rejects_byok_mode_for_stability(self):
        with self.assertRaises(ValueError):
            caps.validate_capability_config_patch({"image_generation": {"mode": "byok_api", "provider": "stability"}})

    def test_validate_capability_config_patch_rejects_byok_mode_for_elevenlabs(self):
        with self.assertRaises(ValueError):
            caps.validate_capability_config_patch({"text_to_speech": {"mode": "byok_api", "provider": "elevenlabs"}})

    def test_validate_capability_config_patch_rejects_byok_mode_for_runway(self):
        with self.assertRaises(ValueError):
            caps.validate_capability_config_patch({"video_generation": {"mode": "byok_api", "provider": "runway"}})

    def test_validate_capability_config_patch_still_allows_platform_credits_for_stability(self):
        """Removing Stability's BYOK path must not remove Stability itself —
        it's still a legitimate platform-credits provider."""
        cleaned = caps.validate_capability_config_patch({"image_generation": {"mode": "platform_credits", "provider": "stability"}})
        self.assertEqual(cleaned["image_generation"], {"mode": "platform_credits", "provider": "stability"})

    def test_validate_capability_config_patch_still_allows_byok_mode_for_openai(self):
        cleaned = caps.validate_capability_config_patch({"image_generation": {"mode": "byok_api", "provider": "openai"}})
        self.assertEqual(cleaned["image_generation"], {"mode": "byok_api", "provider": "openai"})


class ProviderCatalogComplianceTests(unittest.TestCase):
    """The Capabilities tab reads supports_byok straight off the catalog to
    decide whether to render a key-paste box at all — these pin the catalog
    itself to the founder's rule so a future edit can't silently reintroduce
    a non-OpenAI paste box without a test noticing."""

    def test_only_openai_supports_byok_anywhere_in_the_catalog(self):
        for cap, options in caps.CAPABILITY_PROVIDER_CATALOG.items():
            for opt in options:
                if opt.supports_byok:
                    self.assertEqual(
                        opt.id, "openai",
                        f"{cap}/{opt.id} supports_byok=True but isn't OpenAI — violates the no-key-hunting rule.",
                    )

    def test_catalog_payload_prices_only_live_platform_credit_options(self):
        catalog = caps.capability_catalog_payload()
        image_gen = next(c for c in catalog if c["id"] == "image_generation")
        openai_opt = next(p for p in image_gen["providers"] if p["id"] == "openai")
        stability_opt = next(p for p in image_gen["providers"] if p["id"] == "stability")
        self.assertIsNotNone(openai_opt["platform_price_usd"])
        self.assertEqual(openai_opt["platform_price_unit"], "image")
        self.assertFalse(stability_opt["supports_byok"])
        self.assertIsNotNone(stability_opt["platform_price_usd"])  # still priced — platform-credits still works

        tts = next(c for c in catalog if c["id"] == "text_to_speech")
        for opt in tts["providers"]:
            # Neither TTS provider is live yet — no price to show for either.
            self.assertIsNone(opt["platform_price_usd"])
            self.assertIsNone(opt["platform_price_unit"])


class AgentCapabilityStatePayloadTests(unittest.TestCase):
    """The Capabilities tab's GET response — must never leak key material."""

    def test_payload_never_includes_ciphertext_or_plaintext(self):
        secret_patch = caps.store_capability_secret_patch(capability="image_generation", provider="openai", api_key="sk-should-never-appear")
        cfg = {"image_generation": {"mode": "byok_api", "provider": "openai"}}
        payload = caps.agent_capability_state_payload(
            workspace_id="ws-1", agent_id="a-1", capability_config=cfg,
            capability_secrets={"image_generation": secret_patch},
        )
        serialized = str(payload)
        self.assertNotIn("sk-should-never-appear", serialized)
        self.assertNotIn(secret_patch["ciphertext"], serialized)
        image_gen = next(c for c in payload if c["id"] == "image_generation")
        self.assertTrue(image_gen["has_byok_key"])
        self.assertTrue(image_gen["available"])

    def test_payload_covers_all_four_capabilities_even_with_no_config(self):
        payload = caps.agent_capability_state_payload(
            workspace_id="ws-1", agent_id="a-1", capability_config={}, capability_secrets={},
        )
        self.assertEqual({c["id"] for c in payload}, set(caps.ALL_CAPABILITIES))


class ResolveByIdTests(unittest.IsolatedAsyncioTestCase):
    """The async convenience wrapper real callers (execute_single_direct_tool_call,
    _resolve_specialist_toolset, personal_channel_transcription_service) use."""

    async def test_resolves_from_a_fetched_specialist_bundle(self):
        bundle = {
            "id": "agent-x",
            "install_metadata": {
                "capability_config": {"image_generation": {"mode": "byok_api", "provider": "openai"}},
                "capability_secrets": {
                    "image_generation": caps.store_capability_secret_patch(
                        capability="image_generation", provider="openai", api_key="sk-from-bundle",
                    )
                },
            },
        }
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=bundle),
        ):
            r = await caps.resolve_agent_capability_provider_by_id(
                workspace_id="ws-1", tenant_id="t1", agent_id="agent-x", capability="image_generation",
            )
        self.assertTrue(r.available)
        self.assertEqual(r.credentials.get("api_key"), "sk-from-bundle")

    async def test_empty_agent_id_resolves_against_the_master_install(self):
        master_bundle = {"id": "sage-install", "install_metadata": {}}
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            new=AsyncMock(return_value=master_bundle),
        ) as mock_master, patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("OPENAI_API_KEY", None)
            r = await caps.resolve_agent_capability_provider_by_id(
                workspace_id="ws-1", tenant_id="t1", agent_id="", capability="image_generation",
            )
        mock_master.assert_awaited_once()
        self.assertFalse(r.available)  # no config, no env key
        self.assertEqual(r.reason, "platform_provider_not_configured")

    async def test_agent_lookup_failure_fails_closed_not_a_crash(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            r = await caps.resolve_agent_capability_provider_by_id(
                workspace_id="ws-1", tenant_id="t1", agent_id="agent-x", capability="image_generation",
            )
        self.assertFalse(r.available)
        self.assertEqual(r.reason, "agent_lookup_failed")

    async def test_agent_not_found_fails_closed(self):
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=None),
        ):
            r = await caps.resolve_agent_capability_provider_by_id(
                workspace_id="ws-1", tenant_id="t1", agent_id="agent-x", capability="image_generation",
            )
        self.assertFalse(r.available)
        self.assertEqual(r.reason, "agent_not_found")


class MeterPlatformCapabilityUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_a_ledger_entry_against_the_hosted_ai_cost_ledger(self):
        """The SAME table entitlements_service's cap check reads from — so
        repeated capability calls actually count against the workspace's
        cap, not just usage_events (a separate, not-yet-cap-wired table)."""
        with patch(
            "server_modules.control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry",
            new=AsyncMock(return_value={"id": "ledger-1"}),
        ) as mock_record:
            await caps.meter_platform_capability_usage(
                tenant_id="t1", workspace_id="ws-1", agent_id="agent-x",
                capability="image_generation", provider="openai",
            )
        mock_record.assert_awaited_once()
        _, kwargs = mock_record.call_args
        self.assertEqual(kwargs["source_surface"], "capability:image_generation")
        self.assertEqual(kwargs["provider"], "openai")
        self.assertGreater(kwargs["estimated_cost_usd"], 0)
        self.assertEqual(kwargs["metadata"].get("agent_id"), "agent-x")

    async def test_metering_failure_never_raises(self):
        with patch(
            "server_modules.control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry",
            new=AsyncMock(side_effect=RuntimeError("ledger down")),
        ):
            # Must not raise — a metering failure must never undo or block a
            # capability call that already succeeded.
            await caps.meter_platform_capability_usage(
                tenant_id="t1", workspace_id="ws-1", agent_id="agent-x",
                capability="image_generation", provider="openai",
            )


if __name__ == "__main__":
    unittest.main()
