"""Every route that hands a stored file back to a browser sets
`X-Content-Type-Options: nosniff`, and that is enforced structurally rather
than per-route.

THE GAP THIS CLOSES
--------------------
`upload_content_policy.py`'s own docstring: "SVG is deliberately NOT an
accepted image. These files are served straight back by FileResponse; an SVG
carries script and would execute on the workspace's own origin." That
reasoning covers the WRITE side (the extension allowlist) but the four
`FileResponse(...)` call sites that actually serve a file back — three in
`agent_workspace_api.py`, one in `sage_context_files_api.py` — built the
response with no `X-Content-Type-Options` header at all, so a browser was
free to MIME-sniff the body instead of trusting the declared Content-Type.
Verified live against a real e2e stack (2026-08-13, frontend/attachment
injection security review): current Chrome does NOT execute a `.txt`
attachment whose content is a full HTML document even without this header
(it renders as an escaped `<pre>`), so this is hardening rather than a
confirmed live bypass in mainstream browsers today -- but nosniff is exactly
the header that makes "the extension decided the Content-Type" a promise
the BROWSER also has to honor rather than one only this server makes, and it
costs nothing: it never changes what Content-Type is declared, only whether
sniffing may override it.

`safe_file_response` (server_modules/safe_file_response.py) is the one
place a `FileResponse` may be constructed now. `NoDirectFileResponseCallTests`
below is the structural half: a source scan across every `server_modules/*.py`
file (except the wrapper's own module) for a direct `FileResponse(` call,
matching the AST/source-scan idiom this codebase already uses for the same
kind of "guard on the narrow waist" concern (see test_unguarded_reply_paths.py's
StructuralGuardSeamTests). A behavioural test can only cover the four call
sites that exist today; this catches the fifth one a future PR adds without
importing the wrapper.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from server_modules.safe_file_response import (
    NOSNIFF_HEADER_NAME,
    NOSNIFF_HEADER_VALUE,
    safe_file_response,
)

_SERVER_MODULES_ROOT = Path(__file__).resolve().parent.parent
_WRAPPER_MODULE_NAME = "safe_file_response.py"

# Files that legitimately construct a FileResponse THROUGH the wrapper are
# fine (they call `safe_file_response(...)`, never `FileResponse(...)`
# directly) -- this allowlist exists for a module that has a documented
# reason to be exempt from the rule, and today nothing does.
_ALLOWED_DIRECT_CALLERS: frozenset[str] = frozenset()


class NoDirectFileResponseCallTests(unittest.TestCase):
    """A future `FileResponse(...)` call site must import the wrapper
    instead of the class directly, or this fails -- the expected set (this
    allowlist) and the actual set (what's on disk) come from different
    places, the same rule CLAUDE.md's own RLS-coverage-check fix documents."""

    def test_no_module_calls_fileresponse_directly(self) -> None:
        offenders: list[str] = []
        for path in sorted(_SERVER_MODULES_ROOT.glob("*.py")):
            if path.name == _WRAPPER_MODULE_NAME:
                continue
            if path.name in _ALLOWED_DIRECT_CALLERS:
                continue
            text = path.read_text(encoding="utf-8")
            if "FileResponse(" in text:
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            "these modules construct FileResponse directly, bypassing the "
            "nosniff guard -- use server_modules.safe_file_response.safe_file_response instead: "
            f"{offenders}",
        )

    def test_the_four_known_seams_import_the_wrapper(self) -> None:
        """Written verdicts, not just "no raw FileResponse" -- pins the
        actual seams this review found so a rename that keeps the file
        raw-FileResponse-free by accident (e.g. moving the call into a
        helper that itself skips the wrapper) is still caught."""
        expected = {
            "agent_workspace_api.py": 3,
            "sage_context_files_api.py": 1,
        }
        for filename, expected_count in expected.items():
            path = _SERVER_MODULES_ROOT / filename
            text = path.read_text(encoding="utf-8")
            # "safe_file_response(" (with the open paren) only ever matches
            # an actual call -- the import statement reads
            # "...import safe_file_response" with nothing after it, so it
            # never contributes to this count and needs no subtraction.
            actual_count = text.count("safe_file_response(")
            self.assertGreaterEqual(
                actual_count,
                expected_count,
                f"{filename}: expected at least {expected_count} safe_file_response(...) call(s), found {actual_count}",
            )


class SafeFileResponseHeaderTests(unittest.TestCase):
    """Behavioural half: the header is actually on the response object."""

    def test_nosniff_header_present_by_default(self) -> None:
        response = safe_file_response(path=__file__)
        self.assertEqual(
            response.headers.get(NOSNIFF_HEADER_NAME),
            NOSNIFF_HEADER_VALUE,
        )

    def test_nosniff_header_survives_caller_supplied_headers(self) -> None:
        response = safe_file_response(
            path=__file__,
            headers={"Content-Disposition": "attachment; filename=\"x.txt\""},
        )
        self.assertEqual(response.headers.get(NOSNIFF_HEADER_NAME), NOSNIFF_HEADER_VALUE)
        self.assertEqual(
            response.headers.get("content-disposition"),
            'attachment; filename="x.txt"',
        )

    def test_media_type_and_filename_pass_through_unchanged(self) -> None:
        response = safe_file_response(path=__file__, media_type="text/plain", filename="note.txt")
        self.assertEqual(response.media_type, "text/plain")
        self.assertIn("note.txt", response.headers.get("content-disposition", ""))


if __name__ == "__main__":
    unittest.main()
