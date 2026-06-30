import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import agent_workspace_api
from server_modules import artifact_service
from server_modules.runtime_models import RunStartRequest


class AgentWorkspaceApiTests(unittest.TestCase):
    def test_execute_workspace_run_request_routes_through_turn_runtime(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="Write file demo.txt")

        with patch.object(
            agent_workspace_api,
            "_agent_workspace_run_execution_services",
            return_value=object(),
        ), patch.object(
            agent_workspace_api,
            "execute_system_run_start_request_via_turn_runtime",
            return_value={"run_id": "run-1", "status": "starting"},
        ) as execute_run:
            result = agent_workspace_api._execute_workspace_run_request(request)

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["status"], "starting")
        self.assertTrue(callable(execute_run.call_args.kwargs["stamp_request_owner_fn"]))

    def test_execute_workspace_run_request_async_routes_through_async_turn_runtime(self):
        request = RunStartRequest(engine="orion", workspace_id="default", user_goal="Write file demo.txt")

        with patch.object(
            agent_workspace_api,
            "_agent_workspace_run_execution_services",
            return_value=object(),
        ), patch.object(
            agent_workspace_api,
            "execute_async_run_start_request_via_turn_runtime",
            new=AsyncMock(return_value={"run_id": "run-async", "status": "starting"}),
        ) as execute_run:
            result = asyncio.run(agent_workspace_api._execute_workspace_run_request_async(request))

        self.assertEqual(result["run_id"], "run-async")
        self.assertEqual(result["status"], "starting")
        self.assertTrue(callable(execute_run.await_args.kwargs["stamp_request_owner_fn"]))
        self.assertEqual(execute_run.await_args.kwargs["current_user"]["auth_type"], "api_key")

    def test_collect_workspace_materials_preserves_canonical_artifact_metadata(self):
        snapshot = {
            "run_id": "run-1",
            "updated_at": "2026-04-07T00:00:00Z",
            "result_data": {
                "outputs": {
                    "artifacts": [
                        {
                            "artifact_id": "artifact-1",
                            "uri": "artifact://artifact-1/capture.png",
                            "kind": "screenshot",
                            "label": "capture.png",
                            "content_type": "image/png",
                            "byte_size": 123,
                            "created_at": "2026-04-07T00:00:00Z",
                            "step_number": 1,
                            "machine_id": "machine-1",
                            "storage_backend": "filesystem_object_store",
                            "storage_bucket": "empyralis-artifacts",
                            "storage_region": "us-east-1",
                            "storage_endpoint": "https://storage.example.test",
                        }
                    ]
                }
            },
        }
        files = []
        artifacts = []

        agent_workspace_api._collect_workspace_materials_for_snapshot(
            snapshot,
            files=files,
            artifacts=artifacts,
            seen_files=set(),
            seen_artifacts=set(),
            file_limit=20,
            artifact_limit=20,
        )

        self.assertEqual(files, [])
        self.assertEqual(artifacts[0]["uri_or_path"], "artifact://artifact-1/capture.png")
        self.assertEqual(artifacts[0]["artifact_id"], "artifact-1")
        self.assertEqual(artifacts[0]["content_type"], "image/png")
        self.assertEqual(artifacts[0]["byte_size"], 123)
        self.assertEqual(artifacts[0]["machine_id"], "machine-1")
        self.assertEqual(artifacts[0]["storage_bucket"], "empyralis-artifacts")
        self.assertEqual(artifacts[0]["storage_region"], "us-east-1")
        self.assertEqual(artifacts[0]["storage_endpoint"], "https://storage.example.test")

    def test_resolve_workspace_material_target_supports_canonical_artifact_uri(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "proof.txt"
            source.write_text("artifact body", encoding="utf-8")
            store_root = Path(tempdir) / "object-store"
            with patch.dict(os.environ, {"EMPYRALIS_OBJECT_STORAGE_ROOT": str(store_root)}, clear=False):
                record = artifact_service.store_artifact_file(source, run_id="run-1", kind="report")
                target = agent_workspace_api._resolve_workspace_material_target(record.uri)

            self.assertIsNotNone(target)
            self.assertEqual(target.read_text(encoding="utf-8"), "artifact body")

    def test_agent_workspace_channel_bindings_project_identity_fields_only(self):
        with patch.object(
            agent_workspace_api,
            "list_vault_connectors",
            create=True,
            return_value=[
                {
                    "id": "cred-telegram",
                    "connector": "telegram_bot",
                    "label": "Telegram Bot",
                    "workspace_id": "workspace-1",
                    "metadata": {},
                    "updated_at": "2026-04-10T00:00:00Z",
                }
            ],
        ), patch.object(
            agent_workspace_api,
            "resolve_vault_credential",
            create=True,
            return_value={"chat_id": "chat-123"},
        ) as resolve_secret, patch.object(
            agent_workspace_api,
            "normalize_agent_role",
            create=True,
            side_effect=lambda value: str(value or "").strip().lower(),
        ):
            bindings = agent_workspace_api._agent_workspace_channel_bindings(
                agent_role="orchestrator",
                workspace_id="workspace-1",
                telegram_payload={},
                whatsapp_payload={},
            )

        self.assertEqual(bindings[0]["identity_label"], "chat-123")
        self.assertEqual(resolve_secret.call_args.kwargs["purpose"], "channel_identity_projection")
        self.assertEqual(
            resolve_secret.call_args.kwargs["allowed_fields"],
            ["chat_id", "bot_username", "from_number", "phone_number", "emailAddress", "calendar_id"],
        )

    def test_ensure_artifacts_access_rejects_free_workspace_plan(self):
        with patch(
            "server_modules.agent_workspace_api.entitlements_service.workspace_entitlement_payload_for_workspace_id",
            return_value={"capabilities": {"artifacts_enabled": False}},
        ):
            with self.assertRaises(HTTPException) as exc:
                agent_workspace_api._ensure_artifacts_access_for_workspace("workspace-1")

        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(exc.exception.detail, "Artifacts are not included in this workspace plan.")

    def test_list_workspace_live_runs_bounded_pages_through_repository(self):
        with patch.object(
            agent_workspace_api.run_state_repository,
            "sync_list_live_runs_page",
            side_effect=[
                [{"run_id": "run-1"}, {"run_id": "run-2"}],
                [],
            ],
        ) as list_page:
            items = agent_workspace_api._list_workspace_live_runs_bounded(
                workspace_id="workspace-1",
                states={"running"},
                page_size=2,
            )

        self.assertEqual([item["run_id"] for item in items], ["run-1", "run-2"])
        self.assertEqual(list_page.call_args_list[0].kwargs["workspace_id"], "workspace-1")
        self.assertEqual(list_page.call_args_list[0].kwargs["states"], ["running"])

    def test_list_workspace_pending_approvals_bounded_pages_through_repository(self):
        with patch.object(
            agent_workspace_api.run_state_repository,
            "sync_list_pending_approvals_page",
            side_effect=[
                [{"approval_id": "approval-1"}, {"approval_id": "approval-2"}],
                [],
            ],
        ) as list_page:
            items = agent_workspace_api._list_workspace_pending_approvals_bounded(
                workspace_id="workspace-1",
                page_size=2,
            )

        self.assertEqual([item["approval_id"] for item in items], ["approval-1", "approval-2"])
        self.assertEqual(list_page.call_args_list[0].kwargs["workspace_id"], "workspace-1")


if __name__ == "__main__":
    unittest.main()
