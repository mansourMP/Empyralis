import unittest
from contextlib import asynccontextmanager
import importlib
from unittest.mock import AsyncMock, patch

from server_modules import control_plane_repository, thread_service
from server_modules.agent_registry_models import (
    ActivityLedgerEventModel,
    AgentActionEventModel,
    AgentChannelExecutionLeaseModel,
    AgentChannelEventModel,
    CreditLedgerEventModel,
    AgentEgressEventModel,
    AgentSchedulerWakeRequestModel,
    AgentSecretAccessEventModel,
    AgentDefinitionModel,
    AgentDefinitionVersionModel,
    PersonalContextEventModel,
    RuntimeProfileModel,
    SecurityControlEventModel,
    SecurityControlStateModel,
    WorkspaceAgentInstallModel,
    WorkspaceInventoryItemModel,
)


class ControlPlaneAgentRegistrySchemaTests(unittest.TestCase):
    def test_schema_sql_includes_agent_registry_tables_and_relationship_columns(self):
        schema = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL

        for fragment in (
            "CREATE TABLE IF NOT EXISTS governance_holds",
            "CREATE TABLE IF NOT EXISTS runtime_profiles",
            "CREATE TABLE IF NOT EXISTS agent_definitions",
            "CREATE TABLE IF NOT EXISTS agent_definition_versions",
            "CREATE TABLE IF NOT EXISTS workspace_agent_installs",
            "CREATE TABLE IF NOT EXISTS workspace_inventory_items",
            "CREATE TABLE IF NOT EXISTS personal_context_events",
            "CREATE TABLE IF NOT EXISTS agent_channel_events",
            "CREATE TABLE IF NOT EXISTS agent_secret_access_events",
            "CREATE TABLE IF NOT EXISTS agent_egress_events",
            "CREATE TABLE IF NOT EXISTS agent_scheduler_wake_requests",
            "CREATE TABLE IF NOT EXISTS agent_channel_execution_leases",
            "CREATE TABLE IF NOT EXISTS security_control_states",
            "CREATE TABLE IF NOT EXISTS security_control_events",
            "CREATE TABLE IF NOT EXISTS activity_ledger_events",
            "CREATE TABLE IF NOT EXISTS credit_ledger_events",
            "CREATE TABLE IF NOT EXISTS agent_action_events",
            "compiled_workflow_version_id TEXT NULL REFERENCES workflow_versions(id) ON DELETE SET NULL",
            "ALTER TABLE workspace_agent_installs\n    ADD COLUMN IF NOT EXISTS compiled_workflow_version_id TEXT NULL;",
            "ALTER TABLE agent_threads\n    ADD COLUMN IF NOT EXISTS master_agent_install_id TEXT NULL;",
            "ALTER TABLE agent_sessions\n    ADD COLUMN IF NOT EXISTS master_agent_install_id TEXT NULL,",
            "ALTER TABLE agent_turns\n    ADD COLUMN IF NOT EXISTS active_agent_install_id TEXT NULL,",
            "fk_agent_threads_master_agent_install",
            "fk_agent_sessions_runtime_profile",
            "fk_agent_turns_active_agent_install",
            "fk_workspace_agent_installs_compiled_workflow_version",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_bindings_active_inbound_owner",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_execution_leases_active_thread",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_events_inbound_message",
            "CREATE INDEX IF NOT EXISTS idx_personal_context_events_scope_created",
            "CREATE INDEX IF NOT EXISTS idx_agent_channel_events_session",
            "CREATE INDEX IF NOT EXISTS idx_agent_secret_access_events_run",
            "CREATE INDEX IF NOT EXISTS idx_agent_egress_events_scope_created",
            "CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_scope_due",
            "CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_master",
            "CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_trigger",
            "CREATE INDEX IF NOT EXISTS idx_security_control_states_scope",
            "CREATE INDEX IF NOT EXISTS idx_security_control_events_scope",
            "CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_scope_created",
            "CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_actor",
            "CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_scope_created",
            "CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_credit_type",
            "CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_payer",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_ledger_events_source",
            "CREATE INDEX IF NOT EXISTS idx_agent_action_events_scope_created",
            "CREATE INDEX IF NOT EXISTS idx_agent_action_events_domain",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_action_events_source",
            "CREATE INDEX IF NOT EXISTS idx_governance_holds_scope",
        ):
            self.assertIn(fragment, schema)

    def test_sqlalchemy_models_include_isolation_columns_and_foreign_keys(self):
        for model in (
            RuntimeProfileModel,
            AgentDefinitionModel,
            AgentDefinitionVersionModel,
            WorkspaceAgentInstallModel,
            WorkspaceInventoryItemModel,
            PersonalContextEventModel,
            AgentChannelEventModel,
            AgentSecretAccessEventModel,
            AgentEgressEventModel,
            AgentSchedulerWakeRequestModel,
            AgentChannelExecutionLeaseModel,
            SecurityControlStateModel,
            SecurityControlEventModel,
            ActivityLedgerEventModel,
            CreditLedgerEventModel,
            AgentActionEventModel,
        ):
            table = model.__table__
            self.assertIn("tenant_id", table.c)
            self.assertIn("workspace_id", table.c)

        version_fk_targets = {
            foreign_key.target_fullname
            for foreign_key in AgentDefinitionVersionModel.__table__.c["compiled_workflow_version_id"].foreign_keys
        }
        self.assertIn("workflow_versions.id", version_fk_targets)

        install_fk_targets = {
            foreign_key.target_fullname
            for foreign_key in WorkspaceAgentInstallModel.__table__.c["runtime_profile_id"].foreign_keys
        }
        self.assertIn("runtime_profiles.id", install_fk_targets)
        compiled_fk_targets = {
            foreign_key.target_fullname
            for foreign_key in WorkspaceAgentInstallModel.__table__.c["compiled_workflow_version_id"].foreign_keys
        }
        self.assertIn("workflow_versions.id", compiled_fk_targets)

        security_state_fk_targets = {
            foreign_key.target_fullname
            for foreign_key in SecurityControlStateModel.__table__.c["agent_install_id"].foreign_keys
        }
        self.assertIn("workspace_agent_installs.id", security_state_fk_targets)

        security_event_fk_targets = {
            foreign_key.target_fullname
            for foreign_key in SecurityControlEventModel.__table__.c["control_state_id"].foreign_keys
        }
        self.assertIn("security_control_states.id", security_event_fk_targets)


class ThreadServiceAgentBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global thread_service

        thread_service = importlib.import_module("server_modules.thread_service")

    async def test_ensure_master_thread_passes_master_agent_install_id(self):
        ensure_mock = AsyncMock(return_value={"id": "thread-1"})
        with patch("server_modules.thread_service.control_plane_repository.ensure_agent_thread", new=ensure_mock):
            await thread_service.ensure_master_thread(
                thread_id="thread-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                owner_user_id="user-1",
                master_agent_install_id="install-master",
                channel="web",
            )

        self.assertEqual(ensure_mock.await_args.kwargs["master_agent_install_id"], "install-master")

    async def test_record_assistant_turn_passes_install_and_runtime_profile(self):
        upsert_mock = AsyncMock(return_value={"id": "turn-1"})
        with patch("server_modules.thread_service.control_plane_repository.upsert_agent_turn", new=upsert_mock):
            await thread_service.record_assistant_turn(
                thread_id="thread-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                session_id="session-1",
                actor={"type": "assistant", "id": "sage"},
                reply="done",
                status="completed",
                run_id="run-1",
                active_agent_install_id="install-specialist",
                runtime_profile_id="runtime-local",
                metadata={"request_id": "req-1"},
            )

        self.assertEqual(upsert_mock.await_args.kwargs["active_agent_install_id"], "install-specialist")
        self.assertEqual(upsert_mock.await_args.kwargs["runtime_profile_id"], "runtime-local")


class ControlPlaneInstallIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global control_plane_repository

        control_plane_repository = importlib.import_module("server_modules.control_plane_repository")
        self._rust_gate = patch.object(
            control_plane_repository.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={"decision": "allow"},
        )
        self._rust_gate.start()
        self.addCleanup(self._rust_gate.stop)

    async def test_list_agent_turns_can_filter_by_active_install(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            await control_plane_repository.list_agent_turns(
                "thread-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                active_agent_install_id="install-specialist",
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("active_agent_install_id = $4", query)
        self.assertEqual(connection.fetch.await_args.args[1], "thread-1")
        self.assertEqual(connection.fetch.await_args.args[2], "tenant-1")
        self.assertEqual(connection.fetch.await_args.args[3], "workspace-1")
        self.assertEqual(connection.fetch.await_args.args[4], "install-specialist")

    async def test_list_security_control_states_can_filter_active_scope_rows(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            await control_plane_repository.list_security_control_states(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                scope_types=["workspace", "agent"],
                control_kind="kill_switch",
                active_only=True,
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("FROM security_control_states", query)
        self.assertIn("scope_type = ANY($3::text[])", query)
        self.assertIn("control_kind = $4", query)
        self.assertIn("enabled = TRUE", query)

    async def test_record_credit_ledger_event_inserts_durable_unified_row(self):
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(
            side_effect=[
                None,
                {
                    "id": "cled-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "surface": "sage",
                    "source_surface": "sage_direct_chat",
                    "payer": "platform_credits",
                    "credit_type": "ai_tokens",
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "runtime_target": "cloud_default",
                    "user_id": "user-1",
                    "thread_id": "thread-1",
                    "run_id": "req-1",
                    "agent_id": None,
                    "app_id": None,
                    "provider_usage": {"total_tokens": 42},
                    "platform_cost_usd": 0.001,
                    "provider_reported_cost": 0.001,
                    "provider_reported_currency": "USD",
                    "credits_debited": 1,
                    "estimation_mode": "provider_usage_exact",
                    "source_table": "workspace_hosted_ai_monthly_cost_ledger",
                    "source_event_id": "shost-1",
                    "metadata": {"request_id": "req-1"},
                    "created_at": "2026-05-21T00:00:00Z",
                },
            ]
        )

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            row = await control_plane_repository.record_credit_ledger_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                source_table="workspace_hosted_ai_monthly_cost_ledger",
                source_event_id="shost-1",
                event={
                    "surface": "sage",
                    "source_surface": "sage_direct_chat",
                    "payer": "platform_credits",
                    "credit_type": "ai_tokens",
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "runtime_target": "cloud_default",
                    "workspace_id": "workspace-1",
                    "user_id": "user-1",
                    "thread_id": "thread-1",
                    "run_id": "req-1",
                    "provider_usage": {"total_tokens": 42},
                    "platform_cost_usd": 0.001,
                    "provider_reported_cost": 0.001,
                    "provider_reported_currency": "USD",
                    "credits_debited": 1,
                    "estimation_mode": "provider_usage_exact",
                    "metadata": {"request_id": "req-1"},
                },
            )

        query = connection.fetchrow.await_args_list[1].args[0]
        self.assertIn("INSERT INTO credit_ledger_events", query)
        self.assertEqual(row["surface"], "sage")
        self.assertEqual(row["credit_type"], "ai_tokens")
        self.assertEqual(row["source_table"], "workspace_hosted_ai_monthly_cost_ledger")

    async def test_list_credit_ledger_events_supports_usage_filters(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            await control_plane_repository.list_credit_ledger_events(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                surface="mini_app",
                credit_type="ai_tokens",
                payer="platform_credits",
                app_id="flashcards",
                limit=25,
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("FROM credit_ledger_events", query)
        self.assertIn("surface = $3", query)
        self.assertIn("credit_type = $4", query)
        self.assertIn("payer = $5", query)
        self.assertIn("app_id = $6", query)

    async def test_record_agent_action_event_inserts_operation_row(self):
        connection = AsyncMock()
        connection.fetchrow = AsyncMock(
            side_effect=[
                None,
                {
                    "id": "aact-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "surface": "sage",
                    "source_surface": "sage_direct_tool",
                    "action_domain": "tool",
                    "action_type": "execute",
                    "action_name": "browser.click",
                    "tool_kind": "direct_tool",
                    "tool_id": "browser__click",
                    "connector_id": "browser",
                    "skill_id": None,
                    "mcp_server_id": None,
                    "mcp_tool_id": None,
                    "runtime_target": None,
                    "capability_id": "browser.click",
                    "risk_level": "high",
                    "approval_required": True,
                    "approval_id": "approval-1",
                    "approval_status": "approved",
                    "policy_decision": "allowed",
                    "status": "completed",
                    "error_code": None,
                    "payer": "platform_credits",
                    "billing_mode": "metered",
                    "credit_type": "tool_execution",
                    "credits_debited": 1,
                    "platform_cost_usd": 0.0001,
                    "usage_ref": {},
                    "trace_id": "trace-1",
                    "user_id": "user-1",
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "agent_id": None,
                    "app_id": None,
                    "input_summary": "click submit",
                    "output_summary": "clicked",
                    "source_table": "direct_tool_calls",
                    "source_event_id": "run-1:browser-click",
                    "idempotency_key": "run-1:browser-click",
                    "metadata": {},
                    "created_at": "2026-05-21T00:00:00Z",
                    "updated_at": "2026-05-21T00:00:00Z",
                },
            ]
        )

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            row = await control_plane_repository.record_agent_action_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                source_table="direct_tool_calls",
                source_event_id="run-1:browser-click",
                idempotency_key="run-1:browser-click",
                event={
                    "surface": "sage",
                    "source_surface": "sage_direct_tool",
                    "action_domain": "tool",
                    "action_type": "execute",
                    "action_name": "browser.click",
                    "tool_kind": "direct_tool",
                    "tool_id": "browser__click",
                    "connector_id": "browser",
                    "capability_id": "browser.click",
                    "risk_level": "high",
                    "approval_required": True,
                    "approval_id": "approval-1",
                    "approval_status": "approved",
                    "policy_decision": "allowed",
                    "status": "completed",
                    "payer": "platform_credits",
                    "billing_mode": "metered",
                    "credit_type": "tool_execution",
                    "credits_debited": 1,
                    "platform_cost_usd": 0.0001,
                    "trace_id": "trace-1",
                    "user_id": "user-1",
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "input_summary": "click submit",
                    "output_summary": "clicked",
                },
            )

        query = connection.fetchrow.await_args_list[1].args[0]
        self.assertIn("INSERT INTO agent_action_events", query)
        self.assertEqual(row["action_domain"], "tool")
        self.assertEqual(row["credit_type"], "tool_execution")
        self.assertEqual(row["status"], "completed")

    async def test_list_agent_action_events_supports_filters(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            await control_plane_repository.list_agent_action_events(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                surface="sage",
                action_domain="tool",
                status="completed",
                run_id="run-1",
                limit=25,
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("FROM agent_action_events", query)
        self.assertIn("surface = $3", query)
        self.assertIn("action_domain = $4", query)
        self.assertIn("status = $5", query)
        self.assertIn("run_id = $6", query)

    async def test_upsert_agent_turn_returns_minimal_write_payload_without_thread_reread(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock()
        connection.fetchrow = AsyncMock(
            side_effect=[
                {
                    "id": "turn-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "thread_id": "thread-1",
                    "session_id": "session-1",
                    "request_id": "req-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": "done",
                    "run_id": "run-1",
                    "actor": {"type": "assistant"},
                    "active_agent_install_id": "install-1",
                    "runtime_profile_id": "runtime-1",
                    "approvals": [],
                    "interventions": [],
                    "metadata": {},
                    "created_at": "2026-04-12T00:00:00Z",
                    "updated_at": "2026-04-12T00:00:00Z",
                },
                {
                    "id": "thread-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "owner_user_id": "user-1",
                    "master_agent_install_id": "install-master",
                    "channel": "web",
                    "title": "done",
                    "status": "active",
                    "metadata": {},
                    "created_at": "2026-04-12T00:00:00Z",
                    "updated_at": "2026-04-12T00:00:00Z",
                    "last_turn_at": "2026-04-12T00:00:00Z",
                },
            ]
        )

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            payload = await control_plane_repository.upsert_agent_turn(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                thread_id="thread-1",
                session_id="session-1",
                role="assistant",
                content="done",
                request_id="req-1",
                run_id="run-1",
                active_agent_install_id="install-1",
                runtime_profile_id="runtime-1",
            )

        self.assertEqual(payload["turn"]["id"], "turn-1")
        self.assertEqual(payload["thread"]["id"], "thread-1")
        self.assertNotIn("turns", payload["thread"])
        self.assertEqual(connection.fetchrow.await_count, 2)
        connection.fetch.assert_not_called()
        insert_query = connection.fetchrow.await_args_list[0].args[0]
        update_query = connection.fetchrow.await_args_list[1].args[0]
        self.assertIn("INSERT INTO agent_turns", insert_query)
        self.assertIn("RETURNING *", insert_query)
        self.assertIn("UPDATE agent_threads", update_query)
        self.assertIn("RETURNING id, tenant_id, workspace_id", update_query)

    async def test_upsert_security_control_state_records_audit_event(self):
        connection = AsyncMock()
        connection.execute = AsyncMock()
        connection.fetchrow = AsyncMock(
            side_effect=[
                {
                    "id": "sctl-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "scope_type": "workspace",
                    "control_kind": "kill_switch",
                    "scope_key": "workspace",
                    "enabled": True,
                    "status": "active",
                    "reason": "incident",
                    "metadata": {},
                }
            ]
        )

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            result = await control_plane_repository.upsert_security_control_state(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                scope_type="workspace",
                scope_key="workspace",
                enabled=True,
                reason="incident",
                action="enabled",
            )

        self.assertEqual(result["id"], "sctl-1")
        first_query = connection.execute.await_args_list[0].args[0]
        second_query = connection.execute.await_args_list[1].args[0]
        self.assertIn("INSERT INTO security_control_states", first_query)
        self.assertIn("INSERT INTO security_control_events", second_query)

    async def test_append_agent_egress_event_inserts_audit_row(self):
        connection = AsyncMock()
        connection.execute = AsyncMock()
        connection.fetchrow = AsyncMock(return_value={"id": "eevt-1", "tenant_id": "tenant-1", "workspace_id": "workspace-1"})

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            result = await control_plane_repository.append_agent_egress_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                agent_install_id="install-1",
                runtime_mode="hosted_secure",
                tool_name="web-search",
                action_class="read",
                request_method="GET",
                request_url="https://api.openai.com/v1/models",
                request_host="api.openai.com",
                status="allowed",
                metadata={"policy": {"allow_outbound": True}},
            )

        self.assertEqual(result["id"], "eevt-1")
        insert_query = connection.execute.await_args.args[0]
        self.assertIn("INSERT INTO agent_egress_events", insert_query)

    async def test_append_activity_ledger_event_inserts_durable_row(self):
        connection = AsyncMock()
        connection.execute = AsyncMock()
        connection.fetchrow = AsyncMock(
            return_value={
                "id": "aevt-1",
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
                "event_class": "artifact_created",
                "detail_level": "timeline_detail",
            }
        )

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            result = await control_plane_repository.append_activity_ledger_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                actor_type="specialist",
                actor_id="install-1",
                install_id="install-1",
                event_class="artifact_created",
                detail_level="timeline_detail",
                action="artifact_created",
                summary="Created 1 artifact.",
                artifacts=[{"path": "knowledge/out.md"}],
            )

        self.assertEqual(result["id"], "aevt-1")
        insert_query = connection.execute.await_args.args[0]
        self.assertIn("INSERT INTO activity_ledger_events", insert_query)

    async def test_list_activity_ledger_events_can_filter_by_detail_and_scope(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            await control_plane_repository.list_activity_ledger_events(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                event_classes=["approval", "delegation"],
                detail_levels=["feed_summary"],
                actor_type="sage",
                install_id="install-sage",
                run_id="run-1",
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("FROM activity_ledger_events", query)
        self.assertIn("event_class = ANY($3::text[])", query)
        self.assertIn("detail_level = ANY($4::text[])", query)
        self.assertIn("actor_type = $5", query)
        self.assertIn("install_id = $6", query)
        self.assertIn("run_id = $7", query)

    async def test_list_activity_ledger_events_supports_cursor_filters(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def fake_scoped_connection(**_kwargs):
            yield connection

        with patch("server_modules.control_plane_repository._scoped_connection", new=fake_scoped_connection):
            await control_plane_repository.list_activity_ledger_events(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                since_created_at="2026-04-10T09:00:00Z",
                since_id="aevt-1",
                limit=20,
            )

        query = connection.fetch.await_args.args[0]
        self.assertIn("created_at >", query)
        self.assertIn("AND id >", query)
        self.assertIn("ORDER BY created_at DESC, id DESC", query)

    async def test_plan_governance_operation_blocks_destructive_flow_when_hold_active(self):
        with (
            patch(
                "server_modules.control_plane_repository.build_governance_scope_export",
                new=AsyncMock(return_value={"counts": {"thread_count": 3}}),
            ),
            patch(
                "server_modules.control_plane_repository.list_governance_holds",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "hold-1",
                            "hold_key": "legal-hold",
                            "blocked_operations": ["retention_delete", "workspace_delete"],
                            "status": "active",
                        }
                    ]
                ),
            ),
        ):
            result = await control_plane_repository.plan_governance_operation(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                operation="workspace_delete",
            )

        self.assertTrue(result["blocked"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocking_hold_ids"], ["hold-1"])
        self.assertEqual(result["operation"], "workspace_delete")

    async def test_plan_governance_operation_allows_export_under_hold(self):
        with (
            patch(
                "server_modules.control_plane_repository.build_governance_scope_export",
                new=AsyncMock(return_value={"counts": {"thread_count": 3}}),
            ),
            patch(
                "server_modules.control_plane_repository.list_governance_holds",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "hold-1",
                            "hold_key": "legal-hold",
                            "blocked_operations": ["retention_delete", "workspace_delete", "tenant_delete"],
                            "status": "active",
                        }
                    ]
                ),
            ),
        ):
            result = await control_plane_repository.plan_governance_operation(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                operation="export",
            )

        self.assertFalse(result["blocked"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["blocking_hold_ids"], [])


if __name__ == "__main__":
    unittest.main()
