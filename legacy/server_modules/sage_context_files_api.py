from __future__ import annotations

import shutil
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, File, UploadFile

from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules.runtime_common import require_member_api_key, require_viewer_api_key
from server_modules.schemas import SageContextFileUpdateRequest
from server_modules.workspace_context import (
    read_workspace_context_files,
    write_workspace_context_file,
    workspace_attachments_dir,
)


def register_sage_context_file_routes(app) -> None:
    @app.get("/api/sage-context-files", dependencies=[Depends(require_viewer_api_key)])
    async def list_workspace_sage_context_files(
        workspace_id: Optional[str] = None,
        current_user=Depends(require_viewer_api_key),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        files = read_workspace_context_files(workspace_id=resolved_workspace_id)
        return {
            "ok": True,
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            "files": [
                {
                    "filename": filename,
                    "content": str(content or ""),
                }
                for filename, content in files.items()
            ],
        }

    @app.patch("/api/sage-context-files/{filename}", dependencies=[Depends(require_member_api_key)])
    async def update_workspace_sage_context_file(
        filename: str,
        body: SageContextFileUpdateRequest,
        current_user=Depends(require_member_api_key),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        try:
            saved = write_workspace_context_file(
                filename,
                body.content,
                workspace_id=resolved_workspace_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            "filename": saved["filename"],
            "content": saved["content"],
        }

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

        attachments_dir = workspace_attachments_dir(resolved_workspace_id)
        file_id = str(uuid.uuid4())
        extension = ""
        if file.filename and "." in file.filename:
            extension = f".{file.filename.split('.')[-1]}"
        
        safe_filename = f"{file_id}{extension}"
        dest_path = attachments_dir / safe_filename

        try:
            with dest_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as exc:
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

        from fastapi.responses import FileResponse
        return FileResponse(path=file_path)
