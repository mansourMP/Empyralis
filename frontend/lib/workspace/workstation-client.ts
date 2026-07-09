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
  /** Backend-emitted per-stage transparency events for this turn (see
   *  AgentTransparencyEvent.to_user_payload() — event_id, trace_id,
   *  event_type, title, status, timestamp, tool_name, channel). Previously
   *  computed and persisted server-side but never serialized into this
   *  response type — was silently discarded here. */
  transparency_events?: Record<string, unknown>[];
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
  connectionsCatalog: (surface?: string | null) => string;
  connectionsStatus: (surface?: string | null, selectedGatewayId?: string | null) => string;
  sageAgentComputerSelection: string;
  connectionSetupStart: (connectionId: string) => string;
  connectionTest: (connectionId: string) => string;
  connectionVerify: (connectionId: string) => string;
  connectionDisconnect: (connectionId: string) => string;
  discoveryFeed: (options?: { filter?: string | null }) => string;
  discoveryAdopt: (feedItemId: string) => string;
  platformAnalytics: string;
  workspaceRouting: string;
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
  listWorkspaceRuntimeSessions: (options?: { limit?: number }) => Promise<Record<string, unknown>>;
  transcribeSpeech: (audio: Blob) => Promise<Record<string, unknown>>;
  listTraces: (filters?: WorkstationAgentTraceListFilters) => Promise<Record<string, unknown>>;
  getTraceReplay: (options: {
    traceId: string;
    allowMissing?: boolean;
  }) => Promise<Record<string, unknown> | null>;
  listConnectionCatalog: (options?: { surface?: string | null }) => Promise<ConnectionCatalogPayload>;
  listConnectionStatus: (options?: {
    surface?: string | null;
    selectedGatewayId?: string | null;
  }) => Promise<ConnectionStatusPayload>;
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
  listDiscoveryFeed: (options?: {
    filter?: string | null;
  }) => Promise<Record<string, unknown>>;
  adoptDiscoveryItem: (options: {
    feedItemId: string;
  }) => Promise<Record<string, unknown> | null>;
  getPlatformAnalytics: () => Promise<Record<string, unknown>>;
  getWorkspaceRouting: () => Promise<Record<string, unknown>>;
  updateWorkspaceRouting: (options: {
    adminDefaults?: Record<string, unknown>;
  }) => Promise<Record<string, unknown> | null>;
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
    discoveryFeed: (options = {}) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/discovery/feed${buildQueryString({
        filter: options.filter,
      })}`,
    discoveryAdopt: (feedItemId) =>
      `/api/workspaces/${encodeURIComponent(workspaceId)}/discovery/items/${encodeURIComponent(feedItemId)}/adopt`,
    platformAnalytics: `/api/platform-analytics${buildQueryString({ workspace_id: workspaceId })}`,
    workspaceRouting: `/api/workspaces/${encodeURIComponent(workspaceId)}/routing`,
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

    const session = await requestJson<WorkstationSessionRecord>({
      path: paths.sessionCreate,
      init: {
        method: 'POST',
        headers: mergeJsonHeaders(),
        body: JSON.stringify({
          tenant_id: scope.tenantId,
          workspace_id: scope.workspaceId,
          channel,
          actor,
          metadata: {
            thread_id: threadId,
            source,
          },
        }),
      },
    });

    queryClient.set(cacheKey, session as WorkstationSessionRecord);
    return session as WorkstationSessionRecord;
  }

  async function persistUserTurn({
    actor,
    sessionId,
    threadId,
    message,
    channel = 'web',
    runtimeProfileId = null,
    metadata = {},
    clientRequestId = null,
    attachments = [],
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
    const resolvedRequestId = readString(clientRequestId) || createClientRequestId();
    return (await requestJson<Record<string, unknown>>({
      path: paths.threadTurns(threadId),
      init: {
        method: 'POST',
        headers: mergeJsonHeaders(),
        body: JSON.stringify({
          tenant_id: scope.tenantId,
          workspace_id: scope.workspaceId,
          session_id: sessionId,
          channel,
          actor,
          content: message,
          runtime_profile_id: runtimeProfileId ?? undefined,
          metadata,
          client_request_id: resolvedRequestId,
          attachments,
        }),
      },
      policy: CHAT_TURN_PERSIST_REQUEST_POLICY,
    })) as Record<string, unknown>;
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

  async function submitTurnStream({
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
    onEvent,
    clientRequestId = null,
    abortHandle = null,
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
    onEvent?: (event: WorkstationTurnStreamEvent) => void;
    clientRequestId?: string | null;
    abortHandle?: WorkstationTurnStreamAbortHandle | null;
    attachments?: SageChatAttachment[];
  }): Promise<WorkstationTurnResponse> {
    const resolvedRequestId = readString(clientRequestId) || createClientRequestId();
    let response: Response;
    try {
      response = await transport.request(
        paths.turnSubmit,
        {
          method: 'POST',
          signal: abortHandle?.signal,
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
            response_mode: 'stream',
            policy_context: {
              ...(policyContext ?? {}),
              ...(runtimeTarget ? { execution_target: runtimeTarget } : {}),
            },
          }),
        },
        resolveRequestPolicy({ method: 'POST' }, STREAM_REQUEST_POLICY),
      );
    } catch (error) {
      if (abortHandle?.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
        throw new WorkstationClientError(
          'Sage stopped before finishing the response.',
          0,
          null,
          'stream_aborted',
          {
            retryable: false,
          },
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
        return normalizeStreamTurnResponse(
          payload as Record<string, unknown>,
          {
            threadId,
            sessionId,
            traceId: readString((payload as Record<string, unknown>).metadata && typeof (payload as Record<string, unknown>).metadata === 'object'
              ? ((payload as Record<string, unknown>).metadata as Record<string, unknown>).trace_id
              : null),
            fallbackReply: '',
          },
        );
      }
      throw new WorkstationClientError(
        'The workstation stream did not return an event stream.',
        0,
        payload,
        'stream_protocol_error',
      );
    }

    if (!response.body) {
      throw new WorkstationClientError(
        'The workstation stream did not include a readable body.',
        0,
        null,
        'stream_protocol_error',
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalPayload: Record<string, unknown> | null = null;
    let traceId: string | null = null;
    let streamedReply = '';

    while (true) {
      let readResult: ReadableStreamReadResult<Uint8Array>;
      try {
        readResult = await reader.read();
      } catch (error) {
        if (abortHandle?.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
          throw new WorkstationClientError(
            'Sage stopped before finishing the response.',
            0,
            null,
            'stream_aborted',
            {
              retryable: false,
            },
          );
        }
        throw error;
      }
      const { done, value } = readResult;
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r/g, '');

      while (true) {
        const delimiterIndex = buffer.indexOf('\n\n');
        if (delimiterIndex < 0) {
          break;
        }
        const block = buffer.slice(0, delimiterIndex);
        buffer = buffer.slice(delimiterIndex + 2);
        const event = parseSseBlock(block);
        if (!event) {
          continue;
        }
        if (event.event === 'trace') {
          const candidateTraceId = readString(event.payload.trace_id);
          if (candidateTraceId) {
            traceId = candidateTraceId;
          }
        }
        if (event.event === 'chunk') {
          streamedReply = `${streamedReply}${readString(event.payload.delta)}`;
        }
        if (event.event === 'final') {
          finalPayload = event.payload;
        }
        try {
          onEvent?.(event);
        } catch {
          // UI callbacks must not break stream consumption.
        }
      }

      if (done) {
        break;
      }
    }

    if (!finalPayload) {
      if (streamedReply.trim()) {
        return normalizeStreamTurnResponse({
          status: 'incomplete',
          reply: streamedReply,
          thread_id: threadId,
          session_id: sessionId,
          metadata: {
            incomplete: true,
          },
        }, {
          threadId,
          sessionId,
          traceId,
          fallbackReply: streamedReply,
        });
      }
      throw new WorkstationClientError(
        'The workstation stream ended before the final response arrived.',
        0,
        null,
        'stream_incomplete',
      );
    }

    return normalizeStreamTurnResponse(finalPayload, {
      threadId,
      sessionId,
      traceId,
      fallbackReply: streamedReply,
    });
  }

  return {
    scope,
    paths,
    requestJson,
    listThreads: ({ includeTurns = false, limit = 50 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.threads({ includeTurns, limit }),
        allowStatuses: [],
      }).then((payload) => payload ?? { items: [], count: 0 }),
    getThread: ({ threadId, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.thread(threadId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
    listRuns: ({ limit = 80 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.runs(limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getRunDetail: ({ runId, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.runDetail(runId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
    listApprovals: ({ limit = 80 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.approvals(limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getApprovalDetail: ({ approvalId, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.approvalDetail(approvalId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
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
    listActivityTimeline: ({ limit = 80, traceId = null } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.activity(limit, traceId),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
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
    listSageMemory: () =>
      requestJson<Record<string, unknown>>({
        path: paths.sageMemory,
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getSageProfile: () =>
      requestJson<WorkstationSageProfileRecord>({
        path: paths.sageProfile,
        policy: READ_REQUEST_POLICY,
      }) as Promise<WorkstationSageProfileRecord>,
    getSageHeartbeat: () =>
      requestJson<WorkstationSageHeartbeatRecord>({
        path: paths.sageHeartbeat,
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
    listWorkspaceRuntimeSessions: ({ limit = 50 } = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.workspaceRuntimeSessions(limit),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    transcribeSpeech: (audio) =>
      requestJson<Record<string, unknown>>({
        path: paths.speechToText,
        init: {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': audio.type || 'audio/webm',
          },
          body: audio,
        },
        policy: WRITE_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    listTraces: (filters = {}) =>
      requestJson<Record<string, unknown>>({
        path: paths.agentTraces(filters),
        policy: READ_REQUEST_POLICY,
      }) as Promise<Record<string, unknown>>,
    getTraceReplay: ({ traceId, allowMissing = false }) =>
      requestJson<Record<string, unknown>>({
        path: paths.agentTraceDetail(traceId),
        allowStatuses: allowMissing ? [404] : [],
        policy: READ_REQUEST_POLICY,
      }),
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
    resolveApproval: ({ approvalId, payload, runId }) =>
      requestJson<Record<string, unknown>>({
        path: paths.approvalResolve(approvalId, runId),
        init: {
          method: 'POST',
          headers: mergeJsonHeaders(),
          body: JSON.stringify(payload),
        },
        policy: WRITE_REQUEST_POLICY,
      }),
    openTraceStream: (traceId) =>
      realtime.trackEventSource(
        new EventSource(
          resolveAbsoluteUrl(getApiBaseUrl(), paths.agentTraceStream(traceId)),
          { withCredentials: true },
        ),
      ),
    openNotificationsStream: (options = {}) =>
      realtime.trackEventSource(
        new EventSource(
          resolveAbsoluteUrl(getApiBaseUrl(), paths.notificationsStream(options)),
          { withCredentials: true },
        ),
      ),
    openChannelEventsStream: (options = {}) =>
      realtime.trackEventSource(
        new EventSource(
          resolveAbsoluteUrl(getApiBaseUrl(), paths.channelEventsStream(options)),
          { withCredentials: true },
        ),
      ),
    openSageTurnStream: (workspaceId) =>
      realtime.trackEventSource(
        new EventSource(
          resolveAbsoluteUrl(getApiBaseUrl(), paths.sageTurnsStream(workspaceId)),
          { withCredentials: true },
        ),
      ),
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
