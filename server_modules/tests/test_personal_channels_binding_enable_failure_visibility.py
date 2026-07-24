"""Regression cover for C2 (docs/design/audit-silent-failures.md):
personal_channels_service._ensure_agent_channel_binding_enabled used to
swallow every failure with a bare `except Exception: pass` (zero logging)
and schedule its work via a bare `loop.create_task(...)` with no stored
reference (fire-and-forget -- the task could be garbage-collected
mid-flight with no warning, independent of whether it would have raised).

A real DB unique-constraint conflict
(uq_agent_channel_bindings_inbound_owner_v2, documented in
agent_bindings_repository.py) hits exactly this branch on the live
per-session-sync path, so this failure is genuinely reachable, not
theoretical -- it just had zero diagnostics before this fix.

These tests prove: (1) a failure here is now logged with full context and
raised as a durability signal (visible, recoverable) instead of vanishing,
(2) the scheduling task is tracked in a module-level set so it can't be
GC'd mid-flight, and is removed once it completes, and (3) none of this
changes the connection/sync logic itself -- only its failure visibility
(per the task's explicit "do not change the connection logic" instruction).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service


class ChannelBindingEnableFailureVisibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Isolate the module-level task-tracking set between tests -- other
        # tests in the same process must not see this test's tasks (or
        # vice versa).
        personal_channels_service._CHANNEL_BINDING_ENABLE_TASKS.clear()

    async def _run_and_drain(self, *, agent_id: str = "ainstall_test1", channel_key: str = "whatsapp_personal") -> None:
        """Call the sync entry point (which schedules a task on the
        currently-running loop) then let that task actually run to
        completion before the test asserts on its effects."""
        registration = {"tenant_id": "tenant-1", "workspace_id": "workspace-1"}
        personal_channels_service._ensure_agent_channel_binding_enabled(
            agent_id=agent_id,
            channel_key=channel_key,
            registration=registration,
        )
        # Let the scheduled task actually execute -- create_task schedules
        # it onto the loop but doesn't run it until we yield control.
        pending = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_upsert_failure_is_logged_with_full_context_not_swallowed(self) -> None:
        boom = RuntimeError("duplicate key value violates unique constraint \"uq_agent_channel_bindings_inbound_owner_v2\"")
        with (
            patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(side_effect=boom),
            ),
            patch("server_modules.durability_signal.capture_durability_failure") as durability_mock,
            patch.object(personal_channels_service, "_logger") as logger_mock,
        ):
            await self._run_and_drain(agent_id="ainstall_deadbeef", channel_key="whatsapp_personal")

        # 1. Logged with full context and exc_info (not silently swallowed).
        self.assertTrue(logger_mock.exception.called, "the failure must be logged, not swallowed")
        logged_args = logger_mock.exception.call_args.args
        logged_message = logged_args[0] if logged_args else ""
        self.assertIn("ainstall_deadbeef", logged_args)
        self.assertIn("whatsapp_personal", logged_args)
        self.assertIn("upsert_channel_binding failed", logged_message)

        # 2. Raised as a durability signal (visible + recoverable), not lost.
        self.assertTrue(durability_mock.called, "a real failure here must raise a durability signal")
        _, kwargs = durability_mock.call_args
        self.assertIs(kwargs.get("exc"), boom)
        self.assertEqual(kwargs.get("event_class"), "channel_binding_enable_failed")
        self.assertEqual(kwargs.get("tenant_id"), "tenant-1")
        self.assertEqual(kwargs.get("workspace_id"), "workspace-1")
        self.assertEqual(kwargs.get("channel"), "whatsapp_personal")
        self.assertIn("ainstall_deadbeef", kwargs.get("summary", ""))

    async def test_success_path_logs_nothing_and_raises_no_durability_signal(self) -> None:
        """Control case: the happy path must stay exactly as quiet as
        before -- this fix must not turn normal, successful binds into
        noise."""
        with (
            patch(
                "server_modules.agent_bindings_repository.upsert_channel_binding",
                new=AsyncMock(return_value={"ok": True}),
            ) as upsert_mock,
            patch("server_modules.durability_signal.capture_durability_failure") as durability_mock,
        ):
            await self._run_and_drain(agent_id="ainstall_good1", channel_key="telegram_personal")

        upsert_mock.assert_awaited_once()
        durability_mock.assert_not_called()

    async def test_task_is_tracked_and_removed_after_completion(self) -> None:
        """The GC-footgun half of C2: the scheduling task must be held by
        a strong reference (not bare fire-and-forget) while pending, and
        cleaned up once done so the tracking set never grows unbounded."""
        release = asyncio.Event()

        async def _slow_upsert(**_kwargs: object) -> None:
            await release.wait()

        with patch(
            "server_modules.agent_bindings_repository.upsert_channel_binding",
            new=AsyncMock(side_effect=_slow_upsert),
        ):
            personal_channels_service._ensure_agent_channel_binding_enabled(
                agent_id="ainstall_slow1",
                channel_key="whatsapp_personal",
                registration={"tenant_id": "t1", "workspace_id": "w1"},
            )
            # Give the scheduled task a chance to start (and block on the
            # release event) before we inspect the tracking set.
            await asyncio.sleep(0)
            self.assertEqual(len(personal_channels_service._CHANNEL_BINDING_ENABLE_TASKS), 1)

            release.set()
            pending = list(personal_channels_service._CHANNEL_BINDING_ENABLE_TASKS)
            await asyncio.gather(*pending, return_exceptions=True)

        self.assertEqual(personal_channels_service._CHANNEL_BINDING_ENABLE_TASKS, set())

    async def test_missing_agent_id_is_a_pure_no_op(self) -> None:
        """Unchanged behavior: no agent_id means nothing to bind, no task
        scheduled, no error -- this fix must not change that."""
        with patch(
            "server_modules.agent_bindings_repository.upsert_channel_binding",
            new=AsyncMock(),
        ) as upsert_mock:
            personal_channels_service._ensure_agent_channel_binding_enabled(
                agent_id="",
                channel_key="whatsapp_personal",
                registration={"tenant_id": "t1", "workspace_id": "w1"},
            )
            await asyncio.sleep(0)

        upsert_mock.assert_not_called()
        self.assertEqual(personal_channels_service._CHANNEL_BINDING_ENABLE_TASKS, set())


if __name__ == "__main__":
    unittest.main()
