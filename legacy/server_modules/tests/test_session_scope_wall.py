import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_turn


class SessionScopeWallTests(unittest.IsolatedAsyncioTestCase):
    def _turn_request(self, *, channel: str = "web") -> agent_turn.AgentTurnRequest:
        return agent_turn.AgentTurnRequest(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            session_id="session-1",
            channel=channel,
            actor=agent_turn.TurnActor(type="user", id="user-1"),
            message="hello",
            context_hints={"metadata": {}},
            execution_mode="sync",
            response_mode="stream",
        )

    async def _assert_scope_violation_rejected(self):
        with (
            patch.object(agent_turn.session_service, "get_session_scoped", new=AsyncMock(side_effect=agent_turn.session_service.SessionScopeViolationError("scope mismatch"))),
            patch.object(agent_turn.session_service, "extend_session", new=AsyncMock()) as extend_mock,
            patch.object(agent_turn.session_service, "create_session", new=AsyncMock(return_value="session-new")) as create_mock,
            patch.object(agent_turn.thread_service, "ensure_master_thread", new=AsyncMock()) as ensure_mock,
            patch.object(agent_turn.thread_service, "record_user_turn", new=AsyncMock()) as user_turn_mock,
            patch.object(agent_turn.thread_service, "record_assistant_turn", new=AsyncMock()) as assistant_turn_mock,
            patch("server_modules.turn_runtime.build_turn_execution_services", return_value=object()),
            patch(
                "server_modules.turn_runtime.execute_agent_turn_request",
                new=AsyncMock(return_value={"reply": "ok", "status": "completed", "metadata": {}}),
            ) as execute_mock,
        ):
            with self.assertRaises(Exception):
                await agent_turn.agent_turn(
                    turn_request=self._turn_request(channel="web"),
                    current_user={"user_id": "user-1", "auth_type": "bearer"},
                    run_execution_services=object(),
                )

        extend_mock.assert_not_awaited()
        create_mock.assert_not_awaited()
        ensure_mock.assert_not_awaited()
        user_turn_mock.assert_not_awaited()
        assistant_turn_mock.assert_not_awaited()
        execute_mock.assert_not_awaited()

    async def test_agent_turn_rejects_cross_workspace_session_reuse(self):
        await self._assert_scope_violation_rejected()

    async def test_agent_turn_rejects_cross_thread_session_reuse(self):
        await self._assert_scope_violation_rejected()

    async def test_agent_turn_rejects_cross_channel_session_reuse(self):
        await self._assert_scope_violation_rejected()

    async def test_agent_turn_soft_mode_mints_fresh_session_on_scope_mismatch(self):
        with (
            patch.object(
                agent_turn.session_service,
                "get_session_scoped",
                new=AsyncMock(
                    return_value={
                        "session_id": "session-1",
                        "workspace_id": "workspace-2",
                        "tenant_id": "tenant-1",
                        "channel": "web",
                        "actor": {"type": "user", "id": "user-1"},
                        "created_at": "2026-04-12T00:00:00Z",
                        "expires_at": "2099-01-01T00:00:00Z",
                        "metadata": {"thread_id": "thread-1"},
                        "status": "active",
                        "_scope_mismatch": {"workspace_id": {"expected": "workspace-1", "actual": "workspace-2"}},
                    }
                ),
            ),
            patch.object(agent_turn.session_service, "extend_session", new=AsyncMock()) as extend_mock,
            patch.object(agent_turn.session_service, "create_session", new=AsyncMock(return_value="session-new")) as create_mock,
            patch.object(
                agent_turn.session_service,
                "get_session",
                new=AsyncMock(
                    return_value={
                        "session_id": "session-new",
                        "workspace_id": "workspace-1",
                        "tenant_id": "tenant-1",
                        "channel": "web",
                        "actor": {"type": "user", "id": "user-1"},
                        "created_at": "2026-04-12T00:00:00Z",
                        "expires_at": "2099-01-01T00:00:00Z",
                        "metadata": {"thread_id": "thread-1"},
                        "status": "active",
                    }
                ),
            ),
            patch.object(agent_turn.thread_service, "ensure_master_thread", new=AsyncMock()),
            patch.object(agent_turn.thread_service, "record_user_turn", new=AsyncMock()),
            patch.object(agent_turn.thread_service, "record_assistant_turn", new=AsyncMock()),
            patch("server_modules.turn_runtime.build_turn_execution_services", return_value=object()),
            patch(
                "server_modules.turn_runtime.execute_agent_turn_request",
                new=AsyncMock(return_value={"reply": "ok", "status": "completed", "metadata": {}}),
            ),
        ):
            result = await agent_turn.agent_turn(
                turn_request=self._turn_request(channel="web"),
                current_user={"user_id": "user-1", "auth_type": "bearer"},
                run_execution_services=object(),
            )

        self.assertEqual(result["reply"], "ok")
        extend_mock.assert_not_awaited()
        self.assertIsNone(create_mock.await_args.kwargs["session_id"])

    async def test_agent_turn_extends_same_scope_session(self):
        session_record = {
            "session_id": "session-1",
            "workspace_id": "workspace-1",
            "tenant_id": "tenant-1",
            "channel": "web",
            "actor": {"type": "user", "id": "user-1"},
            "created_at": "2026-04-12T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
            "metadata": {"thread_id": "thread-1"},
            "status": "active",
        }
        with (
            patch.object(agent_turn.session_service, "get_session_scoped", new=AsyncMock(return_value=session_record)),
            patch.object(agent_turn.session_service, "extend_session", new=AsyncMock(return_value=session_record)) as extend_mock,
            patch.object(agent_turn.session_service, "create_session", new=AsyncMock(return_value="session-new")) as create_mock,
            patch.object(agent_turn.thread_service, "ensure_master_thread", new=AsyncMock()),
            patch.object(agent_turn.thread_service, "record_user_turn", new=AsyncMock()),
            patch.object(agent_turn.thread_service, "record_assistant_turn", new=AsyncMock()),
            patch("server_modules.turn_runtime.build_turn_execution_services", return_value=object()),
            patch(
                "server_modules.turn_runtime.execute_agent_turn_request",
                new=AsyncMock(return_value={"reply": "ok", "status": "completed", "metadata": {}}),
            ),
        ):
            result = await agent_turn.agent_turn(
                turn_request=self._turn_request(channel="web"),
                current_user={"user_id": "user-1", "auth_type": "bearer"},
                run_execution_services=object(),
            )

        self.assertEqual(result["reply"], "ok")
        create_mock.assert_not_awaited()
        extend_mock.assert_awaited_once()
        self.assertEqual(extend_mock.await_args.kwargs["metadata_updates"]["thread_id"], "thread-1")


if __name__ == "__main__":
    unittest.main()
