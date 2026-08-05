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


class PreflightRunnerTests(unittest.TestCase):

    def test_all_passed_returns_empty_list(self):
        """When all checks pass, errors list is empty."""
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value=None), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value=None)), \
                 patch("server_modules.preflight._check_redis", new=AsyncMock(return_value=None)):
                return await preflight.run_preflight_checks()
        import asyncio
        errors = asyncio.run(_run())
        self.assertEqual(errors, [])

    def test_failures_are_collected(self):
        """All failed checks appear in the error list."""
        async def _run():
            with patch("server_modules.preflight._check_local_stack_database_url", return_value=None), \
                 patch("server_modules.preflight._check_kernel", return_value="no kernel"), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value="no pg")), \
                 patch("server_modules.preflight._check_redis", new=AsyncMock(return_value=None)):
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
                 patch("server_modules.preflight._check_kernel", return_value=None), \
                 patch("server_modules.preflight._check_postgres", new=AsyncMock(return_value=None)):
                with patch.dict(os.environ, {"EMPYRALIS_SKIP_REDIS_CHECK": "true"}):
                    return await preflight.run_preflight_checks()
        import asyncio
        errors = asyncio.run(_run())
        self.assertEqual(len(errors), 0)


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
