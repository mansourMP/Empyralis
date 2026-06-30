import asyncio
import sys
import types
import unittest
from fastapi import HTTPException
from unittest.mock import patch

from server_modules import sage_context_files_api
from server_modules.schemas import SageContextFileUpdateRequest


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

    def patch(self, path, **kwargs):
        return self._register("PATCH", path, **kwargs)


class SageContextFilesApiTests(unittest.TestCase):
    def test_list_route_returns_workspace_context_files(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_context_files_api.register_sage_context_file_routes(app)
            route = app.routes[("GET", "/api/sage-context-files")]
            with (
                patch("server_modules.sage_context_files_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_context_files_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_context_files_api.read_workspace_context_files",
                    return_value={"IDENTITY.md": "# Identity\n\nSaved.\n"},
                ) as read_mock,
            ):
                payload = asyncio.run(route(workspace_id="workspace-1", current_user={"user_id": "user-1"}))
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["tenant_id"], "tenant-1")
            self.assertEqual(payload["files"], [{"filename": "IDENTITY.md", "content": "# Identity\n\nSaved.\n"}])
            self.assertEqual(read_mock.call_args.kwargs["workspace_id"], "workspace-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_update_route_persists_exact_context_file_content(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_context_files_api.register_sage_context_file_routes(app)
            route = app.routes[("PATCH", "/api/sage-context-files/{filename}")]
            content = "# Identity\n\n- exact saved text\n"
            with (
                patch("server_modules.sage_context_files_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_context_files_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_context_files_api.write_workspace_context_file",
                    return_value={"filename": "IDENTITY.md", "content": content},
                ) as write_mock,
            ):
                payload = asyncio.run(
                    route(
                        "IDENTITY.md",
                        SageContextFileUpdateRequest(workspace_id="workspace-1", content=content),
                        current_user={"user_id": "user-1"},
                    )
                )
            self.assertEqual(payload["filename"], "IDENTITY.md")
            self.assertEqual(payload["content"], content)
            self.assertEqual(write_mock.call_args.args, ("IDENTITY.md", content))
            self.assertEqual(write_mock.call_args.kwargs["workspace_id"], "workspace-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_update_route_rejects_invalid_filename_as_http_400(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_context_files_api.register_sage_context_file_routes(app)
            route = app.routes[("PATCH", "/api/sage-context-files/{filename}")]
            with (
                patch("server_modules.sage_context_files_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_context_files_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_context_files_api.write_workspace_context_file",
                    side_effect=ValueError("Unsupported context filename: ../escape.md"),
                ),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(
                        route(
                            "../escape.md",
                            SageContextFileUpdateRequest(workspace_id="workspace-1", content="# nope\n"),
                            current_user={"user_id": "user-1"},
                        )
                    )
            self.assertEqual(ctx.exception.status_code, 400)
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_update_route_blocks_cross_workspace_write(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_context_files_api.register_sage_context_file_routes(app)
            route = app.routes[("PATCH", "/api/sage-context-files/{filename}")]
            with (
                patch("server_modules.sage_context_files_api.enforce_workspace_access", return_value="workspace-allowed") as access_mock,
                patch("server_modules.sage_context_files_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_context_files_api.write_workspace_context_file",
                    return_value={"filename": "MEMORY.md", "content": "# ok\n"},
                ) as write_mock,
            ):
                payload = asyncio.run(
                    route(
                        "MEMORY.md",
                        SageContextFileUpdateRequest(workspace_id="workspace-denied", content="# should not leak\n"),
                        current_user={"user_id": "user-1"},
                    )
                )

            self.assertEqual(access_mock.call_args.args[1], "workspace-denied")
            self.assertEqual(write_mock.call_args.kwargs["workspace_id"], "workspace-allowed")
            self.assertEqual(payload["workspace_id"], "workspace-allowed")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_list_route_includes_all_context_files(self) -> None:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            sage_context_files_api.register_sage_context_file_routes(app)
            route = app.routes[("GET", "/api/sage-context-files")]
            with (
                patch("server_modules.sage_context_files_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.sage_context_files_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.sage_context_files_api.read_workspace_context_files",
                    return_value={
                        "SOUL.md": "# Empyralis\n",
                        "IDENTITY.md": "# Identity\n",
                        "memory/2026-05-10.md": "# Today",
                        "memory/.dreams/sample.md": "# Dream",
                    },
                ),
            ):
                payload = asyncio.run(
                    route(workspace_id="workspace-1", current_user={"user_id": "user-1"})
                )
            self.assertTrue(any(item["filename"] == "memory/2026-05-10.md" for item in payload["files"]))
            self.assertTrue(any(item["filename"] == "memory/.dreams/sample.md" for item in payload["files"]))
            self.assertTrue(any(item["filename"] == "IDENTITY.md" for item in payload["files"]))
            self.assertEqual(payload["workspace_id"], "workspace-1")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()
