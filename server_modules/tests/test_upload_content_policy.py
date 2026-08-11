"""Documents must not become a code-sharing surface.

Founder's instruction: "nobody must push their code, only files probably TXT
or MD or whatever, maybe pictures and some things like this." CLAUDE.md's
standing positioning is the reason it matters -- never build a coding
surface.

Two halves, deliberately:

  * the policy itself (upload_content_policy) -- extension allowlist,
    content refutation, size cap;
  * the ROUTE, driven through its real handler, because a policy module with
    no caller is this codebase's single most common defect ("built, tested,
    and never wired"). The route test is what proves the restriction is
    SERVER-SIDE: it never touches the browser and a rejection is a 400 from
    the handler itself.

Both live registrations of POST /api/sage-chat/attachments are exercised.
routes_workflows.py registers sage_context_files_api FIRST, so that one is
what FastAPI actually serves; sage_chat_api declares the same path and is
shadowed. Testing only the shadowed twin would assert nothing about what a
customer hits.
"""

from __future__ import annotations

import asyncio
import io
import sys
import types
import unittest
from unittest.mock import patch

from server_modules import sage_context_files_api
from server_modules import upload_content_policy


PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ZIP_HEADER = b"PK\x03\x04" + b"\x00" * 32
ELF_HEADER = b"\x7fELF" + b"\x00" * 32


class UploadContentPolicyTests(unittest.TestCase):
    def test_notes_and_pictures_are_accepted(self) -> None:
        self.assertEqual(
            upload_content_policy.assert_allowed_upload(filename="notes.md", data=b"# hi"),
            ".md",
        )
        self.assertEqual(
            upload_content_policy.assert_allowed_upload(filename="log.txt", data=b"plain"),
            ".txt",
        )
        self.assertEqual(
            upload_content_policy.assert_allowed_upload(filename="rows.csv", data=b"a,b\n1,2"),
            ".csv",
        )
        self.assertEqual(
            upload_content_policy.assert_allowed_upload(filename="shot.png", data=PNG_HEADER),
            ".png",
        )

    def test_source_files_are_rejected(self) -> None:
        for name in ("main.py", "index.ts", "App.tsx", "server.go", "lib.rs", "run.sh", "Dockerfile.txt.zip"):
            with self.subTest(name=name):
                with self.assertRaises(upload_content_policy.UploadRejected):
                    upload_content_policy.assert_allowed_upload(filename=name, data=b"print(1)")

    def test_archives_and_binaries_are_rejected(self) -> None:
        for name in ("bundle.zip", "app.tar", "tool.exe", "lib.so", "repo.git"):
            with self.subTest(name=name):
                with self.assertRaises(upload_content_policy.UploadRejected):
                    upload_content_policy.assert_allowed_upload(filename=name, data=b"\x00\x01")

    def test_rejection_says_what_is_accepted(self) -> None:
        """A rejection that does not name the accepted set sends the
        customer back to guess."""
        with self.assertRaises(upload_content_policy.UploadRejected) as caught:
            upload_content_policy.assert_allowed_upload(filename="main.py", data=b"print(1)")
        message = str(caught.exception)
        self.assertIn("Accepted files", message)
        self.assertIn("md", message)
        self.assertIn("png", message)

    def test_a_refutation_message_reads_as_english(self) -> None:
        """Caught on a real upload against a running server: the message read
        "That file is a archive". Customer-facing copy, so it is worth the
        assertion."""
        with self.assertRaises(upload_content_policy.UploadRejected) as caught:
            upload_content_policy.assert_allowed_upload(filename="notes.txt", data=ZIP_HEADER)
        self.assertIn("an archive", str(caught.exception))
        self.assertNotIn("a archive", str(caught.exception))

    def test_extension_is_the_decision_and_bytes_are_the_refutation(self) -> None:
        """A .txt holding a shell script is text and stays accepted -- that
        is not detectable and should not be. A .txt that is really a ZIP or
        an executable is a renamed binary, and that is."""
        upload_content_policy.assert_allowed_upload(
            filename="setup.txt", data=b"#!/bin/sh\nrm -rf /\n"
        )
        with self.assertRaises(upload_content_policy.UploadRejected):
            upload_content_policy.assert_allowed_upload(filename="notes.txt", data=ZIP_HEADER)
        with self.assertRaises(upload_content_policy.UploadRejected):
            upload_content_policy.assert_allowed_upload(filename="notes.md", data=ELF_HEADER)

    def test_an_image_extension_must_carry_a_real_image(self) -> None:
        with self.assertRaises(upload_content_policy.UploadRejected):
            upload_content_policy.assert_allowed_upload(filename="payload.png", data=b"#!/bin/sh\n")

    def test_svg_is_not_an_accepted_picture(self) -> None:
        """These files are served straight back by FileResponse; an SVG is a
        picture that is also a program."""
        self.assertNotIn(".svg", upload_content_policy.ALLOWED_EXTENSIONS)
        with self.assertRaises(upload_content_policy.UploadRejected):
            upload_content_policy.assert_allowed_upload(
                filename="logo.svg", data=b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
            )

    def test_size_cap(self) -> None:
        with self.assertRaises(upload_content_policy.UploadRejected):
            upload_content_policy.assert_allowed_upload(
                filename="big.txt", data=b"x" * 2048, max_bytes=1024
            )

    def test_declared_archive_content_type_is_refused_under_an_allowed_name(self) -> None:
        with self.assertRaises(upload_content_policy.UploadRejected):
            upload_content_policy.assert_allowed_upload(
                filename="notes.txt", data=b"harmless", content_type="application/zip"
            )


class _FakeUploadFile:
    def __init__(self, filename: str, content: bytes, content_type: str = "text/plain") -> None:
        self.filename = filename
        self.content_type = content_type
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


class AttachmentRouteEnforcesPolicyServerSideTests(unittest.TestCase):
    """The live POST /api/sage-chat/attachments handler -- sage_context_
    files_api's, the one routes_workflows.py registers first."""

    def _upload_route(self):
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
        sage_context_files_api.register_sage_context_file_routes(app)
        return app.routes[("POST", "/api/sage-chat/attachments")]

    def _call(self, upload_file, tmpdir):
        route = self._upload_route()
        from pathlib import Path

        with (
            patch("server_modules.sage_context_files_api.enforce_workspace_access", return_value="workspace-1"),
            patch("server_modules.sage_context_files_api.workspace_tenant_id", return_value="tenant-1"),
            patch(
                "server_modules.sage_context_files_api.workspace_attachments_dir",
                return_value=Path(tmpdir),
            ),
        ):
            return asyncio.run(
                route(
                    workspace_id="workspace-1",
                    file=upload_file,
                    current_user={"user_id": "user-1"},
                )
            )

    def test_a_markdown_note_is_accepted_and_written(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._call(_FakeUploadFile("notes.md", b"# hello"), tmpdir)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["safe_filename"].endswith(".md"))
            written = list(Path(tmpdir).iterdir())
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0].read_bytes(), b"# hello")

    def test_a_source_file_is_rejected_by_the_server_and_never_written(self) -> None:
        import tempfile
        from pathlib import Path
        from fastapi import HTTPException

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(HTTPException) as caught:
                self._call(_FakeUploadFile("main.py", b"print(1)", "text/x-python"), tmpdir)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertIn("Accepted files", str(caught.exception.detail))
            # Nothing reached disk: the policy runs before the write, not
            # after it. A check that fires after the bytes land is a report,
            # not a guardrail.
            self.assertEqual(list(Path(tmpdir).iterdir()), [])

    def test_a_zip_wearing_a_txt_name_is_rejected(self) -> None:
        import tempfile
        from fastapi import HTTPException

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(HTTPException) as caught:
                self._call(_FakeUploadFile("notes.txt", ZIP_HEADER), tmpdir)
            self.assertEqual(caught.exception.status_code, 400)


class BothRegistrationsShareOnePolicyTests(unittest.TestCase):
    """sage_chat_api declares the same path and is shadowed by
    sage_context_files_api. Two registrations of one route must not accept
    two different sets of files -- whichever one wins a future registration
    reshuffle."""

    def test_both_modules_call_the_shared_policy(self) -> None:
        import inspect

        from server_modules import sage_chat_api

        for module in (sage_context_files_api, sage_chat_api):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertIn("upload_content_policy.assert_allowed_upload", source)


if __name__ == "__main__":
    unittest.main()
