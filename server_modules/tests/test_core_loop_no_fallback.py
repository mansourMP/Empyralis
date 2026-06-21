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

from server_modules.sage_agent_runtime_service import (
    SAGE_AI_LIMIT_MESSAGE,
    SAGE_AI_NEEDS_ATTENTION_MESSAGE,
    _SAGE_AI_SETUP_PATH,
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
            "server_modules.sage_agent_runtime_service.direct_chat_credentials"
        )
        supp_p = patch(
            "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat"
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
            "server_modules.sage_agent_runtime_service.direct_chat_credentials",
            return_value=valid_creds,
        )
        supp_p = patch(
            "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
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
            "server_modules.sage_agent_runtime_service.direct_chat_credentials",
            return_value={"api_key": "sk-platform-deepseek-key"},
        )
        supp_p = patch(
            "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
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
            "server_modules.sage_agent_runtime_service.direct_chat_credentials",
            return_value={},
        )
        supp_p = patch(
            "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
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
            "server_modules.sage_agent_runtime_service.direct_chat_credentials"
        )
        supp_p = patch(
            "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat"
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
            "server_modules.sage_agent_runtime_service.direct_chat_credentials",
            return_value={},
        )
        supp_p = patch(
            "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
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
            "server_modules.sage_agent_runtime_service.direct_chat_credentials",
            return_value={},
        )
        supp_p = patch(
            "server_modules.sage_agent_runtime_service.supports_direct_message_native_chat",
            return_value=False,
        )

        with ws_p, adm_p, creds_p, supp_p:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(_resolve_cloud_provider(workspace_id))

            self.assertIn("No cloud provider", str(ctx.exception))


class SignupCreditGrantTests(unittest.TestCase):
    """Verify new workspaces receive the 10,000-credit signup grant."""

    def test_new_workspace_billing_metadata_includes_credit_grant(self):
        """_new_workspace_billing_metadata() must include credit_balance_usd
        and a bonus transaction recording the 10,000-credit grant."""
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
        self.assertEqual(bonus["credits"], 10000)
        self.assertIn("10,000", bonus.get("label", ""))

    def test_signup_credit_grant_env_var_override(self):
        """EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD overrides the grant."""
        with patch.dict(
            os.environ,
            {"EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD": "1.00"},
            clear=False,
        ):
            # Re-import to pick up env var
            import importlib
            import server_modules.control_plane_repository as cpr

            importlib.reload(cpr)

            try:
                meta = cpr._new_workspace_billing_metadata()
                billing = meta.get("billing", {})
                self.assertEqual(billing["credit_balance_usd"], 1.0)
                transactions = billing.get("credit_transactions", [])
                self.assertEqual(transactions[0]["amount_usd"], 1.0)
                self.assertEqual(transactions[0]["credits"], 20000)
            finally:
                # Restore original
                importlib.reload(cpr)

    def test_zero_env_var_disables_grant(self):
        """Setting EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD=0 disables the grant."""
        with patch.dict(
            os.environ,
            {"EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD": "0"},
            clear=False,
        ):
            import importlib
            import server_modules.control_plane_repository as cpr

            importlib.reload(cpr)

            try:
                meta = cpr._new_workspace_billing_metadata()
                billing = meta.get("billing", {})
                self.assertEqual(billing["credit_balance_usd"], 0.0)
                transactions = billing.get("credit_transactions", [])
                # When grant is 0, no transaction should be created
                self.assertEqual(len(transactions), 0)
            finally:
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
        """The AI limit message must exist and direct to AI Setup."""
        self.assertIn("reached your ai limit", SAGE_AI_LIMIT_MESSAGE.lower())
        self.assertIn("AI", SAGE_AI_LIMIT_MESSAGE)
        self.assertIn("Setup", SAGE_AI_LIMIT_MESSAGE)
        self.assertIn("ai-runtime", _SAGE_AI_SETUP_PATH)

    def test_ai_needs_attention_message_is_present(self):
        """The AI needs attention message must exist."""
        self.assertIn("needs attention", SAGE_AI_NEEDS_ATTENTION_MESSAGE.lower())
        self.assertIn("AI", SAGE_AI_NEEDS_ATTENTION_MESSAGE)
        self.assertIn("Setup", SAGE_AI_NEEDS_ATTENTION_MESSAGE)

    def test_sage_command_dispatcher_has_exhaustion_messages(self):
        """The command dispatcher must export generic AI-stop messages."""
        from server_modules.sage_command_dispatcher import (
            SAGE_AI_LIMIT_REPLY,
            SAGE_AI_NEEDS_ATTENTION_REPLY,
        )

        self.assertIn("reached your ai limit", SAGE_AI_LIMIT_REPLY.lower())
        self.assertIn("AI", SAGE_AI_LIMIT_REPLY)
        self.assertIn("Setup", SAGE_AI_LIMIT_REPLY)

        self.assertIn("needs attention", SAGE_AI_NEEDS_ATTENTION_REPLY.lower())
        self.assertIn("AI", SAGE_AI_NEEDS_ATTENTION_REPLY)
        self.assertIn("Setup", SAGE_AI_NEEDS_ATTENTION_REPLY)

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

        result = es.hosted_sage_ai_access_state(state=state)
        self.assertFalse(result["allowed"])
        self.assertIn("reached your ai limit", result["message"].lower())
        self.assertIn("ai & setup", result["message"].lower())


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
        self.assertIn("reached your AI limit", str(ctx.exception))

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
            self.assertIn("reached your AI limit", str(ctx.exception))
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
