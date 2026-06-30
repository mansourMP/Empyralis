import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import app_registry_api
from server_modules.schemas import AppCaptainBridgeRequest, AppSpecialistBridgeRequest


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)


class AppBridgeSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        global app_registry_api, AppCaptainBridgeRequest, AppSpecialistBridgeRequest
        app_registry_api = importlib.import_module("server_modules.app_registry_api")
        schemas_module = importlib.import_module("server_modules.schemas")
        AppCaptainBridgeRequest = schemas_module.AppCaptainBridgeRequest
        AppSpecialistBridgeRequest = schemas_module.AppSpecialistBridgeRequest

    def test_app_bridge_requests_execute_canonical_master_and_specialist_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry_path = Path(tempdir) / "apps.json"
            fake_server = types.ModuleType("server")
            fake_server.Depends = lambda dependency: dependency
            fake_server.require_api_key = object()
            fake_server.HTTPException = HTTPException
            fake_server.ORION_APP_REGISTRY_FILE = registry_path
            fake_server._safe_read_json = lambda path, fallback: fallback
            fake_server._safe_write_json = lambda path, value: path.write_text("{}", encoding="utf-8")
            fake_server._utc_now_iso = lambda: "2026-04-11T00:00:00Z"

            previous_server = sys.modules.get("server")
            sys.modules["server"] = fake_server
            try:
                app = _FakeApp()
                app_registry_api.register_app_registry_routes(app)
                current_user = {
                    "auth_type": "bearer",
                    "user_id": "user-1",
                    "email": "user@example.com",
                    "role": "owner",
                    "workspace_access": {
                        "workspace-1": {"tenant_id": "tenant-1", "role": "owner"},
                    },
                }
                master_install = {
                    "id": "install-sage",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "agent_definition": {"agent_kind": "master", "slug": "sage", "name": "Sage"},
                }
                specialist_install = {
                    "id": "install-research",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "enabled": True,
                    "status": "active",
                    "agent_definition": {"agent_kind": "specialist", "slug": "research", "name": "Research"},
                    "agent_definition_version": {
                        "capability_manifest": {
                            "summary": ["research"],
                            "toggles": [{"id": "research", "label": "Research"}],
                        }
                    },
                }
                compiled_by_install = {
                    "install-sage": {
                        "install": {
                            **master_install,
                            "agent_definition_id": "agent-master",
                            "agent_definition_version_id": "agentver-master",
                            "runtime_profile_id": "profile-cloud",
                            "runtime_mode": "hosted_secure",
                            "runtime_profile": {
                                "id": "profile-cloud",
                                "label": "Cloud",
                                "runtime_class": "cloud_worker",
                                "runtime_id": "runtime-cloud",
                            },
                        },
                        "workflow_id": "wf-sage",
                        "workflow_version_id": "wfver-sage",
                        "workflow_snapshot": {
                            "id": "wf-sage",
                            "workflowVersionId": "wfver-sage",
                            "definition": {"version": "empyralist.workflow.v2"},
                        },
                        "run_metadata": {},
                    },
                    "install-research": {
                        "install": {
                            **specialist_install,
                            "agent_definition_id": "agent-research",
                            "agent_definition_version_id": "agentver-research",
                            "runtime_profile_id": "profile-cloud",
                            "runtime_mode": "hosted_secure",
                            "thread_id": "thread-install-research",
                            "runtime_profile": {
                                "id": "profile-cloud",
                                "label": "Cloud",
                                "runtime_class": "cloud_worker",
                                "runtime_id": "runtime-cloud",
                            },
                        },
                        "workflow_id": "wf-research",
                        "workflow_version_id": "wfver-research",
                        "workflow_snapshot": {
                            "id": "wf-research",
                            "workflowVersionId": "wfver-research",
                            "definition": {"version": "empyralist.workflow.v2"},
                        },
                        "run_metadata": {},
                    },
                }

                async def _compiled_artifact(install_id, **kwargs):
                    return compiled_by_install[str(install_id)]

                async def _start_turn(*, payload=None, current_user=None, **kwargs):
                    turn_request = importlib.import_module("server_modules.api_contract").request_body_to_turn_request(payload)
                    run_id = "run-sage"
                    if str(turn_request.context_hints.get("agent_role") or "").strip() == "research":
                        run_id = "run-specialist"
                    return SimpleNamespace(
                        turn_request=turn_request,
                        result={
                            "status": "accepted",
                            "run_id": run_id,
                            "engine": "orion",
                            "route": {"selected": "cloud"},
                            "doctor_preflight": {"blocking": False},
                            "created_run": {"run_id": run_id, "status": "queued"},
                        },
                    )

                with (
                    patch("server_modules.auth.enforce_workspace_access", return_value="workspace-1"),
                    patch("server_modules.auth.workspace_tenant_id", return_value="tenant-1"),
                    patch(
                        "server_modules.app_bridge_service._resolve_registry_app_item",
                        return_value={"id": "study", "status": "installed", "permissions": ["files.read"]},
                    ),
                    patch(
                        "server_modules.agent_registry_api.agent_registry_repository.get_workspace_master_agent_install",
                        new=AsyncMock(return_value=master_install),
                    ),
                    patch(
                        "server_modules.agent_registry_api.agent_registry_repository.list_workspace_agent_installs",
                        new=AsyncMock(return_value=[specialist_install]),
                    ),
                    patch(
                        "server_modules.agent_registry_api.agent_registry_repository.get_workspace_agent_install_bundle",
                        new=AsyncMock(return_value=specialist_install),
                    ),
                    patch(
                        "server_modules.agent_registry_api.template_compiler_service.ensure_install_compiled_artifact",
                        new=AsyncMock(side_effect=_compiled_artifact),
                    ),
                    patch(
                        "server_modules.agent_registry_api.runtime_attachment_service.resolve_install_runtime_plan",
                        new=AsyncMock(
                            return_value={
                                "runtime_mode": "hosted_secure",
                                "deployment_mode": "cloud",
                                "selected_attachment": {
                                    "attachment_id": "managed_cloud:profile-cloud",
                                    "attachment_kind": "managed_cloud",
                                },
                                "machine_target": None,
                                "execution_target_selected": "cloud",
                            }
                        ),
                    ),
                    patch("server_modules.agent_registry_api.thread_service.ensure_master_thread", new=AsyncMock()) as ensure_master_thread,
                    patch("server_modules.agent_registry_api._run_execution_services", return_value=object()),
                    patch(
                        "server_modules.app_bridge_service.record_app_bridge_audit",
                        new=AsyncMock(side_effect=[{"id": "audit-sage"}, {"id": "audit-specialist"}]),
                    ),
                    patch(
                        "server_modules.agent_registry_api.turn_ingress_service.start_turn",
                        new=AsyncMock(side_effect=_start_turn),
                    ) as turn_mock,
                ):
                    captain_result = asyncio.run(
                        app.routes[("POST", "/apps/bridge/captain")](
                            AppCaptainBridgeRequest(
                                workspace_id="workspace-1",
                                app_id="study",
                                bridge_type="summary_request",
                                request_text="Summarize the notes I imported.",
                                context_envelope={
                                    "user_selected_inputs": {"topic": "biology"},
                                    "explicit_imports": [{"kind": "note", "id": "n-1"}],
                                },
                            ),
                            current_user=current_user,
                        )
                    )
                    specialist_result = asyncio.run(
                        app.routes[("POST", "/apps/bridge/specialist")](
                            AppSpecialistBridgeRequest(
                                workspace_id="workspace-1",
                                app_id="study",
                                bridge_type="task_request",
                                target_capability="research",
                                request_text="Research CRISPR delivery methods and summarize the tradeoffs.",
                                context_envelope={
                                    "user_selected_inputs": {"topic": "crispr"},
                                    "explicit_imports": [{"kind": "summary", "id": "summary-1"}],
                                },
                            ),
                            current_user=current_user,
                        )
                    )

                self.assertEqual(captain_result["turn_result"]["run_id"], "run-sage")
                self.assertEqual(captain_result["audit"]["activity_event_id"], "audit-sage")
                self.assertEqual(captain_result["bridge"]["bridge_kind"], "app_to_sage")
                self.assertEqual(specialist_result["turn_result"]["run_id"], "run-specialist")
                self.assertEqual(specialist_result["bridge"]["target"]["target_install_id"], "install-research")
                self.assertEqual(specialist_result["audit"]["activity_event_id"], "audit-specialist")
                ensure_master_thread.assert_awaited_once()

                sage_turn_request = turn_mock.await_args_list[0].kwargs["payload"]
                sage_turn_request = importlib.import_module("server_modules.api_contract").request_body_to_turn_request(sage_turn_request)
                self.assertEqual(sage_turn_request.thread_id, "thread_sage_workspace-1_user-1")
                self.assertEqual(
                    sage_turn_request.context_hints["metadata"]["app_bridge"]["bridge_kind"],
                    "app_to_sage",
                )
                self.assertEqual(
                    sage_turn_request.context_hints["metadata"]["app_context_envelope"]["classes"],
                    ["explicit_imports_from_sage", "user_selected_inputs"],
                )

                specialist_turn_request = turn_mock.await_args_list[1].kwargs["payload"]
                specialist_turn_request = importlib.import_module("server_modules.api_contract").request_body_to_turn_request(specialist_turn_request)
                self.assertEqual(specialist_turn_request.thread_id, "thread-install-research")
                self.assertEqual(
                    specialist_turn_request.context_hints["metadata"]["app_bridge"]["bridge_kind"],
                    "app_to_specialist",
                )
                self.assertEqual(
                    specialist_turn_request.context_hints["metadata"]["app_bridge"]["target"]["target_install_id"],
                    "install-research",
                )
                self.assertNotIn("captain_context", specialist_turn_request.context_hints["metadata"])
            finally:
                if previous_server is None:
                    sys.modules.pop("server", None)
                else:
                    sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()
