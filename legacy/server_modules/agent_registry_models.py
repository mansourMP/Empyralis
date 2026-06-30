from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AgentRegistryBase(DeclarativeBase):
    pass


class _WorkspaceScoped:
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)


class RuntimeProfileModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "runtime_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    runtime_class: Mapped[str] = mapped_column(Text, nullable=False, default="cloud_worker")
    placement_mode: Mapped[str] = mapped_column(Text, nullable=False, default="auto")
    runtime_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    machine_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_execution_target: Mapped[str] = mapped_column(Text, nullable=False, default="auto")
    supported_capabilities: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    root_folder_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    allowed_connector_scopes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentDefinitionModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "agent_definitions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_kind: Mapped[str] = mapped_column(Text, nullable=False, default="specialist")
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="workspace")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    icon: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_version_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_version_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_workflow_definition_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workflow_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    definition_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentDefinitionVersionModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "agent_definition_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_definition_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    compiled_workflow_version_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    capability_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    memory_scope_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    policy_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    placement_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    template_inputs_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WorkspaceAgentInstallModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "workspace_agent_installs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_definition_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_definition_version_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("agent_definition_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    installed_by_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    install_scope: Mapped[str] = mapped_column(Text, nullable=False, default="workspace")
    owner_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("agent_threads.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    runtime_profile_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("runtime_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    compiled_workflow_version_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    root_folder_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_toggles: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    folder_grants: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    connector_bindings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    memory_scope_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    policy_context_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    install_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WorkspaceInventoryItemModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "workspace_inventory_items"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    manufacturer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    make: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    year_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    year_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quantity_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(length=3), nullable=False, default="USD")


class PersonalContextEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "personal_context_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_app: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    seen_by_sage_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentSchedulerWakeRequestModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "agent_scheduler_wake_requests"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    master_agent_install_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workspace_agent_installs.id", ondelete="SET NULL"),
        nullable=True,
    )
    trigger_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    denial_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    request_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentChannelEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "agent_channel_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    channel_key: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_key: Mapped[str] = mapped_column(Text, nullable=False)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("agent_threads.id", ondelete="SET NULL"), nullable=True)
    responder_install_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workspace_agent_installs.id", ondelete="SET NULL"),
        nullable=True,
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parent_event_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="logged")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentSecretAccessEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "agent_secret_access_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    secret_kind: Mapped[str] = mapped_column(Text, nullable=False)
    credential_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connector_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    allowed_fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    access_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="allowed")
    denial_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentEgressEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "agent_egress_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_install_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workspace_agent_installs.id", ondelete="SET NULL"),
        nullable=True,
    )
    run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    runtime_mode: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connector_scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action_class: Mapped[str] = mapped_column(Text, nullable=False, default="read")
    request_method: Mapped[str] = mapped_column(Text, nullable=False, default="GET")
    request_url: Mapped[str] = mapped_column(Text, nullable=False)
    request_host: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    egress_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="allowed")
    denial_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentChannelExecutionLeaseModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "agent_channel_execution_leases"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    responder_install_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workspace_agent_installs.id", ondelete="CASCADE"),
        nullable=True,
    )
    thread_id: Mapped[str] = mapped_column(Text, ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    channel_key: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_key: Mapped[str] = mapped_column(Text, nullable=False)
    lease_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SecurityControlStateModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "security_control_states"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    control_kind: Mapped[str] = mapped_column(Text, nullable=False, default="kill_switch")
    scope_key: Mapped[str] = mapped_column(Text, nullable=False)
    agent_install_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workspace_agent_installs.id", ondelete="CASCADE"),
        nullable=True,
    )
    channel_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    endpoint_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connector_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credential_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SecurityControlEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "security_control_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    control_state_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("security_control_states.id", ondelete="SET NULL"),
        nullable=True,
    )
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    control_kind: Mapped[str] = mapped_column(Text, nullable=False, default="kill_switch")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    scope_key: Mapped[str] = mapped_column(Text, nullable=False)
    agent_install_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workspace_agent_installs.id", ondelete="SET NULL"),
        nullable=True,
    )
    channel_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    endpoint_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connector_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credential_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ActivityLedgerEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "activity_ledger_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    install_id: Mapped[Optional[str]] = mapped_column(
        Text,
        ForeignKey("workspace_agent_installs.id", ondelete="SET NULL"),
        nullable=True,
    )
    app_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("agent_threads.id", ondelete="SET NULL"), nullable=True)
    session_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    direction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_class: Mapped[str] = mapped_column(Text, nullable=False)
    detail_level: Mapped[str] = mapped_column(Text, nullable=False, default="feed_summary")
    action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="logged")
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    artifacts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ledger_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CreditLedgerEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "credit_ledger_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    source_surface: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payer: Mapped[str] = mapped_column(Text, nullable=False)
    credit_type: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    runtime_target: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    app_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    platform_cost_usd: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    provider_reported_cost: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)
    provider_reported_currency: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credits_debited: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    estimation_mode: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_table: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_event_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ledger_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AgentActionEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "agent_action_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    source_surface: Mapped[str] = mapped_column(Text, nullable=False, default="")
    action_domain: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    action_name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_kind: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connector_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skill_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mcp_server_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mcp_tool_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    runtime_target: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capability_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approval_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approval_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    policy_decision: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payer: Mapped[str] = mapped_column(Text, nullable=False)
    billing_mode: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    credit_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credits_debited: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    platform_cost_usd: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    usage_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    trace_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    app_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_table: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_event_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class KnowledgeSourceModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False, default="file")
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="indexed")
    source_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class KnowledgeChunkModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    citation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class KnowledgeEmbeddingModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "knowledge_embeddings"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class KnowledgeRetrievalEventModel(_WorkspaceScoped, AgentRegistryBase):
    __tablename__ = "knowledge_retrieval_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    source_surface: Mapped[str] = mapped_column(Text, nullable=False, default="")
    query_hash: Mapped[str] = mapped_column(Text, nullable=False)
    query_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    app_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    retrieved_chunk_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence_score: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="logged")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retrieval_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
