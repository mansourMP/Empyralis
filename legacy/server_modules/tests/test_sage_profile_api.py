import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from server_modules import sage_profile_api


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

    def patch(self, path, **kwargs):
        return self._register("PATCH", path, **kwargs)


class SageProfileApiTests(unittest.TestCase):
    def test_get_route_returns_bootstrap_payload(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_profile_api.register_sage_profile_routes(app)
            route = app.routes[("GET", "/api/sage-profile")]
            with (
                patch("server_modules.sage_profile_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_profile_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_profile_api.list_sage_profile",
                    return_value={"profile": {"user_name": "Mansur"}, "bootstrap": {"complete": False}},
                ) as list_mock,
            ):
                payload = asyncio.run(
                    route(workspace_id="workspace-1", current_user={"user_id": "user-1", "name": "Mansur", "email": "m@example.com"})
                )
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertFalse(payload["bootstrap"]["complete"])
            self.assertEqual(list_mock.call_args.kwargs["account_seed"]["display_name"], "Mansur")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_update_route_passes_profile_patch(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_profile_api.register_sage_profile_routes(app)
            route = app.routes[("PATCH", "/api/sage-profile")]
            with (
                patch("server_modules.sage_profile_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_profile_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_profile_api.upsert_sage_profile",
                    return_value={"profile": {"user_name": "Mansur"}, "bootstrap": {"complete": True}},
                ) as update_mock,
            ):
                payload = asyncio.run(
                    route(
                        sage_profile_api.SageProfileUpdateRequest(
                            workspace_id="workspace-1",
                            user_name="Mansur",
                            identity_summary="Builds Empyralis.",
                        ),
                        current_user={"user_id": "user-1"},
                    )
                )
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(update_mock.call_args.kwargs["actor_user_id"], "user-1")
            self.assertEqual(update_mock.call_args.kwargs["identity_summary"], "Builds Empyralis.")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_bootstrap_answer_route_passes_answer(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_profile_api.register_sage_profile_routes(app)
            route = app.routes[("POST", "/api/sage-profile/bootstrap/answer")]
            with (
                patch("server_modules.sage_profile_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_profile_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_profile_api.answer_sage_profile_bootstrap",
                    return_value={"bootstrap": {"complete": False, "current_question": {"id": "identity_summary"}}},
                ) as answer_mock,
            ):
                payload = asyncio.run(
                    route(
                        sage_profile_api.SageProfileBootstrapAnswerRequest(
                            workspace_id="workspace-1",
                            answer="Mansur",
                        ),
                        current_user={"user_id": "user-1"},
                    )
                )
            self.assertFalse(payload["bootstrap"]["complete"])
            self.assertEqual(answer_mock.call_args.kwargs["answer"], "Mansur")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()
