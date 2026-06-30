from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from server_modules.runtime_models import ConnectorUpsertRequest, RunStartRequest


class WorkflowCreate(BaseModel):
    app_id: str
    package_id: Optional[str] = None
    release_channel: Optional[str] = None
    install_source: Optional[str] = None


class WorkflowUpdate(WorkflowCreate):
    pass


class WorkflowDelete(BaseModel):
    app_id: str


class AppCaptainBridgeRequest(BaseModel):
    workspace_id: str
    app_id: str
    bridge_type: str
    context_envelope: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    request_text: Optional[str] = None


class AppSpecialistBridgeRequest(BaseModel):
    workspace_id: str
    app_id: str
    bridge_type: str
    target_install_id: Optional[str] = None
    target_capability: Optional[str] = None
    context_envelope: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    request_text: Optional[str] = None


class AppRuntimeBridgeRequest(BaseModel):
    workspace_id: str
    app_id: str
    bridge_type: str
    connector_id: Optional[str] = None
    workflow_id: Optional[str] = None
    route_key: Optional[str] = None
    context_envelope: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class SageAppBridgeRequest(BaseModel):
    workspace_id: str
    app_id: str
    bridge_type: str
    target_app_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    handoff_payload: Optional[Dict[str, Any]] = None


class SageServiceProfileUpdateRequest(BaseModel):
    workspace_id: str
    profile: Dict[str, Any]


class SageServiceEntryCreateRequest(BaseModel):
    workspace_id: str
    entry: Dict[str, Any]


class SageServiceEntryUpdateRequest(BaseModel):
    workspace_id: str
    entry: Dict[str, Any]


class SageServiceEntryPinRequest(BaseModel):
    workspace_id: str
    pinned: bool = True


class SageMemoryEntryCreateRequest(BaseModel):
    workspace_id: str
    category: str
    title: str
    content: str
    pinned: bool = False


class SageMemoryEntryUpdateRequest(SageMemoryEntryCreateRequest):
    pass


class SageMemoryEntryPinRequest(BaseModel):
    workspace_id: str
    pinned: bool = True


class SageMemoryWipeRequest(BaseModel):
    workspace_id: str
    confirm: str


class SageContextFileUpdateRequest(BaseModel):
    workspace_id: str
    content: str


class SageProfileUpdateRequest(BaseModel):
    workspace_id: str
    user_name: Optional[str] = None
    identity_summary: Optional[str] = None
    communication_style: Optional[str] = None
    recurring_responsibility: Optional[str] = None
    standing_rules: Optional[list[str]] = None
    standing_rules_text: Optional[str] = None


class SageProfileBootstrapAnswerRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=120)
    answer: str = Field(min_length=1, max_length=8_000)


class SageChatAttachment(BaseModel):
    file_id: str
    filename: str
    safe_filename: str
    content_type: str
    size: int
    url: str


class SageChatRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=32_000)
    surface: str = Field(default="chat", max_length=80)
    mode: Literal["owner_sage"] = "owner_sage"
    attachments: list[SageChatAttachment] = Field(default_factory=list)


class SageVoiceTaskRequest(BaseModel):
    workspace_id: str
    transcript: str
    source_channel: str = "mobile_voice"
    source_message_id: Optional[str] = None


class SageApprovalResolveRequest(BaseModel):
    workspace_id: str
    approval_token: str


class AgentCreate(BaseModel):
    path: Optional[str] = None
    workspace_id: Optional[str] = None
    content: Optional[str] = None
    overwrite: bool = False


class AgentUpdate(AgentCreate):
    action: Optional[str] = None


class RunCreate(RunStartRequest):
    pass


class ConnectorCreate(ConnectorUpsertRequest):
    pass


class AuthLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    acquisition_token: Optional[str] = Field(default=None, max_length=512)
    channel: Optional[str] = Field(default=None, max_length=80)
    device_id: Optional[str] = Field(default=None, max_length=180)
    device_name: Optional[str] = Field(default=None, max_length=120)
    device_platform: Optional[str] = Field(default=None, max_length=120)
    workspace_id: Optional[str] = Field(default=None, max_length=120)
    session_ttl_seconds: Optional[int] = None


class AuthRegisterRequest(AuthLoginRequest):
    name: Optional[str] = Field(default=None, max_length=120)
    pilot_invite_code: Optional[str] = Field(default=None, max_length=120)


class WorkspaceFileWriteRequest(BaseModel):
    path: str
    content: Optional[str] = None
    overwrite: bool = False
    workspace_id: Optional[str] = None


class WorkspaceFileDeleteRequest(BaseModel):
    path: str
    workspace_id: Optional[str] = None


class DeviceExecuteRequest(BaseModel):
    action: str
    workspace_id: Optional[str] = None


class ConnectorDocumentCreateRequest(BaseModel):
    title: Optional[str] = None


class ConnectorSpreadsheetCreateRequest(BaseModel):
    title: Optional[str] = None


class GenericObjectBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    def as_dict(self) -> Dict[str, Any]:
        if hasattr(self, "model_dump"):
            return dict(self.model_dump())
        return dict(self.dict())


class DeployedAgentAdminDashboardMessage(BaseModel):
    id: Optional[str] = None
    role: str
    content: str = ""
    created_at: Optional[str] = None
    channel: Optional[str] = None


class DeployedAgentAdminDashboardUserRow(BaseModel):
    external_user_id: Optional[str] = None
    last_message_at: Optional[str] = None
    total_message_count: int = 0
    memory_entry_count: int = 0
    last_5_messages: list[DeployedAgentAdminDashboardMessage] = Field(default_factory=list)


class DeployedAgentAdminDashboardQuestion(BaseModel):
    question: str
    count: int = 0


class DeployedAgentCustomerEntry(BaseModel):
    entry_url: Optional[str] = None
    cta_label: Optional[str] = None
    telegram_deep_link: Optional[str] = None
    bot_username: Optional[str] = None
    qr_image_url: Optional[str] = None
    qr_target: Optional[str] = None


class DeployedAgentAdminDashboardResponse(BaseModel):
    deployed_agent_id: str
    total_users: int = 0
    messages_today: int = 0
    messages_this_calendar_month: int = 0
    orders_today: int = 0
    revenue_today_usd: float = 0.0
    users_at_limit_today: int = 0
    upgrade_clicks_this_month: int = 0
    common_questions: list[DeployedAgentAdminDashboardQuestion] = Field(default_factory=list)
    customer_entry: Optional[DeployedAgentCustomerEntry] = None
    specialist_profile: Dict[str, Any] = Field(default_factory=dict)
    user_rows: list[DeployedAgentAdminDashboardUserRow] = Field(default_factory=list)
    limit: int = 50
    offset: int = 0
    has_more: bool = False
    next_cursor_last_message_at: Optional[str] = None
    next_cursor_external_user_id: Optional[str] = None


class MarketplaceAgentCard(BaseModel):
    id: str
    name: str
    description: str = ""
    category: Optional[str] = None
    quality_stars: Optional[int] = None
    cost_tier: Optional[str] = None
    telegram_bot_username: Optional[str] = None


class MarketplaceAgentListResponse(BaseModel):
    items: list[MarketplaceAgentCard] = Field(default_factory=list)
    limit: int = 100
    offset: int = 0
    has_more: bool = False


class MarketplaceUpgradeClickRequest(BaseModel):
    channel_attribution: str
    source: str
    agent_id: Optional[str] = None


class MarketplaceUpgradeClickResponse(BaseModel):
    ok: bool = True
    recorded: bool = False
    duplicate: bool = False
    deployed_agent_id: Optional[str] = None
    clicked_at: Optional[str] = None


class DeployedAgentTestTurnRequest(BaseModel):
    workspace_id: str
    message: str
    channel: str = "test"
    runtime_mode: str = "text_agent"
    customer_profile: Optional[Dict[str, Any]] = None
    recent_messages: list[Dict[str, Any]] = Field(default_factory=list)


class DeployedAgentTestTurnResponse(BaseModel):
    reply: str = ""
    policy_decisions: list[dict] = Field(default_factory=list)
    tools_considered: list[dict] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    memory_context: Dict[str, Any] = Field(default_factory=dict)
    prompt_context: Dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = False
    audit_events: list[dict] = Field(default_factory=list)
    trace_id: str = ""
    transparency_events: list[dict] = Field(default_factory=list)
    model_provider: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
