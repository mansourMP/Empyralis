import asyncio
import importlib
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import runtime_attachment_service
from server_modules.agent_turn import build_run_start_turn_request
from server_modules.run_service import (
    RunExecutionServices,
    build_delegated_child_run_request,
    execute_durable_turn_request,
)
from server_modules.runtime_models import RunStartRequest


class MultiRuntimeDemoProofTests(unittest.TestCase):
    def setUp(self) -> None:
        global runtime_attachment_service
        global build_run_start_turn_request
        global RunExecutionServices
        global build_delegated_child_run_request
        global execute_durable_turn_request
        global RunStartRequest

        runtime_attachment_service = importlib.import_module("server_modules.runtime_attachment_service")
        agent_turn = importlib.import_module("server_modules.agent_turn")
        run_service = importlib.import_module("server_modules.run_service")
        runtime_models = importlib.import_module("server_modules.runtime_models")

        build_run_start_turn_request = agent_turn.build_run_start_turn_request
        RunExecutionServices = run_service.RunExecutionServices
        build_delegated_child_run_request = run_service.build_delegated_child_run_request
        execute_durable_turn_request = run_service.execute_durable_turn_request
        RunStartRequest = runtime_models.RunStartRequest

    def _runtime_profile(self, runtime_mode: str) -> dict:
        if runtime_mode in {"local_secure", "privileged_device"}:
            return {
                "id": "profile-local",
                "label": "Local Box",
                "runtime_class": "desktop_companion",
                "runtime_id": "runtime-local",
                "machine_id": "machine-local",
            }
        return {
            "id": "profile-cloud",
            "label": "Empyralis Cloud",
            "runtime_class": "cloud_worker",
            "runtime_id": "runtime-cloud",
        }

    def _install_bundle(self, runtime_mode: str, install_id: str = "install-1") -> dict:
        return {
            "id": install_id,
            "tenant_id": "default",
            "workspace_id": "default",
            "runtime_mode": runtime_mode,
            "runtime_profile_id": self._runtime_profile(runtime_mode)["id"],
            "runtime_profile": self._runtime_profile(runtime_mode),
        }

    def _execute_mode(self, *, runtime_mode: str, runtime_selection: dict) -> tuple[dict, dict, list[dict]]:
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Research the topic and draft a summary",
            provider="openai",
            model="gpt-test",
            metadata={
                "owner_user_id": "user-1",
                "active_agent_install_id": "install-1",
                "runtime_mode": runtime_mode,
            },
        )
        turn_request = build_run_start_turn_request(run_request)
        captured_metadata: dict = {}
        doctor_calls: list[dict] = []

        def _create_run(req):
            captured_metadata.update(dict(req.metadata or {}))
            return {"run_id": "run-1", "status": "starting"}

        async def _doctor(**kwargs):
            doctor_calls.append(dict(kwargs))
            return {"blocking": False, "title": "ok"}

        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=_create_run,
        )

        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=self._install_bundle(runtime_mode)),
        ), patch(
            "server_modules.runtime_attachment_service.resolve_install_runtime_plan",
            new=AsyncMock(return_value=runtime_selection),
        ), patch(
            "server_modules.run_service.decide_execution_target",
            return_value={"selected": runtime_selection["execution_target_selected"]},
        ), patch(
            "server_modules.run_service.apply_execution_route_metadata",
            side_effect=lambda metadata, route: {**metadata, "execution_target_selected": route["selected"]},
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=_doctor,
        ):
            execution = asyncio.run(
                execute_durable_turn_request(
                    turn_request=turn_request,
                    current_user={"user_id": "user-1"},
                    services=services,
                    base_request=run_request,
                )
            )

        return execution, captured_metadata, doctor_calls

    def test_cloud_only_durable_turn_carries_hosted_runtime_metadata(self) -> None:
        execution, captured, doctor_calls = self._execute_mode(
            runtime_mode="hosted_secure",
            runtime_selection={
                "runtime_mode": "hosted_secure",
                "deployment_mode": "cloud_only",
                "execution_target_selected": "cloud",
                "selected_attachment": {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                },
                "degraded_mode": {"state": "normal"},
                "sync_enforcement": {
                    "effective_sync_class": "sync_allowed",
                    "summary_bridge_status": {"last_safe_summary_available": False},
                },
            },
        )

        self.assertEqual(execution["result"]["run_id"], "run-1")
        self.assertEqual(captured["runtime_mode"], "hosted_secure")
        self.assertEqual(captured["runtime_scope"]["mode"], "hosted_secure")
        self.assertEqual(captured["runtime_attachment_id"], "managed_cloud:profile-cloud")
        self.assertEqual(captured["runtime_attachment_kind"], "managed_cloud")
        self.assertEqual(captured["workspace_runtime_deployment_mode"], "cloud_only")
        self.assertEqual(captured["execution_target_selected"], "cloud")
        self.assertEqual(captured["runtime_selection"]["deployment_mode"], "cloud_only")
        self.assertEqual(doctor_calls[0]["execution_target"], "cloud")

    def test_local_only_durable_turn_carries_local_runtime_metadata(self) -> None:
        execution, captured, doctor_calls = self._execute_mode(
            runtime_mode="local_secure",
            runtime_selection={
                "runtime_mode": "local_secure",
                "deployment_mode": "local_only",
                "machine_target": "machine-local",
                "execution_target_selected": "local_companion",
                "selected_attachment": {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                },
                "degraded_mode": {"state": "normal"},
                "sync_enforcement": {"effective_sync_class": "local_private"},
            },
        )

        self.assertEqual(execution["result"]["run_id"], "run-1")
        self.assertEqual(captured["runtime_mode"], "local_secure")
        self.assertEqual(captured["runtime_scope"]["mode"], "local_secure")
        self.assertEqual(captured["runtime_attachment_id"], "local_companion:profile-local")
        self.assertEqual(captured["runtime_attachment_kind"], "local_companion")
        self.assertEqual(captured["workspace_runtime_deployment_mode"], "local_only")
        self.assertEqual(captured["machine_target"], "machine-local")
        self.assertEqual(captured["execution_target_selected"], "local_companion")
        self.assertEqual(captured["runtime_selection"]["deployment_mode"], "local_only")
        self.assertEqual(doctor_calls[0]["execution_target"], "local_companion")

    def test_hybrid_online_durable_turn_carries_hybrid_local_metadata(self) -> None:
        execution, captured, doctor_calls = self._execute_mode(
            runtime_mode="local_secure",
            runtime_selection={
                "runtime_mode": "local_secure",
                "deployment_mode": "hybrid",
                "machine_target": "machine-local",
                "execution_target_selected": "local_companion",
                "selected_attachment": {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                },
                "hybrid_policy": {
                    "effective_sync_class": "sync_allowed",
                    "shared_sage_identity": True,
                },
                "sync_enforcement": {"effective_sync_class": "sync_allowed"},
                "degraded_mode": {"state": "normal"},
            },
        )

        self.assertEqual(execution["result"]["run_id"], "run-1")
        self.assertEqual(captured["workspace_runtime_deployment_mode"], "hybrid")
        self.assertEqual(captured["execution_target_selected"], "local_companion")
        self.assertEqual(captured["machine_target"], "machine-local")
        self.assertEqual(captured["runtime_selection"]["deployment_mode"], "hybrid")
        self.assertEqual(captured["runtime_selection"]["hybrid_policy"]["effective_sync_class"], "sync_allowed")
        self.assertEqual(doctor_calls[0]["execution_target"], "local_companion")

    def test_hybrid_degraded_durable_turn_carries_summary_bridge_fallback_metadata(self) -> None:
        execution, captured, doctor_calls = self._execute_mode(
            runtime_mode="hosted_secure",
            runtime_selection={
                "runtime_mode": "hosted_secure",
                "deployment_mode": "hybrid",
                "execution_target_selected": "cloud",
                "selected_attachment": {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                },
                "sync_enforcement": {
                    "effective_sync_class": "summary_bridge_only",
                    "summary_bridge_status": {"last_safe_summary_available": True},
                },
                "degraded_mode": {
                    "state": "local_offline_summary_bridge",
                    "last_safe_summary_only": True,
                },
            },
        )

        self.assertEqual(execution["result"]["run_id"], "run-1")
        self.assertEqual(captured["runtime_mode"], "hosted_secure")
        self.assertEqual(captured["runtime_scope"]["mode"], "hosted_secure")
        self.assertEqual(captured["workspace_runtime_deployment_mode"], "hybrid")
        self.assertEqual(captured["execution_target_selected"], "cloud")
        self.assertEqual(captured["runtime_selection"]["degraded_mode"]["state"], "local_offline_summary_bridge")
        self.assertTrue(captured["runtime_selection"]["degraded_mode"]["last_safe_summary_only"])
        self.assertEqual(
            captured["runtime_selection"]["sync_enforcement"]["effective_sync_class"],
            "summary_bridge_only",
        )
        self.assertEqual(doctor_calls[0]["execution_target"], "cloud")

    def test_durable_turn_rejects_unresolved_degraded_fallback(self) -> None:
        run_request = RunStartRequest(
            engine="orion",
            workspace_id="default",
            user_goal="Research the topic and draft a summary",
            provider="openai",
            model="gpt-test",
            metadata={
                "owner_user_id": "user-1",
                "active_agent_install_id": "install-1",
                "runtime_mode": "hosted_secure",
            },
        )
        turn_request = build_run_start_turn_request(run_request)
        services = RunExecutionServices(
            stamp_request_owner=lambda req, current_user: req,
            prepare_run_start_request=lambda req: {"metadata": dict(req.metadata or {})},
            create_run_from_request=lambda req: {"run_id": "run-1", "status": "starting"},
        )

        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value=self._install_bundle("hosted_secure")),
        ), patch(
            "server_modules.runtime_attachment_service.resolve_install_runtime_plan",
            new=AsyncMock(
                side_effect=runtime_attachment_service.RuntimeAttachmentSelectionError(
                    "No last-safe summary snapshot is available for degraded hybrid execution.",
                    reason="summary_bridge_snapshot_unavailable",
                )
            ),
        ), patch(
            "server_modules.run_service.build_doctor_run_gate_live",
            new=AsyncMock(return_value={"blocking": False, "title": "ok"}),
        ):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(
                    execute_durable_turn_request(
                        turn_request=turn_request,
                        current_user={"user_id": "user-1"},
                        services=services,
                        base_request=run_request,
                    )
                )

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("last-safe summary snapshot", error.exception.detail)

    def test_delegated_child_request_preserves_runtime_mode_metadata(self) -> None:
        request = build_delegated_child_run_request(
            {
                "run_id": "parent-run",
                "engine": "orion",
                "agent_role": "sage",
                "context": {
                    "workflow_id": "workflow-1",
                    "workspace_id": "default",
                    "business_plan": "Research and summarize",
                    "max_iterations": 6,
                    "provider": "openai",
                    "model": "gpt-test",
                    "metadata": {
                        "active_agent_install_id": "install-1",
                        "runtime_mode": "hosted_secure",
                        "runtime_scope": {"mode": "hosted_secure", "driver": "cloud_worker"},
                        "runtime_selection": {
                            "execution_target_selected": "cloud",
                            "deployment_mode": "hybrid",
                            "selected_attachment": {
                                "attachment_id": "managed_cloud:profile-cloud",
                                "attachment_kind": "managed_cloud",
                            },
                            "sync_enforcement": {
                                "effective_sync_class": "summary_bridge_only",
                                "summary_bridge_status": {"last_safe_summary_available": True},
                            },
                            "degraded_mode": {
                                "state": "local_offline_summary_bridge",
                                "last_safe_summary_only": True,
                            },
                        },
                        "runtime_attachment_id": "managed_cloud:profile-cloud",
                        "runtime_attachment_kind": "managed_cloud",
                        "workspace_runtime_deployment_mode": "hybrid",
                        "execution_target_selected": "cloud",
                        "owner_user_id": "user-1",
                    },
                },
            },
            {
                "user_goal": "Run the research specialist",
                "agent_role": "research",
                "metadata": {},
            },
            normalize_run_id_token=lambda value: str(value or "").strip() or None,
            normalize_agent_role=lambda value: str(value or "").strip().lower(),
            normalize_requested_max_iterations=lambda value: int(value) if value is not None else None,
            valid_execution_targets={"local_companion", "cloud", "auto"},
            note="Continue through degraded-safe bridge mode.",
        )

        self.assertNotIn("runtime_mode", request.metadata)
        self.assertNotIn("runtime_scope", request.metadata)
        self.assertNotIn("workspace_runtime_deployment_mode", request.metadata)
        self.assertNotIn("runtime_attachment_id", request.metadata)
        self.assertNotIn("runtime_selection", request.metadata)
        self.assertNotIn("execution_target", request.metadata)


if __name__ == "__main__":
    unittest.main()
