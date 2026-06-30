import type {
  DeployedAgentAnalyticsRecord,
  DeployedAgentConversationDetail,
  DeployedAgentConversationRecord,
  DeployedAgentMemoryRecord,
  DeployedAgentRecord,
  DeployedAgentTelegramReadinessRecord,
  ConnectedExternalAgentRecord,
  ProviderCatalogModelRecord,
  ProviderCatalogRecord,
  StudioAgentComputerRecord,
} from '@/lib/workspace/workstation-client';

export type WizardMode = 'create' | 'edit';
export type WizardStepId = 'overview' | 'knowledge' | 'tools' | 'channels' | 'memory' | 'safety' | 'test' | 'deploy';
export type StudioSubview = 'agents' | 'inbox' | 'deploy';
export type SpecialistOverlayTabId = 'overview' | 'chat' | 'channels' | 'knowledge' | 'ai' | 'tools' | 'memory' | 'artifacts' | 'connectors' | 'analytics';
export type StudioSurfaceKind = 'native_studio_agent' | 'connected_external_agent' | 'agent_computer' | 'agent_group_reserved';
export type AgentStudioObjectType = 'studio_agent' | 'connected_external_agent' | 'agent_computer' | 'external_agent_reserved' | 'group_reserved';
export type AgentVisibilityKind = 'private_workspace' | 'public_channel' | 'reserved';
export type StudioConnectedExternalAgent = ConnectedExternalAgentRecord;
export type StudioAgentComputerSurface = StudioAgentComputerRecord;

export type StudioTemplate = {
  id: string;
  title: string;
  category: string;
  icon: string;
  outcome: string;
  description: string;
  setupTime: string;
  channelLabel: string;
  requiredConnectors: string[];
  defaultName: string;
  persona: string;
  systemPrompt: string;
  knowledgePlaceholder: string;
  selectedToolIds: string[];
  memoryEnabled: boolean;
  contextBudgetPreset: string;
};

export type WizardState = {
  name: string;
  avatar: string;
  persona: string;
  systemPrompt: string;
  knowledgeSourceText: string;
  aiTier: 'light' | 'pro' | 'max';
  aiSource: 'empyralis_credits' | 'workspace_api_key' | 'local_model' | 'subscription_passthrough';
  runtimeSupplierKind: 'empyralis' | 'customer' | 'third_party_certified';
  runtimeSupplierId: string;
  runtimeSupplierLabel: string;
  runtimePlacement: 'managed_cloud' | 'hosted_hardware_pool' | 'customer_local' | 'customer_hosted';
  marketplacePublishAllowed: boolean;
  thirdPartyRuntimeAllowed: boolean;
  computerAutomationEnabled: boolean;
  computerAutomationRuntimeClass: 'virtual_browser' | 'virtual_desktop' | 'virtual_code_sandbox' | 'local_browser' | 'local_desktop';
  computerAutomationAllowedDomains: string;
  computerAutomationMaxSessions: string;
  computerAutomationDailyBudgetUsd: string;
  computerAutomationMonthlyBudgetUsd: string;
  runtimeAccessMode: 'default_guarded' | 'custom';
  approvalMode: 'guarded' | 'balanced' | 'autonomous';
  customerChannel: 'telegram' | 'whatsapp' | 'web_widget' | 'draft';
  telegramEnabled: boolean;
  telegramConnectorId: string;
  telegramEndpointKey: string;
  providerId: string;
  modelId: string;
  runtimeTarget: string;
  selfHostedRuntimeProfileId: string;
  selfHostedPrivacyAccepted: boolean;
  selfHostedSafetyAccepted: boolean;
  billingPlan: string;
  selectedToolIds: string[];
  memoryEnabled: boolean;
  contextBudgetPreset: string;
  retentionPreset: string;
  healthSafetyEnabled: boolean;
  healthSafetyAssistantName: string;
  pausedMessage: string;
  welcomeIntro: string;
  welcomeCoreValue: string;
  publicStartCtaLabel: string;
  publicStartCtaUrl: string;
  escalationPreset: string;
  handoffMode: string;
  ownerNotificationDestination: string;
  dailyMessageLimit: string;
  monthlyCostCapUsd: string;
  upgradeCtaUrl: string;
  upgradeCtaLabel: string;
};

export type AgentOperationalMetrics = {
  conversationCount: number;
  conversationCountLabel: string;
  latestActivityAt: string | null;
  latestActivityLabel: string;
  unresolvedEscalations: number;
  unresolvedEscalationsLabel: string;
  latestChannel: string | null;
};

export type AgentAnalyticsSeriesPoint = {
  date: string | null;
  label: string;
  conversations: number;
  messages: number;
};

export type AgentAnalyticsSnapshot = {
  activeUsersLast30d: number;
  messageVolumeDay: number;
  messageVolumeWeek: number;
  messageVolumeMonth: number;
  latestMessageAt: string | null;
  escalationTotalSessions: number;
  escalationSessionCount: number;
  escalationRatePercent: number;
  outcomes: Array<[string, number]>;
  topOutcome: string | null;
  currentBurnUsd: number | null;
  costCapUsd: number | null;
  percentUsed: number | null;
  usageMonth: string | null;
  activitySeries: AgentAnalyticsSeriesPoint[];
};

export type StudioPaneCache = {
  providerCatalog: ProviderCatalogSnapshot[];
  agents: DeployedAgentRecord[];
  connectedExternalAgents: StudioConnectedExternalAgent[];
  runtimeAttachments: RuntimeAttachmentSnapshot[];
  connectorVaultIds: string[];
  agentMetricsById: Record<string, AgentOperationalMetrics>;
  agentAnalyticsById: Record<string, AgentAnalyticsSnapshot>;
  selectedAgentId: string | null;
  selectedExternalAgentId: string | null;
  selectedAgentComputerId: string | null;
  overlayAgentId: string | null;
  selectedAgentDetail: DeployedAgentRecord | null;
  selectedAgentAnalytics: AgentAnalyticsSnapshot | null;
  selectedTelegramReadiness: TelegramReadinessSnapshot | null;
  conversations: DeployedAgentConversationRecord[];
  selectedSessionId: string | null;
  selectedTranscript: DeployedAgentConversationDetail | null;
  isAgentListPriming: boolean;
  isAgentListUnavailable: boolean;
};

export type DetailConfigDraft = Pick<
  WizardState,
  'persona' | 'systemPrompt' | 'knowledgeSourceText' | 'aiTier' | 'aiSource' | 'providerId' | 'modelId' | 'billingPlan' | 'selectedToolIds' | 'memoryEnabled' | 'contextBudgetPreset' | 'retentionPreset'
>;

export type ProviderCatalogModelSnapshot = {
  id: string;
  label: string;
  contextWindowTokens: number | null;
  inputCostPer1kUsd: number | null;
  outputCostPer1kUsd: number | null;
  supportsTools: boolean;
  supportsVision: boolean;
  supportsJson: boolean;
  supportsReasoning: boolean;
  localSelfHostedCompatible: boolean;
  pricingKnown: boolean;
  source: string | null;
  fetchedAt: string | null;
  lifecycle: string | null;
  capabilityLabels: string[];
};

export type ProviderCatalogSnapshot = {
  id: string;
  label: string;
  state: string;
  defaultModel: string | null;
  studioVisible: boolean;
  providerScopes: string[];
  privacyPosture: string | null;
  jurisdiction: string | null;
  residency: string | null;
  localSelfHostedCompatible: boolean;
  capabilityLabels: string[];
  modelsSource: string | null;
  modelsSyncedAt: string | null;
  modelsExpiresAt: string | null;
  modelsError: string | null;
  models: ProviderCatalogModelSnapshot[];
};

export type TelegramReadinessIssue = {
  code: string;
  message: string;
  guidance: string | null;
  severity: string;
};

export type TelegramConnectorOption = {
  id: string;
  label: string;
  endpointKey: string | null;
  botUsername: string | null;
  webhookPath: string | null;
  webhookUrl: string | null;
  profileStatus: string | null;
  profileIssue: string | null;
  lastError: string | null;
  lastErrorAt: string | null;
};

export type AgentIntegrationConnectorCard = {
  id: string;
  label: string;
  image: string;
  connected: boolean;
  statusLabel: string;
  capabilityTags: string[];
};

export type TelegramReadinessSnapshot = {
  readyForLive: boolean;
  status: string;
  nextAction: string | null;
  blockers: TelegramReadinessIssue[];
  warnings: TelegramReadinessIssue[];
  connectors: TelegramConnectorOption[];
  configuredBinding: Record<string, unknown>;
  webhook: Record<string, unknown>;
  autopilot: Record<string, unknown>;
};

export type RuntimeAttachmentSnapshot = {
  attachmentId: string;
  attachmentKind: string;
  runtimeProfileId: string;
  runtimeNodeId: string;
  label: string;
  online: boolean;
  healthy: boolean;
  ownerApproved: boolean;
  status: string;
  selfHostedNodeStatus: string;
  nodeKind: string;
  heartbeatAt: string | null;
  capabilities: string[];
};

export type ConversationFilters = {
  channel: string;
  escalationState: string;
  outcome: string;
};

export type TimelineEntry = Record<string, unknown> & {
  id?: string | null;
  kind?: string | null;
  ts?: string | null;
  summary?: string | null;
  text?: string | null;
  status?: string | null;
  direction?: string | null;
  run_id?: string | null;
  thread_id?: string | null;
  tool_name?: string | null;
  approval_id?: string | null;
  resolution?: string | null;
  action?: string | null;
};

export type RuntimeCardStatus = {
  label: 'Ready' | 'Default' | 'Needs connected computer' | 'Needs Approval Policy' | 'Unsupported in this workspace' | 'Requires setup';
  tone: 'success' | 'warning' | 'danger' | 'neutral';
};

export type LaunchReadinessItem = {
  id: string;
  label: string;
  detail: string;
  ok: boolean;
};
