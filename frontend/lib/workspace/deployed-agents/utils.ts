import type {
  DeployedAgentAnalyticsRecord,
  DeployedAgentConversationRecord,
  DeployedAgentRecord,
  DeployedAgentTelegramReadinessRecord,
  ProviderCatalogModelRecord,
  ProviderCatalogRecord,
  StudioProofAgentSeedRecord,
} from '@/lib/workspace/workstation-client';

import type {
  AgentAnalyticsSeriesPoint,
  AgentAnalyticsSnapshot,
  AgentOperationalMetrics,
  ConversationFilters,
  DetailConfigDraft,
  ProviderCatalogModelSnapshot,
  ProviderCatalogSnapshot,
  RuntimeAttachmentSnapshot,
  StudioPaneCache,
  StudioTemplate,
  TelegramConnectorOption,
  TelegramReadinessIssue,
  TelegramReadinessSnapshot,
  TimelineEntry,
  WizardState,
} from './types';

import {
  CUSTOM_STUDIO_TEMPLATE,
  DEFAULT_SAFE_AGENT_PERSONA,
  DEFAULT_SAFE_AGENT_PERSONA_VALUES,
  DEFAULT_SAFE_AGENT_SYSTEM_PROMPT,
  DEFAULT_SAFE_AGENT_SYSTEM_PROMPT_VALUES,
  DEFAULT_SAFE_MONTHLY_COST_CAP_USD,
  DEFAULT_STUDIO_TEMPLATE,
  PRIMARY_STUDIO_TEMPLATE_IDS,
  PRIMARY_STUDIO_TEMPLATE_ORDER,
  STUDIO_AI_TIER_OPTIONS,
  STUDIO_API_PROVIDER_IDS,
  STUDIO_DEFAULT_RUNTIME_PLACEMENT,
  STUDIO_LOCAL_PROVIDER_IDS,
  STUDIO_RUNTIME_OPTIONS,
  STUDIO_TEMPLATES,
  STUDIO_TOOL_OPTIONS,
  normalizeContextPresetId,
} from './constants';

export const studioPaneCache = new Map<string, StudioPaneCache>();

type StudioPaneCacheListener = (workspaceId: string, cache: StudioPaneCache) => void;

const studioPaneCacheListeners = new Set<StudioPaneCacheListener>();

export const STUDIO_PANE_REFRESH_EVENT = 'empyralis:studio-pane-refresh';

function notifyStudioPaneCacheListeners(workspaceId: string): void {
  setTimeout(() => {
    const cache = studioPaneCache.get(workspaceId);
    if (!cache) {
      return;
    }
    Array.from(studioPaneCacheListeners).forEach((listener) => listener(workspaceId, cache));
  }, 0);
}

export function subscribeStudioPaneCache(listener: StudioPaneCacheListener): () => void {
  studioPaneCacheListeners.add(listener);
  return () => {
    studioPaneCacheListeners.delete(listener);
  };
}

export function updateStudioPaneCache(workspaceId: string, partial: Partial<StudioPaneCache>) {
  const current = studioPaneCache.get(workspaceId);
  const nextCache = {
    providerCatalog: current?.providerCatalog ?? [],
    agents: current?.agents ?? [],
    connectedExternalAgents: current?.connectedExternalAgents ?? [],
    runtimeAttachments: current?.runtimeAttachments ?? [],
    connectorVaultIds: current?.connectorVaultIds ?? [],
    agentMetricsById: current?.agentMetricsById ?? {},
    agentAnalyticsById: current?.agentAnalyticsById ?? {},
    selectedAgentId: current?.selectedAgentId ?? null,
    selectedExternalAgentId: current?.selectedExternalAgentId ?? null,
    selectedAgentComputerId: current?.selectedAgentComputerId ?? null,
    overlayAgentId: null,
    selectedAgentDetail: current?.selectedAgentDetail ?? null,
    selectedAgentAnalytics: current?.selectedAgentAnalytics ?? null,
    selectedTelegramReadiness: current?.selectedTelegramReadiness ?? null,
    conversations: current?.conversations ?? [],
    selectedSessionId: current?.selectedSessionId ?? null,
    selectedTranscript: current?.selectedTranscript ?? null,
    isAgentListPriming: current?.isAgentListPriming ?? false,
    isAgentListUnavailable: current?.isAgentListUnavailable ?? false,
    ...partial,
  };
  studioPaneCache.set(workspaceId, nextCache);
  notifyStudioPaneCacheListeners(workspaceId);
}

export function requestStudioPaneRefresh(workspaceId: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new CustomEvent(STUDIO_PANE_REFRESH_EVENT, { detail: { workspaceId } }));
}

export function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

export function studioAgentDisplayName(agent: DeployedAgentRecord | null | undefined, fallback = 'Business Agent'): string {
  const rawName = readString(agent?.name);
  const agentId = readString(agent?.id);
  const state = readString(agent?.deployment_state).toLowerCase();
  const normalized = rawName.toLowerCase();
  const compact = normalized.replace(/[^a-z0-9]/g, '');
  const looksPlaceholder = !rawName
    || normalized === 'new agent'
    || normalized === 'untitled'
    || normalized === 'untitled agent'
    || normalized === 'draft'
    || normalized === 'test'
    || normalized === 'asdf'
    || normalized === 'qwerty'
    || /^agent[-_\s]?\d*$/i.test(rawName)
    || (/^[a-z]{3,8}$/.test(compact) && !/[aeiou]/.test(compact));

  if (looksPlaceholder && state !== 'live') {
    return 'Untitled agent';
  }
  return rawName || agentId || fallback;
}

export function readOptionalString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

export function readIntegerString(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    return String(Math.trunc(value));
  }
  return readString(value);
}

export function readPositiveDecimalString(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    return String(value);
  }
  return readString(value);
}

export function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function readBoolean(value: unknown): boolean {
  if (typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'string') {
    return ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase());
  }
  return false;
}

export function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function formatCompactCount(value: unknown): string {
  const amount = readNumber(value);
  if (amount === null) {
    return 'n/a';
  }
  return new Intl.NumberFormat(undefined, {
    notation: amount >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(amount);
}

export function readItems<T extends Record<string, unknown>>(payload: unknown): T[] {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const items = (payload as Record<string, unknown>).items;
  return Array.isArray(items)
    ? items.filter((item): item is T => Boolean(item) && typeof item === 'object')
    : [];
}

export function normalizeRuntimeAttachments(payload: unknown): RuntimeAttachmentSnapshot[] {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const attachments = (payload as Record<string, unknown>).attachments;
  if (!Array.isArray(attachments)) {
    return [];
  }
  return attachments
    .map((item) => readRecord(item))
    .map((item) => ({
      attachmentId: readString(item.attachment_id),
      attachmentKind: readString(item.attachment_kind),
      runtimeProfileId: readString(item.runtime_profile_id),
      runtimeNodeId: readString(item.runtime_node_id),
      label: readString(item.label, 'Server/VPS'),
      online: readBoolean(item.online),
      healthy: readBoolean(item.healthy),
      ownerApproved: readBoolean(item.owner_approved),
      status: readString(item.status),
      selfHostedNodeStatus: readString(item.self_hosted_node_status),
      nodeKind: readString(item.node_kind),
      heartbeatAt: readOptionalString(item.heartbeat_at ?? item.last_seen_at),
      capabilities: normalizeLabelList(item.capabilities),
    }))
    .filter((item) => item.attachmentKind === 'self_hosted_business_node' || item.attachmentKind === 'local_companion');
}

export function selfHostedNodeGateReason(node: RuntimeAttachmentSnapshot | null): string | null {
  if (!node) {
    return 'Select a Server/VPS runtime.';
  }
  if (!node.runtimeProfileId) {
    return 'Selected node is missing its binding id.';
  }
  if (!node.ownerApproved) {
    return 'Selected node is not owner-approved.';
  }
  if (!node.online) {
    return 'Selected node is offline.';
  }
  if (!node.healthy) {
    return 'Selected node is unhealthy.';
  }
  const status = node.selfHostedNodeStatus.toLowerCase();
  if (status === 'revoked') {
    return 'Selected node is revoked.';
  }
  return null;
}

export function selfHostedNodeHealthLabel(node: RuntimeAttachmentSnapshot | null): string {
  if (!node) {
    return 'Not selected';
  }
  if (!node.ownerApproved) {
    return 'Pending owner approval';
  }
  if (!node.online) {
    return 'Offline';
  }
  if (!node.healthy) {
    return 'Unhealthy';
  }
  return 'Online and healthy';
}

export function formatTimestamp(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) {
    return 'n/a';
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export function formatRelativeTime(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) {
    return 'No recent activity';
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    return 'No recent activity';
  }
  const diffMinutes = Math.round((Date.now() - parsed) / 60000);
  if (Math.abs(diffMinutes) < 60) {
    return `${Math.max(1, Math.abs(diffMinutes))}m ago`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) {
    return `${Math.max(1, Math.abs(diffHours))}h ago`;
  }
  const diffDays = Math.round(diffHours / 24);
  if (Math.abs(diffDays) < 7) {
    return `${Math.max(1, Math.abs(diffDays))}d ago`;
  }
  const diffWeeks = Math.round(diffDays / 7);
  if (Math.abs(diffWeeks) < 5) {
    return `${Math.max(1, Math.abs(diffWeeks))}w ago`;
  }
  const diffMonths = Math.round(diffDays / 30);
  if (Math.abs(diffMonths) < 12) {
    return `${Math.max(1, Math.abs(diffMonths))}mo ago`;
  }
  return `${Math.max(1, Math.round(diffDays / 365))}y ago`;
}

export function formatUsd(value: unknown): string {
  const amount = readNumber(value);
  if (amount === null) {
    return 'n/a';
  }
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

export function formatUsdPer1k(value: unknown): string {
  const amount = readNumber(value);
  if (amount === null) {
    return 'n/a';
  }
  if (amount === 0) {
    return '$0 / 1K';
  }
  const digits = amount < 0.001 ? 6 : amount < 0.01 ? 4 : 3;
  return `$${amount.toFixed(digits)} / 1K`;
}

export function formatContextWindow(value: unknown): string {
  const amount = readNumber(value);
  if (amount === null || amount <= 0) {
    return 'n/a';
  }
  if (amount >= 1000000) {
    return 'Maximum';
  }
  if (amount >= 128000) {
    return 'Expanded';
  }
  return 'Standard';
}

export function deploymentStateLabel(value: unknown): string {
  return humanizeToken(readString(value), 'Draft');
}

export function rosterStatusTone(value: unknown): 'live' | 'warning' | 'danger' {
  const token = readString(value).toLowerCase();
  if (token === 'live') {
    return 'live';
  }
  if (token === 'error' || token === 'failed' || token === 'blocked') {
    return 'danger';
  }
  return 'warning';
}

export function escalationTone(value: unknown): 'neutral' | 'success' | 'warning' | 'danger' | 'accent' {
  const token = readString(value).toLowerCase();
  if (token === 'clear' || token === 'completed') {
    return 'success';
  }
  if (token === 'approval_requested' || token === 'escalated') {
    return 'warning';
  }
  if (token === 'attention_required') {
    return 'danger';
  }
  return 'neutral';
}

export function outcomeTone(value: unknown): 'neutral' | 'success' | 'warning' | 'danger' | 'accent' {
  const token = readString(value).toLowerCase();
  if (token === 'completed' || token === 'resolved') {
    return 'success';
  }
  if (token === 'pending' || token === 'open') {
    return 'warning';
  }
  if (token === 'failed' || token === 'error') {
    return 'danger';
  }
  return 'neutral';
}

export function humanizeToken(value: unknown, fallback = 'Unknown'): string {
  const token = readString(value);
  if (!token) {
    return fallback;
  }
  return token
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function listEnabledChannels(channels: unknown): string[] {
  const record = readRecord(channels);
  return Object.entries(record)
    .filter(([, config]) => readRecord(config).enabled === true)
    .map(([channelKey]) => humanizeToken(channelKey, channelKey));
}

export function hasEnabledChannel(channels: unknown, channelKey: string): boolean {
  return readRecord(readRecord(channels)[channelKey]).enabled === true;
}

export function serializeKnowledgeSources(knowledgeSources: unknown): string {
  if (!Array.isArray(knowledgeSources)) {
    return '';
  }
  return knowledgeSources
    .map((source) => {
      const record = readRecord(source);
      return readString(record.uri ?? record.id ?? record.label);
    })
    .filter(Boolean)
    .join('\n');
}

export function parseKnowledgeSources(text: string): Array<Record<string, unknown>> {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((uri, index) => ({
      id: `source-${index + 1}`,
      uri,
      label: uri,
      kind: inferKnowledgeSourceKind(uri),
    }));
}

export function inferKnowledgeSourceKind(uri: string): string {
  const token = uri.trim().toLowerCase();
  if (token.startsWith('http://') || token.startsWith('https://')) {
    return 'Website';
  }
  if (token.startsWith('sheet://') || token.includes('docs.google.com/spreadsheets')) {
    return 'Table or sheet';
  }
  if (token.startsWith('app://') || token.startsWith('drive://') || token.includes('docs.google.com/document')) {
    return 'App document';
  }
  if (token.startsWith('note://') || token.startsWith('text://')) {
    return 'Manual note';
  }
  return 'File or document';
}

export function normalizeLabelList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => readString(item)).filter(Boolean)
    : [];
}

export function normalizeToolIds(value: unknown): string[] {
  const allowed = new Set<string>(STUDIO_TOOL_OPTIONS.map((item) => item.id));
  return normalizeLabelList(value)
    .map((item) => item.toLowerCase())
    .filter((item, index, array) => allowed.has(item) && array.indexOf(item) === index);
}

export function mapSeedToolToStudioToolId(toolId: string): string {
  const normalized = toolId.trim().toLowerCase();
  if (normalized.includes('catalog') || normalized.includes('menu') || normalized.includes('availability_read')) {
    return 'spreadsheet_read';
  }
  if (normalized.includes('capture') || normalized.includes('request') || normalized.includes('confirmation')) {
    return 'spreadsheet_append';
  }
  if (normalized.includes('calendar') || normalized.includes('appointment')) {
    return 'calendar_write';
  }
  if (normalized.includes('handoff')) {
    return 'gmail_send';
  }
  return normalized;
}

export function normalizeTemplateToken(value: string): string {
  return value.trim().toLowerCase().replace(/[-\s]+/g, '_');
}

export function studioTemplateById(
  templateId: string | null | undefined,
  templates: ReadonlyArray<StudioTemplate> = STUDIO_TEMPLATES,
): StudioTemplate {
  const normalizedTemplateId = normalizeTemplateToken(readString(templateId));
  if (!normalizedTemplateId) {
    return templates[0] ?? DEFAULT_STUDIO_TEMPLATE;
  }
  if (normalizedTemplateId === normalizeTemplateToken(CUSTOM_STUDIO_TEMPLATE.id)) {
    return CUSTOM_STUDIO_TEMPLATE;
  }
  return templates.find((template) => normalizeTemplateToken(template.id) === normalizedTemplateId)
    ?? templates[0]
    ?? DEFAULT_STUDIO_TEMPLATE;
}

export function mapStudioSeedToTemplate(seed: StudioProofAgentSeedRecord): StudioTemplate | null {
  const slug = readString(seed.slug);
  const name = readString(seed.name);
  if (!slug || !name) {
    return null;
  }
  const persona = readRecord(seed.persona);
  const runtime = readRecord(seed.runtime_tier_recommendation);
  const dataSources = Array.isArray(seed.default_data_sources)
    ? seed.default_data_sources.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    : [];
  const channels = Array.isArray(seed.channels)
    ? seed.channels.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    : [];
  const tools = Array.isArray(seed.tools_skills)
    ? seed.tools_skills.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    : [];
  const requiredConnectors = [
    ...dataSources.map((item) => readString(item.label, readString(item.source_id))).filter(Boolean),
    ...channels
      .filter((item) => readBoolean(item.default_enabled))
      .map((item) => readString(item.label, readString(item.channel_key)))
      .filter(Boolean),
  ];
  const selectedToolIds = normalizeToolIds(
    tools
      .filter((item) => readBoolean(item.default_enabled))
      .map((item) => mapSeedToolToStudioToolId(readString(item.id))),
  );
  const personaRole = readString(persona.role);
  const personaTone = readString(persona.tone);
  const instructions = Array.isArray(persona.instructions)
    ? persona.instructions.map((item) => readString(item)).filter(Boolean)
    : [];
  return {
    id: normalizeTemplateToken(slug),
    title: name,
    category: readString(seed.category, 'Business Agent'),
    icon: name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part.charAt(0).toUpperCase())
      .join('') || 'AI',
    outcome: readString(seed.description, 'Customize and deploy this Business Agent.'),
    description: readString(seed.description, 'Backend-defined Business Agent template.'),
    setupTime: '5 min setup',
    channelLabel: channels.map((item) => readString(item.label, readString(item.channel_key))).filter(Boolean).slice(0, 3).join(' / ') || 'Choose later',
    requiredConnectors: requiredConnectors.length ? requiredConnectors.slice(0, 4) : ['Connect live data'],
    defaultName: readString(persona.default_name, name),
    persona: [personaRole, personaTone].filter(Boolean).join(' — ') || readString(seed.description),
    systemPrompt: instructions.join('\n') || readString(seed.description),
    knowledgePlaceholder: dataSources.length
      ? dataSources.map((item) => `${readString(item.label, readString(item.source_id))}: ${readString(item.kind, 'source')}`).join('\n')
      : 'Add trusted docs, spreadsheets, URLs, or live data sources here.',
    selectedToolIds: selectedToolIds.length ? selectedToolIds : ['web_search'],
    memoryEnabled: true,
    contextBudgetPreset: readString(runtime.tier).includes('hosted') ? 'standard' : 'extended',
  };
}

export function normalizeStudioTemplates(payload: unknown): StudioTemplate[] {
  const seedTemplates = readItems<StudioProofAgentSeedRecord>(payload)
    .map(mapStudioSeedToTemplate)
    .filter((item): item is StudioTemplate => Boolean(item));
  if (!seedTemplates.length) {
    return sortStudioTemplatesForBusinessFlow(STUDIO_TEMPLATES);
  }
  const seedIds = new Set(seedTemplates.map((template) => normalizeTemplateToken(template.id)));
  const primarySamples = STUDIO_TEMPLATES.filter((template) => (
    PRIMARY_STUDIO_TEMPLATE_IDS.has(normalizeTemplateToken(template.id))
    && !seedIds.has(normalizeTemplateToken(template.id))
  ));
  const staticExtras = STUDIO_TEMPLATES.filter((template) => {
    const templateId = normalizeTemplateToken(template.id);
    return !seedIds.has(templateId) && !PRIMARY_STUDIO_TEMPLATE_IDS.has(templateId);
  });
  return sortStudioTemplatesForBusinessFlow([
    ...seedTemplates,
    ...primarySamples,
    ...staticExtras,
  ]);
}

export function sortStudioTemplatesForBusinessFlow(templates: ReadonlyArray<StudioTemplate>): StudioTemplate[] {
  return [...templates].sort((left, right) => {
    const leftRank = PRIMARY_STUDIO_TEMPLATE_ORDER.indexOf(normalizeTemplateToken(left.id) as typeof PRIMARY_STUDIO_TEMPLATE_ORDER[number]);
    const rightRank = PRIMARY_STUDIO_TEMPLATE_ORDER.indexOf(normalizeTemplateToken(right.id) as typeof PRIMARY_STUDIO_TEMPLATE_ORDER[number]);
    const normalizedLeftRank = leftRank === -1 ? Number.MAX_SAFE_INTEGER : leftRank;
    const normalizedRightRank = rightRank === -1 ? Number.MAX_SAFE_INTEGER : rightRank;
    return normalizedLeftRank - normalizedRightRank;
  });
}

export function applyStudioTemplate(state: WizardState, template: StudioTemplate): WizardState {
  const usesTelegram = template.channelLabel.toLowerCase().includes('telegram');
  return {
    ...state,
    name: template.defaultName || state.name,
    persona: template.persona || '',
    systemPrompt: template.systemPrompt || '',
    knowledgeSourceText: template.id === CUSTOM_STUDIO_TEMPLATE.id ? '' : state.knowledgeSourceText,
    selectedToolIds: template.selectedToolIds,
    memoryEnabled: template.memoryEnabled,
    contextBudgetPreset: template.contextBudgetPreset,
    customerChannel: usesTelegram ? 'telegram' : state.customerChannel,
    telegramEnabled: usesTelegram,
  };
}

export function connectorConnected(connectorIds: string[] | undefined, availableConnectorIds: Set<string>): boolean {
  return (connectorIds ?? []).some((connectorId) => availableConnectorIds.has(connectorId));
}

export function toolLabel(toolId: string): string {
  return STUDIO_TOOL_OPTIONS.find((item) => item.id === toolId)?.label ?? humanizeToken(toolId, toolId);
}

export function readProviderCatalogItems(payload: unknown): ProviderCatalogRecord[] {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const providers = (payload as Record<string, unknown>).providers;
  return Array.isArray(providers)
    ? providers.filter((item): item is ProviderCatalogRecord => Boolean(item) && typeof item === 'object')
    : [];
}

export function studioProviderRank(providerId: string): number {
  switch (providerId) {
    case 'gemini':
      return 0;
    case 'openai':
      return 1;
    case 'anthropic':
      return 2;
    case 'openrouter':
      return 3;
    case 'groq':
      return 4;
    case 'xai':
      return 5;
    case 'deepseek':
      return 6;
    case 'mistral':
      return 7;
    case 'qwen':
      return 8;
    case 'vertex':
      return 9;
    case 'azure_openai':
      return 10;
    case 'bedrock':
      return 11;
    case 'custom_openai_compatible':
      return 12;
    default:
      return 99;
  }
}

export function isStudioApiProvider(
  providerId: string,
  provider: ProviderCatalogRecord,
  providerScopes: string[],
): boolean {
  const normalizedProviderId = providerId.trim().toLowerCase();
  if (!normalizedProviderId || STUDIO_LOCAL_PROVIDER_IDS.has(normalizedProviderId)) {
    return false;
  }
  if (provider.local_only === true) {
    return false;
  }
  if (providerScopes.some((scope) => scope.includes('local') || scope.includes('cli'))) {
    return false;
  }
  return providerScopes.includes('studio_safe') || STUDIO_API_PROVIDER_IDS.has(normalizedProviderId);
}

export function normalizeProviderCatalog(payload: unknown): ProviderCatalogSnapshot[] {
  return readProviderCatalogItems(payload)
    .map((provider) => {
      const providerId = readString(provider.id);
      if (!providerId || readString(provider.kind).toLowerCase() !== 'provider' || provider.hidden === true) {
        return null;
      }
      const providerScopes = Array.isArray(provider.provider_scopes)
        ? provider.provider_scopes.map((item) => readString(item).toLowerCase()).filter(Boolean)
        : [];
      const models = Array.isArray(provider.models)
        ? provider.models
          .filter((item): item is ProviderCatalogModelRecord => Boolean(item) && typeof item === 'object')
          .map((model) => ({
            id: readString(model.id),
            label: readString(model.label, readString(model.id)),
            contextWindowTokens: readNumber(model.context_window_tokens),
            inputCostPer1kUsd: readNumber(model.input_cost_per_1k_usd),
            outputCostPer1kUsd: readNumber(model.output_cost_per_1k_usd),
            supportsTools: model.supports_tools === true,
            supportsVision: model.supports_vision === true,
            supportsJson: model.supports_json === true,
            supportsReasoning: model.supports_reasoning === true,
            localSelfHostedCompatible: model.local_self_hosted_compatible === true,
            pricingKnown: model.pricing_known === true || readNumber(model.input_cost_per_1k_usd) !== null || readNumber(model.output_cost_per_1k_usd) !== null,
            source: readOptionalString(model.source),
            fetchedAt: readOptionalString(model.fetched_at),
            lifecycle: readOptionalString(model.lifecycle),
            capabilityLabels: normalizeLabelList(model.capability_labels),
          }))
          .filter((item) => item.id)
        : [];
      return {
        id: providerId,
        label: readString(provider.label, humanizeToken(providerId, providerId)),
        state: readString(provider.state, 'unknown'),
        defaultModel: readOptionalString(provider.default_model),
        studioVisible: isStudioApiProvider(providerId, provider, providerScopes),
        providerScopes,
        privacyPosture: readOptionalString(provider.privacy_posture),
        jurisdiction: readOptionalString(provider.jurisdiction),
        residency: readOptionalString(provider.residency),
        localSelfHostedCompatible: provider.local_self_hosted_compatible === true,
        capabilityLabels: normalizeLabelList(provider.capability_labels),
        modelsSource: readOptionalString(provider.models_source),
        modelsSyncedAt: readOptionalString(provider.models_synced_at),
        modelsExpiresAt: readOptionalString(provider.models_expires_at),
        modelsError: readOptionalString(provider.models_error),
        models,
      } satisfies ProviderCatalogSnapshot;
    })
    .filter((item): item is ProviderCatalogSnapshot => Boolean(item))
    .filter((item) => item.studioVisible)
    .sort((left, right) => {
      const rankDelta = studioProviderRank(left.id) - studioProviderRank(right.id);
      if (rankDelta !== 0) {
        return rankDelta;
      }
      return left.label.localeCompare(right.label);
    });
}

export function normalizeTelegramIssue(value: unknown): TelegramReadinessIssue | null {
  const record = readRecord(value);
  const message = readString(record.message);
  if (!message) {
    return null;
  }
  return {
    code: readString(record.code, 'telegram_issue'),
    message,
    guidance: readOptionalString(record.guidance),
    severity: readString(record.severity, 'warning'),
  };
}

export function normalizeTelegramConnector(value: unknown): TelegramConnectorOption | null {
  const record = readRecord(value);
  const id = readString(record.id);
  if (!id) {
    return null;
  }
  return {
    id,
    label: readString(record.label, id),
    endpointKey: readOptionalString(record.endpoint_key),
    botUsername: readOptionalString(record.bot_username),
    webhookPath: readOptionalString(record.webhook_path),
    webhookUrl: readOptionalString(record.webhook_url),
    profileStatus: readOptionalString(record.profile_status),
    profileIssue: readOptionalString(record.profile_issue),
    lastError: readOptionalString(record.last_error),
    lastErrorAt: readOptionalString(record.last_error_at),
  };
}

export function normalizeTelegramReadiness(payload: DeployedAgentTelegramReadinessRecord | null | undefined): TelegramReadinessSnapshot | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }
  const blockers = Array.isArray(payload.blockers)
    ? payload.blockers.map((item) => normalizeTelegramIssue(item)).filter((item): item is TelegramReadinessIssue => Boolean(item))
    : [];
  const warnings = Array.isArray(payload.warnings)
    ? payload.warnings.map((item) => normalizeTelegramIssue(item)).filter((item): item is TelegramReadinessIssue => Boolean(item))
    : [];
  const connectors = Array.isArray(payload.connectors)
    ? payload.connectors.map((item) => normalizeTelegramConnector(item)).filter((item): item is TelegramConnectorOption => Boolean(item))
    : [];
  return {
    readyForLive: payload.ready_for_live === true,
    status: readString(payload.status, 'draft'),
    nextAction: readOptionalString(payload.next_action),
    blockers,
    warnings,
    connectors,
    configuredBinding: readRecord(payload.configured_binding),
    webhook: readRecord(payload.webhook),
    autopilot: readRecord(payload.autopilot),
  };
}

export function selectedProviderId(agent?: DeployedAgentRecord | null): string {
  const metadata = readRecord(agent?.metadata);
  return readString(agent?.provider ?? metadata.provider);
}

export function selectedModelId(agent?: DeployedAgentRecord | null): string {
  const metadata = readRecord(agent?.metadata);
  return readString(agent?.model ?? metadata.model);
}

export function providerCatalogById(items: ProviderCatalogSnapshot[]): Record<string, ProviderCatalogSnapshot> {
  return Object.fromEntries(items.map((item) => [item.id, item]));
}

export function formatDeploymentModelSummary(
  agent: DeployedAgentRecord | null | undefined,
  catalogByProvider: Record<string, ProviderCatalogSnapshot>,
): string {
  const providerId = selectedProviderId(agent);
  const modelId = selectedModelId(agent);
  if (!providerId && !modelId) {
    return 'Model not pinned';
  }
  const provider = providerId ? catalogByProvider[providerId] ?? null : null;
  const model = provider?.models.find((item) => item.id === modelId) ?? null;
  const providerLabel = provider?.label ?? humanizeToken(providerId, providerId || 'Provider');
  const modelLabel = model?.label ?? (modelId || 'Default model');
  return `${providerLabel} · ${modelLabel}`;
}

export function formatDeploymentModelLabel(
  agent: DeployedAgentRecord | null | undefined,
  catalogByProvider: Record<string, ProviderCatalogSnapshot>,
): string {
  const providerId = selectedProviderId(agent);
  const modelId = selectedModelId(agent);
  if (!providerId && !modelId) {
    return 'Default model';
  }
  const provider = providerId ? catalogByProvider[providerId] ?? null : null;
  const model = provider?.models.find((item) => item.id === modelId) ?? null;
  return model?.label ?? modelId ?? provider?.label ?? humanizeToken(providerId, providerId || 'Model');
}

export function providerIsReadyForStudio(provider: ProviderCatalogSnapshot | null | undefined): boolean {
  const state = readString(provider?.state).toLowerCase();
  return ['ready', 'connected', 'active', 'configured'].includes(state);
}

export function agentModelDeployBlocker(
  agent: DeployedAgentRecord | null | undefined,
  catalogByProvider: Record<string, ProviderCatalogSnapshot>,
): string | null {
  if (!agent) {
    return null;
  }
  const providerId = selectedProviderId(agent);
  const modelId = selectedModelId(agent);
  if (!providerId || !modelId) {
    return 'Connect a model provider before launch.';
  }
  const provider = catalogByProvider[providerId] ?? null;
  if (!provider || !providerIsReadyForStudio(provider)) {
    return 'Connect a model provider before launch.';
  }
  return null;
}

export function normalizeWizardAiTier(value: unknown): WizardState['aiTier'] {
  const token = readString(value).toLowerCase().replace('-', '_');
  if (token === 'light' || token === 'pro' || token === 'max') {
    return token;
  }
  return 'pro';
}

export function normalizeStudioAiSource(value: unknown): WizardState['aiSource'] {
  const token = readString(value).toLowerCase().replace(/[-\s]+/g, '_');
  if (
    token === 'workspace_api_key'
    || token === 'my_api_key'
    || token === 'my_ai_account'
    || token === 'byok'
    || token === 'api_key'
  ) {
    return 'workspace_api_key';
  }
  if (token === 'local_model' || token === 'local_ai' || token === 'local') {
    return 'local_model';
  }
  if (token === 'subscription_passthrough' || token === 'subscription' || token === 'third_party_subscription') {
    return 'subscription_passthrough';
  }
  return 'empyralis_credits';
}

export function normalizeRuntimePlacement(value: unknown): WizardState['runtimePlacement'] {
  const token = readString(value).toLowerCase().replace('-', '_');
  if (
    token === 'hosted_hardware_pool'
    || token === 'empyralis_hosted'
    || token === 'empyralis_hosted_device'
    || token === 'empyralis_hardware_pool'
  ) {
    return 'hosted_hardware_pool';
  }
  if (
    token === 'local'
    || token === 'local_secure'
    || token === 'local_companion'
    || token === 'local_computer'
    || token === 'customer_local'
    || token === 'this_computer'
  ) {
    return 'customer_local';
  }
  if (
    token === 'self_hosted'
    || token === 'self_hosted_business'
    || token === 'self_host_runtime'
    || token === 'customer_hosted'
    || token === 'self_hosted_business_node'
  ) {
    return 'customer_hosted';
  }
  return 'managed_cloud';
}

export function normalizeRuntimeAccessMode(value: unknown, legacyRequiresOwnerApproval?: unknown): WizardState['runtimeAccessMode'] {
  void legacyRequiresOwnerApproval;
  const token = readString(value).toLowerCase().replace(/[-\s]+/g, '_');
  if (token === 'custom') {
    return 'custom';
  }
  if (token === 'default_guarded' || token === 'guarded' || token === 'default') {
    return 'default_guarded';
  }
  return 'default_guarded';
}

export function runtimeTargetForPlacement(value: WizardState['runtimePlacement']): string {
  if (value === 'customer_local') {
    return 'local';
  }
  if (value === 'customer_hosted') {
    return 'self_hosted';
  }
  return 'cloud';
}

export function runtimeSupplierForPlacement(value: WizardState['runtimePlacement']): WizardState['runtimeSupplierKind'] {
  return STUDIO_RUNTIME_OPTIONS.find((item) => item.value === value)?.supplier ?? 'empyralis';
}

export function runtimePlacementLabel(value: unknown): string {
  const placement = normalizeRuntimePlacement(value);
  return STUDIO_RUNTIME_OPTIONS.find((item) => item.value === placement)?.label ?? 'Cloud worker';
}

export function testRuntimeModeForPlacement(value: unknown): string {
  const placement = normalizeRuntimePlacement(value);
  if (placement === 'customer_hosted') {
    return 'self_hosted_agent';
  }
  if (placement === 'customer_local') {
    return 'my_computer_agent';
  }
  if (placement === 'hosted_hardware_pool') {
    return 'cloud_computer_agent';
  }
  return 'text_agent';
}

export function agentRuntimePlacement(agent: DeployedAgentRecord | null | undefined): WizardState['runtimePlacement'] {
  const config = readRecord(agent?.config);
  const metadata = readRecord(agent?.metadata);
  return normalizeRuntimePlacement(
    config.runtime_placement
    ?? metadata.runtime_placement
    ?? agent?.runtime_target,
  );
}

export function normalizeApprovalModeFromPolicies(escalationPreset: unknown, handoffMode: unknown): WizardState['approvalMode'] {
  const escalation = readString(escalationPreset).toLowerCase();
  const handoff = readString(handoffMode).toLowerCase();
  if (escalation === 'conservative' || handoff === 'pause_thread') {
    return 'guarded';
  }
  if (escalation === 'autonomous' || handoff === 'summary_only') {
    return 'autonomous';
  }
  return 'balanced';
}

export function applyApprovalModeToWizardState(
  mode: WizardState['approvalMode'],
  current: Pick<WizardState, 'escalationPreset' | 'handoffMode'>,
): Pick<WizardState, 'escalationPreset' | 'handoffMode'> {
  if (mode === 'guarded') {
    return {
      escalationPreset: 'conservative',
      handoffMode: 'pause_thread',
    };
  }
  if (mode === 'autonomous') {
    return {
      escalationPreset: 'autonomous',
      handoffMode: 'summary_only',
    };
  }
  return {
    escalationPreset: 'standard',
    handoffMode: current.handoffMode === 'summary_only' ? 'notify_owner' : current.handoffMode || 'notify_owner',
  };
}

export function inferCustomerChannel(channels: Record<string, unknown>): WizardState['customerChannel'] {
  if (readRecord(channels.telegram).enabled === true) {
    return 'telegram';
  }
  if (readRecord(channels.whatsapp).enabled === true) {
    return 'whatsapp';
  }
  if (readRecord(channels.web_widget).enabled === true) {
    return 'web_widget';
  }
  return 'draft';
}

export function inferAiTierFromProviderModel(providerId: string, modelId: string): WizardState['aiTier'] {
  const model = modelId.trim().toLowerCase();
  if (/flash|mini|haiku|small|lite|fast/.test(model)) {
    return 'light';
  }
  if (/opus|max|premium|large|gpt-5\.5/.test(model)) {
    return 'max';
  }
  return 'pro';
}

export function modelPreferenceScore(model: ProviderCatalogModelSnapshot, tier: WizardState['aiTier']): number {
  const id = `${model.id} ${model.label}`.toLowerCase();
  if (tier === 'light') {
    if (/flash|mini|haiku|small|lite|fast/.test(id)) {
      return 0;
    }
    return model.inputCostPer1kUsd !== null ? 10 + model.inputCostPer1kUsd : 50;
  }
  if (tier === 'max') {
    if (/opus|max|premium|large|gpt-5\.2|gpt-5\.5/.test(id)) {
      return 0;
    }
    if (/pro|sonnet|gpt-5|reason/.test(id)) {
      return 4;
    }
    return 20;
  }
  if (/pro|sonnet|gpt-5|reason/.test(id)) {
    return 0;
  }
  if (/flash|mini|haiku|small|lite|fast/.test(id)) {
    return 8;
  }
  return 4;
}

export function pickStudioModelForTier(provider: ProviderCatalogSnapshot, tier: WizardState['aiTier']): string {
  if (provider.models.length === 0) {
    return '';
  }
  const defaultModel = provider.defaultModel && provider.models.some((item) => item.id === provider.defaultModel)
    ? provider.defaultModel
    : '';
  if (tier === 'pro' && defaultModel) {
    return defaultModel;
  }
  const rankedModels = [...provider.models].sort((left, right) => {
    const scoreDelta = modelPreferenceScore(left, tier) - modelPreferenceScore(right, tier);
    if (scoreDelta !== 0) {
      return scoreDelta;
    }
    return left.label.localeCompare(right.label);
  });
  return rankedModels[0]?.id || defaultModel || provider.models[0]?.id || '';
}

export function providerReadyForStudio(provider: ProviderCatalogSnapshot | null | undefined): boolean {
  const state = readString(provider?.state).toLowerCase();
  return state === 'ready' || state === 'connected' || state === 'active';
}

export function resolveEmpyralisProviderModelForTier(
  tier: WizardState['aiTier'],
): { providerId: string; modelId: string } {
  // Internal routing IDs — NEVER surface providerId or modelId to the consumer.
  // Consumer-facing labels come from PLATFORM_MODEL_LABELS / platform-brand helpers.
  if (tier === 'light') {
    return { providerId: 'deepseek', modelId: 'deepseek-v4-flash' };
  }
  if (tier === 'max') {
    return { providerId: 'deepseek', modelId: 'deepseek-v4-pro' };
  }
  return { providerId: 'deepseek', modelId: 'deepseek-v4-pro' };
}

export function resolveProviderModelForTier(
  tier: WizardState['aiTier'],
  catalog: ProviderCatalogSnapshot[],
): { providerId: string; modelId: string } {
  if (tier === 'light' || tier === 'pro' || tier === 'max') {
    return resolveEmpyralisProviderModelForTier(tier);
  }
  const catalogByProvider = providerCatalogById(catalog);
  const providerOrder = ['gemini', 'openai', 'anthropic', 'openrouter', 'groq', 'xai', 'deepseek', 'mistral', 'qwen', 'azure_openai', 'vertex', 'ollama_cloud', 'custom_openai_compatible'];
  const orderedProviders = providerOrder
    .map((providerId) => catalogByProvider[providerId] ?? null)
    .filter((provider): provider is ProviderCatalogSnapshot => Boolean(provider) && provider.models.length > 0)
    .sort((left, right) => {
      const readinessDelta = Number(!providerReadyForStudio(left)) - Number(!providerReadyForStudio(right));
      if (readinessDelta !== 0) {
        return readinessDelta;
      }
      return providerOrder.indexOf(left.id) - providerOrder.indexOf(right.id);
    });
  for (const provider of orderedProviders) {
    return {
      providerId: provider.id,
      modelId: pickStudioModelForTier(provider, tier),
    };
  }
  const sampleProvider = catalogByProvider.gemini ? catalogByProvider.gemini : catalog[0] ?? null;
  if (!sampleProvider) {
    return {
      providerId: '',
      modelId: '',
    };
  }
  const sampleModel = sampleProvider.defaultModel && sampleProvider.models.some((item) => item.id === sampleProvider.defaultModel)
    ? sampleProvider.defaultModel
    : sampleProvider.models[0]?.id || '';
  return {
    providerId: sampleProvider.id,
    modelId: sampleModel,
  };
}

export function applyProviderCatalogDefaults(
  state: WizardState,
  catalog: ProviderCatalogSnapshot[],
): WizardState {
  if (catalog.length === 0) {
    return state;
  }
  const tierRoute = resolveProviderModelForTier(state.aiTier, catalog);
  const catalogByProvider = providerCatalogById(catalog);
  const preferredProviderId = tierRoute.providerId || (catalogByProvider.gemini ? 'gemini' : catalog[0]?.id || '');
  const providerId = state.providerId && catalogByProvider[state.providerId]
    ? state.providerId
    : preferredProviderId;
  const provider = catalogByProvider[providerId] ?? null;
  const availableModels = provider?.models ?? [];
  const preferredModelId = providerId === tierRoute.providerId ? tierRoute.modelId : '';
  const nextModelId = state.modelId && availableModels.some((item) => item.id === state.modelId)
    ? state.modelId
    : preferredModelId && availableModels.some((item) => item.id === preferredModelId)
      ? preferredModelId
      : provider?.defaultModel && availableModels.some((item) => item.id === provider.defaultModel)
        ? provider.defaultModel
        : availableModels[0]?.id || '';
  if (providerId === state.providerId && nextModelId === state.modelId) {
    return state;
  }
  return {
    ...state,
    providerId,
    modelId: nextModelId,
  };
}

export function buildWizardState(agent?: DeployedAgentRecord | null): WizardState {
  const isNewDraft = !agent;
  const channels = readRecord(agent?.channels);
  const telegram = readRecord(channels.telegram);
  const config = readRecord(agent?.config);
  const customerPolicy = readRecord(config.customer_policy);
  const memoryPolicy = readRecord(config.memory_policy);
  const safetyPolicy = readRecord(config.safety_policy);
  const commercePolicy = readRecord(config.commerce_policy);
  const escalationPolicy = readRecord(config.escalation_policy);
  const computerAutomation = readRecord(config.computer_automation ?? readRecord(agent?.metadata).computer_automation);
  const metadata = readRecord(agent?.metadata);
  const selfHostedBinding = readRecord(metadata.self_hosted_runtime_binding);
  const privacyContractSnapshot = readRecord(metadata.privacy_contract_snapshot);
  const computerSafetyContractSnapshot = readRecord(metadata.computer_safety_contract_snapshot);
  const runtimeSupply = readRecord(config.runtime_supply ?? metadata.runtime_supply);
  const runtimeSupplySupplier = readRecord(runtimeSupply.supplier);
  const runtimeSupplyMarketplace = readRecord(runtimeSupply.marketplace_policy);
  const runtimeSupplyModelTier = readRecord(runtimeSupply.model_tier);
  const runtimeSupplyAiSource = readRecord(runtimeSupply.ai_source);
  const runtimeSupplyProviderBinding = readRecord(runtimeSupply.provider_binding);
  const rawProviderId = readString(agent?.provider ?? metadata.provider ?? runtimeSupplyProviderBinding.internal_provider);
  const rawModelId = readString(agent?.model ?? metadata.model ?? runtimeSupplyProviderBinding.internal_model);
  const aiTier = normalizeWizardAiTier(
    metadata.public_tier
    ?? metadata.model_tier
    ?? metadata.empyralis_model_tier
    ?? inferAiTierFromProviderModel(rawProviderId, rawModelId),
  );
  const aiSource = normalizeStudioAiSource(
    runtimeSupplyAiSource.kind
    ?? runtimeSupplyAiSource.payer
    ?? runtimeSupplyModelTier.billing_source
    ?? metadata.billing_source,
  );
  const empyralisRoute = aiSource === 'empyralis_credits'
    ? resolveEmpyralisProviderModelForTier(aiTier)
    : null;
  const providerId = empyralisRoute?.providerId ?? rawProviderId;
  const modelId = empyralisRoute?.modelId ?? rawModelId;
  const customerChannel = inferCustomerChannel(channels);
  const runtimePlacement = normalizeRuntimePlacement(
    readRecord(runtimeSupply.placement).kind
    ?? config.runtime_placement
    ?? metadata.runtime_placement
    ?? metadata.studio_runtime
    ?? metadata.runtime
    ?? agent?.runtime_target,
  );
  const runtimeSupplierKind = readString(runtimeSupplySupplier.kind) === 'customer'
    ? 'customer'
    : readString(runtimeSupplySupplier.kind) === 'third_party_certified'
      ? 'third_party_certified'
      : runtimeSupplierForPlacement(runtimePlacement);
  const approvalMode = normalizeApprovalModeFromPolicies(
    escalationPolicy.preset ?? metadata.escalation_preset,
    escalationPolicy.handoff_mode ?? metadata.handoff_mode,
  );
  const selectedToolIds = normalizeToolIds(readRecord(config.tool_policy).enabled_tools ?? metadata.selected_tool_ids);
  return {
    name: readString(agent?.name),
    avatar: readString(agent?.avatar),
    persona: agent ? readString(agent.persona) : DEFAULT_SAFE_AGENT_PERSONA,
    systemPrompt: agent ? readString(agent.system_prompt) : DEFAULT_SAFE_AGENT_SYSTEM_PROMPT,
    knowledgeSourceText: serializeKnowledgeSources(agent?.knowledge_sources),
    aiTier,
    aiSource,
    runtimeSupplierKind,
    runtimeSupplierId: readString(runtimeSupplySupplier.id, runtimeSupplierKind),
    runtimeSupplierLabel: readString(runtimeSupplySupplier.label, runtimeSupplierKind === 'empyralis' ? 'Empyralis' : 'Customer-owned compute'),
    runtimePlacement,
    marketplacePublishAllowed: readString(runtimeSupplyMarketplace.visibility) === 'marketplace',
    thirdPartyRuntimeAllowed: runtimeSupplyMarketplace.third_party_runtime_allowed === true,
    computerAutomationEnabled: computerAutomation.enabled === true,
    computerAutomationRuntimeClass: readString(computerAutomation.runtime_class, 'virtual_browser') as WizardState['computerAutomationRuntimeClass'],
    computerAutomationAllowedDomains: Array.isArray(computerAutomation.allowed_domains)
      ? computerAutomation.allowed_domains.map((item) => readString(item)).filter(Boolean).join(', ')
      : '',
    computerAutomationMaxSessions: readIntegerString(computerAutomation.max_concurrent_sessions) || '1',
    computerAutomationDailyBudgetUsd: readPositiveDecimalString(computerAutomation.daily_budget_usd),
    computerAutomationMonthlyBudgetUsd: readPositiveDecimalString(computerAutomation.monthly_budget_usd),
    runtimeAccessMode: normalizeRuntimeAccessMode(
      computerAutomation.runtime_access_mode,
      computerAutomation.requires_owner_approval,
    ),
    approvalMode,
    customerChannel,
    telegramEnabled: customerChannel === 'telegram' && telegram.enabled === true,
    telegramConnectorId: readString(telegram.connector_id ?? telegram.credential_id),
    telegramEndpointKey: readString(telegram.endpoint_key),
    providerId,
    modelId,
    runtimeTarget: readString(agent?.runtime_target, runtimeTargetForPlacement(runtimePlacement)),
    selfHostedRuntimeProfileId: readString(
      config.runtime_profile_id
      ?? selfHostedBinding.runtime_profile_id
      ?? metadata.runtime_profile_id,
    ),
    selfHostedPrivacyAccepted: Boolean(
      readOptionalString(privacyContractSnapshot.accepted_at)
      || readOptionalString(metadata.privacy_contract_accepted_at),
    ),
    selfHostedSafetyAccepted: Boolean(
      readOptionalString(computerSafetyContractSnapshot.accepted_at)
      || readOptionalString(metadata.computer_safety_contract_accepted_at),
    ),
    billingPlan: readString(agent?.billing_plan, 'free'),
    selectedToolIds: selectedToolIds.length > 0 ? selectedToolIds : ['spreadsheet_read', 'spreadsheet_append'],
    memoryEnabled: agent
      ? memoryPolicy.memory_enabled === true || metadata.memory_enabled === true
      : false,
    contextBudgetPreset: normalizeContextPresetId(readString(memoryPolicy.context_budget_preset ?? metadata.context_budget_preset, 'standard')),
    retentionPreset: readString(memoryPolicy.retention_preset ?? metadata.retention_preset, 'standard'),
    healthSafetyEnabled: safetyPolicy.health_safety_enabled === true || metadata.health_safety_enabled === true,
    healthSafetyAssistantName: readString(safetyPolicy.assistant_name ?? metadata.health_safety_assistant_name),
    pausedMessage: readString(customerPolicy.paused_message ?? metadata.paused_message),
    welcomeIntro: readString(customerPolicy.public_intro ?? metadata.public_intro, isNewDraft ? 'Scan to browse the menu, check specials, and place an order in minutes.' : ''),
    welcomeCoreValue: readString(customerPolicy.public_core_value ?? metadata.public_core_value, isNewDraft ? 'Menu answers, availability, and order confirmation in one Telegram flow.' : ''),
    publicStartCtaLabel: readString(customerPolicy.public_start_cta_label ?? metadata.platform_cta_label, isNewDraft ? 'Open menu' : ''),
    publicStartCtaUrl: readString(customerPolicy.public_start_cta_url ?? metadata.platform_cta_url),
    escalationPreset: readString(escalationPolicy.preset ?? metadata.escalation_preset, 'standard'),
    handoffMode: readString(escalationPolicy.handoff_mode ?? metadata.handoff_mode, 'notify_owner'),
    ownerNotificationDestination: readString(
      escalationPolicy.owner_notification_destination ?? metadata.owner_notification_destination,
    ),
    dailyMessageLimit: readIntegerString(customerPolicy.daily_message_limit ?? metadata.daily_message_limit),
    monthlyCostCapUsd: readPositiveDecimalString(
      commercePolicy.monthly_cost_cap_usd ?? metadata.monthly_cost_cap_usd,
    ) || DEFAULT_SAFE_MONTHLY_COST_CAP_USD,
    upgradeCtaUrl: readString(customerPolicy.upgrade_cta_url ?? metadata.upgrade_cta_url),
    upgradeCtaLabel: readString(customerPolicy.upgrade_cta_label ?? metadata.upgrade_cta_label),
  };
}

export function buildCreateDraftWizardState(state: WizardState): WizardState {
  return {
    ...state,
    selectedToolIds: [],
    memoryEnabled: false,
    telegramEnabled: false,
    telegramConnectorId: '',
    telegramEndpointKey: '',
    runtimePlacement: STUDIO_DEFAULT_RUNTIME_PLACEMENT,
    runtimeTarget: runtimeTargetForPlacement(STUDIO_DEFAULT_RUNTIME_PLACEMENT),
    runtimeSupplierKind: runtimeSupplierForPlacement(STUDIO_DEFAULT_RUNTIME_PLACEMENT),
    runtimeSupplierId: 'empyralis',
    runtimeSupplierLabel: 'Empyralis',
    selfHostedRuntimeProfileId: '',
    selfHostedPrivacyAccepted: false,
    selfHostedSafetyAccepted: false,
    computerAutomationEnabled: false,
    runtimeAccessMode: 'default_guarded',
    monthlyCostCapUsd: state.monthlyCostCapUsd || DEFAULT_SAFE_MONTHLY_COST_CAP_USD,
    dailyMessageLimit: '',
    upgradeCtaLabel: '',
    upgradeCtaUrl: '',
    approvalMode: 'balanced',
    escalationPreset: 'standard',
    handoffMode: 'notify_owner',
  };
}

export function truncateExternalUserId(value: unknown): string {
  const token = readString(value);
  if (!token) {
    return 'unknown';
  }
  return token.length <= 8 ? token : token.slice(0, 8);
}

export function buildChannelPayload(state: WizardState): Record<string, unknown> {
  const telegramEnabled = state.customerChannel === 'telegram' && state.telegramEnabled;
  const whatsappEnabled = state.customerChannel === 'whatsapp';
  const webWidgetEnabled = state.customerChannel === 'web_widget';
  return {
    telegram: {
      enabled: telegramEnabled,
      connector_id: state.telegramConnectorId.trim() || undefined,
      credential_id: state.telegramConnectorId.trim() || undefined,
      endpoint_key: state.telegramEndpointKey.trim() || undefined,
    },
    whatsapp: {
      enabled: whatsappEnabled,
      availability: 'roadmap',
    },
    instagram: {
      enabled: false,
      availability: 'roadmap',
    },
    web_widget: {
      enabled: webWidgetEnabled,
      availability: 'roadmap',
    },
  };
}

export function buildDeploymentConfig(state: WizardState): Record<string, unknown> {
  const dailyMessageLimit = state.dailyMessageLimit.trim();
  const monthlyCostCapUsd = state.monthlyCostCapUsd.trim();
  const automationDailyBudget = state.computerAutomationDailyBudgetUsd.trim();
  const automationMonthlyBudget = state.computerAutomationMonthlyBudgetUsd.trim();
  const automationMaxSessions = state.computerAutomationMaxSessions.trim();
  const automationAllowedDomains = state.computerAutomationAllowedDomains
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  const runtimeSupplier = runtimeSupplierForPlacement(state.runtimePlacement);
  const runtimeAccessMode = state.computerAutomationEnabled
    ? state.runtimeAccessMode
    : 'default_guarded';
  const computerAutomation = {
    enabled: state.computerAutomationEnabled,
    runtime_class: state.computerAutomationEnabled ? state.computerAutomationRuntimeClass : null,
    allowed_domains: state.computerAutomationEnabled ? automationAllowedDomains : [],
    max_concurrent_sessions: state.computerAutomationEnabled && automationMaxSessions ? Number(automationMaxSessions) : 0,
    daily_budget_usd: state.computerAutomationEnabled && automationDailyBudget ? Number(automationDailyBudget) : null,
    monthly_budget_usd: state.computerAutomationEnabled && automationMonthlyBudget ? Number(automationMonthlyBudget) : null,
    runtime_access_mode: runtimeAccessMode,
    requires_owner_approval: true,
  };
  return {
    runtime_placement: state.runtimePlacement,
    runtime_profile_id: state.runtimePlacement === 'customer_hosted'
      ? state.selfHostedRuntimeProfileId.trim() || null
      : null,
    runtime_supplier: runtimeSupplier,
    runtime_supply: {
      schema_version: 1,
      supplier: {
        kind: runtimeSupplier,
        id: runtimeSupplier === 'empyralis' ? 'empyralis' : state.runtimeSupplierId.trim() || runtimeSupplier,
        label: runtimeSupplier === 'empyralis' ? 'Empyralis' : state.runtimeSupplierLabel.trim() || 'Customer-owned compute',
      },
      placement: {
        kind: state.runtimePlacement,
        runtime_target: runtimeTargetForPlacement(state.runtimePlacement),
      },
      computer_automation: computerAutomation,
      marketplace_policy: {
        visibility: state.marketplacePublishAllowed ? 'marketplace' : 'private',
        third_party_runtime_allowed: state.thirdPartyRuntimeAllowed,
      },
      model_tier: {
        public_tier: state.aiTier,
        public_label: STUDIO_AI_TIER_OPTIONS.find((item) => item.value === state.aiTier)?.label ?? 'Pro',
        billing_source: state.aiSource,
      },
      ai_source: {
        kind: state.aiSource,
      },
      provider_binding: {
        internal_provider: state.providerId || null,
        internal_model: state.modelId || null,
        expose_provider_model_to_ordinary_ui: false,
      },
    },
    customer_policy: {
      paused_message: state.pausedMessage.trim() || null,
      public_intro: state.welcomeIntro.trim() || null,
      public_core_value: state.welcomeCoreValue.trim() || null,
      public_start_cta_label: state.publicStartCtaLabel.trim() || null,
      public_start_cta_url: state.publicStartCtaUrl.trim() || null,
      daily_message_limit: dailyMessageLimit ? Number(dailyMessageLimit) : null,
      upgrade_cta_url: state.upgradeCtaUrl.trim() || null,
      upgrade_cta_label: state.upgradeCtaLabel.trim() || null,
    },
    memory_policy: {
      memory_enabled: state.memoryEnabled,
      context_budget_preset: normalizeContextPresetId(state.contextBudgetPreset),
      retention_preset: state.retentionPreset,
    },
    safety_policy: {
      health_safety_enabled: state.healthSafetyEnabled,
      assistant_name: state.healthSafetyAssistantName.trim() || null,
    },
    commerce_policy: {
      monthly_cost_cap_usd: monthlyCostCapUsd ? Number(monthlyCostCapUsd) : null,
    },
    tool_policy: {
      enabled_tools: state.selectedToolIds,
    },
    computer_automation: computerAutomation,
    escalation_policy: {
      preset: state.escalationPreset,
      handoff_mode: state.handoffMode,
      owner_notification_destination: state.ownerNotificationDestination.trim() || null,
    },
  };
}

export function buildDetailConfigDraft(agent?: DeployedAgentRecord | null): DetailConfigDraft {
  const state = buildWizardState(agent);
  return {
    persona: DEFAULT_SAFE_AGENT_PERSONA_VALUES.includes(state.persona) ? '' : state.persona,
    systemPrompt: DEFAULT_SAFE_AGENT_SYSTEM_PROMPT_VALUES.includes(state.systemPrompt) ? '' : state.systemPrompt,
    knowledgeSourceText: state.knowledgeSourceText,
    aiTier: state.aiTier,
    aiSource: state.aiSource,
    providerId: state.providerId,
    modelId: state.modelId,
    billingPlan: state.billingPlan,
    selectedToolIds: state.selectedToolIds,
    memoryEnabled: state.memoryEnabled,
    contextBudgetPreset: state.contextBudgetPreset,
    retentionPreset: state.retentionPreset,
  };
}

export function readBudgetCycle(agent?: DeployedAgentRecord | null): Record<string, unknown> {
  const metadata = readRecord(agent?.metadata);
  return readRecord(metadata.current_budget_cycle);
}

export function isEscalationOutstanding(value: unknown): boolean {
  const token = readString(value).toLowerCase();
  return token !== '' && !['clear', 'completed', 'resolved', 'approved'].includes(token);
}

export function conversationCountLabel(count: number, hasMore = false): string {
  if (count <= 0) {
    return 'No conversations';
  }
  const display = hasMore ? `${count}+` : `${count}`;
  return `${display} conversation${count === 1 && !hasMore ? '' : 's'}`;
}

export function unresolvedEscalationLabel(count: number): string {
  if (count <= 0) {
    return 'No open escalations';
  }
  return `${count} unresolved`;
}

export function summarizeConversationMetrics(
  items: DeployedAgentConversationRecord[],
  options?: {
    hasMore?: boolean;
  },
): AgentOperationalMetrics {
  const hasMore = options?.hasMore === true;
  const ordered = [...items].sort((left, right) => {
    const leftTs = readString(left.last_message_at);
    const rightTs = readString(right.last_message_at);
    if (leftTs === rightTs) {
      return readString(left.session_id).localeCompare(readString(right.session_id));
    }
    return rightTs.localeCompare(leftTs);
  });
  const latest = ordered[0] ?? null;
  const unresolvedEscalations = items.filter((item) => isEscalationOutstanding(item.escalation_state)).length;
  return {
    conversationCount: items.length,
    conversationCountLabel: conversationCountLabel(items.length, hasMore),
    latestActivityAt: readOptionalString(latest?.last_message_at),
    latestActivityLabel: latest?.last_message_at ? `Latest ${formatTimestamp(latest.last_message_at)}` : 'No recent activity',
    unresolvedEscalations,
    unresolvedEscalationsLabel: unresolvedEscalationLabel(unresolvedEscalations),
    latestChannel: readOptionalString(latest?.channel),
  };
}

export function buildMetricsPlaceholder(): AgentOperationalMetrics {
  return {
    conversationCount: 0,
    conversationCountLabel: 'Syncing inbox',
    latestActivityAt: null,
    latestActivityLabel: 'Fetching recent customer activity',
    unresolvedEscalations: 0,
    unresolvedEscalationsLabel: 'Open escalation state pending',
    latestChannel: null,
  };
}

function readAnalyticsSeriesItems(payload: DeployedAgentAnalyticsRecord): Record<string, unknown>[] {
  const candidateKeys = ['daily_series', 'time_series', 'timeseries', 'series', 'activity_series'];
  for (const key of candidateKeys) {
    const value = payload[key];
    if (Array.isArray(value)) {
      return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
    }
  }
  return [];
}

function normalizeAnalyticsSeries(payload: DeployedAgentAnalyticsRecord): AgentAnalyticsSeriesPoint[] {
  return readAnalyticsSeriesItems(payload)
    .map((item) => {
      const date = readOptionalString(item.date ?? item.day ?? item.bucket ?? item.timestamp);
      const label = readString(item.label, date ?? '');
      return {
        date,
        label,
        conversations: readNumber(item.conversations ?? item.conversation_count ?? item.sessions ?? item.session_count) ?? 0,
        messages: readNumber(item.messages ?? item.message_count ?? item.message_volume) ?? 0,
      };
    })
    .filter((item) => item.label || item.date);
}

export function normalizeAgentAnalytics(payload: DeployedAgentAnalyticsRecord | null | undefined): AgentAnalyticsSnapshot | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }
  const messageVolume = readRecord(payload.message_volume);
  const escalation = readRecord(payload.escalation);
  const outcomes = readRecord(payload.outcomes);
  const costBurn = readRecord(payload.cost_burn);
  const counts = readRecord(outcomes.counts);
  const normalizedOutcomes = Object.entries(counts)
    .map(([key, value]) => [key, readNumber(value) ?? 0] as [string, number])
    .filter(([, value]) => value > 0)
    .sort((left, right) => {
      if (left[1] === right[1]) {
        return left[0].localeCompare(right[0]);
      }
      return right[1] - left[1];
    });
  return {
    activeUsersLast30d: readNumber(payload.active_users_last_30d) ?? 0,
    messageVolumeDay: readNumber(messageVolume.day) ?? 0,
    messageVolumeWeek: readNumber(messageVolume.week) ?? 0,
    messageVolumeMonth: readNumber(messageVolume.month) ?? 0,
    latestMessageAt: readOptionalString(messageVolume.latest_message_at),
    escalationTotalSessions: readNumber(escalation.total_sessions) ?? 0,
    escalationSessionCount: readNumber(escalation.escalated_sessions) ?? 0,
    escalationRatePercent: readNumber(escalation.rate_percent) ?? 0,
    outcomes: normalizedOutcomes,
    topOutcome: readOptionalString(outcomes.top_outcome),
    currentBurnUsd: readNumber(costBurn.current_burn_usd),
    costCapUsd: readNumber(costBurn.cap_usd),
    percentUsed: readNumber(costBurn.percent_used),
    usageMonth: readOptionalString(costBurn.usage_month),
    activitySeries: normalizeAnalyticsSeries(payload),
  };
}

export function formatOutcomeSummary(snapshot: AgentAnalyticsSnapshot | null): string {
  if (!snapshot || snapshot.outcomes.length === 0) {
    return 'No outcomes yet';
  }
  return snapshot.outcomes
    .slice(0, 3)
    .map(([key, value]) => `${humanizeToken(key, key)} ${value}`)
    .join(' · ');
}

export function formatRosterMetricValue(value: number, emptyLabel: string): string {
  return value > 0 ? formatCompactCount(value) : emptyLabel;
}

export function formatRosterOutcomeMetric(snapshot: AgentAnalyticsSnapshot | null): { label: string; value: string } {
  if (!snapshot || snapshot.outcomes.length === 0) {
    return { label: 'Outcome', value: 'None yet' };
  }
  const [topOutcome, topCount] = snapshot.outcomes[0];
  return {
    label: humanizeToken(topOutcome, 'Outcome'),
    value: formatCompactCount(topCount),
  };
}

export function formatRosterSpendMetric(snapshot: AgentAnalyticsSnapshot | null): string {
  if (!snapshot || snapshot.currentBurnUsd === null || !Number.isFinite(snapshot.currentBurnUsd)) {
    return 'n/a';
  }
  return formatUsd(snapshot.currentBurnUsd);
}

export function matchesConversationFilters(
  conversation: DeployedAgentConversationRecord,
  filters: ConversationFilters,
): boolean {
  const channel = readString(conversation.channel).toLowerCase();
  const escalationState = readString(conversation.escalation_state).toLowerCase();
  const outcome = readString(conversation.outcome).toLowerCase();
  if (filters.channel !== 'all' && channel !== filters.channel) {
    return false;
  }
  if (filters.escalationState !== 'all' && escalationState !== filters.escalationState) {
    return false;
  }
  if (filters.outcome !== 'all' && outcome !== filters.outcome) {
    return false;
  }
  return true;
}

export function upsertAgentRecord(items: DeployedAgentRecord[], nextRecord: DeployedAgentRecord): DeployedAgentRecord[] {
  const nextId = readString(nextRecord.id);
  if (!nextId) {
    return items;
  }
  const existingIndex = items.findIndex((item) => readString(item.id) === nextId);
  if (existingIndex === -1) {
    return [nextRecord, ...items];
  }
  const copy = [...items];
  copy[existingIndex] = nextRecord;
  return copy;
}

export function conversationCustomerLabel(item: DeployedAgentConversationRecord): string {
  const customer = readRecord(item.customer);
  return readString(customer.label ?? customer.display_name ?? customer.id, 'Unknown customer');
}

export function transcriptEntryTitle(entry: TimelineEntry): string {
  const kind = readString(entry.kind).toLowerCase();
  if (kind === 'message') {
    return readString(entry.direction).toLowerCase() === 'inbound' ? 'Customer message' : 'Agent response';
  }
  if (kind === 'tool_call') {
    return 'Tool call';
  }
  if (kind === 'approval') {
    return 'Approval event';
  }
  if (kind === 'escalation') {
    return 'Escalation';
  }
  return humanizeToken(entry.kind, 'Transcript event');
}

export function transcriptEntryBody(entry: TimelineEntry): string {
  const kind = readString(entry.kind).toLowerCase();
  if (kind === 'message') {
    return readString(entry.text, 'No message text was logged.');
  }
  if (kind === 'tool_call') {
    return readString(entry.summary ?? entry.tool_name, 'Connector activity was recorded for this conversation.');
  }
  if (kind === 'approval') {
    return readString(entry.summary ?? entry.resolution, 'An approval state change was recorded.');
  }
  if (kind === 'escalation') {
    return readString(entry.summary ?? entry.action, 'This conversation needs team attention.');
  }
  return readString(entry.summary ?? entry.text, 'Conversation event recorded.');
}

export function transcriptEntryTone(entry: TimelineEntry): 'neutral' | 'success' | 'warning' | 'danger' | 'accent' {
  const kind = readString(entry.kind).toLowerCase();
  if (kind === 'tool_call') {
    return 'accent';
  }
  if (kind === 'approval') {
    return 'warning';
  }
  if (kind === 'escalation') {
    return 'danger';
  }
  if (kind === 'message' && readString(entry.direction).toLowerCase() === 'outbound') {
    return 'success';
  }
  return 'neutral';
}

export function summarizeStudioErrorMessage(message: string | null): string | null {
  if (!message) {
    return null;
  }
  const normalized = message.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return null;
  }
  if (normalized.toLowerCase() === 'sage cannot run that request in this workspace right now.') {
    return 'Business Agents cannot load that workspace data right now. Refresh, or check workspace access if it keeps happening.';
  }
  if (
    normalized.length > 220
    || /<!doctype|<html|<script|hydration|react/i.test(normalized)
  ) {
    return null;
  }
  return normalized;
}

export function isWizardScopedError(message: string | null): boolean {
  const normalized = readString(message).toLowerCase();
  return Boolean(normalized) && (
    normalized.includes('self-hosted mode requires selecting')
    || normalized.includes('select a server/vps runtime')
    || normalized.includes('accept the privacy contract')
    || normalized.includes('accept the safety contract')
    || normalized.includes('daily message limit')
    || normalized.includes('monthly cost cap')
    || normalized.includes('telegram connected app')
    || normalized.includes('assistant needs a public name')
    || normalized.includes('ai tier route is unavailable')
    || normalized.includes('computer automation needs')
  );
}

export type RuntimeCardStatus = {
  label: 'Ready' | 'Default' | 'Needs connected computer' | 'Needs Approval Policy' | 'Unsupported in this workspace' | 'Requires setup';
  tone: 'success' | 'warning' | 'danger' | 'neutral';
};

export function studioRuntimeOption(value: WizardState['runtimePlacement']) {
  return STUDIO_RUNTIME_OPTIONS.find((option) => option.value === value) ?? STUDIO_RUNTIME_OPTIONS[0];
}

export function resolveRuntimeCardStatus(
  option: typeof STUDIO_RUNTIME_OPTIONS[number],
  hasGatewayOnlineTarget: boolean,
  hasCloudComputerAvailableTarget: boolean,
): RuntimeCardStatus[] {
  if (option.value === 'managed_cloud') {
    return [
      { label: 'Default', tone: 'success' },
      { label: 'Ready', tone: 'success' },
    ];
  }
  if (option.value === 'hosted_hardware_pool') {
    if (!hasCloudComputerAvailableTarget) {
      return [{ label: 'Unsupported in this workspace', tone: 'danger' }];
    }
    return [
      { label: 'Needs Approval Policy', tone: 'warning' },
      { label: 'Ready', tone: 'success' },
    ];
  }
  if (option.value === 'customer_local') {
    if (!hasGatewayOnlineTarget) {
      return [{ label: 'Needs connected computer', tone: 'warning' }];
    }
    return [{ label: 'Ready', tone: 'success' }];
  }
  return [{ label: 'Requires setup', tone: 'neutral' }];
}
