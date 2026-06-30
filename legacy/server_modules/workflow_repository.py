from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_json(value: Any, *, default: Any) -> str:
    payload = value if value is not None else default
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _normalize_token(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value).strip() or None


def _row_to_workflow_record(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    created_at = _iso(payload.get("created_at"))
    updated_at = _iso(payload.get("updated_at"))
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenantId": str(payload.get("tenant_id") or "").strip() or None,
        "workspaceId": str(payload.get("workspace_id") or "").strip() or None,
        "name": str(payload.get("name") or "").strip() or "Untitled Workflow",
        "description": str(payload.get("description") or "").strip(),
        "status": str(payload.get("status") or "").strip() or "draft",
        "definition": definition,
        "validation": validation,
        "nodeCount": len(definition.get("nodes") or []),
        "lastRun": _iso(payload.get("last_run_at")),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "created_at": created_at,
        "updated_at": updated_at,
        "currentVersionId": _normalize_token(payload.get("current_version_id")),
        "publishedVersionId": _normalize_token(payload.get("published_version_id")),
        "workflowVersionId": _normalize_token(payload.get("workflow_version_id")),
        "versionNumber": int(payload.get("version_number") or 0) or None,
        "versionStatus": _normalize_token(payload.get("workflow_version_status")),
        "metadata": dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {},
    }


async def list_workflows(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await pool.fetch(
        """
        SELECT
            wd.id,
            wd.tenant_id,
            wd.workspace_id,
            wd.name,
            wd.description,
            wd.status,
            wd.current_version_id,
            wd.published_version_id,
            wd.last_run_at,
            wd.metadata,
            wd.created_at,
            wd.updated_at,
            wv.id AS workflow_version_id,
            wv.version_number,
            wv.status AS workflow_version_status,
            wv.definition,
            wv.validation
        FROM workflow_definitions wd
        LEFT JOIN workflow_versions wv
            ON wv.id = wd.current_version_id
        WHERE wd.tenant_id = $1
          AND wd.workspace_id = $2
        ORDER BY wd.updated_at DESC
        LIMIT $3
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        max(1, int(limit or 200)),
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        payload = _row_to_workflow_record(row)
        if payload is not None:
            items.append(payload)
    return items


async def get_workflow(
    workflow_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workflow_version_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    version_token = _normalize_token(workflow_version_id)
    base_where = ["wd.id = $1"]
    params: List[Any] = [str(workflow_id or "").strip()]
    if tenant_id:
        params.append(str(tenant_id or "").strip())
        base_where.append(f"wd.tenant_id = ${len(params)}")
    if workspace_id:
        params.append(str(workspace_id or "").strip())
        base_where.append(f"wd.workspace_id = ${len(params)}")
    if version_token:
        params.append(version_token)
        version_join = f"LEFT JOIN workflow_versions wv ON wv.workflow_id = wd.id AND wv.id = ${len(params)}"
    else:
        version_join = "LEFT JOIN workflow_versions wv ON wv.id = wd.current_version_id"
    row = await pool.fetchrow(
        f"""
        SELECT
            wd.id,
            wd.tenant_id,
            wd.workspace_id,
            wd.name,
            wd.description,
            wd.status,
            wd.current_version_id,
            wd.published_version_id,
            wd.last_run_at,
            wd.metadata,
            wd.created_at,
            wd.updated_at,
            wv.id AS workflow_version_id,
            wv.version_number,
            wv.status AS workflow_version_status,
            wv.definition,
            wv.validation
        FROM workflow_definitions wd
        {version_join}
        WHERE {' AND '.join(base_where)}
        LIMIT 1
        """,
        *params,
    )
    return _row_to_workflow_record(row)


async def create_workflow(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    description: str,
    definition: Dict[str, Any],
    validation: Dict[str, Any],
    created_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    version_id = f"wfver_{uuid.uuid4().hex[:16]}"
    now_iso = _utc_now_iso()
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO workflow_definitions (
                    id, tenant_id, workspace_id, name, description, status, current_version_id, published_version_id,
                    created_by_user_id, last_run_at, metadata, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'draft', $6, NULL, $7, NULL, $8::jsonb, $9::timestamptz, $9::timestamptz
                )
                """,
                workflow_id,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
                str(name or "").strip() or "Untitled Workflow",
                str(description or "").strip(),
                version_id,
                _normalize_token(created_by_user_id),
                _to_json(metadata, default={}),
                now_iso,
            )
            await connection.execute(
                """
                INSERT INTO workflow_versions (
                    id, tenant_id, workspace_id, workflow_id, version_number, status, definition, validation, created_by_user_id, metadata, created_at
                ) VALUES (
                    $1, $2, $3, $4, 1, 'draft', $5::jsonb, $6::jsonb, $7, $8::jsonb, $9::timestamptz
                )
                """,
                version_id,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
                workflow_id,
                _to_json(definition, default={}),
                _to_json(validation, default={}),
                _normalize_token(created_by_user_id),
                _to_json(metadata, default={}),
                now_iso,
            )
    return await get_workflow(workflow_id, tenant_id=tenant_id, workspace_id=workspace_id)


async def update_workflow(
    workflow_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    definition: Optional[Dict[str, Any]] = None,
    validation: Optional[Dict[str, Any]] = None,
    created_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    now_iso = _utc_now_iso()
    async with pool.acquire() as connection:
        async with connection.transaction():
            existing = await connection.fetchrow(
                """
                SELECT id, name, description, status, current_version_id
                FROM workflow_definitions
                WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
                LIMIT 1
                """,
                str(workflow_id or "").strip(),
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
            )
            if existing is None:
                return None
            next_name = str(name if name is not None else existing.get("name") or "").strip() or "Untitled Workflow"
            next_description = str(description if description is not None else existing.get("description") or "").strip()
            current_version_id = _normalize_token(existing.get("current_version_id"))
            if isinstance(definition, dict):
                version_row = await connection.fetchrow(
                    "SELECT COALESCE(MAX(version_number), 0) AS max_version FROM workflow_versions WHERE workflow_id = $1",
                    str(workflow_id or "").strip(),
                )
                next_version_number = int((dict(version_row).get("max_version") if version_row is not None else 0) or 0) + 1
                current_version_id = f"wfver_{uuid.uuid4().hex[:16]}"
                await connection.execute(
                    """
                    INSERT INTO workflow_versions (
                        id, tenant_id, workspace_id, workflow_id, version_number, status, definition, validation, created_by_user_id, metadata, created_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, 'draft', $6::jsonb, $7::jsonb, $8, $9::jsonb, $10::timestamptz
                    )
                    """,
                    current_version_id,
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                    str(workflow_id or "").strip(),
                    next_version_number,
                    _to_json(definition, default={}),
                    _to_json(validation, default={}),
                    _normalize_token(created_by_user_id),
                    _to_json(metadata, default={}),
                    now_iso,
                )
            await connection.execute(
                """
                UPDATE workflow_definitions
                SET
                    name = $2,
                    description = $3,
                    status = CASE WHEN published_version_id IS NOT NULL AND $4 IS DISTINCT FROM published_version_id THEN 'draft' ELSE status END,
                    current_version_id = $4,
                    metadata = COALESCE(metadata, '{}'::jsonb) || $5::jsonb,
                    updated_at = $6::timestamptz
                WHERE id = $1 AND tenant_id = $7 AND workspace_id = $8
                """,
                str(workflow_id or "").strip(),
                next_name,
                next_description,
                current_version_id,
                _to_json(metadata, default={}),
                now_iso,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
            )
    return await get_workflow(workflow_id, tenant_id=tenant_id, workspace_id=workspace_id)


async def publish_workflow(
    workflow_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    definition: Dict[str, Any],
    validation: Dict[str, Any],
    created_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    now_iso = _utc_now_iso()
    version_id = f"wfver_{uuid.uuid4().hex[:16]}"
    async with pool.acquire() as connection:
        async with connection.transaction():
            existing = await connection.fetchrow(
                """
                SELECT id
                FROM workflow_definitions
                WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
                LIMIT 1
                """,
                str(workflow_id or "").strip(),
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
            )
            if existing is None:
                return None
            version_row = await connection.fetchrow(
                "SELECT COALESCE(MAX(version_number), 0) AS max_version FROM workflow_versions WHERE workflow_id = $1",
                str(workflow_id or "").strip(),
            )
            next_version_number = int((dict(version_row).get("max_version") if version_row is not None else 0) or 0) + 1
            await connection.execute(
                """
                INSERT INTO workflow_versions (
                    id, tenant_id, workspace_id, workflow_id, version_number, status, definition, validation, created_by_user_id, metadata, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'published', $6::jsonb, $7::jsonb, $8, $9::jsonb, $10::timestamptz
                )
                """,
                version_id,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
                str(workflow_id or "").strip(),
                next_version_number,
                _to_json(definition, default={}),
                _to_json(validation, default={}),
                _normalize_token(created_by_user_id),
                _to_json(metadata, default={}),
                now_iso,
            )
            await connection.execute(
                """
                UPDATE workflow_definitions
                SET
                    status = 'published',
                    current_version_id = $2,
                    published_version_id = $2,
                    metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb,
                    updated_at = $4::timestamptz
                WHERE id = $1 AND tenant_id = $5 AND workspace_id = $6
                """,
                str(workflow_id or "").strip(),
                version_id,
                _to_json(metadata, default={}),
                now_iso,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
            )
    return await get_workflow(workflow_id, tenant_id=tenant_id, workspace_id=workspace_id)


async def delete_workflow(
    workflow_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> bool:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    result = await pool.execute(
        """
        DELETE FROM workflow_definitions
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        str(workflow_id or "").strip(),
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
    )
    return str(result or "").strip().upper() == "DELETE 1"


async def fetch_workflow_snapshot(
    workflow_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workflow_version_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return await get_workflow(
        workflow_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workflow_version_id=workflow_version_id,
    )
