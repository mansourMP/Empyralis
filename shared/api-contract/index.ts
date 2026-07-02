export type ApiTurnActor = {
  type: string;
  id: string;
  display_name: string;
};

export type {
  EmpyralisModelBillingSource,
  EmpyralisModelTier,
  EmpyralisModelTierContract,
} from './model-tier-contract';
export {
  EMPYRALIS_HOSTED_MODEL_TIERS,
  EMPYRALIS_MODEL_TIERS,
  USER_OWNED_MODEL_TIERS,
  exposesProviderModelToOrdinaryUi,
} from './model-tier-contract';

export type ApiTurnAttachment = {
  kind: string;
  uri: string;
  name?: string;
  metadata?: Record<string, unknown>;
};

export type DeployedAgentRuntimePlacement =
  | 'managed_cloud'
  | 'hosted_hardware_pool'
  | 'customer_local'
  | 'customer_hosted';

export type DeployedAgentRuntimeSupplierKind =
  | 'empyralis'
  | 'customer'
  | 'third_party_certified';

export type DeployedAgentComputerAutomationConfig = {
  enabled: boolean;
  runtime_class?: 'virtual_browser' | 'virtual_desktop' | 'virtual_code_sandbox' | 'local_browser' | 'local_desktop' | null;
  allowed_domains?: string[];
  max_concurrent_sessions?: number;
  daily_budget_usd?: number | null;
  monthly_budget_usd?: number | null;
  requires_owner_approval?: boolean;
  idle_timeout_seconds?: number;
  max_session_runtime_seconds?: number;
};

export type DeployedAgentWorkspaceContract = {
  schema_version: number;
  workspace_root: string;
  files_root: string;
  artifacts_root: string;
  browser_profile_root: string;
  logs_root: string;
  state_root: string;
  customer_scope_key?: string | null;
  customer_root?: string | null;
  session_scope_key?: string | null;
  session_root?: string | null;
  isolation: {
    scope: 'deployed_agent';
    cross_agent_read: false;
    cross_customer_read: false;
    host_filesystem_default: 'workspace_root_only';
    sage_memory_access: false;
  };
};

export type DeployedAgentRuntimeSupplyContract = {
  schema_version: number;
  supplier: {
    kind: DeployedAgentRuntimeSupplierKind;
    id: string;
    label: string;
    owner_workspace_id?: string | null;
  };
  placement: {
    kind: DeployedAgentRuntimePlacement;
    runtime_target: 'cloud' | 'local' | 'self_hosted';
    trust_zone: 'empyralis_managed_cloud' | 'empyralis_owned_hardware' | 'customer_owned' | 'certified_third_party';
  };
  computer_automation: DeployedAgentComputerAutomationConfig;
  marketplace_policy: {
    visibility: string;
    third_party_runtime_allowed: boolean;
    review_state: string;
    verification_status: string;
    install_eligible: boolean;
    install_blockers: string[];
  };
  model_tier: {
    public_tier: string;
    public_label: string;
    billing_source: string;
  };
  provider_binding: {
    internal_provider?: string | null;
    internal_model?: string | null;
    expose_provider_model_to_ordinary_ui: boolean;
  };
};

export type AgentTurnPolicyContext = {
  trust_mode?: 'auto' | 'guarded' | 'strict' | 'cost_guard' | 'sensitive_guard';
  session_mode?: 'copilot' | 'agent';
  approval_ui?: 'card';
  interactive_approvals?: boolean;
  approval_reviewer?: 'human' | 'policy';
  runtime_lane?: 'managed_cloud' | 'personal_gateway' | 'dedicated_computer' | string;
  permission_mode?: 'default' | 'auto_review' | 'autopilot' | 'device_access';
  runtime_trust_zone?: 'shared_cloud' | 'user_owned_local' | 'owned_dedicated_runtime';
  elevated_mode?: 'off' | 'ask' | 'full';
  elevated?: {
    mode?: 'off' | 'ask' | 'full';
    runtime_trust_zone?: 'shared_cloud' | 'user_owned_local' | 'owned_dedicated_runtime';
    granted_at?: string;
    expires_at?: string;
    ttl_seconds?: number;
    task_id?: string;
    granted_by?: string;
    reason?: string;
    allowed_action_classes?: string[];
    denied_action_classes?: string[];
  } | string;
  [key: string]: unknown;
};

export type AgentTurnApprovalRequest = {
  approval_id?: string | null;
  run_id?: string | null;
  prompt: string;
  labels?: string[];
  capabilities?: string[];
  actions?: string[];
  target?: string | null;
  scope?: 'once';
  reusable?: boolean;
  consequence?: string | null;
  status?: 'waiting' | 'approved' | 'rejected';
};

export type AgentTurnInterventionKind =
  | 'system_notice'
  | 'system_error'
  | 'connect_required'
  | 'run_offer'
  | 'workflow_offer'
  | 'run_handoff'
  | 'loop_detected';

export type AgentTurnIntervention = {
  kind: AgentTurnInterventionKind;
  title: string;
  detail?: string | null;
  severity?: 'info' | 'warning' | 'error';
  status?: 'ready' | 'waiting' | 'active' | 'completed' | 'failed';
  code?: string | null;
  run_id?: string | null;
  metadata?: Record<string, unknown>;
};

export type AgentTurnRequest = {
  tenant_id?: string;
  workspace_id: string;
  thread_id?: string;
  session_id: string;
  channel?: string;
  actor: ApiTurnActor;
  message: string;
  attachments?: ApiTurnAttachment[];
  context_hints?: Record<string, unknown>;
  execution_mode?: 'sync' | 'durable';
  response_mode?: 'stream' | 'artifact' | 'channel_reply';
  machine_target?: string | null;
  policy_context?: AgentTurnPolicyContext;
};

export type AgentTurnResponse = {
  ok?: boolean;
  status: string;
  reply?: string;
  run_id?: string | null;
  thread_id?: string | null;
  session_id?: string | null;
  artifacts?: Array<Record<string, unknown>>;
  approvals?: AgentTurnApprovalRequest[];
  interventions?: AgentTurnIntervention[];
  metadata?: Record<string, unknown>;
};

export type SessionCreateRequest = {
  tenant_id?: string;
  workspace_id: string;
  channel?: string;
  actor: ApiTurnActor;
  metadata?: Record<string, unknown>;
  session_id?: string | null;
};

export type SessionResponse = {
  ok?: boolean;
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

export type ThreadTurnRecord = {
  id: string;
  tenant_id?: string;
  workspace_id?: string;
  thread_id: string;
  session_id?: string | null;
  request_id?: string | null;
  role: 'user' | 'assistant';
  status?: string | null;
  content: string;
  run_id?: string | null;
  actor?: Record<string, unknown>;
  approvals?: AgentTurnApprovalRequest[];
  interventions?: AgentTurnIntervention[];
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ThreadRecord = {
  id: string;
  tenant_id?: string;
  workspace_id?: string;
  owner_user_id?: string | null;
  master_agent_install_id?: string | null;
  channel?: string | null;
  title: string;
  status?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
  last_turn_at?: string | null;
  turns?: ThreadTurnRecord[];
};

export type ThreadListResponse = {
  items: ThreadRecord[];
  count: number;
  workspace_id?: string;
  tenant_id?: string | null;
};

export type RunListItem = {
  run_id?: string | null;
  engine?: string | null;
  status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  workspace_id?: string | null;
  owner_user_id?: string | null;
  owner_email?: string | null;
  workflow_id?: string | null;
  pack_id?: string | null;
  agent_role?: string | null;
  parent_run_id?: string | null;
  execution_target_selected?: string | null;
  result_summary?: string | null;
  source?: 'live' | 'history' | null;
  raw?: Record<string, unknown>;
  [key: string]: unknown;
};

export type RunListResponse = {
  items: RunListItem[];
  count: number;
  total: number;
  limit: number;
  offset: number;
  next_offset?: number | null;
};

export type RunDetailResponse = Record<string, unknown> & {
  run_id?: string | null;
  status?: string | null;
  result?: string | null;
  result_data?: unknown;
  machine_id?: string | null;
  runtime_id?: string | null;
  interrupt_requested?: boolean;
  interrupt_requested_at?: string | null;
  interrupt_reason?: string | null;
  interrupt_scope?: string | null;
  pending_confirmation?: Record<string, unknown> | null;
  pending_approval?: Record<string, unknown> | null;
  route?: Record<string, unknown> | null;
};

export type RunReplayResponse = {
  item?: {
    status?: string | null;
    result_data?: unknown;
    events?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  } | null;
};

export type ComputerActionEventPayload = {
  schema?: 'empyralis.computer_action.v1';
  mode?: 'observing' | 'acting' | 'paused' | 'takeover' | string;
  phase?: 'planned' | 'completed' | 'failed' | 'paused' | string;
  step_number?: number | null;
  step_total?: number | null;
  tool?: string | null;
  action_type?: string | null;
  label?: string | null;
  reason?: string | null;
  detail?: string | null;
  status?: string | null;
  success?: boolean | null;
  x?: number | null;
  y?: number | null;
  text_summary?: string | null;
  file_path?: string | null;
  url?: string | null;
  error?: string | null;
  confidence?: number | null;
  certainty?: string | null;
  retry_count?: number | null;
  replanned?: boolean | null;
  readiness_state?: string | null;
  readiness_summary?: string | null;
  grounding_summary?: string | null;
  recovery_strategy?: string | null;
};

export type ArtifactItem = Record<string, unknown> & {
  id?: string | null;
  run_id?: string | null;
  kind?: string | null;
  uri_or_path?: string | null;
  focus_target?: string | null;
};

export type ArtifactListResponse = {
  ok?: boolean;
  workspace_id?: string;
  updated_at?: string | null;
  summary?: Record<string, unknown>;
  items: ArtifactItem[];
};

export type ApprovalItem = Record<string, unknown> & {
  approval_id?: string | null;
  run_id?: string | null;
  workspace_id?: string | null;
  status?: string | null;
  action?: string | null;
  summary?: string | null;
  requested_at?: string | null;
  expires_at?: string | null;
  correlation_id?: string | null;
  target?: string | null;
  labels?: string[];
  capabilities?: string[];
  metadata?: Record<string, unknown>;
  email_preview?: {
    recipient?: string | null;
    subject?: string | null;
    body_preview?: string | null;
    connector?: string | null;
    action_id?: string | null;
    artifact_reference?: string | null;
  } | null;
};

export type ApprovalListResponse = {
  items: ApprovalItem[];
  pending?: ApprovalItem[];
  count?: number;
  total?: number;
  workspace_id?: string;
};

export type ApprovalResolveRequest = {
  approval_id?: string | null;
  resolution: "approved" | "rejected";
  actor?: string;
  reason?: string | null;
  approval_scope?: "once" | "session" | null;
};

export type ApprovalResolveResponse = Record<string, unknown> & {
  status?: string;
  approval_id: string;
  run_id?: string | null;
  resolution: "approved" | "rejected";
  actor?: string;
  reason?: string;
  outbox_event?: Record<string, unknown>;
};

export type MachineListResponse = Record<string, unknown> & {
  items?: Array<Record<string, unknown>>;
  runtimes?: Array<Record<string, unknown>>;
};

export type ConnectorListResponse = Record<string, unknown> & {
  items?: Array<Record<string, unknown>>;
  connectors?: Array<Record<string, unknown>>;
};

export type NotificationItem = {
  id?: string | null;
  ts?: string | null;
  title?: string | null;
  channel?: string | null;
  direction?: string | null;
  event_type?: string | null;
  tenant_id?: string | null;
  workspace_id?: string | null;
  session_key?: string | null;
  session_id?: string | null;
  message_id?: string | null;
  parent_id?: string | null;
  run_id?: string | null;
  machine_id?: string | null;
  trace_id?: string | null;
  action?: string | null;
  text?: string | null;
  read_at?: string | null;
  metadata?: Record<string, unknown>;
};

export type NotificationListResponse = {
  items: NotificationItem[];
  count: number;
  total: number;
  sessions: Array<Record<string, unknown>>;
  session_count: number;
  stream?: boolean;
};

export type NotificationReadRequest = {
  notification_ids?: string[];
  workspace_id?: string;
  mark_all?: boolean;
};

export type NotificationReadResponse = {
  status?: string;
  marked_count: number;
  marked_ids: string[];
  read_at?: string | null;
};

export type NotificationDeviceRegistrationRequest = {
  workspace_id: string;
  device_id: string;
  push_token: string;
  provider?: string;
  platform?: string | null;
  device_name?: string | null;
  app_id?: string | null;
  capabilities?: string[];
};

export type NotificationDeviceRegistrationResponse = {
  ok: boolean;
  device_id: string;
  workspace_id: string;
  tenant_id?: string | null;
  status: string;
  registered_at?: string | null;
  provider?: string | null;
};

export type HealthResponse = Record<string, unknown> & {
  ok?: boolean;
  status?: string | null;
  detail?: string | null;
  model?: string | null;
  codex_model?: string | null;
  model_name?: string | null;
  model_id?: string | null;
  modelId?: string | null;
};
