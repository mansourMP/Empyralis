from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules.builder_schema import normalize_builder_generated_workflow
from server_modules import control_plane_repository
from server_modules import workflow_repository


def _normalize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    code = str(issue.get("code") or "").strip()
    message = str(issue.get("message") or "").strip()
    node_id = str(issue.get("node_id") or issue.get("nodeId") or "").strip() or None
    warning_markers = (
        "requires approval",
        "prefer",
        "warn",
        "reviewed",
        "session-backed",
        "interactive",
    )
    level = "warning" if any(marker in message.lower() or marker in code.lower() for marker in warning_markers) else "error"
    return {
        "code": code or "workflow_issue",
        "message": message or "Workflow validation issue.",
        "level": level,
        "nodeId": node_id,
    }


def build_validation_summary(definition: Dict[str, Any]) -> Dict[str, Any]:
    draft_normalized = normalize_builder_generated_workflow(definition, for_publish=False)
    draft_issues = [
        _normalize_issue(issue)
        for issue in (draft_normalized.get("issues") if isinstance(draft_normalized.get("issues"), list) else [])
        if isinstance(issue, dict)
    ]

    publish_issues: List[Dict[str, Any]] = []
    try:
        publish_normalized = normalize_builder_generated_workflow(definition, for_publish=True)
        publish_issues = [
            _normalize_issue(issue)
            for issue in (publish_normalized.get("issues") if isinstance(publish_normalized.get("issues"), list) else [])
            if isinstance(issue, dict)
        ]
    except HTTPException as exc:
        detail = exc.detail
        detail_text = detail if isinstance(detail, str) else "Workflow cannot be published yet."
        publish_issues = [{
            "code": "publish_blocked",
            "message": detail_text,
            "level": "error",
            "nodeId": None,
        }]

    draft_error_count = sum(1 for issue in draft_issues if issue.get("level") == "error")
    publish_error_count = sum(1 for issue in publish_issues if issue.get("level") == "error")
    draft_warning_count = sum(1 for issue in draft_issues if issue.get("level") == "warning")
    publish_warning_count = sum(1 for issue in publish_issues if issue.get("level") == "warning")
    return {
        "draftIssues": draft_issues,
        "publishIssues": publish_issues,
        "hasDraftErrors": draft_error_count > 0,
        "hasPublishErrors": publish_error_count > 0,
        "draftErrorCount": draft_error_count,
        "publishErrorCount": publish_error_count,
        "draftWarningCount": draft_warning_count,
        "publishWarningCount": publish_warning_count,
    }


def normalize_workflow_definition(value: Any, *, for_publish: bool = False) -> Dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return normalize_builder_generated_workflow(source, for_publish=for_publish)


def _ensure_record(result: Optional[Dict[str, Any]], *, detail: str = "Workflow control plane is unavailable.") -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    raise HTTPException(status_code=503, detail=detail)


async def _ensure_control_plane_available() -> None:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise HTTPException(status_code=503, detail="Workflow control plane is unavailable.")


async def list_workflows(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    await _ensure_control_plane_available()
    return await workflow_repository.list_workflows(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        limit=limit,
    )


async def get_workflow(
    workflow_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workflow_version_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    await _ensure_control_plane_available()
    return await workflow_repository.get_workflow(
        workflow_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workflow_version_id=workflow_version_id,
    )


async def create_workflow(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    description: Optional[str] = None,
    definition: Optional[Dict[str, Any]] = None,
    created_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    await _ensure_control_plane_available()
    normalized_definition = normalize_workflow_definition(
        definition or {"version": "empyralist.workflow.v2", "nodes": [], "edges": []},
        for_publish=False,
    )
    validation = build_validation_summary(normalized_definition)
    return _ensure_record(
        await workflow_repository.create_workflow(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=str(name or "").strip() or "Untitled Workflow",
            description=str(description or "").strip(),
            definition=normalized_definition,
            validation=validation,
            created_by_user_id=created_by_user_id,
            metadata=metadata,
        )
    )


async def update_workflow(
    workflow_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    definition: Optional[Dict[str, Any]] = None,
    created_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    await _ensure_control_plane_available()
    normalized_definition = normalize_workflow_definition(definition, for_publish=False) if isinstance(definition, dict) else None
    validation = build_validation_summary(normalized_definition) if isinstance(normalized_definition, dict) else None
    return await workflow_repository.update_workflow(
        workflow_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        name=name,
        description=description,
        definition=normalized_definition,
        validation=validation,
        created_by_user_id=created_by_user_id,
        metadata=metadata,
    )


async def publish_workflow(
    workflow_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    created_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    await _ensure_control_plane_available()
    existing = await get_workflow(workflow_id, tenant_id=tenant_id, workspace_id=workspace_id)
    if not isinstance(existing, dict):
        return None
    current_definition = existing.get("definition") if isinstance(existing.get("definition"), dict) else {}
    normalized_definition = normalize_workflow_definition(current_definition, for_publish=True)
    validation = build_validation_summary(normalized_definition)
    if bool(validation.get("hasPublishErrors")):
        raise HTTPException(status_code=409, detail="Workflow cannot be published yet.")
    return await workflow_repository.publish_workflow(
        workflow_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        definition=normalized_definition,
        validation=validation,
        created_by_user_id=created_by_user_id,
        metadata=metadata,
    )


async def delete_workflow(
    workflow_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> bool:
    await _ensure_control_plane_available()
    return await workflow_repository.delete_workflow(
        workflow_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def fetch_workflow_snapshot(
    workflow_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workflow_version_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    await _ensure_control_plane_available()
    return await workflow_repository.fetch_workflow_snapshot(
        workflow_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workflow_version_id=workflow_version_id,
    )


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: Dict[str, Any] = {}
    error: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - thread bridge
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


def fetch_workflow_snapshot_sync(
    workflow_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workflow_version_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return _run_coro_sync(
        fetch_workflow_snapshot(
            workflow_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workflow_version_id=workflow_version_id,
        )
    )
