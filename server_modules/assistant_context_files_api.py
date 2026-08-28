from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, File, UploadFile

from server_modules import upload_content_policy, workspace_storage_service
from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules.runtime_common import require_member_api_key, require_viewer_api_key
from server_modules.workspace_context import workspace_attachments_dir


def register_assistant_context_file_routes(app) -> None:
    # Founder ruling (2026-07-23, final): the two SOUL.md/MEMORY.md/GOALS.md/
    # etc. "context file" routes that used to live here (GET
    # /api/sage-context-files, PATCH /api/sage-context-files/{filename}) are
    # removed -- confirmed zero frontend callers (workstation-client.ts's
    # listSageContextFiles/updateSageContextFile wrappers, removed in the
    # same change, had no call sites anywhere in frontend/). The
    # /api/sage-chat/attachments routes below are unrelated (chat file
    # uploads, still live -- workstation-client.ts:1215 calls them) and stay
    # registered through this same function under its original name.
    #
    # THIS is the live POST handler for that path. assistant_chat_api.py declares
    # an identical route, but routes_workflows.py registers this module
    # first and FastAPI serves the first match -- so the size cap and the
    # content policy have to be here, not only there. Both now call the same
    # upload_content_policy so the shadowed twin cannot disagree with the
    # one customers actually hit.
    @app.post("/api/sage-chat/attachments", dependencies=[Depends(require_member_api_key)])
    async def upload_sage_chat_attachment(
        workspace_id: str,
        file: UploadFile = File(...),
        current_user=Depends(require_member_api_key),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)

        # Read before writing: the policy needs the bytes to refuse a
        # renamed archive, and a streamed copy would have already landed on
        # disk by the time we could look at it.
        raw = await file.read()
        try:
            extension = upload_content_policy.assert_allowed_upload(
                filename=file.filename or "",
                data=raw,
                content_type=file.content_type,
            )
        except upload_content_policy.UploadRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        attachments_dir = workspace_attachments_dir(resolved_workspace_id)
        file_id = str(uuid.uuid4())
        safe_filename = f"{file_id}{extension}"
        dest_path = attachments_dir / safe_filename

        # THE STORAGE CAP, checked BEFORE a byte lands on disk. The decision
        # itself is upload_content_policy.assert_within_storage_cap, raised
        # from inside reserve_storage_for_upload's transaction -- so a
        # refusal has written neither the ledger row nor the file, and the
        # sentence the customer reads comes out of the same module that
        # writes every other upload refusal here.
        #
        # project_id is the workspace-level bucket because this route has no
        # project anywhere in its request; see workspace_storage_service's
        # docstring for why no optional parameter was invented for it.
        try:
            reservation = await workspace_storage_service.reserve_storage_for_upload(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                project_id=workspace_storage_service.WORKSPACE_LEVEL_BUCKET,
                object_id=file_id,
                surface=workspace_storage_service.SURFACE_CHAT_ATTACHMENT,
                object_key=safe_filename,
                byte_size=len(raw),
                filename=file.filename,
                content_type=file.content_type,
                created_by=str((current_user or {}).get("id") or "").strip() or None,
            )
        except upload_content_policy.UploadRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            dest_path.write_bytes(raw)
        except Exception as exc:
            # The ledger row committed and the file did not, so those bytes
            # would be charged against the cap forever with nothing on disk
            # to delete. Release it. Best-effort: a failed release must not
            # replace the real "could not save" the caller needs to hear.
            if reservation.get("recorded"):
                try:
                    await workspace_storage_service.forget_stored_object(
                        tenant_id=tenant_id,
                        workspace_id=resolved_workspace_id,
                        surface=workspace_storage_service.SURFACE_CHAT_ATTACHMENT,
                        object_key=safe_filename,
                    )
                except Exception:  # noqa: BLE001
                    pass
            raise HTTPException(status_code=500, detail=f"Failed to save attachment: {exc}")

        return {
            "ok": True,
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            "file_id": file_id,
            "filename": file.filename,
            "safe_filename": safe_filename,
            "content_type": file.content_type,
            "size": dest_path.stat().st_size,
            "url": f"/api/sage-chat/attachments/{safe_filename}?workspace_id={resolved_workspace_id}",
        }

    @app.get("/api/sage-chat/attachments/{filename}", dependencies=[Depends(require_viewer_api_key)])
    async def get_sage_chat_attachment(
        filename: str,
        workspace_id: str,
        current_user=Depends(require_viewer_api_key),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        attachments_dir = workspace_attachments_dir(resolved_workspace_id)
        file_path = attachments_dir / filename

        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Attachment not found.")

        from server_modules.safe_file_response import safe_file_response
        return safe_file_response(path=file_path)
