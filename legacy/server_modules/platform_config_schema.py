from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from server_modules import config_defaults_service


def _token(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _token_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized: List[str] = []
    for item in value:
        token = _token(item)
        if token and token not in normalized:
            normalized.append(token)
    return normalized


class PlatformProviderAuthModeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    secret_required: bool = True


class PlatformModelEntryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: Optional[str] = None
    context_window_tokens: Optional[int] = None
    supports_tools: bool = False
    supports_reasoning: bool = False
    reasoning_levels: List[str] = Field(default_factory=list)
    local_self_hosted_compatible: bool = False
    capability_labels: List[str] = Field(default_factory=list)


class PlatformModelPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = None
    allowed_surfaces: List[str] = Field(default_factory=list)
    allowed_payers: List[str] = Field(default_factory=list)
    platform_paid_allowed: Optional[bool] = None
    pricing_known: Optional[bool] = None
    pricing_source: Optional[str] = None
    pricing_version: Optional[str] = None
    context_window_tokens: Optional[int] = None
    supports_tools: Optional[bool] = None
    supports_vision: Optional[bool] = None
    supports_reasoning: Optional[bool] = None
    fallback_allowed: Optional[bool] = None


class PlatformProviderGovernanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    privacy_posture: Optional[str] = None
    jurisdiction: Optional[str] = None
    residency: Optional[str] = None
    enterprise_risk_note: Optional[str] = None
    capability_labels: List[str] = Field(default_factory=list)
    local_self_hosted_compatible: bool = False


class PlatformProviderEntryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    auth: List[str] = Field(default_factory=list)
    auth_modes: List[PlatformProviderAuthModeConfig] = Field(default_factory=list)
    default_auth_mode: Optional[str] = None
    default_model: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    alias_for: Optional[str] = None
    base_url: Optional[str] = None
    hidden: bool = False
    provider_scopes: List[str] = Field(default_factory=list)


class PlatformDefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_target: str = Field(default_factory=config_defaults_service.default_deployed_agent_runtime_target)
    billing_plan: str = Field(default_factory=config_defaults_service.default_deployed_agent_billing_plan)
    deployment_state: str = Field(default_factory=config_defaults_service.default_deployed_agent_deployment_state)
    workspace_machine_enrollment_scope: str = Field(
        default_factory=config_defaults_service.default_workspace_machine_enrollment_scope
    )


class PlatformLiveDeploymentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_channels: List[str] = Field(default_factory=lambda: sorted(config_defaults_service.live_deployment_channels()))


class PlatformProviderCatalogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default_factory=config_defaults_service.config_schema_version)
    defaults: PlatformDefaultsConfig = Field(default_factory=PlatformDefaultsConfig)
    live_deployment_policy: PlatformLiveDeploymentPolicy = Field(default_factory=PlatformLiveDeploymentPolicy)
    providers: Dict[str, PlatformProviderEntryConfig] = Field(default_factory=dict)
    governance: Dict[str, PlatformProviderGovernanceConfig] = Field(default_factory=dict)
    models: Dict[str, Dict[str, PlatformModelEntryConfig]] = Field(default_factory=dict)
    model_policies: Dict[str, Dict[str, PlatformModelPolicyConfig]] = Field(default_factory=dict)

    def provider(self, provider_id: Any) -> Optional[PlatformProviderEntryConfig]:
        token = str(provider_id or "").strip().lower()
        return self.providers.get(token)

    def provider_governance(self, provider_id: Any) -> Optional[PlatformProviderGovernanceConfig]:
        token = str(provider_id or "").strip().lower()
        return self.governance.get(token)

    def provider_models(self, provider_id: Any) -> List[PlatformModelEntryConfig]:
        token = str(provider_id or "").strip().lower()
        payload = self.models.get(token) or {}
        return list(payload.values())

    def provider_model_policy(self, provider_id: Any, model_id: Any) -> Optional[PlatformModelPolicyConfig]:
        provider_token = str(provider_id or "").strip().lower()
        model_token = str(model_id or "").strip()
        if not provider_token or not model_token:
            return None
        return (self.model_policies.get(provider_token) or {}).get(model_token)


def load_model_catalog_policy_overrides() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("EMPYRALIS_MODEL_CATALOG_POLICY_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def build_platform_provider_catalog_config(
    *,
    provider_catalog: Dict[str, Dict[str, Any]],
    governance_catalog: Dict[str, Dict[str, Any]],
    model_catalog: Dict[str, Dict[str, Dict[str, Any]]],
    model_policy_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> PlatformProviderCatalogConfig:
    providers: Dict[str, PlatformProviderEntryConfig] = {}
    governance: Dict[str, PlatformProviderGovernanceConfig] = {}
    models: Dict[str, Dict[str, PlatformModelEntryConfig]] = {}
    model_policies: Dict[str, Dict[str, PlatformModelPolicyConfig]] = {}

    for raw_provider_id, raw_entry in dict(provider_catalog or {}).items():
        provider_id = str(raw_provider_id or "").strip().lower()
        if not provider_id or not isinstance(raw_entry, dict):
            continue
        providers[provider_id] = PlatformProviderEntryConfig.model_validate(
            {
                "id": provider_id,
                "label": str(raw_entry.get("label") or provider_id),
                "auth": _token_list(raw_entry.get("auth")),
                "auth_modes": [
                    {
                        "id": str((item or {}).get("id") or "").strip(),
                        "label": str((item or {}).get("label") or "").strip(),
                        "secret_required": bool((item or {}).get("secret_required", True)),
                    }
                    for item in list(raw_entry.get("auth_modes") or [])
                    if isinstance(item, dict) and _token(item.get("id")) and _token(item.get("label"))
                ],
                "default_auth_mode": _token(raw_entry.get("default_auth_mode")),
                "default_model": _token(raw_entry.get("default_model")),
                "models": _token_list(raw_entry.get("models")),
                "note": _token(raw_entry.get("note")),
                "alias_for": _token(raw_entry.get("alias_for")),
                "base_url": _token(raw_entry.get("base_url")),
                "hidden": bool(raw_entry.get("hidden")),
                "provider_scopes": _token_list(raw_entry.get("provider_scopes")),
            }
        )

    for raw_provider_id, raw_entry in dict(governance_catalog or {}).items():
        provider_id = str(raw_provider_id or "").strip().lower()
        if not provider_id or not isinstance(raw_entry, dict):
            continue
        governance[provider_id] = PlatformProviderGovernanceConfig.model_validate(
            {
                "privacy_posture": _token(raw_entry.get("privacy_posture")),
                "jurisdiction": _token(raw_entry.get("jurisdiction")),
                "residency": _token(raw_entry.get("residency")),
                "enterprise_risk_note": _token(raw_entry.get("enterprise_risk_note")),
                "capability_labels": _token_list(raw_entry.get("capability_labels")),
                "local_self_hosted_compatible": bool(raw_entry.get("local_self_hosted_compatible")),
            }
        )

    for raw_provider_id, raw_models in dict(model_catalog or {}).items():
        provider_id = str(raw_provider_id or "").strip().lower()
        if not provider_id or not isinstance(raw_models, dict):
            continue
        provider_models: Dict[str, PlatformModelEntryConfig] = {}
        for raw_model_id, raw_entry in raw_models.items():
            model_id = str(raw_model_id or "").strip()
            if not model_id or not isinstance(raw_entry, dict):
                continue
            provider_models[model_id] = PlatformModelEntryConfig.model_validate(
                {
                    "id": model_id,
                    "label": _token(raw_entry.get("label")),
                    "context_window_tokens": raw_entry.get("context_window_tokens"),
                    "supports_tools": bool(raw_entry.get("supports_tools")),
                    "supports_reasoning": bool(raw_entry.get("supports_reasoning")),
                    "reasoning_levels": _token_list(raw_entry.get("reasoning_levels")),
                    "local_self_hosted_compatible": bool(raw_entry.get("local_self_hosted_compatible")),
                    "capability_labels": _token_list(raw_entry.get("capability_labels")),
                }
            )
        models[provider_id] = provider_models

    for raw_provider_id, raw_models in dict(model_policy_overrides or {}).items():
        provider_id = str(raw_provider_id or "").strip().lower()
        if not provider_id or not isinstance(raw_models, dict):
            continue
        provider_policies: Dict[str, PlatformModelPolicyConfig] = {}
        for raw_model_id, raw_policy in raw_models.items():
            model_id = str(raw_model_id or "").strip()
            if not model_id or not isinstance(raw_policy, dict):
                continue
            provider_policies[model_id] = PlatformModelPolicyConfig.model_validate(
                {
                    "enabled": raw_policy.get("enabled"),
                    "allowed_surfaces": _token_list(raw_policy.get("allowed_surfaces")),
                    "allowed_payers": _token_list(raw_policy.get("allowed_payers")),
                    "platform_paid_allowed": raw_policy.get("platform_paid_allowed"),
                    "pricing_known": raw_policy.get("pricing_known"),
                    "pricing_source": _token(raw_policy.get("pricing_source")),
                    "pricing_version": _token(raw_policy.get("pricing_version")),
                    "context_window_tokens": raw_policy.get("context_window_tokens"),
                    "supports_tools": raw_policy.get("supports_tools"),
                    "supports_vision": raw_policy.get("supports_vision"),
                    "supports_reasoning": raw_policy.get("supports_reasoning"),
                    "fallback_allowed": raw_policy.get("fallback_allowed"),
                }
            )
        if provider_policies:
            model_policies[provider_id] = provider_policies

    return PlatformProviderCatalogConfig(
        providers=providers,
        governance=governance,
        models=models,
        model_policies=model_policies,
    )
