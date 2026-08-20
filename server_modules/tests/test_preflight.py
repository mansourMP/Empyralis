"""Phase T: Startup preflight tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import preflight


class KernelCheckTests(unittest.TestCase):

    def test_kernel_found_returns_none(self):
        """When the kernel binary exists, no error is returned."""
        with patch(
            "server_modules.preflight._check_kernel",
            return_value=None,
        ):
            self.assertIsNone(preflight._check_kernel())

    def test_kernel_missing_returns_error(self):
        """When the kernel binary is missing, an error string is returned."""
        with patch(
            "server_modules.preflight._check_kernel",
            return_value="kernel not found",
        ):
            self.assertEqual(preflight._check_kernel(), "kernel not found")


class KernelStalenessCheckTests(unittest.TestCase):
    """MAN-306: the Rust kernel is a compiled binary invoked over subprocess,
    never re-read from source. A binary built before a source fix (e.g. the
    2026-07-28 TERMINAL_RUN_STATUSES widening) keeps enforcing the old
    policy forever, silently, since nothing else ever notices. This check
    turns that drift into a boot-time failure instead."""

    def test_binary_missing_returns_error(self):
        with patch(
            "server_modules.rust_runtime_kernel_client.runtime_kernel_binary",
            return_value=None,
        ):
            error = preflight._check_kernel()
        self.assertIsNotNone(error)
        self.assertIn("not found", error)

    def test_fresh_binary_returns_none(self):
        with patch(
            "server_modules.rust_runtime_kernel_client.runtime_kernel_binary",
            return_value=MagicMock(),
        ), patch(
            "server_modules.rust_runtime_kernel_client.stale_kernel_source_file",
            return_value=None,
        ):
            self.assertIsNone(preflight._check_kernel())

    def test_stale_binary_returns_error_naming_the_newer_source_file(self):
        fake_source = MagicMock()
        fake_source.__str__.return_value = "empyralis-runtime-kernel/src/runtime_state_store.rs"
        with patch(
            "server_modules.rust_runtime_kernel_client.runtime_kernel_binary",
            return_value=MagicMock(),
        ), patch(
            "server_modules.rust_runtime_kernel_client.stale_kernel_source_file",
            return_value=fake_source,
        ), patch.dict(os.environ, {}, clear=True):
            error = preflight._check_kernel()
        self.assertIsNotNone(error)
        self.assertIn("stale", error)
        self.assertIn("runtime_state_store.rs", error)
        self.assertIn("cargo build", error)

    def test_stale_binary_allowed_via_env_var_bypass(self):
        with patch(
            "server_modules.rust_runtime_kernel_client.runtime_kernel_binary",
            return_value=MagicMock(),
        ), patch(
            "server_modules.rust_runtime_kernel_client.stale_kernel_source_file",
            return_value=MagicMock(),
        ), patch.dict(
            os.environ, {"EMPYRALIS_ALLOW_STALE_RUNTIME_KERNEL": "true"}, clear=True
        ):
            self.assertIsNone(preflight._check_kernel())


class LocalStackDatabaseUrlCheckTests(unittest.TestCase):
    """MAN-202 / MAN-268: a dev/test/local boot must have DATABASE_URL set
    explicitly, never inherited silently from whatever the environment
    happens to contain."""

    def test_missing_database_url_in_test_env_returns_error(self):
        with patch.dict(os.environ, {"ORION_ENV": "test"}, clear=True):
            error = preflight._check_local_stack_database_url()
        self.assertIsNotNone(error)
        self.assertIn("DATABASE_URL is not set", error)

    def test_missing_database_url_in_development_env_returns_error(self):
        with patch.dict(os.environ, {"ORION_ENV": "development"}, clear=True):
            error = preflight._check_local_stack_database_url()
        self.assertIsNotNone(error)

    def test_explicit_database_url_passes(self):
        with patch.dict(
            os.environ,
            {"ORION_ENV": "test", "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/empyralis_test"},
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_database_url())

    def test_unrecognized_env_token_is_left_alone(self):
        """No ORION_ENV/EMPYRALIS_DEPLOY_ENV/ENV/NODE_ENV at all resolves to
        "" -- not a recognized local/dev/test boot, so this check does not
        apply (existing SQLite-fallback behavior for that case is
        unchanged)."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(preflight._check_local_stack_database_url())

    def test_durable_runtime_required_skips_this_check(self):
        """beta/staging/production already require DATABASE_URL via
        _check_postgres() -- this check backs off rather than double-erroring."""
        with patch.dict(
            os.environ,
            {"ORION_ENV": "test", "ORION_REQUIRE_DURABLE_RUN_STATE": "1"},
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_database_url())

    def test_explicit_bypass_flag_skips_with_warning(self):
        with patch.dict(
            os.environ,
            {"ORION_ENV": "test", "EMPYRALIS_ALLOW_IMPLICIT_LOCAL_DATABASE_URL": "true"},
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_database_url())


class LocalStackLiveProviderSecretsCheckTests(unittest.TestCase):
    """2026-08-13 incident: a throwaway backend launched from the real repo
    root inherited the real ANTHROPIC_API_KEY/DEEPSEEK_API_KEY/
    EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN from that root's .env (DATABASE_URL
    was explicit, so LocalStackDatabaseUrlCheckTests above would have passed
    cleanly) and made real, billed, externally-visible calls before anyone
    noticed. This check closes that second hole."""

    def test_live_looking_api_key_in_test_env_returns_error(self):
        with patch.dict(
            os.environ,
            {"ORION_ENV": "test", "DEEPSEEK_API_KEY": "sk-live-abc123reallongrealkey"},
            clear=True,
        ):
            error = preflight._check_local_stack_live_provider_secrets()
        self.assertIsNotNone(error)
        self.assertIn("DEEPSEEK_API_KEY", error)
        self.assertIn("EMPYRALIS_ALLOW_LOCAL_STACK_LIVE_SECRETS", error)

    def test_live_looking_bot_token_in_development_env_returns_error(self):
        with patch.dict(
            os.environ,
            {"ORION_ENV": "development", "EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN": "8870032163:AAHXX7cY9VM4Oib4"},
            clear=True,
        ):
            error = preflight._check_local_stack_live_provider_secrets()
        self.assertIsNotNone(error)
        self.assertIn("EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN", error)

    def test_derives_the_variable_by_name_shape_not_a_hand_written_list(self):
        """A brand-new provider this codebase has never heard of (no
        ANTHROPIC/DEEPSEEK/TELEGRAM literal anywhere in the check) is still
        caught, because the check matches on the NAME's shape (`*_API_KEY`),
        never on an enumerated provider list."""
        with patch.dict(
            os.environ,
            {"ORION_ENV": "test", "BRAND_NEW_PROVIDER_API_KEY": "totally-real-live-value"},
            clear=True,
        ):
            error = preflight._check_local_stack_live_provider_secrets()
        self.assertIsNotNone(error)
        self.assertIn("BRAND_NEW_PROVIDER_API_KEY", error)

    def test_empty_value_is_not_live(self):
        with patch.dict(
            os.environ,
            {"ORION_ENV": "test", "DEEPSEEK_API_KEY": ""},
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_live_provider_secrets())

    def test_placeholder_shaped_value_is_not_live(self):
        with patch.dict(
            os.environ,
            {"ORION_ENV": "test", "DEEPSEEK_API_KEY": "sk-throwaway-audit-blocked"},
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_live_provider_secrets())

    def test_non_credential_shaped_name_is_left_alone(self):
        """DATABASE_URL, GOOGLE_OAUTH_CLIENT_ID etc. don't end in
        _API_KEY/_TOKEN and are out of scope for this check (DATABASE_URL
        has its own dedicated check above)."""
        with patch.dict(
            os.environ,
            {"ORION_ENV": "test", "DATABASE_URL": "postgresql://x", "SOME_OTHER_VALUE": "real-looking-secret"},
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_live_provider_secrets())

    def test_unrecognized_env_token_is_left_alone(self):
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "sk-live-abc123reallongrealkey"},
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_live_provider_secrets())

    def test_durable_runtime_required_skips_this_check(self):
        with patch.dict(
            os.environ,
            {
                "ORION_ENV": "test",
                "ORION_REQUIRE_DURABLE_RUN_STATE": "1",
                "DEEPSEEK_API_KEY": "sk-live-abc123reallongrealkey",
            },
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_live_provider_secrets())

    def test_explicit_bypass_flag_skips_with_warning(self):
        with patch.dict(
            os.environ,
            {
                "ORION_ENV": "test",
                "DEEPSEEK_API_KEY": "sk-live-abc123reallongrealkey",
                "EMPYRALIS_ALLOW_LOCAL_STACK_LIVE_SECRETS": "true",
            },
            clear=True,
        ):
            self.assertIsNone(preflight._check_local_stack_live_provider_secrets())

    def test_multiple_live_credentials_all_named_in_one_error(self):
        with patch.dict(
            os.environ,
            {
                "ORION_ENV": "test",
                "ANTHROPIC_API_KEY": "sk-ant-reallongrealkeyvalue",
                "DEEPSEEK_API_KEY": "sk-live-abc123reallongrealkey",
                "EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN": "8870032163:AAHXX7cY9VM4Oib4",
            },
            clear=True,
        ):
            error = preflight._check_local_stack_live_provider_secrets()
        self.assertIsNotNone(error)
        self.assertIn("ANTHROPIC_API_KEY", error)
        self.assertIn("DEEPSEEK_API_KEY", error)
        self.assertIn("EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN", error)


class StateHomeModuleLevelAssignmentTargetsTests(unittest.TestCase):
    """_module_level_assignment_targets is the precision layer that tells a
    module-level (import-time-baked, unsafe) constant apart from the same
    name resolved inside a function (call-time, safe) — the exact
    distinction Class 2 depends on."""

    def test_module_level_assignment_is_found(self):
        source = 'EMPYRALIS_STATE_HOME = Path(os.getenv("EMPYRALIS_STATE_HOME"))\n'
        self.assertEqual(
            preflight._module_level_assignment_targets(source),
            {"EMPYRALIS_STATE_HOME"},
        )

    def test_underscored_variant_is_found(self):
        source = '_STATE_HOME = Path(os.getenv("EMPYRALIS_STATE_HOME"))\n'
        self.assertEqual(preflight._module_level_assignment_targets(source), {"_STATE_HOME"})

    def test_function_scoped_assignment_is_not_found(self):
        # The safe shape — this is exactly what sage_telegram_hosted_
        # service.py's fix and mcp_server_auth.py's fix both moved to.
        source = (
            "def _state_dir():\n"
            "    EMPYRALIS_STATE_HOME = Path(os.getenv(\"EMPYRALIS_STATE_HOME\"))\n"
            "    return str(EMPYRALIS_STATE_HOME)\n"
        )
        self.assertEqual(preflight._module_level_assignment_targets(source), set())

    def test_syntax_error_returns_empty_set_not_a_crash(self):
        self.assertEqual(preflight._module_level_assignment_targets("def broken(:\n"), set())


class LocalStackStateHomeResolutionCheckTests(unittest.TestCase):
    """2026-08-14 incident: sage_telegram_hosted_service.py hardcoded
    ~/.empyralis/state and ignored EMPYRALIS_STATE_HOME entirely, so a
    throwaway stack with EMPYRALIS_STATE_HOME pointed at a fresh temp dir
    still silently loaded the founder's real hosted-Telegram pairing
    records. This check closes that third hole — a SOURCE-shaped leak
    neither the DATABASE_URL nor the live-secrets check above can see,
    since both only inspect os.environ."""

    def _write(self, tmp_dir: str, name: str, content: str) -> str:
        path = os.path.join(tmp_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_class_1_hardcoded_home_path_with_no_env_reference_is_caught(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            bad = self._write(
                tmp_dir,
                "totally_new_module.py",
                "import os\n_STATE_DIR = os.path.join(os.path.expanduser('~'), '.empyralis', 'state')\n",
            )
            with patch("server_modules.preflight._state_home_scan_targets", return_value=[bad]), \
                 patch.dict(os.environ, {"ORION_ENV": "test"}, clear=True):
                error = preflight._check_local_stack_state_home_resolution()
        self.assertIsNotNone(error)
        self.assertIn("totally_new_module.py", error)
        self.assertIn("CLASS 1", error)

    def test_class_1_is_not_flagged_once_the_env_var_is_referenced(self):
        # This is exactly the shape of the fix: reference the env var at
        # all, even sloppily, and it drops out of Class 1 (it may still be
        # Class 2, tested separately below).
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            fixed = self._write(
                tmp_dir,
                "fixed_module.py",
                "import os\n"
                "def _state_dir():\n"
                "    return os.getenv('EMPYRALIS_STATE_HOME') or os.path.join(os.path.expanduser('~'), '.empyralis', 'state')\n",
            )
            with patch("server_modules.preflight._state_home_scan_targets", return_value=[fixed]), \
                 patch.dict(os.environ, {"ORION_ENV": "test"}, clear=True):
                error = preflight._check_local_stack_state_home_resolution()
        self.assertIsNone(error)

    def test_class_2_new_module_level_bake_is_caught(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            bad = self._write(
                tmp_dir,
                "another_new_module.py",
                "import os\nfrom pathlib import Path\n"
                "EMPYRALIS_STATE_HOME = Path(os.getenv('EMPYRALIS_STATE_HOME', '/tmp'))\n",
            )
            with patch("server_modules.preflight._state_home_scan_targets", return_value=[bad]), \
                 patch.dict(os.environ, {"ORION_ENV": "test"}, clear=True):
                error = preflight._check_local_stack_state_home_resolution()
        self.assertIsNotNone(error)
        self.assertIn("another_new_module.py", error)
        self.assertIn("CLASS 2", error)
        self.assertIn("NEW, not on the", error)

    def test_class_2_known_exception_does_not_block_boot(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            known = self._write(
                tmp_dir,
                "auth.py",
                "import os\nfrom pathlib import Path\n"
                "EMPYRALIS_STATE_HOME = Path(os.getenv('EMPYRALIS_STATE_HOME', '/tmp'))\n",
            )
            with patch("server_modules.preflight._state_home_scan_targets", return_value=[known]), \
                 patch.dict(os.environ, {"ORION_ENV": "test"}, clear=True):
                error = preflight._check_local_stack_state_home_resolution()
        self.assertIsNone(error, "auth.py is a seeded, already-known Class 2 exception")

    def test_class_1_known_exception_does_not_block_boot(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            known = self._write(
                tmp_dir,
                "cli_companion_service.py",
                "from pathlib import Path\n_CONFIG_DIR = Path.home() / '.empyralis'\n",
            )
            with patch("server_modules.preflight._state_home_scan_targets", return_value=[known]), \
                 patch.dict(os.environ, {"ORION_ENV": "test"}, clear=True):
                error = preflight._check_local_stack_state_home_resolution()
        self.assertIsNone(error, "cli_companion_service.py is a seeded, already-known Class 1 exception")

    def test_durable_runtime_required_skips_this_check(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            bad = self._write(
                tmp_dir,
                "would_be_flagged.py",
                "import os\n_STATE_DIR = os.path.join(os.path.expanduser('~'), '.empyralis', 'state')\n",
            )
            with patch("server_modules.preflight._state_home_scan_targets", return_value=[bad]), \
                 patch.dict(
                     os.environ,
                     {"ORION_ENV": "test", "ORION_REQUIRE_DURABLE_RUN_STATE": "1"},
                     clear=True,
                 ):
                self.assertIsNone(preflight._check_local_stack_state_home_resolution())

    def test_unrecognized_env_token_is_left_alone(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            bad = self._write(
                tmp_dir,
                "would_be_flagged.py",
                "import os\n_STATE_DIR = os.path.join(os.path.expanduser('~'), '.empyralis', 'state')\n",
            )
            with patch("server_modules.preflight._state_home_scan_targets", return_value=[bad]), \
                 patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(preflight._check_local_stack_state_home_resolution())

    def test_the_real_source_tree_is_clean_today(self):
        """Regression guard: proves both fixes (sage_telegram_hosted_service.py,
        mcp_server_auth.py) actually removed the violations, and that the two
        remaining known exceptions plus the dozen Class 2 exceptions are the
        ONLY thing keeping this check quiet — not a scan that silently found
        nothing because it never ran. Scans the real repo tree, unmocked."""
        with patch.dict(os.environ, {"ORION_ENV": "test"}, clear=True):
            targets = preflight._state_home_scan_targets()
            self.assertGreater(len(targets), 100, "sanity: the scan actually found the real source tree")
            error = preflight._check_local_stack_state_home_resolution()
        self.assertIsNone(error, error)


class RemovedKnowledgeRagConfigCheckTests(unittest.TestCase):
    """The embeddings/RAG knowledge pipeline was removed 2026-08-08. Its env
    knobs have no reader left, so a boot that still sets one must FAIL rather
    than ignore it (CLAUDE.md: removing a provider/route makes stale config
    fail loudly instead of falling through to a default)."""

    def test_clean_environment_passes(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(preflight._check_removed_knowledge_rag_config())

    def test_each_removed_var_is_rejected_on_its_own(self):
        # Driven off the module's own tuple rather than a hand-copied sample:
        # a hand-written list goes stale the moment someone adds a var, and
        # this failure is silent by construction.
        self.assertTrue(preflight._REMOVED_KNOWLEDGE_RAG_ENV_VARS)
        for name in preflight._REMOVED_KNOWLEDGE_RAG_ENV_VARS:
            with self.subTest(env_var=name):
                with patch.dict(os.environ, {name: "something"}, clear=True):
                    error = preflight._check_removed_knowledge_rag_config()
                self.assertIsNotNone(error, f"{name} must fail the boot, not be ignored")
                self.assertIn(name, error)

    def test_blank_value_is_not_treated_as_configured(self):
        with patch.dict(os.environ, {"EMPYRALIS_RAG_EMBEDDING_BACKEND": "   "}, clear=True):
            self.assertIsNone(preflight._check_removed_knowledge_rag_config())

    def test_error_names_every_offending_var_not_just_the_first(self):
        with patch.dict(
            os.environ,
            {"EMPYRALIS_RAG_EMBEDDING_BACKEND": "hash", "OPENAI_EMBEDDINGS_URL": "https://x/y"},
            clear=True,
        ):
            error = preflight._check_removed_knowledge_rag_config()
        self.assertIsNotNone(error)
        self.assertIn("EMPYRALIS_RAG_EMBEDDING_BACKEND", error)
        self.assertIn("OPENAI_EMBEDDINGS_URL", error)

    def test_error_states_uploaded_files_are_unaffected(self):
        # The operator's first question on seeing this failure is "did I just
        # lose the files people uploaded?" — the answer belongs in the message.
        with patch.dict(os.environ, {"EMPYRALIS_RAG_LANCEDB_URI": "/tmp/x"}, clear=True):
            error = preflight._check_removed_knowledge_rag_config()
        self.assertIn("Uploaded knowledge files are unaffected", error)


class RemovedStripeBillingConfigCheckTests(unittest.TestCase):
    """The payment processor was replaced 2026-08-20 (Polar, not Stripe --
    see CLAUDE.md's "Payment processor is Polar, not Stripe"). Its env knobs
    have no reader left (and never did -- billing_service.py has always
    read its own env vars directly), so a boot that still sets one must
    FAIL rather than ignore it."""

    def test_clean_environment_passes(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(preflight._check_removed_stripe_billing_config())

    def test_each_removed_var_is_rejected_on_its_own(self):
        # Driven off the module's own tuple rather than a hand-copied
        # sample -- a hand-written list goes stale the moment someone
        # renames a var, and this failure is silent by construction.
        self.assertTrue(preflight._REMOVED_STRIPE_BILLING_ENV_VARS)
        for name in preflight._REMOVED_STRIPE_BILLING_ENV_VARS:
            with self.subTest(env_var=name):
                with patch.dict(os.environ, {name: "something"}, clear=True):
                    error = preflight._check_removed_stripe_billing_config()
                self.assertIsNotNone(error, f"{name} must fail the boot, not be ignored")
                self.assertIn(name, error)

    def test_blank_value_is_not_treated_as_configured(self):
        with patch.dict(os.environ, {"EMPYRALIS_STRIPE_SECRET_KEY": "   "}, clear=True):
            self.assertIsNone(preflight._check_removed_stripe_billing_config())

    def test_error_names_every_offending_var_not_just_the_first(self):
        with patch.dict(
            os.environ,
            {"EMPYRALIS_BILLING_PROVIDER": "stripe", "EMPYRALIS_STRIPE_SECRET_KEY": "sk_live_x"},
            clear=True,
        ):
            error = preflight._check_removed_stripe_billing_config()
        self.assertIsNotNone(error)
        self.assertIn("EMPYRALIS_BILLING_PROVIDER", error)
        self.assertIn("EMPYRALIS_STRIPE_SECRET_KEY", error)

    def test_error_names_the_real_polar_variables_to_set_instead(self):
        # The operator's next question after "what broke" is "what do I set
        # instead" -- the answer belongs in the message, not a separate doc.
        with patch.dict(os.environ, {"EMPYRALIS_STRIPE_WEBHOOK_SECRET": "whsec_x"}, clear=True):
            error = preflight._check_removed_stripe_billing_config()
        self.assertIn("EMPYRALIS_POLAR_ACCESS_TOKEN", error)


class PlatformCreditKeyCheckTests(unittest.TestCase):
    """preflight.py's advisory DeepSeek ``/user/balance`` health check — the
    one preflight step that makes a real outbound HTTPS request.

    It had no direct coverage at all: the only thing reaching it was
    PreflightRunnerTests below, which calls run_preflight_checks() for
    unrelated reasons and therefore fired a REAL, billed request at
    api.deepseek.com on any machine with a key in its environment. These
    tests exercise the check itself with the HTTP response mocked, including
    the failure shapes, so the branch that exists to shout "PLATFORM-CREDIT
    KEY DEAD" is actually verified rather than merely executed.
    """

    def _run_check(self, *, api_key="sk-platform-test", http_return=None, http_side_effect=None, env=None):
        import asyncio

        resolution = MagicMock()
        resolution.value = api_key
        http = MagicMock(return_value=http_return, side_effect=http_side_effect)
        with patch.dict(os.environ, env or {}, clear=True), \
             patch(
                 "server_modules.secrets_broker.resolve_hosted_provider_secret",
                 return_value=resolution,
             ) as broker, \
             patch("server_modules.runtime_common.http_json_request", new=http):
            with self.assertLogs(preflight.LOGGER, level="INFO") as captured:
                asyncio.run(preflight._check_platform_credit_keys())
        return http, broker, captured.output

    def test_healthy_key_makes_one_balance_request_and_does_not_shout(self):
        http, _broker, logs = self._run_check(
            http_return={"status": 200, "json": {"is_available": True}},
        )
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.args[0], "https://api.deepseek.com/user/balance")
        self.assertEqual(http.call_args.kwargs["method"], "GET")
        self.assertIn("Bearer sk-platform-test", http.call_args.kwargs["headers"]["Authorization"])
        self.assertFalse([line for line in logs if line.startswith("CRITICAL")])
        self.assertTrue([line for line in logs if "healthy" in line])

    def test_empty_balance_is_reported_as_a_dead_platform_credit_key(self):
        _http, _broker, logs = self._run_check(
            http_return={"status": 200, "json": {"is_available": False}},
        )
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM-CREDIT KEY DEAD", critical[0])

    def test_rejected_key_is_reported_as_a_dead_platform_credit_key(self):
        _http, _broker, logs = self._run_check(
            http_return={"status": 401, "json": {"error": "invalid api key"}},
        )
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM-CREDIT KEY DEAD", critical[0])

    def test_transport_failure_warns_but_never_claims_the_key_is_dead(self):
        """A network problem on OUR side is not evidence the upstream account
        is empty — mislabelling it would send an operator chasing a billing
        problem that doesn't exist."""
        _http, _broker, logs = self._run_check(
            http_side_effect=OSError("connection reset"),
        )
        self.assertFalse([line for line in logs if line.startswith("CRITICAL")])
        self.assertTrue([line for line in logs if "network/transport" in line])

    def test_no_configured_key_makes_no_request_at_all(self):
        http, _broker, logs = self._run_check(api_key="")
        self.assertEqual(http.call_count, 0)
        self.assertTrue([line for line in logs if "no DeepSeek platform-credit key" in line])

    def test_skip_flag_makes_no_request_and_does_not_resolve_a_secret(self):
        http, broker, logs = self._run_check(
            env={"EMPYRALIS_SKIP_PLATFORM_CREDIT_CHECK": "true"},
        )
        self.assertEqual(http.call_count, 0)
        self.assertEqual(broker.call_count, 0)
        self.assertTrue([line for line in logs if "skipped" in line])


class PlatformDigitalOceanTokenCheckTests(unittest.TestCase):
    """MAN-131: preflight.py's advisory DigitalOcean ``/v2/account`` health
    check for the platform-owned token that decides which DO account
    vps_provisioning_service.provision_vps creates a droplet in. Same shape
    as PlatformCreditKeyCheckTests above, and for the same reason: this is
    the one preflight step that makes a real outbound HTTPS request, so it
    needs the HTTP response mocked rather than left to run for real."""

    def _run_check(self, *, token="dop_v1_platform_test", http_return=None, http_side_effect=None, env=None):
        import asyncio

        resolution = MagicMock()
        resolution.value = token
        http = MagicMock(return_value=http_return, side_effect=http_side_effect)
        with patch.dict(os.environ, env or {}, clear=True), \
             patch(
                 "server_modules.secrets_broker.resolve_hosted_provider_secret",
                 return_value=resolution,
             ) as broker, \
             patch("server_modules.runtime_common.http_json_request", new=http):
            with self.assertLogs(preflight.LOGGER, level="INFO") as captured:
                asyncio.run(preflight._check_platform_digitalocean_token())
        return http, broker, captured.output

    def test_healthy_token_makes_one_account_request_and_does_not_shout(self):
        http, broker, logs = self._run_check(
            http_return={"status": 200, "json": {"account": {"uuid": "acct-1", "status": "active"}}},
        )
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.args[0], "https://api.digitalocean.com/v2/account")
        self.assertEqual(http.call_args.kwargs["method"], "GET")
        self.assertIn("Bearer dop_v1_platform_test", http.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual(broker.call_args.kwargs.get("provider_id"), "digitalocean")
        self.assertFalse([line for line in logs if line.startswith("CRITICAL")])
        self.assertTrue([line for line in logs if "healthy" in line])

    def test_unauthorized_token_is_reported_as_dead(self):
        _http, _broker, logs = self._run_check(
            http_return={"status": 401, "json": {"id": "Unauthorized", "message": "Unable to authenticate you."}},
        )
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM DIGITALOCEAN TOKEN DEAD", critical[0])

    def test_malformed_200_with_no_account_body_is_reported_as_dead(self):
        _http, _broker, logs = self._run_check(
            http_return={"status": 200, "json": {}},
        )
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM DIGITALOCEAN TOKEN DEAD", critical[0])

    def test_transport_failure_warns_but_never_claims_the_token_is_dead(self):
        """A network problem on OUR side (or DigitalOcean having a bad
        morning) is not evidence the token itself is bad — mislabelling it
        would send an operator chasing a credential problem that doesn't
        exist, and CLAUDE.md is explicit that a boot must never depend on a
        cloud provider's uptime."""
        _http, _broker, logs = self._run_check(
            http_side_effect=OSError("connection reset"),
        )
        self.assertFalse([line for line in logs if line.startswith("CRITICAL")])
        self.assertTrue([line for line in logs if "network/transport" in line])

    def test_no_configured_token_makes_no_request_at_all(self):
        http, _broker, logs = self._run_check(token="")
        self.assertEqual(http.call_count, 0)
        self.assertTrue([line for line in logs if "no platform DigitalOcean token" in line])

    def test_skip_flag_makes_no_request_and_does_not_resolve_a_secret(self):
        http, broker, logs = self._run_check(
            env={"EMPYRALIS_SKIP_PLATFORM_DIGITALOCEAN_CHECK": "true"},
        )
        self.assertEqual(http.call_count, 0)
        self.assertEqual(broker.call_count, 0)
        self.assertTrue([line for line in logs if "skipped" in line])

    def test_never_boot_blocking_even_when_dead(self):
        """The whole point of this check: a dead token must never surface in
        the errors list run_preflight_checks() returns, only in the logs."""
        import asyncio

        resolution = MagicMock()
        resolution.value = "dop_v1_platform_test"
        http = MagicMock(return_value={"status": 401, "json": {"id": "Unauthorized"}})
        with patch(
            "server_modules.secrets_broker.resolve_hosted_provider_secret",
            return_value=resolution,
        ), patch("server_modules.runtime_common.http_json_request", new=http):
            result = asyncio.run(preflight._check_platform_digitalocean_token())
        self.assertIsNone(result)  # advisory: nothing to append to errors[]


class PlatformGoogleOperatorCredentialCheckTests(unittest.TestCase):
    """2026-08-13 launch-readiness audit: Google Cloud VPS provisioning had
    NO preflight check at all — this closes that gap, mirroring
    PlatformDigitalOceanTokenCheckTests above. Same reason that class mocks
    the HTTP call rather than leaving it to run for real: this is the one
    other preflight step that makes a genuine outbound HTTPS request."""

    _ALL_FOUR_ENV = {
        "GOOGLE_CLOUD_CLIENT_ID": "client-id-test",
        "GOOGLE_CLOUD_CLIENT_SECRET": "client-secret-test",
        "GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL": "operator@empyralis-provisioner.iam.gserviceaccount.com",
        "GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN": "1//operator-refresh-token-test",
    }

    def _run_check(self, *, env_overrides=None, http_return=None, http_side_effect=None, skip=False):
        import asyncio

        env = dict(self._ALL_FOUR_ENV)
        if env_overrides:
            env.update(env_overrides)
        if skip:
            env["EMPYRALIS_SKIP_PLATFORM_GOOGLE_CHECK"] = "true"
        http = MagicMock(return_value=http_return, side_effect=http_side_effect)
        with patch.dict(os.environ, env, clear=True), \
             patch("server_modules.runtime_common.http_json_request", new=http):
            with self.assertLogs(preflight.LOGGER, level="INFO") as captured:
                asyncio.run(preflight._check_platform_google_operator_credentials())
        return http, captured.output

    def test_healthy_refresh_makes_one_token_request_and_does_not_shout(self):
        http, logs = self._run_check(
            http_return={"status": 200, "json": {"access_token": "ya29.fake", "expires_in": 3599}},
        )
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.args[0], "https://oauth2.googleapis.com/token")
        self.assertEqual(http.call_args.kwargs["method"], "POST")
        payload = http.call_args.kwargs["payload"]
        self.assertEqual(payload["grant_type"], "refresh_token")
        self.assertEqual(payload["client_id"], "client-id-test")
        self.assertEqual(payload["client_secret"], "client-secret-test")
        self.assertEqual(payload["refresh_token"], "1//operator-refresh-token-test")
        self.assertFalse([line for line in logs if line.startswith("CRITICAL")])
        self.assertTrue([line for line in logs if "healthy" in line])

    def test_rejected_refresh_token_is_reported_as_dead(self):
        _http, logs = self._run_check(
            http_return={"status": 400, "json": {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}},
        )
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM GOOGLE CLOUD OPERATOR CREDENTIALS DEAD", critical[0])

    def test_malformed_200_with_no_access_token_is_reported_as_dead(self):
        _http, logs = self._run_check(
            http_return={"status": 200, "json": {}},
        )
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM GOOGLE CLOUD OPERATOR CREDENTIALS DEAD", critical[0])

    def test_transport_failure_warns_but_never_claims_the_credential_is_dead(self):
        """Same reasoning as the DigitalOcean check's identical test: a
        network problem is not evidence the credential itself is bad."""
        _http, logs = self._run_check(
            http_side_effect=OSError("connection reset"),
        )
        self.assertFalse([line for line in logs if line.startswith("CRITICAL")])
        self.assertTrue([line for line in logs if "network/transport" in line])

    def test_any_one_missing_env_var_makes_no_request_at_all(self):
        """All FOUR must be present before the live call is even attempted —
        unlike DigitalOcean's single token, a partially-configured Google
        setup (e.g. the OAuth app but not the operator identity) must not
        silently attempt a call that can only ever fail."""
        for missing_key in self._ALL_FOUR_ENV:
            with self.subTest(missing=missing_key):
                http, logs = self._run_check(env_overrides={missing_key: ""})
                self.assertEqual(http.call_count, 0)
                self.assertTrue([line for line in logs if "is not configured" in line])
                self.assertTrue([line for line in logs if missing_key in line])

    def test_skip_flag_makes_no_request_at_all(self):
        http, logs = self._run_check(skip=True)
        self.assertEqual(http.call_count, 0)
        self.assertTrue([line for line in logs if "skipped" in line])

    def test_never_boot_blocking_even_when_dead(self):
        """The whole point of this check: a dead credential must never
        surface in the errors list run_preflight_checks() returns, only in
        the logs."""
        import asyncio

        http = MagicMock(return_value={"status": 400, "json": {"error": "invalid_grant"}})
        with patch.dict(os.environ, self._ALL_FOUR_ENV, clear=True), \
             patch("server_modules.runtime_common.http_json_request", new=http):
            result = asyncio.run(preflight._check_platform_google_operator_credentials())
        self.assertIsNone(result)  # advisory: nothing to append to errors[]


class PlatformEmailProviderKeyCheckTests(unittest.TestCase):
    """MAN-343: preflight.py's advisory Resend liveness check for
    EMAIL_PROVIDER_API_KEY. Same shape as PlatformDigitalOceanTokenCheckTests
    above and for the same reason: this is the one preflight step besides
    those three that makes a real outbound HTTPS request, so it needs the
    HTTP response mocked rather than left to run for real. Unlike the
    DigitalOcean/DeepSeek/Google checks, this key is read via bare os.environ
    (matching email_provider_service._api_key()'s own pattern), never the
    secrets broker — so there is no broker mock here, only the env and the
    HTTP call."""

    def _run_check(self, *, http_return=None, http_side_effect=None, env=None):
        import asyncio

        http = MagicMock(return_value=http_return, side_effect=http_side_effect)
        with patch.dict(os.environ, env or {}, clear=True), \
             patch("server_modules.runtime_common.http_json_request", new=http):
            with self.assertLogs(preflight.LOGGER, level="INFO") as captured:
                asyncio.run(preflight._check_platform_email_provider_key())
        return http, captured.output

    def test_healthy_key_makes_one_send_request_to_the_test_address_and_does_not_shout(self):
        http, logs = self._run_check(
            env={"EMAIL_PROVIDER_API_KEY": "re_test_platform_key"},
            http_return={"status": 200, "json": {"id": "email-abc123"}},
        )
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.args[0], "https://api.resend.com/emails")
        self.assertEqual(http.call_args.kwargs["method"], "POST")
        self.assertIn("Bearer re_test_platform_key", http.call_args.kwargs["headers"]["Authorization"])
        # Never a real inbox -- see the check's own docstring on why this
        # specific address is the only safe liveness probe Resend offers.
        self.assertEqual(http.call_args.kwargs["payload"]["to"], ["delivered@resend.dev"])
        self.assertFalse([line for line in logs if line.startswith("CRITICAL")])
        self.assertTrue([line for line in logs if "healthy" in line])

    def test_unauthorized_key_is_reported_as_dead(self):
        _http, logs = self._run_check(
            env={"EMAIL_PROVIDER_API_KEY": "re_revoked"},
            http_return={"status": 401, "json": {"name": "restricted_api_key", "message": "This API key is not valid."}},
        )
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM EMAIL PROVIDER KEY DEAD", critical[0])

    def test_malformed_200_with_no_id_is_reported_as_dead(self):
        _http, logs = self._run_check(
            env={"EMAIL_PROVIDER_API_KEY": "re_test_platform_key"},
            http_return={"status": 200, "json": {}},
        )
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM EMAIL PROVIDER KEY DEAD", critical[0])

    def test_transport_failure_warns_but_never_claims_the_key_is_dead(self):
        """Same reasoning as the DigitalOcean check's identical test: a
        network problem is not evidence the key itself is bad."""
        _http, logs = self._run_check(
            env={"EMAIL_PROVIDER_API_KEY": "re_test_platform_key"},
            http_side_effect=OSError("connection reset"),
        )
        self.assertFalse([line for line in logs if line.startswith("CRITICAL")])
        self.assertTrue([line for line in logs if "network/transport" in line])

    def test_no_configured_key_makes_no_request_and_is_reported_absent_not_dead(self):
        """Absent and dead are different problems with different fixes
        (the ticket's own framing) -- an absent key must never be reported
        with the same "DEAD" wording a revoked key gets."""
        http, logs = self._run_check(env={})
        self.assertEqual(http.call_count, 0)
        critical = [line for line in logs if line.startswith("CRITICAL")]
        self.assertEqual(len(critical), 1)
        self.assertIn("PLATFORM EMAIL PROVIDER KEY ABSENT", critical[0])
        self.assertNotIn("DEAD", critical[0])

    def test_skip_flag_makes_no_request_at_all(self):
        http, logs = self._run_check(
            env={
                "EMAIL_PROVIDER_API_KEY": "re_test_platform_key",
                "EMPYRALIS_SKIP_PLATFORM_EMAIL_PROVIDER_CHECK": "true",
            },
        )
        self.assertEqual(http.call_count, 0)
        self.assertTrue([line for line in logs if "skipped" in line])

    def test_never_boot_blocking_even_when_dead(self):
        """The whole point of this check: a dead key must never surface in
        the errors list run_preflight_checks() returns, only in the logs."""
        import asyncio

        http = MagicMock(return_value={"status": 401, "json": {"message": "invalid"}})
        with patch.dict(os.environ, {"EMAIL_PROVIDER_API_KEY": "re_test_platform_key"}, clear=True), \
             patch("server_modules.runtime_common.http_json_request", new=http):
            result = asyncio.run(preflight._check_platform_email_provider_key())
        self.assertIsNone(result)  # advisory: nothing to append to errors[]


class PreflightRunnerTests(unittest.TestCase):
    """run_preflight_checks() composition. Step 6 (the advisory DeepSeek
    balance check), step 7 (the advisory platform DigitalOcean token
    check), step 8 (the advisory platform Google Cloud operator credential
    check), and step 9 (the advisory platform email provider key check) are
    mocked out in every test here because none is the subject: these assert
    which checks run and how their errors are collected. Left unmocked,
    step 6 reached api.deepseek.com for real, step 7 would reach
    api.digitalocean.com for real, step 8 would reach oauth2.googleapis.com
    for real, and step 9 would reach api.resend.com for real — see
    PlatformCreditKeyCheckTests / PlatformDigitalOceanTokenCheckTests /
    PlatformGoogleOperatorCredentialCheckTests / PlatformEmailProviderKeyCheckTests
    above for their own coverage."""

    @staticmethod
    def _no_platform_credit_call():
        return patch(
            "server_modules.preflight._check_platform_credit_keys",
            new=AsyncMock(return_value=None),
        )

    @staticmethod
    def _no_platform_digitalocean_call():
        return patch(
            "server_modules.preflight._check_platform_digitalocean_token",
            new=AsyncMock(return_value=None),
        )

    @staticmethod
    def _no_platform_google_call():
        return patch(
            "server_modules.preflight._check_platform_google_operator_credentials",
            new=AsyncMock(return_value=None),
        )

    @staticmethod
    def _no_platform_email_call():
        return patch(
            "server_modules.preflight._check_platform_email_provider_key",
            new=AsyncMock(return_value=None),
        )

    def test_all_passed_returns_empty_list(self):
        """When all checks pass, errors list is empty."""
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_local_stack_live_provider_secrets", return_value=None), \
                 patch("server_modules.preflight._check_removed_knowledge_rag_config", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value=None), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value=None)), \
                 patch("server_modules.preflight._check_redis", new=AsyncMock(return_value=None)), \
                 self._no_platform_credit_call(), \
                 self._no_platform_digitalocean_call(), \
                 self._no_platform_google_call(), \
                 self._no_platform_email_call():
                return await preflight.run_preflight_checks()
        import asyncio
        errors = asyncio.run(_run())
        self.assertEqual(errors, [])

    def test_failures_are_collected(self):
        """All failed checks appear in the error list."""
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_local_stack_live_provider_secrets", return_value=None), \
                 patch("server_modules.preflight._check_removed_knowledge_rag_config", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value="no kernel"), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value="no pg")), \
                 patch("server_modules.preflight._check_redis", new=AsyncMock(return_value=None)), \
                 self._no_platform_credit_call(), \
                 self._no_platform_digitalocean_call(), \
                 self._no_platform_google_call(), \
                 self._no_platform_email_call():
                return await preflight.run_preflight_checks()
        import asyncio
        errors = asyncio.run(_run())
        self.assertEqual(len(errors), 2)
        self.assertIn("no kernel", errors[0])
        self.assertIn("no pg", errors[1])

    def test_redis_skipped_when_env_set(self):
        """When EMPYRALIS_SKIP_REDIS_CHECK is true, Redis is not checked."""
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_local_stack_live_provider_secrets", return_value=None), \
                 patch("server_modules.preflight._check_removed_knowledge_rag_config", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value=None), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value=None)), \
                 self._no_platform_credit_call(), \
                 self._no_platform_digitalocean_call(), \
                 self._no_platform_google_call(), \
                 self._no_platform_email_call():
                with patch.dict(os.environ, {"EMPYRALIS_SKIP_REDIS_CHECK": "true"}):
                    return await preflight.run_preflight_checks()
        import asyncio
        errors = asyncio.run(_run())
        self.assertEqual(len(errors), 0)

    def test_a_dead_platform_digitalocean_token_never_appears_in_errors(self):
        """The whole point of step 7 being advisory: run_preflight_checks()
        must still return an empty list even when the DigitalOcean account
        check itself would report CRITICAL, because that check is never
        appended to errors — only logged. This drives the REAL
        _check_platform_digitalocean_token (mocking only its HTTP call and
        secret resolution), unlike the other tests in this class. Step 8
        (Google) and step 9 (email) are left to run for real too — with
        their env vars absent, each takes its own early "not configured"
        exit and makes no HTTP call, which is exactly what
        http.call_count == 1 below proves: the DigitalOcean call is the
        ONLY one made. (Left un-popped, step 9 would share this same
        http_json_request mock and, seeing status 401 with a truthy "id"
        value, misread it as ITS OWN dead-key signal — a false CRITICAL
        for a check that never should have run at all here.)"""
        resolution = MagicMock()
        resolution.value = "dop_v1_platform_test"
        http = MagicMock(return_value={"status": 401, "json": {"id": "Unauthorized"}})
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_local_stack_live_provider_secrets", return_value=None), \
                 patch("server_modules.preflight._check_removed_knowledge_rag_config", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value=None), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value=None)), \
                 patch("server_modules.preflight._check_redis", new=AsyncMock(return_value=None)), \
                 self._no_platform_credit_call(), \
                 patch("server_modules.secrets_broker.resolve_hosted_provider_secret", return_value=resolution), \
                 patch("server_modules.runtime_common.http_json_request", new=http):
                # Determinism regardless of the developer's own shell — same
                # reasoning as test_provision_vps_platform_path_off_without_token
                # in test_vps_provisioning_service.py. patch.dict restores
                # whatever these were (set or absent) on exit even though
                # they're deleted, not just overwritten, inside the block.
                with patch.dict(os.environ, {}, clear=False):
                    for key in (
                        "GOOGLE_CLOUD_CLIENT_ID",
                        "GOOGLE_CLOUD_CLIENT_SECRET",
                        "GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL",
                        "GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN",
                        "EMAIL_PROVIDER_API_KEY",
                    ):
                        os.environ.pop(key, None)
                    return await preflight.run_preflight_checks()
        import asyncio
        errors = asyncio.run(_run())
        self.assertEqual(errors, [])
        self.assertEqual(http.call_count, 1)


class FailOnPreflightErrorsTests(unittest.TestCase):

    def test_empty_errors_does_not_raise(self):
        preflight.fail_on_preflight_errors([])

    def test_non_empty_errors_raises_preflight_error(self):
        with self.assertRaises(preflight.PreflightError) as ctx:
            preflight.fail_on_preflight_errors(["missing kernel", "no database"])
        self.assertIn("2 preflight check", str(ctx.exception))
        self.assertIn("missing kernel", str(ctx.exception))
        self.assertIn("no database", str(ctx.exception))


class RedactedDsnTests(unittest.TestCase):

    def test_password_is_redacted(self):
        result = preflight._redacted_dsn("postgresql://user:secret@localhost:5432/db")
        self.assertIn("user:***@localhost", result)
        self.assertNotIn("secret", result)

    def test_no_password_preserved(self):
        result = preflight._redacted_dsn("postgresql://user@localhost:5432/db")
        self.assertEqual(result, "postgresql://user@localhost:5432/db")

    def test_no_at_symbol_preserved(self):
        result = preflight._redacted_dsn("localhost:5432")
        self.assertEqual(result, "localhost:5432")


if __name__ == "__main__":
    unittest.main()
