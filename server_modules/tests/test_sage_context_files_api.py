import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from server_modules import assistant_context_files_api


class _FakeUploadFile:
    """Mirrors the two members of Starlette's UploadFile the route touches:
    the sync `.file` handle and the ASYNC `.read()`. The route reads the
    bytes up front now (upload_content_policy has to see them before
    anything lands on disk), so a stand-in without `read()` would keep this
    file green while diverging from the real object."""

    def __init__(self, filename: str, content: bytes, content_type: str = "text/plain") -> None:
        self.filename = filename
        self.content_type = content_type
        import io

        self._content = content
        self.file = io.BytesIO(content)

    async def read(self) -> bytes:
        return self._content


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


class SageContextFilesApiTaxonomyRemovalTests(unittest.TestCase):
    """Founder ruling (2026-07-23, final): the two SOUL.md/MEMORY.md/GOALS.md/
    etc. "context file" routes this module used to register (GET
    /api/sage-context-files, PATCH /api/sage-context-files/{filename}) are
    removed -- confirmed zero frontend callers. This test asserts they no
    longer register, and that the unrelated /api/sage-chat/attachments
    routes (still live -- workstation-client.ts:1215) keep working through
    the same registration function."""

    def _register(self) -> _FakeApp:
        fake_server = types.ModuleType("server")
        fake_server.Depends = lambda dependency: dependency
        fake_server.require_api_key = object()
        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        self.addCleanup(
            lambda: sys.modules.pop("server", None)
            if previous_server is None
            else sys.modules.__setitem__("server", previous_server)
        )
        app = _FakeApp()
        assistant_context_files_api.register_assistant_context_file_routes(app)
        return app

    def test_context_file_routes_are_not_registered(self) -> None:
        app = self._register()
        self.assertNotIn(("GET", "/api/sage-context-files"), app.routes)
        self.assertNotIn(("PATCH", "/api/sage-context-files/{filename}"), app.routes)

    def test_attachment_routes_still_registered_and_reachable(self) -> None:
        app = self._register()
        self.assertIn(("POST", "/api/sage-chat/attachments"), app.routes)
        self.assertIn(("GET", "/api/sage-chat/attachments/{filename}"), app.routes)

        upload_route = app.routes[("POST", "/api/sage-chat/attachments")]
        with (
            patch("server_modules.assistant_context_files_api.enforce_workspace_access", return_value="workspace-1"),
            patch("server_modules.assistant_context_files_api.workspace_tenant_id", return_value="tenant-1"),
        ):
            payload = asyncio.run(
                upload_route(
                    workspace_id="workspace-1",
                    file=_FakeUploadFile("note.txt", b"hello world"),
                    current_user={"user_id": "user-1"},
                )
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["workspace_id"], "workspace-1")
        self.assertEqual(payload["size"], len(b"hello world"))


if __name__ == "__main__":
    unittest.main()
