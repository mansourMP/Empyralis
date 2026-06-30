import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import runs_history, security_audit_service
from server_modules.connectors.channel_workspace_scope_service import resolve_connector_workspace_scope


class Phase73AuthorizationBoundaryTests(unittest.TestCase):
    def test_resolve_connector_workspace_scope_requires_explicit_workspace(self) -> None:
        with self.assertRaises(RuntimeError):
            resolve_connector_workspace_scope(
                {"id": "conn-1", "label": "Main", "workspace_id": "default"},
                normalize_workspace_id=lambda value: str(value or "").strip(),
                fallback_workspace_id="default",
                detail_prefix="Telegram connector",
            )

        resolved = resolve_connector_workspace_scope(
            {"id": "conn-1", "label": "Main", "workspace_id": "ws-1"},
            normalize_workspace_id=lambda value: str(value or "").strip(),
            fallback_workspace_id="default",
            detail_prefix="Telegram connector",
        )

        self.assertEqual(resolved, "ws-1")

    def test_list_pending_approvals_rejects_inaccessible_workspace(self) -> None:
        current_user = {
            "auth_type": "bearer",
            "user_id": "user-1",
            "workspace_access": {
                "ws-1": {
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }

        with self.assertRaises(HTTPException) as exc:
            self._run_async(runs_history.list_pending_approvals(workspace_id="ws-2", current_user=current_user))

        self.assertEqual(exc.exception.status_code, 403)

    def test_get_approval_audit_filters_by_workspace(self) -> None:
        current_user = {
            "auth_type": "bearer",
            "user_id": "user-1",
            "workspace_access": {
                "ws-1": {
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }
        with runs_history.APPROVAL_AUDIT_LOCK:
            original_items = list(runs_history.APPROVAL_AUDIT)
            runs_history.APPROVAL_AUDIT[:] = [
                {"approval_id": "approval-1", "run_id": "run-1", "workspace_id": "ws-1"},
                {"approval_id": "approval-2", "run_id": "run-2", "workspace_id": "ws-2"},
            ]
        try:
            with (
                patch.object(runs_history, "_owned_run_ids_for_user", return_value={"run-1", "run-2"}),
                patch.object(
                    runs_history.entitlements_service,
                    "workspace_entitlement_payload_for_workspace_id",
                    return_value={"capabilities": {"approvals_enabled": True}},
                ),
            ):
                payload = self._run_async(
                    runs_history.get_approval_audit(
                        workspace_id="ws-1",
                        current_user=current_user,
                    )
                )
        finally:
            with runs_history.APPROVAL_AUDIT_LOCK:
                runs_history.APPROVAL_AUDIT[:] = original_items

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["approval_id"], "approval-1")

    def test_security_audit_event_uses_security_event_type(self) -> None:
        captured = {}

        with patch.object(security_audit_service.outbox_service, "emit_runtime_event") as mock_emit:
            mock_emit.side_effect = lambda **kwargs: captured.update(kwargs) or None
            security_audit_service.emit_security_audit_event(
                action="auth.login",
                tenant_id="tenant-1",
                workspace_id="ws-1",
                actor_user_id="user-1",
                actor_email="user@example.com",
            )

        self.assertEqual(captured["event_type"], "security_audit")
        self.assertEqual(captured["tenant_id"], "tenant-1")
        self.assertEqual(captured["workspace_id"], "ws-1")
        self.assertEqual(captured["payload"]["action"], "auth.login")

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
