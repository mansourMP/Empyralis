from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Literal

from server_modules.agent_manifest import AgentManifest, manifest_skill_ids
from server_modules import agent_action_metering_service, egress_policy, safe_mode_service, skill_registry, tool_broker_guard_service


ToolActionClass = Literal["read", "write", "execute"]
ConnectorExecutionClass = Literal["api_connector", "browser_connector", "media_generation_connector"]
_ACTION_CLASS_ORDER = {"read": 0, "write": 1, "execute": 2}
_SUPPORTED_RUNTIME_MODES = {"hosted_secure", "local_secure", "privileged_device"}
_SUPPORTED_CONNECTOR_CLASSES = {"api_connector", "browser_connector", "media_generation_connector"}
_CONNECTOR_CLASS_ALLOWED_ACTIONS = {
    "api_connector": {"read", "write"},
    "browser_connector": {"read", "write", "execute"},
    "media_generation_connector": {"write", "execute"},
}
_TOKEN_VERSION = "empyralis.tool-grant.v1"
DEFAULT_CAPABILITY_TOKEN_TTL_SECONDS = 120
MASTER_SCOPE_WILDCARD = "*"
_LOCAL_ENV_TOKENS = {"", "dev", "development", "local", "test", "testing"}
_INSECURE_BROKER_SECRETS = {
    "empyralis-dev-tool-broker-secret",
    "empyralis-dev-secrets-broker-secret",
}

# ── Stage 4B: missing-agent-id instrumentation ──────────────────────────
_MISSING_AGENT_ID_COUNT = 0
# Phase K: legacy tokens without agent_id claim
_LEGACY_TOKEN_AGENT_ID_COUNT = 0


def _require_agent_id_flag() -> bool:
    """Read EMPYRALIS_REQUIRE_AGENT_ID from env.

    Default: false. When true, execute_skill raises if agent_id is missing.
    Do NOT flip until all callers thread agent_id.
    """
    return os.getenv("EMPYRALIS_REQUIRE_AGENT_ID", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def missing_agent_id_count() -> int:
    """Return total count of execute_skill calls missing agent_id."""
    return _MISSING_AGENT_ID_COUNT


def legacy_token_agent_id_count() -> int:
    """Return count of capability tokens validated without agent_id claim."""
    return _LEGACY_TOKEN_AGENT_ID_COUNT


@dataclass(frozen=True)
class CapabilityGrant:
    token: str
    claims: Dict[str, Any]


class ToolBrokerError(RuntimeError):
    pass


class ToolExecutionDeniedError(ToolBrokerError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = str(code or "tool_execution_denied").strip() or "tool_execution_denied"
        self.detail = str(detail or "Tool execution was denied.").strip() or "Tool execution was denied."


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_runtime_mode(runtime_mode: str) -> str:
    token = _normalize_token(runtime_mode)
    return token if token in _SUPPORTED_RUNTIME_MODES else "hosted_secure"


def _normalize_action_class(action_class: Any) -> ToolActionClass:
    token = _normalize_token(action_class)
    if token not in _ACTION_CLASS_ORDER:
        raise ToolExecutionDeniedError("invalid_action_class", f"Unsupported action class '{action_class}'.")
    return token  # type: ignore[return-value]


def _normalize_connector_class(connector_class: Any) -> ConnectorExecutionClass:
    token = _normalize_token(connector_class)
    if token not in _SUPPORTED_CONNECTOR_CLASSES:
        raise ToolExecutionDeniedError("invalid_connector_class", f"Unsupported connector class '{connector_class}'.")
    return token  # type: ignore[return-value]


def _ordered_unique(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = _normalize_token(raw)
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _resolved_environment() -> str:
    return str(os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or os.getenv("ENV") or os.getenv("NODE_ENV") or "").strip().lower()


def _environment_requires_explicit_secret() -> bool:
    return _resolved_environment() not in _LOCAL_ENV_TOKENS


def _signing_secret() -> bytes:
    secret = str(
        os.getenv("EMPYRALIS_TOOL_BROKER_SECRET")
        or os.getenv("EMPYRALIS_SECRETS_BROKER_SECRET")
        or ""
    ).strip()
    if secret and secret not in _INSECURE_BROKER_SECRETS:
        return secret.encode("utf-8")
    raise RuntimeError(
        "Broker secret must be explicitly set. Refusing to start with insecure default."
    )


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64decode(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(f"{token}{padding}")


def _json_dumps(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _is_master_manifest(manifest: AgentManifest) -> bool:
    return manifest.scope == "global_master" or manifest.identity.archetype == "master_os"


def _skill_definitions_for_ids(skill_ids: list[str], *, workspace_id: str | None = None) -> list[skill_registry.SkillDefinition]:
    definitions: list[skill_registry.SkillDefinition] = []
    for skill_id in skill_ids:
        definition = skill_registry.get_skill_definition(skill_id, workspace_id=workspace_id)
        if definition is not None:
            definitions.append(definition)
    return definitions


def _connector_scopes_for_manifest(manifest: AgentManifest, skill_ids: list[str], *, workspace_id: str | None = None) -> list[str]:
    if _is_master_manifest(manifest):
        return [MASTER_SCOPE_WILDCARD]
    manifest_scopes = _ordered_unique(list(manifest.connectors.bound))
    derived_scopes = skill_registry.skill_connector_scopes(skill_ids, workspace_id=workspace_id)
    return _ordered_unique([*manifest_scopes, *derived_scopes])


def _action_classes_for_manifest(manifest: AgentManifest, definitions: list[skill_registry.SkillDefinition]) -> list[str]:
    if _is_master_manifest(manifest):
        return ["read", "write", "execute"]
    action_classes = _ordered_unique([definition.action_class for definition in definitions])
    return sorted(action_classes or ["read"], key=lambda item: _ACTION_CLASS_ORDER.get(item, 99))


def issue_capability_token(
    *,
    manifest: AgentManifest,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str | None = None,
    agent_id: str = "",
    runtime_mode: str,
    runtime_scope: Dict[str, Any] | None = None,
    privileged_runtime_approved: bool = False,
    ttl_seconds: int = DEFAULT_CAPABILITY_TOKEN_TTL_SECONDS,
) -> CapabilityGrant:
    normalized_runtime_mode = _normalize_runtime_mode(runtime_mode)
    normalized_runtime_scope = _coerce_dict(runtime_scope)
    issued_at = int(time.time())
    expires_at = issued_at + max(int(ttl_seconds or DEFAULT_CAPABILITY_TOKEN_TTL_SECONDS), 1)
    skill_ids = (
        [definition.id for definition in skill_registry.list_skill_definitions(workspace_id=workspace_id)]
        if _is_master_manifest(manifest)
        else manifest_skill_ids(manifest)
    )
    definitions = _skill_definitions_for_ids(skill_ids, workspace_id=workspace_id)
    claims = {
        "version": _TOKEN_VERSION,
        "grant_id": f"grant_{secrets.token_urlsafe(12)}",
        "manifest_id": manifest.manifest_id,
        "agent_name": manifest.identity.name,
        "agent_scope": manifest.scope,
        "agent_install_id": str(agent_install_id or "").strip() or None,
        "agent_id": str(agent_id or "").strip() or None,
        "runtime_mode": normalized_runtime_mode,
        "runtime_constraints": {
            "mode": normalized_runtime_mode,
            "driver": str(normalized_runtime_scope.get("driver") or "").strip() or None,
            "workspace_kind": str(normalized_runtime_scope.get("workspace_kind") or "").strip() or None,
            "state_layer_policy": _coerce_dict(normalized_runtime_scope.get("state_layer_policy")),
        },
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "allowed_skills": skill_ids,
        "allowed_connector_scopes": _connector_scopes_for_manifest(manifest, skill_ids, workspace_id=workspace_id),
        "allowed_action_classes": _action_classes_for_manifest(manifest, definitions),
        "approval_granted": bool(privileged_runtime_approved or manifest.policy.approval_mode == "system"),
        "policy_approval_mode": manifest.policy.approval_mode,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    payload = _b64encode(_json_dumps(claims).encode("utf-8"))
    signature = _b64encode(hmac.new(_signing_secret(), payload.encode("utf-8"), hashlib.sha256).digest())
    return CapabilityGrant(token=f"{payload}.{signature}", claims=claims)


def verify_capability_token(
    token: str,
    *,
    expected_manifest_id: str | None = None,
    expected_tenant_id: str | None = None,
    expected_workspace_id: str | None = None,
    runtime_mode: str | None = None,
    now: int | None = None,
) -> Dict[str, Any]:
    raw = str(token or "").strip()
    if "." not in raw:
        raise ToolExecutionDeniedError("invalid_token", "Capability token is malformed.")
    payload_part, signature_part = raw.split(".", 1)
    expected_signature = _b64encode(hmac.new(_signing_secret(), payload_part.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature_part, expected_signature):
        raise ToolExecutionDeniedError("invalid_token", "Capability token signature is invalid.")
    try:
        claims = json.loads(_b64decode(payload_part))
    except Exception as error:
        raise ToolExecutionDeniedError("invalid_token", "Capability token payload is invalid.") from error
    if not isinstance(claims, dict):
        raise ToolExecutionDeniedError("invalid_token", "Capability token payload is invalid.")
    if str(claims.get("version") or "").strip() != _TOKEN_VERSION:
        raise ToolExecutionDeniedError("invalid_token_version", "Capability token version is invalid.")

    current_time = int(now if now is not None else time.time())
    if current_time >= int(claims.get("expires_at") or 0):
        raise ToolExecutionDeniedError("token_expired", "Capability token expired before the tool call executed.")
    if expected_manifest_id and str(claims.get("manifest_id") or "").strip() != str(expected_manifest_id or "").strip():
        raise ToolExecutionDeniedError("manifest_mismatch", "Capability token does not match the active manifest.")
    if expected_tenant_id and str(claims.get("tenant_id") or "").strip() != str(expected_tenant_id or "").strip():
        raise ToolExecutionDeniedError("tenant_mismatch", "Capability token does not match the active tenant.")
    if expected_workspace_id and str(claims.get("workspace_id") or "").strip() != str(expected_workspace_id or "").strip():
        raise ToolExecutionDeniedError("workspace_mismatch", "Capability token does not match the active workspace.")
    if runtime_mode and _normalize_runtime_mode(str(claims.get("runtime_mode") or "")) != _normalize_runtime_mode(runtime_mode):
        raise ToolExecutionDeniedError("runtime_mismatch", "Capability token does not authorize this runtime profile.")
    return claims


def _require_action_class(claims: Dict[str, Any], action_class: ToolActionClass) -> None:
    allowed = _ordered_unique([str(item) for item in list(claims.get("allowed_action_classes") or [])])
    required_rank = _ACTION_CLASS_ORDER.get(action_class, 99)
    granted_rank = max((_ACTION_CLASS_ORDER.get(token, -1) for token in allowed), default=-1)
    if granted_rank < required_rank:
        raise ToolExecutionDeniedError(
            "action_class_not_granted",
            f"The current capability token does not allow {action_class} tool execution.",
        )


def _require_runtime_security_controls(claims: Dict[str, Any]) -> None:
    tenant_id = str(claims.get("tenant_id") or "").strip() or None
    workspace_id = str(claims.get("workspace_id") or "").strip() or None
    agent_install_id = str(claims.get("agent_install_id") or "").strip() or None
    machine_policy = safe_mode_service.resolve_machine_policy_status(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if bool((machine_policy.get("kill_switch") or {}).get("active")):
        raise ToolExecutionDeniedError(
            "workspace_disabled",
            "This workspace is under a security kill switch and cannot execute tools right now.",
        )
    if agent_install_id and safe_mode_service.is_agent_disabled(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
    ):
        raise ToolExecutionDeniedError(
            "agent_disabled",
            "This agent is currently disabled by a security kill switch.",
        )


def _require_connector_scopes(claims: Dict[str, Any], required_scopes: tuple[str, ...] | list[str]) -> None:
    required = _ordered_unique(list(required_scopes))
    if not required:
        return
    allowed = set(_ordered_unique([str(item) for item in list(claims.get("allowed_connector_scopes") or [])]))
    if MASTER_SCOPE_WILDCARD in allowed:
        return
    missing = [scope for scope in required if scope not in allowed]
    if missing:
        raise ToolExecutionDeniedError(
            "connector_scope_not_granted",
            f"The current capability token is missing connector scope(s): {', '.join(missing)}.",
        )
    workspace_id = str(claims.get("workspace_id") or "").strip() or None
    tenant_id = str(claims.get("tenant_id") or "").strip() or None
    for scope in required:
        if safe_mode_service.is_connector_disabled(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            connector_id=scope,
        ):
            raise ToolExecutionDeniedError(
                "connector_disabled",
                f"The {scope} connector is disabled by a security control.",
            )
        incident = safe_mode_service.resolve_connector_incident_state(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            connector_id=scope,
        )
        if bool(incident.get("active")):
            mode = str(incident.get("mode") or "pause").strip().lower() or "pause"
            matched_scope = str(incident.get("scope") or "connector").strip().lower() or "connector"
            code_prefix = "workspace" if matched_scope == "workspace" else "connector"
            detail = (
                "This workspace is temporarily paused by an operator incident control."
                if code_prefix == "workspace" and mode == "pause"
                else "This workspace is temporarily draining new work while the operator clears backlog."
                if code_prefix == "workspace" and mode == "drain"
                else "This workspace is temporarily rejecting new work during an incident."
                if code_prefix == "workspace" and mode == "reject"
                else f"The {scope} connector is temporarily paused by an operator incident control."
                if mode == "pause"
                else f"The {scope} connector is temporarily draining backlog before it accepts new work."
                if mode == "drain"
                else f"The {scope} connector is temporarily rejecting new work during an incident."
            )
            denial_code = {
                ("workspace", "pause"): "workspace_paused",
                ("workspace", "drain"): "workspace_draining",
                ("workspace", "reject"): "workspace_rejected",
                ("connector", "pause"): "connector_paused",
                ("connector", "drain"): "connector_draining",
                ("connector", "reject"): "connector_rejected",
            }.get((code_prefix, mode), "connector_paused")
            raise ToolExecutionDeniedError(denial_code, detail)


def _require_runtime_allowed(definition: skill_registry.SkillDefinition, runtime_mode: str) -> None:
    normalized_runtime_mode = _normalize_runtime_mode(runtime_mode)
    if normalized_runtime_mode not in definition.allowed_runtime_modes:
        raise ToolExecutionDeniedError(
            "runtime_not_allowed",
            f"{definition.label} cannot run in {normalized_runtime_mode}.",
        )


def _require_approval_if_needed(claims: Dict[str, Any], definition: skill_registry.SkillDefinition) -> None:
    if definition.requires_approval and not bool(claims.get("approval_granted")):
        raise ToolExecutionDeniedError(
            "elevated_action_blocked",
            f"{definition.label} is not available in the current safety mode. "
            "Switch to a less restrictive mode or rephrase the request.",
        )


def _require_broker_guard(
    *,
    claims: Dict[str, Any],
    tool_key: str,
    action_class: str,
    connector_scope: str = "",
    surface: str = "skill",
) -> None:
    decision = tool_broker_guard_service.evaluate_tool_call(
        claims=claims,
        tool_key=tool_key,
        action_class=action_class,
        connector_scope=connector_scope,
        surface=surface,
    )
    if not decision.allowed:
        raise ToolExecutionDeniedError(decision.code, decision.detail)


# ── Stage 4B: per-agent tool/connector enforcement ──────────────────────

async def _enforce_agent_tool_policy(
    agent_install_id: str,
    workspace_id: str,
    tool_name: str,
    connector_scopes: list,
) -> None:
    """Deny if the agent's install does not permit this tool or connector.

    Writes a platform-voice denial to the ledger. Does NOT bypass via env var.
    Existing agents default to ALL (no-op), so this is safe to enable now.
    """
    try:
        from server_modules import agent_registry_repository as _reg
        install = await _reg.get_workspace_agent_install_bundle(
            agent_install_id, tenant_id=None, workspace_id=workspace_id
        )
    except Exception:
        return  # can't load install — allow through (fail-open for now)

    if not install or not isinstance(install, dict):
        return

    enabled_tools = install.get("enabled_tools")
    enabled_connectors = install.get("enabled_connectors")

    denial_reasons: list[str] = []

    # Check tool
    if enabled_tools is not None and isinstance(enabled_tools, list) and len(enabled_tools) > 0:
        if tool_name not in enabled_tools:
            denial_reasons.append(f"tool_not_enabled:{tool_name}")

    # Check connectors
    if (
        enabled_connectors is not None
        and isinstance(enabled_connectors, list)
        and len(enabled_connectors) > 0
        and connector_scopes
    ):
        for scope in connector_scopes:
            if scope and scope not in enabled_connectors:
                denial_reasons.append(f"connector_not_enabled:{scope}")

    if denial_reasons:
        detail = "; ".join(denial_reasons)
        # Write ledger denial record
        try:
            from server_modules import activity_ledger_service

            await activity_ledger_service.append_activity_event(
                tenant_id="system",
                workspace_id=workspace_id,
                actor_type="agent",
                actor_id=agent_install_id,
                event_class="policy_denial",
                detail_level="audit_reference",
                action="tool_not_enabled",
                title=f"Tool denied: {tool_name}",
                summary=(
                    f"Agent {agent_install_id} attempted {tool_name} "
                    f"but is not authorized. {detail}"
                ),
                status="blocked",
                metadata={
                    "agent_install_id": agent_install_id,
                    "tool": tool_name,
                    "connector_scopes": connector_scopes,
                    "denial_reasons": denial_reasons,
                },
            )
        except Exception:
            pass

        raise ToolExecutionDeniedError(
            "agent_tool_not_enabled",
            f"Access not enabled for this agent. {detail}",
        )


# ── Phase N: memory tool dispatch ───────────────────────────────────────────

_MEMORY_SKILL_IDS = frozenset({"memory-read", "memory-write", "memory-list"})


async def _dispatch_memory_tool(
    *,
    skill_id: str,
    agent_install_id: str,
    agent_id: str,
    workspace_id: str,
    goal: str,
) -> Dict[str, Any]:
    """Dispatch a memory tool to agent_memory_tools.

    Parses the `goal` field for tool arguments.
    For memory_read: extracts a path from the goal
    For memory_write: extracts path and content from the goal
    For memory_list: no arguments needed
    """
    from server_modules import agent_memory_tools as _mem

    if skill_id == "memory-list":
        return await _mem.memory_list(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
            agent_id=agent_id,
        )

    if skill_id == "memory-read":
        # Extract path from goal — look for a filename or path mention
        path = _extract_memory_path(goal)
        if not path:
            return {"ok": False, "error": "No path specified. Usage: memory_read with a filename like notes.md or memory/notes.md"}
        return await _mem.memory_read(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
            agent_id=agent_id,
            path=path,
        )

    if skill_id == "memory-write":
        path = _extract_memory_path(goal)
        content = _extract_memory_content(goal)
        if not path:
            return {"ok": False, "error": "No path specified. Usage: memory_write with a filename and content."}
        if not content:
            return {"ok": False, "error": "No content to write."}
        return await _mem.memory_write(
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
            agent_id=agent_id,
            path=path,
            content=content,
        )

    return {"ok": False, "error": f"Unknown memory tool: {skill_id}"}


def _extract_memory_path(goal: str) -> str:
    """Extract a file path from a goal string.

    Looks for quoted strings, bare .md filenames, or path-like patterns.
    """
    import re
    text = str(goal or "")

    # Quoted path: "notes.md" or 'memory/notes.md'
    m = re.search(r"""["']([^"']+\.[a-z]{1,10})["']""", text)
    if m:
        return m.group(1).strip()

    # Bare .md filename: notes.md, memory/notes.md
    m = re.search(r"(\S+\.md)\b", text)
    if m:
        return m.group(1).strip()

    # Look for "path:", "file:", "read:" prefixes
    for prefix in ("path:", "file:", "read:", "write to ", "write ", "read "):
        if prefix in text.lower():
            after = text.lower().split(prefix, 1)[-1].strip()
            # Take first word or quoted string
            qm = re.search(r"""["']([^"']+)["']""", after)
            if qm:
                return qm.group(1).strip()
            word = after.split()[0] if after.split() else ""
            return word.strip().rstrip(",.;:")

    return ""


def _extract_memory_content(goal: str) -> str:
    """Extract write content from a goal string.

    Takes everything after the path/file reference as content.
    Also handles content after colons, quotes, or newline markers.
    """
    import re
    text = str(goal or "")

    # Remove leading "memory_write" or tool mention
    text = re.sub(r"(?i)memory[_ ]?write\b[:\s]*", "", text)

    # Try to find content after the path
    # Pattern: "path" followed by content
    m = re.search(r"""["']([^"']+\.[a-z]{1,10})["']\s*[:：]\s*(.+)""", text, re.DOTALL)
    if m:
        return m.group(2).strip()

    # Pattern: path.md followed by content
    m = re.search(r"(\S+\.md)\s*[:：]\s*(.+)", text, re.DOTALL)
    if m:
        return m.group(2).strip()

    # If there's a clear colon separator, take content after it
    if ": " in text:
        parts = text.split(": ", 1)
        # Skip the part that looks like a path
        if ".md" in parts[0] or len(parts[0].split()) <= 2:
            return parts[-1].strip()

    # Fallback: everything after first sentence that mentions a path
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) > 1:
        return " ".join(sentences[1:]).strip()

    return text.strip()


# ── Phase L: fleet tool role enforcement ────────────────────────────────────

_FLEET_SKILL_IDS = frozenset({
    "fleet-create-agent",
    "fleet-list-agents",
    "fleet-get-agent-activity",
    "fleet-configure-agent",
    "fleet-message-agent",
})


def _is_fleet_tool(skill_id: str) -> bool:
    """Check if a skill is a fleet management tool."""
    return str(skill_id or "").strip() in _FLEET_SKILL_IDS


async def _enforce_fleet_tool_role(
    agent_install_id: str,
    workspace_id: str,
    skill_id: str,
) -> None:
    """Deny if the agent is not an operator invoking a fleet tool.

    Writes a policy_denial ledger event. Only operators can use fleet tools.
    Does NOT bypass via env var.
    """
    if not _is_fleet_tool(skill_id):
        return

    try:
        from server_modules import agent_registry_repository as _reg
        from server_modules.fleet_tools import resolve_agent_role, OPERATOR_ROLE

        install = await _reg.get_workspace_agent_install_bundle(
            agent_install_id, tenant_id=None, workspace_id=workspace_id
        )
        role = resolve_agent_role(install)
        if role == OPERATOR_ROLE:
            return  # operator — allowed

        # Specialist or unrecognized — denied
        try:
            from server_modules import activity_ledger_service

            await activity_ledger_service.append_activity_event(
                tenant_id="system",
                workspace_id=workspace_id,
                actor_type="agent",
                actor_id=agent_install_id,
                event_class="fleet_control",
                detail_level="audit_reference",
                action="policy_denial",
                title=f"Fleet tool denied: {skill_id}",
                summary=(
                    f"Agent {agent_install_id} (role={role}) attempted fleet tool "
                    f"'{skill_id}'. Only operators may manage the fleet."
                ),
                status="blocked",
                metadata={
                    "agent_install_id": agent_install_id,
                    "agent_role": role,
                    "skill_id": skill_id,
                    "required_role": OPERATOR_ROLE,
                },
            )
        except Exception:
            pass

        # Phase 4: a specialist reaching for an operator tool is an out-of-scope
        # decision — escalate to the owner/operator instead of silently blocking.
        try:
            from server_modules import specialist_escalation_service

            _install_meta = install.get("metadata") if isinstance(install, dict) else {}
            _tenant = str((_install_meta or {}).get("tenant_id") or "system").strip() or "system"
            await specialist_escalation_service.escalate_out_of_scope(
                tenant_id=_tenant,
                workspace_id=workspace_id,
                agent_install_id=agent_install_id,
                agent_label=str((install or {}).get("label") or "").strip(),
                reason=f"attempted operator-only fleet tool '{skill_id}'",
                detail="Specialists cannot manage the fleet; routed to the operator for a decision.",
                metadata={"skill_id": skill_id, "agent_role": role, "required_role": OPERATOR_ROLE},
            )
        except Exception:
            pass

        raise ToolExecutionDeniedError(
            "fleet_tool_requires_operator",
            f"The {skill_id} tool is restricted to operator agents. "
            f"Current role: {role}. Required role: {OPERATOR_ROLE}.",
        )
    except ToolExecutionDeniedError:
        raise
    except Exception:
        # Can't resolve role — fail closed for fleet tools
        raise ToolExecutionDeniedError(
            "fleet_tool_role_unresolvable",
            f"Cannot resolve agent role for fleet tool '{skill_id}'. "
            f"Fleet tools require a confirmed operator role.",
        )


async def execute_skill(
    *,
    capability_token: str,
    manifest_id: str,
    tenant_id: str,
    workspace_id: str,
    runtime_mode: str,
    skill_id: str,
    goal: str,
    agent_label: str,
    hard_context: str,
    operational_policy: str,
    agent_id: str = "",
    agent_install_id: str = "",
) -> Dict[str, Any]:
    global _MISSING_AGENT_ID_COUNT, _LEGACY_TOKEN_AGENT_ID_COUNT
    # ── Stage 4B: missing-agent-id instrumentation ────────────────────
    resolved_agent_id = str(agent_id or agent_install_id or "").strip()
    if not resolved_agent_id:
        _MISSING_AGENT_ID_COUNT += 1
        try:
            from server_modules import activity_ledger_service as _als
            import asyncio as _aio
            async def _emit():
                await _als.append_activity_event(
                    tenant_id="system",
                    workspace_id=workspace_id,
                    actor_type="platform",
                    actor_id="tool_broker",
                    event_class="platform_integrity",
                    detail_level="audit_reference",
                    action="tool_dispatch_missing_agent_id",
                    title="Tool dispatch without agent_id",
                    summary=(
                        f"execute_skill called for '{skill_id}' without agent_id. "
                        f"manifest_id={manifest_id}, caller requires agent_id "
                        f"threading for Stage 4B enforcement."
                    ),
                    status="logged",
                    metadata={
                        "skill_id": skill_id,
                        "manifest_id": manifest_id,
                        "require_agent_id_flag": _require_agent_id_flag(),
                    },
                )
            try:
                _aio.get_running_loop()
                _aio.create_task(_emit())
            except RuntimeError:
                _aio.run(_emit())
        except Exception:
            pass
        if _require_agent_id_flag():
            raise ToolExecutionDeniedError(
                "missing_agent_id",
                "agent_id is required for tool dispatch "
                "(EMPYRALIS_REQUIRE_AGENT_ID is set).",
            )

    source_event_id = agent_action_metering_service.build_source_event_id(
        source_surface="tool_broker_skill",
        run_id=manifest_id,
        tool_call_id=f"{skill_id}:{uuid.uuid4().hex}",
        action_name=skill_id,
    )
    common_event = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "surface": "sage",
        "source_surface": "tool_broker_skill",
        "action_domain": "skill",
        "action_name": skill_id,
        "tool_kind": "skill",
        "skill_id": skill_id,
        "payer": "platform_credits",
        "billing_mode": "transparency",
        "input_summary": goal,
        "source_table": "tool_broker_skill_calls",
        "source_event_id": source_event_id,
        "metadata": {
            "manifest_id": manifest_id,
            "runtime_mode": runtime_mode,
            "agent_label": agent_label,
        },
    }
    definition = skill_registry.get_skill_definition(skill_id, workspace_id=workspace_id, include_disabled=True)
    if definition is None:
        await agent_action_metering_service.record_failed(
            **common_event,
            action_type="invoke",
            error_code="skill_missing",
            output_summary=f"The requested skill {skill_id} is not registered.",
        )
        return {
            "status": "missing",
            "reply": f"The requested skill {skill_id} is not registered in the universal harness.",
            "artifact": None,
            "steps": [
                {"label": "Resolving skill registry", "detail": skill_id, "status": "error", "kind": "connector"},
            ],
        }
    if not definition.enabled:
        await agent_action_metering_service.record_blocked(
            **common_event,
            action_type="invoke",
            error_code="skill_disabled",
            policy_decision="blocked",
            output_summary=f"The {definition.label} skill is disabled for this workspace.",
        )
        raise ToolExecutionDeniedError(
            "skill_disabled",
            f"The {definition.label} skill is disabled for this workspace.",
        )

    try:
        claims = verify_capability_token(
            capability_token,
            expected_manifest_id=manifest_id,
            expected_tenant_id=tenant_id,
            expected_workspace_id=workspace_id,
            runtime_mode=runtime_mode,
        )
        _require_runtime_security_controls(claims)
        allowed_skills = set(_ordered_unique([str(item) for item in list(claims.get("allowed_skills") or [])]))
        if skill_id not in allowed_skills:
            raise ToolExecutionDeniedError(
                "skill_not_granted",
                f"The current capability token does not allow the {definition.label} skill.",
            )

        _require_action_class(claims, definition.action_class)
        _require_connector_scopes(claims, definition.connector_scopes)
        _require_broker_guard(
            claims=claims,
            tool_key=skill_id,
            action_class=definition.action_class,
            connector_scope=",".join(definition.connector_scopes),
            surface="skill",
        )

        # ── Stage 4B + Phase K: per-agent tool/connector enforcement ──
        # Prefer agent_id claim (Phase K) over agent_install_id (legacy)
        claim_agent_id = str(claims.get("agent_id") or "").strip()
        claim_install_id = str(claims.get("agent_install_id") or "").strip()
        resolved_agent_id = str(agent_id or claim_agent_id or claim_install_id or "").strip()

        # Phase K: instrument legacy tokens without agent_id claim
        if claim_install_id and not claim_agent_id:
            _LEGACY_TOKEN_AGENT_ID_COUNT += 1
            # Ledger: actor_id as "unattributed_legacy_token"
            try:
                from server_modules import activity_ledger_service as _als2
                import asyncio as _aio2
                async def _legacy_ledger():
                    await _als2.append_activity_event(
                        tenant_id="system",
                        workspace_id=workspace_id,
                        actor_type="platform",
                        actor_id="unattributed_legacy_token",
                        event_class="platform_integrity",
                        detail_level="audit_reference",
                        action="legacy_token_no_agent_id",
                        title="Capability token without agent_id claim",
                        summary=(
                            f"Token validated for '{skill_id}' carries agent_install_id "
                            f"but no agent_id claim. Legacy token — instrumenting."
                        ),
                        status="logged",
                        metadata={
                            "skill_id": skill_id,
                            "manifest_id": manifest_id,
                            "agent_install_id_hash": (
                                hashlib.sha256(claim_install_id.encode()).hexdigest()[:16]
                                if claim_install_id else "none"
                            ),
                        },
                    )
                try:
                    _aio2.get_running_loop()
                    _aio2.create_task(_legacy_ledger())
                except RuntimeError:
                    _aio2.run(_legacy_ledger())
            except Exception:
                pass

        if resolved_agent_id:
            _enforce_agent_tool_policy(
                resolved_agent_id,
                workspace_id,
                skill_id,
                definition.connector_scopes,
            )

        # ── Phase L: fleet tool role gate ──────────────────────────
        if resolved_agent_id:
            await _enforce_fleet_tool_role(
                resolved_agent_id,
                workspace_id,
                skill_id,
            )

        # ── Phase N: memory tool dispatch ──────────────────────────
        if skill_id in ("memory-read", "memory-write", "memory-list"):
            return await _dispatch_memory_tool(
                skill_id=skill_id,
                agent_install_id=resolved_agent_id,
                agent_id=str(agent_id or claim_agent_id or "").strip(),
                workspace_id=workspace_id,
                goal=goal,
            )

        if definition.executor is not None:
            _require_runtime_allowed(definition, runtime_mode)
            _require_approval_if_needed(claims, definition)
    except ToolExecutionDeniedError as exc:
        await agent_action_metering_service.record_blocked(
            **common_event,
            action_type=definition.action_class,
            connector_id=",".join(definition.connector_scopes) or None,
            risk_level="high" if definition.requires_approval else None,
            elevated=definition.requires_approval,
            policy_decision="blocked",
            error_code=exc.code,
            output_summary=exc.detail,
        )
        raise

    skill_egress_policy = egress_policy.build_egress_policy(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_mode=runtime_mode,
        agent_install_id=str(claims.get("agent_install_id") or "").strip() or None,
        tool_name=skill_id,
        connector_scopes=list(definition.connector_scopes),
        allowed_action_classes=[definition.action_class],
        metadata={
            "skill_id": skill_id,
            "manifest_id": manifest_id,
        },
    )
    with egress_policy.activate_egress_policy(skill_egress_policy, merge_with_current=True):
        try:
            await agent_action_metering_service.record_started(
                **common_event,
                action_type=definition.action_class,
                connector_id=",".join(definition.connector_scopes) or None,
                risk_level="high" if definition.requires_approval else None,
                elevated=definition.requires_approval,
                policy_decision="allowed",
            )
            result = await skill_registry.execute_skill(
                skill_id=skill_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                goal=goal,
                agent_label=agent_label,
                hard_context=hard_context,
                operational_policy=operational_policy,
            )
            await agent_action_metering_service.record_completed(
                **common_event,
                action_type=definition.action_class,
                connector_id=",".join(definition.connector_scopes) or None,
                risk_level="high" if definition.requires_approval else None,
                elevated=definition.requires_approval,
                policy_decision="allowed",
                output_summary=(result.get("reply") if isinstance(result, dict) else ""),
            )
            return result
        except egress_policy.EgressPolicyDeniedError as error:
            await agent_action_metering_service.record_blocked(
                **common_event,
                action_type=definition.action_class,
                connector_id=",".join(definition.connector_scopes) or None,
                risk_level="high" if definition.requires_approval else None,
                elevated=definition.requires_approval,
                policy_decision="blocked",
                error_code="egress_denied",
                output_summary=error.detail,
            )
            raise ToolExecutionDeniedError("egress_denied", error.detail) from error
        except Exception as error:
            await agent_action_metering_service.record_failed(
                **common_event,
                action_type=definition.action_class,
                connector_id=",".join(definition.connector_scopes) or None,
                risk_level="high" if definition.requires_approval else None,
                elevated=definition.requires_approval,
                policy_decision="allowed",
                error_code=type(error).__name__,
                output_summary=str(error),
            )
            raise


def authorize_connector_action(
    *,
    capability_token: str,
    manifest_id: str,
    tenant_id: str,
    workspace_id: str,
    runtime_mode: str,
    connector_scope: str,
    action_class: ToolActionClass,
    connector_class: str | None = None,
    elevated: bool | None = None,
) -> Dict[str, Any]:
    claims = verify_capability_token(
        capability_token,
        expected_manifest_id=manifest_id,
        expected_tenant_id=tenant_id,
        expected_workspace_id=workspace_id,
        runtime_mode=runtime_mode,
    )
    _require_runtime_security_controls(claims)
    normalized_action_class = _normalize_action_class(action_class)
    normalized_connector_class = _normalize_connector_class(connector_class) if connector_class is not None else None
    if normalized_connector_class is not None and normalized_action_class not in _CONNECTOR_CLASS_ALLOWED_ACTIONS[normalized_connector_class]:
        raise ToolExecutionDeniedError(
            "connector_action_not_supported_for_class",
            f"{normalized_action_class} actions are not allowed for {normalized_connector_class}.",
        )
    _require_action_class(claims, normalized_action_class)
    _require_connector_scopes(claims, (_normalize_token(connector_scope),))
    _require_broker_guard(
        claims=claims,
        tool_key=_normalize_token(connector_scope),
        action_class=normalized_action_class,
        connector_scope=_normalize_token(connector_scope),
        surface="connector",
    )
    if bool(elevated if elevated is not None else normalized_action_class != "read") and not bool(claims.get("approval_granted")):
        raise ToolExecutionDeniedError(
            "elevated_action_blocked",
            f"{connector_scope} connector actions are not available in the current safety mode. "
            "Switch to a less restrictive mode or rephrase the request.",
        )
    return claims


def summarize_capability_grant(claims: Dict[str, Any]) -> str:
    skill_count = len(list(claims.get("allowed_skills") or []))
    scope_count = len(list(claims.get("allowed_connector_scopes") or []))
    actions = ", ".join(list(claims.get("allowed_action_classes") or [])) or "none"
    issued_at = int(claims.get("issued_at") or 0)
    expires_at = int(claims.get("expires_at") or issued_at)
    ttl_seconds = max(expires_at - issued_at, 0)
    runtime_mode = str(claims.get("runtime_mode") or "hosted_secure")
    return (
        f"{skill_count} skill(s) · {scope_count} connector scope(s) · "
        f"actions {actions} · expires in {ttl_seconds}s · runtime {runtime_mode}"
    )
