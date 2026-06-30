from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from server_modules import rust_runtime_kernel_client


def _enforce_runtime_workspace_state_decision(**payload: Any) -> dict[str, Any]:
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "runtime-state-store-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        result = getattr(exc, "result", None)
        if not isinstance(result, dict):
            result = {}
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_runtime_state_store_denied",
                "operation": result.get("operation") or payload.get("operation"),
                "reason": result.get("reason") or str(exc),
            },
        ) from exc
    expected_next_action = {
        "delete_workspace_memory": "delete_workspace_memory",
        "update_workspace_context_file": "write_workspace_context_file",
    }.get(str(payload.get("operation") or "").strip())
    next_action = str(decision.get("next_action") or "").strip()
    if expected_next_action and next_action != expected_next_action:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_runtime_state_store_invalid_next_action",
                "operation": decision.get("operation") or payload.get("operation"),
                "reason": (
                    "Rust runtime state store returned unexpected next_action for "
                    f"{payload.get('operation') or 'operation'}: {next_action or 'missing'}"
                ),
            },
        )
    return decision


def list_workspace_memory_payload(workspace_id: str, *, workspace_memory_snapshot: Callable[[str], Any]) -> dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    return workspace_memory_snapshot(normalized_workspace_id).as_payload()


def delete_workspace_memory_payload(
    workspace_id: str,
    key: str,
    *,
    delete_memory: Callable[[str, str], bool],
) -> dict[str, Any]:
    normalized_workspace_id = str(workspace_id or "default").strip() or "default"
    normalized_key = str(key or "").strip()
    if not normalized_key:
        raise HTTPException(status_code=400, detail="Memory key is required.")
    _enforce_runtime_workspace_state_decision(
        operation="delete_workspace_memory",
        state_class="workspace_memory",
        storage_engine="durable_postgres",
        tenant_id=normalized_workspace_id,
        workspace_id=normalized_workspace_id,
        actor_id="runtime_workspace_service",
        status="archived",
        payload={"key": normalized_key},
        payload_bytes=len(normalized_key.encode("utf-8")),
        workspace_access=True,
        owner_access=True,
        destructive_approval=True,
    )
    deleted = delete_memory(normalized_workspace_id, normalized_key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory entry not found.")
    return {
        "ok": True,
        "workspace_id": normalized_workspace_id,
        "key": normalized_key,
    }


def workspace_context_files_payload(*, read_workspace_context_files: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    files = read_workspace_context_files()
    return {
        "ok": True,
        "files": [
            {
                "filename": filename,
                "content": str(content or ""),
            }
            for filename, content in files.items()
        ],
    }


def update_workspace_context_file_payload(
    filename: str,
    body: Any,
    *,
    write_workspace_context_file: Callable[[str, str], dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid context file payload.")
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="Field 'content' is required.")
    content_text = str(content)
    _enforce_runtime_workspace_state_decision(
        operation="update_workspace_context_file",
        state_class="workspace_context_files",
        storage_engine="durable_postgres",
        tenant_id="default",
        workspace_id="default",
        actor_id="runtime_workspace_service",
        status="active",
        payload={"filename": str(filename or "").strip(), "content": content_text},
        payload_bytes=len(content_text.encode("utf-8")),
        workspace_access=True,
        owner_access=True,
        destructive_approval=True,
    )
    try:
        saved = write_workspace_context_file(filename, content_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        **saved,
    }
