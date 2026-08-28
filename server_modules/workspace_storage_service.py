"""Byte accounting for stored customer files, per project, rolled up per
workspace.

WHY THIS EXISTS: file TYPE has been gated server-side since
``upload_content_policy`` shipped, and a single file has been capped at 32MB,
but until this module NOTHING anywhere counted total bytes. You cannot cap
what you do not count, so this is the prerequisite half of the per-project
storage cap -- not an optimisation of an existing counter, a first count.

── WHAT IS COUNTED ──────────────────────────────────────────────────────

Bytes that land on a DISK as a distinct stored object, recorded one row per
object at the moment it is written:

    chat_attachment   POST /api/sage-chat/attachments -- the only multipart
                      upload route in the product (registered twice; see
                      assistant_context_files_api.py's own comment on which
                      registration FastAPI actually serves). Real customer
                      bytes, written to workspace_attachments_dir, served
                      back out by FileResponse.

The ``surface`` column exists so the next disk-writing surface joins the
same ledger instead of starting a second one. Two are known and deliberately
NOT enrolled by this pass -- see below.

── WHAT IS DELIBERATELY NOT COUNTED, AND WHY ────────────────────────────

    project document BODIES     Markdown TEXT in a Postgres column
                                (project_documents.body). Not disk in the
                                sense this cap is about, and CLAUDE.md's
                                positioning is explicit that context --
                                projects, documents, tasks -- is the product
                                and is never the paywall. Counting document
                                text would make writing a document cost
                                storage allowance, which is charging for the
                                thing being sold.
    task/comment/memory rows    Same reasoning, one level down. Rows, not
                                objects.
    agent knowledge files       POST /deployed-agents/{id}/knowledge/files.
                                Real bytes on disk (workspace_knowledge_dir)
                                and a legitimate future member of this
                                ledger -- but it is a JSON content_text
                                route, not the multipart gate this pass's
                                brief scopes the cap to, and enrolling it
                                without also giving it a delete path that
                                decrements would leave a counter that only
                                ever grows. Flagged, not silently included.
    inbound channel media       personal_channel_media_store_service writes
                                bytes fetched from a gateway. Also real disk,
                                also not enrolled: those bytes arrive from a
                                third party mid-turn, and refusing them at a
                                cap would drop a customer's message rather
                                than refuse a customer's action. Capping
                                inbound is a different product decision than
                                capping upload.

Saying "we count attachments" when three surfaces write bytes would be the
same collapse this codebase keeps re-discovering -- two different facts
sharing one signal. The ledger names its surface per row, so what is and is
not counted is readable from the data, not only from this comment.

── DERIVED, NEVER DENORMALISED ──────────────────────────────────────────

Usage is ``SUM(byte_size)`` over the rows, computed on read. There is no
running-total column, on purpose: a counter and the objects it counts are
two sources that WILL drift (a failed write, a deleted file, a partial
rollback), and a drifted counter is worse than none -- it refuses uploads
for space that is actually free, with nothing on screen able to explain it.
Uploads are rare enough that a SUM over an indexed bucket is free.

── SCOPE TUPLE, AND THE HONEST LIMIT OF IT TODAY ────────────────────────

One row is scoped ``(tenant_id, workspace_id, project_id)``. ``project_id``
is TEXT NOT NULL DEFAULT '' rather than a nullable FK: '' is a real bucket
meaning "not attributable to a project", and a foreign key would forbid it.

    project_id = 'proj_x'   this project's own bucket
    project_id = ''         the workspace-level bucket

Both are capped by the SAME number (billing_credit_config.project_storage_
cap_bytes) -- an unattributed bucket that was uncapped would be a way around
the cap, and one with its own number would be a second dial nobody tuned.

STATE OF THE PRODUCT AS OF THIS MODULE'S FIRST COMMIT: every byte written
lands in the '' bucket, because the one upload route in the backend is
workspace-scoped and carries no project_id anywhere in its request. So the
cap is per-project by construction and per-workspace in effect. No optional
project_id parameter was bolted onto that route to make this look finished:
nothing in the frontend could send it (the upload is Sage's own per-user
Ask AI console, which has no project), and a parameter no caller sends is
exactly the "built, tested, and never wired" shape CLAUDE.md names as this
codebase's most common defect. When a project-scoped file surface ships, it
passes its own ``project_id`` to ``reserve_storage_for_upload`` /
``record_stored_object`` and the bucket is real with no change here.
"""

from __future__ import annotations

import logging
import uuid
import zlib
from typing import Any, Dict, List, Optional

from server_modules import billing_credit_config, control_plane_repository, upload_content_policy

logger = logging.getLogger(__name__)

# One row per stored object; the surface it came from is part of the row so
# the ledger can answer "what is actually taking the space", not only "how
# much".
SURFACE_CHAT_ATTACHMENT = "chat_attachment"

WORKSPACE_LEVEL_BUCKET = ""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _bucket_lock_key(workspace_id: str, project_id: str) -> int:
    """A stable 63-bit advisory-lock key for one storage bucket.

    Serialises concurrent uploads into the SAME bucket so two requests
    cannot each read the same "there is room" and both write. Different
    buckets never contend. zlib.crc32 is used only as a spreader here --
    nothing about this is a security decision, and a collision costs two
    unrelated buckets a moment of serialisation, never a wrong answer.
    """
    seed = f"empyralis:storage:{workspace_id}:{project_id}".encode("utf-8")
    return zlib.crc32(seed) & 0x7FFFFFFF


async def _pool():
    return await control_plane_repository.ensure_control_plane_schema()


async def project_storage_usage_bytes(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str = WORKSPACE_LEVEL_BUCKET,
) -> int:
    """Bytes currently stored in ONE bucket. 0 when Postgres is unavailable
    -- see ``reserve_storage_for_upload`` for why that degradation is
    deliberate rather than a fail-closed refusal."""
    clean_tenant_id = _clean(tenant_id)
    clean_workspace_id = _clean(workspace_id)
    if not clean_tenant_id or not clean_workspace_id:
        return 0
    pool = await _pool()
    if pool is None:
        return 0
    total = await control_plane_repository.rls_fetchval(
        pool,
        """
        SELECT COALESCE(SUM(byte_size), 0)
        FROM workspace_storage_objects
        WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3
        """,
        clean_tenant_id,
        clean_workspace_id,
        _clean(project_id),
        tenant_id=clean_tenant_id,
        workspace_id=clean_workspace_id,
    )
    return max(0, int(total or 0))


async def workspace_storage_usage(
    *,
    tenant_id: str,
    workspace_id: str,
) -> Dict[str, Any]:
    """The per-project breakdown AND the workspace roll-up, from one read.

    Returned shape:
        {"total_bytes": int,
         "cap_bytes": int,
         "buckets": [{"project_id": str, "bytes": int, "objects": int}, ...]}

    ``buckets`` is sorted largest first -- the question anyone asks of a
    storage screen is "what is taking the space", not "list them by id".
    """
    clean_tenant_id = _clean(tenant_id)
    clean_workspace_id = _clean(workspace_id)
    cap = billing_credit_config.project_storage_cap_bytes()
    empty: Dict[str, Any] = {"total_bytes": 0, "cap_bytes": cap, "buckets": []}
    if not clean_tenant_id or not clean_workspace_id:
        return empty
    pool = await _pool()
    if pool is None:
        return empty
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT project_id, COALESCE(SUM(byte_size), 0) AS bytes, COUNT(*) AS objects
        FROM workspace_storage_objects
        WHERE tenant_id = $1 AND workspace_id = $2
        GROUP BY project_id
        ORDER BY bytes DESC, project_id ASC
        """,
        clean_tenant_id,
        clean_workspace_id,
        tenant_id=clean_tenant_id,
        workspace_id=clean_workspace_id,
    )
    buckets: List[Dict[str, Any]] = []
    total = 0
    for row in rows or []:
        payload = dict(row)
        size = max(0, int(payload.get("bytes") or 0))
        total += size
        buckets.append(
            {
                "project_id": _clean(payload.get("project_id")),
                "bytes": size,
                "objects": max(0, int(payload.get("objects") or 0)),
            }
        )
    return {"total_bytes": total, "cap_bytes": cap, "buckets": buckets}


async def reserve_storage_for_upload(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str = WORKSPACE_LEVEL_BUCKET,
    object_id: str,
    surface: str,
    object_key: str,
    byte_size: int,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Check the cap and record the object, atomically, in one transaction.

    THIS IS THE WHOLE ENFORCEMENT SEQUENCE, in one function, on purpose. A
    caller that reads usage, decides, writes the file, and then records the
    row has four steps and three places to forget one -- and the classic
    version of that bug (two concurrent uploads each reading the same
    "there is room") is exactly what the per-bucket advisory lock below
    closes. The decision itself is NOT made here: it is
    ``upload_content_policy.assert_within_storage_cap``, beside the
    extension allowlist, so there is one vocabulary for "this upload does
    not happen".

    Raises ``upload_content_policy.StorageCapExceeded`` when it does not
    fit, having written nothing. Returns
    ``{"object_id", "used_bytes", "cap_bytes", "recorded"}`` on success.

    FAILS OPEN when Postgres is unavailable (``recorded: False``). A
    workspace whose control plane is down must not lose the ability to
    attach a file -- the cap exists to bound cost, not to be a second
    availability dependency in front of a working feature. The uncounted
    bytes are logged, and the next successful upload's SUM is over whatever
    rows do exist; this is a known, bounded undercount, stated rather than
    hidden.
    """
    clean_tenant_id = _clean(tenant_id)
    clean_workspace_id = _clean(workspace_id)
    clean_project_id = _clean(project_id)
    size = max(0, int(byte_size or 0))
    cap = billing_credit_config.project_storage_cap_bytes()
    scope_label = "project" if clean_project_id else "workspace"

    pool = await _pool()
    if pool is None or not clean_tenant_id or not clean_workspace_id:
        logger.warning(
            "workspace_storage: %s bytes NOT accounted for workspace_id=%s project_id=%s "
            "surface=%s -- control plane unavailable, upload admitted uncounted",
            size,
            clean_workspace_id,
            clean_project_id,
            surface,
        )
        return {"object_id": _clean(object_id), "used_bytes": 0, "cap_bytes": cap, "recorded": False}

    async with pool.acquire() as connection:
        async with connection.transaction():
            await control_plane_repository.apply_connection_scope(
                connection, tenant_id=clean_tenant_id, workspace_id=clean_workspace_id,
            )
            # Held for the rest of this transaction; released on commit or
            # rollback, so a refusal never leaks a lock.
            await connection.execute(
                "SELECT pg_advisory_xact_lock($1)",
                _bucket_lock_key(clean_workspace_id, clean_project_id),
            )
            used = await connection.fetchval(
                """
                SELECT COALESCE(SUM(byte_size), 0)
                FROM workspace_storage_objects
                WHERE tenant_id = $1 AND workspace_id = $2 AND project_id = $3
                """,
                clean_tenant_id,
                clean_workspace_id,
                clean_project_id,
            )
            used_bytes = max(0, int(used or 0))
            # Raises out of the transaction -> rollback -> nothing recorded.
            upload_content_policy.assert_within_storage_cap(
                used_bytes=used_bytes,
                incoming_bytes=size,
                cap_bytes=cap,
                scope_label=scope_label,
            )
            await connection.execute(
                """
                INSERT INTO workspace_storage_objects (
                    id, tenant_id, workspace_id, project_id, surface,
                    object_key, byte_size, filename, content_type, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (workspace_id, surface, object_key) DO UPDATE
                    SET byte_size = EXCLUDED.byte_size,
                        filename = EXCLUDED.filename,
                        content_type = EXCLUDED.content_type
                """,
                _clean(object_id) or str(uuid.uuid4()),
                clean_tenant_id,
                clean_workspace_id,
                clean_project_id,
                _clean(surface) or SURFACE_CHAT_ATTACHMENT,
                _clean(object_key),
                size,
                _clean(filename) or None,
                _clean(content_type) or None,
                _clean(created_by) or None,
            )
    return {
        "object_id": _clean(object_id),
        "used_bytes": used_bytes + size,
        "cap_bytes": cap,
        "recorded": True,
    }


async def forget_stored_object(
    *,
    tenant_id: str,
    workspace_id: str,
    surface: str,
    object_key: str,
) -> bool:
    """Drop one object's row so its bytes stop counting.

    The counterpart every ledger needs on the day something can be deleted.
    Nothing in the product deletes a stored attachment today, so this has no
    production caller yet -- it is here because a reservation function
    shipping without its release is how a counter becomes monotonic, and
    because ``reserve_storage_for_upload`` must have somewhere to point when
    a file write fails AFTER the row committed.
    """
    clean_tenant_id = _clean(tenant_id)
    clean_workspace_id = _clean(workspace_id)
    if not clean_tenant_id or not clean_workspace_id:
        return False
    pool = await _pool()
    if pool is None:
        return False
    result = await control_plane_repository.rls_execute(
        pool,
        """
        DELETE FROM workspace_storage_objects
        WHERE tenant_id = $1 AND workspace_id = $2 AND surface = $3 AND object_key = $4
        """,
        clean_tenant_id,
        clean_workspace_id,
        _clean(surface),
        _clean(object_key),
        tenant_id=clean_tenant_id,
        workspace_id=clean_workspace_id,
    )
    return str(result or "").strip() != "DELETE 0"
