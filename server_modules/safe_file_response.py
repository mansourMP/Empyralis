"""The one place a stored file becomes an HTTP response.

CLAUDE.md's own upload-policy note: "attachments are served straight back
by FileResponse... SVG is deliberately not an accepted picture... an SVG is
a picture that is also a program on the workspace's own origin." That
reasoning holds for the extension allowlist (`upload_content_policy.py`) at
the WRITE side. This module is the matching guard at the READ side: every
`FileResponse(...)` in this codebase now goes through `safe_file_response`
instead, which adds `X-Content-Type-Options: nosniff` unconditionally.

Why this matters even though the extension allowlist already blocks
`.svg`/`.html`: a bare `FileResponse(path=...)` with no explicit
`media_type` lets Starlette infer the Content-Type from the file's own
extension via `mimetypes.guess_type`, and a bare `FileResponse` with no
`filename` sets NO `Content-Disposition` at all. Nothing in that chain
forces the browser to trust the declared Content-Type over what it sniffs
from the bytes. `nosniff` is what makes "the extension decided the
Content-Type" a promise the BROWSER also has to honor, rather than a
promise this server makes and a client is free to second-guess — the
second half of the story CLAUDE.md's own note was already telling about
FileResponse, just not written down as its own guard until this pass
(2026-08-13 frontend/attachment injection review).

`X-Content-Type-Options: nosniff` is safe to add unconditionally: it never
changes what content type is DECLARED, only whether a browser is allowed to
override that declaration by inspecting the bytes. Every existing caller's
Content-Type is unaffected; only browser MIME-sniffing on ambiguous/generic
types is closed off.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi.responses import FileResponse

NOSNIFF_HEADER_NAME = "X-Content-Type-Options"
NOSNIFF_HEADER_VALUE = "nosniff"


def safe_file_response(
    path: str,
    *,
    media_type: Optional[str] = None,
    filename: Optional[str] = None,
    headers: Optional[Mapping[str, str]] = None,
    **kwargs: Any,
) -> FileResponse:
    """The only sanctioned way to build a `FileResponse` in this codebase.

    Identical to `fastapi.responses.FileResponse` in every other respect —
    same positional/keyword surface, same streaming behavior — except the
    response headers always carry `X-Content-Type-Options: nosniff`, and a
    caller-supplied `headers` mapping is merged rather than silently
    dropped (the merge favors the caller for every OTHER header; nosniff is
    never removable through this function, by design — a route that
    genuinely needs to disable it does not exist today and should not add
    itself here quietly).
    """
    merged_headers = dict(headers or {})
    merged_headers[NOSNIFF_HEADER_NAME] = NOSNIFF_HEADER_VALUE
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers=merged_headers,
        **kwargs,
    )
