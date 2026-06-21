from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from server_modules import config_defaults_service


def _normalize_tokens(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized: List[str] = []
    for item in value:
        token = str(item or "").strip()
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def _normalize_scope(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"workspace", "tenant", "global"}:
        return token
    return config_defaults_service.default_workspace_machine_enrollment_scope()


def _normalize_hosted_sage_ai_policy(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"disabled", "owner_opt_in", "enabled_with_cap"}:
        return token
    return config_defaults_service.default_hosted_sage_ai_policy()


def _coerce_non_negative_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if parsed < 0:
        return 0.0
    return float(parsed)


class WorkspaceTokenPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: List[str] = Field(default_factory=list)
    deny: List[str] = Field(default_factory=list)


class WorkspaceMachinePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    machine_enrollment_scope: str = Field(
        default_factory=config_defaults_service.default_workspace_machine_enrollment_scope
    )
    trusted_owner_machine_ids: List[str] = Field(default_factory=list)


class WorkspacePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    capabilities: WorkspaceTokenPolicyConfig = Field(default_factory=WorkspaceTokenPolicyConfig)
    dangerous_action_classes: WorkspaceTokenPolicyConfig = Field(default_factory=WorkspaceTokenPolicyConfig)
    connectors: WorkspaceTokenPolicyConfig = Field(default_factory=WorkspaceTokenPolicyConfig)
    machine_policy: WorkspaceMachinePolicyConfig = Field(default_factory=WorkspaceMachinePolicyConfig)
    updated_at: Optional[int] = None


class WorkspaceConfigEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default_factory=config_defaults_service.config_schema_version)
    payload: WorkspacePolicyConfig


class WorkspaceAdminDefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_target: str = Field(default_factory=config_defaults_service.default_deployed_agent_runtime_target)
    billing_plan: str = Field(default_factory=config_defaults_service.default_deployed_agent_billing_plan)
    privacy_policy_url: Optional[str] = None
    public_start_cta_label: Optional[str] = None
    public_start_cta_url: Optional[str] = None
    context_budget_preset: str = Field(default_factory=config_defaults_service.default_context_budget_preset)
    retention_preset: str = Field(default_factory=config_defaults_service.default_retention_preset)
    health_safety_enabled: bool = False
    hosted_sage_ai_policy: str = Field(default_factory=config_defaults_service.default_hosted_sage_ai_policy)
    hosted_sage_ai_monthly_cap_usd: float = Field(
        default_factory=config_defaults_service.default_hosted_sage_ai_monthly_cap_usd
    )
    allowed_live_channels: List[str] = Field(
        default_factory=lambda: sorted(config_defaults_service.live_deployment_channels())
    )
    sage_ai_provider: str = ""


class WorkspaceAdminDefaultsEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default_factory=config_defaults_service.config_schema_version)
    payload: WorkspaceAdminDefaultsConfig


def workspace_policy_config_from_legacy(
    policy: Optional[Dict[str, Any]],
    *,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> WorkspacePolicyConfig:
    payload = dict(policy or {})
    capabilities = dict(payload.get("capabilities") or {})
    dangerous = dict(payload.get("dangerous_action_classes") or {})
    connectors = dict(payload.get("connectors") or {})
    return WorkspacePolicyConfig.model_validate(
        {
            "workspace_id": str(payload.get("workspace_id") or workspace_id or "").strip() or None,
            "tenant_id": str(payload.get("tenant_id") or tenant_id or "").strip() or None,
            "capabilities": {
                "allow": _normalize_tokens(capabilities.get("allow")),
                "deny": _normalize_tokens(capabilities.get("deny")),
            },
            "dangerous_action_classes": {
                "allow": _normalize_tokens(dangerous.get("allow")),
                "deny": _normalize_tokens(dangerous.get("deny")),
            },
            "connectors": {
                "allow": _normalize_tokens(connectors.get("allow")),
                "deny": _normalize_tokens(connectors.get("deny")),
            },
            "machine_policy": {
                "machine_enrollment_scope": _normalize_scope(
                    payload.get("machine_enrollment_scope")
                ),
                "trusted_owner_machine_ids": _normalize_tokens(
                    payload.get("trusted_owner_machine_ids")
                ),
            },
            "updated_at": payload.get("updated_at"),
        }
    )


def workspace_policy_to_legacy_payload(config: WorkspacePolicyConfig) -> Dict[str, Any]:
    return {
        "workspace_id": config.workspace_id,
        "tenant_id": config.tenant_id,
        "capabilities": config.capabilities.model_dump(),
        "dangerous_action_classes": config.dangerous_action_classes.model_dump(),
        "connectors": config.connectors.model_dump(),
        "machine_enrollment_scope": config.machine_policy.machine_enrollment_scope,
        "trusted_owner_machine_ids": list(config.machine_policy.trusted_owner_machine_ids),
        "updated_at": config.updated_at,
    }


def workspace_policy_envelope(config: WorkspacePolicyConfig) -> Dict[str, Any]:
    return WorkspaceConfigEnvelope(payload=config).model_dump(exclude_none=True)


def workspace_admin_defaults_from_metadata(
    metadata: Optional[Dict[str, Any]],
) -> WorkspaceAdminDefaultsConfig:
    payload = dict(metadata or {})
    raw_defaults = payload.get("admin_defaults")
    if isinstance(raw_defaults, dict) and isinstance(raw_defaults.get("payload"), dict):
        raw_defaults = raw_defaults.get("payload")
    raw_defaults = dict(raw_defaults or {}) if isinstance(raw_defaults, dict) else {}
    return WorkspaceAdminDefaultsConfig.model_validate(
        {
            "runtime_target": str(
                raw_defaults.get("runtime_target")
                or config_defaults_service.default_deployed_agent_runtime_target()
            ).strip()
            or config_defaults_service.default_deployed_agent_runtime_target(),
            "billing_plan": str(
                raw_defaults.get("billing_plan")
                or config_defaults_service.default_deployed_agent_billing_plan()
            ).strip()
            or config_defaults_service.default_deployed_agent_billing_plan(),
            "privacy_policy_url": str(raw_defaults.get("privacy_policy_url") or "").strip() or None,
            "public_start_cta_label": str(raw_defaults.get("public_start_cta_label") or "").strip() or None,
            "public_start_cta_url": str(raw_defaults.get("public_start_cta_url") or "").strip() or None,
            "context_budget_preset": str(
                raw_defaults.get("context_budget_preset")
                or config_defaults_service.default_context_budget_preset()
            ).strip()
            or config_defaults_service.default_context_budget_preset(),
            "retention_preset": str(
                raw_defaults.get("retention_preset")
                or config_defaults_service.default_retention_preset()
            ).strip()
            or config_defaults_service.default_retention_preset(),
            "health_safety_enabled": bool(raw_defaults.get("health_safety_enabled")),
            "hosted_sage_ai_policy": _normalize_hosted_sage_ai_policy(
                raw_defaults.get("hosted_sage_ai_policy")
            ),
            "hosted_sage_ai_monthly_cap_usd": _coerce_non_negative_float(
                raw_defaults.get("hosted_sage_ai_monthly_cap_usd"),
                config_defaults_service.default_hosted_sage_ai_monthly_cap_usd(),
            ),
            "allowed_live_channels": _normalize_tokens(raw_defaults.get("allowed_live_channels"))
            or list(sorted(config_defaults_service.live_deployment_channels())),
            "sage_ai_provider": str(raw_defaults.get("sage_ai_provider") or "").strip().lower(),
        }
    )


def workspace_admin_defaults_envelope(config: WorkspaceAdminDefaultsConfig) -> Dict[str, Any]:
    return WorkspaceAdminDefaultsEnvelope(payload=config).model_dump(exclude_none=True)
