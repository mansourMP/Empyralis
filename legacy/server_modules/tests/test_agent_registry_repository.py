import asyncio
import importlib
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

from server_modules import agent_registry_repository


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "INSERT 0 1"

    def transaction(self):
        return _FakeTransaction()


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def acquire(self):
        return _FakeAcquire(self._connection)


class _FakeExecutePool:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "UPDATE 1"


class _FakeFetchConnection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.row


@asynccontextmanager
async def _fake_scoped_connection(connection: _FakeFetchConnection, scopes: list[dict[str, str]], **kwargs):
    scopes.append(kwargs)
    yield connection


class AgentRegistryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        global agent_registry_repository
        agent_registry_repository = importlib.import_module("server_modules.agent_registry_repository")
        self._rust_gate = patch.object(
            agent_registry_repository.control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"decision": "allow"},
        )
        self._rust_gate.start()
        self.addCleanup(self._rust_gate.stop)

    def test_create_compiled_workflow_artifact_binds_real_datetimes(self) -> None:
        connection = _FakeConnection()
        pool = _FakePool(connection)

        with (
            patch(
                "server_modules.agent_registry_repository.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=pool),
            ),
            patch(
                "server_modules.agent_registry_repository.fetch_workflow_snapshot",
                new=AsyncMock(return_value={"workflow_id": "wf-test", "workflow_version_id": "wfver-test"}),
            ) as fetch_snapshot,
        ):
            result = asyncio.run(
                agent_registry_repository.create_compiled_workflow_artifact(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    name="Compiled Agent Artifact",
                    description="Compiled for install execution",
                    definition={"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
                    validation={"status": "ok"},
                    metadata={"source": "test"},
                    created_by_user_id="user-1",
                )
            )

        self.assertEqual(len(connection.calls), 2)
        first_args = connection.calls[0][1]
        second_args = connection.calls[1][1]
        self.assertIsInstance(first_args[8], datetime)
        self.assertIsInstance(second_args[8], datetime)
        self.assertEqual(result["workflow_id"], "wf-test")
        self.assertEqual(result["workflow_version_id"], "wfver-test")
        fetch_snapshot.assert_awaited_once()

    def test_get_workspace_agent_install_bundle_uses_scoped_connection_when_scope_provided(self) -> None:
        row = {
            "id": "ainstall_1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "agent_definition_id": "agentdef_1",
            "agent_definition_version_id": "agentver_1",
            "label": "QA Agent",
            "status": "draft",
            "enabled": True,
            "tool_toggles": {},
            "folder_grants": [],
            "connector_bindings": {},
            "memory_scope_overrides": {},
            "policy_context_overrides": {},
            "metadata": {"specialist_mode": "owner_edit"},
            "agent_definition_slug": "qa-agent",
            "agent_definition_name": "QA Agent",
            "agent_definition_description": "",
            "agent_kind": "specialist",
            "manifest": {},
            "capability_manifest": {},
            "policy_manifest": {},
            "placement_manifest": {},
        }
        connection = _FakeFetchConnection(row)
        scopes: list[dict[str, str]] = []

        with (
            patch(
                "server_modules.agent_registry_repository.control_plane_repository._scoped_connection",
                side_effect=lambda **kwargs: _fake_scoped_connection(connection, scopes, **kwargs),
            ),
            patch(
                "server_modules.agent_registry_repository.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(side_effect=AssertionError("unscoped pool should not be used")),
            ),
        ):
            result = asyncio.run(
                agent_registry_repository.get_workspace_agent_install_bundle(
                    "ainstall_1",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual(result["id"], "ainstall_1")
        self.assertEqual(scopes, [{"tenant_id": "tenant-1", "workspace_id": "workspace-1"}])
        query, args = connection.calls[0]
        self.assertIn("wai.tenant_id", query)
        self.assertIn("wai.workspace_id", query)
        self.assertEqual(args[:3], ("ainstall_1", "tenant-1", "workspace-1"))

    def test_enqueue_self_hosted_runtime_command_rejects_runtime_node_scope_mismatch(self) -> None:
        profile = {
            "id": "rprof-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "runtime_id": "node-expected",
            "runtime_class": "self_hosted_business_node",
            "metadata": {"owner_approved": True, "runtime_node_id": "node-expected"},
        }
        with patch(
            "server_modules.agent_registry_repository.get_runtime_profile",
            new=AsyncMock(return_value=profile),
        ):
            with self.assertRaises(ValueError) as exc:
                asyncio.run(
                    agent_registry_repository.enqueue_self_hosted_runtime_command(
                        runtime_profile_id="rprof-1",
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        runtime_node_id="node-other",
                        agent_id="agent-1",
                        command_type="runtime_action",
                        command_payload={"action": "open_url"},
                    )
                )
        self.assertIn("runtime_node_id does not match runtime profile binding", str(exc.exception))

    def test_claim_self_hosted_runtime_commands_requires_valid_node_session_token(self) -> None:
        profile = {
            "id": "rprof-1",
            "workspace_id": "workspace-1",
            "runtime_id": "node-1",
            "runtime_class": "self_hosted_business_node",
            "metadata": {"node_session_token_hash": agent_registry_repository._token_hash("good-token")},
        }
        with patch(
            "server_modules.agent_registry_repository.get_runtime_profile",
            new=AsyncMock(return_value=profile),
        ):
            with self.assertRaises(ValueError) as exc:
                asyncio.run(
                    agent_registry_repository.claim_self_hosted_runtime_commands(
                        runtime_profile_id="rprof-1",
                        node_session_token="bad-token",
                    )
                )
        self.assertIn("Node session token is invalid", str(exc.exception))

    def test_claim_self_hosted_runtime_commands_returns_signed_scoped_commands(self) -> None:
        profile = {
            "id": "rprof-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "runtime_id": "node-1",
            "runtime_class": "self_hosted_business_node",
            "metadata": {
                "node_session_token_hash": agent_registry_repository._token_hash("sess-node-1"),
                "node_command_queue": [
                    {
                        "id": "shcmd_1",
                        "workspace_id": "workspace-1",
                        "runtime_node_id": "node-1",
                        "agent_id": "agent-1",
                        "command_type": "runtime_action",
                        "command_payload": {"action": "open_url", "url": "https://example.com"},
                        "state": "queued",
                        "created_at": "2026-05-11T00:00:00Z",
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                ],
            },
        }
        fake_pool = _FakeExecutePool()
        with (
            patch(
                "server_modules.agent_registry_repository.get_runtime_profile",
                new=AsyncMock(return_value=profile),
            ),
            patch(
                "server_modules.agent_registry_repository.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=fake_pool),
            ),
        ):
            result = asyncio.run(
                agent_registry_repository.claim_self_hosted_runtime_commands(
                    runtime_profile_id="rprof-1",
                    node_session_token="sess-node-1",
                    max_commands=1,
                    lease_seconds=60,
                )
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["claimed_count"], 1)
        claimed = result["commands"][0]
        self.assertEqual(claimed["workspace_id"], "workspace-1")
        self.assertEqual(claimed["runtime_node_id"], "node-1")
        self.assertEqual(claimed["agent_id"], "agent-1")
        self.assertTrue(str(claimed["command_signature"]).strip())
        self.assertEqual(len(fake_pool.calls), 1)

    def test_complete_self_hosted_runtime_command_rejects_cross_node_scope(self) -> None:
        profile = {
            "id": "rprof-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "runtime_id": "node-1",
            "runtime_class": "self_hosted_business_node",
            "metadata": {
                "node_session_token_hash": agent_registry_repository._token_hash("sess-node-1"),
                "node_command_queue": [
                    {
                        "id": "shcmd_1",
                        "workspace_id": "workspace-1",
                        "runtime_node_id": "node-2",
                        "agent_id": "agent-1",
                        "command_type": "runtime_action",
                        "command_payload": {"action": "open_url"},
                        "state": "claimed",
                        "created_at": "2026-05-11T00:00:00Z",
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                ],
            },
        }
        with patch(
            "server_modules.agent_registry_repository.get_runtime_profile",
            new=AsyncMock(return_value=profile),
        ):
            with self.assertRaises(ValueError) as exc:
                asyncio.run(
                    agent_registry_repository.complete_self_hosted_runtime_command(
                        runtime_profile_id="rprof-1",
                        node_session_token="sess-node-1",
                        command_id="shcmd_1",
                        status="completed",
                        result_payload={"ok": True},
                    )
                )
        self.assertIn("runtime_node_id scope mismatch", str(exc.exception))

    def test_enroll_self_hosted_runtime_profile_rejects_expired_token(self) -> None:
        profile = {
            "id": "rprof-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "runtime_id": "node-1",
            "runtime_class": "self_hosted_business_node",
            "metadata": {
                "enrollment": {
                    "token_hash": agent_registry_repository._token_hash("tok-expired"),
                    "expires_at": "2000-01-01T00:00:00Z",
                    "issued_for_workspace_id": "workspace-1",
                }
            },
        }
        with patch(
            "server_modules.agent_registry_repository.get_runtime_profile",
            new=AsyncMock(return_value=profile),
        ):
            with self.assertRaises(ValueError) as exc:
                asyncio.run(
                    agent_registry_repository.enroll_self_hosted_runtime_profile(
                        runtime_profile_id="rprof-1",
                        enrollment_token="tok-expired",
                        public_key="pk-node-1",
                    )
                )
        self.assertIn("expired", str(exc.exception).lower())

    def test_enroll_self_hosted_runtime_profile_rejects_workspace_scope_mismatch(self) -> None:
        profile = {
            "id": "rprof-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-2",
            "runtime_id": "node-1",
            "runtime_class": "self_hosted_business_node",
            "metadata": {
                "enrollment": {
                    "token_hash": agent_registry_repository._token_hash("tok-1"),
                    "expires_at": "2099-01-01T00:00:00Z",
                    "issued_for_workspace_id": "workspace-1",
                }
            },
        }
        with patch(
            "server_modules.agent_registry_repository.get_runtime_profile",
            new=AsyncMock(return_value=profile),
        ):
            with self.assertRaises(ValueError) as exc:
                asyncio.run(
                    agent_registry_repository.enroll_self_hosted_runtime_profile(
                        runtime_profile_id="rprof-1",
                        enrollment_token="tok-1",
                        public_key="pk-node-1",
                    )
                )
        self.assertIn("workspace scope mismatch", str(exc.exception).lower())


if __name__ == "__main__":
    unittest.main()
