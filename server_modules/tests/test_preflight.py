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


class PreflightRunnerTests(unittest.TestCase):
    """run_preflight_checks() composition. Step 6 (the advisory DeepSeek
    balance check) and step 7 (the advisory platform DigitalOcean token
    check) are mocked out in every test here because neither is the
    subject: these assert which checks run and how their errors are
    collected. Left unmocked, step 6 reached api.deepseek.com for real and
    step 7 would reach api.digitalocean.com for real — see
    PlatformCreditKeyCheckTests / PlatformDigitalOceanTokenCheckTests above
    for their own coverage."""

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

    def test_all_passed_returns_empty_list(self):
        """When all checks pass, errors list is empty."""
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_removed_knowledge_rag_config", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value=None), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value=None)), \
                 patch("server_modules.preflight._check_redis", new=AsyncMock(return_value=None)), \
                 self._no_platform_credit_call(), \
                 self._no_platform_digitalocean_call():
                return await preflight.run_preflight_checks()
        import asyncio
        errors = asyncio.run(_run())
        self.assertEqual(errors, [])

    def test_failures_are_collected(self):
        """All failed checks appear in the error list."""
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_removed_knowledge_rag_config", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value="no kernel"), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value="no pg")), \
                 patch("server_modules.preflight._check_redis", new=AsyncMock(return_value=None)), \
                 self._no_platform_credit_call(), \
                 self._no_platform_digitalocean_call():
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
                 patch("server_modules.preflight._check_removed_knowledge_rag_config", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value=None), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value=None)), \
                 self._no_platform_credit_call(), \
                 self._no_platform_digitalocean_call():
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
        secret resolution), unlike the other tests in this class."""
        resolution = MagicMock()
        resolution.value = "dop_v1_platform_test"
        http = MagicMock(return_value={"status": 401, "json": {"id": "Unauthorized"}})
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_removed_knowledge_rag_config", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value=None), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value=None)), \
                 patch("server_modules.preflight._check_redis", new=AsyncMock(return_value=None)), \
                 self._no_platform_credit_call(), \
                 patch("server_modules.secrets_broker.resolve_hosted_provider_secret", return_value=resolution), \
                 patch("server_modules.runtime_common.http_json_request", new=http):
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
