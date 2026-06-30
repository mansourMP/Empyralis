from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from server_modules import agent_registry_repository, control_plane_repository


LEGACY_CATALOG_SLUGS = [
    "executive-assistant",
    "web-researcher",
    "desktop-operator",
]


def _slugify(value: Any, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or fallback


def _to_json(value: Any, *, default: Any) -> str:
    payload = value if value is not None else default
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


async def _bootstrap_default_workspace(pool: Any) -> None:
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO tenants (
                    id, tenant_id, workspace_id, slug, name, status, created_by_user_id, metadata, created_at, updated_at
                ) VALUES (
                    'tenant_default', 'default', 'default', 'default', 'Default Tenant', 'active', NULL,
                    '{"bootstrapped":true,"source":"seed_first_party_agents"}'::jsonb, NOW(), NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    workspace_id = EXCLUDED.workspace_id,
                    slug = EXCLUDED.slug,
                    name = EXCLUDED.name,
                    status = 'active',
                    metadata = COALESCE(tenants.metadata, '{}'::jsonb) || EXCLUDED.metadata,
                    updated_at = NOW()
                """
            )
            await connection.execute(
                """
                INSERT INTO workspaces (
                    id, tenant_id, workspace_id, slug, name, workspace_type, status, created_by_user_id, metadata, created_at, updated_at
                ) VALUES (
                    'default', 'default', 'default', 'default', 'Default Workspace', 'personal', 'active', NULL,
                    '{"bootstrapped":true,"source":"seed_first_party_agents"}'::jsonb, NOW(), NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    workspace_id = EXCLUDED.workspace_id,
                    slug = EXCLUDED.slug,
                    name = EXCLUDED.name,
                    workspace_type = EXCLUDED.workspace_type,
                    status = 'active',
                    metadata = COALESCE(workspaces.metadata, '{}'::jsonb) || EXCLUDED.metadata,
                    updated_at = NOW()
                """
            )


async def _upsert_definition(
    connection: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    created_by_user_id: str | None,
    definition: Dict[str, Any],
    system_agent: bool,
) -> str:
    workspace_slug = _slugify(workspace_id, fallback="workspace")
    slug = str(definition.get("slug") or "").strip()
    if not slug:
        raise ValueError("Agent definition slug is required.")

    existing = await connection.fetchrow(
        """
        SELECT id
        FROM agent_definitions
        WHERE tenant_id = $1 AND workspace_id = $2 AND slug = $3
        LIMIT 1
        """,
        tenant_id,
        workspace_id,
        slug,
    )
    definition_id = str(existing.get("id") or "").strip() if existing is not None else f"agentdef_{workspace_slug}_{_slugify(slug, fallback='agent')}"
    version_id = f"{definition_id}_v1"

    metadata = {"first_party": True}
    if system_agent:
        metadata.update({"system_agent": True, "hidden_from_catalog": True})

    await connection.execute(
        """
        INSERT INTO agent_definitions (
            id, tenant_id, workspace_id, slug, name, description, agent_kind, visibility, status,
            category, icon, created_by_user_id, current_version_id, published_version_id,
            source_workflow_definition_id, metadata, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, 'published',
            $9, $10, $11, $12, $12, NULL, $13::jsonb, NOW(), NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            slug = EXCLUDED.slug,
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            agent_kind = EXCLUDED.agent_kind,
            visibility = EXCLUDED.visibility,
            status = 'published',
            category = EXCLUDED.category,
            icon = EXCLUDED.icon,
            current_version_id = EXCLUDED.current_version_id,
            published_version_id = EXCLUDED.published_version_id,
            created_by_user_id = COALESCE(agent_definitions.created_by_user_id, EXCLUDED.created_by_user_id),
            metadata = COALESCE(agent_definitions.metadata, '{}'::jsonb) || EXCLUDED.metadata,
            updated_at = NOW()
        """,
        definition_id,
        tenant_id,
        workspace_id,
        slug,
        str(definition.get("name") or "").strip() or "Agent",
        str(definition.get("description") or "").strip(),
        str(definition.get("agent_kind") or "").strip() or "specialist",
        str(definition.get("visibility") or "").strip() or ("private" if system_agent else "workspace"),
        str(definition.get("category") or "").strip() or None,
        str(definition.get("icon") or "").strip() or None,
        created_by_user_id,
        version_id,
        _to_json(metadata, default={}),
    )

    await connection.execute(
        """
        INSERT INTO agent_definition_versions (
            id, tenant_id, workspace_id, agent_definition_id, version_number, status, manifest,
            compiled_workflow_version_id, capability_manifest, memory_scope_manifest, policy_manifest,
            placement_manifest, template_inputs_schema, metadata, created_by_user_id, created_at
        ) VALUES (
            $1, $2, $3, $4, 1, 'published', $5::jsonb, NULL, $6::jsonb, '{}'::jsonb, $7::jsonb,
            $8::jsonb, '{}'::jsonb, $9::jsonb, $10, NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            status = 'published',
            manifest = EXCLUDED.manifest,
            capability_manifest = EXCLUDED.capability_manifest,
            policy_manifest = EXCLUDED.policy_manifest,
            placement_manifest = EXCLUDED.placement_manifest,
            metadata = COALESCE(agent_definition_versions.metadata, '{}'::jsonb) || EXCLUDED.metadata,
            created_by_user_id = COALESCE(agent_definition_versions.created_by_user_id, EXCLUDED.created_by_user_id)
        """,
        version_id,
        tenant_id,
        workspace_id,
        definition_id,
        _to_json(definition.get("manifest"), default={}),
        _to_json(definition.get("capability_manifest"), default={}),
        _to_json(definition.get("policy_manifest"), default={}),
        _to_json(definition.get("placement_manifest"), default={}),
        _to_json(metadata, default={}),
        created_by_user_id,
    )
    return definition_id


async def _seed_workspace(
    connection: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    created_by_user_id: str | None,
) -> List[str]:
    await agent_registry_repository.ensure_workspace_agent_registry_seeded(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        created_by_user_id=created_by_user_id,
    )

    await connection.execute(
        """
        UPDATE agent_definitions
        SET
            visibility = 'private',
            status = 'archived',
            metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
            updated_at = NOW()
        WHERE tenant_id = $1
          AND workspace_id = $2
          AND slug = ANY($3::text[])
        """,
        tenant_id,
        workspace_id,
        LEGACY_CATALOG_SLUGS,
        _to_json({"hidden_from_catalog": True, "legacy_seed": True}, default={}),
    )

    await _upsert_definition(
        connection,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        created_by_user_id=created_by_user_id,
        definition=agent_registry_repository.DEFAULT_MASTER_AGENT_DEFINITION,
        system_agent=True,
    )

    seeded: List[str] = []
    for definition in agent_registry_repository.DEFAULT_AGENT_DEFINITIONS:
        await _upsert_definition(
            connection,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            definition=definition,
            system_agent=False,
        )
        seeded.append(str(definition.get("name") or definition.get("slug") or "Agent"))
    return seeded


async def _main() -> int:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        print("First-party seeding skipped: DATABASE_URL is not configured or Postgres is unavailable.")
        return 1

    workspace_rows = await pool.fetch(
        """
        SELECT id, tenant_id
        FROM workspaces
        ORDER BY created_at ASC, id ASC
        """
    )
    if not workspace_rows:
        await _bootstrap_default_workspace(pool)
        workspace_rows = await pool.fetch(
            """
            SELECT id, tenant_id
            FROM workspaces
            ORDER BY created_at ASC, id ASC
            """
        )
    if not workspace_rows:
        print("No workspaces found. Nothing to seed.")
        return 0

    seeded_workspaces = 0
    total_specialists = 0
    async with pool.acquire() as connection:
        async with connection.transaction():
            for row in workspace_rows:
                workspace_id = str(row.get("id") or "").strip()
                tenant_id = str(row.get("tenant_id") or "").strip() or "default"
                if not workspace_id:
                    continue
                seeded = await _seed_workspace(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    created_by_user_id=None,
                )
                seeded_workspaces += 1
                total_specialists += len(seeded)
                print(f"Seeded workspace {workspace_id}: {', '.join(seeded)}")

    print(f"Seeded {total_specialists} first-party specialist definitions across {seeded_workspaces} workspace(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
