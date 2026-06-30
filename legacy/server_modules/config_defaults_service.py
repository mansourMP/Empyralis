from __future__ import annotations

from typing import FrozenSet

from server_modules import billing_credit_config


CONFIG_SCHEMA_VERSION = 1
DEFAULT_DEPLOYED_AGENT_RUNTIME_TARGET = "cloud"
DEFAULT_DEPLOYED_AGENT_BILLING_PLAN = "free"
DEFAULT_DEPLOYED_AGENT_DEPLOYMENT_STATE = "draft"
DEFAULT_WORKSPACE_MACHINE_ENROLLMENT_SCOPE = "workspace"
DEFAULT_LIVE_DEPLOYMENT_CHANNELS: FrozenSet[str] = frozenset({"telegram"})
DEFAULT_PUBLIC_START_CTA_LABEL = "Continue on Empyralist"
DEFAULT_CONTEXT_BUDGET_PRESET = "standard"
DEFAULT_RETENTION_PRESET = "standard"
DEFAULT_ESCALATION_PRESET = "standard"
DEFAULT_HANDOFF_MODE = "notify_owner"
DEFAULT_HEALTH_SAFETY_ASSISTANT_NAME = "HealthGuide"
DEFAULT_HOSTED_SAGE_AI_POLICY = "enabled_with_cap"


DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD = billing_credit_config.DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD


def config_schema_version() -> int:
    return CONFIG_SCHEMA_VERSION


def default_deployed_agent_runtime_target() -> str:
    return DEFAULT_DEPLOYED_AGENT_RUNTIME_TARGET


def default_deployed_agent_billing_plan() -> str:
    return DEFAULT_DEPLOYED_AGENT_BILLING_PLAN


def default_deployed_agent_deployment_state() -> str:
    return DEFAULT_DEPLOYED_AGENT_DEPLOYMENT_STATE


def default_workspace_machine_enrollment_scope() -> str:
    return DEFAULT_WORKSPACE_MACHINE_ENROLLMENT_SCOPE


def live_deployment_channels() -> FrozenSet[str]:
    return DEFAULT_LIVE_DEPLOYMENT_CHANNELS


def default_public_start_cta_label() -> str:
    return DEFAULT_PUBLIC_START_CTA_LABEL


def default_context_budget_preset() -> str:
    return DEFAULT_CONTEXT_BUDGET_PRESET


def default_retention_preset() -> str:
    return DEFAULT_RETENTION_PRESET


def default_deployed_agent_escalation_preset() -> str:
    return DEFAULT_ESCALATION_PRESET


def default_deployed_agent_handoff_mode() -> str:
    return DEFAULT_HANDOFF_MODE


def default_health_safety_assistant_name() -> str:
    return DEFAULT_HEALTH_SAFETY_ASSISTANT_NAME


def default_hosted_sage_ai_policy() -> str:
    return DEFAULT_HOSTED_SAGE_AI_POLICY


def default_hosted_sage_ai_monthly_cap_usd() -> float:
    return float(DEFAULT_HOSTED_SAGE_AI_MONTHLY_CAP_USD)
