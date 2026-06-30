import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import run_state_repository, runs_execution, runs_output, shared
from server_modules.runtime_state_store import init_runtime_state_db


class WorkflowLoopTests(unittest.TestCase):
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
        for patcher in reversed(self.patchers):
            patcher.stop()
        shared.sync_acp_manager_paths(runtime_db_path=runs_execution.ORION_RUNTIME_STATE_DB)
        runs_execution.ACP_MANAGER.reload_runtime_state()
        self.tmpdir.cleanup()

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

    def test_foreach_iterates_over_array(self):
        log_queue = self._register_live_run("run-foreach")
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "loop_1",
                    "type": "loop",
                    "variant": "for_each",
                    "config": {
                        "array_source": ["alpha", "beta", "gamma"],
                        "item_variable_name": "item",
                        "body": {
                            "version": "empyralist.workflow.v2",
                            "nodes": [
                                {
                                    "id": "agent_1",
                                    "type": "agent",
                                    "variant": "",
                                    "config": {
                                        "identity": {"name": "Loop worker", "role": "worker", "goal": "Echo loop item"},
                                        "runtime": {"provider": "openai", "model": "gpt-4o-mini"},
                                    },
                                }
                            ],
                            "edges": [],
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "loop_1"}],
        }

        with (
            patch("server_modules.runs_execution._resolve_agent_generation_state", side_effect=lambda context, config: self._mock_agent_generation_state()),
            patch("server_modules.runs_execution.generate_with_candidate_failover", side_effect=["first", "second", "third"]),
        ):
            result = runs_execution._execute_workflow_graph(
                "run-foreach",
                {"workflow_id": "wf_foreach", "user_goal": "Loop through items", "metadata": {}},
                log_queue,
                definition,
            )

        last_node_data = result["result_data"]["last_node_data"]
        self.assertEqual(len(last_node_data["results"]), 3)
        self.assertEqual(last_node_data["results"][0]["result_text"], "first")
        snapshot = runs_output._serialize_run_snapshot("run-foreach", runs_execution.runs["run-foreach"])
        items = {item["node_id"]: item for item in snapshot["node_states"]["items"]}
        self.assertEqual(items["loop_1"]["status"], "succeeded")

    def test_while_exits_on_condition(self):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "loop_1",
                    "type": "loop",
                    "variant": "while",
                    "config": {
                        "expression": 'result_data["text"] != "stop"',
                        "body": {
                            "version": "empyralist.workflow.v2",
                            "nodes": [
                                {
                                    "id": "agent_1",
                                    "type": "agent",
                                    "variant": "",
                                    "config": {
                                        "identity": {"name": "While worker", "role": "worker", "goal": "Emit state"},
                                        "runtime": {"provider": "openai", "model": "gpt-4o-mini"},
                                    },
                                }
                            ],
                            "edges": [],
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "loop_1"}],
        }

        with (
            patch("server_modules.runs_execution._resolve_agent_generation_state", side_effect=lambda context, config: self._mock_agent_generation_state()),
            patch("server_modules.runs_execution.generate_with_candidate_failover", side_effect=["continue", "stop"]),
        ):
            result = runs_execution._execute_workflow_graph(
                "run-while",
                {"workflow_id": "wf_while", "user_goal": "Loop while condition holds", "metadata": {}},
                queue.Queue(),
                definition,
            )

        self.assertEqual(result["result_text"], "stop")

    def test_repeat_runs_n_times(self):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "loop_1",
                    "type": "loop",
                    "variant": "repeat",
                    "config": {
                        "count": 3,
                        "body": {
                            "version": "empyralist.workflow.v2",
                            "nodes": [
                                {"id": "data_1", "type": "data", "variant": "compose", "config": {"template": "repeat body"}}
                            ],
                            "edges": [],
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "loop_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-repeat",
            {"workflow_id": "wf_repeat", "user_goal": "Repeat a few times", "metadata": {}},
            queue.Queue(),
            definition,
        )

        last_node_data = result["result_data"]["last_node_data"]
        self.assertEqual(len(last_node_data["results"]), 3)

    def test_loop_respects_max_iterations_cap(self):
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "loop_1",
                    "type": "loop",
                    "variant": "for_each",
                    "config": {
                        "array_source": [1, 2, 3, 4, 5],
                        "max_iterations": 2,
                        "body": {
                            "version": "empyralist.workflow.v2",
                            "nodes": [
                                {"id": "data_1", "type": "data", "variant": "compose", "config": {"template": "capped"}}
                            ],
                            "edges": [],
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "loop_1"}],
        }

        result = runs_execution._execute_workflow_graph(
            "run-cap",
            {"workflow_id": "wf_cap", "user_goal": "Cap loop iterations", "metadata": {}},
            queue.Queue(),
            definition,
        )

        self.assertEqual(len(result["result_data"]["last_node_data"]["results"]), 2)

    def test_nested_loop_depth_limit(self):
        innermost_body = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {
                    "id": "loop_4",
                    "type": "loop",
                    "variant": "repeat",
                    "config": {
                        "count": 1,
                        "body": {
                            "version": "empyralist.workflow.v2",
                            "nodes": [
                                {"id": "data_leaf", "type": "data", "variant": "compose", "config": {"template": "leaf"}}
                            ],
                            "edges": [],
                        },
                    },
                }
            ],
            "edges": [],
        }
        body_3 = {
            "version": "empyralist.workflow.v2",
            "nodes": [{"id": "loop_3", "type": "loop", "variant": "repeat", "config": {"count": 1, "body": innermost_body}}],
            "edges": [],
        }
        body_2 = {
            "version": "empyralist.workflow.v2",
            "nodes": [{"id": "loop_2", "type": "loop", "variant": "repeat", "config": {"count": 1, "body": body_3}}],
            "edges": [],
        }
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {"id": "loop_1", "type": "loop", "variant": "repeat", "config": {"count": 1, "body": body_2}},
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "loop_1"}],
        }

        with self.assertRaises(RuntimeError):
            runs_execution._execute_workflow_graph(
                "run-nested",
                {"workflow_id": "wf_nested", "user_goal": "Too many nested loops", "metadata": {}},
                queue.Queue(),
                definition,
            )

    def test_loop_cancellable(self):
        log_queue = self._register_live_run("run-cancel-loop")
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "loop_1",
                    "type": "loop",
                    "variant": "for_each",
                    "config": {
                        "array_source": ["a", "b", "c"],
                        "body": {
                            "version": "empyralist.workflow.v2",
                            "nodes": [
                                {
                                    "id": "agent_1",
                                    "type": "agent",
                                    "variant": "",
                                    "config": {
                                        "identity": {"name": "Cancel worker", "role": "worker", "goal": "Cancel after first iteration"},
                                        "runtime": {"provider": "openai", "model": "gpt-4o-mini"},
                                    },
                                }
                            ],
                            "edges": [],
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "loop_1"}],
        }

        def _cancel_after_first(*_args, **_kwargs):
            runs_execution.runs["run-cancel-loop"]["status"] = "cancelled"
            return "cancelled after first iteration"

        with patch("server_modules.runs_execution.generate_with_candidate_failover", side_effect=_cancel_after_first):
            with self.assertRaises(RuntimeError):
                runs_execution._execute_workflow_graph(
                    "run-cancel-loop",
                    {"workflow_id": "wf_cancel", "user_goal": "Cancel the loop", "metadata": {}},
                    log_queue,
                    definition,
                )


if __name__ == "__main__":
    unittest.main()
