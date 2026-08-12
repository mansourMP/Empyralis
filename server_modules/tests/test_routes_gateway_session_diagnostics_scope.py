from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import routes_gateway


class SessionDiagnosticsExportScopeTests(unittest.TestCase):
    """GET /diagnostics/sessions/{session_id}/export -- security-review
    finding 2026-08-13: session_diagnostics_service.export_session_trace
    resolves ANY session_id on the platform with no tenant/workspace
    predicate of its own (session_service.get_session is a bare
    `WHERE session_id = $1`). The route's own comparison
    (`session_workspace_id != resolved_workspace_id`) is the ONLY boundary
    between "my own session" and someone else's -- and it used to be
    written `if session_workspace_id and session_workspace_id != resolved`,
    which FAILS OPEN for any session row whose recorded workspace_id is
    empty/missing: the full diagnostics payload (channel, actor identity,
    timestamps, redacted-but-not-fully metadata) went to any authenticated
    caller of any tenant who could name or guess a session_id."""

    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(routes_gateway.router, prefix="/api")
        self.app.dependency_overrides[routes_gateway.require_api_key] = lambda: self._current_user()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()

    @staticmethod
    def _current_user():
        return {
            "auth_type": "api_key",
            "role": "owner",
            "is_admin": True,
            "user_id": "owner-b",
            "workspace_roles": {"ws-b": "owner"},
            "workspace_access": {
                "ws-b": {
                    "workspace_id": "ws-b",
                    "tenant_id": "tenant-b",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        }

    def _export_trace_mock(self, session_workspace_id):
        return AsyncMock(
            return_value={
                "diagnostics_version": 1,
                "generated_at": "2026-08-13T00:00:00Z",
                "session": {
                    "session_id": "sess-victim",
                    "workspace_id": session_workspace_id,
                    "tenant_id": "tenant-a",
                    "channel": "web",
                    "created_at": "2026-08-01T00:00:00Z",
                    "expires_at": "2026-09-01T00:00:00Z",
                    "status": "active",
                },
                "metadata": {},
            }
        )

    def test_cross_tenant_session_export_is_rejected(self) -> None:
        with patch(
            "server_modules.session_diagnostics_service.export_session_trace",
            new=self._export_trace_mock("ws-a"),
        ):
            response = self.client.get(
                "/api/diagnostics/sessions/sess-victim/export",
                params={"workspace_id": "ws-b"},
            )
        self.assertEqual(response.status_code, 403)

    def test_session_with_no_recorded_workspace_id_fails_closed_not_open(self) -> None:
        # This is the exact fail-open shape: an empty/missing workspace_id on
        # the session row used to be treated as "no conflict, allow it"
        # instead of "cannot prove this is the caller's own session, deny".
        with patch(
            "server_modules.session_diagnostics_service.export_session_trace",
            new=self._export_trace_mock(""),
        ):
            response = self.client.get(
                "/api/diagnostics/sessions/sess-victim/export",
                params={"workspace_id": "ws-b"},
            )
        self.assertEqual(response.status_code, 403)

    def test_same_tenant_session_export_still_succeeds(self) -> None:
        with patch(
            "server_modules.session_diagnostics_service.export_session_trace",
            new=self._export_trace_mock("ws-b"),
        ):
            response = self.client.get(
                "/api/diagnostics/sessions/sess-victim/export",
                params={"workspace_id": "ws-b"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session"]["workspace_id"], "ws-b")


if __name__ == "__main__":
    unittest.main()
