"""What a customer may put INTO Empyralis as a file: notes and pictures.

Founder's instruction, and it is positioning rather than hygiene: "nobody
must push their code, only files probably TXT or MD or whatever, maybe
pictures and some things like this." CLAUDE.md's standing rule is "never
build a coding surface" -- Claude Code / Codex / Cursor are the execution
layer and Empyralis is the layer above them. A file surface that accepts
anything becomes a code-sharing tool by accident, which is a different
product than the one being built.

SCOPE, stated plainly so nobody re-derives it:

    project documents   title + markdown BODY, no file anywhere in the
                        path (routes_fleet.fleet_create_document ->
                        project_documents_repository). Text by construction;
                        nothing here to gate.
    knowledge files     JSON {file_name, content_text}; already extension-
                        gated by deployed_agent_service._safe_knowledge_
                        filename to .md/.markdown/.txt/.csv/.json.
    chat attachments    POST /api/sage-chat/attachments -- real multipart
                        bytes, written to disk and served back out. This
                        accepted ANY extension and ANY content, and is what
                        this module gates.

Extension is the decision; the bytes are a REFUTATION, never an
independent vote. A `.txt` holding a shell script is text and is not
detectable -- and should not be. A `.txt` whose first bytes are a ZIP or an
ELF header is a renamed binary, and that is both detectable and exactly the
thing the instruction is about. So: reject an extension outside the
allowlist, then reject content that contradicts the extension it arrived
under.

SVG is deliberately NOT an accepted image. These files are served straight
back by FileResponse; an SVG carries script and would execute on the
workspace's own origin. A picture format that is also a program is not a
picture for this purpose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple


class UploadRejected(ValueError):
    """A file the platform does not accept. Carries a message that names
    what IS accepted -- a rejection that does not say what would work sends
    the customer back to guess."""


# Notes, not source. .csv/.json ride along because the knowledge surface
# already accepts them and a customer's exported spreadsheet is the same
# kind of thing as a note.
ALLOWED_TEXT_EXTENSIONS = frozenset({".txt", ".text", ".md", ".markdown", ".csv", ".json"})
ALLOWED_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif"})
ALLOWED_EXTENSIONS = frozenset(ALLOWED_TEXT_EXTENSIONS | ALLOWED_IMAGE_EXTENSIONS)

# 32MB, the cap sage_chat_api.py already declared. The live registration of
# that route (sage_context_files_api.py -- registered FIRST on the same path,
# so it is the one FastAPI serves) had no cap at all.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024

# Magic numbers for the families a note or a picture can never be. Matched
# as a prefix on the raw bytes.
_BINARY_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "archive"),
    (b"PK\x05\x06", "archive"),
    (b"PK\x07\x08", "archive"),
    (b"Rar!\x1a\x07", "archive"),
    (b"\x1f\x8b", "archive"),
    (b"7z\xbc\xaf\x27\x1c", "archive"),
    (b"BZh", "archive"),
    (b"\xfd7zXZ\x00", "archive"),
    (b"\x7fELF", "program"),
    (b"MZ", "program"),
    (b"\xca\xfe\xba\xbe", "program"),
    (b"\xcf\xfa\xed\xfe", "program"),
    (b"\xce\xfa\xed\xfe", "program"),
    (b"\xfe\xed\xfa\xcf", "program"),
    (b"\xfe\xed\xfa\xce", "program"),
    (b"dex\n", "program"),
    (b"\xed\xab\xee\xdb", "package"),
    (b"!<arch>", "archive"),
    (b"SQLite format 3\x00", "database"),
)

# What each accepted image extension must actually start with. An image
# whose bytes are not that image is not a picture, whatever it is called.
_IMAGE_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
    ".heic": (b"",),  # ftyp box lives at offset 4; checked separately below
    ".heif": (b"",),
}


def _article(word: str) -> str:
    """"a archive" is the kind of thing a customer notices and we do not."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def allowed_extensions_sentence() -> str:
    """One line naming what is accepted, for every rejection message."""
    text = ", ".join(sorted(ext.lstrip(".") for ext in ALLOWED_TEXT_EXTENSIONS))
    images = ", ".join(sorted(ext.lstrip(".") for ext in ALLOWED_IMAGE_EXTENSIONS))
    return f"Accepted files: text and notes ({text}) and images ({images})."


def normalized_extension(filename: str) -> str:
    return Path(str(filename or "").replace("\\", "/")).suffix.lower()


def is_allowed_extension(filename: str) -> bool:
    return normalized_extension(filename) in ALLOWED_EXTENSIONS


def _binary_family(data: bytes) -> Optional[str]:
    for magic, family in _BINARY_MAGIC:
        if data.startswith(magic):
            return family
    return None


def _looks_like_image(extension: str, data: bytes) -> bool:
    if extension in {".heic", ".heif"}:
        return len(data) >= 12 and data[4:8] == b"ftyp"
    signatures = _IMAGE_MAGIC.get(extension) or ()
    if extension == ".webp":
        return data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP"
    return any(data.startswith(sig) for sig in signatures if sig)


def assert_allowed_upload(
    *,
    filename: str,
    data: bytes,
    content_type: Optional[str] = None,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> str:
    """Raise UploadRejected unless this is a note or a picture. Returns the
    normalized extension on success.

    `content_type` is advisory only -- a browser's guess, and trivially
    forged -- so it is never what admits a file. It is used only to refuse a
    declared archive/executable that arrived wearing an allowed extension.
    """
    name = Path(str(filename or "").replace("\\", "/")).name.strip()
    if not name:
        raise UploadRejected("A filename is required. " + allowed_extensions_sentence())

    extension = normalized_extension(name)
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadRejected(
            f"Empyralis does not accept {extension or 'files without an extension'} files. "
            + allowed_extensions_sentence()
        )

    if max_bytes and len(data) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise UploadRejected(f"File is larger than {limit_mb}MB.")

    declared = str(content_type or "").split(";", 1)[0].strip().lower()
    if declared and (
        declared.startswith("application/")
        and declared
        not in {"application/json", "application/csv", "application/octet-stream"}
    ):
        raise UploadRejected(
            f"Empyralis does not accept {declared} files. " + allowed_extensions_sentence()
        )

    family = _binary_family(data)
    if family is not None:
        raise UploadRejected(
            f"That file is {_article(family)} {family}, not a {extension.lstrip('.')} file. "
            + allowed_extensions_sentence()
        )

    if extension in ALLOWED_IMAGE_EXTENSIONS:
        if data and not _looks_like_image(extension, data):
            raise UploadRejected(
                f"That file is not a real {extension.lstrip('.')} image. "
                + allowed_extensions_sentence()
            )
        return extension

    # Text: a NUL byte is the one cheap, certain signal that this is not
    # text at all. Encoding is deliberately not policed -- a latin-1 CSV is
    # a real customer file, and refusing it would be pedantry, not a
    # guardrail.
    if b"\x00" in data[:8192]:
        raise UploadRejected(
            f"That file is binary, not a {extension.lstrip('.')} file. "
            + allowed_extensions_sentence()
        )
    return extension


# ── Storage cap ────────────────────────────────────────────────────────────
#
# THE SECOND HALF OF THE SAME GATE. `assert_allowed_upload` above answers
# "may this KIND of file be stored"; this answers "is there room for it".
# Both live in this module on purpose: a refusal a customer reads has to
# come out of one vocabulary, and splitting the two questions across two
# modules is how the second one grows a bare "denied" with no next step.
#
# This function stays PURE — it takes the already-measured usage as an
# argument and touches no database. The counting lives in
# `workspace_storage_service` (which owns the ledger and the RLS-scoped
# read); the DECISION lives here, beside the extension allowlist, so there
# is exactly one place that says no to an upload.


class StorageCapExceeded(UploadRejected):
    """No room left in this project's storage allowance.

    A subclass of UploadRejected so every existing `except UploadRejected`
    at a route keeps working — a full project and a rejected file type are
    both "this upload does not happen", and both already answer 400 with
    the sentence carried here. It is a distinct class anyway because the
    two are different FACTS: one is fixed by sending a different file, the
    other by deleting something or asking for more room, and a caller that
    wants to tell them apart must be able to.
    """


def format_bytes(value: int) -> str:
    """Human sizes for a refusal message. A cap stated as `1073741824` is a
    number the person cannot act on."""
    size = max(0, int(value or 0))
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        amount = size / scale
        # 0.995 rather than 1.0: 1 GiB minus ten bytes must read "1 GB", not
        # "1024 MB" -- a limit message that names a number nobody writes down
        # is the same failure as naming the raw byte count.
        if amount >= 0.995:
            # One decimal only below 10 -- "1.5 GB" reads, "1.53 GB" does not.
            rendered = f"{amount:.1f}".rstrip("0").rstrip(".") if amount < 10 else f"{amount:.0f}"
            return f"{rendered} {unit}"
    return f"{size} bytes"


def storage_cap_sentence(*, used_bytes: int, cap_bytes: int, scope_label: str = "project") -> str:
    """One line naming the limit and what is left, for every cap refusal —
    the same "say what WOULD work" rule `allowed_extensions_sentence` follows
    for the extension allowlist."""
    used = max(0, int(used_bytes or 0))
    cap = max(0, int(cap_bytes or 0))
    remaining = max(0, cap - used)
    return (
        f"This {scope_label} has {format_bytes(remaining)} of its "
        f"{format_bytes(cap)} storage left ({format_bytes(used)} used). "
        "Delete files you no longer need, or upload a smaller one."
    )


def assert_within_storage_cap(
    *,
    used_bytes: int,
    incoming_bytes: int,
    cap_bytes: int,
    scope_label: str = "project",
) -> int:
    """Raise StorageCapExceeded unless this file fits. Returns the resulting
    total on success, so a caller can record it without recomputing.

    `cap_bytes <= 0` means "no cap configured" and admits everything — a
    misconfigured or unset limit must never silently refuse every upload in
    the product. The env override in billing_credit_config clamps to >= 1,
    so reaching that branch takes a deliberate call.
    """
    used = max(0, int(used_bytes or 0))
    incoming = max(0, int(incoming_bytes or 0))
    cap = int(cap_bytes or 0)
    if cap <= 0:
        return used + incoming
    if used + incoming > cap:
        raise StorageCapExceeded(
            f"This file needs {format_bytes(incoming)} and does not fit. "
            + storage_cap_sentence(used_bytes=used, cap_bytes=cap, scope_label=scope_label)
        )
    return used + incoming
