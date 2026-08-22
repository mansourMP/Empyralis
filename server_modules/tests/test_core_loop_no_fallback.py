"""
Core-loop reliability tests: no-fallback, credit grant, hard-stop.

Tests the "ONE AI ROAD. NO FALLBACK. EVER." rule and the 10,000-credit
signup grant.  These do NOT require the Rust kernel or a live database —
they test the pure decision logic.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules.agent_turn_runtime_service import (
    SAGE_AI_LIMIT_MESSAGE,
    SAGE_AI_NEEDS_ATTENTION_MESSAGE,
    _SAGE_AI_SETUP_PATH,
    _resolve_agent_cloud_provider,
    _resolve_cloud_provider,
)


class NoFallbackProviderResolutionTests(unittest.TestCase):
    """Verify _resolve_cloud_provider enforces ONE AI ROAD."""

    def setUp(self):
        self._original_deepseek_key = os.environ.get("DEEPSEEK_API_KEY")

    def tearDown(self):
        if self._original_deepseek_key:
            os.environ["DEEPSEEK_API_KEY"] = self._original_deepseek_key
        elif "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]

    @staticmethod
    def _patch_workspace(metadata=None, sage_ai_provider=""):
        """Context manager that patches workspace loading + admin defaults."""
        ws_meta = metadata or {}
        admin_mock = MagicMock(sage_ai_provider=sage_ai_provider)
        ws_patch = patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            AsyncMock(return_value={"metadata": ws_meta}),
        )
        admin_patch = patch(
            "server_modules.workspace_config_schema.workspace_admin_defaults_from_metadata",
            return_value=admin_mock,
        )
        return ws_patch, admin_patch

    def test_explicit_provider_locked_no_fallback_to_byok(self):
        """When a user explicitly selected a provider, it's LOCKED."""
        selected_provider = "deepseek"
        workspace_id = "ws_no_fallback_test_1"
        byok_creds = {"api_key": "sk-ant-byok-key", "auth_mode": "api_key"}

        ws_p, adm_p = self._patch_workspace(sage_ai_provider=selected_provider)
        creds_p = patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials"
        )
        supp_p = patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat"
        )

        with ws_p, adm_p, creds_p as mock_creds, supp_p as mock_supports:
            def creds_side_effect(ws, provider):
                if provider == selected_provider:
                    return {}
                return byok_creds
            mock_creds.side_effect = creds_side_effect

            def supports_side_effect(provider, creds):
                return provider != selected_provider and bool(creds.get("api_key"))
            mock_supports.side_effect = supports_side_effect

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(_resolve_cloud_provider(workspace_id))

            msg = str(ctx.exception).lower()
            self.assertIn(selected_provider, msg)
            self.assertIn("not available", msg)
            self.assertNotIn("switched", msg)

    def test_explicit_provider_works_when_available(self):
        """Happy path: explicit provider has valid credentials."""
        selected_provider = "anthropic"
        workspace_id = "ws_no_fallback_test_2"
        valid_creds = {"api_key": "sk-ant-valid", "auth_mode": "api_key"}

        ws_p, adm_p = self._patch_workspace(sage_ai_provider=selected_provider)
        creds_p = patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials",
            return_value=valid_creds,
        )
        supp_p = patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat",
            return_value=True,
        )

        with ws_p, adm_p, creds_p, supp_p:
            provider, credentials = asyncio.run(
                _resolve_cloud_provider(workspace_id)
            )
            self.assertEqual(provider, selected_provider)
            self.assertEqual(credentials, valid_creds)

    def test_no_explicit_provider_defaults_to_platform_not_vault(self):
        """Without an explicit selection, the PLATFORM provider (DeepSeek,
        credit-gated) is the default.  Vault keys are NOT scanned."""
        workspace_id = "ws_no_fallback_test_3"
        os.environ["DEEPSEEK_API_KEY"] = "sk-platform-deepseek-key"

        ws_p, adm_p = self._patch_workspace(sage_ai_provider="")
        creds_p = patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials",
            return_value={"api_key": "sk-platform-deepseek-key"},
        )
        supp_p = patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat",
            return_value=True,
        )
        ent_p = patch(
            "server_modules.entitlements_service.hosted_sage_ai_access_state_for_workspace_id",
            return_value={"allowed": True},
        )

        with ws_p, adm_p, creds_p, supp_p, ent_p:
            provider, _ = asyncio.run(_resolve_cloud_provider(workspace_id))
            # Must be platform DeepSeek, NOT a vault key
            self.assertEqual(provider, "deepseek")

    def test_vault_key_ignored_when_not_explicitly_selected(self):
        """Vault key present + platform credits exhausted + no explicit
        selection → hard stop.  The vault key MUST NOT be used as a
        silent fallback."""
        workspace_id = "ws_no_fallback_test_vault_ignored"
        os.environ["DEEPSEEK_API_KEY"] = "sk-platform-deepseek-key"

        ws_p, adm_p = self._patch_workspace(sage_ai_provider="")
        # direct_chat_credentials returns valid BYOK creds for anthropic
        # but the resolver should NEVER call it for anthropic — it goes
        # straight to platform, which is exhausted.
        creds_p = patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials",
            return_value={},
        )
        supp_p = patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat",
            return_value=False,
        )
        ent_p = patch(
            "server_modules.entitlements_service.hosted_sage_ai_access_state_for_workspace_id",
            return_value={
                "allowed": False,
                "message": "You've reached your AI limit. Open AI & Setup →",
                "reason": "cap_reached",
            },
        )

        with ws_p, adm_p, creds_p, supp_p, ent_p:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(_resolve_cloud_provider(workspace_id))
            msg = str(ctx.exception).lower()
            self.assertIn("reached your ai limit", msg)
            # Must NOT mention any BYOK provider
            self.assertNotIn("anthropic", msg)
            self.assertNotIn("openai", msg)

    def test_explicit_byok_fails_no_platform_fallback(self):
        """Explicitly selected BYOK provider fails → clean hard stop.
        Must NOT fall through to platform or any other provider."""
        selected_provider = "openai"
        workspace_id = "ws_no_fallback_test_byok_fail"

        ws_p, adm_p = self._patch_workspace(sage_ai_provider=selected_provider)
        creds_p = patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials"
        )
        supp_p = patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat"
        )

        with ws_p, adm_p, creds_p as mock_creds, supp_p as mock_supports:
            def creds_side_effect(ws, provider):
                if provider == selected_provider:
                    return {}  # selected provider has no valid creds
                # Other providers have creds — but MUST NOT be used
                return {"api_key": "sk-other-valid-key", "auth_mode": "api_key"}

            mock_creds.side_effect = creds_side_effect

            def supports_side_effect(provider, creds):
                # Only non-selected providers pass validation
                return provider != selected_provider and bool(creds.get("api_key"))

            mock_supports.side_effect = supports_side_effect

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(_resolve_cloud_provider(workspace_id))

            msg = str(ctx.exception).lower()
            self.assertIn(selected_provider, msg)
            self.assertIn("not available", msg)
            # Must NOT mention platform or other providers
            self.assertNotIn("deepseek", msg)
            self.assertNotIn("switched", msg)

    def test_platform_deepseek_configured_but_credits_exhausted(self):
        """When DEEPSEEK_API_KEY is set but credits are exhausted,
        raises credit exhaustion error, NOT fall through."""
        workspace_id = "ws_no_fallback_test_4"
        os.environ["DEEPSEEK_API_KEY"] = "sk-platform-deepseek-key"

        ws_p, adm_p = self._patch_workspace(sage_ai_provider="")
        creds_p = patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials",
            return_value={},
        )
        supp_p = patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat",
            return_value=False,
        )
        ent_p = patch(
            "server_modules.entitlements_service.hosted_sage_ai_access_state_for_workspace_id",
            return_value={
                "allowed": False,
                "message": "You've reached your AI limit. Open AI & Setup →",
                "reason": "cap_reached",
            },
        )

        with ws_p, adm_p, creds_p, supp_p, ent_p:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(_resolve_cloud_provider(workspace_id))

            msg = str(ctx.exception).lower()
            self.assertIn("reached your ai limit", msg)
            self.assertIn("ai & setup", msg)

    def test_bare_metal_no_provider_configured(self):
        """When nothing is configured, the error is clear."""
        workspace_id = "ws_no_fallback_test_5"

        if "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]

        ws_p, adm_p = self._patch_workspace(sage_ai_provider="")
        creds_p = patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials",
            return_value={},
        )
        supp_p = patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat",
            return_value=False,
        )

        with ws_p, adm_p, creds_p, supp_p:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(_resolve_cloud_provider(workspace_id))

            self.assertIn("No cloud provider", str(ctx.exception))


class MasterModelConfigHonestBlockTests(unittest.TestCase):
    """_resolve_cloud_provider is Sage's OWN resolver, but it is ALSO called
    on behalf of any other agent's "platform_credits" mode (that branch
    delegates to the shared workspace default). Before this fix, Sage's
    Model tab could save cli_subscription/local mode and the save would
    succeed — but Sage's turns never actually used it, silently continuing
    on the platform/DeepSeek default with no error anywhere. The fix is
    OPT-IN (check_master_model_config, default False) specifically so a
    completely unrelated specialist agent's platform_credits turn can never
    fail because of a stale/wrong setting on Sage's own card — that would
    be a cross-agent coupling bug of exactly the kind this platform works
    hard to avoid elsewhere (the memory-isolation audit). These tests cover
    both halves: the check fires and is honest when explicitly requested,
    and is a complete no-op (byte-for-byte today's behavior) when not."""

    def setUp(self):
        self._original_deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = "sk-platform-deepseek-key"

    def tearDown(self):
        if self._original_deepseek_key:
            os.environ["DEEPSEEK_API_KEY"] = self._original_deepseek_key
        elif "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]

    @staticmethod
    def _patch_master_lookup(model_config=None, raise_error=False):
        """Patches the master-install lookup _resolve_cloud_provider now does
        to check Sage's own model_config before resolving anything."""
        tenant_patch = patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            AsyncMock(return_value="tenant-1"),
        )
        if raise_error:
            install_patch = patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                AsyncMock(side_effect=RuntimeError("db unavailable")),
            )
        else:
            install_patch = patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                AsyncMock(return_value={"metadata": {"model_config": dict(model_config or {})}}),
            )
        return tenant_patch, install_patch

    @staticmethod
    def _patch_normal_resolution():
        """The happy-path mocks NoFallbackProviderResolutionTests uses,
        reused here so tests that expect resolution to PROCEED (not be
        blocked) have a working platform-credits path underneath."""
        ws_p = patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            AsyncMock(return_value={"metadata": {}}),
        )
        adm_p = patch(
            "server_modules.workspace_config_schema.workspace_admin_defaults_from_metadata",
            return_value=MagicMock(sage_ai_provider=""),
        )
        creds_p = patch(
            "server_modules.agent_turn_runtime_service.direct_chat_credentials",
            return_value={"api_key": "sk-platform-deepseek-key"},
        )
        supp_p = patch(
            "server_modules.agent_turn_runtime_service.supports_direct_message_native_chat",
            return_value=True,
        )
        ent_p = patch(
            "server_modules.entitlements_service.hosted_sage_ai_access_state_for_workspace_id",
            return_value={"allowed": True},
        )
        return ws_p, adm_p, creds_p, supp_p, ent_p

    # ── check_master_model_config=True: the check is active and honest ──

    def test_master_cli_subscription_mode_is_blocked_with_a_clear_reason(self):
        tenant_p, install_p = self._patch_master_lookup({"mode": "cli_subscription", "runtime": "codex"})
        with tenant_p, install_p:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(_resolve_cloud_provider("ws_master_cli_sub", check_master_model_config=True))
            msg = str(ctx.exception)
            self.assertIn("cli_subscription", msg)
            # Was assertIn("Sage", msg). "Sage" is retired naming -- the
            # message correctly says "this agent" now, and this assertion had
            # been failing ever since. It asserts the message names what the
            # setting applies to, without pinning a product name that is
            # deliberately being removed.
            self.assertIn("agent", msg.lower())
            self.assertNotIn("No cloud provider", msg, "must be the specific honest-block message, not the generic fallback")

    def test_master_local_mode_is_blocked_with_a_clear_reason(self):
        tenant_p, install_p = self._patch_master_lookup({"mode": "local", "runtime": "ollama"})
        with tenant_p, install_p:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(_resolve_cloud_provider("ws_master_local", check_master_model_config=True))
            self.assertIn("local", str(ctx.exception))

    def test_master_platform_credits_mode_resolves_normally(self):
        """The overwhelming common case — mode absent or explicitly
        platform_credits — must be completely unaffected even WITH the
        check turned on: no new error, normal DeepSeek resolution."""
        tenant_p, install_p = self._patch_master_lookup({"mode": "platform_credits"})
        ws_p, adm_p, creds_p, supp_p, ent_p = self._patch_normal_resolution()
        with tenant_p, install_p, ws_p, adm_p, creds_p, supp_p, ent_p:
            provider, _ = asyncio.run(_resolve_cloud_provider("ws_master_platform_credits", check_master_model_config=True))
            self.assertEqual(provider, "deepseek")

    def test_master_with_no_model_config_at_all_resolves_normally(self):
        """Most workspaces: the master install exists but has never had its
        model_config touched at all — empty dict, not an error."""
        tenant_p, install_p = self._patch_master_lookup({})
        ws_p, adm_p, creds_p, supp_p, ent_p = self._patch_normal_resolution()
        with tenant_p, install_p, ws_p, adm_p, creds_p, supp_p, ent_p:
            provider, _ = asyncio.run(_resolve_cloud_provider("ws_master_no_model_config", check_master_model_config=True))
            self.assertEqual(provider, "deepseek")

    def test_master_lookup_failure_never_blocks_normal_resolution(self):
        """Best-effort: if the master-install lookup itself fails (DB hiccup,
        workspace mid-migration, whatever), Sage's turn must still be able
        to resolve normally — this check must never be a NEW single point
        of failure for the common case."""
        tenant_p, install_p = self._patch_master_lookup(raise_error=True)
        ws_p, adm_p, creds_p, supp_p, ent_p = self._patch_normal_resolution()
        with tenant_p, install_p, ws_p, adm_p, creds_p, supp_p, ent_p:
            provider, _ = asyncio.run(_resolve_cloud_provider("ws_master_lookup_fails", check_master_model_config=True))
            self.assertEqual(provider, "deepseek")

    def test_master_byok_mode_resolves_normally_not_blocked(self):
        """Only cli_subscription/local are blocked — byok_api is a real,
        working mode for Sage's own card and must not be affected."""
        tenant_p, install_p = self._patch_master_lookup({"mode": "byok_api", "provider": "anthropic"})
        ws_p, adm_p, creds_p, supp_p, ent_p = self._patch_normal_resolution()
        with tenant_p, install_p, ws_p, adm_p, creds_p, supp_p, ent_p:
            provider, _ = asyncio.run(_resolve_cloud_provider("ws_master_byok", check_master_model_config=True))
            self.assertEqual(provider, "deepseek")

    # ── default (check_master_model_config=False): complete no-op, proves
    # a specialist's platform_credits delegation can NEVER be cross-
    # contaminated by an unrelated mismatch on Sage's own card ──────────

    def test_default_never_looks_up_the_master_install_at_all(self):
        """The strongest possible proof of "zero behavior change by
        default": patch the master lookup to explode if it's ever called,
        and confirm normal resolution still succeeds without touching it."""
        tenant_p = patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            AsyncMock(side_effect=AssertionError("must not be called when check_master_model_config=False")),
        )
        install_p = patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            AsyncMock(side_effect=AssertionError("must not be called when check_master_model_config=False")),
        )
        ws_p, adm_p, creds_p, supp_p, ent_p = self._patch_normal_resolution()
        with tenant_p, install_p, ws_p, adm_p, creds_p, supp_p, ent_p:
            provider, _ = asyncio.run(_resolve_cloud_provider("ws_default_no_lookup"))
            self.assertEqual(provider, "deepseek")

    def test_default_does_not_block_even_when_master_is_misconfigured(self):
        """The cross-agent-coupling proof: even if Sage's OWN card is stuck
        on cli_subscription (the exact case that raises above), a caller
        that does NOT opt in (e.g. a specialist's platform_credits
        delegation) must still resolve normally, not inherit Sage's error."""
        tenant_p, install_p = self._patch_master_lookup({"mode": "cli_subscription", "runtime": "codex"})
        ws_p, adm_p, creds_p, supp_p, ent_p = self._patch_normal_resolution()
        with tenant_p, install_p, ws_p, adm_p, creds_p, supp_p, ent_p:
            provider, _ = asyncio.run(_resolve_cloud_provider("ws_default_ignores_bad_master"))
            self.assertEqual(provider, "deepseek")

    def test_resolve_agent_cloud_provider_platform_credits_branch_opts_out_explicitly(self):
        """Integration-level proof for the actual in-scope caller:
        _resolve_agent_cloud_provider's platform_credits branch must not
        be blocked by a bad master config either."""
        tenant_p, install_p = self._patch_master_lookup({"mode": "local", "runtime": "ollama"})
        ws_p, adm_p, creds_p, supp_p, ent_p = self._patch_normal_resolution()
        with tenant_p, install_p, ws_p, adm_p, creds_p, supp_p, ent_p:
            provider, credentials, billing_mode = asyncio.run(
                _resolve_agent_cloud_provider("ws_specialist_platform_credits", {"mode": "platform_credits"}, "specialist-1")
            )
            self.assertEqual(provider, "deepseek")
            self.assertEqual(billing_mode, "platform_credits")


class SignupCreditGrantTests(unittest.TestCase):
    """Verify new workspaces receive the lean 100-credit signup grant.

    The numbers below are PRICING DECISIONS and are deliberately written as
    literals, not derived from billing_credit_config. A test that computed
    them from the same constant the code reads could only ever confirm
    itself, and would stay green through an accidental 20x change to what
    every new account is given away for free. If an assertion here fails,
    the correct response is to confirm the pricing change was intended and
    then update the literal -- never to derive it.

    This file was missed by f452d44cc ("reconcile stale docs/tests with the
    lean-grant rate change"), so it kept asserting the OLD rate -- 10,000
    credits, and 2,000 credits per dollar -- for weeks after the product
    deliberately moved to 100 credits at 1 credit = 1 cent. It failed the
    whole time; nothing gates on this suite, so nobody saw it.
    """

    def test_new_workspace_billing_metadata_includes_credit_grant(self):
        """_new_workspace_billing_metadata() must include credit_balance_usd
        and a bonus transaction recording the 100-credit grant."""
        from server_modules.control_plane_repository import (
            _new_workspace_billing_metadata,
            NEW_ACCOUNT_SIGNUP_CREDIT_USD,
        )

        meta = _new_workspace_billing_metadata()
        billing = meta.get("billing", {})

        # Credit balance must be set
        self.assertIn("credit_balance_usd", billing)
        self.assertEqual(billing["credit_balance_usd"], NEW_ACCOUNT_SIGNUP_CREDIT_USD)

        # A bonus transaction must record the grant
        transactions = billing.get("credit_transactions", [])
        self.assertGreater(len(transactions), 0)
        bonus = transactions[0]
        self.assertEqual(bonus["kind"], "bonus")
        self.assertEqual(bonus["amount_usd"], NEW_ACCOUNT_SIGNUP_CREDIT_USD)
        self.assertEqual(bonus["source"], "signup_grant")
        # $1.00 at 100 credits/$ -- the lean grant, 1 credit = 1 cent.
        self.assertEqual(bonus["credits"], 100)
        self.assertIn("100", bonus.get("label", ""))

    def test_signup_credit_grant_env_var_override(self):
        """EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD overrides the grant.

        The override value must DIFFER from the default. It used to be
        "1.00", which is exactly the default -- so this test passed whether
        the env var was honoured or ignored completely, and proved nothing
        about overriding. 7.50 is picked precisely because no default in
        billing_credit_config is 7.50.
        """
        # Re-import to pick up env var. The constant now lives in
        # billing_credit_config.py (single source of truth) and
        # control_plane_repository re-exports it at import time, so both
        # modules must be reloaded — billing_credit_config first (to
        # recompute from the patched env), then control_plane_repository
        # (to re-bind the fresh value).
        #
        # The RESTORING reloads have to sit outside the patch.dict block.
        # Run inside it (as they were) they re-read the still-overridden
        # variable, "restore" the module to the overridden value, and leak
        # it into every later test in the session — silently, because
        # control_plane_repository is on conftest's restore list and
        # billing_credit_config was not, so the two ended up disagreeing
        # about the same constant.
        import importlib
        import server_modules.billing_credit_config as bcc
        import server_modules.control_plane_repository as cpr

        try:
            with patch.dict(
                os.environ,
                {"EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD": "7.50"},
                clear=False,
            ):
                importlib.reload(bcc)
                importlib.reload(cpr)

                meta = cpr._new_workspace_billing_metadata()
                billing = meta.get("billing", {})
                self.assertEqual(billing["credit_balance_usd"], 7.5)
                transactions = billing.get("credit_transactions", [])
                self.assertEqual(transactions[0]["amount_usd"], 7.5)
                # 7.50 at 100 credits/$ = 750.
                self.assertEqual(transactions[0]["credits"], 750)
                # The default is 1.00/100 -- asserting the overridden value is
                # NOT the default is what makes this an override test at all.
                self.assertNotEqual(transactions[0]["credits"], 100)
        finally:
            # env is no longer patched here, so these reload the real defaults
            importlib.reload(bcc)
            importlib.reload(cpr)

    def test_zero_env_var_disables_grant(self):
        """Setting EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD=0 disables the grant."""
        # Restoring reloads outside the patch.dict block — see the comment in
        # test_signup_credit_grant_env_var_override above. This is the case
        # that actually bit: "0" is not the default, so the leaked value was a
        # disabled signup grant for every test that ran after this one.
        import importlib
        import server_modules.billing_credit_config as bcc
        import server_modules.control_plane_repository as cpr

        try:
            with patch.dict(
                os.environ,
                {"EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD": "0"},
                clear=False,
            ):
                importlib.reload(bcc)
                importlib.reload(cpr)

                meta = cpr._new_workspace_billing_metadata()
                billing = meta.get("billing", {})
                self.assertEqual(billing["credit_balance_usd"], 0.0)
                transactions = billing.get("credit_transactions", [])
                # When grant is 0, no transaction should be created
                self.assertEqual(len(transactions), 0)
        finally:
            importlib.reload(bcc)
            importlib.reload(cpr)

    def test_workspace_shell_metadata_includes_billing_grant(self):
        """_workspace_shell_metadata with _new_workspace_billing_metadata()
        must preserve credit_balance_usd — the fix for create_workspace_for_user
        (was passing {} before, which silently dropped the grant)."""
        from server_modules.control_plane_repository import (
            _new_workspace_billing_metadata,
            _workspace_shell_metadata,
        )

        meta = _workspace_shell_metadata(
            _new_workspace_billing_metadata(),
            preferred_shell_profile="default",
            default_route="/w/ws_test/chat",
            setup_completed=True,
        )
        # The workspace shell metadata must carry the billing grant forward
        billing = meta.get("billing", {})
        self.assertIn("credit_balance_usd", billing)
        self.assertGreater(billing["credit_balance_usd"], 0)

        # Verify the grant amount: USD × 20,000 credits/USD
        from server_modules.billing_credit_config import HOSTED_SAGE_AI_CREDITS_PER_USD
        from server_modules.control_plane_repository import NEW_ACCOUNT_SIGNUP_CREDIT_USD
        expected_credits = int(NEW_ACCOUNT_SIGNUP_CREDIT_USD * HOSTED_SAGE_AI_CREDITS_PER_USD)
        credits = int(billing["credit_balance_usd"] * HOSTED_SAGE_AI_CREDITS_PER_USD)
        self.assertEqual(credits, expected_credits)
        self.assertGreater(credits, 0)

        # The bonus transaction must record the grant
        transactions = billing.get("credit_transactions", [])
        self.assertGreater(len(transactions), 0)
        bonus = transactions[0]
        self.assertEqual(bonus["kind"], "bonus")
        self.assertEqual(bonus["credits"], expected_credits)
        self.assertEqual(bonus["source"], "signup_grant")

        # Contrast: passing {} (the old behavior) would lose the grant
        empty_meta = _workspace_shell_metadata(
            {},
            preferred_shell_profile="default",
            default_route="/w/ws_test/chat",
            setup_completed=True,
        )
        empty_billing = empty_meta.get("billing", {})
        self.assertEqual(empty_billing.get("credit_balance_usd", 0), 0)


class HardStopMessageTests(unittest.TestCase):
    """Verify the hard-stop messages are consistent and actionable."""

    def test_credit_exhaustion_message_is_present(self):
        """The AI limit message must be the canonical one and direct to AI Setup.

        Asserted by IDENTITY against platform_event, not by matching prose.
        This test used to require the phrase "reached your ai limit"; the
        message was reworded to "AI usage limit reached. Open AI & Setup" and
        the test simply failed from then on. CLAUDE.md already records this
        exact failure mode ("Stale string matching... Match on stable codes,
        never on prose") from the time an error BUCKET matched dead prose and
        real users got "Something went wrong" for five weeks.

        Identity is the stronger check anyway: it catches the thing that
        actually matters -- a caller hand-typing its own copy of this message
        instead of using the shared constant -- and it survives rewording.
        """
        from server_modules import platform_event

        self.assertEqual(SAGE_AI_LIMIT_MESSAGE, platform_event.AI_LIMIT_REACHED_WEB.detail)
        self.assertTrue(SAGE_AI_LIMIT_MESSAGE.strip(), "the limit message must not be empty")
        self.assertIn("ai-runtime", _SAGE_AI_SETUP_PATH)

    def test_ai_needs_attention_message_is_present(self):
        """The AI needs attention message must exist and be actionable.

        Was a prose match on "Setup" -- the literal page name in the OLD
        copy ("Open AI & Setup"), which has not existed as a real page in
        this app for some time (CLAUDE.md's "copy that names a screen goes
        stale" failure mode). Reworded 2026-08-19 to name the agent's own
        Model settings instead and to drop the trailing "→", which this
        surface renders as plain text with no click target (CLAUDE.md's "No
        dead controls" law). Asserted by IDENTITY against platform_event
        now, matching test_credit_exhaustion_message_is_present's own
        established pattern in this same class -- it survives rewording.
        """
        from server_modules import platform_event

        self.assertEqual(SAGE_AI_NEEDS_ATTENTION_MESSAGE, platform_event.AUTH_FAILED_WEB.detail)
        self.assertIn("needs attention", SAGE_AI_NEEDS_ATTENTION_MESSAGE.lower())
        self.assertIn("AI", SAGE_AI_NEEDS_ATTENTION_MESSAGE)
        self.assertFalse(SAGE_AI_NEEDS_ATTENTION_MESSAGE.rstrip().endswith("→"))

    def test_sage_command_dispatcher_has_exhaustion_messages(self):
        """The command dispatcher must export error classification messages."""
        from server_modules.agent_command_dispatcher import (
            SAGE_AI_LIMIT_REPLY,
            SAGE_AI_NEEDS_ATTENTION_REPLY,
            SAGE_RATE_LIMITED_REPLY,
            SAGE_PROVIDER_UNREACHABLE_REPLY,
            SAGE_ERROR_REPLY,
        )

        # The property that actually matters is that the five buckets are
        # DISTINGUISHABLE -- collapsing two of them is the failure this whole
        # classification exists to prevent, and it is the shape CLAUDE.md
        # documents over and over ("an empty string is not a decision",
        # "three facts, not two").
        replies = {
            "ai_limit": SAGE_AI_LIMIT_REPLY,
            "needs_attention": SAGE_AI_NEEDS_ATTENTION_REPLY,
            "rate_limited": SAGE_RATE_LIMITED_REPLY,
            "unreachable": SAGE_PROVIDER_UNREACHABLE_REPLY,
            "generic": SAGE_ERROR_REPLY,
        }
        for name, reply in replies.items():
            self.assertTrue(str(reply or "").strip(), f"{name} reply must not be empty")
        self.assertEqual(
            len(set(replies.values())),
            len(replies),
            "every bucket must read differently, or the classification tells the user nothing",
        )

        # Bucket 1 is the canonical shared constant rather than a hand-typed
        # twin. Asserted by identity so a rewording cannot break it -- the
        # previous version required the chatty "Heads up" prefix, which was
        # deliberately removed (a professional tool labels, it does not
        # lecture), and these assertions have failed ever since.
        from server_modules.platform_event import AI_LIMIT_REACHED

        self.assertEqual(SAGE_AI_LIMIT_REPLY, AI_LIMIT_REACHED.channel_text)
        self.assertIn("authentication", SAGE_AI_NEEDS_ATTENTION_REPLY.lower())
        self.assertIn("api key", SAGE_AI_NEEDS_ATTENTION_REPLY.lower())

        # Bucket 4 — Provider unreachable. The "heads up" prefix these
        # assertions required was deliberately dropped from every reply; only
        # the load-bearing word is checked now.
        self.assertIn("unreachable", SAGE_PROVIDER_UNREACHABLE_REPLY.lower())
        self.assertIn("try again", SAGE_PROVIDER_UNREACHABLE_REPLY.lower())

        # Bucket 5 — Catch-all
        self.assertIn("something went wrong", SAGE_ERROR_REPLY.lower())
        self.assertIn("try again", SAGE_ERROR_REPLY.lower())

    def test_entitlement_exhaustion_message_is_friendly(self):
        """The entitlement service must return a friendly message when
        both credit_balance and monthly cap are zero."""
        import server_modules.entitlements_service as es

        # Simulate zero credit balance and zero monthly remaining
        state = MagicMock()
        state.entitlements = {
            "hosted_ai_enabled": True,
            "hosted_sage_ai_policy": "enabled_with_cap",
            "hosted_sage_ai_monthly_cap_usd": 0.0,
        }
        state.usage = {
            "hosted_sage_cost_usd_monthly": 0.50,
            "hosted_sage_credit_balance_usd": 0.0,
        }

        from server_modules import platform_event

        result = es.hosted_sage_ai_access_state(state=state)
        self.assertFalse(result["allowed"])
        # Identity, not prose -- see test_credit_exhaustion_message_is_present.
        # A blocked turn must hand back the ONE canonical limit message, so
        # that rewording it is a single edit rather than a hunt through
        # every module that happened to retype the sentence.
        self.assertEqual(result["message"], platform_event.AI_LIMIT_REACHED_WEB.detail)


class ClassifyErrorTests(unittest.TestCase):
    """Verify classify_error() maps error strings to the correct buckets."""

    @classmethod
    def setUpClass(cls):
        from server_modules.agent_command_dispatcher import classify_error
        cls._classify = staticmethod(classify_error)

    def _classify(self, error_text, *, is_platform_credits=True):
        """Call the classify_error function without instance binding."""
        # Use the underlying function via the descriptor protocol
        return type(self)._classify.__func__(error_text, is_platform_credits=is_platform_credits)

    # ── Bucket 1 — Credits exhausted ──

    def test_credits_exhausted_reached_limit(self):
        msg = self._classify("You've reached your AI limit. Open AI & Setup →")
        self.assertIn("credit exhausted", msg.lower())

    def test_credits_exhausted_cap_reached(self):
        msg = self._classify("cap_reached: monthly limit")
        self.assertIn("credit exhausted", msg.lower())

    # ── Bucket 2 — Rate limited ──

    def test_rate_limited_provider_code(self):
        msg = self._classify("provider_rate_limited: http 429")
        self.assertIn("rate limited", msg.lower())
        self.assertIn("try again in a moment", msg.lower())

    def test_rate_limited_429(self):
        msg = self._classify("HTTP 429 Too Many Requests")
        self.assertIn("rate limited", msg.lower())

    def test_rate_limited_too_many_requests(self):
        msg = self._classify("too many requests error")
        self.assertIn("rate limited", msg.lower())

    # ── Bucket 3 — Auth / key failed ──
    #
    # is_platform_credits defaults to True (the safer, less-blaming
    # assumption — see classify_error's docstring), so these BYOK-flavored
    # assertions ("api key", "verify") pass is_platform_credits=False
    # explicitly: they're testing the case where the customer legitimately
    # owns the failing key. The complementary platform-credits tests below
    # cover the opposite, default case.

    def test_auth_failed_provider_code(self):
        msg = self._classify("provider_generation_failed: http 401", is_platform_credits=False)
        self.assertIn("authentication", msg.lower())
        self.assertIn("api key", msg.lower())

    def test_auth_failed_401(self):
        msg = self._classify("HTTP error 401 Unauthorized", is_platform_credits=False)
        self.assertIn("authentication", msg.lower())

    def test_auth_failed_403(self):
        msg = self._classify("403 Forbidden - check your API key", is_platform_credits=False)
        self.assertIn("authentication", msg.lower())

    def test_auth_failed_invalid_key(self):
        msg = self._classify("invalid key: authentication failed", is_platform_credits=False)
        self.assertIn("authentication", msg.lower())

    def test_auth_failed_unauthorized(self):
        msg = self._classify("unauthorized access to model", is_platform_credits=False)
        self.assertIn("authentication", msg.lower())

    # ── Bucket 3, platform-credits variant — never blame the customer's key ──

    def test_auth_failed_platform_credits_never_mentions_api_key(self):
        """The 2026-07-09 first-run integrity fix: a platform-credits agent's
        auth failure must never send the customer to "verify" a key they
        never configured. Called with no override — is_platform_credits
        defaults to True, so this also proves the safe default applies to
        callers that haven't been migrated to pass the flag explicitly."""
        msg = self._classify("provider_generation_failed: http 401")
        self.assertNotIn("api key", msg.lower())
        self.assertNotIn("verify", msg.lower())
        self.assertIn("platform side", msg.lower())

    # ── Bucket — Provider payment/balance required (HTTP 402) ──
    #
    # Distinct from auth failed: the key works, the account is empty. Must
    # be checked before bucket 3 — "provider_generation_failed" (bucket 3's
    # own generic-fallback keyword) must never swallow a payment-required
    # signal that reaches classify_error as a coded string.

    def test_payment_required_platform_credits(self):
        msg = self._classify("deepseek generation failed: http_402: Payment Required")
        self.assertNotIn("api key", msg.lower())
        self.assertIn("platform-side issue", msg.lower())

    def test_payment_required_byok(self):
        msg = self._classify(
            "openai generation failed: http_402: Payment Required",
            is_platform_credits=False,
        )
        self.assertIn("provider account", msg.lower())

    def test_payment_required_insufficient_balance_phrase(self):
        msg = self._classify("Insufficient Balance")
        self.assertIn("credits", msg.lower())

    def test_payment_required_checked_before_generic_auth_code(self):
        """The generic fallback code alone (no 402/balance detail) still
        correctly falls into the auth bucket, not payment-required — this
        guards the bucket ordering, not just keyword presence."""
        msg = self._classify("provider_generation_failed")
        self.assertIn("platform side", msg.lower())

    # ── Bucket 4 — Provider unreachable ──

    def test_provider_unreachable_code(self):
        msg = self._classify("provider_transport_unavailable: connection refused")
        self.assertIn("unreachable", msg.lower())
        self.assertIn("try again shortly", msg.lower())

    def test_provider_unreachable_timeout(self):
        msg = self._classify("connection timeout after 30s")
        self.assertIn("unreachable", msg.lower())

    def test_provider_unreachable_connection(self):
        msg = self._classify("connection error: unreachable host")
        self.assertIn("unreachable", msg.lower())

    # ── Bucket 5 — Catch-all ──

    def test_catch_all_unknown_error(self):
        msg = self._classify("something exploded unexpectedly")
        self.assertIn("something went wrong", msg.lower())
        self.assertIn("try again", msg.lower())

    def test_catch_all_none_input(self):
        msg = self._classify(None)
        self.assertIn("something went wrong", msg.lower())

    def test_catch_all_empty_string(self):
        msg = self._classify("")
        self.assertIn("something went wrong", msg.lower())

    # ── Order: credits exhausted before rate limited ──

    def test_credit_limit_checked_before_rate_limit(self):
        """When error mentions both limit and 429, credit takes priority."""
        msg = self._classify("reached your ai limit: HTTP 429")
        self.assertIn("credit exhausted", msg.lower())

    # ── Order: rate limited before auth ──

    def test_rate_limit_checked_before_auth(self):
        """When error mentions both 429 and 401, rate limit takes priority."""
        msg = self._classify("HTTP 429 rate limit; also 401 auth issue")
        self.assertIn("rate limited", msg.lower())


class ErrorNotificationTests(unittest.TestCase):
    """Verify classify_error_notification() produces full notifications."""

    def test_rate_limited_notification_has_warning_tone(self):
        from server_modules.error_notification import classify_error_notification
        n = classify_error_notification("HTTP 429 rate limit", raw_error="TooManyRequests")
        self.assertEqual(n.tone, "warning")
        self.assertEqual(n.title, "Rate Limited")
        self.assertIn("rate limited", n.body.lower())
        self.assertEqual(n.raw_detail, "TooManyRequests")
        self.assertTrue(len(n.actions) > 0)
        self.assertEqual(n.actions[0].label, "Try Again")
        self.assertEqual(n.actions[0].type, "command")
        self.assertEqual(n.actions[0].value, "/retry")
        self.assertEqual(n.actions[0].style, "primary")
        self.assertTrue(n.dismissible)

    def test_auth_failed_notification_has_danger_tone(self):
        from server_modules.error_notification import classify_error_notification
        n = classify_error_notification("HTTP 401 Unauthorized", raw_error="Invalid API key")
        self.assertEqual(n.tone, "danger")
        self.assertEqual(n.title, "Auth Failed")
        self.assertIn("authentication", n.body.lower())
        self.assertEqual(n.actions[0].label, "Try Again")
        self.assertEqual(n.actions[0].type, "command")
        self.assertEqual(n.actions[0].value, "/retry")

    def test_credit_exhausted_notification_has_try_again_action(self):
        from server_modules.error_notification import classify_error_notification
        n = classify_error_notification("reached your AI limit")
        self.assertEqual(n.tone, "danger")
        self.assertEqual(n.title, "Credit Exhausted")
        self.assertIn("credit exhausted", n.body.lower())
        self.assertEqual(n.actions[0].label, "Try Again")

    def test_catch_all_notification_has_try_again_action(self):
        from server_modules.error_notification import classify_error_notification
        n = classify_error_notification(None, raw_error="Connection reset")
        self.assertEqual(n.tone, "danger")
        self.assertEqual(n.title, "Something Went Wrong")
        self.assertEqual(n.raw_detail, "Connection reset")
        self.assertEqual(n.actions[0].label, "Try Again")

    def test_as_dict_serializable(self):
        from server_modules.error_notification import classify_error_notification
        import json
        n = classify_error_notification("timeout unreachable", raw_error="ETIMEDOUT")
        d = n.as_dict()
        self.assertEqual(d["tone"], "warning")
        self.assertEqual(d["title"], "Service Unreachable")
        self.assertEqual(len(d["actions"]), 1)
        self.assertEqual(d["actions"][0]["label"], "Try Again")
        json.dumps(d)  # does not raise

    def test_render_text_includes_title_body_and_actions(self):
        from server_modules.error_notification import (
            classify_error_notification, render_error_notification_text,
        )
        n = classify_error_notification("HTTP 429 rate limit", raw_error="try later")
        text = render_error_notification_text(n)
        self.assertIn("Rate Limited", text)
        self.assertIn("rate limited", text.lower())
        self.assertIn("try later", text)
        self.assertIn("/retry", text)

    def test_provider_unreachable_notification(self):
        from server_modules.error_notification import classify_error_notification
        n = classify_error_notification("provider_transport_unavailable")
        self.assertEqual(n.tone, "warning")
        self.assertEqual(n.title, "Service Unreachable")
        self.assertIn("unreachable", n.body.lower())

    def test_all_buckets_have_try_again_action(self):
        """Every error bucket has exactly one Try Again /retry action."""
        from server_modules.error_notification import classify_error_notification
        for error in (
            "reached your ai limit",
            "HTTP 429 rate limit",
            "401 unauthorized api key",
            "provider_transport_unavailable",
            "something weird happened",
            None,
        ):
            n = classify_error_notification(error)
            self.assertEqual(len(n.actions), 1)
            self.assertEqual(n.actions[0].label, "Try Again")
            self.assertEqual(n.actions[0].value, "/retry")


class NoFallbackDefaultTests(unittest.TestCase):
    """Verify the fallback engine defaults to NO fallback."""

    def test_provider_order_defaults_to_single_provider(self):
        """Without any flags, provider_order_for_run returns at most ONE provider."""
        import sys
        import os as _os

        # Ensure the script is importable
        sys.path.insert(0, str(
            __import__('pathlib').Path(__file__).resolve().parent.parent.parent
        ))
        from scripts.orion_local_worker_llm import (
            provider_order_for_run,
            SUPPORTED_PROVIDERS,
            provider_has_usable_credentials,
        )

        # Remove env var overrides
        with patch.dict(
            _os.environ,
            {
                "ORION_LOCAL_WORKER_PROVIDER_FALLBACK": "0",
                "ORION_LOCAL_WORKER_PROVIDER": "",
                "ORION_LOCAL_WORKER_PROVIDER_ORDER": "",
            },
            clear=False,
        ):
            # Simulate 3 providers having credentials
            context = {"provider": "deepseek"}
            metadata = {"source": "sage_chat"}
            # No disable_provider_fallback or enable_provider_fallback set

            def mock_has_creds(pid, ctx, meta):
                return pid in {"deepseek", "anthropic", "openai"}

            import scripts.orion_local_worker_llm as llm_mod

            with patch.object(
                llm_mod, "provider_has_usable_credentials", side_effect=mock_has_creds
            ):
                result = provider_order_for_run(context, metadata)

            # Must return at most 1 provider (no fallback)
            self.assertLessEqual(
                len(result), 1,
                f"Expected at most 1 provider (no fallback default), got {len(result)}: {result}"
            )

    def test_provider_order_returns_empty_when_no_credentials(self):
        """When the selected provider has no credentials, return empty list
        (hard stop) rather than falling through to other providers."""
        import sys
        import os as _os

        sys.path.insert(0, str(
            __import__('pathlib').Path(__file__).resolve().parent.parent.parent
        ))
        from scripts.orion_local_worker_llm import provider_order_for_run

        with patch.dict(
            _os.environ,
            {
                "ORION_LOCAL_WORKER_PROVIDER_FALLBACK": "0",
                "ORION_LOCAL_WORKER_PROVIDER": "",
                "ORION_LOCAL_WORKER_PROVIDER_ORDER": "",
            },
            clear=False,
        ):
            import scripts.orion_local_worker_llm as llm_mod

            # Context provider has NO credentials, but others do
            context = {"provider": "deepseek"}
            metadata = {"source": "sage_chat"}

            def mock_has_creds(pid, ctx, meta):
                # deepseek has NO credentials; others do
                return pid != "deepseek"

            with patch.object(
                llm_mod, "provider_has_usable_credentials", side_effect=mock_has_creds
            ):
                result = provider_order_for_run(context, metadata)

            # With no-fallback default, when context_provider has no creds
            # and disable_fallback is True (default), line 3520 returns []
            self.assertEqual(
                len(result), 0,
                f"Expected empty list (hard stop), got {len(result)}: {result}"
            )


from server_modules.platform_event import AI_LIMIT_REACHED_WEB


class CreditHoldSettleTests(unittest.TestCase):
    """Verify the hold→settle credit accounting pattern.

    Each test gets a fresh temp SQLite DB so stale reservations from
    previous runs don't interfere.
    """

    @staticmethod
    def _session_ctx(request_id, tenant_id="tenant-test"):
        return {
            "request_id": request_id,
            "tenant_id": tenant_id,
        }

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._temp_db = os.path.join(self._temp_dir.name, "reservations.sqlite3")
        self._orig_env = os.environ.get("EMPYRALIS_HOSTED_AI_RESERVATION_DB")
        os.environ["EMPYRALIS_HOSTED_AI_RESERVATION_DB"] = self._temp_db
        # Reload the module so it picks up the new DB path + fresh schema flag
        import server_modules.direct_chat_hosted_usage_service as _svc
        import importlib
        importlib.reload(_svc)
        self._svc = _svc

    def tearDown(self):
        if self._orig_env is not None:
            os.environ["EMPYRALIS_HOSTED_AI_RESERVATION_DB"] = self._orig_env
        elif "EMPYRALIS_HOSTED_AI_RESERVATION_DB" in os.environ:
            del os.environ["EMPYRALIS_HOSTED_AI_RESERVATION_DB"]
        self._temp_dir.cleanup()
        # Reload again to restore original DB path
        import server_modules.direct_chat_hosted_usage_service as _svc
        import importlib
        importlib.reload(_svc)

    def test_reservation_rejects_when_credit_available_is_zero(self):
        """When credit_available_usd is 0, reservation must raise hard-stop."""
        with self.assertRaises(RuntimeError) as ctx:
            self._svc.reserve_direct_chat_hosted_usage_best_effort(
                workspace_id="ws_test_credit_zero",
                thread_id="thread-1",
                session_ctx=self._session_ctx("req-1"),
                availability_payload={
                    "credential_plane": "platform_runtime",
                    "platform_runtime_allowed": True,
                },
                requested_provider="deepseek",
                requested_model="deepseek-chat",
                credit_available_usd=0.0,
            )
        # The REFUSAL above is what this test guards -- that part never broke.
        # Only the message match was stale ("reached your AI limit" was
        # reworded to "AI usage limit reached"), so assert identity against
        # the canonical constant instead of retyping the sentence.
        self.assertEqual(str(ctx.exception), AI_LIMIT_REACHED_WEB.detail)

    def test_reservation_allows_when_credit_available_sufficient(self):
        """When credit is sufficient, reservation succeeds."""
        try:
            result = self._svc.reserve_direct_chat_hosted_usage_best_effort(
                workspace_id="ws_test_credit_ok",
                thread_id="thread-2",
                session_ctx=self._session_ctx("req-2"),
                availability_payload={
                    "credential_plane": "platform_runtime",
                    "platform_runtime_allowed": True,
                },
                requested_provider="deepseek",
                requested_model="deepseek-chat",
                credit_available_usd=10.0,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "active")
            self.assertGreater(result["amount_usd"], 0)
        finally:
            self._svc.release_direct_chat_hosted_usage_reservation_best_effort(
                workspace_id="ws_test_credit_ok",
                thread_id="thread-2",
                session_ctx=self._session_ctx("req-2"),
                status="released",
            )

    def test_release_after_failure_clears_hold(self):
        """After releasing a hold, a new reservation should succeed
        (proving the hold was released and not leaked)."""
        ws_id = "ws_test_release"

        r1 = self._svc.reserve_direct_chat_hosted_usage_best_effort(
            workspace_id=ws_id,
            thread_id="thread-r",
            session_ctx=self._session_ctx("req-release"),
            availability_payload={
                "credential_plane": "platform_runtime",
                "platform_runtime_allowed": True,
            },
            requested_provider="deepseek",
            requested_model="deepseek-chat",
            credit_available_usd=10.0,
        )
        self.assertIsNotNone(r1)

        self._svc.release_direct_chat_hosted_usage_reservation_best_effort(
            workspace_id=ws_id,
            thread_id="thread-r",
            session_ctx=self._session_ctx("req-release"),
            status="released",
        )

        r2 = self._svc.reserve_direct_chat_hosted_usage_best_effort(
            workspace_id=ws_id,
            thread_id="thread-r2",
            session_ctx=self._session_ctx("req-release-2"),
            availability_payload={
                "credential_plane": "platform_runtime",
                "platform_runtime_allowed": True,
            },
            requested_provider="deepseek",
            requested_model="deepseek-chat",
            credit_available_usd=10.0,
        )
        self.assertIsNotNone(r2)

        self._svc.release_direct_chat_hosted_usage_reservation_best_effort(
            workspace_id=ws_id,
            thread_id="thread-r2",
            session_ctx=self._session_ctx("req-release-2"),
            status="released",
        )

    def test_hold_prevents_overspend_when_balance_low(self):
        """When balance is exactly enough for one hold but not two,
        the second reservation fails — preventing overspend."""
        ws_id = "ws_test_overspend_guard"
        hold_amount = max(float(self._svc._HOSTED_AI_PREFLIGHT_RESERVATION_USD or 0), 0.01)
        available = hold_amount

        r1 = self._svc.reserve_direct_chat_hosted_usage_best_effort(
            workspace_id=ws_id,
            thread_id="thread-os-1",
            session_ctx=self._session_ctx("req-os-1"),
            availability_payload={
                "credential_plane": "platform_runtime",
                "platform_runtime_allowed": True,
            },
            requested_provider="deepseek",
            requested_model="deepseek-chat",
            credit_available_usd=available,
        )
        self.assertIsNotNone(r1)

        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._svc.reserve_direct_chat_hosted_usage_best_effort(
                    workspace_id=ws_id,
                    thread_id="thread-os-2",
                    session_ctx=self._session_ctx("req-os-2"),
                    availability_payload={
                        "credential_plane": "platform_runtime",
                        "platform_runtime_allowed": True,
                    },
                    requested_provider="deepseek",
                    requested_model="deepseek-chat",
                    credit_available_usd=available,
                )
            self.assertEqual(str(ctx.exception), AI_LIMIT_REACHED_WEB.detail)
        finally:
            self._svc.release_direct_chat_hosted_usage_reservation_best_effort(
                workspace_id=ws_id,
                thread_id="thread-os-1",
                session_ctx=self._session_ctx("req-os-1"),
                status="released",
            )

    def test_empty_usage_skips_debit(self):
        """When total_tokens is 0 (empty reply), persist skips debit
        and releases the hold."""
        ws_id = "ws_test_empty"
        ctx = self._session_ctx("req-empty")

        self._svc.reserve_direct_chat_hosted_usage_best_effort(
            workspace_id=ws_id,
            thread_id="thread-empty",
            session_ctx=ctx,
            availability_payload={
                "credential_plane": "platform_runtime",
                "platform_runtime_allowed": True,
            },
            requested_provider="deepseek",
            requested_model="deepseek-chat",
            credit_available_usd=10.0,
        )

        self._svc.persist_direct_chat_hosted_usage_best_effort(
            workspace_id=ws_id,
            thread_id="thread-empty",
            session_ctx=ctx,
            availability_payload={
                "credential_plane": "platform_runtime",
                "platform_runtime_allowed": True,
            },
            usage_masked={"total_tokens": 0},
            requested_provider="deepseek",
            effective_provider="deepseek",
            requested_model="deepseek-chat",
            effective_model="deepseek-chat",
        )

        self._svc.release_direct_chat_hosted_usage_reservation_best_effort(
            workspace_id=ws_id,
            thread_id="thread-empty",
            session_ctx=ctx,
            status="released",
        )


if __name__ == "__main__":
    unittest.main()
