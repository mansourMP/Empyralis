'use client';

export type WorkstationClientScope = {
  workspaceId: string;
  tenantId: string;
  kernelKey: string;
};

type CreditUsageQueryOptions = {
  limit?: number;
  surface?: string | null;
  creditType?: string | null;
  payer?: string | null;
  agentId?: string | null;
  appId?: string | null;
  threadId?: string | null;
  runId?: string | null;
};

export type WorkstationSessionActor = {
  type: string;
  id: string;
  display_name?: string | null;
};

export type WorkstationSessionRecord = {
  session_id: string;
  workspace_id?: string;
  tenant_id?: string;
  channel?: string;
  actor?: Record<string, unknown>;
  created_at?: string | null;
  expires_at?: string | null;
  metadata?: Record<string, unknown>;
  status?: string;
};

export type SageTaskRouteMode =
  | 'chat_only'
  | 'connector_api'
  | 'cloud_browser'
  | 'cloud_computer'
  | 'gateway_required';

export type SageTaskRouteDecision = {
  mode?: SageTaskRouteMode | string;
  user_label?: string;
  reason?: string;
  required_connections?: string[];
  fallback_modes?: Array<SageTaskRouteMode | string>;
  approval_required?: boolean;
};

export type WorkstationTurnResponse = {
  status?: string;
  reply?: string;
  error?: string;
  run_id?: string | null;
  thread_id?: string | null;
  session_id?: string | null;
  approvals?: Record<string, unknown>[];
  interventions?: Record<string, unknown>[];
  route_decision?: SageTaskRouteDecision | null;
  metadata?: Record<string, unknown>;
};

export type SageChatAttachment = {
  file_id: string;
  filename: string;
  safe_filename: string;
  content_type: string;
  size: number;
  url: string;
};

export type WorkstationTurnStreamEvent = {
  id?: string | null;
  event: string;
  payload: Record<string, unknown>;
};

export type WorkstationTurnStreamAbortHandle = {
  abort: () => void;
  signal: AbortSignal;
};

export type WorkspaceAiRouteKind =
  | 'empyralis_managed'
  | 'user_api_key'
  | 'local_model'
  | 'enterprise_gateway';

export type WorkspaceAiRouteRecord = Record<string, unknown> & {
  id?: string | null;
  kind?: WorkspaceAiRouteKind | string | null;
  label?: string | null;
  publicLabel?: string | null;
  description?: string | null;
  providerId?: string | null;
  modelId?: string | null;
  modelPreset?: string | null;
  billingSource?: string | null;
  runtimeTarget?: string | null;
  enabled?: boolean | null;
  status?: string | null;
};

export type WorkspaceAiRoutePayload = Record<string, unknown> & {
  workspaceId?: string | null;
  workspaceDefault?: WorkspaceAiRouteRecord | null;
  availableRoutes?: WorkspaceAiRouteRecord[] | null;
  usedBy?: Record<string, unknown>[] | null;
  budgets?: Record<string, unknown> | null;
};

export type ConnectionCatalogItem = Record<string, unknown> & {
  id?: string | null;
  display_name?: string | null;
  lane?: string | null;
  surface?: string[] | null;
  setup_kind?: string | null;
  launch_status?: string | null;
  description?: string | null;
  requires_gateway?: boolean | null;
  supports_inbound?: boolean | null;
  supports_outbound?: boolean | null;
  media_support?: Record<string, boolean> | null;
  approval_policy?: string | null;
  health_check?: string | null;
  test_action?: string | null;
  connector_ids?: string[] | null;
  connector_id?: string | null;
  account_provider?: string | null;
  vault_provider?: string | null;
  runtime_provider?: string | null;
  auth_required_fields?: string[] | null;
  setup_available?: boolean | null;
  runtime_usable?: boolean | null;
  launchable?: boolean | null;
  launch_blockers?: string[] | null;
  proof_blockers?: string[] | null;
  test_runner?: string | null;
};

export type ConnectionStatusItem = ConnectionCatalogItem & {
  connected?: boolean | null;
  configured?: boolean | null;
  display_state?: 'connected' | 'connectable' | 'requires_setup' | 'requires_agent_computer' | 'unavailable' | string | null;
  test_status?: string | null;
  health_status?: string | null;
  selected_gateway_id?: string | null;
  last_error?: string | null;
  next_action?: string | null;
};

export type ConnectionCatalogPayload = Record<string, unknown> & {
  items?: ConnectionCatalogItem[] | null;
};

export type ConnectionStatusPayload = Record<string, unknown> & {
  items?: ConnectionStatusItem[] | null;
};

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function createClientRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `req_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function parseIsoTimestamp(value: unknown): number | null {
  if (typeof value !== 'string') {
    return null;
  }
  const normalized = value.trim();
  if (!normalized) {
    return null;
  }
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function isActiveSessionRecord(session: WorkstationSessionRecord | null | undefined): session is WorkstationSessionRecord {
  if (!session || typeof session !== 'object') {
    return false;
  }
  const sessionId = readString(session.session_id);
  if (!sessionId) {
    return false;
  }
  const status = readString(session.status).toLowerCase();
  if (status && status !== 'active') {
    return false;
  }
  const expiresAt = parseIsoTimestamp(session.expires_at);
  if (expiresAt !== null && expiresAt <= Date.now()) {
    return false;
  }
  return true;
}

export type WorkstationRunDetailPayload = Record<string, unknown> & {
  run_id?: string | null;
  status?: string | null;
  archived?: boolean;
  diagnostics?: Record<string, unknown> | null;
  pending_confirmation?: Record<string, unknown> | null;
  result_summary?: string | null;
};

export type WorkstationApprovalDetailPayload = Record<string, unknown> & {
  approval_id?: string | null;
  run_id?: string | null;
  prompt?: string | null;
  status?: string | null;
  resolution?: string | null;
  requested_at?: string | null;
  resolved_at?: string | null;
  run?: Record<string, unknown> | null;
};

export type WorkstationArtifactDetailPayload = Record<string, unknown> & {
  artifact_id?: string | null;
  label?: string | null;
  file_name?: string | null;
  kind?: string | null;
  media_type?: string | null;
  preview_kind?: string | null;
  text_preview?: string | null;
  byte_size?: number | null;
  run_id?: string | null;
  created_at?: string | null;
  workspace_id?: string | null;
  tenant_id?: string | null;
  uri?: string | null;
  machine_id?: string | null;
  step_id?: string | null;
  step_index?: number | null;
  step_number?: number | null;
  retention?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
};

export type WorkstationSageServiceRecord = Record<string, unknown> & {
  id?: string | null;
  name?: string | null;
  description?: string | null;
  entry_noun?: string | null;
  profile?: Record<string, unknown> | null;
  entries?: Record<string, unknown>[] | null;
  recent_entries?: Record<string, unknown>[] | null;
  recent_activity?: Record<string, unknown>[] | null;
  memory_snapshot?: Record<string, unknown> | null;
  summary?: Record<string, unknown> | null;
  suggested_prompt?: string | null;
  status?: string | null;
};

export type WorkstationSageMemoryRecord = Record<string, unknown> & {
  id?: string | null;
  category?: string | null;
  category_label?: string | null;
  title?: string | null;
  content?: string | null;
  summary?: string | null;
  pinned?: boolean | null;
  source?: string | null;
  source_label?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
  history?: Record<string, unknown>[] | null;
};

export type WorkstationSageProfileQuestionRecord = Record<string, unknown> & {
  id?: string | null;
  field?: string | null;
  prompt?: string | null;
  placeholder?: string | null;
};

export type WorkstationSageProfileRecord = Record<string, unknown> & {
  profile?: Record<string, unknown> | null;
  bootstrap?: Record<string, unknown> | null;
  storage_policy?: Record<string, unknown> | null;
  projections?: Record<string, unknown> | null;
  account_seed?: Record<string, unknown> | null;
  updated_at?: string | null;
};

export type WorkstationSageContextFileRecord = {
  filename: string;
  content: string;
};

export type WorkstationSageHeartbeatRecord = Record<string, unknown> & {
  profile?: Record<string, unknown> | null;
  bootstrap?: Record<string, unknown> | null;
  quiet_hours?: Record<string, unknown> | null;
  ambient_monitor?: Record<string, unknown> | null;
  reminders?: Record<string, unknown> | null;
  next_scheduled_action?: Record<string, unknown> | null;
  wake_queue?: Record<string, unknown> | null;
  lane_queue?: Record<string, unknown> | null;
  queue_overview?: Record<string, unknown> | null;
  policy?: Record<string, unknown> | null;
};

export type WorkstationSageSkillRecord = Record<string, unknown> & {
  id?: string | null;
  name?: string | null;
  description?: string | null;
  what_it_does?: string | null;
  enabled?: boolean | null;
  available?: boolean | null;
  active_now?: boolean | null;
  status?: string | null;
  status_label?: string | null;
  reason?: string | null;
  setup_requirement?: string | null;
  source?: string | null;
  required_bins?: string[] | null;
  missing_bins?: string[] | null;
  required_env_vars?: string[] | null;
  missing_env_vars?: string[] | null;
  required_python_packages?: string[] | null;
  missing_python_packages?: string[] | null;
  supported_os?: string[] | null;
  tools?: string[] | null;
  slash_commands?: string[] | null;
  permission_label?: string | null;
  action_class?: string | null;
  requires_approval?: boolean | null;
  execution_mode?: string | null;
  allowed_runtime_modes?: string[] | null;
  curated?: boolean | null;
  curated_rank?: number | null;
};

export type WorkstationAgentTraceRecord = Record<string, unknown> & {
  id?: string | null;
  tenant_id?: string | null;
  workspace_id?: string | null;
  thread_id?: string | null;
  run_id?: string | null;
  root_agent_id?: string | null;
  surface?: string | null;
  runtime_target?: string | null;
  provider?: string | null;
  model?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  outcome?: string | null;
  final_message_id?: string | null;
};

export type WorkstationAgentTraceEvent = Record<string, unknown> & {
  id?: string | null;
  trace_id?: string | null;
  seq?: number | null;
  ts?: string | null;
  event_type?: string | null;
  persisted?: boolean;
  agent_id?: string | null;
  parent_id?: string | null;
  item_id?: string | null;
  tool_call_id?: string | null;
  child_run_id?: string | null;
  approval_id?: string | null;
  artifact_id?: string | null;
  data?: Record<string, unknown> | null;
};

export type WorkstationAgentTraceReplayPayload = Record<string, unknown> & {
  trace?: WorkstationAgentTraceRecord | null;
  events?: WorkstationAgentTraceEvent[] | null;
};

export type WorkstationAgentTraceListFilters = {
  threadId?: string | null;
  runId?: string | null;
  surface?: string | null;
  outcome?: string | null;
  rootAgentId?: string | null;
  limit?: number;
};

export type DeployedAgentRecord = Record<string, unknown> & {
  id?: string | null;
  owner_workspace_id?: string | null;
  backing_install_id?: string | null;
  name?: string | null;
  avatar?: string | null;
  persona?: string | null;
  system_prompt?: string | null;
  deployment_state?: string | null;
  channels?: Record<string, unknown> | null;
  knowledge_sources?: Record<string, unknown>[] | null;
  runtime_target?: string | null;
  billing_plan?: string | null;
  is_public?: boolean | null;
  quality_stars?: number | null;
  cost_tier?: string | null;
  category?: string | null;
  provider?: string | null;
  model?: string | null;
  config?: Record<string, unknown> | null;
  operational_state?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ConnectedExternalAgentRecord = Record<string, unknown> & {
  id?: string | null;
  surface_kind?: 'connected_external_agent' | string | null;
  studio_object_type?: string | null;
  workspace_id?: string | null;
  tenant_id?: string | null;
  name?: string | null;
  label?: string | null;
  description?: string | null;
  provider_kind?: string | null;
  status?: string | null;
  enabled?: boolean | null;
  connection_state?: string | null;
  trust_state?: string | null;
  endpoint_refs?: Record<string, unknown> | null;
  secret_ref?: string | null;
  capability_manifest?: Record<string, unknown> | null;
  manifest_projection?: Record<string, unknown> | null;
  surface_sections?: Record<string, unknown>[] | null;
  object_types?: string[] | null;
  external_sub_agents?: Record<string, unknown>[] | null;
  protocols?: Record<string, unknown>[] | null;
  local_connector?: Record<string, unknown> | null;
  manifest?: Record<string, unknown> | null;
  last_error?: string | null;
  last_manifest_refresh_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type StudioAgentComputerRecord = Record<string, unknown> & {
  id?: string | null;
  surface_kind?: 'agent_computer' | string | null;
  name?: string | null;
  status?: string | null;
  record?: Record<string, unknown> | null;
};

export type StudioAgentSurfacesPayload = Record<string, unknown> & {
  workspace_id?: string | null;
  tenant_id?: string | null;
  native_studio_agents?: Record<string, unknown>[] | null;
  connected_external_agents?: ConnectedExternalAgentRecord[] | null;
  agent_computers?: StudioAgentComputerRecord[] | null;
  items?: Record<string, unknown>[] | null;
};

export type DeployedAgentMemoryRecord = Record<string, unknown> & {
  id?: string | null;
  channel?: string | null;
  external_user_id?: string | null;
  session_id?: string | null;
  summary_text?: string | null;
  recent_message_count?: number | null;
  source_message_count?: number | null;
  updated_at?: string | null;
};

export type ProviderCatalogModelRecord = Record<string, unknown> & {
  id?: string | null;
  label?: string | null;
  provider?: string | null;
  context_window_tokens?: number | null;
  input_cost_per_1k_usd?: number | null;
  output_cost_per_1k_usd?: number | null;
  supports_tools?: boolean | null;
  supports_reasoning?: boolean | null;
  reasoning_levels?: string[] | null;
  local_self_hosted_compatible?: boolean | null;
  capability_labels?: string[] | null;
};

export type ProviderCatalogRecord = Record<string, unknown> & {
  id?: string | null;
  kind?: string | null;
  label?: string | null;
  state?: string | null;
  usable?: boolean | null;
  active?: boolean | null;
  configured?: boolean | null;
  hidden?: boolean | null;
  default_model?: string | null;
  default_auth_mode?: string | null;
  auth_modes?: Record<string, unknown>[] | null;
  provider_scopes?: string[] | null;
  sage_visible?: boolean | null;
  studio_visible?: boolean | null;
  local_only?: boolean | null;
  privacy_posture?: string | null;
  jurisdiction?: string | null;
  residency?: string | null;
  local_self_hosted_compatible?: boolean | null;
  capability_labels?: string[] | null;
  models?: ProviderCatalogModelRecord[] | null;
  models_source?: string | null;
  models_synced_at?: string | null;
  models_error?: string | null;
  connection_state?: string | null;
  connection_state_detail?: string | null;
  connection_active_source?: string | null;
  connection_credential_sources?: string[] | null;
  runtime_state?: string | null;
  runtime_state_detail?: string | null;
  runtime_active_source?: string | null;
  runtime_credential_sources?: string[] | null;
  workspace_connected?: boolean | null;
  credential_owner_kind?: string | null;
  credential_owner_label?: string | null;
  credential_plane?: string | null;
  credential_plane_label?: string | null;
  hosted_sage_ai_policy?: string | null;
  hosted_sage_ai_monthly_cap_usd?: number | null;
  hosted_sage_ai_monthly_cost_usd?: number | null;
  hosted_sage_ai_monthly_remaining_usd?: number | null;
  hosted_sage_ai_reason?: string | null;
  platform_runtime_allowed?: boolean | null;
};

export type ProviderProfileRecord = Record<string, unknown> & {
  id?: string | null;
  provider?: string | null;
  label?: string | null;
  credential_id?: string | null;
  auth_mode?: string | null;
  workspace_id?: string | null;
  priority?: number | null;
  enabled?: boolean | null;
  model?: string | null;
  health?: string | null;
  last_error?: string | null;
};

export type VaultCredentialRecord = Record<string, unknown> & {
  id?: string | null;
  label?: string | null;
  provider?: string | null;
  connector?: string | null;
  mode?: string | null;
  workspace_id?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type DeployedAgentConversationRecord = Record<string, unknown> & {
  session_id?: string | null;
  channel?: string | null;
  last_message?: string | null;
  last_message_at?: string | null;
  customer?: Record<string, unknown> | null;
  latest_run_id?: string | null;
  escalation_state?: string | null;
  outcome?: string | null;
};

export type DeployedAgentConversationDetail = Record<string, unknown> & {
  deployed_agent_id?: string | null;
  session_id?: string | null;
  channel?: string | null;
  thread_id?: string | null;
  run_ids?: string[] | null;
  customer?: Record<string, unknown> | null;
  messages?: Record<string, unknown>[] | null;
  tool_calls?: Record<string, unknown>[] | null;
  approval_events?: Record<string, unknown>[] | null;
  escalation_events?: Record<string, unknown>[] | null;
  entries?: Record<string, unknown>[] | null;
  outcome?: string | null;
};

export type DeployedAgentAnalyticsRecord = Record<string, unknown> & {
  deployed_agent_id?: string | null;
  active_users_last_30d?: number | null;
  message_volume?: Record<string, unknown> | null;
  escalation?: Record<string, unknown> | null;
  outcomes?: Record<string, unknown> | null;
  cost_burn?: Record<string, unknown> | null;
};

export type DeployedAgentAdminDashboardMessage = Record<string, unknown> & {
  id?: string | null;
  role?: string | null;
  content?: string | null;
  created_at?: string | null;
  channel?: string | null;
};

export type DeployedAgentAdminDashboardUserRow = Record<string, unknown> & {
  external_user_id?: string | null;
  last_message_at?: string | null;
  total_message_count?: number | null;
  memory_entry_count?: number | null;
  last_5_messages?: DeployedAgentAdminDashboardMessage[] | null;
};

export type DeployedAgentAdminDashboardQuestion = Record<string, unknown> & {
  question?: string | null;
  count?: number | null;
};

export type DeployedAgentCustomerEntryRecord = Record<string, unknown> & {
  entry_url?: string | null;
  cta_label?: string | null;
  telegram_deep_link?: string | null;
  bot_username?: string | null;
  qr_image_url?: string | null;
  qr_target?: string | null;
};

export type DeployedAgentAdminDashboardRecord = Record<string, unknown> & {
  deployed_agent_id?: string | null;
  total_users?: number | null;
  messages_today?: number | null;
  messages_this_calendar_month?: number | null;
  orders_today?: number | null;
  revenue_today_usd?: number | null;
  users_at_limit_today?: number | null;
  upgrade_clicks_this_month?: number | null;
  common_questions?: DeployedAgentAdminDashboardQuestion[] | null;
  customer_entry?: DeployedAgentCustomerEntryRecord | null;
  specialist_profile?: Record<string, unknown> | null;
  user_rows?: DeployedAgentAdminDashboardUserRow[] | null;
  limit?: number | null;
  offset?: number | null;
  has_more?: boolean | null;
};

export type DeployedAgentBusinessInsightAction = 'approve' | 'dismiss' | 'archive' | 'apply';

export type DeployedAgentBusinessInsightRecord = Record<string, unknown> & {
  id?: string | null;
  workspace_id?: string | null;
  deployed_agent_id?: string | null;
  pattern_key?: string | null;
  insight_type?: string | null;
  title?: string | null;
  summary?: string | null;
  recommendation?: string | null;
  sensitivity?: string | null;
  status?: string | null;
  channel_key?: string | null;
  event_count?: number | null;
  confidence?: number | null;
  window_start?: string | null;
  window_end?: string | null;
  redacted_examples?: unknown[] | null;
  metadata?: Record<string, unknown> | null;
  reviewed_at?: string | null;
  applied_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type DeployedAgentBusinessInsightsRecord = Record<string, unknown> & {
  workspace_id?: string | null;
  deployed_agent_id?: string | null;
  count?: number | null;
  items?: DeployedAgentBusinessInsightRecord[] | null;
};

export type MarketplacePackageRecord = Record<string, unknown> & {
  package_id?: string | null;
  kind?: string | null;
  label?: string | null;
  description?: string | null;
  category?: string | null;
  verification_status?: string | null;
  review_state?: string | null;
  health_state?: string | null;
  installed?: boolean | null;
  install_eligible?: boolean | null;
  install_blockers?: string[] | null;
  runtime_truth?: Record<string, unknown> | null;
  billing?: Record<string, unknown> | null;
  analytics?: Record<string, unknown> | null;
  publisher?: Record<string, unknown> | null;
  package?: Record<string, unknown> | null;
};

export type StudioProofAgentSeedRecord = Record<string, unknown> & {
  slug?: string | null;
  name?: string | null;
  category?: string | null;
  description?: string | null;
  persona?: Record<string, unknown> | null;
  default_data_sources?: Array<Record<string, unknown>> | null;
  channels?: Array<Record<string, unknown>> | null;
  tools_skills?: Array<Record<string, unknown>> | null;
  runtime_tier_recommendation?: Record<string, unknown> | null;
  approval_policy?: Record<string, unknown> | null;
  customization?: Record<string, unknown> | null;
};

export type MarketplaceAgentCardRecord = Record<string, unknown> & {
  id?: string | null;
  name?: string | null;
  description?: string | null;
  category?: string | null;
  quality_stars?: number | null;
  cost_tier?: string | null;
  telegram_bot_username?: string | null;
};

export type DeployedAgentTelegramReadinessRecord = Record<string, unknown> & {
  channel?: string | null;
  workspace_id?: string | null;
  deployed_agent_id?: string | null;
  ready_for_live?: boolean | null;
  status?: string | null;
  blockers?: Record<string, unknown>[] | null;
  warnings?: Record<string, unknown>[] | null;
  next_action?: string | null;
  configured_binding?: Record<string, unknown> | null;
  connectors?: Record<string, unknown>[] | null;
  webhook?: Record<string, unknown> | null;
  autopilot?: Record<string, unknown> | null;
  whatsapp?: Record<string, unknown> | null;
};

export type ChannelCatalogItemRecord = Record<string, unknown> & {
  channel_key?: string | null;
  binding_channel_key?: string | null;
  label?: string | null;
  provider?: string | null;
  connector_id?: string | null;
  account_provider?: string | null;
  runtime_lane?: string | null;
  category?: string | null;
  surface_kind?: string | null;
  product_surface?: string | null;
  navigation_group?: string | null;
  extension_kind?: string | null;
  ownership_boundary?: string | null;
  conversation_capable?: boolean | null;
  work_system_capable?: boolean | null;
  status?: string | null;
  stage?: string | null;
  live_capable?: boolean | null;
  launch_allowed?: boolean | null;
  requires_agent_computer?: boolean | null;
  surface_support?: string[] | null;
  capabilities?: string[] | null;
};

export type ChannelAccountRecord = Record<string, unknown> & {
  id?: string | null;
  account_ref?: string | null;
  secret_ref?: string | null;
  credential_id?: string | null;
  provider?: string | null;
  label?: string | null;
  scope?: string | null;
  runtime_lane?: string | null;
  status?: string | null;
  gateway_id?: string | null;
  requires_agent_computer?: boolean | null;
  metadata?: Record<string, unknown> | null;
};

export type AgentChannelBindingRecord = Record<string, unknown> & {
  channel_key?: string | null;
  catalog_id?: string | null;
  label?: string | null;
  provider?: string | null;
  connector_id?: string | null;
  account_ref?: string | null;
  account_label?: string | null;
  account_status?: string | null;
  endpoint_key?: string | null;
  enabled?: boolean | null;
  status?: string | null;
  runtime_lane?: string | null;
  launch_allowed?: boolean | null;
  live_capable?: boolean | null;
};

export type ChannelCatalogPayload = Record<string, unknown> & {
  items?: ChannelCatalogItemRecord[] | null;
  reserved?: Record<string, unknown>[] | null;
  count?: number | null;
};

export type ChannelAccountPayload = Record<string, unknown> & {
  items?: ChannelAccountRecord[] | null;
  count?: number | null;
};

export type AgentChannelBindingsPayload = Record<string, unknown> & {
  deployed_agent_id?: string | null;
  items?: AgentChannelBindingRecord[] | null;
  count?: number | null;
};

export type WorkstationPlatformAnalyticsDeploymentRecord = Record<string, unknown> & {
  deployed_agent_id?: string | null;
  name?: string | null;
  deployment_state?: string | null;
  provider?: string | null;
  model?: string | null;
  deployment_billing_plan?: string | null;
  workspace?: Record<string, unknown> | null;
  message_volume?: Record<string, unknown> | null;
  cost_burn?: Record<string, unknown> | null;
  billing_proxy?: Record<string, unknown> | null;
};

export type WorkstationPlatformAnalyticsCostRecord = Record<string, unknown> & {
  provider?: string | null;
  model?: string | null;
  runs_count?: number | null;
  total_tokens?: number | null;
  total_cost_usd?: number | null;
  share_percent?: number | null;
};

export type WorkstationPlatformAnalyticsPayload = Record<string, unknown> & {
  summary?: Record<string, unknown> | null;
  deployments?: WorkstationPlatformAnalyticsDeploymentRecord[] | null;
  most_active_deployed_agents?: WorkstationPlatformAnalyticsDeploymentRecord[] | null;
  model_cost_breakdown?: WorkstationPlatformAnalyticsCostRecord[] | null;
};

export type WorkstationClientDependencies = {
  scope: WorkstationClientScope;
  transport: {
    request: (
      path: string,
      init?: RequestInit,
      policy?: WorkstationRequestPolicy,
    ) => Promise<Response>;
  };
  queryClient: {
    peek: <T>(key: string) => T | null;
    set: <T>(key: string, value: T) => T;
    readThrough?: <T>(
      key: string,
      executor: (context: { signal: AbortSignal; cacheKey: string }) => Promise<T>,
      ttlMs?: number,
    ) => Promise<T>;
    invalidate?: (key?: string) => void;
  };
  realtime: {
    trackEventSource: <T extends EventSource>(source: T) => T;
  };
  getApiBaseUrl: () => string;
};

export type WorkstationRequestPolicy = {
  timeoutMs?: number;
  retryCount?: number;
  retryOnStatuses?: number[];
  refreshSessionOn401?: boolean;
};

export type WorkstationClientRequestOptions = {
  path: string;
  init?: RequestInit;
  allowStatuses?: number[];
  policy?: WorkstationRequestPolicy;
};

export type WorkstationClientStreamOptions = {
  sinceId?: string;
  sinceTs?: string;
  includeBacklog?: boolean;
  limit?: number;
};

export type WorkstationClientPaths = {
  workspaceBootstrap: (workspaceId: string) => string;
  sessionCreate: string;
  turnSubmit: string;
  threads: (options?: { includeTurns?: boolean; limit?: number }) => string;
  thread: (threadId: string) => string;
  threadTurns: (threadId: string) => string;
  runs: (limit?: number) => string;
  runDetail: (runId: string) => string;
  approvals: (limit?: number) => string;
  approvalDetail: (approvalId: string) => string;
  approvalResolve: (approvalId: string, runId?: string | null) => string;
  artifacts: (limit?: number) => string;
  artifactDetail: (artifactId: string) => string;
  artifactFile: (artifactId: string) => string;
  artifactContent: (artifactId: string) => string;
  notifications: (limit?: number) => string;
  activity: (limit?: number, traceId?: string | null) => string;
  pilotProofReadiness: (days?: number, limit?: number) => string;
  pilotProofCaseStudy: (days?: number, limit?: number) => string;
  pilotProofInvestorMemo: (days?: number, limit?: number) => string;
  pilotProofAdsReadiness: (days?: number, limit?: number) => string;
  sageMemory: string;
  sageProfile: string;
  sageHeartbeat: string;
  sageSkills: string;
  sageCapabilities: string;
  sageProfileBootstrapAnswer: string;
  sageMemoryStoragePolicy: string;
  sageMemoryExport: string;
  sageMemoryWipe: string;
  sageContextFiles: string;
  sageContextFile: (filename: string) => string;
  sageMemoryEntries: string;
  sageMemoryEntry: (entryId: string) => string;
  sageMemoryPin: (entryId: string) => string;
  sageServices: string;
  sageServiceProfile: (serviceId: string) => string;
  sageServiceEntries: (serviceId: string) => string;
  sageServiceEntry: (serviceId: string, entryId: string) => string;
  sageServicePin: (serviceId: string, entryId: string) => string;
  appsInstalled: string;
  appsStore: string;
  appsUpdates: string;
  appsInstall: string;
  appsUninstall: string;
  appsUpdate: string;
  agentDefinitions: string;
  agentInstalls: string;
  runtimeAttachments: string;
  runtimeTargets: string;
  mcpServers: string;
  mcpServer: (serverId: string) => string;
  mcpServerRefresh: (serverId: string) => string;
  mcpToolApprove: (serverId: string) => string;
  providers: string;
  providersCatalog: string;
  workspaceAiRoute: string;
  workspaceAiRouteDefault: string;
  providerModels: (providerId: string, profileId?: string | null) => string;
  workspaceProviderModelsRefresh: (providerId: string) => string;
  workspaceTransparencySettings: string;
  workspaceRuntimeSessions: (limit?: number) => string;
  speechToText: string;
  providerProfiles: (provider?: string | null) => string;
  providerProfilesRoot: string;
  credentialsVault: string;
  connectorsVault: string;
  connectorVaultCredential: (credentialId: string) => string;
  workspaceProviderCredentials: string;
  sageToolPolicy: string;
  sageChatAttachments: string;
  runInstalledAgent: (installId: string) => string;
  agentTraces: (filters?: WorkstationAgentTraceListFilters) => string;
  agentTraceDetail: (traceId: string) => string;
  agentTraceStream: (traceId: string) => string;
  sageTurnsStream: (workspaceId: string) => string;
  deployedAgents: (deploymentState?: string | null) => string;
  deployedAgentTelegramReadiness: (deployedAgentId?: string | null) => string;
  deployedAgentDetail: (deployedAgentId: string) => string;
  deployedAgentDeploy: (deployedAgentId: string) => string;
  deployedAgentPause: (deployedAgentId: string) => string;
  deployedAgentRuntimeKill: (deployedAgentId: string, sessionId: string) => string;
  deployedAgentAuditExport: (deployedAgentId: string, limit?: number) => string;
  deployedAgentTestTurn: (deployedAgentId: string) => string;
  deployedAgentKnowledgeVerify: (deployedAgentId: string) => string;
  deployedAgentKnowledgeFiles: (deployedAgentId: string) => string;
  deployedAgentAnalyticsRoster: string;
  deployedAgentAnalyticsDetail: (deployedAgentId: string) => string;
  deployedAgentAdminDashboard: (deployedAgentId: string, limit?: number, offset?: number) => string;
  deployedAgentBusinessInsights: (deployedAgentId: string, status?: string | null, limit?: number, offset?: number) => string;
  deployedAgentBusinessInsightReview: (
    deployedAgentId: string,
    insightId: string,
    action: DeployedAgentBusinessInsightAction,
  ) => string;
  studioAgentSurfaces: string;
  studioChannelCatalog: string;
  connectionsCatalog: (surface?: string | null) => string;
  connectionsStatus: (surface?: string | null, selectedGatewayId?: string | null) => string;
  sageAgentComputerSelection: string;
  connectionSetupStart: (connectionId: string) => string;
  connectionTest: (connectionId: string) => string;
  connectionVerify: (connectionId: string) => string;
  connectionDisconnect: (connectionId: string) => string;
  studioChannelAccounts: string;
  studioAgentChannelBindings: (deployedAgentId: string) => string;
  studioAgentChannelBindingAction: (deployedAgentId: string, channelKey: string, action: 'pause' | 'resume' | 'revoke' | 'test') => string;
  studioExternalAgents: string;
  studioExternalAgent: (externalAgentId: string) => string;
  studioExternalAgentRefreshManifest: (externalAgentId: string) => string;
  studioExternalAgentChatTurn: (externalAgentId: string) => string;
  studioExternalAgentSection: (externalAgentId: string, sectionId: string) => string;
  studioExternalAgentDisconnect: (externalAgentId: string) => string;
  deployedAgentMemory: (deployedAgentId: string, limit?: number, offset?: number) => string;
  deployedAgentConversations: (deployedAgentId: string, limit?: number, offset?: number) => string;
  deployedAgentConversationDetail: (deployedAgentId: string, sessionId: string) => string;
  deployedAgentExternalUserDelete: (deployedAgentId: string, externalUserId: string) => string;
  marketplaceAgents: (filters?: { category?: string | null; costTier?: string | null; limit?: number; offset?: number }) => string;
  discoveryFeed: (options?: { filter?: string | null }) => string;
  discoveryAdopt: (feedItemId: string) => string;
  marketplacePackages: (options?: { kind?: string | null; runtimeType?: string | null; includeReviewQueue?: boolean }) => string;
  studioTemplates: string;
  marketplaceProviderRegister: string;
  marketplaceAppRegister: string;
  marketplaceAppSubmissions: string;
  marketplacePackageReview: (packageId: string) => string;
  marketplacePackageInstall: (packageId: string) => string;
  platformAnalytics: string;
  workspaceRouting: string;
  workspaceMembers: string;
  workspaceMemberInvites: string;
  workspaceMemberInvite: (inviteId: string) => string;
  workspaceMember: (userId: string) => string;
  workspacePolicies: string;
  usageSummary: (period?: string) => string;
  billingSummary: string;
  billingCheckout: string;
  billingPortal: string;
  billingCreditPurchase: string;
  billingCreditBalance: string;
  billingCreditUsageHistory: (limit?: number) => string;
  billingCreditUsage: (options?: CreditUsageQueryOptions) => string;
  notificationsStream: (options?: WorkstationClientStreamOptions) => string;
  channelEventsStream: (options?: WorkstationClientStreamOptions) => string;
};

export class WorkstationClientError extends Error {
  readonly status: number;

  readonly detail: unknown;

  readonly code: string | null;

  readonly retryable: boolean;

  readonly retryAfterSeconds: number | null;

  readonly errorClass: string | null;

  constructor(
    message: string,
    status: number,
    detail: unknown,
    code: string | null = null,
    options: {
      retryable?: boolean;
      retryAfterSeconds?: number | null;
      errorClass?: string | null;
    } = {},
  ) {
    super(message);
    this.name = 'WorkstationClientError';
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.retryable = Boolean(options.retryable);
    this.retryAfterSeconds = typeof options.retryAfterSeconds === 'number'
      && Number.isFinite(options.retryAfterSeconds)
      ? options.retryAfterSeconds
      : null;
    this.errorClass = typeof options.errorClass === 'string' && options.errorClass.trim()
      ? options.errorClass.trim()
      : null;
  }
}

export type WorkstationClient = {
  scope: WorkstationClientScope;
  paths: WorkstationClientPaths;
  requestJson: <T>(options: WorkstationClientRequestOptions) => Promise<T | null>;
  listThreads: (options?: { includeTurns?: boolean; limit?: number }) => Promise<Record<string, unknown>>;
  getThread: (options: { threadId: string; allowMissing?: boolean }) => Promise<Record<string, unknown> | null>;
  persistUserTurn: (options: {
    actor: WorkstationSessionActor;
    sessionId: string;
    threadId: string;
    message: string;
    channel?: string;
    runtimeProfileId?: string | null;
    metadata?: Record<string, unknown>;
    clientRequestId?: string | null;
    attachments?: SageChatAttachment[];
  }) => Promise<Record<string, unknown>>;
  listRuns: (options?: { limit?: number }) => Promise<Record<string, unknown>>;
  getRunDetail: (options: { runId: string; allowMissing?: boolean }) => Promise<Record<string, unknown> | null>;
  listApprovals: (options?: { limit?: number }) => Promise<Record<string, unknown>>;
  getApprovalDetail: (options: { approvalId: string; allowMissing?: boolean }) => Promise<Record<string, unknown> | null>;
  listArtifacts: (options?: { limit?: number }) => Promise<Record<string, unknown>>;
  getArtifactDetail: (options: { artifactId: string; allowMissing?: boolean }) => Promise<Record<string, unknown> | null>;
  artifactFileUrl: (artifactId: string) => string;
  artifactDownloadUrl: (artifactId: string) => string;
  listNotifications: (options?: { limit?: number }) => Promise<Record<string, unknown>>;
  markNotificationsRead: (options?: {
    notificationIds?: string[];
    markAll?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  listActivityTimeline: (options?: { limit?: number; traceId?: string | null }) => Promise<Record<string, unknown>>;
  getPilotProofReadiness: (options?: { days?: number; limit?: number }) => Promise<Record<string, unknown>>;
  getPilotProofCaseStudy: (options?: { days?: number; limit?: number }) => Promise<Record<string, unknown>>;
  getPilotProofInvestorMemo: (options?: { days?: number; limit?: number }) => Promise<Record<string, unknown>>;
  getPilotProofAdsReadiness: (options?: { days?: number; limit?: number }) => Promise<Record<string, unknown>>;
  listSageMemory: () => Promise<Record<string, unknown>>;
  getSageProfile: () => Promise<WorkstationSageProfileRecord>;
  getSageHeartbeat: () => Promise<WorkstationSageHeartbeatRecord>;
  listSageSkills: () => Promise<Record<string, unknown>>;
  listSageCapabilities: () => Promise<Record<string, unknown>>;
  updateSageProfile: (options: {
    userName?: string | null;
    identitySummary?: string | null;
    communicationStyle?: string | null;
    recurringResponsibility?: string | null;
    standingRules?: string[] | null;
    standingRulesText?: string | null;
  }) => Promise<WorkstationSageProfileRecord>;
  answerSageProfileBootstrap: (options: {
    answer: string;
  }) => Promise<WorkstationSageProfileRecord>;
  getSageMemoryStoragePolicy: () => Promise<Record<string, unknown>>;
  listSageContextFiles: () => Promise<Record<string, unknown>>;
  updateSageContextFile: (options: {
    filename: string;
    content: string;
  }) => Promise<Record<string, unknown>>;
  exportSageMemory: () => Promise<Record<string, unknown>>;
  wipeSageMemory: (options: { confirm: string }) => Promise<Record<string, unknown>>;
  createSageMemoryEntry: (options: {
    category: string;
    title: string;
    content: string;
    pinned?: boolean;
  }) => Promise<Record<string, unknown>>;
  updateSageMemoryEntry: (options: {
    entryId: string;
    category: string;
    title: string;
    content: string;
    pinned?: boolean;
  }) => Promise<Record<string, unknown>>;
  deleteSageMemoryEntry: (options: {
    entryId: string;
  }) => Promise<Record<string, unknown> | null>;
  setSageMemoryEntryPinned: (options: {
    entryId: string;
    pinned: boolean;
  }) => Promise<Record<string, unknown>>;
  listSageServices: () => Promise<Record<string, unknown>>;
  updateSageServiceProfile: (options: {
    serviceId: string;
    profile: Record<string, unknown>;
  }) => Promise<Record<string, unknown>>;
  createSageServiceEntry: (options: {
    serviceId: string;
    entry: Record<string, unknown>;
  }) => Promise<Record<string, unknown>>;
  updateSageServiceEntry: (options: {
    serviceId: string;
    entryId: string;
    entry: Record<string, unknown>;
  }) => Promise<Record<string, unknown>>;
  deleteSageServiceEntry: (options: {
    serviceId: string;
    entryId: string;
  }) => Promise<Record<string, unknown> | null>;
  setSageServiceEntryPinned: (options: {
    serviceId: string;
    entryId: string;
    pinned: boolean;
  }) => Promise<Record<string, unknown>>;
  listInstalledApps: () => Promise<Record<string, unknown>>;
  listStoreApps: () => Promise<Record<string, unknown>>;
  listAppUpdates: () => Promise<Record<string, unknown>>;
  installApp: (options: {
    appId: string;
    packageId?: string | null;
    releaseChannel?: string | null;
    installSource?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  uninstallApp: (options: { appId: string }) => Promise<Record<string, unknown> | null>;
  updateApp: (options: {
    appId: string;
    packageId?: string | null;
    releaseChannel?: string | null;
    installSource?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  listAgentDefinitions: () => Promise<Record<string, unknown>>;
  listAgentInstalls: () => Promise<Record<string, unknown>>;
  listRuntimeAttachments: () => Promise<Record<string, unknown>>;
  listRuntimeTargets: () => Promise<Record<string, unknown>>;
  listMcpServers: () => Promise<Record<string, unknown>>;
  saveMcpServer: (options: {
    serverId: string;
    label?: string | null;
    endpoint: string;
    enabled?: boolean;
    discoverTools?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  refreshMcpServer: (options: { serverId: string }) => Promise<Record<string, unknown> | null>;
  approveMcpTool: (options: { serverId: string; toolName: string }) => Promise<Record<string, unknown> | null>;
  deleteMcpServer: (options: { serverId: string }) => Promise<Record<string, unknown> | null>;
  listProviders: () => Promise<Record<string, unknown>>;
  listProviderCatalog: () => Promise<Record<string, unknown>>;
  getWorkspaceAiRoute: () => Promise<WorkspaceAiRoutePayload>;
  updateWorkspaceAiRouteDefault: (options: {
    routeId?: string | null;
    kind?: WorkspaceAiRouteKind | null;
    provider?: string | null;
    model?: string | null;
    modelPreset?: string | null;
  }) => Promise<WorkspaceAiRoutePayload>;
  listProviderModels: (options: { providerId: string; profileId?: string | null }) => Promise<Record<string, unknown>>;
  refreshWorkspaceProviderModels: (options: { providerId: string }) => Promise<Record<string, unknown> | null>;
  getWorkspaceTransparencySettings: () => Promise<Record<string, unknown>>;
  updateWorkspaceTransparencySettings: (options: Record<string, unknown>) => Promise<Record<string, unknown> | null>;
  listProviderProfiles: (options?: { provider?: string | null }) => Promise<Record<string, unknown>>;
  listVaultCredentials: () => Promise<Record<string, unknown>>;
  listConnectorsVault: () => Promise<Record<string, unknown>>;
  upsertProviderProfile: (options: {
    id?: string | null;
    provider: string;
    label: string;
    credentialId?: string | null;
    authMode?: string | null;
    priority?: number;
    enabled?: boolean;
    model?: string | null;
    metadata?: Record<string, unknown> | null;
  }) => Promise<Record<string, unknown> | null>;
  upsertWorkspaceProviderCredential: (options: {
    provider: string;
    apiKey?: string | null;
    baseUrl?: string | null;
    model?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  deleteWorkspaceProviderCredential: (options: {
    provider: string;
  }) => Promise<Record<string, unknown> | null>;
  deleteConnectorVaultCredential: (options: {
    credentialId: string;
  }) => Promise<Record<string, unknown> | null>;
  getSageToolPolicy: () => Promise<Record<string, unknown>>;
  updateSageToolPolicy: (options: {
    tool: string;
    enabled: boolean;
  }) => Promise<Record<string, unknown> | null>;
  runInstalledAgent: (options: {
    installId: string;
    message?: string;
    threadId?: string | null;
    sessionId?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  killDeployedAgentRuntimeSession: (options: {
    deployedAgentId: string;
    sessionId: string;
  }) => Promise<Record<string, unknown> | null>;
  listWorkspaceRuntimeSessions: (options?: { limit?: number }) => Promise<Record<string, unknown>>;
  exportDeployedAgentAudit: (options: {
    deployedAgentId: string;
    limit?: number;
  }) => Promise<Record<string, unknown>>;
  transcribeSpeech: (audio: Blob) => Promise<Record<string, unknown>>;
  listTraces: (filters?: WorkstationAgentTraceListFilters) => Promise<Record<string, unknown>>;
  getTraceReplay: (options: {
    traceId: string;
    allowMissing?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  listDeployedAgents: (options?: { deploymentState?: string | null }) => Promise<Record<string, unknown>>;
  listStudioAgentSurfaces: () => Promise<StudioAgentSurfacesPayload>;
  listStudioChannelCatalog: () => Promise<ChannelCatalogPayload>;
  listConnectionCatalog: (options?: { surface?: string | null }) => Promise<ConnectionCatalogPayload>;
  listConnectionStatus: (options?: {
    surface?: string | null;
    selectedGatewayId?: string | null;
  }) => Promise<ConnectionStatusPayload>;
  // MAN-36: rewired to v2 backend GET /apps
  listApps: () => Promise<Record<string, unknown>>;
  // MAN-36: rewired to v2 backend DELETE /apps/{provider}
  disconnectApp: (provider: string) => Promise<Record<string, unknown>>;
  // MAN-36: v2 backend GET /oauth/start
  getOAuthStartUrl: (app: string) => Promise<Record<string, unknown>>;
  getSageAgentComputerSelection: () => Promise<Record<string, unknown>>;
  setSageAgentComputerSelection: (options: {
    selectedGatewayId: string;
    metadata?: Record<string, unknown> | null;
  }) => Promise<Record<string, unknown> | null>;
  startConnectionSetup: (options: {
    connectionId: string;
    surface?: string | null;
    selectedGatewayId?: string | null;
    metadata?: Record<string, unknown> | null;
  }) => Promise<Record<string, unknown> | null>;
  /** Phase 1 bridge: completes an app OAuth flow and registers MCP servers with credential injection.
   *  Calls POST /apps/{provider}/oauth/complete on the backend.
   *  TODO: This should be called by the backend OAuth callback (routes_connections.py
   *        complete_connection_oauth_callback) AFTER the token exchange — not from the
   *        frontend, because the frontend never sees the OAuth authorization code. */
  completeAppOAuthForMcp: (options: {
    provider: string;
    code: string;
    redirectUri: string;
    workspaceId: string;
  }) => Promise<Record<string, unknown> | null>;
  testConnection: (options: {
    connectionId: string;
    surface?: string | null;
    selectedGatewayId?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  verifyConnection: (options: {
    connectionId: string;
    surface?: string | null;
    selectedGatewayId?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  disconnectConnection: (options: {
    connectionId: string;
    surface?: string | null;
    selectedGatewayId?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  listStudioChannelAccounts: () => Promise<ChannelAccountPayload>;
  createStudioChannelAccount: (options: {
    provider: string;
    label: string;
    credentials: Record<string, unknown>;
    metadata?: Record<string, unknown> | null;
    skipValidation?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  listAgentChannelBindings: (options: {
    deployedAgentId: string;
  }) => Promise<AgentChannelBindingsPayload>;
  createAgentChannelBinding: (options: {
    deployedAgentId: string;
    catalogId: string;
    accountRef?: string | null;
    endpointKey?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  pauseAgentChannelBinding: (options: {
    deployedAgentId: string;
    channelKey: string;
  }) => Promise<Record<string, unknown> | null>;
  resumeAgentChannelBinding: (options: {
    deployedAgentId: string;
    channelKey: string;
  }) => Promise<Record<string, unknown> | null>;
  revokeAgentChannelBinding: (options: {
    deployedAgentId: string;
    channelKey: string;
  }) => Promise<Record<string, unknown> | null>;
  testAgentChannelBinding: (options: {
    deployedAgentId: string;
    channelKey: string;
    message?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  listConnectedExternalAgents: () => Promise<Record<string, unknown>>;
  createConnectedExternalAgent: (options: {
    name: string;
    providerKind?: string | null;
    endpoints?: Record<string, unknown> | null;
    manifest?: Record<string, unknown> | null;
    secretRef?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  updateConnectedExternalAgent: (options: {
    externalAgentId: string;
    name?: string | null;
    providerKind?: string | null;
    endpoints?: Record<string, unknown> | null;
    manifest?: Record<string, unknown> | null;
    secretRef?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  refreshConnectedExternalAgentManifest: (options: {
    externalAgentId: string;
  }) => Promise<Record<string, unknown> | null>;
  chatTurnConnectedExternalAgent: (options: {
    externalAgentId: string;
    message: string;
    recentMessages?: Record<string, unknown>[];
  }) => Promise<Record<string, unknown> | null>;
  getConnectedExternalAgentSectionData: (options: {
    externalAgentId: string;
    sectionId: string;
  }) => Promise<Record<string, unknown> | null>;
  disconnectConnectedExternalAgent: (options: {
    externalAgentId: string;
  }) => Promise<Record<string, unknown> | null>;
  createDeployedAgent: (options: {
    name: string;
    avatar?: string | null;
    persona?: string | null;
    systemPrompt?: string | null;
    channels?: Record<string, unknown>;
    knowledgeSources?: Record<string, unknown>[];
    runtimeTarget?: string | null;
    billingPlan?: string | null;
    config?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
    runtimeProfileId?: string | null;
    provider?: string | null;
    model?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  getDeployedAgent: (options: {
    deployedAgentId: string;
    allowMissing?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  getDeployedAgentTelegramReadiness: (options?: {
    deployedAgentId?: string | null;
    allowMissing?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  updateDeployedAgent: (options: {
    deployedAgentId: string;
    name?: string | null;
    avatar?: string | null;
    persona?: string | null;
    systemPrompt?: string | null;
    deploymentState?: string | null;
    channels?: Record<string, unknown>;
    knowledgeSources?: Record<string, unknown>[];
    runtimeTarget?: string | null;
    billingPlan?: string | null;
    config?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
    provider?: string | null;
    model?: string | null;
    isPublic?: boolean | null;
    category?: string | null;
    qualityStars?: number | null;
    costTier?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  deployDeployedAgent: (options: { deployedAgentId: string }) => Promise<Record<string, unknown> | null>;
  pauseDeployedAgent: (options: { deployedAgentId: string }) => Promise<Record<string, unknown> | null>;
  testTurnDeployedAgent: (options: { deployedAgentId: string; body: Record<string, unknown> }) => Promise<Record<string, unknown> | null>;
  verifyDeployedAgentKnowledge: (options: { deployedAgentId: string; query: string; limit?: number }) => Promise<Record<string, unknown> | null>;
  uploadDeployedAgentKnowledgeFile: (options: {
    deployedAgentId: string;
    fileName: string;
    contentText: string;
  }) => Promise<Record<string, unknown> | null>;
  listDeployedAgentAnalytics: () => Promise<Record<string, unknown>>;
  getDeployedAgentAnalytics: (options: {
    deployedAgentId: string;
    allowMissing?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  getDeployedAgentAdminDashboard: (options: {
    deployedAgentId: string;
    limit?: number;
    offset?: number;
    allowMissing?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  listDeployedAgentBusinessInsights: (options: {
    deployedAgentId: string;
    status?: string | null;
    limit?: number;
    offset?: number;
  }) => Promise<Record<string, unknown>>;
  reviewDeployedAgentBusinessInsight: (options: {
    deployedAgentId: string;
    insightId: string;
    action: DeployedAgentBusinessInsightAction;
    note?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  listDeployedAgentMemory: (options: {
    deployedAgentId: string;
    limit?: number;
    offset?: number;
  }) => Promise<Record<string, unknown>>;
  listDeployedAgentConversations: (options: {
    deployedAgentId: string;
    limit?: number;
    offset?: number;
  }) => Promise<Record<string, unknown>>;
  getDeployedAgentConversationDetail: (options: {
    deployedAgentId: string;
    sessionId: string;
    allowMissing?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  deleteDeployedAgentExternalUserData: (options: {
    deployedAgentId: string;
    externalUserId: string;
    channel: string;
    sessionId?: string | null;
    note?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  listMarketplaceAgents: (options?: {
    category?: string | null;
    costTier?: string | null;
    limit?: number;
    offset?: number;
  }) => Promise<Record<string, unknown>>;
  listDiscoveryFeed: (options?: {
    filter?: string | null;
  }) => Promise<Record<string, unknown>>;
  adoptDiscoveryItem: (options: {
    feedItemId: string;
  }) => Promise<Record<string, unknown> | null>;
  listMarketplacePackages: (options?: {
    kind?: string | null;
    runtimeType?: string | null;
    includeReviewQueue?: boolean;
  }) => Promise<Record<string, unknown>>;
  listMarketplaceAppSubmissions: () => Promise<Record<string, unknown>>;
  listStudioTemplates: () => Promise<Record<string, unknown>>;
  registerMarketplaceProvider: (payload: Record<string, unknown>) => Promise<Record<string, unknown> | null>;
  registerMarketplaceApp: (payload: Record<string, unknown>) => Promise<Record<string, unknown> | null>;
  reviewMarketplaceAppSubmission: (options: {
    packageId: string;
    approved: boolean;
    reason?: string | null;
    verificationStatus?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  installMarketplacePackage: (options: {
    packageId: string;
  }) => Promise<Record<string, unknown> | null>;
  getPlatformAnalytics: () => Promise<Record<string, unknown>>;
  getWorkspaceRouting: () => Promise<Record<string, unknown>>;
  updateWorkspaceRouting: (options: {
    adminDefaults?: Record<string, unknown>;
  }) => Promise<Record<string, unknown> | null>;
  listWorkspaceMembers: () => Promise<Record<string, unknown>>;
  inviteWorkspaceMember: (options: { email: string; role: string }) => Promise<Record<string, unknown> | null>;
  revokeWorkspaceInvite: (options: { inviteId: string }) => Promise<Record<string, unknown> | null>;
  updateWorkspaceMemberRole: (options: { userId: string; role: string }) => Promise<Record<string, unknown> | null>;
  removeWorkspaceMember: (options: { userId: string }) => Promise<Record<string, unknown> | null>;
  getWorkspacePolicies: () => Promise<Record<string, unknown>>;
  updateWorkspacePolicies: (payload: Record<string, unknown>) => Promise<Record<string, unknown> | null>;
  getUsageSummary: (options?: { period?: string }) => Promise<Record<string, unknown>>;
  getBillingSummary: () => Promise<Record<string, unknown>>;
  createBillingCheckoutSession: (options: {
    planId: string;
    successUrl?: string | null;
    cancelUrl?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  createBillingPortalSession: (options?: {
    returnUrl?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  createCreditPurchaseSession: (options: {
    amountUsd: number;
    successUrl?: string | null;
    cancelUrl?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  getCreditBalance: () => Promise<Record<string, unknown>>;
  getCreditUsageHistory: (options?: { limit?: number }) => Promise<Record<string, unknown>>;
  getCreditUsage: (options?: CreditUsageQueryOptions) => Promise<Record<string, unknown>>;
  uploadSageChatAttachment: (options: {
    file: File;
    onProgress?: (progress: number) => void;
  }) => Promise<SageChatAttachment>;
  createSession: (options: {
    actor: WorkstationSessionActor;
    threadId: string;
    channel?: string;
    source?: string;
    forceNew?: boolean;
    existingSession?: WorkstationSessionRecord | null;
  }) => Promise<WorkstationSessionRecord>;
  submitTurn: (options: {
    actor: WorkstationSessionActor;
    sessionId: string;
    threadId: string;
    message: string;
    channel?: string;
    source?: string;
    runtimeTarget?: string | null;
    machineTarget?: string | null;
    provider?: string | null;
    model?: string | null;
    reasoningEffort?: string | null;
    policyContext?: Record<string, unknown>;
    clientRequestId?: string | null;
    attachments?: SageChatAttachment[];
  }) => Promise<WorkstationTurnResponse>;
  submitTurnStream: (options: {
    actor: WorkstationSessionActor;
    sessionId: string;
    threadId: string;
    message: string;
    channel?: string;
    source?: string;
    runtimeTarget?: string | null;
    machineTarget?: string | null;
    provider?: string | null;
    model?: string | null;
    reasoningEffort?: string | null;
    policyContext?: Record<string, unknown>;
    onEvent?: (event: WorkstationTurnStreamEvent) => void;
    clientRequestId?: string | null;
    abortHandle?: WorkstationTurnStreamAbortHandle | null;
    attachments?: SageChatAttachment[];
  }) => Promise<WorkstationTurnResponse>;
  submitTurnWithSessionRetry: (options: {
    actor: WorkstationSessionActor;
    threadId: string;
    message: string;
    channel?: string;
    source?: string;
    runtimeTarget?: string | null;
    machineTarget?: string | null;
    provider?: string | null;
    model?: string | null;
    reasoningEffort?: string | null;
    policyContext?: Record<string, unknown>;
    existingSession?: WorkstationSessionRecord | null;
    clientRequestId?: string | null;
    attachments?: SageChatAttachment[];
  }) => Promise<{
    response: WorkstationTurnResponse;
    session: WorkstationSessionRecord;
    renewed: boolean;
  }>;
  submitTurnStreamWithSessionRetry: (options: {
    actor: WorkstationSessionActor;
    threadId: string;
    message: string;
    channel?: string;
    source?: string;
    runtimeTarget?: string | null;
    machineTarget?: string | null;
    provider?: string | null;
    model?: string | null;
    reasoningEffort?: string | null;
    policyContext?: Record<string, unknown>;
    onEvent?: (event: WorkstationTurnStreamEvent) => void;
    existingSession?: WorkstationSessionRecord | null;
    clientRequestId?: string | null;
    abortHandle?: WorkstationTurnStreamAbortHandle | null;
    attachments?: SageChatAttachment[];
  }) => Promise<{
    response: WorkstationTurnResponse;
    session: WorkstationSessionRecord;
    renewed: boolean;
  }>;
  resolveApproval: (options: {
    approvalId: string;
    payload: Record<string, unknown>;
    runId?: string | null;
  }) => Promise<Record<string, unknown> | null>;
  openTraceStream: (traceId: string) => EventSource;
  openNotificationsStream: (options?: WorkstationClientStreamOptions) => EventSource;
  openChannelEventsStream: (options?: WorkstationClientStreamOptions) => EventSource;
  openSageTurnStream: (workspaceId: string) => EventSource;
  snapshot: () => {
    scope: WorkstationClientScope;
    paths: {
      sessionCreate: string;
      turnSubmit: string;
      runs: string;
      approvals: string;
      artifacts: string;
      agentTraces: string;
      notifications: string;
      activity: string;
      agentTraceStream: string;
      notificationsStream: string;
      channelEventsStream: string;
      sageTurnsStream: string;
    };
  };
};

function buildQueryString(
  entries: Record<string, string | number | boolean | null | undefined>,
): string {
  const params = new URLSearchParams();

  for (const [key, value] of Object.entries(entries)) {
    if (value === null || value === undefined || value === '') {
      continue;
    }
    params.set(key, String(value));
  }

  const query = params.toString();
  return query ? `?${query}` : '';
}

export function buildWorkstationApiPaths(workspaceId: string): WorkstationClientPaths {
  return {
    workspaceBootstrap: (targetWorkspaceId) =>
      `/api/workspaces/${encodeURIComponent(targetWorkspaceId)}/bootstrap`,
    sessionCreate: '/api/sessions',
    turnSubmit: '/api/turn',
    threads: ({ includeTurns = false, limit = 50 } = {}) =>
      `/api/threads${buildQueryString({ workspace_id: workspaceId, include_turns: includeTurns, limit })}`,
    thread: (threadId) =>
      `/api/threads/${encodeURIComponent(threadId)}${buildQueryString({ workspace_id: workspaceId })}`,
    threadTurns: (threadId) =>
      `/api/threads/${encodeURIComponent(threadId)}/turns${buildQueryString({ workspace_id: workspaceId })}`,
    runs: (limit = 80) =>
      `/api/runs${buildQueryString({ workspace_id: workspaceId, limit })}`,
    runDetail: (runId) =>
      `/api/runs/${encodeURIComponent(runId)}`,
    approvals: (limit = 80) =>
      `/api/approvals${buildQueryString({ workspace_id: workspaceId, limit })}`,
    approvalDetail: (approvalId) =>
      `/api/approvals/${encodeURIComponent(approvalId)}`,
    approvalResolve: (approvalId, runId) => (
      runId
        ? `/api/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}/resolve${buildQueryString({ workspace_id: workspaceId })}`
        : `/api/approvals/${encodeURIComponent(approvalId)}/resolve${buildQueryString({ workspace_id: workspaceId })}`
    ),
    artifacts: (limit = 80) =>
      `/api/artifacts${buildQueryString({ workspace_id: workspaceId, limit })}`,
    artifactDetail: (artifactId) =>
      `/api/artifacts/${encodeURIComponent(artifactId)}`,
    artifactFile: (artifactId) =>
      `/api/artifacts/${encodeURIComponent(artifactId)}/file`,
    artifactContent: (artifactId) =>
      `/api/artifacts/${encodeURIComponent(artifactId)}/content`,
    notifications: (limit = 80) =>
      `/api/notifications${buildQueryString({ workspace_id: workspaceId, limit })}`,
    activity: (limit = 80, traceId: string | null = null) =>
      `/api/activity/timeline${buildQueryString({ workspace_id: workspaceId, limit, trace_id: traceId })}`,
    pilotProofReadiness: (days = 30, limit = 1000) =>
      `/api/pilot/proof/readiness${buildQueryString({ workspace_id: workspaceId, days, limit })}`,
    pilotProofCaseStudy: (days = 30, limit = 1000) =>
      `/api/pilot/proof/case-study${buildQueryString({ workspace_id: workspaceId, days, limit })}`,
    pilotProofInvestorMemo: (days = 30, limit = 1000) =>
      `/api/pilot/proof/investor-memo${buildQueryString({ workspace_id: workspaceId, days, limit })}`,
    pilotProofAdsReadiness: (days = 30, limit = 1000) =>
      `/api/pilot/proof/ads-readiness${buildQueryString({ workspace_id: workspaceId, days, limit })}`,
    sageMemory:
      `/api/sage-memory${buildQueryString({ workspace_id: workspaceId })}`,
    sageProfile:
      `/api/sage-profile${buildQueryString({ workspace_id: workspaceId })}`,
    sageHeartbeat:
      `/api/sage-heartbeat${buildQueryString({ workspace_id: workspaceId })}`,
    sageSkills:
      `/api/sage-skills${buildQueryString({ workspace_id: workspaceId })}`,
    sageCapabilities:
      `/api/sage-capabilities${buildQueryString({ workspace_id: workspaceId })}`,
    sageProfileBootstrapAnswer:
      '/api/sage-profile/bootstrap/answer',
    sageMemoryStoragePolicy:
      `/api/sage-memory/storage-policy${buildQueryString({ workspace_id: workspaceId })}`,
    sageMemoryExport:
      `/api/sage-memory/export${buildQueryString({ workspace_id: workspaceId })}`,
    sageMemoryWipe: '/api/sage-memory/wipe',
    sageContextFiles:
      `/api/sage-context-files${buildQueryString({ workspace_id: workspaceId })}`,
    sageContextFile: (filename) =>
      `/api/sage-context-files/${filename.split('/').map((segment) => encodeURIComponent(segment)).join('/')}`,
    sageMemoryEntries: '/api/sage-memory/entries',
    sageMemoryEntry: (entryId) =>
      `/api/sage-memory/entries/${encodeURIComponent(entryId)}`,
    sageMemoryPin: (entryId) =>
      `/api/sage-memory/entries/${encodeURIComponent(entryId)}/pin`,
    sageServices:
      `/api/sage-services${buildQueryString({ workspace_id: workspaceId })}`,
    sageServiceProfile: (serviceId) =>
      `/api/sage-services/${encodeURIComponent(serviceId)}/profile`,
    sageServiceEntries: (serviceId) =>
      `/api/sage-services/${encodeURIComponent(serviceId)}/entries`,
    sageServiceEntry: (serviceId, entryId) =>
      `/api/sage-services/${encodeURIComponent(serviceId)}/entries/${encodeURIComponent(entryId)}`,
    sageServicePin: (serviceId, entryId) =>
      `/api/sage-services/${encodeURIComponent(serviceId)}/entries/${encodeURIComponent(entryId)}/pin`,
    appsInstalled: '/apps/installed',
    appsStore: '/apps/store',
    appsUpdates: '/apps/updates',
    appsInstall: '/apps/install',
    appsUninstall: '/apps/uninstall',
    appsUpdate: '/apps/update',
    agentDefinitions: `/agent-registry/definitions${buildQueryString({ workspace_id: workspaceId })}`,
    agentInstalls: `/agent-registry/installs${buildQueryString({ workspace_id: workspaceId })}`,
    runtimeAttachments: `/agent-registry/runtime-attachments${buildQueryString({ workspace_id: workspaceId })}`,
    runtimeTargets: `/agent-registry/runtime-targets${buildQueryString({ workspace_id: workspaceId })}`,
    mcpServers: `/agent-registry/mcp/servers${buildQueryString({ workspace_id: workspaceId })}`,
    mcpServer: (serverId: string) =>
      `/agent-registry/mcp/servers/${encodeURIComponent(serverId)}`,
    mcpServerRefresh: (serverId: string) =>
      `/agent-registry/mcp/servers/${encodeURIComponent(serverId)}/refresh`,
    mcpToolApprove: (serverId: string) =>
      `/agent-registry/mcp/servers/${encodeURIComponent(serverId)}/tools/approve`,
    providers: `/api/providers${buildQueryString({ workspace_id: workspaceId })}`,
    providersCatalog: `/api/providers/catalog${buildQueryString({ workspace_id: workspaceId })}`,
    workspaceAiRoute: `/api/workspaces/${encodeURIComponent(workspaceId)}/ai-route`,
    workspaceAiRouteDefault: `/api/workspaces/${encodeURIComponent(workspaceId)}/ai-route/default`,
    providerModels: (providerId: string, profileId: string | null = null) =>
      `/api/providers/${encodeURIComponent(providerId)}/models${buildQueryString({ workspace_id: workspaceId, profile_id: profileId })}`,
    workspaceProviderModelsRefresh: (providerId: string) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/providers/${encodeURIComponent(providerId)}/models/refresh`,
    workspaceTransparencySettings: `/api/workspaces/${encodeURIComponent(workspaceId)}/transparency-settings`,
    workspaceRuntimeSessions: (limit = 50) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/runtime-sessions${buildQueryString({ limit })}`,
    speechToText: '/api/stt',
    providerProfiles: (provider = null) =>
      `/api/providers/profiles${buildQueryString({ workspace_id: workspaceId, provider })}`,
    providerProfilesRoot: '/api/providers/profiles',
    credentialsVault: `/api/credentials/vault${buildQueryString({ workspace_id: workspaceId })}`,
    connectorsVault: `/api/connectors/vault${buildQueryString({ workspace_id: workspaceId })}`,
    connectorVaultCredential: (credentialId: string) =>
      `/api/connectors/vault/${encodeURIComponent(credentialId)}${buildQueryString({ workspace_id: workspaceId })}`,
    workspaceProviderCredentials: `/api/workspaces/${encodeURIComponent(workspaceId)}/providers/credentials`,
    sageToolPolicy: `/api/workspaces/${encodeURIComponent(workspaceId)}/sage/tool-policy`,
    sageChatAttachments: `/api/sage-chat/attachments${buildQueryString({ workspace_id: workspaceId })}`,
    runInstalledAgent: (installId) => `/agents/${encodeURIComponent(installId)}/run`,
    agentTraces: (filters = {}) =>
      `/api/agent-traces${buildQueryString({
        workspace_id: workspaceId,
        thread_id: filters.threadId,
        run_id: filters.runId,
        surface: filters.surface,
        outcome: filters.outcome,
        root_agent_id: filters.rootAgentId,
        limit: filters.limit,
      })}`,
    agentTraceDetail: (traceId) =>
      `/api/agent-traces/${encodeURIComponent(traceId)}${buildQueryString({ workspace_id: workspaceId })}`,
    agentTraceStream: (traceId) =>
      `/api/agent-traces/${encodeURIComponent(traceId)}/stream${buildQueryString({ workspace_id: workspaceId })}`,
    deployedAgents: (deploymentState) =>
      `/api/deployed-agents${buildQueryString({ workspace_id: workspaceId, deployment_state: deploymentState })}`,
    deployedAgentTelegramReadiness: (deployedAgentId) =>
      `/api/deployed-agents/telegram-readiness${buildQueryString({
        workspace_id: workspaceId,
        deployed_agent_id: deployedAgentId,
      })}`,
    deployedAgentDetail: (deployedAgentId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}${buildQueryString({ workspace_id: workspaceId })}`,
    deployedAgentDeploy: (deployedAgentId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/deploy`,
    deployedAgentPause: (deployedAgentId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/pause`,
    deployedAgentRuntimeKill: (deployedAgentId, sessionId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/runtime-sessions/${encodeURIComponent(sessionId)}/kill`,
    deployedAgentAuditExport: (deployedAgentId, limit = 500) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/audit-export${buildQueryString({
        workspace_id: workspaceId,
        limit,
      })}`,
    deployedAgentTestTurn: (deployedAgentId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/test-turn`,
    deployedAgentKnowledgeVerify: (deployedAgentId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/knowledge/verify`,
    deployedAgentKnowledgeFiles: (deployedAgentId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/knowledge/files`,
    deployedAgentAnalyticsRoster:
      `/api/deployed-agents/analytics${buildQueryString({ workspace_id: workspaceId })}`,
    deployedAgentAnalyticsDetail: (deployedAgentId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/analytics${buildQueryString({ workspace_id: workspaceId })}`,
    deployedAgentAdminDashboard: (deployedAgentId, limit = 50, offset = 0) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/admin-dashboard${buildQueryString({
        workspace_id: workspaceId,
        limit,
        offset,
      })}`,
    deployedAgentBusinessInsights: (deployedAgentId, status = null, limit = 50, offset = 0) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/business-insights${buildQueryString({
        workspace_id: workspaceId,
        status,
        limit,
        offset,
      })}`,
    deployedAgentBusinessInsightReview: (deployedAgentId, insightId, action) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/business-insights/${encodeURIComponent(insightId)}/${action}${buildQueryString({
        workspace_id: workspaceId,
      })}`,
    studioAgentSurfaces:
      `/api/studio/agent-surfaces${buildQueryString({ workspace_id: workspaceId })}`,
    studioChannelCatalog:
      `/api/studio/channel-catalog${buildQueryString({ workspace_id: workspaceId })}`,
    connectionsCatalog: (surface = null) =>
      `/api/connections/catalog${buildQueryString({ workspace_id: workspaceId, surface })}`,
    connectionsStatus: (surface = null, selectedGatewayId = null) =>
      `/api/connections/status${buildQueryString({
        workspace_id: workspaceId,
        surface,
        selected_gateway_id: selectedGatewayId,
      })}`,
    sageAgentComputerSelection:
      `/api/connections/sage-agent-computer${buildQueryString({ workspace_id: workspaceId })}`,
    connectionSetupStart: (connectionId) =>
      `/api/connections/${encodeURIComponent(connectionId)}/setup/start`,
    connectionTest: (connectionId) =>
      `/api/connections/${encodeURIComponent(connectionId)}/test`,
    connectionVerify: (connectionId) =>
      `/api/connections/${encodeURIComponent(connectionId)}/verify`,
    connectionDisconnect: (connectionId) =>
      `/api/connections/${encodeURIComponent(connectionId)}/disconnect`,
    studioChannelAccounts:
      `/api/studio/channel-accounts${buildQueryString({ workspace_id: workspaceId })}`,
    studioAgentChannelBindings: (deployedAgentId) =>
      `/api/studio/agents/${encodeURIComponent(deployedAgentId)}/channel-bindings${buildQueryString({ workspace_id: workspaceId })}`,
    studioAgentChannelBindingAction: (deployedAgentId, channelKey, action) =>
      `/api/studio/agents/${encodeURIComponent(deployedAgentId)}/channel-bindings/${encodeURIComponent(channelKey)}/${action}`,
    studioExternalAgents:
      `/api/studio/external-agents${buildQueryString({ workspace_id: workspaceId })}`,
    studioExternalAgent: (externalAgentId) =>
      `/api/studio/external-agents/${encodeURIComponent(externalAgentId)}${buildQueryString({ workspace_id: workspaceId })}`,
    studioExternalAgentRefreshManifest: (externalAgentId) =>
      `/api/studio/external-agents/${encodeURIComponent(externalAgentId)}/refresh-manifest`,
    studioExternalAgentChatTurn: (externalAgentId) =>
      `/api/studio/external-agents/${encodeURIComponent(externalAgentId)}/chat-turn`,
    studioExternalAgentSection: (externalAgentId, sectionId) =>
      `/api/studio/external-agents/${encodeURIComponent(externalAgentId)}/sections/${encodeURIComponent(sectionId)}${buildQueryString({ workspace_id: workspaceId })}`,
    studioExternalAgentDisconnect: (externalAgentId) =>
      `/api/studio/external-agents/${encodeURIComponent(externalAgentId)}/disconnect`,
    deployedAgentMemory: (deployedAgentId, limit = 50, offset = 0) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/memory${buildQueryString({
        workspace_id: workspaceId,
        limit,
        offset,
      })}`,
    deployedAgentConversations: (deployedAgentId, limit = 50, offset = 0) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/conversations${buildQueryString({
        workspace_id: workspaceId,
        limit,
        offset,
      })}`,
    deployedAgentConversationDetail: (deployedAgentId, sessionId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/conversations/${encodeURIComponent(sessionId)}${buildQueryString({
        workspace_id: workspaceId,
      })}`,
    deployedAgentExternalUserDelete: (deployedAgentId, externalUserId) =>
      `/api/deployed-agents/${encodeURIComponent(deployedAgentId)}/external-users/${encodeURIComponent(externalUserId)}/delete`,
    marketplaceAgents: (filters = {}) =>
      `/api/marketplace/agents${buildQueryString({
        category: filters.category,
        cost_tier: filters.costTier,
        limit: filters.limit,
        offset: filters.offset,
      })}`,
    discoveryFeed: (options = {}) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/discovery/feed${buildQueryString({
        filter: options.filter,
      })}`,
    discoveryAdopt: (feedItemId) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/discovery/items/${encodeURIComponent(feedItemId)}/adopt`,
    marketplacePackages: (options = {}) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/marketplace/packages${buildQueryString({
        kind: options.kind,
        runtime_type: options.runtimeType,
        include_review_queue: options.includeReviewQueue ? 'true' : undefined,
      })}`,
    studioTemplates: `/api/workspaces/${encodeURIComponent(workspaceId)}/studio/templates`,
    marketplaceProviderRegister: `/api/workspaces/${encodeURIComponent(workspaceId)}/marketplace/providers`,
    marketplaceAppRegister: `/api/workspaces/${encodeURIComponent(workspaceId)}/marketplace/apps`,
    marketplaceAppSubmissions: `/api/workspaces/${encodeURIComponent(workspaceId)}/marketplace/app-submissions`,
    marketplacePackageReview: (packageId) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/marketplace/packages/${encodeURIComponent(packageId)}/review`,
    marketplacePackageInstall: (packageId) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/marketplace/packages/${encodeURIComponent(packageId)}/install`,
    platformAnalytics: `/api/platform-analytics${buildQueryString({ workspace_id: workspaceId })}`,
    workspaceRouting: `/api/workspaces/${encodeURIComponent(workspaceId)}/routing`,
    workspaceMembers: `/api/workspaces/${encodeURIComponent(workspaceId)}/members`,
    workspaceMemberInvites: `/api/workspaces/${encodeURIComponent(workspaceId)}/members/invites`,
    workspaceMemberInvite: (inviteId) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/members/invites/${encodeURIComponent(inviteId)}`,
    workspaceMember: (userId) => `/api/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
    workspacePolicies: `/api/workspaces/${encodeURIComponent(workspaceId)}/policies`,
    usageSummary: (period = 'all') =>
      `/api/usage/summary${buildQueryString({ workspace_id: workspaceId, period })}`,
    billingSummary: `/api/billing/summary${buildQueryString({ workspace_id: workspaceId })}`,
    billingCheckout: '/api/billing/checkout',
    billingPortal: '/api/billing/portal',
    billingCreditPurchase: '/api/billing/credits/purchase',
    billingCreditBalance: `/api/billing/credits/balance${buildQueryString({ workspace_id: workspaceId })}`,
    billingCreditUsageHistory: (limit = 50) =>
      `/api/billing/credits/usage-history${buildQueryString({ workspace_id: workspaceId, limit })}`,
    billingCreditUsage: (options: CreditUsageQueryOptions = {}) =>
      `/api/billing/credits/usage${buildQueryString({
        workspace_id: workspaceId,
        limit: options.limit ?? 50,
        surface: options.surface || undefined,
        credit_type: options.creditType || undefined,
        payer: options.payer || undefined,
        agent_id: options.agentId || undefined,
        app_id: options.appId || undefined,
        thread_id: options.threadId || undefined,
        run_id: options.runId || undefined,
      })}`,
    notificationsStream: (options = {}) =>
      `/api/notifications${buildQueryString({
        workspace_id: workspaceId,
        stream: 'true',
        since_id: options.sinceId,
        since_ts: options.sinceTs,
        include_backlog: options.includeBacklog ? 'true' : undefined,
        limit: options.limit ?? 120,
      })}`,
    sageTurnsStream: (workspaceId: string) =>
      `/api/workstation/${encodeURIComponent(workspaceId)}/sage/turns/stream`,
    channelEventsStream: (options = {}) =>
      `/api/events/inbox/stream${buildQueryString({
        workspace_id: workspaceId,
        since_id: options.sinceId,
        since_ts: options.sinceTs,
        include_backlog: options.includeBacklog ? 'true' : undefined,
        limit: options.limit ?? 120,
      })}`,
  };
}

function extractErrorDetail(payload: unknown): unknown {
  if (payload && typeof payload === 'object' && 'detail' in (payload as Record<string, unknown>)) {
    return (payload as Record<string, unknown>).detail;
  }
  return payload;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function extractPlatformError(payload: unknown, detail: unknown): Record<string, unknown> | null {
  const payloadRecord = asRecord(payload);
  const payloadError = asRecord(payloadRecord?.error);
  if (payloadError) {
    return payloadError;
  }
  const detailRecord = asRecord(detail);
  const detailError = asRecord(detailRecord?.error);
  if (detailError) {
    return detailError;
  }
  return null;
}

function extractErrorCode(payload: unknown, detail: unknown, platformError: Record<string, unknown> | null): string | null {
  if (payload && typeof payload === 'object' && typeof (payload as Record<string, unknown>).code === 'string') {
    return String((payload as Record<string, unknown>).code).trim() || null;
  }
  if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).code === 'string') {
    return String((detail as Record<string, unknown>).code).trim() || null;
  }
  if (platformError && typeof platformError.code === 'string') {
    return String(platformError.code).trim() || null;
  }
  return null;
}

function fallbackErrorMessage(status: number): string {
  if (status === 0) {
    return 'The request could not connect. Retry when ready.';
  }
  if (status === 401) {
    return 'Your session expired. Sign in again and retry.';
  }
  if (status === 403) {
    return 'Sage cannot run that request in this workspace right now.';
  }
  if (status === 429) {
    return 'Capacity is busy right now. Retry in a moment.';
  }
  if (status === 404) {
    return 'The requested item could not be found.';
  }
  if (status === 409) {
    return 'Session context changed. Retry your message once more.';
  }
  if (status >= 500) {
    return 'The request could not finish. Retry when ready.';
  }
  return 'Request failed. Please try again.';
}

function extractErrorMessage(detail: unknown, platformError: Record<string, unknown> | null): string {
  const looksLikeHtmlDocument = (value: string): boolean => /<!doctype html>|<html[\s>]/i.test(value);
  if (typeof detail === 'string' && detail.trim()) {
    const normalized = detail.trim();
    return looksLikeHtmlDocument(normalized) ? '' : normalized;
  }
  const detailRecord = asRecord(detail);
  if (detailRecord) {
    const detailMessage = detailRecord.message ?? detailRecord.detail ?? detailRecord.error;
    if (typeof detailMessage === 'string' && detailMessage.trim()) {
      const normalized = detailMessage.trim();
      return looksLikeHtmlDocument(normalized) ? '' : normalized;
    }
  }
  if (platformError && typeof platformError.message === 'string' && platformError.message.trim()) {
    const normalized = platformError.message.trim();
    return looksLikeHtmlDocument(normalized) ? '' : normalized;
  }
  return '';
}

function inferRetryableFromStatus(status: number): boolean {
  return status === 0 || status === 408 || status === 425 || status === 429 || status >= 500;
}

function extractRetryable(
  status: number,
  payload: unknown,
  detail: unknown,
  platformError: Record<string, unknown> | null,
): boolean {
  if (typeof platformError?.retryable === 'boolean') {
    return platformError.retryable;
  }
  const detailRecord = asRecord(detail);
  if (typeof detailRecord?.retryable === 'boolean') {
    return detailRecord.retryable;
  }
  const payloadRecord = asRecord(payload);
  if (typeof payloadRecord?.retryable === 'boolean') {
    return payloadRecord.retryable;
  }
  return inferRetryableFromStatus(status);
}

function extractRetryAfterSeconds(
  payload: unknown,
  detail: unknown,
  platformError: Record<string, unknown> | null,
): number | null {
  const maybeRetryAfter = platformError?.details && typeof platformError.details === 'object'
    ? (platformError.details as Record<string, unknown>).retry_after_seconds
    : null;
  const candidates = [
    maybeRetryAfter,
    asRecord(detail)?.retry_after_seconds,
    asRecord(payload)?.retry_after_seconds,
  ];
  for (const value of candidates) {
    const parsed = typeof value === 'number' ? value : Number(value);
    if (Number.isFinite(parsed) && parsed >= 0) {
      return parsed;
    }
  }
  return null;
}

const READ_REQUEST_POLICY: WorkstationRequestPolicy = {
  timeoutMs: 10_000,
  retryCount: 1,
  retryOnStatuses: [408, 425, 500, 502, 503, 504],
  refreshSessionOn401: true,
};

const PROVIDER_READ_REQUEST_POLICY: WorkstationRequestPolicy = {
  timeoutMs: 25_000,
  retryCount: 1,
  retryOnStatuses: [408, 425, 500, 502, 503, 504],
  refreshSessionOn401: true,
};

const AGENT_STUDIO_READ_REQUEST_POLICY: WorkstationRequestPolicy = {
  timeoutMs: 25_000,
  retryCount: 2,
  retryOnStatuses: [408, 425, 500, 502, 503, 504],
  refreshSessionOn401: true,
};

const WRITE_REQUEST_POLICY: WorkstationRequestPolicy = {
  timeoutMs: 15_000,
  retryCount: 0,
  refreshSessionOn401: true,
};

const CHAT_TURN_PERSIST_REQUEST_POLICY: WorkstationRequestPolicy = {
  timeoutMs: 60_000,
  retryCount: 0,
  refreshSessionOn401: true,
};

const STREAM_REQUEST_POLICY: WorkstationRequestPolicy = {
  timeoutMs: 120_000,
  retryCount: 0,
  refreshSessionOn401: true,
};

const STREAM_TRANSPORT_RETRY_STATUSES = new Set([502, 503, 504]);
const STREAM_TRANSPORT_RETRY_CODES = new Set([
  'request_timeout',
  'stream_incomplete',
  'transport_failure',
]);
const MAX_STREAM_TRANSPORT_RETRIES = 1;

function resolveRequestPolicy(
  init: RequestInit | undefined,
  policy: WorkstationRequestPolicy | undefined,
): WorkstationRequestPolicy {
  const method = String(init?.method ?? 'GET').trim().toUpperCase();
  const base = ['GET', 'HEAD'].includes(method) ? READ_REQUEST_POLICY : WRITE_REQUEST_POLICY;
  return {
    ...base,
    ...(policy ?? {}),
  };
}

function shouldRetryTurnStreamFailure(error: unknown, attempt: number): boolean {
  if (attempt >= MAX_STREAM_TRANSPORT_RETRIES) {
    return false;
  }
  if (!(error instanceof WorkstationClientError)) {
    return false;
  }
  if (error.code === 'stream_aborted') {
    return false;
  }
  if (STREAM_TRANSPORT_RETRY_STATUSES.has(error.status)) {
    return true;
  }
  return Boolean(error.code && STREAM_TRANSPORT_RETRY_CODES.has(error.code));
}

function normalizeTransportFailure(error: unknown): WorkstationClientError {
  if (error instanceof WorkstationClientError) {
    return error;
  }
  const baseMessage =
    error instanceof Error && error.message.trim()
      ? error.message
      : 'The workstation request failed before the server responded.';

  const isAbort = (error instanceof Error && error.name === 'AbortError') || /aborted/i.test(baseMessage);
  const isTimeout = /timed out/i.test(baseMessage);

  let message = 'The request could not connect. Retry when ready.';
  let code = 'transport_failure';
  let retryable = true;

  if (isTimeout) {
    message = 'Sage took too long to respond. Please try again.';
    code = 'request_timeout';
  } else if (isAbort) {
    message = 'The request was cancelled.';
    code = 'request_aborted';
    retryable = false;
  }

  return new WorkstationClientError(message, 0, null, code, {
    retryable,
  });
}

function normalizeClientError(status: number, payload: unknown): WorkstationClientError {
  const detail = extractErrorDetail(payload);
  const platformError = extractPlatformError(payload, detail);
  const code = extractErrorCode(payload, detail, platformError);
  const rawMessage = extractErrorMessage(detail, platformError);
  const normalizedMessage =
    status === 403 || status === 429 || status >= 500 || /rate.?limit/i.test(String(code ?? ''))
      ? fallbackErrorMessage(status === 0 ? 429 : status)
      : rawMessage || fallbackErrorMessage(status);
  return new WorkstationClientError(normalizedMessage, status, detail, code, {
    retryable: extractRetryable(status, payload, detail, platformError),
    retryAfterSeconds: extractRetryAfterSeconds(payload, detail, platformError),
    errorClass: typeof platformError?.error_class === 'string' ? platformError.error_class : null,
  });
}

function mergeJsonHeaders(headers?: HeadersInit): Headers {
  const merged = new Headers(headers ?? {});
  if (!merged.has('content-type')) {
    merged.set('content-type', 'application/json');
  }
  return merged;
}

async function readResponsePayload(response: Response): Promise<unknown> {
  try {
    const text = await response.text();
    if (!text.trim()) {
      return null;
    }
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  } catch (error) {
    throw normalizeTransportFailure(error);
  }
}

function appendWorkspaceScope(path: string, workspaceId: string): string {
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}workspace_id=${encodeURIComponent(workspaceId)}`;
}

function readCsrfTokenFromCookie(): string | null {
  if (typeof document === 'undefined') {
    return null;
  }
  const prefix = 'empyralis_csrf_token=';
  for (const chunk of document.cookie.split(';')) {
    const part = chunk.trim();
    if (part.startsWith(prefix)) {
      const value = part.slice(prefix.length).trim();
      return value ? decodeURIComponent(value) : null;
    }
  }
  return null;
}

function resolveAbsoluteUrl(baseUrl: string, path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }
  const root = baseUrl.replace(/\/+$/, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${root}${suffix}`;
}

function requestJsonCacheKey(path: string, init: RequestInit, allowStatuses: number[]): string {
  const method = String(init.method || 'GET').trim().toUpperCase() || 'GET';
  const allow = allowStatuses.length > 0 ? allowStatuses.slice().sort((a, b) => a - b).join(',') : 'none';
  return `request-json:${method}:${path}:allow:${allow}`;
}

function isCacheableRequestJson(init: RequestInit): boolean {
  const method = String(init.method || 'GET').trim().toUpperCase() || 'GET';
  return method === 'GET' && init.body == null;
}

function sessionCacheKey(
  scope: WorkstationClientScope,
  threadId: string,
  channel: string,
  actorId: string,
): string {
  return `workstation:session:${scope.kernelKey}:${channel}:${threadId}:${actorId}`;
}

export function createWorkstationClient(
  dependencies: WorkstationClientDependencies,
): WorkstationClient {
  const { scope, transport, queryClient, realtime, getApiBaseUrl } = dependencies;
  const paths = buildWorkstationApiPaths(scope.workspaceId);

  async function requestJson<T>({
    path,
    init = {},
    allowStatuses = [],
    policy,
  }: WorkstationClientRequestOptions): Promise<T | null> {
    const method = String(init.method || 'GET').trim().toUpperCase() || 'GET';
    const execute = async (signal?: AbortSignal): Promise<T | null> => {
      const requestInit = signal && !init.signal ? { ...init, signal } : init;
      const response = await transport.request(path, requestInit, resolveRequestPolicy(requestInit, policy));
      const payload = await readResponsePayload(response);

      if (!response.ok && allowStatuses.includes(response.status)) {
        return null;
      }

      if (!response.ok) {
        throw normalizeClientError(response.status, payload);
      }

      return payload as T;
    };

    try {
      if (queryClient.readThrough && isCacheableRequestJson(init)) {
        return await queryClient.readThrough<T | null>(
          requestJsonCacheKey(path, init, allowStatuses),
          ({ signal }) => execute(signal),
          10_000,
        );
      }
      const payload = await execute();
      if (method !== 'GET') {
        queryClient.invalidate?.();
      }
      return payload;
    } catch (error) {
      throw normalizeTransportFailure(error);
    }
  }

  async function createSession({
    actor,
    threadId,
    channel = 'web',
    source = 'workstation_client',
    forceNew = false,
    existingSession = null,
  }: {
    actor: WorkstationSessionActor;
    threadId: string;
    channel?: string;
    source?: string;
    forceNew?: boolean;
    existingSession?: WorkstationSessionRecord | null;
  }): Promise<WorkstationSessionRecord> {
    if (!forceNew && isActiveSessionRecord(existingSession)) {
      return existingSession;
    }

    const cacheKey = sessionCacheKey(scope, threadId, channel, actor.id);
    if (!forceNew) {
      const cached = queryClient.peek<WorkstationSessionRecord>(cacheKey);
      if (isActiveSessionRecord(cached)) {
        return cached;
      }
      const staleCached = cached as WorkstationSessionRecord | null;
      const cachedSessionId = readString(staleCached?.session_id);
      if (cachedSessionId) {
        queryClient.invalidate?.(cacheKey);
      }
    }

    // MAN-30: rewired to v2 backend POST /session (BYOK/trial auth session)
    const sessionPayload = await requestJson<Record<string, unknown>>({
      path: '/session',
      init: {
        method: 'POST',
        headers: mergeJsonHeaders(),
        body: JSON.stringify({}),
      },
    });

    const session: WorkstationSessionRecord = {
      session_id: readString(sessionPayload?.session_id) || 'unknown',
      workspace_id: scope.workspaceId,
      channel,
      actor: actor as Record<string, unknown>,
      status: 'active',
    };

    queryClient.set(cacheKey, session);
    return session;
  }

  // MAN-30: no-op — v2 backend POST /chat handles persistence internally
  async function persistUserTurn({
    actor: _actor,
    sessionId: _sessionId,
    threadId: _threadId,
    message: _message,
    channel: _channel = 'web',
    runtimeProfileId: _runtimeProfileId = null,
    metadata: _metadata = {},
    clientRequestId: _clientRequestId = null,
    attachments: _attachments = [],
  }: {
    actor: WorkstationSessionActor;
    sessionId: string;
    threadId: string;
    message: string;
    channel?: string;
    runtimeProfileId?: string | null;
    metadata?: Record<string, unknown>;
    clientRequestId?: string | null;
    attachments?: SageChatAttachment[];
  }): Promise<Record<string, unknown>> {
    return {};
  }

  async function submitTurn({
    actor,
    sessionId,
    threadId,
    message,
    channel = 'web',
    source = 'workstation_client',
    runtimeTarget = null,
    machineTarget = null,
    provider = null,
    model = null,
    reasoningEffort = null,
    policyContext = {},
    clientRequestId = null,
    attachments = [],
  }: {
    actor: WorkstationSessionActor;
    sessionId: string;
    threadId: string;
    message: string;
    channel?: string;
    source?: string;
    runtimeTarget?: string | null;
    machineTarget?: string | null;
    provider?: string | null;
    model?: string | null;
    reasoningEffort?: string | null;
    policyContext?: Record<string, unknown>;
    clientRequestId?: string | null;
    attachments?: SageChatAttachment[];
  }): Promise<WorkstationTurnResponse> {
    const resolvedRequestId = readString(clientRequestId) || createClientRequestId();
    return (await requestJson<WorkstationTurnResponse>({
      path: paths.turnSubmit,
      init: {
        method: 'POST',
        headers: mergeJsonHeaders(),
        body: JSON.stringify({
          tenant_id: scope.tenantId,
          workspace_id: scope.workspaceId,
          thread_id: threadId,
          session_id: sessionId,
          client_request_id: resolvedRequestId,
          channel,
          actor,
          message,
          provider: provider ?? undefined,
          model: model ?? undefined,
          reasoning_effort: reasoningEffort ?? undefined,
          machine_target: machineTarget ?? undefined,
          attachments: attachments ?? [],
          context_hints: {
            source,
            thread_id: threadId,
            request_id: resolvedRequestId,
            provider: provider ?? undefined,
            model: model ?? undefined,
            reasoning_effort: reasoningEffort ?? undefined,
            force_direct_chat: true,
          },
          execution_mode: 'sync',
          response_mode: 'artifact',
          policy_context: {
            ...(policyContext ?? {}),
            ...(runtimeTarget ? { execution_target: runtimeTarget } : {}),
          },
        }),
      },
    })) as WorkstationTurnResponse;
  }

  function parseStreamPayload(data: string): Record<string, unknown> {
    const text = String(data ?? '').trim();
    if (!text) {
      return {};
    }
    try {
      const parsed = JSON.parse(text);
      return parsed && typeof parsed === 'object'
        ? parsed as Record<string, unknown>
        : { value: parsed };
    } catch {
      return { value: text };
    }
  }

  function parseSseBlock(block: string): WorkstationTurnStreamEvent | null {
    const lines = block.split('\n');
    let eventName = 'message';
    let eventId: string | null = null;
    const dataLines: string[] = [];

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (!line || line.startsWith(':')) {
        continue;
      }
      if (line.startsWith('event:')) {
        eventName = line.slice('event:'.length).trim() || 'message';
        continue;
      }
      if (line.startsWith('id:')) {
        eventId = line.slice('id:'.length).trim() || null;
        continue;
      }
      if (line.startsWith('data:')) {
        dataLines.push(line.slice('data:'.length).trimStart());
      }
    }

    if (dataLines.length === 0) {
      return null;
    }

    return {
      id: eventId,
      event: eventName,
      payload: parseStreamPayload(dataLines.join('\n')),
    };
  }

  function normalizeStreamTurnResponse(
    payload: Record<string, unknown>,
    options: {
      threadId: string;
      sessionId: string;
      traceId: string | null;
      fallbackReply: string;
    },
  ): WorkstationTurnResponse {
    const {
      threadId,
      sessionId,
      traceId,
      fallbackReply,
    } = options;
    const metadata = payload.metadata && typeof payload.metadata === 'object'
      ? { ...(payload.metadata as Record<string, unknown>) }
      : {};
    if (traceId) {
      metadata.trace_id = traceId;
    }
    const reply = typeof payload.reply === 'string' && payload.reply.trim()
      ? payload.reply
      : fallbackReply;
    return {
      ...(payload as WorkstationTurnResponse),
      reply,
      status: typeof payload.status === 'string' && payload.status.trim()
        ? payload.status
        : 'completed',
      thread_id: typeof payload.thread_id === 'string' && payload.thread_id.trim()
        ? payload.thread_id
        : threadId,
      session_id: typeof payload.session_id === 'string' && payload.session_id.trim()
        ? payload.session_id
        : sessionId,
      metadata,
    };
  }

  // MAN-30: rewired to v2 backend POST /chat (SSE stream)
  async function submitTurnStream({
    actor: _actor,
    sessionId,
    threadId,
    message,
    channel: _channel = 'web',
    source: _source = 'workstation_client',
    runtimeTarget: _runtimeTarget = null,
    machineTarget: _machineTarget = null,
    provider: _provider = null,
    model: _model = null,
    reasoningEffort: _reasoningEffort = null,
    policyContext: _policyContext = {},
    onEvent,
    clientRequestId: _clientRequestId = null,
    abortHandle = null,
    attachments: _attachments = [],
  }: {
    actor: WorkstationSessionActor;
    sessionId: string;
    threadId: string;
    message: string;
    channel?: string;
    source?: string;
    runtimeTarget?: string | null;
    machineTarget?: string | null;
    provider?: string | null;
    model?: string | null;
    reasoningEffort?: string | null;
    policyContext?: Record<string, unknown>;
    onEvent?: (event: WorkstationTurnStreamEvent) => void;
    clientRequestId?: string | null;
    abortHandle?: WorkstationTurnStreamAbortHandle | null;
    attachments?: SageChatAttachment[];
  }): Promise<WorkstationTurnResponse> {
    let response: Response;
    try {
      response = await transport.request(
        '/chat',
        {
          method: 'POST',
          signal: abortHandle?.signal,
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ message }),
        },
        resolveRequestPolicy({ method: 'POST' }, STREAM_REQUEST_POLICY),
      );
    } catch (error) {
      if (abortHandle?.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
        throw new WorkstationClientError(
          'Sage stopped before finishing the response.',
          0, null, 'stream_aborted', { retryable: false },
        );
      }
      throw normalizeTransportFailure(error);
    }

    if (!response.ok) {
      const payload = await readResponsePayload(response);
      throw normalizeClientError(response.status, payload);
    }

    const contentType = response.headers.get('content-type') ?? '';
    if (!/text\/event-stream/i.test(contentType)) {
      const payload = await readResponsePayload(response);
      if (payload && typeof payload === 'object') {
        const reply = readString((payload as Record<string, unknown>).reply);
        return {
          reply, status: 'completed', thread_id: threadId, session_id: sessionId,
          metadata: {},
        };
      }
      throw new WorkstationClientError(
        'The chat stream did not return an event stream.', 0, payload, 'stream_protocol_error',
      );
    }

    if (!response.body) {
      throw new WorkstationClientError(
        'The chat stream did not include a readable body.', 0, null, 'stream_protocol_error',
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let streamedReply = '';
    let finalPayload: Record<string, unknown> | null = null;

    while (true) {
      let readResult: ReadableStreamReadResult<Uint8Array>;
      try {
        readResult = await reader.read();
      } catch (error) {
        if (abortHandle?.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
          throw new WorkstationClientError(
            'Sage stopped before finishing the response.', 0, null, 'stream_aborted', { retryable: false },
          );
        }
        throw error;
      }
      const { done, value } = readResult;
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r/g, '');

      while (true) {
        const delim = buffer.indexOf('\n\n');
        if (delim < 0) break;
        const block = buffer.slice(0, delim);
        buffer = buffer.slice(delim + 2);

        // v2 backend SSE format: data: {"token":"...", ...}
        const dataMatch = block.match(/^data:\s*(.+)$/m);
        if (!dataMatch) continue;
        let dataPayload: Record<string, unknown>;
        try { dataPayload = JSON.parse(dataMatch[1]); }
        catch { continue; }

        if (typeof dataPayload.token === 'string') {
          // per-token chunk → legacy chunk event
          streamedReply += dataPayload.token;
          onEvent?.({ id: null, event: 'chunk', payload: { delta: dataPayload.token } });
        } else if (dataPayload.done) {
          // final event
          finalPayload = { status: 'completed', reply: streamedReply, thread_id: threadId, session_id: sessionId };
          onEvent?.({ id: null, event: 'final', payload: finalPayload });
        } else if (typeof dataPayload.error === 'string') {
          finalPayload = { status: 'error', reply: '', error: dataPayload.error, thread_id: threadId, session_id: sessionId };
          onEvent?.({ id: null, event: 'final', payload: finalPayload });
        } else if (dataPayload.trial_exhausted) {
          finalPayload = { status: 'exhausted', reply: readString(dataPayload.message), thread_id: threadId, session_id: sessionId };
          onEvent?.({ id: null, event: 'final', payload: finalPayload });
        }
      }

      if (done) break;
    }

    if (!finalPayload) {
      if (streamedReply.trim()) {
        return { status: 'incomplete', reply: streamedReply, thread_id: threadId, session_id: sessionId, metadata: { incomplete: true } };
      }
      throw new WorkstationClientError(
        'The chat stream ended before the final response arrived.', 0, null, 'stream_incomplete',
      );
    }

    return normalizeStreamTurnResponse(finalPayload, {
      threadId, sessionId, traceId: null, fallbackReply: streamedReply,
    });
  }

  return {
    scope,
    paths,
    requestJson,
    // MAN-30: rewired to v2 backend GET /chat/history
    listThreads: ({ includeTurns: _includeTurns = false, limit: _limit = 50 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: '/chat/history',
        init: { method: 'GET', headers: mergeJsonHeaders() },
        allowStatuses: [401],
      }).then((payload) => {
        // Transform v2 flat message list → legacy thread list format
        const messages = Array.isArray((payload as Record<string, unknown>)?.messages)
          ? (payload as Record<string, unknown>).messages as Array<Record<string, unknown>>
          : [];
        if (messages.length === 0) return { items: [], count: 0 };
        // Wrap all messages as a single "current" thread
        return {
          items: [{
            id: 'current',
            title: 'Current chat',
            created_at: (messages[0] as Record<string, unknown>)?.ts ?? new Date().toISOString(),
            turns: messages.map((m: Record<string, unknown>) => ({
              role: m.role ?? 'user',
              content: m.content ?? '',
            })),
          }],
          count: 1,
        };
      }).catch(() => ({ items: [], count: 0 })),
    // MAN-30: removed — v2 backend has no thread detail concept
    getThread: ({ threadId: _threadId, allowMissing: _allowMissing = false }) =>
      Promise.resolve({}),
    // MAN-30: removed (approval-free)
    listRuns: ({ limit: _limit = 80 } = {}) =>
      Promise.resolve({ items: [], count: 0 } as unknown as Record<string, unknown>),
    getRunDetail: ({ runId: _runId, allowMissing: _allowMissing = false }) =>
      Promise.resolve({}),
    listApprovals: ({ limit: _limit = 80 } = {}) =>
      Promise.resolve({ items: [], count: 0 } as unknown as Record<string, unknown>),
    getApprovalDetail: ({ approvalId: _approvalId, allowMissing: _allowMissing = false }) =>
      Promise.resolve({}),
    listArtifacts: ({ limit = 80 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.artifacts(limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getArtifactDetail: ({ artifactId, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.artifactDetail(artifactId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
    artifactFileUrl: (artifactId) => appendWorkspaceScope(paths.artifactFile(artifactId), scope.workspaceId),
    artifactDownloadUrl: (artifactId) =>
      appendWorkspaceScope(paths.artifactContent(artifactId), scope.workspaceId),
    listNotifications: ({ limit = 80 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.notifications(limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    markNotificationsRead: ({ notificationIds, markAll = false } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.notifications(),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            notification_ids: notificationIds ?? [],
            mark_all: markAll,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    // MAN-30: removed — v2 backend has no activity timeline
    listActivityTimeline: ({ limit: _limit = 80, traceId: _traceId = null } = {}) =>
      Promise.resolve({ items: [], count: 0 } as unknown as Record<string, unknown>),
    getPilotProofReadiness: ({ days = 30, limit = 1000 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.pilotProofReadiness(days, limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getPilotProofCaseStudy: ({ days = 30, limit = 1000 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.pilotProofCaseStudy(days, limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getPilotProofInvestorMemo: ({ days = 30, limit = 1000 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.pilotProofInvestorMemo(days, limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getPilotProofAdsReadiness: ({ days = 30, limit = 1000 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.pilotProofAdsReadiness(days, limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    // MAN-30: rewired to v2 backend GET /memory (read-only viewer)
    listSageMemory: () =>
      requestJson<Record<string, unknown>>({
        path: '/memory',
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getSageProfile: () =>
      requestJson<WorkstationSageProfileRecord>({
        path: paths.sageProfile,
        policy: READ_REQUEST_POLICY,
      }) as Promise<WorkstationSageProfileRecord>,
    // MAN-30: rewired to v2 backend GET /tasks (activity feed — no task engine)
    getSageHeartbeat: () =>
      requestJson<WorkstationSageHeartbeatRecord>({
        path: '/tasks',
        policy: READ_REQUEST_POLICY,
      }) as Promise<WorkstationSageHeartbeatRecord>,
    listSageSkills: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageSkills,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listSageCapabilities: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageCapabilities,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateSageProfile: ({
      userName = null,
      identitySummary = null,
      communicationStyle = null,
      recurringResponsibility = null,
      standingRules = null,
      standingRulesText = null,
    }) =>
      requestJson<WorkstationSageProfileRecord>({
        path: paths.sageProfile,
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            user_name: userName,
            identity_summary: identitySummary,
            communication_style: communicationStyle,
            recurring_responsibility: recurringResponsibility,
            standing_rules: standingRules,
            standing_rules_text: standingRulesText,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<WorkstationSageProfileRecord>,
    answerSageProfileBootstrap: ({ answer }) =>
      requestJson<WorkstationSageProfileRecord>({
        path: paths.sageProfileBootstrapAnswer,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            answer,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<WorkstationSageProfileRecord>,
    getSageMemoryStoragePolicy: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageMemoryStoragePolicy,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listSageContextFiles: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageContextFiles,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateSageContextFile: ({ filename, content }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageContextFile(filename),
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            content,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    exportSageMemory: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageMemoryExport,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    wipeSageMemory: ({ confirm }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageMemoryWipe,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            confirm,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    createSageMemoryEntry: ({ category, title, content, pinned = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageMemoryEntries,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            category,
            title,
            content,
            pinned,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateSageMemoryEntry: ({ entryId, category, title, content, pinned = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageMemoryEntry(entryId),
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            category,
            title,
            content,
            pinned,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    deleteSageMemoryEntry: ({ entryId }) =>
      requestJson<Record<string, unknown>>({
        path: `${paths.sageMemoryEntry(entryId)}${buildQueryString({ workspace_id: scope.workspaceId })}`,
        init: {
          method: 'DELETE',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    setSageMemoryEntryPinned: ({ entryId, pinned }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageMemoryPin(entryId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            pinned,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listSageServices: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageServices,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateSageServiceProfile: ({ serviceId, profile }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageServiceProfile(serviceId),
        init: {
          method: 'PUT',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            profile,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    createSageServiceEntry: ({ serviceId, entry }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageServiceEntries(serviceId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            entry,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateSageServiceEntry: ({ serviceId, entryId, entry }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageServiceEntry(serviceId, entryId),
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            entry,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    deleteSageServiceEntry: ({ serviceId, entryId }) =>
      requestJson<Record<string, unknown>>({
        path: `${paths.sageServiceEntry(serviceId, entryId)}${buildQueryString({ workspace_id: scope.workspaceId })}`,
        init: {
          method: 'DELETE',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    setSageServiceEntryPinned: ({ serviceId, entryId, pinned }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageServicePin(serviceId, entryId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            pinned,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listInstalledApps: () =>
      requestJson<Record<string, unknown>>({
        path: paths.appsInstalled,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listStoreApps: () =>
      requestJson<Record<string, unknown>>({
        path: paths.appsStore,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listAppUpdates: () =>
      requestJson<Record<string, unknown>>({
        path: paths.appsUpdates,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    installApp: ({ appId, packageId, releaseChannel, installSource }) =>
      requestJson<Record<string, unknown>>({
        path: paths.appsInstall,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            app_id: appId,
            package_id: packageId ?? undefined,
            release_channel: releaseChannel ?? undefined,
            install_source: installSource ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    uninstallApp: ({ appId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.appsUninstall,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            app_id: appId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    updateApp: ({ appId, packageId, releaseChannel, installSource }) =>
      requestJson<Record<string, unknown>>({
        path: paths.appsUpdate,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            app_id: appId,
            package_id: packageId ?? undefined,
            release_channel: releaseChannel ?? undefined,
            install_source: installSource ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listAgentDefinitions: () =>
      requestJson<Record<string, unknown>>({
        path: paths.agentDefinitions,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listAgentInstalls: () =>
      requestJson<Record<string, unknown>>({
        path: paths.agentInstalls,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listRuntimeAttachments: () =>
      requestJson<Record<string, unknown>>({
        path: paths.runtimeAttachments,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listRuntimeTargets: () =>
      requestJson<Record<string, unknown>>({
        path: paths.runtimeTargets,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listMcpServers: () =>
      requestJson<Record<string, unknown>>({
        path: paths.mcpServers,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    saveMcpServer: ({ serverId, label = null, endpoint, enabled = true, discoverTools = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.mcpServer(serverId),
        init: {
          method: 'PUT',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            label: label ?? undefined,
            endpoint,
            transport: 'streamable_http',
            enabled,
            discover_tools: discoverTools,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    refreshMcpServer: ({ serverId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.mcpServerRefresh(serverId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ workspace_id: scope.workspaceId }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    approveMcpTool: ({ serverId, toolName }) =>
      requestJson<Record<string, unknown>>({
        path: paths.mcpToolApprove(serverId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            tool_name: toolName,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    deleteMcpServer: ({ serverId }) =>
      requestJson<Record<string, unknown>>({
        path: `${paths.mcpServer(serverId)}${buildQueryString({ workspace_id: scope.workspaceId })}`,
        init: {
          method: 'DELETE',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listProviders: () =>
      requestJson<Record<string, unknown>>({
        path: paths.providers,
        policy: PROVIDER_READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listProviderCatalog: () =>
      requestJson<Record<string, unknown>>({
        path: paths.providersCatalog,
        policy: PROVIDER_READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getWorkspaceAiRoute: () =>
      requestJson<WorkspaceAiRoutePayload>({
        path: paths.workspaceAiRoute,
        policy: PROVIDER_READ_REQUEST_POLICY,
      }) as Promise<WorkspaceAiRoutePayload>,
    updateWorkspaceAiRouteDefault: ({
      routeId = null,
      kind = null,
      provider = null,
      model = null,
      modelPreset = null,
    }) =>
      requestJson<WorkspaceAiRoutePayload>({
        path: paths.workspaceAiRouteDefault,
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            routeId: routeId ?? undefined,
            kind: kind ?? undefined,
            provider: provider ?? undefined,
            model: model ?? undefined,
            modelPreset: modelPreset ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<WorkspaceAiRoutePayload>,
    listProviderModels: ({ providerId, profileId = null }: { providerId: string; profileId?: string | null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.providerModels(providerId, profileId),
        policy: PROVIDER_READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    refreshWorkspaceProviderModels: ({ providerId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceProviderModelsRefresh(providerId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    getWorkspaceTransparencySettings: () =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceTransparencySettings,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateWorkspaceTransparencySettings: (options) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceTransparencySettings,
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify(options),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listProviderProfiles: ({ provider = null } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.providerProfiles(provider),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listVaultCredentials: () =>
      requestJson<Record<string, unknown>>({
        path: paths.credentialsVault,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    // MAN-36: rewired to v2 backend GET /apps
    listApps: () =>
      requestJson<Record<string, unknown>>({
        path: '/apps',
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    // MAN-36: rewired to v2 backend DELETE /apps/{provider}
    disconnectApp: (provider: string) =>
      requestJson<Record<string, unknown>>({
        path: `/apps/${encodeURIComponent(provider)}`,
        init: { method: 'DELETE', headers: mergeJsonHeaders() },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    // MAN-36: v2 backend GET /oauth/start
    getOAuthStartUrl: (app: string) =>
      requestJson<Record<string, unknown>>({
        path: `/oauth/start?app=${encodeURIComponent(app)}`,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listConnectorsVault: () =>
      requestJson<Record<string, unknown>>({
        path: paths.connectorsVault,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    upsertProviderProfile: ({
      id = null,
      provider,
      label,
      credentialId = null,
      authMode = null,
      priority = 0,
      enabled = true,
      model = null,
      metadata = null,
    }) =>
      requestJson<Record<string, unknown>>({
        path: paths.providerProfilesRoot,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            id: id ?? undefined,
            provider,
            label,
            credential_id: credentialId ?? undefined,
            auth_mode: authMode ?? undefined,
            workspace_id: scope.workspaceId,
            priority,
            enabled,
            model: model ?? undefined,
            metadata: metadata ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    upsertWorkspaceProviderCredential: ({ provider, apiKey = null, baseUrl = null, model = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceProviderCredentials,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            provider,
            api_key: apiKey ?? undefined,
            base_url: baseUrl ?? undefined,
            model: model ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    deleteWorkspaceProviderCredential: ({ provider }) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceProviderCredentials,
        init: {
          method: 'DELETE',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ provider }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    deleteConnectorVaultCredential: ({ credentialId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.connectorVaultCredential(credentialId),
        init: {
          method: 'DELETE',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    getSageToolPolicy: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageToolPolicy,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateSageToolPolicy: ({ tool, enabled }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageToolPolicy,
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ tool, enabled }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    runInstalledAgent: ({ installId, message, threadId, sessionId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.runInstalledAgent(installId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            message: message ?? undefined,
            thread_id: threadId ?? undefined,
            session_id: sessionId ?? undefined,
            channel: 'web',
            execution_mode: 'durable',
            response_mode: 'artifact',
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    killDeployedAgentRuntimeSession: ({ deployedAgentId, sessionId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentRuntimeKill(deployedAgentId, sessionId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listWorkspaceRuntimeSessions: ({ limit = 50 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceRuntimeSessions(limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    exportDeployedAgentAudit: ({ deployedAgentId, limit = 500 }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentAuditExport(deployedAgentId, limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    // MAN-30: removed — speech-to-text not in v2 scope
    transcribeSpeech: (_audio: Blob) =>
      Promise.reject(new Error('Speech-to-text is not available.')),
    // MAN-30: removed — v2 backend has no trace/telemetry
    listTraces: (_filters: Record<string, unknown> = {}) =>
      Promise.resolve({ items: [], count: 0 } as unknown as Record<string, unknown>),
    getTraceReplay: ({ traceId: _traceId, allowMissing: _allowMissing = false }) =>
      Promise.resolve({}),
    listDeployedAgents: ({ deploymentState = null } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgents(deploymentState),
        policy: AGENT_STUDIO_READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listStudioAgentSurfaces: () =>
      requestJson<StudioAgentSurfacesPayload>({
        path: paths.studioAgentSurfaces,
        policy: AGENT_STUDIO_READ_REQUEST_POLICY,
      }) as Promise<StudioAgentSurfacesPayload>,
    listStudioChannelCatalog: () =>
      requestJson<ChannelCatalogPayload>({
        path: paths.studioChannelCatalog,
        policy: AGENT_STUDIO_READ_REQUEST_POLICY,
      }) as Promise<ChannelCatalogPayload>,
    listConnectionCatalog: ({ surface = null } = {}) =>
      requestJson<ConnectionCatalogPayload>({
        path: paths.connectionsCatalog(surface),
        policy: READ_REQUEST_POLICY,
      }) as Promise<ConnectionCatalogPayload>,
    listConnectionStatus: ({ surface = null, selectedGatewayId = null } = {}) =>
      requestJson<ConnectionStatusPayload>({
        path: paths.connectionsStatus(surface, selectedGatewayId),
        policy: READ_REQUEST_POLICY,
      }) as Promise<ConnectionStatusPayload>,
    getSageAgentComputerSelection: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageAgentComputerSelection,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    setSageAgentComputerSelection: ({ selectedGatewayId, metadata = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.sageAgentComputerSelection,
        init: {
          method: 'PUT',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            selected_gateway_id: selectedGatewayId,
            metadata: metadata ?? {},
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    startConnectionSetup: ({ connectionId, surface = null, selectedGatewayId = null, metadata = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.connectionSetupStart(connectionId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            surface,
            selected_gateway_id: selectedGatewayId,
            metadata: metadata ?? {},
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    completeAppOAuthForMcp: ({ provider, code, redirectUri, workspaceId }) =>
      requestJson<Record<string, unknown>>({
        path: `/api/apps/${encodeURIComponent(provider)}/oauth/complete`,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            code,
            redirect_uri: redirectUri,
            workspace_id: workspaceId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    testConnection: ({ connectionId, surface = null, selectedGatewayId = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.connectionTest(connectionId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            surface,
            selected_gateway_id: selectedGatewayId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    verifyConnection: ({ connectionId, surface = null, selectedGatewayId = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.connectionVerify(connectionId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            surface,
            selected_gateway_id: selectedGatewayId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    disconnectConnection: ({ connectionId, surface = null, selectedGatewayId = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.connectionDisconnect(connectionId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            surface,
            selected_gateway_id: selectedGatewayId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listStudioChannelAccounts: () =>
      requestJson<ChannelAccountPayload>({
        path: paths.studioChannelAccounts,
        policy: AGENT_STUDIO_READ_REQUEST_POLICY,
      }) as Promise<ChannelAccountPayload>,
    createStudioChannelAccount: ({ provider, label, credentials, metadata = null, skipValidation = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioChannelAccounts,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            provider,
            label,
            credentials,
            metadata: metadata ?? {},
            skip_validation: Boolean(skipValidation),
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listAgentChannelBindings: ({ deployedAgentId }) =>
      requestJson<AgentChannelBindingsPayload>({
        path: paths.studioAgentChannelBindings(deployedAgentId),
        policy: AGENT_STUDIO_READ_REQUEST_POLICY,
      }) as Promise<AgentChannelBindingsPayload>,
    createAgentChannelBinding: ({ deployedAgentId, catalogId, accountRef = null, endpointKey = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioAgentChannelBindings(deployedAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            catalog_id: catalogId,
            account_ref: accountRef,
            endpoint_key: endpointKey,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    pauseAgentChannelBinding: ({ deployedAgentId, channelKey }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioAgentChannelBindingAction(deployedAgentId, channelKey, 'pause'),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ workspace_id: scope.workspaceId }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    resumeAgentChannelBinding: ({ deployedAgentId, channelKey }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioAgentChannelBindingAction(deployedAgentId, channelKey, 'resume'),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ workspace_id: scope.workspaceId }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    revokeAgentChannelBinding: ({ deployedAgentId, channelKey }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioAgentChannelBindingAction(deployedAgentId, channelKey, 'revoke'),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ workspace_id: scope.workspaceId }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    testAgentChannelBinding: ({ deployedAgentId, channelKey, message = 'Studio channel binding test' }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioAgentChannelBindingAction(deployedAgentId, channelKey, 'test'),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            message: message || 'Studio channel binding test',
            dry_run: true,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listConnectedExternalAgents: () =>
      requestJson<Record<string, unknown>>({
        path: paths.studioExternalAgents,
        policy: AGENT_STUDIO_READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    createConnectedExternalAgent: ({ name, providerKind = 'custom', endpoints = {}, manifest = {}, secretRef = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioExternalAgents,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            name,
            provider_kind: providerKind,
            endpoints: endpoints ?? {},
            manifest: manifest ?? {},
            secret_ref: secretRef ?? null,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    updateConnectedExternalAgent: ({ externalAgentId, name, providerKind, endpoints, manifest, secretRef }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioExternalAgent(externalAgentId),
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            name: name ?? undefined,
            provider_kind: providerKind ?? undefined,
            endpoints: endpoints ?? undefined,
            manifest: manifest ?? undefined,
            secret_ref: secretRef ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    refreshConnectedExternalAgentManifest: ({ externalAgentId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioExternalAgentRefreshManifest(externalAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    chatTurnConnectedExternalAgent: ({ externalAgentId, message, recentMessages = [] }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioExternalAgentChatTurn(externalAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            message,
            recent_messages: Array.isArray(recentMessages) ? recentMessages : [],
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    getConnectedExternalAgentSectionData: ({ externalAgentId, sectionId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioExternalAgentSection(externalAgentId, sectionId),
        policy: AGENT_STUDIO_READ_REQUEST_POLICY,
      }),
    disconnectConnectedExternalAgent: ({ externalAgentId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.studioExternalAgentDisconnect(externalAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    createDeployedAgent: ({
      name,
      avatar,
      persona,
      systemPrompt,
      channels,
      knowledgeSources,
      runtimeTarget,
      billingPlan,
      config,
      metadata,
      runtimeProfileId,
      provider,
      model,
    }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgents(),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            name,
            avatar: avatar ?? null,
            persona: persona ?? '',
            system_prompt: systemPrompt ?? '',
            channels: channels ?? {},
            knowledge_sources: knowledgeSources ?? [],
            runtime_target: runtimeTarget ?? 'cloud',
            billing_plan: billingPlan ?? 'free',
            config: config ?? undefined,
            metadata: metadata ?? {},
            runtime_profile_id: runtimeProfileId ?? null,
            provider: provider ?? null,
            model: model ?? null,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    getDeployedAgent: ({ deployedAgentId, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentDetail(deployedAgentId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
    getDeployedAgentTelegramReadiness: ({ deployedAgentId = null, allowMissing = false } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentTelegramReadiness(deployedAgentId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
    updateDeployedAgent: ({
      deployedAgentId,
      name,
      avatar,
      persona,
      systemPrompt,
      deploymentState,
      channels,
      knowledgeSources,
      runtimeTarget,
      billingPlan,
      config,
      metadata,
      provider,
      model,
      isPublic,
      category,
      qualityStars,
      costTier,
    }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentDetail(deployedAgentId),
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            name: name ?? undefined,
            avatar: avatar ?? undefined,
            persona: persona ?? undefined,
            system_prompt: systemPrompt ?? undefined,
            deployment_state: deploymentState ?? undefined,
            channels: channels ?? undefined,
            knowledge_sources: knowledgeSources ?? undefined,
            runtime_target: runtimeTarget ?? undefined,
            billing_plan: billingPlan ?? undefined,
            config: config ?? undefined,
            metadata: metadata ?? undefined,
            provider: provider ?? undefined,
            model: model ?? undefined,
            is_public: isPublic ?? undefined,
            category: category ?? undefined,
            quality_stars: qualityStars ?? undefined,
            cost_tier: costTier ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    deployDeployedAgent: ({ deployedAgentId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentDeploy(deployedAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    pauseDeployedAgent: ({ deployedAgentId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentPause(deployedAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    testTurnDeployedAgent: ({ deployedAgentId, body }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentTestTurn(deployedAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            message: body.message,
            channel: body.channel ?? 'test',
            runtime_mode: body.runtime_mode ?? 'text_agent',
            customer_profile: body.customer_profile ?? null,
            recent_messages: Array.isArray(body.recent_messages) ? body.recent_messages : [],
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    verifyDeployedAgentKnowledge: ({ deployedAgentId, query, limit = 5 }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentKnowledgeVerify(deployedAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            query,
            limit,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    uploadDeployedAgentKnowledgeFile: ({ deployedAgentId, fileName, contentText }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentKnowledgeFiles(deployedAgentId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            file_name: fileName,
            content_text: contentText,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listDeployedAgentAnalytics: () =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentAnalyticsRoster,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getDeployedAgentAnalytics: ({ deployedAgentId, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentAnalyticsDetail(deployedAgentId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
    getDeployedAgentAdminDashboard: ({ deployedAgentId, limit = 50, offset = 0, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentAdminDashboard(deployedAgentId, limit, offset),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
    listDeployedAgentBusinessInsights: ({ deployedAgentId, status = null, limit = 50, offset = 0 }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentBusinessInsights(deployedAgentId, status, limit, offset),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    reviewDeployedAgentBusinessInsight: ({ deployedAgentId, insightId, action, note = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentBusinessInsightReview(deployedAgentId, insightId, action),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            note: note ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listDeployedAgentMemory: ({ deployedAgentId, limit = 50, offset = 0 }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentMemory(deployedAgentId, limit, offset),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listDeployedAgentConversations: ({ deployedAgentId, limit = 50, offset = 0 }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentConversations(deployedAgentId, limit, offset),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getDeployedAgentConversationDetail: ({ deployedAgentId, sessionId, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentConversationDetail(deployedAgentId, sessionId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
    deleteDeployedAgentExternalUserData: ({ deployedAgentId, externalUserId, channel, sessionId, note }) =>
      requestJson<Record<string, unknown>>({
        path: paths.deployedAgentExternalUserDelete(deployedAgentId, externalUserId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            channel,
            session_id: sessionId ?? undefined,
            note: note ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listMarketplaceAgents: ({ category = null, costTier = null, limit = 100, offset = 0 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.marketplaceAgents({
          category,
          costTier,
          limit,
          offset,
        }),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listMarketplacePackages: ({ kind = null, runtimeType = null, includeReviewQueue = false } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.marketplacePackages({ kind, runtimeType, includeReviewQueue }),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listDiscoveryFeed: ({ filter = null } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.discoveryFeed({ filter }),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    adoptDiscoveryItem: ({ feedItemId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.discoveryAdopt(feedItemId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listMarketplaceAppSubmissions: () =>
      requestJson<Record<string, unknown>>({
        path: paths.marketplaceAppSubmissions,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listStudioTemplates: () =>
      requestJson<Record<string, unknown>>({
        path: paths.studioTemplates,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    registerMarketplaceProvider: (payload: Record<string, unknown>) =>
      requestJson<Record<string, unknown>>({
        path: paths.marketplaceProviderRegister,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify(payload),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    registerMarketplaceApp: (payload: Record<string, unknown>) =>
      requestJson<Record<string, unknown>>({
        path: paths.marketplaceAppRegister,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify(payload),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    reviewMarketplaceAppSubmission: ({ packageId, approved, reason = null, verificationStatus = null }) =>
      requestJson<Record<string, unknown>>({
        path: paths.marketplacePackageReview(packageId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            approved,
            reason: reason ?? undefined,
            verification_status: verificationStatus ?? undefined,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    installMarketplacePackage: ({ packageId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.marketplacePackageInstall(packageId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    getPlatformAnalytics: () =>
      requestJson<Record<string, unknown>>({
        path: paths.platformAnalytics,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getWorkspaceRouting: () =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceRouting,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateWorkspaceRouting: ({ adminDefaults }) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceRouting,
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            admin_defaults: adminDefaults ?? {},
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    listWorkspaceMembers: () =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceMembers,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    inviteWorkspaceMember: ({ email, role }) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceMemberInvites,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ email, role }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    revokeWorkspaceInvite: ({ inviteId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceMemberInvite(inviteId),
        init: {
          method: 'DELETE',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    updateWorkspaceMemberRole: ({ userId, role }) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceMember(userId),
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({ role }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    removeWorkspaceMember: ({ userId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceMember(userId),
        init: {
          method: 'DELETE',
          headers: mergeJsonHeaders(),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    getWorkspacePolicies: () =>
      requestJson<Record<string, unknown>>({
        path: paths.workspacePolicies,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    updateWorkspacePolicies: (payload) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspacePolicies,
        init: {
          method: 'PATCH',
          headers: mergeJsonHeaders(),
          body: JSON.stringify(payload),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    getUsageSummary: ({ period } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.usageSummary(period),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getBillingSummary: () =>
      requestJson<Record<string, unknown>>({
        path: paths.billingSummary,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    createBillingCheckoutSession: ({ planId, successUrl, cancelUrl }) =>
      requestJson<Record<string, unknown>>({
        path: paths.billingCheckout,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            plan_id: planId,
            success_url: successUrl,
            cancel_url: cancelUrl,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    createBillingPortalSession: ({ returnUrl } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.billingPortal,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            return_url: returnUrl,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    createCreditPurchaseSession: ({ amountUsd, successUrl, cancelUrl }) =>
      requestJson<Record<string, unknown>>({
        path: paths.billingCreditPurchase,
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify({
            workspace_id: scope.workspaceId,
            amount_usd: amountUsd,
            success_url: successUrl,
            cancel_url: cancelUrl,
          }),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    getCreditBalance: () =>
      requestJson<Record<string, unknown>>({
        path: paths.billingCreditBalance,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getCreditUsageHistory: ({ limit } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.billingCreditUsageHistory(limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getCreditUsage: (options = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.billingCreditUsage(options),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    uploadSageChatAttachment: async ({ file, onProgress }) => {
      const formData = new FormData();
      formData.append('file', file);

      const csrfToken = readCsrfTokenFromCookie();

      // We use XMLHttpRequest for progress tracking
      return new Promise<SageChatAttachment>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', resolveAbsoluteUrl(getApiBaseUrl(), paths.sageChatAttachments));
        xhr.withCredentials = true;

        if (csrfToken) {
          xhr.setRequestHeader('x-csrf-token', csrfToken);
        }

        if (xhr.upload && onProgress) {
          xhr.upload.onprogress = (event) => {
            if (event.lengthComputable) {
              onProgress((event.loaded / event.total) * 100);
            }
          };
        }

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const response = JSON.parse(xhr.responseText);
              resolve(response as SageChatAttachment);
            } catch (err) {
              reject(new WorkstationClientError('Failed to parse upload response.', xhr.status, null, 'parse_error'));
            }
          } else {
            let detail = xhr.responseText;
            try {
              const parsed = JSON.parse(xhr.responseText);
              detail = parsed.detail || parsed.message || xhr.responseText;
            } catch {
              // Not JSON
            }
            reject(new WorkstationClientError(`Upload failed (${xhr.status}): ${detail}`, xhr.status, detail, 'upload_failure'));
          }
        };

        xhr.onerror = () => {
          reject(new WorkstationClientError('Network error during upload.', 0, null, 'network_error'));
        };

        xhr.send(formData);
      });
    },
    createSession,
    persistUserTurn,
    submitTurn,
    submitTurnWithSessionRetry: async ({
      actor,
      threadId,
      message,
      channel = 'web',
      source = 'workstation_client',
      runtimeTarget = null,
      machineTarget = null,
      provider = null,
      model = null,
      reasoningEffort = null,
      policyContext = {},
      existingSession = null,
      clientRequestId = null,
      attachments = [],
      }) => {

      let session = await createSession({
        actor,
        threadId,
        channel,
        source,
        forceNew: false,
        existingSession,
      });

      try {
        const response = await submitTurn({
          actor,
          sessionId: String(session.session_id),
          threadId,
          message,
          channel,
          source,
          runtimeTarget,
          machineTarget,
          provider,
          model,
          reasoningEffort,
          policyContext,
          clientRequestId,
          attachments,
        });
        return { response, session, renewed: false };
      } catch (error) {
        if (!(error instanceof WorkstationClientError) || error.status !== 409) {
          throw error;
        }

        session = await createSession({
          actor,
          threadId,
          channel,
          source,
          forceNew: true,
          existingSession: null,
        });
        const response = await submitTurn({
          actor,
          sessionId: String(session.session_id),
          threadId,
          message,
          channel,
          source,
          runtimeTarget,
          machineTarget,
          provider,
          model,
          reasoningEffort,
          policyContext,
          clientRequestId,
          attachments,
        });
        return { response, session, renewed: true };
      }
    },
    submitTurnStream,
    submitTurnStreamWithSessionRetry: async ({
      actor,
      threadId,
      message,
      channel = 'web',
      source = 'workstation_client',
      runtimeTarget = null,
      machineTarget = null,
      provider = null,
      model = null,
      reasoningEffort = null,
      policyContext = {},
    onEvent,
    existingSession = null,
    clientRequestId = null,
    abortHandle = null,
    attachments = [],
  }) => {
      let session = await createSession({
        actor,
        threadId,
        channel,
        source,
        forceNew: false,
        existingSession,
      });
      let streamTransportRetryCount = 0;

      while (true) {
        try {
          const response = await submitTurnStream({
            actor,
            sessionId: String(session.session_id),
            threadId,
            message,
            channel,
            source,
            runtimeTarget,
            machineTarget,
            provider,
            model,
            reasoningEffort,
            policyContext,
            onEvent,
            clientRequestId,
            abortHandle,
            attachments,
            });

          return { response, session, renewed: false };
        } catch (error) {
          if (error instanceof WorkstationClientError && error.status === 409) {
            session = await createSession({
              actor,
              threadId,
              channel,
              source,
              forceNew: true,
              existingSession: null,
            });
            try {
              const response = await submitTurnStream({
                actor,
                sessionId: String(session.session_id),
                threadId,
                message,
                channel,
                source,
                runtimeTarget,
                machineTarget,
                provider,
                model,
                reasoningEffort,
                policyContext,
                onEvent,
                clientRequestId,
                abortHandle,
                attachments,
                });

              return { response, session, renewed: true };
            } catch (renewedError) {
              if (shouldRetryTurnStreamFailure(renewedError, streamTransportRetryCount)) {
                streamTransportRetryCount += 1;
                continue;
              }
              throw renewedError;
            }
          }
          if (shouldRetryTurnStreamFailure(error, streamTransportRetryCount)) {
            streamTransportRetryCount += 1;
            continue;
          }
          throw error;
        }
      }
    },
    // MAN-30: removed — approval-free architecture
    resolveApproval: ({ approvalId: _approvalId, payload: _payload, runId: _runId }) =>
      Promise.resolve({}),
    // MAN-30: removed — SSE event streams not in v2 scope
    openTraceStream: (_traceId: string) =>
      (new EventSource('about:blank') as EventSource),
    openNotificationsStream: (_options: Record<string, unknown> = {}) =>
      (new EventSource('about:blank') as EventSource),
    openChannelEventsStream: (_options: Record<string, unknown> = {}) =>
      (new EventSource('about:blank') as EventSource),
    // MAN-30: removed — no SSE push stream in v2
    openSageTurnStream: (_workspaceId: string) =>
      (new EventSource('about:blank') as EventSource),
    snapshot: () => ({
      scope,
      paths: {
        sessionCreate: paths.sessionCreate,
        turnSubmit: paths.turnSubmit,
        runs: paths.runs(),
        approvals: paths.approvals(),
        artifacts: paths.artifacts(),
        agentTraces: paths.agentTraces(),
        notifications: paths.notifications(),
        activity: paths.activity(),
        agentTraceStream: paths.agentTraceStream(':traceId'),
        notificationsStream: paths.notificationsStream(),
        channelEventsStream: paths.channelEventsStream(),
        sageTurnsStream: paths.sageTurnsStream(':workspaceId'),
      },
    }),
  };
}
