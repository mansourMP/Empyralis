import queue
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from server_modules import run_service, run_state_repository, runs_execution, runs_output, safe_mode_service, shared
from server_modules.runtime_state_store import init_runtime_state_db


class RunsExecutionGraphTests(unittest.TestCase):
    def _mock_agent_generation_state(self):
        execution_context = {"metadata": {}, "provider": "openai", "model": "gpt-4o-mini"}
        agent_state = {
            "provider": "openai",
            "selected_model": "gpt-4o-mini",
            "credential_candidates": [{"credentials": {"api_key": "test-key"}}],
            "credentials": {"api_key": "test-key"},
        }
        return execution_context, agent_state

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "runtime-state.sqlite3"
        init_runtime_state_db(self.db_path)
        self.live_run_store: dict[str, dict] = {}
        self.archive_store: dict[str, dict] = {}
        self.patchers = [
            patch.object(runs_execution, "ORION_RUNTIME_STATE_DB", self.db_path),
            patch.object(runs_output, "ORION_RUNTIME_STATE_DB", self.db_path),
            patch.object(run_state_repository, "sync_upsert_live_run", side_effect=self._sync_upsert_live_run),
            patch.object(run_state_repository, "sync_delete_live_run", side_effect=self._sync_delete_live_run),
            patch.object(run_state_repository, "sync_list_live_runs", side_effect=self._sync_list_live_runs),
            patch.object(run_state_repository, "sync_archive_run", side_effect=self._sync_archive_run),
            patch.object(run_state_repository, "sync_list_run_archive", side_effect=self._sync_list_run_archive),
        ]
        for patcher in self.patchers:
            patcher.start()
        shared.sync_acp_manager_paths(runtime_db_path=self.db_path)
        runs_execution.ACP_MANAGER.reload_runtime_state()
        runs_execution.runs.clear()
        runs_execution.RUN_QUEUE_INDEX.clear()
        with runs_execution.IDEMPOTENCY_LOCK:
            runs_execution.IDEMPOTENCY_RECORDS.clear()

    def tearDown(self) -> None:
        runs_execution.runs.clear()
        runs_execution.RUN_QUEUE_INDEX.clear()
        with runs_execution.IDEMPOTENCY_LOCK:
            runs_execution.IDEMPOTENCY_RECORDS.clear()
        safe_mode_service.reset_state_for_tests()
        for patcher in reversed(self.patchers):
            patcher.stop()
        shared.sync_acp_manager_paths(runtime_db_path=runs_execution.ORION_RUNTIME_STATE_DB)
        runs_execution.ACP_MANAGER.reload_runtime_state()
        self.tmpdir.cleanup()

    @patch.dict("os.environ", {"EMPYRALIS_MAX_WORKFLOW_TURN_DEPTH": "1"})
    def test_workflow_child_run_request_rejects_recursion_depth_over_limit(self) -> None:
        request = run_service.RunStartRequest(
            engine="orion",
            workflow_id="wf-child",
            workspace_id="ws-1",
            user_goal="child",
            metadata={"workflow_turn_depth": 2},
        )

        with self.assertRaises(run_service.WorkflowRecursionError):
            runs_execution._execute_workflow_child_run_request(request)

    def _sync_upsert_live_run(
        self,
        run_id: str,
        workspace_id: str,
        tenant_id: str,
        state: str,
        payload: dict,
        trace_id: str,
    ) -> None:
        snapshot = dict(payload)
        snapshot["run_id"] = run_id
        snapshot["workspace_id"] = workspace_id
        snapshot["tenant_id"] = tenant_id
        snapshot["status"] = state
        snapshot["trace_id"] = trace_id
        self.live_run_store[run_id] = snapshot

    def _sync_delete_live_run(self, run_id: str) -> None:
        self.live_run_store.pop(run_id, None)

    def _sync_list_live_runs(self) -> list[dict]:
        return [dict(item) for item in self.live_run_store.values()]

    def _sync_archive_run(self, run_id: str, final_state: str, payload: dict, trace_id: str) -> None:
        snapshot = dict(payload)
        snapshot["run_id"] = run_id
        snapshot["status"] = final_state
        snapshot["trace_id"] = trace_id
        self.archive_store[run_id] = snapshot

    def _sync_list_run_archive(self, limit: int = 200) -> list[dict]:
        items = list(self.archive_store.values())
        return [dict(item) for item in items[: max(1, int(limit or 0))]]

    def _register_live_run(self, run_id: str) -> queue.Queue:
        log_queue = queue.Queue()
        runs_execution.runs[run_id] = {
            "status": "running",
            "logs": log_queue,
            "input_queue": queue.Queue(),
            "events": [],
            "_event_seq": 0,
            "node_states": None,
            "context": {"metadata": {}},
            "tool_policy_audit": [],
            "memory_trace": {},
        }
        runs_execution.RUN_QUEUE_INDEX[id(log_queue)] = run_id
        return log_queue

    def test_predict_tool_ids_from_workflow_definition_uses_agent_and_tool_nodes(self):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {
                    "id": "agent_1",
                    "type": "agent",
                    "config": {
                        "tools": {
                            "dynamic_allowed": ["draft_email"],
                            "explicit_required": ["send_message"],
                        }
                    },
                },
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "shell",
                    "config": {},
                },
            ],
            "edges": [],
        }
        context = {"workflow_definition": definition, "metadata": {}}

        predicted = runs_execution._predict_tool_ids_for_context(context)

        self.assertEqual(predicted, ["draft_email", "send_message", "shell.execute"])

    def test_compile_orion_dag_prefers_workflow_definition(self):
        dag = runs_execution._compile_orion_dag(
            {
                "workflow_id": "wf_123",
                "workflow_definition": {
                    "version": "empyralist.workflow.v2",
                    "nodes": [{"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}}],
                    "edges": [],
                },
                "metadata": {},
            }
        )

        self.assertEqual(dag["type"], "workflow_graph")
        self.assertEqual(dag["nodes"][0]["kind"], "workflow_graph_execute")

    def test_tool_policy_tool_id_maps_connector_and_document_variants(self):
        self.assertEqual(
            runs_execution._workflow_tool_policy_tool_id(
                "connector_action",
                {"action_id": "create_doc"},
            ),
            "document_create",
        )
        self.assertEqual(
            runs_execution._workflow_tool_policy_tool_id(
                "spreadsheet",
                {"operation": "append"},
            ),
            "spreadsheet_append",
        )
        self.assertEqual(
            runs_execution._workflow_tool_policy_tool_id(
                "document",
                {"operation": "update", "file_path": "deck.pptx"},
            ),
            "presentation_update",
        )

    def test_workflow_decision_value_supports_safe_boolean_expressions(self):
        result = runs_execution._workflow_decision_value(
            "hello from workflow",
            {"last_data": {"status": "ok", "count": 2}},
            'result_data["status"] == "ok" and "hello" in context_text and result_data["count"] >= 2',
        )

        self.assertTrue(result)

    def test_workflow_decision_value_rejects_callable_expression_injection(self):
        with self.assertRaises(ValueError):
            runs_execution._workflow_decision_value(
                "hello from workflow",
                {"last_data": {"status": "ok"}},
                '__import__("os").system("whoami")',
            )

    @patch(
        "server_modules.runs_execution.wait_for_human_response",
        return_value={
            "approval_id": "approval-1",
            "correlation_id": "corr-1",
            "decision": "approve",
            "raw_decision": "approve",
            "note": None,
            "approved": True,
            "rejected": False,
            "escalated": False,
        },
    )
    def test_execute_workflow_graph_runs_explicit_approval(self, _approval_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "approval_1",
                    "type": "human",
                    "variant": "approval",
                    "config": {
                        "title": "Approve draft",
                        "instructions": "Confirm before continuing.",
                        "decision_options": ["approve", "reject"],
                    },
                },
                {
                    "id": "data_1",
                    "type": "data",
                    "variant": "compose",
                    "config": {"template": "Approved and ready"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "trigger_1", "target": "approval_1"},
                {"id": "e2", "source": "approval_1", "target": "data_1"},
            ],
        }

        result = runs_execution._execute_workflow_graph(
            "run-1",
            {"workflow_id": "wf_approval", "user_goal": "Review this", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("Approved and ready", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "data_1")

    @patch(
        "server_modules.runs_execution.wait_for_human_response",
        return_value={
            "approval_id": "approval-1",
            "correlation_id": "corr-1",
            "decision": "please add an escalation branch after review",
            "raw_decision": "Please add an escalation branch after review",
            "note": None,
            "approved": False,
            "rejected": False,
            "escalated": False,
        },
    )
    def test_execute_workflow_graph_captures_review_reply(self, _response_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "review_1",
                    "type": "human",
                    "variant": "review",
                    "config": {
                        "title": "Review workflow",
                        "instructions": "Provide feedback before continuing.",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "review_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-review",
            {"workflow_id": "wf_review", "user_goal": "Review flow", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertEqual(result["result_text"], "Please add an escalation branch after review")
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "review_1")

    @patch("server_modules.runs_execution.wait_for_human_response")
    def test_execute_workflow_graph_records_live_node_states(self, approval_mock):
        def _approve(*_args, **_kwargs):
            node_states = runs_execution.runs["run-node-state"]["node_states"]
            approval_state = node_states["items"]["approval_1"]
            self.assertEqual(approval_state["status"], "waiting_human")
            self.assertTrue(approval_state["waiting_for_approval"])
            return {
                "approval_id": "approval-1",
                "correlation_id": "corr-1",
                "decision": "approve",
                "raw_decision": "approve",
                "note": None,
                "approved": True,
                "rejected": False,
                "escalated": False,
            }

        approval_mock.side_effect = _approve
        log_queue = self._register_live_run("run-node-state")
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "approval_1",
                    "type": "human",
                    "variant": "approval",
                    "config": {"title": "Approve draft", "decision_options": ["approve", "reject"]},
                },
                {"id": "data_1", "type": "data", "variant": "compose", "config": {"template": "Ready to send"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger_1", "target": "approval_1"},
                {"id": "e2", "source": "approval_1", "target": "data_1"},
            ],
        }

        runs_execution._execute_workflow_graph(
            "run-node-state",
            {"workflow_id": "wf_live", "user_goal": "Review message", "metadata": {}},
            log_queue,
            definition,
        )

        snapshot = runs_output._serialize_run_snapshot("run-node-state", runs_execution.runs["run-node-state"])
        node_states = snapshot["node_states"]
        self.assertEqual(node_states["graph_kind"], "workflow")
        self.assertEqual(node_states["final_node_id"], "data_1")
        self.assertEqual(node_states["counts"]["succeeded"], 3)
        items = {item["node_id"]: item for item in node_states["items"]}
        self.assertEqual(items["trigger_1"]["status"], "succeeded")
        self.assertEqual(items["approval_1"]["status"], "succeeded")
        self.assertEqual(items["data_1"]["status"], "succeeded")
        self.assertIsNone(node_states["active_node_id"])

    def test_serialize_run_snapshot_uses_connector_evidence_from_last_node_data(self):
        log_queue = self._register_live_run("run-evidence")
        runs_execution.runs["run-evidence"].update(
            {
                "status": "completed",
                "result": "Connector action completed: telegram_bot.send_message.",
                "result_data": {
                    "summary": "Connector action completed: telegram_bot.send_message.",
                    "last_node_data": {
                        "connector_action": {
                            "connector": "telegram_bot",
                            "action_id": "send_message",
                            "chat_id": "1932934047",
                            "result": {"ok": True, "message_id": "msg-1"},
                            "executed": True,
                            "duplicate_protected": False,
                        }
                    },
                },
                "events": [
                    {
                        "event": "approval_resolved",
                        "data": {"approved": True, "decision": "approved"},
                    }
                ],
                "context": {"metadata": {}},
            }
        )

        snapshot = runs_output._serialize_run_snapshot("run-evidence", runs_execution.runs["run-evidence"])
        evidence_by_label = {item["label"]: item["value"] for item in snapshot["evidence_items"]}

        self.assertEqual(evidence_by_label.get("Execution"), "Executed")
        self.assertEqual(evidence_by_label.get("Action"), "Send Message")
        self.assertEqual(evidence_by_label.get("Target"), "1932934047")

    def test_serialize_run_snapshot_builds_run_detail_contract_for_connector_execution(self):
        log_queue = self._register_live_run("run-contract")
        runs_execution.runs["run-contract"].update(
            {
                "status": "completed",
                "result": "Connector action completed: telegram_bot.send_message.",
                "usage_masked": {"provider": "openai", "model": "gpt-5.4"},
                "result_data": {
                    "summary": "Connector action completed: telegram_bot.send_message.",
                    "last_node_data": {
                        "connector_action": {
                            "connector": "telegram_bot",
                            "action_id": "send_message",
                            "chat_id": "1932934047",
                            "result": {"ok": True, "message_id": "msg-1"},
                            "executed": True,
                            "duplicate_protected": False,
                        }
                    },
                },
                "events": [
                    {
                        "event": "approval_resolved",
                        "data": {"approved": True, "decision": "approved"},
                    }
                ],
                "context": {
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "metadata": {},
                },
            }
        )

        snapshot = runs_output._serialize_run_snapshot("run-contract", runs_execution.runs["run-contract"])
        contract = snapshot["run_detail_contract"]

        self.assertEqual(contract["provider_model"]["requested_provider"], "openai")
        self.assertEqual(contract["provider_model"]["effective_provider"], "openai")
        self.assertEqual(contract["provider_model"]["requested_model"], "gpt-4.1")
        self.assertEqual(contract["provider_model"]["effective_model"], "gpt-5.4")
        self.assertEqual(contract["approval_outcome"]["label"], "Confirmed")
        self.assertEqual(contract["connector_mutation"]["execution_label"], "Executed")
        self.assertEqual(contract["connector_mutation"]["action_label"], "Send Message")
        self.assertEqual(contract["connector_mutation"]["target_label"], "1932934047")
        self.assertEqual(contract["evidence_items"][0]["label"], "Execution")
        self.assertEqual(contract["evidence_items"][0]["value"], "Executed")

    def test_serialize_run_snapshot_marks_duplicate_protected_in_contract_and_evidence(self):
        log_queue = self._register_live_run("run-duplicate-evidence")
        runs_execution.runs["run-duplicate-evidence"].update(
            {
                "status": "completed",
                "result": "Connector action completed: telegram_bot.send_message.",
                "result_data": {
                    "summary": "Connector action completed: telegram_bot.send_message.",
                    "last_node_data": {
                        "connector_action": {
                            "connector": "telegram_bot",
                            "action_id": "send_message",
                            "chat_id": "1932934047",
                            "result": {"ok": True, "message_id": "msg-1"},
                            "executed": False,
                            "duplicate_protected": True,
                        }
                    },
                },
                "events": [],
                "context": {"metadata": {}},
            }
        )

        snapshot = runs_output._serialize_run_snapshot(
            "run-duplicate-evidence",
            runs_execution.runs["run-duplicate-evidence"],
        )
        contract = snapshot["run_detail_contract"]
        evidence_by_label = {item["label"]: item["value"] for item in snapshot["evidence_items"]}

        self.assertEqual(contract["connector_mutation"]["execution_label"], "Duplicate protected")
        self.assertEqual(evidence_by_label.get("Execution"), "Duplicate protected")

    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    @patch("server_modules.runs_execution._workflow_execute_connector_action", side_effect=RuntimeError("connector offline"))
    def test_execute_workflow_graph_degrades_retryable_connector_outages(self, _connector_action_mock, _approval_mock):
        log_queue = self._register_live_run("run-node-failure")
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {"connector": "telegram_bot", "action_id": "send_message"},
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-node-failure",
            {"workflow_id": "wf_fail", "user_goal": "Send alert", "metadata": {}},
            log_queue,
            definition,
        )

        snapshot = runs_output._serialize_run_snapshot("run-node-failure", runs_execution.runs["run-node-failure"])
        items = {item["node_id"]: item for item in snapshot["node_states"]["items"]}
        self.assertEqual(items["tool_1"]["status"], "succeeded")
        self.assertTrue(items["tool_1"]["detail"]["degraded"])
        self.assertIn("temporarily unavailable", result["result_text"].lower())

    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    @patch("server_modules.runs_execution._workflow_execute_connector_action")
    def test_execute_workflow_graph_runs_connector_action_tool(self, connector_action_mock, _approval_mock):
        connector_action_mock.return_value = {
            "summary": "Connector action completed: google_workspace.draft_email.",
            "result_data": {
                "connector_action": {
                    "connector": "google_workspace",
                    "action_id": "draft_email",
                }
            },
        }
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "google_workspace",
                        "action_id": "draft_email",
                        "to_email": "ops@example.com",
                        "subject": "Draft",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-connector",
            {"workflow_id": "wf_connector", "user_goal": "Draft email", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("Connector action completed", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch("server_modules.runs_execution._workflow_execute_connector_action")
    def test_execute_workflow_graph_reuses_resolved_connector_approval_without_reprompting(self, connector_action_mock):
        connector_action_mock.return_value = {
            "summary": "Connector action completed: google_workspace.send_email.",
            "result_data": {
                "connector_action": {
                    "connector": "google_workspace",
                    "action_id": "send_email",
                    "recipient": "ops@example.com",
                }
            },
        }
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "google_workspace",
                        "action_id": "send_email",
                        "to_email": "ops@example.com",
                        "subject": "Ops update",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }
        approval_metadata = runs_execution._connector_tool_node_approval_metadata(
            node_id="tool_1",
            label="send_email",
            tool_id="send_email",
            variant="connector_action",
            config=definition["nodes"][1]["config"],
            evaluation={"approval_label": "Email"},
        )
        fingerprint = run_service.approval_resolution_fingerprint(approval_metadata)
        context = {
            "workflow_id": "wf_connector",
            "user_goal": "Send email",
            "metadata": {
                run_service.RESOLVED_APPROVAL_MARKERS_KEY: [
                    {
                        "fingerprint": fingerprint,
                        "approval_id": "approval-1",
                        "decision": "approved",
                    }
                ]
            },
        }

        with patch("server_modules.runs_execution.wait_for_human_decision", side_effect=AssertionError("approval should not repeat")):
            result = runs_execution._execute_workflow_graph(
                "run-connector-approved",
                context,
                queue.Queue(),
                definition,
            )

        self.assertIn("Connector action completed", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    def test_execute_workflow_graph_rejects_invalid_email_before_requesting_approval(self):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "google_workspace",
                        "action_id": "send_email",
                        "to_email": "not-an-email",
                        "subject": "Ops update",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        with patch("server_modules.runs_execution.wait_for_human_decision", side_effect=AssertionError("approval should not be requested")):
            with self.assertRaises(RuntimeError) as raised:
                runs_execution._execute_workflow_graph(
                    "run-connector-invalid-email",
                    {"workflow_id": "wf_connector", "user_goal": "Send email", "metadata": {}},
                    queue.Queue(),
                    definition,
                )

        self.assertEqual(str(raised.exception), "Connector action 'send_email' requires a valid recipient email.")

    @patch("server_modules.runs_execution._workflow_execute_connector_action")
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=False)
    def test_execute_workflow_graph_stops_when_connector_approval_is_denied(self, _approval_mock, connector_action_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "google_workspace",
                        "action_id": "send_email",
                        "to_email": "ops@example.com",
                        "subject": "Ops update",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        with self.assertRaises(RuntimeError) as raised:
            runs_execution._execute_workflow_graph(
                "run-connector-denied",
                {"workflow_id": "wf_connector", "user_goal": "Send email", "metadata": {}},
                queue.Queue(),
                definition,
            )

        self.assertEqual(str(raised.exception), "Workflow stopped before tool node 'tool_1'.")
        connector_action_mock.assert_not_called()

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-discord", "discord_bot", {"bot_token": "discord-token", "channel_id": "123456"}),
    )
    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"id": "msg-1", "content": "hello"}, "text": ""},
    )
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    def test_execute_workflow_graph_runs_discord_connector_action(self, _approval_mock, _http_mock, _secret_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "discord_bot",
                        "action_id": "send_message",
                        "message": "Ship it",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-discord",
            {"workflow_id": "wf_discord", "user_goal": "Send Discord message", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("discord_bot.send_message", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=(
            "cred-whatsapp",
            "whatsapp_twilio",
            {
                "account_sid": "AC123",
                "auth_token": "token-123",
                "from_number": "whatsapp:+10000000000",
                "to_number": "whatsapp:+19999999999",
            },
        ),
    )
    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 201, "json": {"sid": "SM123"}, "text": ""},
    )
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    def test_execute_workflow_graph_runs_whatsapp_connector_action(self, _approval_mock, _http_mock, _secret_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "whatsapp_twilio",
                        "action_id": "send_message",
                        "message": "Ping from workflow",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-whatsapp",
            {"workflow_id": "wf_whatsapp", "user_goal": "Send WhatsApp", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("whatsapp_twilio.send_message", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-wechat", "wechat_work", {"webhook_url": "https://example.com/wechat"}),
    )
    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"errcode": 0, "errmsg": "ok"}, "text": ""},
    )
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    def test_execute_workflow_graph_runs_wechat_connector_action(self, _approval_mock, _http_mock, _secret_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "wechat_work",
                        "action_id": "send_message",
                        "message": "Post update",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-wechat",
            {"workflow_id": "wf_wechat", "user_goal": "Send WeChat", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("wechat_work.send_message", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-instagram", "instagram_business", {"access_token": "ig-token", "page_id": "998877"}),
    )
    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"id": "reply-1"}, "text": ""},
    )
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    def test_execute_workflow_graph_runs_instagram_publish_reply(self, _approval_mock, _http_mock, _secret_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "instagram_business",
                        "action_id": "publish_reply",
                        "comment_id": "112233",
                        "message": "Thanks for the comment.",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-instagram-reply",
            {"workflow_id": "wf_instagram_reply", "user_goal": "Reply to comment", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("instagram_business.publish_reply", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-instagram", "instagram_business", {"access_token": "ig-token", "page_id": "998877"}),
    )
    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"message_id": "dm-1"}, "text": ""},
    )
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    def test_execute_workflow_graph_runs_instagram_send_dm(self, _approval_mock, _http_mock, _secret_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "instagram_business",
                        "action_id": "send_dm",
                        "recipient_id": "445566",
                        "message": "Thanks for reaching out.",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-instagram-dm",
            {"workflow_id": "wf_instagram_dm", "user_goal": "Reply in DM", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("instagram_business.send_dm", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"ok": True, "id": "req-1"}, "text": ""},
    )
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    def test_execute_workflow_graph_runs_custom_api_http_request(self, _approval_mock, _http_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "custom_api",
                        "action_id": "http_request",
                        "method": "POST",
                        "url": "https://example.com/hook",
                        "payload": {"hello": "world"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-custom-api",
            {"workflow_id": "wf_custom_api", "user_goal": "Call API", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("custom_api.http_request", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch(
        "server_modules.runs_execution.generate_with_candidate_failover",
        side_effect=AssertionError("LLM fallback should not be used for simple field extraction"),
    )
    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={
            "status": 200,
            "json": {"origin": "203.0.113.9"},
            "text": '{"origin":"203.0.113.9"}',
            "headers": {"Content-Type": "application/json"},
        },
    )
    @patch(
        "server_modules.runs_execution.wait_for_human_decision",
        return_value=True,
    )
    def test_execute_workflow_graph_extracts_simple_json_field_without_llm(self, _approval_mock, _http_mock, _llm_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "http",
                    "config": {
                        "method": "GET",
                        "url": "https://httpbin.org/get",
                        "permissions": {
                            "trust_preset": "trusted_workflow",
                        },
                    },
                },
                {
                    "id": "agent_1",
                    "type": "agent",
                    "config": {
                        "identity": {
                            "name": "Extract origin",
                            "goal": "Extract the origin field",
                            "output_contract": "origin",
                        }
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "trigger_1", "target": "tool_1"},
                {"id": "e2", "source": "tool_1", "target": "agent_1"},
            ],
        }

        result = runs_execution._execute_workflow_graph(
            "run-http-origin",
            {"workflow_id": "wf_http_origin", "user_goal": "Fetch and extract origin", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertEqual(result["result_text"], "203.0.113.9")
        final_data = result["result_data"]["workflow_execution"]["final_data"]
        self.assertEqual(final_data["field_path"], "origin")
        self.assertTrue(final_data["extracted_without_llm"])

    def test_execute_workflow_graph_blocks_custom_api_private_url(self):
        with self.assertRaises(RuntimeError):
            runs_execution._workflow_execute_connector_action(
                "run-private-url",
                "node-private-url",
                {"workspace_id": "default", "metadata": {}},
                {
                    "connector": "custom_api",
                    "action_id": "http_request",
                    "url": "http://127.0.0.1:8080/hook",
                },
                current_text="Call API",
            )

    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"ok": True, "id": "req-dup-1"}, "text": ""},
    )
    def test_workflow_connector_action_duplicate_guard_reuses_prior_write(self, http_mock):
        first = runs_execution._workflow_execute_connector_action(
            "run-dup-connector",
            "tool-dup-1",
            {"workflow_id": "wf_dup", "workspace_id": "default", "metadata": {}},
            {
                "connector": "custom_api",
                "action_id": "http_request",
                "method": "POST",
                "url": "https://example.com/hook",
                "payload": {"hello": "world"},
            },
            current_text="Call API",
        )
        second = runs_execution._workflow_execute_connector_action(
            "run-dup-connector",
            "tool-dup-1",
            {"workflow_id": "wf_dup", "workspace_id": "default", "metadata": {}},
            {
                "connector": "custom_api",
                "action_id": "http_request",
                "method": "POST",
                "url": "https://example.com/hook",
                "payload": {"hello": "world"},
            },
            current_text="Call API",
        )

        self.assertEqual(http_mock.call_count, 1)
        self.assertTrue(first["result_data"]["connector_action"]["executed"])
        self.assertFalse(second["result_data"]["connector_action"]["executed"])
        self.assertTrue(second["result_data"]["connector_action"]["duplicate_protected"])
        self.assertIn("Skipped duplicate execution", second["summary"])

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-telegram", "telegram_bot", {"chat_id": "123"}),
    )
    def test_workflow_connector_action_degrades_when_connector_incident_is_active(self, _secret_mock):
        safe_mode_service.set_incident_control(
            scope="connector",
            mode="pause",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            connector_id="telegram_bot",
            credential_id="cred-telegram",
            reason="connector pause",
        )

        result = runs_execution._workflow_execute_connector_action(
            "run-incident",
            "tool-incident",
            {"tenant_id": "tenant-1", "workspace_id": "workspace-1", "metadata": {}},
            {
                "connector": "telegram_bot",
                "action_id": "send_message",
                "chat_id": "123",
            },
            current_text="Send alert",
        )

        action = result["result_data"]["connector_action"]
        self.assertTrue(action["degraded"])
        self.assertFalse(action["executed"])
        self.assertEqual(action["incident_mode"], "pause")

    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"ok": True, "id": "req-auth-1"}, "text": ""},
    )
    def test_workflow_connector_action_duplicate_guard_separates_custom_api_auth_fingerprint(self, http_mock):
        first = runs_execution._workflow_execute_connector_action(
            "run-auth-1",
            "tool-auth-1",
            {"workflow_id": "wf_auth", "workspace_id": "default", "metadata": {}},
            {
                "connector": "custom_api",
                "action_id": "http_request",
                "method": "POST",
                "url": "https://example.com/hook",
                "headers": {"Authorization": "Bearer token-a"},
                "payload": {"hello": "world"},
            },
            current_text="Call API",
        )
        second = runs_execution._workflow_execute_connector_action(
            "run-auth-1",
            "tool-auth-1",
            {"workflow_id": "wf_auth", "workspace_id": "default", "metadata": {}},
            {
                "connector": "custom_api",
                "action_id": "http_request",
                "method": "POST",
                "url": "https://example.com/hook",
                "headers": {"Authorization": "Bearer token-b"},
                "payload": {"hello": "world"},
            },
            current_text="Call API",
        )

        self.assertEqual(http_mock.call_count, 2)
        self.assertTrue(first["result_data"]["connector_action"]["executed"])
        self.assertTrue(second["result_data"]["connector_action"]["executed"])
        self.assertFalse(second["result_data"]["connector_action"]["duplicate_protected"])

    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"ok": True, "id": "req-root-1"}, "text": ""},
    )
    def test_workflow_connector_action_duplicate_guard_reuses_retry_root_across_run_ids(self, http_mock):
        context = {
            "workflow_id": "wf_retry_root",
            "workspace_id": "default",
            "metadata": {"retry_root_run_id": "retry-root-1"},
        }
        first = runs_execution._workflow_execute_connector_action(
            "run-root-1",
            "tool-root-1",
            context,
            {
                "connector": "custom_api",
                "action_id": "http_request",
                "method": "POST",
                "url": "https://example.com/hook",
                "payload": {"hello": "world"},
            },
            current_text="Call API",
        )
        second = runs_execution._workflow_execute_connector_action(
            "run-root-2",
            "tool-root-1",
            context,
            {
                "connector": "custom_api",
                "action_id": "http_request",
                "method": "POST",
                "url": "https://example.com/hook",
                "payload": {"hello": "world"},
            },
            current_text="Call API",
        )

        self.assertEqual(http_mock.call_count, 1)
        self.assertTrue(first["result_data"]["connector_action"]["executed"])
        self.assertFalse(second["result_data"]["connector_action"]["executed"])
        self.assertTrue(second["result_data"]["connector_action"]["duplicate_protected"])

    @patch(
        "server_modules.runs_execution.http_json_request",
        return_value={"status": 200, "json": {"id": "msg-acct-1", "content": "hello"}, "text": ""},
    )
    def test_workflow_connector_action_duplicate_guard_separates_connector_accounts(self, http_mock):
        with patch(
            "server_modules.runs_execution._workflow_tool_connector_secret",
            side_effect=[
                ("cred-discord-a", "discord_bot", {"bot_token": "discord-token-a", "channel_id": "123456"}),
                ("cred-discord-b", "discord_bot", {"bot_token": "discord-token-b", "channel_id": "123456"}),
            ],
        ):
            first = runs_execution._workflow_execute_connector_action(
                "run-account-1",
                "tool-account-1",
                {"workflow_id": "wf_account", "workspace_id": "default", "metadata": {}},
                {
                    "connector": "discord_bot",
                    "action_id": "send_message",
                    "message": "Ship it",
                },
                current_text="Ship it",
            )
            second = runs_execution._workflow_execute_connector_action(
                "run-account-1",
                "tool-account-1",
                {"workflow_id": "wf_account", "workspace_id": "default", "metadata": {}},
                {
                    "connector": "discord_bot",
                    "action_id": "send_message",
                    "message": "Ship it",
                },
                current_text="Ship it",
            )

        self.assertEqual(http_mock.call_count, 2)
        self.assertTrue(first["result_data"]["connector_action"]["executed"])
        self.assertTrue(second["result_data"]["connector_action"]["executed"])
        self.assertFalse(second["result_data"]["connector_action"]["duplicate_protected"])

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-gws-doc", "google_workspace", {"access_token": "token-doc"}),
    )
    @patch(
        "server_modules.runs_execution.google_workspace_create_document",
        return_value={"documentId": "doc-1"},
    )
    def test_workflow_connector_action_duplicate_guard_reuses_same_title_create_doc(self, doc_mock, _secret_mock):
        first = runs_execution._workflow_execute_connector_action(
            "run-doc-1",
            "tool-doc-1",
            {"workflow_id": "wf_doc", "workspace_id": "default", "metadata": {}},
            {
                "connector": "google_workspace",
                "action_id": "create_doc",
                "title": "Quarterly Plan",
            },
            current_text="Create doc",
        )
        second = runs_execution._workflow_execute_connector_action(
            "run-doc-1",
            "tool-doc-1",
            {"workflow_id": "wf_doc", "workspace_id": "default", "metadata": {}},
            {
                "connector": "google_workspace",
                "action_id": "create_doc",
                "title": "Quarterly Plan",
            },
            current_text="Create doc",
        )

        self.assertEqual(doc_mock.call_count, 1)
        self.assertTrue(first["result_data"]["connector_action"]["executed"])
        self.assertFalse(second["result_data"]["connector_action"]["executed"])
        self.assertTrue(second["result_data"]["connector_action"]["duplicate_protected"])

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-gws-mail", "google_workspace", {"auth_mode": "gws_local"}),
    )
    @patch(
        "server_modules.runs_execution.google_workspace_local_create_draft",
        side_effect=[
            {"id": "draft-new-1", "message": {"id": "msg-new-1"}},
            {"id": "draft-new-2", "message": {"id": "msg-new-2"}},
        ],
    )
    @patch("server_modules.runs_execution.google_workspace_uses_local_cli", return_value=True)
    def test_workflow_connector_action_new_explicit_run_executes_google_draft_again(self, _local_cli_mock, draft_mock, _secret_mock):
        first = runs_execution._workflow_execute_connector_action(
            "run-draft-new-1",
            "tool-draft-new-1",
            {"workflow_id": "wf_draft_new", "workspace_id": "default", "metadata": {}},
            {
                "connector": "google_workspace",
                "action_id": "draft_email",
                "to_email": "mansurao886@gmail.com",
                "subject": "Duplicate semantics draft",
                "text": "Draft this twice on purpose",
            },
            current_text="Draft this twice on purpose",
        )
        second = runs_execution._workflow_execute_connector_action(
            "run-draft-new-2",
            "tool-draft-new-1",
            {"workflow_id": "wf_draft_new", "workspace_id": "default", "metadata": {}},
            {
                "connector": "google_workspace",
                "action_id": "draft_email",
                "to_email": "mansurao886@gmail.com",
                "subject": "Duplicate semantics draft",
                "text": "Draft this twice on purpose",
            },
            current_text="Draft this twice on purpose",
        )

        self.assertEqual(draft_mock.call_count, 2)
        self.assertTrue(first["result_data"]["connector_action"]["executed"])
        self.assertTrue(second["result_data"]["connector_action"]["executed"])
        self.assertFalse(second["result_data"]["connector_action"]["duplicate_protected"])

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-gws-cal", "google_workspace", {"auth_mode": "gws_local", "calendar_id": "primary"}),
    )
    @patch(
        "server_modules.runs_execution.google_workspace_local_create_calendar_event",
        side_effect=[
            {"id": "event-new-1", "summary": "Duplicate semantics event"},
            {"id": "event-new-2", "summary": "Duplicate semantics event"},
        ],
    )
    @patch("server_modules.runs_execution.google_workspace_uses_local_cli", return_value=True)
    def test_workflow_connector_action_new_explicit_run_executes_google_calendar_again(self, _local_cli_mock, calendar_mock, _secret_mock):
        config = {
            "connector": "google_workspace",
            "action_id": "create_calendar_event",
            "title": "Duplicate semantics event",
            "start": "2026-03-30T15:00:00+00:00",
            "end": "2026-03-30T16:00:00+00:00",
            "timezone": "UTC",
            "description": "",
        }
        first = runs_execution._workflow_execute_connector_action(
            "run-calendar-new-1",
            "tool-calendar-new-1",
            {"workflow_id": "wf_calendar_new", "workspace_id": "default", "metadata": {}},
            dict(config),
            current_text="Create event twice on purpose",
        )
        second = runs_execution._workflow_execute_connector_action(
            "run-calendar-new-2",
            "tool-calendar-new-1",
            {"workflow_id": "wf_calendar_new", "workspace_id": "default", "metadata": {}},
            dict(config),
            current_text="Create event twice on purpose",
        )

        self.assertEqual(calendar_mock.call_count, 2)
        self.assertTrue(first["result_data"]["connector_action"]["executed"])
        self.assertTrue(second["result_data"]["connector_action"]["executed"])
        self.assertFalse(second["result_data"]["connector_action"]["duplicate_protected"])

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-gws-doc", "google_workspace", {"access_token": "token-doc"}),
    )
    @patch(
        "server_modules.runs_execution.google_workspace_create_document",
        return_value={"documentId": "doc-1"},
    )
    def test_workflow_connector_action_duplicate_guard_allows_create_doc_intent_token(self, doc_mock, _secret_mock):
        first = runs_execution._workflow_execute_connector_action(
            "run-doc-token",
            "tool-doc-token",
            {"workflow_id": "wf_doc_token", "workspace_id": "default", "metadata": {}},
            {
                "connector": "google_workspace",
                "action_id": "create_doc",
                "title": "Quarterly Plan",
                "intent_token": "intent-a",
            },
            current_text="Create doc",
        )
        second = runs_execution._workflow_execute_connector_action(
            "run-doc-token",
            "tool-doc-token",
            {"workflow_id": "wf_doc_token", "workspace_id": "default", "metadata": {}},
            {
                "connector": "google_workspace",
                "action_id": "create_doc",
                "title": "Quarterly Plan",
                "intent_token": "intent-b",
            },
            current_text="Create doc",
        )

        self.assertEqual(doc_mock.call_count, 2)
        self.assertTrue(first["result_data"]["connector_action"]["executed"])
        self.assertTrue(second["result_data"]["connector_action"]["executed"])
        self.assertFalse(second["result_data"]["connector_action"]["duplicate_protected"])

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-m365", "microsoft_365", {"access_token": "token"}),
    )
    @patch(
        "server_modules.runs_execution.microsoft_365_upload_drive_file",
        return_value={"id": "drive-item-1", "name": "report.txt"},
    )
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    def test_execute_workflow_graph_runs_microsoft_upload_drive_file(self, _approval_mock, _upload_mock, _secret_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "microsoft_365",
                        "action_id": "upload_drive_file",
                        "path": "reports/report.txt",
                        "content": "hello onedrive",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-onedrive-upload",
            {"workflow_id": "wf_onedrive", "user_goal": "Upload file", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("microsoft_365.upload_drive_file", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    @patch("server_modules.runs_execution.execute_outcome_pack")
    def test_execute_workflow_graph_runs_document_tool(self, execute_outcome_pack_mock, _approval_mock):
        execute_outcome_pack_mock.return_value = {
            "pack_id": "document-studio-v1",
            "summary": "Document Studio completed: created Word document.",
            "outputs": {
                "actions": [{"action": "document_create", "file_path": "notes.docx"}],
                "items_written": 1,
                "outbound_actions": 1,
                "urgent_count": 0,
            },
            "next_steps": ["Review the generated document."],
        }
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "document",
                    "config": {
                        "operation": "create",
                        "file_path": "notes.docx",
                        "title": "Notes",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-document",
            {"workflow_id": "wf_document", "user_goal": "Create doc", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("Document Studio completed", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    def test_execute_workflow_graph_rejects_code_tool_targeting_cloud(self):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "code",
                    "config": {
                        "execution_target": "cloud",
                        "command": "python",
                        "argv": ["-c", "print('hi')"],
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        with self.assertRaises(RuntimeError) as ctx:
            runs_execution._execute_workflow_graph(
                "run-code-cloud",
                {"workflow_id": "wf_code_cloud", "user_goal": "Run code", "metadata": {}},
                queue.Queue(),
                definition,
            )

        self.assertIn("cannot target cloud directly", str(ctx.exception))

    @patch("server_modules.run_service.create_workflow_child_local_run")
    def test_local_code_tool_requires_reviewed_execution_path(self, create_child_run_mock):
        with self.assertRaises(RuntimeError) as ctx:
            runs_execution._workflow_execute_local_tool(
                "run-code-local",
                {"metadata": {}},
                {
                    "code": "print('ok')",
                    "execution_target": "local_companion",
                    "permissions": {"file_mount_grants": []},
                },
                label="Run code",
                variant="code",
                current_text="",
            )

        self.assertIn("reviewed higher-trust execution path", str(ctx.exception))
        create_child_run_mock.assert_not_called()

    @patch("server_modules.run_service.create_workflow_child_local_run")
    def test_local_code_tool_rejects_shell_fields(self, create_child_run_mock):
        with self.assertRaises(RuntimeError) as ctx:
            runs_execution._workflow_execute_local_tool(
                "run-code-shell-fields",
                {"metadata": {}},
                {
                    "command": "python3 -c \"print('ok')\"",
                    "execution_target": "local_companion",
                    "permissions": {"file_mount_grants": []},
                },
                label="Run code",
                variant="code",
                current_text="",
            )

        self.assertIn("cannot use command, argv, or capability", str(ctx.exception))
        create_child_run_mock.assert_not_called()

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-telegram", "telegram_bot", {"chat_id": "12345"}),
    )
    @patch(
        "server_modules.runs_execution.handle_telegram_send_message",
        return_value={"ok": True, "message_id": "tg-1"},
    )
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    @patch(
        "server_modules.runs_execution.generate_with_candidate_failover",
        return_value="Thanks, your request has been triaged and assigned.",
    )
    @patch("server_modules.runs_execution._resolve_agent_generation_state")
    def test_launch_gate_connector_triage_workflow(self, resolve_state_mock, _generate_mock, _approval_mock, _telegram_mock, _secret_mock):
        resolve_state_mock.side_effect = lambda context, config: self._mock_agent_generation_state()
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {
                    "id": "trigger_1",
                    "type": "trigger",
                    "variant": "connector_event",
                    "config": {"connector": "telegram_bot", "event": "inbound_message"},
                },
                {
                    "id": "agent_1",
                    "type": "agent",
                    "config": {"identity": {"name": "Triage", "goal": "Classify and respond"}},
                },
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {"connector": "telegram_bot", "action_id": "send_message"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "trigger_1", "target": "agent_1"},
                {"id": "e2", "source": "agent_1", "target": "tool_1"},
            ],
        }

        result = runs_execution._execute_workflow_graph(
            "launch-connector-triage",
            {"workflow_id": "wf_launch_message", "user_goal": "Customer asked for help", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("telegram_bot.send_message", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch(
        "server_modules.runs_execution._workflow_tool_connector_secret",
        return_value=("cred-gws", "google_workspace", {"auth_mode": "gws_local"}),
    )
    @patch(
        "server_modules.runs_execution.google_workspace_local_create_draft",
        return_value={"id": "draft-1", "message": {"id": "msg-1"}},
    )
    @patch("server_modules.runs_execution.google_workspace_uses_local_cli", return_value=True)
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    @patch(
        "server_modules.runs_execution.wait_for_human_response",
        return_value={
            "approval_id": "approval-1",
            "correlation_id": "corr-1",
            "decision": "approve",
            "raw_decision": "approve",
            "note": "",
            "approved": True,
            "rejected": False,
            "escalated": False,
        },
    )
    def test_launch_gate_scheduled_approval_workflow(self, _human_mock, _approval_mock, _local_cli_mock, _draft_mock, _secret_mock):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "schedule", "config": {"cron": "0 9 * * 1-5"}},
                {"id": "data_1", "type": "data", "variant": "compose", "config": {"template": "Daily brief ready"}},
                {
                    "id": "approval_1",
                    "type": "human",
                    "variant": "approval",
                    "config": {"title": "Approve daily brief", "decision_options": ["approve", "reject"]},
                },
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "connector_action",
                    "config": {
                        "connector": "google_workspace",
                        "action_id": "draft_email",
                        "to_email": "ops@example.com",
                        "subject": "Daily brief",
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "trigger_1", "target": "data_1"},
                {"id": "e2", "source": "data_1", "target": "approval_1"},
                {"id": "e3", "source": "approval_1", "target": "tool_1"},
            ],
        }

        result = runs_execution._execute_workflow_graph(
            "launch-scheduled-approval",
            {"workflow_id": "wf_launch_schedule", "user_goal": "Send daily brief", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("google_workspace.draft_email", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch("server_modules.runs_execution.wait_for_human_response")
    @patch("server_modules.run_service.wait_for_workflow_child_run")
    @patch("server_modules.runs_execution._execute_workflow_child_run_request")
    def test_launch_gate_subflow_review_workflow(self, create_child_mock, wait_child_mock, human_mock):
        create_child_mock.return_value = {"run_id": "child-1", "route": {"selected": "cloud"}}
        wait_child_mock.return_value = {
            "status": "completed",
            "result": "Subflow prepared a draft response.",
            "result_data": {"summary": "child complete"},
        }
        human_mock.return_value = {
            "approval_id": "approval-review",
            "correlation_id": "corr-review",
            "decision": "Please publish after legal review",
            "raw_decision": "Please publish after legal review",
            "note": "",
            "approved": False,
            "rejected": False,
            "escalated": False,
        }

        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "workflow", "config": {"workflow_id": "parent"}},
                {"id": "subflow_1", "type": "subflow", "variant": "call_workflow", "config": {"workflow_id": "child_wf"}},
                {
                    "id": "review_1",
                    "type": "human",
                    "variant": "review",
                    "config": {"title": "Review child result", "instructions": "Share final note"},
                },
                {"id": "data_1", "type": "data", "variant": "compose", "config": {"template": "Final handoff"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger_1", "target": "subflow_1"},
                {"id": "e2", "source": "subflow_1", "target": "review_1"},
                {"id": "e3", "source": "review_1", "target": "data_1"},
            ],
        }

        result = runs_execution._execute_workflow_graph(
            "launch-subflow-review",
            {"workflow_id": "wf_launch_subflow", "user_goal": "Run child workflow then review", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "data_1")
        self.assertIn("Final handoff", result["result_text"])
        self.assertIn("Please publish after legal review", result["result_text"])

    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    @patch("server_modules.runs_execution.run_service.execute_workflow_local_tool")
    def test_execute_workflow_graph_runs_local_tool_variant(self, local_tool_mock, _wait_for_human_decision):
        local_tool_mock.return_value = {
            "summary": "Local tool node completed: Write file.",
            "result_data": {"local_child_run_id": "child-local-1"},
        }
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "file",
                    "config": {
                        "path": "README.md",
                        "mode": "write",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-local",
            {"workflow_id": "wf_local", "user_goal": "Write file", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertIn("Local tool node completed", result["result_text"])
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "tool_1")

    @patch("server_modules.run_service.create_workflow_child_local_run")
    def test_local_file_tool_blocks_local_root_without_grant(self, create_child_run_mock):
        with self.assertRaises(RuntimeError):
            runs_execution._workflow_execute_local_tool(
                "run-local-blocked",
                {"metadata": {}},
                {
                    "path": str(Path.cwd() / "README.md"),
                    "mode": "read",
                    "execution_target": "local_companion",
                    "permissions": {"file_mount_grants": []},
                },
                label="Read host file",
                variant="file",
                current_text="",
            )

        create_child_run_mock.assert_not_called()

    @patch("server_modules.run_service.create_workflow_child_local_run")
    def test_local_shell_tool_blocks_absolute_cwd_without_local_root_grant(self, create_child_run_mock):
        with self.assertRaises(RuntimeError):
            runs_execution._workflow_execute_local_tool(
                "run-shell-blocked",
                {"metadata": {}},
                {
                    "command": "pwd",
                    "cwd": str(Path.cwd()),
                    "execution_target": "local_companion",
                    "permissions": {"file_mount_grants": []},
                },
                label="Run shell",
                variant="shell",
                current_text="",
            )

        create_child_run_mock.assert_not_called()

    @patch("server_modules.run_service.create_workflow_child_local_run")
    def test_local_shell_tool_rejects_mixed_capability_and_command(self, create_child_run_mock):
        with self.assertRaises(RuntimeError):
            runs_execution._workflow_execute_local_tool(
                "run-shell-mixed",
                {"metadata": {}},
                {
                    "capability": "stack.status",
                    "command": "pwd",
                    "execution_target": "local_companion",
                    "permissions": {"file_mount_grants": []},
                },
                label="Run shell",
                variant="shell",
                current_text="",
            )

        create_child_run_mock.assert_not_called()

    @patch("server_modules.run_service.create_workflow_child_local_run")
    def test_browser_tool_requires_explicit_browser_permission_for_session_profile(self, create_child_run_mock):
        with self.assertRaises(RuntimeError):
            runs_execution._workflow_execute_local_tool(
                "run-browser-auth",
                {"metadata": {}},
                {
                    "url": "https://example.com",
                    "mode": "capture_page",
                    "session_profile": "default",
                    "execution_target": "local_companion",
                    "permissions": {
                        "file_mount_grants": [],
                        "browser_permissions": {"allow": False},
                    },
                },
                label="Capture page",
                variant="browser",
                current_text="",
            )

        create_child_run_mock.assert_not_called()

    @patch("server_modules.run_service.wait_for_workflow_child_run")
    @patch("server_modules.run_service.create_workflow_child_local_run")
    def test_browser_tool_passes_reviewed_interactive_metadata_to_child_run(self, create_child_run_mock, wait_child_mock):
        create_child_run_mock.return_value = "child-browser-run"
        wait_child_mock.return_value = {
            "status": "completed",
            "result": "Browser done",
            "result_data": {"summary": "Browser done"},
        }

        runs_execution._workflow_execute_local_tool(
            "run-browser-interactive",
            {"metadata": {}},
            {
                "url": "https://example.com",
                "mode": "capture_page",
                "session_profile": "default",
                "browser_actions": [{"action": "click", "selector": "#login"}],
                "execution_target": "local_companion",
                "permissions": {
                    "file_mount_grants": [],
                    "browser_permissions": {"allow": True},
                },
            },
            label="Capture page",
            variant="browser",
            current_text="",
        )

        metadata_overrides = create_child_run_mock.call_args.kwargs["metadata_overrides"]
        self.assertEqual(metadata_overrides["browser_session_profile"], "default")
        self.assertTrue(metadata_overrides["browser_reviewed_approval_required"])
        self.assertTrue(metadata_overrides["browser_immutable_plan_hash"])

    @patch("server_modules.runs_execution.run_service.execute_workflow_local_tool")
    @patch("server_modules.runs_execution.wait_for_human_decision", return_value=True)
    def test_execute_workflow_graph_requires_approval_for_session_backed_browser_tool(self, approval_mock, local_tool_mock):
        local_tool_mock.return_value = {
            "summary": "Browser automation completed.",
            "result_data": {"browser": {"url": "https://example.com"}},
        }
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "tool_1",
                    "type": "tool",
                    "variant": "browser",
                    "config": {
                        "url": "https://example.com/private",
                        "mode": "extract_text",
                        "session_profile": "default",
                        "execution_target": "local_companion",
                        "permissions": {
                            "action_policy": "guarded",
                            "file_mount_grants": [],
                            "browser_permissions": {"allow": True},
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-browser-approval",
            {"workflow_id": "wf_browser_guarded", "user_goal": "Read a private page", "metadata": {}},
            queue.Queue(),
            definition,
        )

        approval_mock.assert_called_once()
        self.assertIn("Browser automation completed", result["result_text"])

    @patch("server_modules.runs_execution._execute_workflow_child_run_request")
    def test_execute_workflow_graph_waits_for_subflow_completion(self, create_child_run_mock):
        create_child_run_mock.return_value = {
            "run_id": "child-run-1",
            "route": {"selected": "cloud"},
        }
        runs_execution.runs["child-run-1"] = {
            "status": "completed",
            "result": "Child workflow finished.",
            "result_data": {"summary": "Child workflow finished."},
        }
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "subflow_1",
                    "type": "subflow",
                    "variant": "call_workflow",
                    "config": {"workflow_id": "wf_child", "mode": "sync"},
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "subflow_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-parent",
            {
                "engine": "orion",
                "workflow_id": "wf_parent",
                "workspace_id": "default",
                "user_goal": "Run child workflow",
                "metadata": {},
            },
            queue.Queue(),
            definition,
        )

        self.assertEqual(result["result_text"], "Child workflow finished.")
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "subflow_1")

    @patch("server_modules.runs_execution._execute_workflow_child_run_request")
    def test_execute_workflow_graph_waits_for_local_companion_subflow_completion(self, create_child_run_mock):
        create_child_run_mock.return_value = {
            "run_id": "child-run-local-1",
            "route": {"selected": "local_companion"},
        }
        runs_execution.runs["child-run-local-1"] = {
            "status": "completed",
            "result": "Local child workflow finished.",
            "result_data": {"summary": "Local child workflow finished."},
        }
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "subflow_1",
                    "type": "subflow",
                    "variant": "call_workflow",
                    "config": {"workflow_id": "wf_child_local", "mode": "sync"},
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "subflow_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-parent-local-subflow",
            {
                "engine": "orion",
                "workflow_id": "wf_parent_local",
                "workspace_id": "default",
                "user_goal": "Run local child workflow",
                "metadata": {
                    "execution_target": "local_companion",
                    "execution_target_selected": "local_companion",
                },
            },
            queue.Queue(),
            definition,
        )

        self.assertEqual(result["result_text"], "Local child workflow finished.")
        self.assertEqual(result["result_data"]["workflow_execution"]["final_node_id"], "subflow_1")
        request = create_child_run_mock.call_args.args[0]
        self.assertEqual(request.metadata["execution_target"], "local_companion")

    @patch("server_modules.run_service.wait_for_workflow_child_run")
    @patch("server_modules.runs_execution._execute_workflow_child_run_request")
    def test_execute_workflow_graph_waits_for_subflow_human_input_and_resumes(self, create_child_run_mock, wait_child_mock):
        create_child_run_mock.return_value = {
            "run_id": "child-run-wait",
            "route": {"selected": "cloud"},
        }
        wait_child_mock.side_effect = lambda **kwargs: kwargs["on_waiting_for_input"](
            "child-run-wait",
            {
                "status": "waiting_for_input",
                "pending_approval": {"approval_id": "approval-child-1"},
                "result": None,
                "result_data": {},
            },
        ) or kwargs["on_resumed"](
            "child-run-wait",
            {"status": "running"},
        ) or {
            "status": "completed",
            "result": "Child workflow finished after approval.",
            "result_data": {"summary": "Child workflow finished after approval."},
        }
        log_queue = self._register_live_run("run-parent-wait")
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "subflow_1",
                    "type": "subflow",
                    "variant": "call_workflow",
                    "config": {"workflow_id": "wf_child", "mode": "sync"},
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "subflow_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-parent-wait",
            {
                "engine": "orion",
                "workflow_id": "wf_parent",
                "workspace_id": "default",
                "user_goal": "Run child workflow",
                "metadata": {},
            },
            log_queue,
            definition,
        )

        self.assertEqual(result["result_text"], "Child workflow finished after approval.")
        snapshot = runs_output._serialize_run_snapshot("run-parent-wait", runs_execution.runs["run-parent-wait"])
        items = {item["node_id"]: item for item in snapshot["node_states"]["items"]}
        self.assertEqual(items["subflow_1"]["status"], "succeeded")
        self.assertFalse(items["subflow_1"]["waiting_for_approval"])
        self.assertEqual(items["subflow_1"]["child_run_id"], "child-run-wait")


if __name__ == "__main__":
    unittest.main()
