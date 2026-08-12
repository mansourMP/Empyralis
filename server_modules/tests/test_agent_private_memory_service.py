"""Tests for agent_private_memory_service.py -- the sync-facing wrapper that
adds tenant resolution, secret redaction, and a size cap on top of
agent_private_memory_repository.py. No live Postgres required: every test
here patches the repository layer and asserts CALL COUNTS, so "this write
was refused" is distinguished from "this write silently never reached
storage" -- CLAUDE.md's own "a call-count assertion is the only thing that
tells a double-write from a correct one" reasoning, applied here to refusal
paths (an assertion that a ValueError was raised is not proof nothing was
written unless the downstream call count is also checked).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_private_memory_service as service


def _async_return(value):
    async def _coro(*_args, **_kwargs):
        return value

    return _coro


class RequiredIdentityTests(unittest.TestCase):
    """user_id and agent_install_id are required, no default, and a
    refusal must never reach the repository layer."""

    def test_write_without_user_id_raises_and_never_calls_repository(self) -> None:
        with patch(
            "server_modules.agent_private_memory_repository.upsert_private_note",
            new_callable=AsyncMock,
        ) as mocked_upsert:
            with self.assertRaises(ValueError):
                service.write_private_memory_note(
                    "ws-1", agent_install_id="agent-1", user_id="", content="likes concise updates",
                )
            self.assertEqual(mocked_upsert.await_count, 0)

    def test_write_without_agent_install_id_raises_and_never_calls_repository(self) -> None:
        with patch(
            "server_modules.agent_private_memory_repository.upsert_private_note",
            new_callable=AsyncMock,
        ) as mocked_upsert:
            with self.assertRaises(ValueError):
                service.write_private_memory_note(
                    "ws-1", agent_install_id="", user_id="user-a", content="likes concise updates",
                )
            self.assertEqual(mocked_upsert.await_count, 0)

    def test_read_without_user_id_raises_and_never_calls_repository(self) -> None:
        with patch(
            "server_modules.agent_private_memory_repository.get_private_note",
            new_callable=AsyncMock,
        ) as mocked_get:
            with self.assertRaises(ValueError):
                service.get_private_memory_note("ws-1", agent_install_id="agent-1", user_id="")
            self.assertEqual(mocked_get.await_count, 0)

    def test_empty_content_raises_and_never_calls_repository(self) -> None:
        with patch(
            "server_modules.agent_private_memory_repository.upsert_private_note",
            new_callable=AsyncMock,
        ) as mocked_upsert:
            with self.assertRaises(ValueError):
                service.write_private_memory_note(
                    "ws-1", agent_install_id="agent-1", user_id="user-a", content="   ",
                )
            self.assertEqual(mocked_upsert.await_count, 0)

    def test_oversized_content_raises_and_never_calls_repository(self) -> None:
        with patch(
            "server_modules.agent_private_memory_repository.upsert_private_note",
            new_callable=AsyncMock,
        ) as mocked_upsert:
            oversized = "x" * (service.PRIVATE_MEMORY_NOTE_MAX_CHARS + 1)
            with self.assertRaises(ValueError):
                service.write_private_memory_note(
                    "ws-1", agent_install_id="agent-1", user_id="user-a", content=oversized,
                )
            self.assertEqual(mocked_upsert.await_count, 0)


class ResolveAgentInstallScopeTests(unittest.TestCase):
    def test_blank_resolves_to_the_root_sentinel(self) -> None:
        self.assertEqual(service.resolve_agent_install_scope(""), service.ROOT_AGENT_PRIVATE_MEMORY_SCOPE)
        self.assertEqual(service.resolve_agent_install_scope(None), service.ROOT_AGENT_PRIVATE_MEMORY_SCOPE)

    def test_real_install_id_is_passed_through(self) -> None:
        self.assertEqual(service.resolve_agent_install_scope("agent-42"), "agent-42")


class RedactionAndCallCountTests(unittest.TestCase):
    """Same secret-redaction discipline as memory_write_file: content is
    scrubbed BEFORE the repository call, and the repository is called
    EXACTLY ONCE with the redacted (not raw) content."""

    def setUp(self) -> None:
        patcher = patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=_async_return("tenant-1"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_secret_is_redacted_before_reaching_the_repository_exactly_once(self) -> None:
        captured = {}

        async def _fake_upsert(**kwargs):
            captured.update(kwargs)
            return {
                "id": "privmem_1", "content": kwargs["content"], "revision_recorded": True,
            }

        with patch(
            "server_modules.agent_private_memory_repository.upsert_private_note",
            side_effect=_fake_upsert,
        ) as mocked_upsert:
            saved = service.write_private_memory_note(
                "ws-1", agent_install_id="agent-1", user_id="user-a",
                content="My OpenAI key is sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD, keep it safe.",
            )
        self.assertEqual(mocked_upsert.call_count, 1)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD", captured["content"])
        self.assertIn("[redacted-secret]", captured["content"])
        self.assertTrue(saved["redacted"])
        # The identity actually bound in the repository call is exactly
        # what was passed in -- never silently substituted.
        self.assertEqual(captured["user_id"], "user-a")
        self.assertEqual(captured["agent_install_id"], "agent-1")
        self.assertEqual(captured["tenant_id"], "tenant-1")

    def test_clean_content_is_stored_unchanged_and_not_flagged(self) -> None:
        captured = {}

        async def _fake_upsert(**kwargs):
            captured.update(kwargs)
            return {"id": "privmem_1", "content": kwargs["content"], "revision_recorded": True}

        with patch(
            "server_modules.agent_private_memory_repository.upsert_private_note",
            side_effect=_fake_upsert,
        ) as mocked_upsert:
            saved = service.write_private_memory_note(
                "ws-1", agent_install_id="agent-1", user_id="user-a",
                content="Prefers concise, bullet-point answers over long prose.",
            )
        self.assertEqual(mocked_upsert.call_count, 1)
        self.assertEqual(captured["content"], "Prefers concise, bullet-point answers over long prose.")
        self.assertFalse(saved["redacted"])

    def test_get_private_memory_block_returns_empty_string_when_nothing_saved(self) -> None:
        with patch(
            "server_modules.agent_private_memory_repository.get_private_note",
            new=_async_return(None),
        ):
            block = service.get_private_memory_block("ws-1", agent_install_id="agent-1", user_id="user-a")
        self.assertEqual(block, "")

    def test_get_private_memory_block_returns_this_users_content_only(self) -> None:
        with patch(
            "server_modules.agent_private_memory_repository.get_private_note",
            new=_async_return({"content": "user-a's saved preference."}),
        ):
            block = service.get_private_memory_block("ws-1", agent_install_id="agent-1", user_id="user-a")
        self.assertEqual(block, "user-a's saved preference.")


class TenantResolutionNeverReadsUsersTableTests(unittest.TestCase):
    """CLAUDE.md's standing warning: users.tenant_id is written once at
    signup and goes stale for any second workspace -- tenant_id must always
    be resolved per-workspace. Assert this module calls the per-workspace
    resolver, never anything reading a user record."""

    def test_write_resolves_tenant_via_the_per_workspace_resolver_call_count(self) -> None:
        with patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new_callable=AsyncMock,
            return_value="resolved-tenant",
        ) as mocked_resolver, patch(
            "server_modules.agent_private_memory_repository.upsert_private_note",
            new_callable=AsyncMock,
            return_value={"id": "n1", "content": "x", "revision_recorded": True},
        ):
            service.write_private_memory_note(
                "ws-1", agent_install_id="agent-1", user_id="user-a", content="a preference note",
            )
        self.assertEqual(mocked_resolver.await_count, 1)
        mocked_resolver.assert_awaited_with("ws-1")


if __name__ == "__main__":
    unittest.main()
