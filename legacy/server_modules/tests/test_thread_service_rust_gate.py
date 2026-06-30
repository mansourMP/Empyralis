import unittest
from unittest.mock import AsyncMock, patch

from server_modules import thread_service


class ThreadServiceRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_master_thread_uses_rust_normalized_title(self):
        with patch.object(
            thread_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "normalize_thread",
                "next_action": "normalize_thread_record",
                "normalized_title": "Normalized Title",
            },
        ), patch.object(
            thread_service.control_plane_repository,
            "ensure_agent_thread",
            new=AsyncMock(return_value={"thread_id": "thread-1", "title": "Normalized Title"}),
        ) as ensure_mock:
            payload = await thread_service.ensure_master_thread(
                thread_id="thread-1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                owner_user_id="user-1",
                channel="chat",
                title="Original",
            )

        ensure_mock.assert_awaited_once()
        self.assertEqual(ensure_mock.await_args.kwargs["title"], "Normalized Title")
        self.assertEqual(payload["title"], "Normalized Title")

    async def test_record_user_turn_rust_denial_blocks_repository_write(self):
        denied = thread_service.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "thread_turn_content_missing",
            },
            command="thread-record-decision",
        )

        with patch.object(
            thread_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ) as rust_mock, patch.object(
            thread_service.control_plane_repository,
            "upsert_agent_turn",
            new=AsyncMock(),
        ) as upsert_mock:
            with self.assertRaisesRegex(RuntimeError, "Rust thread-record kernel blocked create_turn"):
                await thread_service.record_user_turn(
                    thread_id="thread-1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    session_id="session-1",
                    actor={"type": "user", "id": "user-1"},
                    content="hello",
                )

        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.args[0], "thread-record-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "create_turn")
        upsert_mock.assert_not_awaited()

    async def test_list_threads_calls_rust_before_repository_read(self):
        calls = []

        def rust_allow(command, payload, **kwargs):
            calls.append(command)
            self.assertEqual(command, "thread-record-decision")
            self.assertEqual(payload["operation"], "list_threads")
            self.assertEqual(payload["workspace_id"], "ws-1")
            return {"ok": True, "decision": "allow", "next_action": "list_workspace_threads"}

        async def list_agent_threads(**kwargs):
            calls.append("repository")
            return [{"id": "thread-1"}]

        with patch.object(
            thread_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=rust_allow,
        ), patch.object(
            thread_service.control_plane_repository,
            "list_agent_threads",
            new=AsyncMock(side_effect=list_agent_threads),
        ) as list_mock:
            result = await thread_service.list_threads(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                include_turns=True,
                limit=25,
            )

        self.assertEqual(result, [{"id": "thread-1"}])
        self.assertEqual(calls, ["thread-record-decision", "repository"])
        list_mock.assert_awaited_once()

    async def test_record_user_turn_uses_rust_request_id(self):
        with patch.object(
            thread_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "create_turn",
                "next_action": "create_thread_turn",
                "request_id": "rust-request-1",
            },
        ), patch.object(
            thread_service.control_plane_repository,
            "upsert_agent_turn",
            new=AsyncMock(return_value={"thread_id": "thread-1"}),
        ) as upsert_mock:
            await thread_service.record_user_turn(
                thread_id="thread-1",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                session_id="session-1",
                actor={"type": "user", "id": "user-1"},
                content="hello",
                metadata={"request_id": "client-request-1"},
            )

        self.assertEqual(upsert_mock.await_args.kwargs["request_id"], "rust-request-1")

    async def test_get_thread_primary_fallback_uses_rust_next_action(self):
        with patch.object(
            thread_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "get_thread",
                "next_action": "return_empty_primary_thread",
            },
        ), patch.object(
            thread_service.control_plane_repository,
            "get_agent_thread",
            new=AsyncMock(),
        ) as get_mock:
            payload = await thread_service.get_thread(
                "primary",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                include_turns=True,
            )

        get_mock.assert_not_awaited()
        self.assertEqual(payload["thread_id"], "primary")
        self.assertEqual(payload["turns"], [])


if __name__ == "__main__":
    unittest.main()
