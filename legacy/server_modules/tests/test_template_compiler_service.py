import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import runs_execution, template_compiler_service


def _install_bundle(*, outcome_pack: str | None = None, workflow_definition: dict | None = None):
    manifest: dict = {}
    if outcome_pack:
        manifest["outcome_pack"] = outcome_pack
    if workflow_definition is not None:
        manifest["workflow_definition"] = workflow_definition
    return {
        "id": "install-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "agent_definition_id": "agent-1",
        "agent_definition_version_id": "agentver-1",
        "runtime_profile_id": "profile-1",
        "tool_toggles": {
            "shell.execute": True,
            "computer_control": False,
        },
        "folder_grants": ["/Users/mansur/Desktop"],
        "connector_bindings": {"gmail": "cred-1"},
        "memory_scope_overrides": {"scope": "workspace"},
        "policy_context_overrides": {"trust_mode": "guarded"},
        "metadata": {},
        "agent_definition": {
            "id": "agent-1",
            "name": "Desktop Operator",
            "description": "Handles local desktop operations.",
            "slug": "desktop-operator",
        },
        "agent_definition_version": {
            "id": "agentver-1",
            "manifest": manifest,
            "policy_manifest": {},
        },
        "runtime_profile": {
            "id": "profile-1",
            "label": "My Mac",
            "runtime_class": "desktop_companion",
            "default_execution_target": "local_companion",
            "machine_id": "machine-1",
            "runtime_id": "runtime-1",
        },
    }


class TemplateCompilerServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_compile_install_template_preserves_graphs_for_dag_engine(self):
        bundle = _install_bundle(
            workflow_definition={
                "version": "empyralist.workflow.v2",
                "nodes": [
                    {"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}},
                    {"id": "data_1", "type": "data", "variant": "compose", "config": {"template": "Compiled ok"}},
                ],
                "edges": [
                    {"id": "edge_1", "source": "trigger_1", "target": "data_1"},
                ],
            }
        )

        compiled = template_compiler_service.compile_install_template(bundle)
        dag = runs_execution._compile_orion_dag(
            {
                "workflow_id": "wf-compiled",
                "workflow_version_id": "wfver-compiled",
                "workflow_definition": compiled["definition"],
                "metadata": compiled["run_metadata"],
            }
        )

        self.assertEqual(dag["type"], "workflow_graph")
        self.assertFalse(compiled["validation"]["hasDraftErrors"])
        self.assertEqual(compiled["run_metadata"]["execution_target"], "local_companion")
        self.assertIn("computer_control", compiled["run_metadata"]["action_policy"]["blocked_actions"])

    def test_compile_install_template_emits_outcome_pack_metadata_for_graphless_templates(self):
        bundle = _install_bundle(outcome_pack=runs_execution.LOCAL_EXECUTION_PACK_ID)

        compiled = template_compiler_service.compile_install_template(bundle)
        dag = runs_execution._compile_orion_dag(
            {
                "workflow_id": "wf-pack",
                "workflow_version_id": "wfver-pack",
                "workflow_definition": compiled["definition"],
                "metadata": compiled["run_metadata"],
            }
        )

        self.assertEqual(dag["type"], "outcome_pack")
        self.assertEqual(compiled["run_metadata"]["outcome_pack"], runs_execution.LOCAL_EXECUTION_PACK_ID)

    async def test_ensure_install_compiled_artifact_persists_compiled_version_on_install(self):
        bundle = _install_bundle(
            workflow_definition={
                "version": "empyralist.workflow.v2",
                "nodes": [{"id": "trigger_1", "type": "trigger", "variant": "manual", "config": {}}],
                "edges": [],
            }
        )
        artifact = {
            "id": "wf-compiled",
            "workflowVersionId": "wfver-compiled",
            "definition": {"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
            "validation": {},
            "metadata": {},
        }
        updated_install = {
            **bundle,
            "compiled_workflow_id": "wf-compiled",
            "compiled_workflow_version_id": "wfver-compiled",
        }

        with (
            patch("server_modules.template_compiler_service.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value=bundle)),
            patch("server_modules.template_compiler_service.agent_registry_repository.create_compiled_workflow_artifact", new=AsyncMock(return_value=artifact)) as create_mock,
            patch("server_modules.template_compiler_service.agent_registry_repository.update_workspace_agent_install_compiled_artifact", new=AsyncMock(return_value=updated_install)) as update_mock,
        ):
            compiled = await template_compiler_service.ensure_install_compiled_artifact("install-1")

        self.assertEqual(compiled["workflow_version_id"], "wfver-compiled")
        create_mock.assert_awaited_once()
        update_mock.assert_awaited_once()
        self.assertEqual(update_mock.await_args.kwargs["compiled_workflow_version_id"], "wfver-compiled")

    async def test_ensure_install_compiled_artifact_raises_for_missing_install(self):
        with patch(
            "server_modules.template_compiler_service.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await template_compiler_service.ensure_install_compiled_artifact("missing-install")

        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
