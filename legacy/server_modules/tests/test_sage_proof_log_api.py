from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import sage_chat_api


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


class SageProofLogApiTests(unittest.TestCase):
    def _with_routes(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        app = _FakeApp()
        sage_chat_api.register_sage_chat_routes(app)
        return app, previous_server

    def _restore_server(self, previous_server):
        if previous_server is None:
            sys.modules.pop("server", None)
        else:
            sys.modules["server"] = previous_server

    def test_list_sage_proof_logs_enforces_workspace_and_returns_items(self):
        app, previous_server = self._with_routes()
        try:
            route = app.routes[("GET", "/api/sage/proof-logs")]
            with (
                patch("server_modules.sage_chat_api.enforce_workspace_access", return_value="workspace-1") as access_mock,
                patch("server_modules.sage_chat_api._resolve_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_chat_api.sage_proof_log_service.list_proof_logs",
                    return_value={"items": [{"proof_id": "proof-1"}], "total_count": 1, "count": 1},
                ) as list_mock,
            ):
                payload = asyncio.run(
                    route(
                        workspace_id="workspace-1",
                        status="completed",
                        surface="chat",
                        limit=10,
                        current_user={"user_id": "user-1"},
                    )
                )
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["tenant_id"], "tenant-1")
            self.assertEqual(payload["items"][0]["proof_id"], "proof-1")
            self.assertEqual(access_mock.call_args.kwargs["minimum_role"], "viewer")
            self.assertEqual(list_mock.call_args.kwargs["status"], "completed")
            self.assertEqual(list_mock.call_args.kwargs["surface"], "chat")
        finally:
            self._restore_server(previous_server)

    def test_summary_route_returns_aggregate_payload(self):
        app, previous_server = self._with_routes()
        try:
            route = app.routes[("GET", "/api/sage/proof-logs/summary")]
            with (
                patch("server_modules.sage_chat_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_chat_api._resolve_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_chat_api.sage_proof_log_service.summarize_proof_logs",
                    return_value={"total_count": 2, "checked_count": 3},
                ),
            ):
                payload = asyncio.run(route(workspace_id="workspace-1", current_user={"user_id": "user-1"}))
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["tenant_id"], "tenant-1")
            self.assertEqual(payload["total_count"], 2)
            self.assertEqual(payload["checked_count"], 3)
        finally:
            self._restore_server(previous_server)

    def test_detail_route_returns_404_for_missing_proof_log(self):
        app, previous_server = self._with_routes()
        try:
            route = app.routes[("GET", "/api/sage/proof-logs/{proof_id}")]
            with (
                patch("server_modules.sage_chat_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_chat_api._resolve_tenant_id", return_value="tenant-1"),
                patch("server_modules.sage_chat_api.sage_proof_log_service.get_proof_log", return_value=None),
            ):
                with self.assertRaises(HTTPException) as caught:
                    asyncio.run(route(proof_id="missing", workspace_id="workspace-1", current_user={"user_id": "user-1"}))
            self.assertEqual(caught.exception.status_code, 404)
        finally:
            self._restore_server(previous_server)


if __name__ == "__main__":
    unittest.main()
