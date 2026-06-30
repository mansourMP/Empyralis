from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from server_modules import agent_registry_repository, control_plane_repository
from server_modules.agent_manifest import (
    AgentManifest,
    AgentManifestBible,
    AgentManifestSkillBinding,
    SAGE_GLOBAL_MANIFEST,
    render_manifest_bible,
)
from server_modules.db import asyncpg


CHANNEL_OWNERSHIP_CONFLICT_MESSAGE = "This channel already has an inbound owner."
_INBOUND_OWNER_CHANNEL_KEYS = frozenset({"telegram", "whatsapp", "slack", "discord", "email", "github", "phone", "web_chat"})
_WEB_CHAT_DEFAULT_ENDPOINT_KEY = "workspace-default"
_RUNTIME_MODES = frozenset({"hosted_secure", "local_secure", "privileged_device"})
_LOCAL_SPECIALIST_LOCK = threading.RLock()
_LOCAL_SPECIALIST_INSTALLS: Dict[tuple[str, str, str], Dict[str, Any]] = {}


class ChannelOwnershipConflictError(Exception):
    pass


class ChannelBindingValidationError(Exception):
    pass


class RuntimeProfileValidationError(Exception):
    pass


def _normalize_token(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _dict_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return {}
        try:
            parsed = json.loads(token)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _to_json(value: Any, *, default: Any) -> str:
    payload = value if value is not None else default
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _slugify(value: Any, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or fallback


def _normalize_endpoint_key(value: Any) -> Optional[str]:
    token = str(value or "").strip().lower()
    return token or None


def _normalize_runtime_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in _RUNTIME_MODES else "hosted_secure"


def _normalize_channel_binding_payload(
    channel_key: str,
    binding: Any,
    *,
    manifest_enabled: bool,
) -> Dict[str, Any]:
    enabled = bool(manifest_enabled)
    payload: Dict[str, Any] = {}
    if isinstance(binding, bool):
        enabled = binding
    else:
        payload = _dict_json(binding)
        if "enabled" in payload:
            enabled = bool(payload.get("enabled"))
    normalized_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"enabled", "endpoint_key", "is_inbound_owner"}
    }
    is_inbound_owner = bool(payload.get("is_inbound_owner"))
    endpoint_key = _normalize_endpoint_key(payload.get("endpoint_key"))

    if channel_key in _INBOUND_OWNER_CHANNEL_KEYS and enabled and is_inbound_owner:
        if endpoint_key is None and channel_key == "web_chat":
            endpoint_key = _WEB_CHAT_DEFAULT_ENDPOINT_KEY
        if endpoint_key is None:
            raise ChannelBindingValidationError("Inbound owners must define an endpoint key.")

    normalized_payload["enabled"] = enabled
    if channel_key in _INBOUND_OWNER_CHANNEL_KEYS:
        normalized_payload["is_inbound_owner"] = is_inbound_owner
    if endpoint_key is not None:
        normalized_payload["endpoint_key"] = endpoint_key
    return normalized_payload


def _normalize_channel_bindings(
    *,
    manifest: AgentManifest,
    channel_bindings: Dict[str, Any],
) -> Dict[str, Any]:
    manifest_channels = manifest.channels.model_dump(mode="json")
    keys = sorted(set(manifest_channels.keys()) | set(channel_bindings.keys()))
    normalized: Dict[str, Any] = {}
    for channel_key in keys:
        token = str(channel_key or "").strip()
        if not token:
            continue
        normalized[token] = _normalize_channel_binding_payload(
            token,
            channel_bindings.get(token),
            manifest_enabled=bool(manifest_channels.get(token)),
        )
    return normalized


def _merge_channel_bindings(
    existing_bindings: Dict[str, Any],
    incoming_bindings: Dict[str, Any],
) -> Dict[str, Any]:
    merged = {
        str(key): _dict_json(value)
        for key, value in existing_bindings.items()
        if str(key or "").strip()
    }
    for channel_key, binding in incoming_bindings.items():
        token = str(channel_key or "").strip()
        if not token:
            continue
        current = _dict_json(merged.get(token))
        if isinstance(binding, bool):
            merged[token] = {
                **current,
                "enabled": binding,
            }
            continue
        merged[token] = {
            **current,
            **_dict_json(binding),
        }
    return merged


async def _assert_unique_inbound_channel_owners(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    channel_bindings: Dict[str, Any],
) -> None:
    for channel_key, binding in channel_bindings.items():
        token = str(channel_key or "").strip()
        if token not in _INBOUND_OWNER_CHANNEL_KEYS:
            continue
        payload = _dict_json(binding)
        if not bool(payload.get("enabled")) or not bool(payload.get("is_inbound_owner")):
            continue
        endpoint_key = _normalize_endpoint_key(payload.get("endpoint_key"))
        if endpoint_key is None:
            continue
        conflict = await connection.fetchrow(
            """
            SELECT agent_install_id
            FROM agent_channel_bindings
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND channel_key = $3
              AND agent_install_id <> $4
              AND enabled = TRUE
              AND lower(COALESCE(binding->>'endpoint_key', '')) = $5
              AND lower(COALESCE(binding->>'is_inbound_owner', 'false')) = 'true'
            LIMIT 1
            """,
            tenant_id,
            workspace_id,
            token,
            install_id,
            endpoint_key,
        )
        if conflict is not None:
            raise ChannelOwnershipConflictError(CHANNEL_OWNERSHIP_CONFLICT_MESSAGE)


def _is_channel_ownership_unique_violation(error: Exception) -> bool:
    constraint_name = str(getattr(error, "constraint_name", "") or "").strip()
    if constraint_name == "uq_agent_channel_bindings_active_inbound_owner":
        return True
    unique_error = getattr(asyncpg, "UniqueViolationError", None)
    if unique_error and isinstance(error, unique_error):
        return constraint_name == "uq_agent_channel_bindings_active_inbound_owner"
    return "uq_agent_channel_bindings_active_inbound_owner" in str(error).lower()


def _manifest_category(manifest: AgentManifest) -> str:
    if manifest.identity.archetype == "task_automator":
        return "Operations"
    if manifest.identity.archetype == "intelligence_researcher":
        return "Research"
    if manifest.identity.archetype == "master_os":
        return "System"
    return "Support"


def _manifest_capability_payload(manifest: AgentManifest) -> Dict[str, Any]:
    enabled_skills = [binding.id for binding in manifest.skills if binding.enabled]
    return {
        "summary": enabled_skills,
        "toggles": [
            {
                "id": binding.id,
                "label": binding.id,
                "default_enabled": bool(binding.enabled),
            }
            for binding in manifest.skills
        ],
    }


def _manifest_policy_payload(manifest: AgentManifest) -> Dict[str, Any]:
    return {
        "reflection_enabled": bool(manifest.policy.reflection_enabled),
        "approval_mode": manifest.policy.approval_mode,
        "trust_mode": "strict" if manifest.policy.approval_mode == "strict" else "guarded",
    }


def _manifest_placement_payload(runtime_profile_id: Optional[str], runtime_mode: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "runtime_mode": runtime_mode,
    }
    if runtime_profile_id:
        payload["preferred_runtime_profile_id"] = runtime_profile_id
    return payload


def _manifest_projection_metadata(
    manifest: AgentManifest,
    *,
    source: str,
    visibility: str,
    extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "source": source,
        "visibility": visibility,
        "agent_manifest": manifest.model_dump(mode="json"),
        "draft_bible": render_manifest_bible(manifest),
        "bound_skills": [binding.id for binding in manifest.skills if binding.enabled],
        "draft_archetype": None if manifest.identity.archetype == "master_os" else manifest.identity.archetype,
        "manifest_id": manifest.manifest_id,
        "manifest_engine": manifest.engine,
        "channel_bindings": manifest.channels.model_dump(mode="json"),
        "draft_prompt": manifest.identity.summary,
    }
    if extras:
        payload.update(_dict_json(extras))
    return payload


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _local_specialist_key(tenant_id: str, workspace_id: str, install_id: str) -> tuple[str, str, str]:
    return (
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(install_id or "").strip(),
    )


def _clone_local_specialist_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(bundle, ensure_ascii=False, default=str))


def _build_local_specialist_bundle(
    *,
    tenant_id: str,
    workspace_id: str,
    install_id: str,
    definition_id: str,
    version_id: str,
    manifest: AgentManifest,
    created_by_user_id: Optional[str],
    label: Optional[str],
    runtime_profile_id: Optional[str],
    runtime_mode: str,
    tool_toggles: Optional[Dict[str, Any]],
    connector_bindings: Optional[Dict[str, Any]],
    channel_bindings: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    status: str = "draft",
) -> Dict[str, Any]:
    specialist_name = str(label or manifest.identity.name).strip() or manifest.identity.name
    source = str(_dict_json(metadata).get("source") or "draft").strip() or "draft"
    visibility = str(_dict_json(metadata).get("visibility") or "private").strip() or "private"
    projection_metadata = _manifest_projection_metadata(
        manifest,
        source=source,
        visibility=visibility,
        extras=metadata,
    )
    projected_contract = agent_registry_repository.project_install_contract_fields(
        install_id=install_id,
        label=specialist_name,
        agent_kind=agent_registry_repository.SPECIALIST_AGENT_KIND,
        metadata=projection_metadata,
    )
    now = _utc_now_iso()
    return {
        "id": install_id,
        "tenant_id": str(tenant_id or "").strip() or None,
        "workspace_id": str(workspace_id or "").strip() or None,
        "agent_definition_id": definition_id,
        "agent_definition_version_id": version_id,
        "installed_by_user_id": str(created_by_user_id or "").strip() or None,
        "install_scope": "workspace",
        "owner_user_id": str(created_by_user_id or "").strip() or None,
        "thread_id": None,
        "label": specialist_name,
        "status": str(status or "draft").strip() or "draft",
        "enabled": True,
        "runtime_profile_id": _normalize_token(runtime_profile_id),
        "runtime_mode": _normalize_runtime_mode(runtime_mode),
        "compiled_workflow_version_id": None,
        "compiled_workflow_id": None,
        "root_folder_uri": None,
        "tool_toggles": _dict_json(tool_toggles),
        "folder_grants": [],
        "connector_bindings": _dict_json(connector_bindings),
        "memory_scope_overrides": {},
        "policy_context_overrides": _manifest_policy_payload(manifest),
        "metadata": projected_contract["metadata"],
        "captain_identity": projected_contract["captain_identity"],
        "specialist_mode": projected_contract["specialist_mode"],
        "specialist_mode_contract": projected_contract["specialist_mode_contract"],
        "created_at": now,
        "updated_at": now,
        "agent_definition": {
            "id": definition_id,
            "slug": f"{_slugify(specialist_name, fallback='specialist')}-local",
            "name": specialist_name,
            "description": str(manifest.identity.summary or "").strip(),
            "agent_kind": agent_registry_repository.SPECIALIST_AGENT_KIND,
            "visibility": "workspace",
            "status": "draft",
            "source_workflow_definition_id": None,
            "metadata": {"source": "local_specialist"},
        },
        "agent_definition_version": {
            "id": version_id,
            "status": "draft",
            "manifest": manifest.model_dump(mode="json"),
            "capability_manifest": _manifest_capability_payload(manifest),
            "memory_scope_manifest": {},
            "policy_manifest": _manifest_policy_payload(manifest),
            "placement_manifest": _manifest_placement_payload(runtime_profile_id, _normalize_runtime_mode(runtime_mode)),
            "template_inputs_schema": {},
            "metadata": {"source": "local_specialist"},
            "compiled_workflow_version_id": None,
        },
        "runtime_profile": None,
        "channel_bindings": _dict_json(channel_bindings),
    }


def _store_local_specialist_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    key = _local_specialist_key(
        str(bundle.get("tenant_id") or ""),
        str(bundle.get("workspace_id") or ""),
        str(bundle.get("id") or ""),
    )
    with _LOCAL_SPECIALIST_LOCK:
        _LOCAL_SPECIALIST_INSTALLS[key] = _clone_local_specialist_bundle(bundle)
        return _clone_local_specialist_bundle(_LOCAL_SPECIALIST_INSTALLS[key])


def _get_local_specialist_bundle(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> Optional[Dict[str, Any]]:
    key = _local_specialist_key(tenant_id, workspace_id, install_id)
    with _LOCAL_SPECIALIST_LOCK:
        bundle = _LOCAL_SPECIALIST_INSTALLS.get(key)
        return _clone_local_specialist_bundle(bundle) if isinstance(bundle, dict) else None


def _parse_bible_text(bible_text: str) -> Dict[str, str]:
    sections = {
        "mission": "",
        "hard context": "",
        "operational policy": "",
        "core responsibilities": "",
        "guardrails": "",
        "escalation triggers": "",
    }
    current: Optional[str] = None
    for raw_line in str(bible_text or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip().lower()
        if line in sections:
            current = line
            continue
        if not current:
            continue
        sections[current] = f"{sections[current]}{sections[current] and chr(10) or ''}{raw_line}".rstrip()
    return sections


async def _default_runtime_profile_id(connection: Any, tenant_id: str, workspace_id: str) -> Optional[str]:
    row = await connection.fetchrow(
        """
        SELECT id
        FROM runtime_profiles
        WHERE tenant_id = $1 AND workspace_id = $2
        ORDER BY
            CASE WHEN slug = 'empyralis-cloud' THEN 0 ELSE 1 END,
            updated_at DESC,
            created_at DESC
        LIMIT 1
        """,
        tenant_id,
        workspace_id,
    )
    return _normalize_token(row.get("id")) if row else None


async def _load_manifest(connection: Any, install_id: str, tenant_id: str, workspace_id: str) -> Optional[AgentManifest]:
    row = await connection.fetchrow(
        """
        SELECT am.manifest, wai.metadata, adv.manifest AS definition_manifest
        FROM workspace_agent_installs wai
        LEFT JOIN agent_manifests am
            ON am.agent_install_id = wai.id
           AND am.tenant_id = wai.tenant_id
           AND am.workspace_id = wai.workspace_id
        LEFT JOIN agent_definition_versions adv
            ON adv.id = wai.agent_definition_version_id
        WHERE wai.id = $1 AND wai.tenant_id = $2 AND wai.workspace_id = $3
        LIMIT 1
        """,
        install_id,
        tenant_id,
        workspace_id,
    )
    if row is None:
        return None
    payload = dict(row)
    for candidate in (
        _dict_json(payload.get("manifest")),
        _dict_json(_dict_json(payload.get("metadata")).get("agent_manifest")),
        _dict_json(payload.get("definition_manifest")),
    ):
        if not candidate:
            continue
        try:
            return AgentManifest.model_validate(candidate)
        except Exception:
            continue
    return None


async def _latest_install_metadata(connection: Any, install_id: str, tenant_id: str, workspace_id: str) -> Dict[str, Any]:
    row = await connection.fetchrow(
        """
        SELECT metadata
        FROM workspace_agent_installs
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        LIMIT 1
        """,
        install_id,
        tenant_id,
        workspace_id,
    )
    return _dict_json(row.get("metadata")) if row else {}


async def _load_channel_bindings(connection: Any, install_id: str, tenant_id: str, workspace_id: str) -> Dict[str, Any]:
    rows = await connection.fetch(
        """
        SELECT channel_key, enabled, binding
        FROM agent_channel_bindings
        WHERE tenant_id = $1 AND workspace_id = $2 AND agent_install_id = $3
        """,
        tenant_id,
        workspace_id,
        install_id,
    )
    payload: Dict[str, Any] = {}
    for row in rows:
        channel_key = str(row.get("channel_key") or "").strip()
        if not channel_key:
            continue
        binding = _dict_json(row.get("binding"))
        binding["enabled"] = bool(row.get("enabled"))
        payload[channel_key] = binding
    return payload


async def _runtime_profile_row(
    connection: Any,
    runtime_profile_id: str,
    tenant_id: str,
    workspace_id: str,
) -> Optional[Dict[str, Any]]:
    row = await connection.fetchrow(
        """
        SELECT id, slug, label, runtime_class, placement_mode, status
        FROM runtime_profiles
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        LIMIT 1
        """,
        runtime_profile_id,
        tenant_id,
        workspace_id,
    )
    return dict(row) if row is not None else None


async def _resolve_runtime_binding(
    connection: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_profile_id: Optional[str],
    runtime_mode: Optional[str],
) -> tuple[Optional[str], str]:
    resolved_mode = _normalize_runtime_mode(runtime_mode)
    resolved_profile_id = _normalize_token(runtime_profile_id)

    if resolved_mode == "hosted_secure":
        resolved_profile_id = resolved_profile_id or await _default_runtime_profile_id(connection, tenant_id, workspace_id)
        if not resolved_profile_id:
            raise RuntimeProfileValidationError("Hosted Secure requires a cloud runtime profile.")
        profile = await _runtime_profile_row(connection, resolved_profile_id, tenant_id, workspace_id)
        if profile is None:
            raise RuntimeProfileValidationError("Selected runtime profile is unavailable.")
        if str(profile.get("runtime_class") or "").strip() != "cloud_worker":
            raise RuntimeProfileValidationError("Hosted Secure requires a cloud runtime profile.")
        return resolved_profile_id, resolved_mode

    if not resolved_profile_id:
        raise RuntimeProfileValidationError("Local and privileged runtimes require a desktop runtime profile.")

    profile = await _runtime_profile_row(connection, resolved_profile_id, tenant_id, workspace_id)
    if profile is None:
        raise RuntimeProfileValidationError("Selected runtime profile is unavailable.")
    if str(profile.get("runtime_class") or "").strip() != "desktop_companion":
        raise RuntimeProfileValidationError("Local and privileged runtimes require a desktop companion runtime profile.")
    return resolved_profile_id, resolved_mode


async def _sync_definition_projection(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    manifest: AgentManifest,
    runtime_profile_id: Optional[str],
    runtime_mode: str,
) -> None:
    install_row = await connection.fetchrow(
        """
        SELECT agent_definition_id, agent_definition_version_id
        FROM workspace_agent_installs
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        LIMIT 1
        """,
        install_id,
        tenant_id,
        workspace_id,
    )
    if install_row is None:
        return
    definition_id = str(install_row.get("agent_definition_id") or "").strip()
    definition_version_id = str(install_row.get("agent_definition_version_id") or "").strip()
    await connection.execute(
        """
        UPDATE agent_definitions
        SET
            name = $4,
            description = $5,
            category = $6,
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        definition_id,
        tenant_id,
        workspace_id,
        manifest.identity.name,
        manifest.identity.summary,
        _manifest_category(manifest),
    )
    await connection.execute(
        """
        UPDATE agent_definition_versions
        SET
            manifest = $4::jsonb,
            capability_manifest = $5::jsonb,
            policy_manifest = $6::jsonb,
            placement_manifest = $7::jsonb
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        definition_version_id,
        tenant_id,
        workspace_id,
        _to_json(manifest.model_dump(mode="json"), default={}),
        _to_json(_manifest_capability_payload(manifest), default={}),
        _to_json(_manifest_policy_payload(manifest), default={}),
        _to_json(_manifest_placement_payload(runtime_profile_id, runtime_mode), default={}),
    )


async def _sync_install_projection(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    manifest: AgentManifest,
    status: str,
    runtime_profile_id: Optional[str],
    runtime_mode: str,
    tool_toggles: Dict[str, Any],
    connector_bindings: Dict[str, Any],
    channel_bindings: Dict[str, Any],
    metadata: Dict[str, Any],
) -> None:
    existing_metadata = await _latest_install_metadata(connection, install_id, tenant_id, workspace_id)
    next_metadata = {
        **existing_metadata,
        **metadata,
        "channel_registry_bindings": channel_bindings,
        "channel_bindings": manifest.channels.model_dump(mode="json"),
        "runtime_binding": {
            "runtime_profile_id": runtime_profile_id,
            "runtime_mode": runtime_mode,
        },
    }
    await connection.execute(
        """
        UPDATE workspace_agent_installs
        SET
            label = $4,
            status = $5,
            runtime_profile_id = $6,
            tool_toggles = $7::jsonb,
            connector_bindings = $8::jsonb,
            metadata = $9::jsonb,
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        install_id,
        tenant_id,
        workspace_id,
        manifest.identity.name,
        status,
        runtime_profile_id,
        _to_json(tool_toggles, default={}),
        _to_json(connector_bindings, default={}),
        _to_json(next_metadata, default={}),
    )


async def _upsert_agent_manifest(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    manifest: AgentManifest,
    status: str,
    metadata: Dict[str, Any],
) -> None:
    await connection.execute(
        """
        INSERT INTO agent_manifests (
            id, tenant_id, workspace_id, agent_install_id, manifest_id, status, manifest, metadata, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, NOW(), NOW()
        )
        ON CONFLICT (agent_install_id)
        DO UPDATE SET
            manifest_id = EXCLUDED.manifest_id,
            status = EXCLUDED.status,
            manifest = EXCLUDED.manifest,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        """,
        f"amanifest_{install_id}",
        tenant_id,
        workspace_id,
        install_id,
        manifest.manifest_id,
        status,
        _to_json(manifest.model_dump(mode="json"), default={}),
        _to_json(metadata, default={}),
    )


async def _insert_bible_version(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    manifest: AgentManifest,
    created_by_user_id: Optional[str],
) -> None:
    version_number = int(
        await connection.fetchval(
            """
            SELECT COALESCE(MAX(version_number), 0) + 1
            FROM agent_bible_versions
            WHERE tenant_id = $1 AND workspace_id = $2 AND agent_install_id = $3
            """,
            tenant_id,
            workspace_id,
            install_id,
        )
        or 1
    )
    sections = {
        "mission": manifest.bible.mission,
        "hard_context": manifest.bible.hard_context,
        "operational_policy": manifest.bible.operational_policy,
        "core_responsibilities": manifest.bible.core_responsibilities,
        "guardrails": manifest.bible.guardrails,
        "escalation_triggers": manifest.bible.escalation_triggers,
    }
    await connection.execute(
        """
        INSERT INTO agent_bible_versions (
            id, tenant_id, workspace_id, agent_install_id, version_number, bible_text, bible_sections, created_by_user_id, created_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7::jsonb, $8, NOW()
        )
        """,
        f"abible_{uuid.uuid4().hex[:16]}",
        tenant_id,
        workspace_id,
        install_id,
        version_number,
        render_manifest_bible(manifest),
        _to_json(sections, default={}),
        created_by_user_id,
    )


async def _replace_skill_bindings(connection: Any, *, install_id: str, tenant_id: str, workspace_id: str, manifest: AgentManifest) -> None:
    await connection.execute(
        """
        DELETE FROM agent_skill_bindings
        WHERE tenant_id = $1 AND workspace_id = $2 AND agent_install_id = $3
        """,
        tenant_id,
        workspace_id,
        install_id,
    )
    for binding in manifest.skills:
        await connection.execute(
            """
            INSERT INTO agent_skill_bindings (
                id, tenant_id, workspace_id, agent_install_id, skill_id, enabled, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, '{}'::jsonb, NOW(), NOW()
            )
            """,
            f"askill_{uuid.uuid4().hex[:16]}",
            tenant_id,
            workspace_id,
            install_id,
            binding.id,
            bool(binding.enabled),
        )


async def _replace_connector_bindings(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    connector_bindings: Dict[str, Any],
) -> None:
    await connection.execute(
        """
        DELETE FROM agent_connector_bindings
        WHERE tenant_id = $1 AND workspace_id = $2 AND agent_install_id = $3
        """,
        tenant_id,
        workspace_id,
        install_id,
    )
    for connector_key, binding in connector_bindings.items():
        normalized_key = str(connector_key or "").strip()
        if not normalized_key:
            continue
        enabled = True
        if isinstance(binding, bool):
            enabled = binding
            payload: Dict[str, Any] = {}
        else:
            payload = _dict_json(binding)
            if "enabled" in payload:
                enabled = bool(payload.get("enabled"))
        await connection.execute(
            """
            INSERT INTO agent_connector_bindings (
                id, tenant_id, workspace_id, agent_install_id, connector_key, enabled, binding, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7::jsonb, NOW(), NOW()
            )
            """,
            f"aconn_{uuid.uuid4().hex[:16]}",
            tenant_id,
            workspace_id,
            install_id,
            normalized_key,
            enabled,
            _to_json(payload, default={}),
        )


async def _replace_channel_bindings(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    manifest: AgentManifest,
    channel_bindings: Dict[str, Any],
) -> Dict[str, Any]:
    manifest_channels = manifest.channels.model_dump(mode="json")
    normalized_channel_bindings = _normalize_channel_bindings(
        manifest=manifest,
        channel_bindings=channel_bindings,
    )
    await _assert_unique_inbound_channel_owners(
        connection,
        install_id=install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        channel_bindings=normalized_channel_bindings,
    )
    await connection.execute(
        """
        DELETE FROM agent_channel_bindings
        WHERE tenant_id = $1 AND workspace_id = $2 AND agent_install_id = $3
        """,
        tenant_id,
        workspace_id,
        install_id,
    )
    try:
        keys = sorted(set(manifest_channels.keys()) | set(normalized_channel_bindings.keys()))
        for channel_key in keys:
            payload = _dict_json(normalized_channel_bindings.get(channel_key))
            enabled = bool(payload.get("enabled", manifest_channels.get(channel_key)))
            await connection.execute(
                """
                INSERT INTO agent_channel_bindings (
                    id, tenant_id, workspace_id, agent_install_id, channel_key, enabled, binding, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7::jsonb, NOW(), NOW()
                )
                """,
                f"achan_{uuid.uuid4().hex[:16]}",
                tenant_id,
                workspace_id,
                install_id,
                channel_key,
                enabled,
                _to_json(payload, default={}),
            )
    except Exception as error:
        if _is_channel_ownership_unique_violation(error):
            raise ChannelOwnershipConflictError(CHANNEL_OWNERSHIP_CONFLICT_MESSAGE) from error
        raise
    return normalized_channel_bindings


async def _upsert_runtime_profile_binding(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    runtime_profile_id: Optional[str],
    runtime_mode: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    await connection.execute(
        """
        INSERT INTO agent_runtime_profiles (
            id, tenant_id, workspace_id, agent_install_id, runtime_profile_id, runtime_mode, metadata, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7::jsonb, NOW(), NOW()
        )
        ON CONFLICT (agent_install_id)
        DO UPDATE SET
            runtime_profile_id = EXCLUDED.runtime_profile_id,
            runtime_mode = EXCLUDED.runtime_mode,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        """,
        f"artp_{install_id}",
        tenant_id,
        workspace_id,
        install_id,
        runtime_profile_id,
        runtime_mode,
        _to_json(metadata, default={}),
    )


async def _persist_specialist_state(
    connection: Any,
    *,
    install_id: str,
    tenant_id: str,
    workspace_id: str,
    manifest: AgentManifest,
    runtime_profile_id: Optional[str],
    runtime_mode: str,
    tool_toggles: Dict[str, Any],
    connector_bindings: Dict[str, Any],
    channel_bindings: Dict[str, Any],
    created_by_user_id: Optional[str],
    metadata: Dict[str, Any],
    status: str,
    write_bible_version: bool,
) -> None:
    await _sync_definition_projection(
        connection,
        install_id=install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        runtime_profile_id=runtime_profile_id,
        runtime_mode=runtime_mode,
    )
    await _upsert_agent_manifest(
        connection,
        install_id=install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        status=status,
        metadata=metadata,
    )
    if write_bible_version:
        await _insert_bible_version(
            connection,
            install_id=install_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            manifest=manifest,
            created_by_user_id=created_by_user_id,
        )
    await _replace_skill_bindings(
        connection,
        install_id=install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
    )
    await _replace_connector_bindings(
        connection,
        install_id=install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        connector_bindings=connector_bindings,
    )
    normalized_channel_bindings = await _replace_channel_bindings(
        connection,
        install_id=install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        channel_bindings=channel_bindings,
    )
    await _upsert_runtime_profile_binding(
        connection,
        install_id=install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_profile_id=runtime_profile_id,
        runtime_mode=runtime_mode,
        metadata={"source": "specialist_manifest"},
    )
    await _sync_install_projection(
        connection,
        install_id=install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        status=status,
        runtime_profile_id=runtime_profile_id,
        runtime_mode=runtime_mode,
        tool_toggles=tool_toggles,
        connector_bindings=connector_bindings,
        channel_bindings=normalized_channel_bindings,
        metadata=metadata,
    )


async def create_workspace_specialist(
    *,
    tenant_id: str,
    workspace_id: str,
    manifest: AgentManifest,
    created_by_user_id: Optional[str] = None,
    label: Optional[str] = None,
    runtime_profile_id: Optional[str] = None,
    runtime_mode: str = "hosted_secure",
    tool_toggles: Optional[Dict[str, Any]] = None,
    connector_bindings: Optional[Dict[str, Any]] = None,
    channel_bindings: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    tool_toggles = _dict_json(tool_toggles)
    await agent_registry_repository.ensure_workspace_agent_registry_seeded(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        created_by_user_id=created_by_user_id,
    )
    connector_bindings = _dict_json(connector_bindings)
    channel_bindings = _dict_json(channel_bindings)
    metadata = _dict_json(metadata)
    source = str(metadata.get("source") or "draft").strip() or "draft"
    visibility = str(metadata.get("visibility") or "private").strip() or "private"

    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            specialist_name = str(label or manifest.identity.name).strip() or manifest.identity.name
            return _store_local_specialist_bundle(
                _build_local_specialist_bundle(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    install_id=f"ainstall_{uuid.uuid4().hex[:16]}",
                    definition_id=f"agentdef_{uuid.uuid4().hex[:16]}",
                    version_id=f"agentver_{uuid.uuid4().hex[:16]}",
                    manifest=manifest,
                    created_by_user_id=created_by_user_id,
                    label=specialist_name,
                    runtime_profile_id=_normalize_token(runtime_profile_id),
                    runtime_mode=_normalize_runtime_mode(runtime_mode),
                    tool_toggles=tool_toggles,
                    connector_bindings=connector_bindings,
                    channel_bindings=channel_bindings,
                    metadata=metadata,
                    status="draft",
                )
            )
        resolved_runtime_profile_id, resolved_runtime_mode = await _resolve_runtime_binding(
            connection,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            runtime_profile_id=runtime_profile_id,
            runtime_mode=runtime_mode,
        )
        specialist_name = str(label or manifest.identity.name).strip() or manifest.identity.name
        specialist_slug = f"{_slugify(specialist_name, fallback='specialist')}-{uuid.uuid4().hex[:6]}"
        definition_id = f"agentdef_{uuid.uuid4().hex[:16]}"
        version_id = f"agentver_{uuid.uuid4().hex[:16]}"
        install_id = f"ainstall_{uuid.uuid4().hex[:16]}"
        projection_metadata = _manifest_projection_metadata(
            manifest,
            source=source,
            visibility=visibility,
            extras=metadata,
        )

        await connection.execute(
            """
            INSERT INTO agent_definitions (
                id, tenant_id, workspace_id, slug, name, description, agent_kind, visibility, status,
                category, icon, created_by_user_id, current_version_id, published_version_id,
                source_workflow_definition_id, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, 'specialist', 'workspace', 'draft',
                $7, NULL, $8, $9, $9,
                NULL, $10::jsonb, NOW(), NOW()
            )
            """,
            definition_id,
            tenant_id,
            workspace_id,
            specialist_slug,
            specialist_name,
            manifest.identity.summary,
            _manifest_category(manifest),
            created_by_user_id,
            version_id,
            _to_json({"source": "forge_specialist"}, default={}),
        )
        await connection.execute(
            """
            INSERT INTO agent_definition_versions (
                id, tenant_id, workspace_id, agent_definition_id, version_number, status, manifest,
                compiled_workflow_version_id, capability_manifest, memory_scope_manifest, policy_manifest,
                placement_manifest, template_inputs_schema, metadata, created_by_user_id, created_at
            ) VALUES (
                $1, $2, $3, $4, 1, 'draft', $5::jsonb,
                NULL, $6::jsonb, '{}'::jsonb, $7::jsonb,
                $8::jsonb, '{}'::jsonb, $9::jsonb, $10, NOW()
            )
            """,
            version_id,
            tenant_id,
            workspace_id,
            definition_id,
            _to_json(manifest.model_dump(mode="json"), default={}),
            _to_json(_manifest_capability_payload(manifest), default={}),
            _to_json(_manifest_policy_payload(manifest), default={}),
            _to_json(_manifest_placement_payload(resolved_runtime_profile_id, resolved_runtime_mode), default={}),
            _to_json({"source": "forge_specialist"}, default={}),
            created_by_user_id,
        )
        await connection.execute(
            """
            INSERT INTO workspace_agent_installs (
                id, tenant_id, workspace_id, agent_definition_id, agent_definition_version_id, installed_by_user_id,
                install_scope, owner_user_id, thread_id, label, status, enabled, runtime_profile_id, compiled_workflow_version_id,
                root_folder_uri, tool_toggles, folder_grants, connector_bindings, memory_scope_overrides,
                policy_context_overrides, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                'workspace', $6, NULL, $7, 'draft', TRUE, $8, NULL,
                NULL, $9::jsonb, '[]'::jsonb, $10::jsonb, '{}'::jsonb,
                $11::jsonb, $12::jsonb, NOW(), NOW()
            )
            """,
            install_id,
            tenant_id,
            workspace_id,
            definition_id,
            version_id,
            created_by_user_id,
            specialist_name,
            resolved_runtime_profile_id,
            _to_json(tool_toggles, default={}),
            _to_json(connector_bindings, default={}),
            _to_json(_manifest_policy_payload(manifest), default={}),
            _to_json(projection_metadata, default={}),
        )
        await _persist_specialist_state(
            connection,
            install_id=install_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            manifest=manifest,
            runtime_profile_id=resolved_runtime_profile_id,
            runtime_mode=resolved_runtime_mode,
            tool_toggles=tool_toggles,
            connector_bindings=connector_bindings,
            channel_bindings=channel_bindings,
            created_by_user_id=created_by_user_id,
            metadata=projection_metadata,
            status="draft",
            write_bible_version=True,
        )

    return await agent_registry_repository.get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def get_workspace_specialist(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> Optional[Dict[str, Any]]:
    bundle = await agent_registry_repository.get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if isinstance(bundle, dict):
        return bundle
    return _get_local_specialist_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


def manifest_from_install_bundle(install: Dict[str, Any]) -> Optional[AgentManifest]:
    payload = _dict_json(install)
    if str(_dict_json(payload.get("agent_definition")).get("agent_kind") or "").strip() == "master":
        return SAGE_GLOBAL_MANIFEST.model_copy(deep=True)
    for candidate in (
        _dict_json(_dict_json(payload.get("metadata")).get("agent_manifest")),
        _dict_json(_dict_json(payload.get("agent_definition_version")).get("manifest")),
        _dict_json(_dict_json(_dict_json(payload.get("agent_definition")).get("metadata")).get("agent_manifest")),
    ):
        if not candidate:
            continue
        try:
            return AgentManifest.model_validate(candidate)
        except Exception:
            continue
    return None


def channel_binding_matches_endpoint(
    install: Dict[str, Any],
    *,
    channel_key: str,
    endpoint_key: str,
) -> bool:
    payload = _dict_json(install)
    metadata = _dict_json(payload.get("metadata"))
    bindings = _dict_json(metadata.get("channel_registry_bindings"))
    channel_binding = _dict_json(bindings.get(channel_key))
    if not bool(channel_binding.get("enabled")) or not bool(channel_binding.get("is_inbound_owner")):
        return False
    return _normalize_endpoint_key(channel_binding.get("endpoint_key")) == _normalize_endpoint_key(endpoint_key)


async def resolve_active_inbound_channel_owner(
    *,
    tenant_id: str,
    workspace_id: str,
    channel_key: str,
    endpoint_key: str,
    allow_master_fallback: bool = False,
) -> Optional[Dict[str, Any]]:
    normalized_channel_key = str(channel_key or "").strip().lower()
    normalized_endpoint_key = _normalize_endpoint_key(endpoint_key)
    if normalized_channel_key not in _INBOUND_OWNER_CHANNEL_KEYS or normalized_endpoint_key is None:
        return None

    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            SELECT wai.id, ad.agent_kind
            FROM agent_channel_bindings acb
            INNER JOIN workspace_agent_installs wai
                ON wai.id = acb.agent_install_id
               AND wai.tenant_id = acb.tenant_id
               AND wai.workspace_id = acb.workspace_id
            INNER JOIN agent_definitions ad
                ON ad.id = wai.agent_definition_id
            WHERE acb.tenant_id = $1
              AND acb.workspace_id = $2
              AND acb.channel_key = $3
              AND acb.enabled = TRUE
              AND wai.enabled = TRUE
              AND lower(COALESCE(wai.status, 'active')) = 'active'
              AND lower(COALESCE(acb.binding->>'is_inbound_owner', 'false')) = 'true'
              AND lower(COALESCE(acb.binding->>'endpoint_key', '')) = $4
            ORDER BY CASE WHEN ad.agent_kind = 'master' THEN 0 ELSE 1 END, wai.updated_at DESC, wai.created_at DESC
            LIMIT 1
            """,
            tenant_id,
            workspace_id,
            normalized_channel_key,
            normalized_endpoint_key,
        )

    if row is not None:
        install = await agent_registry_repository.get_workspace_agent_install_bundle(
            str(row.get("id") or "").strip(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        manifest = manifest_from_install_bundle(install or {})
        if isinstance(install, dict) and manifest is not None:
            return {
                "install": install,
                "manifest": manifest,
                "owner_type": str(row.get("agent_kind") or "").strip() or "specialist",
            }

    if not allow_master_fallback:
        return None

    master_install = await agent_registry_repository.get_workspace_master_agent_install(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not isinstance(master_install, dict):
        return None
    master_manifest = manifest_from_install_bundle(master_install)
    if master_manifest is None:
        return None
    if channel_binding_matches_endpoint(
        master_install,
        channel_key=normalized_channel_key,
        endpoint_key=normalized_endpoint_key,
    ) or (
        normalized_channel_key == "web_chat"
        and normalized_endpoint_key == _WEB_CHAT_DEFAULT_ENDPOINT_KEY
    ):
        return {
            "install": master_install,
            "manifest": master_manifest,
            "owner_type": "master",
        }
    return None


async def update_workspace_specialist_manifest(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    manifest: AgentManifest,
    updated_by_user_id: Optional[str] = None,
    runtime_profile_id: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    tool_toggles: Optional[Dict[str, Any]] = None,
    connector_bindings: Optional[Dict[str, Any]] = None,
    channel_bindings: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    write_bible_version: bool = True,
) -> Optional[Dict[str, Any]]:
    metadata = _dict_json(metadata)
    tool_toggles = _dict_json(tool_toggles)
    connector_bindings = _dict_json(connector_bindings)
    channel_bindings = _dict_json(channel_bindings)
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            local_install = _get_local_specialist_bundle(
                install_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if not isinstance(local_install, dict):
                return None
            current_metadata = _dict_json(local_install.get("metadata"))
            merged_metadata = {**current_metadata, **metadata}
            next_bundle = _build_local_specialist_bundle(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                install_id=install_id,
                definition_id=str(local_install.get("agent_definition_id") or f"agentdef_{uuid.uuid4().hex[:16]}"),
                version_id=str(local_install.get("agent_definition_version_id") or f"agentver_{uuid.uuid4().hex[:16]}"),
                manifest=manifest,
                created_by_user_id=updated_by_user_id or local_install.get("installed_by_user_id"),
                label=str(local_install.get("label") or manifest.identity.name).strip() or manifest.identity.name,
                runtime_profile_id=(
                    _normalize_token(runtime_profile_id)
                    if runtime_profile_id is not None
                    else _normalize_token(local_install.get("runtime_profile_id"))
                ),
                runtime_mode=(
                    _normalize_token(runtime_mode)
                    if runtime_mode is not None
                    else _normalize_token(local_install.get("runtime_mode")) or "hosted_secure"
                ),
                tool_toggles=tool_toggles or _dict_json(local_install.get("tool_toggles")),
                connector_bindings={**_dict_json(local_install.get("connector_bindings")), **connector_bindings},
                channel_bindings=channel_bindings or _dict_json(local_install.get("channel_bindings")),
                metadata=merged_metadata,
                status=str(merged_metadata.get("status") or local_install.get("status") or "draft").strip() or "draft",
            )
            next_bundle["created_at"] = local_install.get("created_at") or next_bundle.get("created_at")
            return _store_local_specialist_bundle(next_bundle)
        install_row = await connection.fetchrow(
            """
            SELECT
                wai.connector_bindings,
                wai.tool_toggles,
                wai.runtime_profile_id,
                wai.metadata,
                arp.runtime_mode
            FROM workspace_agent_installs wai
            LEFT JOIN agent_runtime_profiles arp
                ON arp.agent_install_id = wai.id
               AND arp.tenant_id = wai.tenant_id
               AND arp.workspace_id = wai.workspace_id
            WHERE wai.id = $1 AND wai.tenant_id = $2 AND wai.workspace_id = $3
            LIMIT 1
            """,
            install_id,
            tenant_id,
            workspace_id,
        )
        if install_row is None:
            return None
        requested_runtime_profile_id = (
            _normalize_token(runtime_profile_id)
            if runtime_profile_id is not None
            else _normalize_token(install_row.get("runtime_profile_id"))
        )
        requested_runtime_mode = (
            _normalize_token(runtime_mode)
            if runtime_mode is not None
            else _normalize_token(install_row.get("runtime_mode")) or "hosted_secure"
        )
        resolved_runtime_profile_id, resolved_runtime_mode = await _resolve_runtime_binding(
            connection,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            runtime_profile_id=requested_runtime_profile_id,
            runtime_mode=requested_runtime_mode,
        )
        merged_tool_toggles = (
            tool_toggles
            if tool_toggles
            else _dict_json(install_row.get("tool_toggles"))
        )
        merged_connector_bindings = _dict_json(install_row.get("connector_bindings"))
        merged_connector_bindings.update(connector_bindings)
        existing_channel_bindings = await _load_channel_bindings(connection, install_id, tenant_id, workspace_id)
        merged_channel_bindings = _merge_channel_bindings(
            existing_channel_bindings or manifest.channels.model_dump(mode="json"),
            channel_bindings,
        )
        current_metadata = _dict_json(install_row.get("metadata"))
        projection_metadata = _manifest_projection_metadata(
            manifest,
            source=str(metadata.get("source") or current_metadata.get("source") or "draft").strip() or "draft",
            visibility=str(metadata.get("visibility") or current_metadata.get("visibility") or "private").strip() or "private",
            extras={**current_metadata, **metadata},
        )
        await _persist_specialist_state(
            connection,
            install_id=install_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            manifest=manifest,
            runtime_profile_id=resolved_runtime_profile_id,
            runtime_mode=resolved_runtime_mode,
            tool_toggles=merged_tool_toggles,
            connector_bindings=merged_connector_bindings,
            channel_bindings=merged_channel_bindings,
            created_by_user_id=updated_by_user_id,
            metadata=projection_metadata,
            status=str(metadata.get("status") or current_metadata.get("status") or "draft").strip() or "draft",
            write_bible_version=write_bible_version,
        )
    return await agent_registry_repository.get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def save_workspace_specialist_bible(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    bible_text: str,
    updated_by_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return None
        manifest = await _load_manifest(connection, install_id, tenant_id, workspace_id)
        if manifest is None:
            return None
        sections = _parse_bible_text(bible_text)
        manifest.bible = AgentManifestBible(
            mission=sections["mission"] or manifest.bible.mission,
            hard_context=sections["hard context"] or manifest.bible.hard_context,
            operational_policy=sections["operational policy"] or manifest.bible.operational_policy,
            core_responsibilities=sections["core responsibilities"] or manifest.bible.core_responsibilities,
            guardrails=sections["guardrails"] or manifest.bible.guardrails,
            escalation_triggers=sections["escalation triggers"] or manifest.bible.escalation_triggers,
        )
    return await update_workspace_specialist_manifest(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        updated_by_user_id=updated_by_user_id,
        write_bible_version=True,
    )


async def save_workspace_specialist_skill_bindings(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    skill_ids: list[str],
    updated_by_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return None
        manifest = await _load_manifest(connection, install_id, tenant_id, workspace_id)
        if manifest is None:
            return None
        seen: set[str] = set()
        manifest.skills = []
        for skill_id in skill_ids:
            token = str(skill_id or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            manifest.skills.append(AgentManifestSkillBinding(id=token, enabled=True))
    return await update_workspace_specialist_manifest(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        updated_by_user_id=updated_by_user_id,
        write_bible_version=False,
    )


async def save_workspace_specialist_connector_bindings(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    connector_bindings: Dict[str, Any],
    updated_by_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return None
        manifest = await _load_manifest(connection, install_id, tenant_id, workspace_id)
        if manifest is None:
            return None
    return await update_workspace_specialist_manifest(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        updated_by_user_id=updated_by_user_id,
        connector_bindings=connector_bindings,
        write_bible_version=False,
    )


async def save_workspace_specialist_channel_bindings(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    channel_bindings: Dict[str, Any],
    updated_by_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return None
        manifest = await _load_manifest(connection, install_id, tenant_id, workspace_id)
        if manifest is None:
            return None
        current_channels = manifest.channels.model_dump(mode="json")
        next_channels = {**current_channels}
        for key, value in channel_bindings.items():
            token = str(key or "").strip()
            if not token:
                continue
            if isinstance(value, bool):
                next_channels[token] = value
            else:
                payload = _dict_json(value)
                if "enabled" in payload:
                    next_channels[token] = bool(payload.get("enabled"))
        manifest.channels = type(manifest.channels).model_validate(next_channels)
    return await update_workspace_specialist_manifest(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        updated_by_user_id=updated_by_user_id,
        channel_bindings=channel_bindings,
        write_bible_version=False,
    )


async def save_workspace_specialist_runtime_profile(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_profile_id: Optional[str],
    runtime_mode: str,
    updated_by_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return None
        manifest = await _load_manifest(connection, install_id, tenant_id, workspace_id)
        if manifest is None:
            return None
    return await update_workspace_specialist_manifest(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        manifest=manifest,
        updated_by_user_id=updated_by_user_id,
        runtime_profile_id=runtime_profile_id,
        runtime_mode=runtime_mode,
        write_bible_version=False,
    )
