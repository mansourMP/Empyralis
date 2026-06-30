import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
import importlib

from server_modules import channel_concurrency_service


class _FakeConstraintError(Exception):
    def __init__(self, constraint_name: str):
        super().__init__(constraint_name)
        self.constraint_name = constraint_name


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(
        self,
        *,
        recent_turn_count: int = 0,
        active_workspace_count: int = 0,
        active_agent_count: int = 0,
        thread_conflict: bool = False,
    ) -> None:
        self.recent_turn_count = recent_turn_count
        self.active_workspace_count = active_workspace_count
        self.active_agent_count = active_agent_count
        self.thread_conflict = thread_conflict
        self.inserted_leases = []
        self.released_leases = []
        self.cleanup_calls = 0

    def transaction(self):
        return _FakeTransaction()

    async def fetchval(self, query, *params):
        if "pg_advisory_xact_lock" in query:
            return None
        if "FROM agent_channel_events" in query:
            return self.recent_turn_count
        if "FROM agent_channel_execution_leases" in query and "thread_id = $3" in query:
            return 1 if self.thread_conflict else None
        if "FROM agent_channel_execution_leases" in query and "responder_install_id = $3" in query:
            return self.active_agent_count
        if "FROM agent_channel_execution_leases" in query:
            return self.active_workspace_count
        raise AssertionError(query)

    async def execute(self, query, *params):
        if "DELETE FROM agent_channel_execution_leases" in query and "expires_at <= NOW()" in query:
            self.cleanup_calls += 1
            return "DELETE 0"
        if "INSERT INTO agent_channel_execution_leases" in query:
            if self.thread_conflict:
                raise _FakeConstraintError("uq_agent_channel_execution_leases_active_thread")
            self.inserted_leases.append(params)
            return "INSERT 0 1"
        if "DELETE FROM agent_channel_execution_leases" in query and "WHERE id = $1" in query:
            self.released_leases.append(params[0])
            return "DELETE 1"
        raise AssertionError(query)


def _fake_scoped_connection(connection: _FakeConnection):
    @asynccontextmanager
    async def _manager(*args, **kwargs):
        yield connection

    return _manager


class ChannelConcurrencyServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        global channel_concurrency_service

        channel_concurrency_service = importlib.import_module("server_modules.channel_concurrency_service")
        self._rust_decision_patch = patch(
            "server_modules.channel_concurrency_service.rust_runtime_kernel_client.runtime_state_store_decision",
            lambda **request: {
                "ok": True,
                "decision": "allow",
                "operation": request.get("operation"),
                "next_action": request.get("operation"),
            },
        )
        self._rust_enforce_patch = patch(
            "server_modules.channel_concurrency_service.rust_runtime_kernel_client.enforce_kernel_decision",
            lambda _command, decision: decision,
        )
        self._rust_decision_patch.start()
        self._rust_enforce_patch.start()
        self.addCleanup(self._rust_decision_patch.stop)
        self.addCleanup(self._rust_enforce_patch.stop)

    def test_resolve_channel_quota_snapshot_prefers_workspace_and_agent_metadata(self):
        workspace = {
            "metadata": {
                "plan_limits": {
                    "max_concurrent_customer_threads": 11,
                    "max_concurrent_threads_per_agent": 4,
                    "max_customer_turns_per_minute": 77,
                    "max_customer_runtime_seconds": 19,
                }
            }
        }
        install = {
            "metadata": {
                "concurrency": {
                    "max_active_threads": 2,
                    "max_runtime_seconds": 13,
                }
            }
        }

        snapshot = channel_concurrency_service.resolve_channel_quota_snapshot(
            workspace=workspace,
            install=install,
        )

        self.assertEqual(snapshot.max_workspace_active_threads, 11)
        self.assertEqual(snapshot.max_agent_active_threads, 2)
        self.assertEqual(snapshot.max_workspace_turns_per_minute, 77)
        self.assertEqual(snapshot.max_runtime_seconds, 13)

    async def test_acquire_channel_execution_lease_enforces_workspace_turn_rate_limit(self):
        connection = _FakeConnection(recent_turn_count=3)
        workspace = {"metadata": {"plan_limits": {"max_customer_turns_per_minute": 2}}}

        with (
            patch(
                "server_modules.channel_concurrency_service.control_plane_repository._scoped_connection",
                new=_fake_scoped_connection(connection),
            ),
            patch(
                "server_modules.channel_concurrency_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value=workspace),
            ),
        ):
            with self.assertRaises(channel_concurrency_service.ChannelExecutionLimitError) as error:
                await channel_concurrency_service.acquire_channel_execution_lease(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    responder_install_id="install-1",
                    thread_id="thread-1",
                    session_key="session-1",
                    channel_key="telegram",
                    endpoint_key="@partspro_bot",
                    install={"metadata": {}},
                )

            self.assertEqual(error.exception.reason, "workspace_rate_limited")

    async def test_acquire_channel_execution_lease_blocks_at_exact_workspace_turn_limit(self):
        connection = _FakeConnection(recent_turn_count=2)
        workspace = {"metadata": {"plan_limits": {"max_customer_turns_per_minute": 2}}}

        with (
            patch(
                "server_modules.channel_concurrency_service.control_plane_repository._scoped_connection",
                new=_fake_scoped_connection(connection),
            ),
            patch(
                "server_modules.channel_concurrency_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value=workspace),
            ),
        ):
            with self.assertRaises(channel_concurrency_service.ChannelExecutionLimitError) as error:
                await channel_concurrency_service.acquire_channel_execution_lease(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    responder_install_id="install-1",
                    thread_id="thread-1",
                    session_key="session-1",
                    channel_key="telegram",
                    endpoint_key="@partspro_bot",
                    install={"metadata": {}},
                )

            self.assertEqual(error.exception.reason, "workspace_rate_limited")
        self.assertEqual(connection.cleanup_calls, 1)

    async def test_channel_execution_slot_releases_lease_after_success(self):
        connection = _FakeConnection()
        workspace = {"metadata": {"plan_limits": {"max_customer_runtime_seconds": 9}}}

        with (
            patch(
                "server_modules.channel_concurrency_service.control_plane_repository._scoped_connection",
                new=_fake_scoped_connection(connection),
            ),
            patch(
                "server_modules.channel_concurrency_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value=workspace),
            ),
        ):
            async with channel_concurrency_service.channel_execution_slot(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                responder_install_id="install-1",
                thread_id="thread-1",
                session_key="session-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                install={"metadata": {}},
            ) as slot:
                self.assertIn("lease_id", slot)
                self.assertEqual(slot["quota_snapshot"].max_runtime_seconds, 9)

        self.assertEqual(len(connection.inserted_leases), 1)
        self.assertEqual(len(connection.released_leases), 1)


if __name__ == "__main__":
    unittest.main()
