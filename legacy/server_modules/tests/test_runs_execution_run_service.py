import unittest
from unittest.mock import patch

from server_modules import runs_execution


class RunsExecutionRunServiceTests(unittest.TestCase):
    def test_create_run_delegates_live_creation_entrypoint_to_run_service(self) -> None:
        with patch.object(
            runs_execution.run_service,
            "create_live_run",
            return_value="run-1",
        ) as create_mock:
            run_id = runs_execution.create_run("orion", {"workspace_id": "default"})

        self.assertEqual(run_id, "run-1")
        create_mock.assert_called_once()
        _, kwargs = create_mock.call_args
        self.assertEqual(kwargs["runtime_db_path"], runs_execution.ORION_RUNTIME_STATE_DB)
        self.assertEqual(kwargs["local_companion_target"], runs_execution.EXECUTION_TARGET_LOCAL_COMPANION)
        self.assertIs(kwargs["persist_live_run_state_fn"], runs_execution._persist_live_run_state)
        self.assertIs(kwargs["inject_runtime_skill_defaults_fn"], runs_execution._inject_runtime_skill_defaults)

    def test_workflow_human_node_delegates_to_run_service(self) -> None:
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {
                    "id": "review_1",
                    "type": "human",
                    "variant": "review",
                    "config": {"title": "Review draft"},
                },
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "review_1"}],
        }

        with patch.object(
            runs_execution.run_service,
            "execute_workflow_human_node",
            side_effect=lambda **kwargs: kwargs["state"].__setitem__("last_text", "Reviewed reply") or kwargs["state"].__setitem__(
                "last_data",
                {"node_id": "review_1", "variant": "review"},
            ) or "Reviewed reply",
        ) as helper_mock:
            result = runs_execution._execute_workflow_graph(
                "run-review-delegate",
                {"workflow_id": "wf-review", "user_goal": "Review flow", "metadata": {}},
                __import__("queue").Queue(),
                definition,
                _track_node_states=False,
            )

        self.assertEqual(result["result_text"], "Reviewed reply")
        helper_mock.assert_called_once()
        _, kwargs = helper_mock.call_args
        self.assertEqual(kwargs["run_id"], "run-review-delegate")
        self.assertEqual(kwargs["node_id"], "review_1")
        self.assertEqual(kwargs["variant"], "review")

    def test_workflow_subflow_node_delegates_to_run_service(self) -> None:
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {"id": "subflow_1", "type": "subflow", "variant": "call_workflow", "config": {"workflow_id": "wf-child"}},
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "subflow_1"}],
        }

        with patch.object(
            runs_execution.run_service,
            "execute_workflow_subflow_node",
            side_effect=lambda **kwargs: kwargs["state"].__setitem__(
                "last_data",
                {"node_id": "subflow_1", "child_workflow_id": "wf-child"},
            ) or kwargs["state"].__setitem__("last_text", "Subflow reply") or "Subflow reply",
        ) as helper_mock:
            result = runs_execution._execute_workflow_graph(
                "run-subflow-delegate",
                {"workflow_id": "wf-parent", "user_goal": "Run child", "metadata": {}},
                __import__("queue").Queue(),
                definition,
                _track_node_states=False,
            )

        self.assertEqual(result["result_text"], "Subflow reply")
        helper_mock.assert_called_once()
        _, kwargs = helper_mock.call_args
        self.assertEqual(kwargs["run_id"], "run-subflow-delegate")
        self.assertEqual(kwargs["node_id"], "subflow_1")

    def test_workflow_local_tool_node_delegates_to_run_service(self) -> None:
        definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [
                {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                {"id": "tool_1", "type": "tool", "variant": "file", "config": {"path": "README.md", "mode": "write"}},
            ],
            "edges": [{"id": "e1", "source": "trigger_1", "target": "tool_1"}],
        }

        with (
            patch.object(runs_execution, "wait_for_human_decision", return_value=True),
            patch.object(
                runs_execution.run_service,
                "execute_workflow_local_tool",
                return_value={"summary": "Local tool finished", "result_data": {"local_child_run_id": "child-1"}},
            ) as helper_mock,
        ):
            result = runs_execution._execute_workflow_graph(
                "run-tool-delegate",
                {"workflow_id": "wf-tool", "user_goal": "Write file", "metadata": {}},
                __import__("queue").Queue(),
                definition,
                _track_node_states=False,
            )

        self.assertEqual(result["result_text"], "Local tool finished")
        helper_mock.assert_called_once()
        _, kwargs = helper_mock.call_args
        self.assertEqual(kwargs["run_id"], "run-tool-delegate")
        self.assertEqual(kwargs["label"], "tool_1")
        self.assertEqual(kwargs["variant"], "file")


if __name__ == "__main__":
    unittest.main()
