'use client';

import { memo, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import {
  BarChart3,
  BookOpen,
  Brain,
  Cable,
  FileText,
  LayoutDashboard,
  MessageSquareText,
  Route,
  ShieldCheck,
  Zap,
  type LucideIcon,
} from 'lucide-react';

import { ListDetailPanel } from '@/lib/ui/list-detail';
import { AnimatePresence, MotionTabPanel } from '@/lib/ui/motion';
import { AppButton, AppModal, AppSurfaceStat, AppSurfaceStatGrid, AppTextarea, joinClassNames } from '@/lib/ui/primitives';
import { DataBadge } from '@/lib/ui/data-table';
import { EmptyPanel } from '@/lib/ui/empty-panel';
import type { ConnectedExternalAgentRecord } from '@/lib/workspace/workstation-client';
import type { SpecialistOverlayTabId } from './types';
import { AGENT_STUDIO_OBJECT_LABELS, AGENT_VISIBILITY_LABELS, SPECIALIST_OVERLAY_TABS } from './constants';
import { formatTimestamp, humanizeToken, readRecord, readString } from './utils';

export type ExternalAgentChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
};

export type ExternalAgentChatSessionState = {
  messages: ExternalAgentChatMessage[];
};

type ExternalSurfaceSection = {
  id: string;
  title: string;
  description: string;
  emptyState: string;
  category: string;
  priority: number;
  icon: string;
  capabilityRequired: string;
  dataEndpointRef: string;
  actionsEndpointRef: string;
  displayKind: string;
  interaction: string;
};

export function createEmptyExternalAgentChatSession(): ExternalAgentChatSessionState {
  return { messages: [] };
}

const EXTERNAL_AGENT_SECTION_ICONS: Record<SpecialistOverlayTabId, LucideIcon> = {
  overview: LayoutDashboard,
  chat: MessageSquareText,
  channels: Cable,
  knowledge: BookOpen,
  ai: Route,
  tools: Zap,
  memory: Brain,
  artifacts: FileText,
  connectors: Cable,
  analytics: BarChart3,
};

type ExternalAgentPrimaryTabId = Extract<SpecialistOverlayTabId, 'overview' | 'chat'>;
type ExternalAgentSetupTabId = Exclude<SpecialistOverlayTabId, ExternalAgentPrimaryTabId>;

function isExternalAgentSetupTabId(tabId: SpecialistOverlayTabId): tabId is ExternalAgentSetupTabId {
  return tabId !== 'overview' && tabId !== 'chat';
}

function capabilityEnabled(capabilityManifest: Record<string, unknown>, key: string): boolean {
  if (capabilityManifest[key] === true) {
    return true;
  }
  const capabilities = capabilityManifest.capabilities;
  return Array.isArray(capabilities) && capabilities.map((item) => readString(item).toLowerCase()).includes(key);
}

function makeMessageId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}_${crypto.randomUUID()}`;
  }
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function formatOptionalTimestamp(value: unknown, fallback: string): string {
  return readString(value) ? formatTimestamp(value) : fallback;
}

function normalizeSurfaceSections(value: unknown): ExternalSurfaceSection[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => {
    const record = readRecord(item);
    return {
      id: readString(record.id),
      title: readString(record.title, 'External section'),
      description: readString(record.description),
      emptyState: readString(record.empty_state),
      category: readString(record.category, 'activity'),
      priority: Number.isFinite(Number(record.priority)) ? Number(record.priority) : 50,
      icon: readString(record.icon, 'key_value'),
      capabilityRequired: readString(record.capability_required),
      dataEndpointRef: readString(record.data_endpoint_ref),
      actionsEndpointRef: readString(record.actions_endpoint_ref),
      displayKind: readString(record.display_kind, 'key_value'),
      interaction: readString(record.interaction, 'read_only'),
    };
  }).filter((item) => item.id && item.dataEndpointRef)
    .sort((left, right) => left.priority - right.priority || left.title.localeCompare(right.title));
}

function externalOwnershipLabel(value: unknown): string {
  return readString(value, 'external') === 'external' ? 'External-owned' : humanizeToken(value, 'External-owned');
}

function sectionCacheKey(externalAgentId: string, sectionId: string): string {
  return `${externalAgentId}:${sectionId}`;
}

function readSectionItems(payload: Record<string, unknown>): Record<string, unknown>[] {
  const candidateKeys = ['items', 'records', 'objects', 'sources', 'knowledge_sources', 'entries', 'memory_entries'];
  for (const key of candidateKeys) {
    const value = payload[key];
    if (Array.isArray(value)) {
      return value.map(readRecord);
    }
  }
  return [];
}

function resolveManagedSection(
  sections: readonly ExternalSurfaceSection[],
  kind: 'knowledge' | 'memory',
): ExternalSurfaceSection | null {
  const aliases = kind === 'knowledge'
    ? ['knowledge', 'knowledge_sources', 'sources']
    : ['memory', 'memory_entries', 'memories'];
  return sections.find((section) => aliases.includes(section.id.toLowerCase()))
    ?? sections.find((section) => readString(section.capabilityRequired).toLowerCase() === `${kind}_read`)
    ?? sections.find((section) => readString(section.category).toLowerCase() === kind)
    ?? null;
}

function firstString(values: unknown[], fallback = ''): string {
  for (const value of values) {
    const normalized = readString(value);
    if (normalized) {
      return normalized;
    }
  }
  return fallback;
}

function itemRaw(item: Record<string, unknown>): Record<string, unknown> {
  return readRecord(item.raw_redacted ?? item.raw);
}

function sectionStatusTone(value: unknown): 'neutral' | 'success' | 'warning' | 'danger' {
  const normalized = readString(value).toLowerCase();
  if (['connected', 'indexed', 'ready', 'ok', 'live', 'synced', 'active'].includes(normalized)) {
    return 'success';
  }
  if (['failed', 'error', 'blocked', 'revoked'].includes(normalized)) {
    return 'danger';
  }
  if (normalized) {
    return 'warning';
  }
  return 'neutral';
}

function knowledgeSourceName(item: Record<string, unknown>, index: number): string {
  const raw = itemRaw(item);
  return firstString([item.name, item.title, item.label, item.source_name, raw.name, raw.title, raw.source_name], `Knowledge source ${index + 1}`);
}

function knowledgeSourceType(item: Record<string, unknown>): string {
  const raw = itemRaw(item);
  return humanizeToken(firstString([item.type, item.source_type, item.kind, raw.type, raw.source_type, item.object_type], 'source'), 'Source');
}

function knowledgeSourceStatus(item: Record<string, unknown>): string {
  const raw = itemRaw(item);
  return humanizeToken(firstString([item.status, item.state, raw.status, raw.state], 'connected'), 'Connected');
}

function memoryEntryId(item: Record<string, unknown>, index: number): string {
  return firstString([item.id, item.external_id, item.key, item.name], `memory-entry-${index}`);
}

function memoryEntryKey(item: Record<string, unknown>, index: number): string {
  const raw = itemRaw(item);
  return firstString([item.key, item.name, item.title, raw.key, raw.name, raw.title], `Memory ${index + 1}`);
}

function memoryEntryValue(item: Record<string, unknown>): string {
  const raw = itemRaw(item);
  const value = item.value ?? item.summary ?? item.content ?? item.text ?? raw.value ?? raw.summary ?? raw.content ?? raw.text;
  if (typeof value === 'string') {
    return value.trim();
  }
  if (value === null || value === undefined) {
    return '';
  }
  return JSON.stringify(value);
}

function memoryEntryTimestamp(item: Record<string, unknown>): string {
  return firstString([item.updated_at, item.created_at, item.observed_at, item.timestamp]);
}

function sectionBelongsToTab(section: ExternalSurfaceSection, tabId: ExternalAgentSetupTabId): boolean {
  const category = readString(section.category).toLowerCase();
  const capability = readString(section.capabilityRequired).toLowerCase();
  const id = readString(section.id).toLowerCase();
  const icon = readString(section.icon).toLowerCase();
  const displayKind = readString(section.displayKind).toLowerCase();
  if (tabId === 'knowledge') {
    return category === 'knowledge' || capability.startsWith('knowledge') || id.includes('knowledge') || id.includes('source');
  }
  if (tabId === 'memory') {
    return category === 'memory' || capability.startsWith('memory') || id.includes('memory');
  }
  if (tabId === 'tools') {
    return ['actions', 'tools'].includes(category) || ['actions', 'tools'].includes(icon) || capability === 'actions' || capability === 'tools' || id.includes('tool') || id.includes('action');
  }
  if (tabId === 'channels') {
    return category.includes('channel') || id.includes('channel');
  }
  if (tabId === 'artifacts') {
    return ['artifact', 'output', 'resource'].some((token) => (
      category.includes(token)
      || capability.includes(token)
      || id.includes(token)
      || icon.includes(token)
      || displayKind.includes(token)
    ));
  }
  if (tabId === 'connectors') {
    return false;
  }
  if (tabId === 'analytics') {
    return ['activity', 'logs', 'events', 'runs', 'usage', 'health', 'configuration', 'security'].includes(category)
      && !sectionBelongsToTab(section, 'knowledge')
      && !sectionBelongsToTab(section, 'memory')
      && !sectionBelongsToTab(section, 'tools')
      && !sectionBelongsToTab(section, 'artifacts');
  }
  return false;
}

function externalItemSummary(item: Record<string, unknown>): string {
  const raw = readRecord(item.raw_redacted);
  const parts = [
    externalOwnershipLabel(item.ownership),
    humanizeToken(item.object_type, 'External object'),
    readString(item.external_id) ? `ID ${readString(item.external_id)}` : '',
    readString(raw.provider || raw.provider_kind) ? humanizeToken(raw.provider || raw.provider_kind) : '',
  ].filter(Boolean);
  return parts.join(' · ');
}

function localConnectorStatusLabel(localConnector: Record<string, unknown>): string {
  if (!localConnector.required) {
    return 'Not required';
  }
  const state = readString(localConnector.binding_state, 'missing_agent_computer');
  if (state === 'bound') {
    return readString(localConnector.agent_computer_label, 'Bound');
  }
  return humanizeToken(state, 'Needs Agent Computer');
}

function localConnectorStatusHint(localConnector: Record<string, unknown>): string {
  if (!localConnector.required) {
    return 'Public HTTPS path';
  }
  const state = readString(localConnector.binding_state, 'missing_agent_computer');
  if (state === 'bound' && !localConnector.proxy_available) {
    return 'Bound, Agent Computer bridge disabled';
  }
  if (readString(localConnector.binding_message)) {
    return readString(localConnector.binding_message);
  }
  return 'Local runtimes require an approved Agent Computer bridge';
}

export const ConnectedExternalAgentDetailView = memo(({
  externalAgent,
  overlayTab,
  onSelectTab,
  services,
  chatSession,
  onChatSessionChange,
  onExternalAgentUpdated,
}: {
  externalAgent: ConnectedExternalAgentRecord | null;
  overlayTab: SpecialistOverlayTabId;
  onSelectTab: (tabId: SpecialistOverlayTabId) => void;
  services: any;
  chatSession: ExternalAgentChatSessionState;
  onChatSessionChange: Dispatch<SetStateAction<ExternalAgentChatSessionState>>;
  onExternalAgentUpdated: (record: ConnectedExternalAgentRecord) => void;
}) => {
  const [messageDraft, setMessageDraft] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [isRefreshingManifest, setIsRefreshingManifest] = useState(false);
  const [isDisconnecting, setIsDisconnecting] = useState(false);
  const [loadingSectionIds, setLoadingSectionIds] = useState<Set<string>>(() => new Set());
  const [sectionPayloads, setSectionPayloads] = useState<Record<string, Record<string, unknown>>>({});
  const [sectionErrors, setSectionErrors] = useState<Record<string, string>>({});
  const [isDetailModalOpen, setIsDetailModalOpen] = useState(false);
  const [detailModalTab, setDetailModalTab] = useState<ExternalAgentSetupTabId>('knowledge');
  const [localError, setLocalError] = useState<string | null>(null);

  const externalAgentId = readString(externalAgent?.id);
  const displayName = readString(externalAgent?.name ?? externalAgent?.label, 'Connected Agent');
  const providerLabel = humanizeToken(externalAgent?.provider_kind, 'Custom endpoint');
  const connectionState = readString(externalAgent?.connection_state, 'unverified').toLowerCase();
  const trustState = readString(externalAgent?.trust_state, connectionState).toLowerCase();
  const endpointRefs = readRecord(externalAgent?.endpoint_refs);
  const localConnector = readRecord(externalAgent?.local_connector);
  const capabilityManifest = readRecord(externalAgent?.capability_manifest);
  const externalSections = normalizeSurfaceSections(externalAgent?.surface_sections);
  const objectTypes = Array.isArray(externalAgent?.object_types)
    ? externalAgent.object_types.map((item) => humanizeToken(item, '')).filter(Boolean)
    : [];
  const rawObjectTypes = Array.isArray(externalAgent?.object_types)
    ? externalAgent.object_types.map((item) => readString(item).toLowerCase()).filter(Boolean)
    : [];
  const protocols = Array.isArray(externalAgent?.protocols)
    ? externalAgent.protocols.map((item) => humanizeToken(readRecord(item).kind, '')).filter(Boolean)
    : [];
  const hasChat = capabilityEnabled(capabilityManifest, 'chat');
  const canReadKnowledge = capabilityEnabled(capabilityManifest, 'knowledge_read');
  const canReadMemory = capabilityEnabled(capabilityManifest, 'memory_read');
  const knowledgeSection = useMemo(
    () => resolveManagedSection(externalSections, 'knowledge'),
    [externalSections],
  );
  const memorySection = useMemo(
    () => resolveManagedSection(externalSections, 'memory'),
    [externalSections],
  );
  const isVerified = connectionState === 'verified';
  const isRevoked = connectionState === 'revoked' || externalAgent?.enabled === false;
  const hasArtifactSurface = useMemo(() => (
    capabilityEnabled(capabilityManifest, 'artifacts')
    || capabilityEnabled(capabilityManifest, 'artifact')
    || capabilityEnabled(capabilityManifest, 'outputs')
    || capabilityEnabled(capabilityManifest, 'resources')
    || rawObjectTypes.some((item) => ['external_agent_artifact', 'artifact', 'output', 'resource'].some((token) => item.includes(token)))
    || externalSections.some((section) => sectionBelongsToTab(section, 'artifacts'))
  ), [capabilityManifest, externalSections, rawObjectTypes]);

  const visibleTabs = useMemo(() => SPECIALIST_OVERLAY_TABS.filter((tab) => {
    if (tab.id === 'overview' || tab.id === 'chat' || tab.id === 'analytics') {
      return true;
    }
    if (tab.id === 'knowledge') {
      return canReadKnowledge;
    }
    if (tab.id === 'memory') {
      return canReadMemory;
    }
    if (tab.id === 'tools') {
      return capabilityEnabled(capabilityManifest, 'actions') || capabilityEnabled(capabilityManifest, 'tools');
    }
    if (tab.id === 'artifacts') {
      return hasArtifactSurface;
    }
    if (tab.id === 'channels' || tab.id === 'ai' || tab.id === 'connectors') {
      return false;
    }
    return false;
  }), [canReadKnowledge, canReadMemory, capabilityManifest, hasArtifactSurface]);

  const activeTab = visibleTabs.some((tab) => tab.id === overlayTab) ? overlayTab : 'overview';
  const primaryTabs = visibleTabs.filter((tab) => !isExternalAgentSetupTabId(tab.id));
  const setupTabs = visibleTabs.filter((tab) => isExternalAgentSetupTabId(tab.id));
  const activeTopTab: ExternalAgentPrimaryTabId = activeTab === 'chat' ? 'chat' : 'overview';
  const capabilityLabels = Array.isArray(capabilityManifest.capabilities)
    ? capabilityManifest.capabilities.map((item) => humanizeToken(item, '')).filter(Boolean)
    : [];
  const tabSections = useMemo(() => ({
    knowledge: externalSections.filter((section) => sectionBelongsToTab(section, 'knowledge')),
    tools: externalSections.filter((section) => sectionBelongsToTab(section, 'tools')),
    memory: externalSections.filter((section) => sectionBelongsToTab(section, 'memory')),
    artifacts: externalSections.filter((section) => sectionBelongsToTab(section, 'artifacts')),
    analytics: externalSections.filter((section) => sectionBelongsToTab(section, 'analytics')),
  }), [externalSections]);

  const defaultDetailModalTab: ExternalAgentSetupTabId =
    setupTabs.length > 0 && isExternalAgentSetupTabId(setupTabs[0].id)
      ? setupTabs[0].id
      : 'knowledge';
  const openDetailModal = (tabId: ExternalAgentSetupTabId = isExternalAgentSetupTabId(activeTab) ? activeTab : defaultDetailModalTab) => {
    setDetailModalTab(tabId);
    setIsDetailModalOpen(true);
  };

  const closeDetailModal = () => {
    setIsDetailModalOpen(false);
  };

  const selectExternalAgentSection = (tabId: SpecialistOverlayTabId) => {
    if (isExternalAgentSetupTabId(tabId)) {
      setDetailModalTab(tabId);
      setIsDetailModalOpen(false);
      onSelectTab(tabId);
      return;
    }
    setIsDetailModalOpen(false);
    onSelectTab(tabId);
  };

  useEffect(() => {
    if (!externalAgentId || !isVerified || isRevoked) {
      return;
    }
    if ((activeTab === 'knowledge' || (isDetailModalOpen && detailModalTab === 'knowledge')) && canReadKnowledge && knowledgeSection) {
      const cacheKey = sectionCacheKey(externalAgentId, knowledgeSection.id);
      if (!sectionPayloads[cacheKey] && !sectionErrors[cacheKey] && !loadingSectionIds.has(cacheKey)) {
        void loadExternalSection(knowledgeSection);
      }
    }
    if ((activeTab === 'memory' || (isDetailModalOpen && detailModalTab === 'memory')) && canReadMemory && memorySection) {
      const cacheKey = sectionCacheKey(externalAgentId, memorySection.id);
      if (!sectionPayloads[cacheKey] && !sectionErrors[cacheKey] && !loadingSectionIds.has(cacheKey)) {
        void loadExternalSection(memorySection);
      }
    }
  }, [
    activeTab,
    canReadKnowledge,
    canReadMemory,
    externalAgentId,
    detailModalTab,
    isDetailModalOpen,
    isRevoked,
    isVerified,
    knowledgeSection,
    loadingSectionIds,
    memorySection,
    sectionErrors,
    sectionPayloads,
  ]);

  async function sendPrivateChatTurn() {
    const message = messageDraft.trim();
    if (!externalAgentId || !message || isSending) {
      return;
    }
    if (!isVerified || isRevoked) {
      setLocalError(isRevoked ? 'This connected agent is disconnected.' : 'Verify this connected agent before sending a Studio chat turn.');
      return;
    }
    setLocalError(null);
    setMessageDraft('');
    const userMessage: ExternalAgentChatMessage = {
      id: makeMessageId('user'),
      role: 'user',
      content: message,
    };
    onChatSessionChange((current) => ({
      ...current,
      messages: [...current.messages, userMessage],
    }));
    setIsSending(true);
    try {
      const payload = await services.client.chatTurnConnectedExternalAgent({
        externalAgentId,
        message,
        recentMessages: chatSession.messages.map((item) => ({
          role: item.role,
          content: item.content,
        })),
      });
      const reply = readString(readRecord(payload).reply, 'The connected agent did not return text.');
      onChatSessionChange((current) => ({
        ...current,
        messages: [
          ...current.messages,
          { id: makeMessageId('assistant'), role: 'assistant', content: reply },
        ],
      }));
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Connected agent chat failed.');
    } finally {
      setIsSending(false);
    }
  }

  async function refreshManifest() {
    if (!externalAgentId || isRefreshingManifest) {
      return;
    }
    setIsRefreshingManifest(true);
    setLocalError(null);
    try {
      const payload = await services.client.refreshConnectedExternalAgentManifest({ externalAgentId });
      onExternalAgentUpdated(payload as ConnectedExternalAgentRecord);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Manifest refresh failed.');
    } finally {
      setIsRefreshingManifest(false);
    }
  }

  async function disconnectAgent() {
    if (!externalAgentId || isDisconnecting) {
      return;
    }
    setIsDisconnecting(true);
    setLocalError(null);
    try {
      const payload = await services.client.disconnectConnectedExternalAgent({ externalAgentId });
      onExternalAgentUpdated(payload as ConnectedExternalAgentRecord);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Connected agent could not be disconnected.');
    } finally {
      setIsDisconnecting(false);
    }
  }

  async function loadExternalSection(section: ExternalSurfaceSection) {
    if (!externalAgentId || !section.id) {
      return;
    }
    const cacheKey = sectionCacheKey(externalAgentId, section.id);
    if (loadingSectionIds.has(cacheKey)) {
      return;
    }
    setLocalError(null);
    setSectionErrors((current) => {
      const next = { ...current };
      delete next[cacheKey];
      return next;
    });
    setLoadingSectionIds((current) => new Set([...current, cacheKey]));
    try {
      const payload = await services.client.getConnectedExternalAgentSectionData({
        externalAgentId,
        sectionId: section.id,
      });
      setSectionPayloads((current) => ({
        ...current,
        [cacheKey]: readRecord(payload),
      }));
    } catch (error) {
      setSectionErrors((current) => ({
        ...current,
        [cacheKey]: error instanceof Error ? error.message : 'External section could not be loaded.',
      }));
    } finally {
      setLoadingSectionIds((current) => {
        const next = new Set(current);
        next.delete(cacheKey);
        return next;
      });
    }
  }

  function renderExternalRecordCard(item: Record<string, unknown>, index: number) {
    const raw = readRecord(item.raw_redacted);
    return (
      <div key={readString(item.id, `external-item-${index}`)} className="studio-agent-overview__card">
        <div className="studio-agent-overview__card-icon"><FileText size={15} aria-hidden="true" /></div>
        <div>
          <strong>{readString(item.title || item.name || item.external_id, 'External record')}</strong>
          <span>{externalItemSummary(item)}</span>
          {readString(item.status) ? <span>{humanizeToken(item.status)}</span> : null}
          {readString(item.summary) ? <span>{readString(item.summary)}</span> : null}
          {readString(raw.runtime || raw.runtime_kind) ? <span>{humanizeToken(raw.runtime || raw.runtime_kind)}</span> : null}
        </div>
      </div>
    );
  }

  function renderSectionItems(payload: Record<string, unknown>, section: ExternalSurfaceSection) {
    const items = readSectionItems(payload);
    if (items.length === 0) {
      return (
        <EmptyPanel
          title="No external records"
          body={section.emptyState || 'This external section returned no displayable records.'}
        />
      );
    }
    if (section.displayKind === 'key_value') {
      const summary = readRecord(payload.summary);
      const summaryEntries = Object.entries(summary).slice(0, 12);
      return (
        <div className="app-stack-3">
          {summaryEntries.length > 0 ? (
            <AppSurfaceStatGrid>
              {summaryEntries.map(([key, value]) => (
                <AppSurfaceStat
                  key={key}
                  label={humanizeToken(key, key)}
                  value={readString(value, 'Not set')}
                  hint="External-owned value"
                />
              ))}
            </AppSurfaceStatGrid>
          ) : null}
          <div className="studio-agent-overview__grid">
            {items.slice(0, 12).map(renderExternalRecordCard)}
          </div>
        </div>
      );
    }
    if (section.displayKind === 'logs') {
      return (
        <div className="studio-external-chat__transcript">
          {items.slice(0, 24).map((item, index) => (
            <div key={readString(item.id, `external-log-${index}`)} className="studio-external-chat__message">
              <span>{readString(item.status) ? humanizeToken(item.status) : 'External log'} · {readString(item.updated_at || item.created_at || item.observed_at)}</span>
              <p>{readString(item.summary || item.title, 'External log entry')}</p>
            </div>
          ))}
        </div>
      );
    }
    return (
      <div className="studio-agent-overview__grid">
        {items.slice(0, 12).map(renderExternalRecordCard)}
      </div>
    );
  }

  function renderSectionLoading(label: string) {
    return (
      <div className="external-section-skeleton" aria-label={`Loading ${label}`}>
        <div className="external-section-skeleton__row" />
        <div className="external-section-skeleton__row" />
        <div className="external-section-skeleton__row" />
      </div>
    );
  }

  function renderSectionError(section: ExternalSurfaceSection, title: string, message: string) {
    return (
      <EmptyPanel
        title={title}
        body={message}
        actions={(
          <AppButton type="button" tone="secondary" onClick={() => { void loadExternalSection(section); }}>
            Retry
          </AppButton>
        )}
      />
    );
  }

  function renderExternalSectionPanel(section: ExternalSurfaceSection) {
    const cacheKey = sectionCacheKey(externalAgentId, section.id);
    const payload = sectionPayloads[cacheKey];
    const isLoading = loadingSectionIds.has(cacheKey);
    const error = sectionErrors[cacheKey];
    return (
      <ListDetailPanel
        key={section.id}
        className="studio-panel studio-panel--detail"
        eyebrow={`${humanizeToken(section.category)} · ${humanizeToken(section.displayKind)}`}
        title={section.title}
        subtitle={section.actionsEndpointRef
          ? 'External-owned records are read-only in this pass. Actions are declared but not enabled.'
          : 'External-owned records load through the Agent Computer or hosted connection.'}
        actions={(
          <AppButton
            type="button"
            tone="secondary"
            onClick={() => { void loadExternalSection(section); }}
            disabled={isLoading || !isVerified || isRevoked}
          >
            {isLoading ? 'Loading...' : payload ? 'Refresh' : 'Load'}
          </AppButton>
        )}
      >
        {error ? renderSectionError(section, 'External section unavailable', error) : payload ? renderSectionItems(payload, section) : (
          <EmptyPanel
            title="Section not loaded"
            body={isVerified ? 'Load this external-agent-owned section to inspect external records.' : 'Verify the connection before loading external-agent-owned records.'}
          />
        )}
      </ListDetailPanel>
    );
  }

  function renderExternalSectionGroup(sections: readonly ExternalSurfaceSection[], emptyTitle: string, emptyBody: string) {
    if (sections.length === 0) {
      return <EmptyPanel title={emptyTitle} body={emptyBody} />;
    }
    return <div className="app-stack-3">{sections.map(renderExternalSectionPanel)}</div>;
  }

  function renderKnowledgeSection() {
    if (!knowledgeSection) {
      return (
        <EmptyPanel
          title="No external knowledge endpoint"
          body="This connected agent has not declared a knowledge section. Add it to the external manifest, then refresh the manifest."
        />
      );
    }
    const cacheKey = sectionCacheKey(externalAgentId, knowledgeSection.id);
    const payload = sectionPayloads[cacheKey];
    const error = sectionErrors[cacheKey];
    const isLoading = loadingSectionIds.has(cacheKey);
    if (isLoading && !payload) {
      return renderSectionLoading('knowledge sources');
    }
    if (error) {
      return renderSectionError(knowledgeSection, 'No knowledge sources connected', error);
    }
    const items = payload ? readSectionItems(payload) : [];
    if (items.length === 0) {
      return (
        <EmptyPanel
          title="No external knowledge records"
          body="External knowledge is read-only in Studio v1. Add sources in the external runtime or manifest, then refresh this section."
        />
      );
    }
    return (
      <div className="external-section-list" aria-label="External knowledge sources">
        {items.map((item, index) => {
          const status = knowledgeSourceStatus(item);
          return (
            <div key={readString(item.id, `knowledge-source-${index}`)} className="external-section-list__row">
              <span
                className={joinClassNames(
                  'external-section-list__status-dot',
                  `external-section-list__status-dot--${sectionStatusTone(status)}`,
                )}
                aria-hidden="true"
              />
              <span className="external-section-list__copy">
                <strong>{knowledgeSourceName(item, index)}</strong>
                <span>{status}</span>
              </span>
              <DataBadge>{knowledgeSourceType(item)}</DataBadge>
            </div>
          );
        })}
      </div>
    );
  }

  function renderMemorySection() {
    if (!memorySection) {
      return (
        <EmptyPanel
          title="No external memory endpoint"
          body="This connected agent has not declared a memory section. Add it to the external manifest, then refresh the manifest."
        />
      );
    }
    const cacheKey = sectionCacheKey(externalAgentId, memorySection.id);
    const payload = sectionPayloads[cacheKey];
    const error = sectionErrors[cacheKey];
    const isLoading = loadingSectionIds.has(cacheKey);
    if (isLoading && !payload) {
      return renderSectionLoading('memory entries');
    }
    if (error) {
      return renderSectionError(memorySection, 'No memory stored yet', error);
    }
    const items = payload ? readSectionItems(payload) : [];
    if (items.length === 0) {
      return (
        <EmptyPanel
          title="No memory stored yet"
          body="Memory entries exposed by the external agent will appear here."
        />
      );
    }
    return (
      <div className="external-section-list external-section-list--memory" aria-label="External memory entries">
        {items.map((item, index) => {
          const itemId = memoryEntryId(item, index);
          const value = memoryEntryValue(item);
          const timestamp = memoryEntryTimestamp(item);
          return (
            <div key={itemId} className="external-section-list__row external-section-list__row--memory">
              <span className="external-section-list__copy">
                <strong>{memoryEntryKey(item, index)}</strong>
                <span>{value || 'No value stored'}</span>
                {timestamp ? <time dateTime={timestamp}>{formatTimestamp(timestamp)}</time> : null}
              </span>
              <DataBadge>Read-only</DataBadge>
            </div>
          );
        })}
      </div>
    );
  }

  function renderSetupTabContent(tabId: ExternalAgentSetupTabId, keyPrefix: string) {
    if (tabId === 'knowledge') {
      return (
        <MotionTabPanel key={`${keyPrefix}-external-knowledge`} className="studio-agent-motion-panel">
          <ListDetailPanel
            className="studio-panel studio-panel--detail"
            eyebrow="Knowledge"
            title="External knowledge"
            subtitle="Knowledge is owned by the external runtime. Native Studio uploads stay unavailable."
          >
            <div className="app-stack-3">
              {renderKnowledgeSection()}
              {tabSections.knowledge.filter((section) => section.id !== knowledgeSection?.id).map(renderExternalSectionPanel)}
            </div>
          </ListDetailPanel>
        </MotionTabPanel>
      );
    }

    if (tabId === 'ai') {
      return (
        <MotionTabPanel key={`${keyPrefix}-external-model`} className="studio-agent-motion-panel">
          <ListDetailPanel className="studio-panel studio-panel--detail" eyebrow="Model" title="External agent owns its AI route" subtitle="AI route switching is disabled because this agent runs outside the native Studio runtime.">
            <AppSurfaceStatGrid>
              <AppSurfaceStat label="External agent" value={providerLabel} hint="Declared by manifest" />
              <AppSurfaceStat label="Route" value="External endpoint" hint="AI route is outside native Studio" />
              <AppSurfaceStat label="Platform control" value="Read-only" hint="No native AI route switching" />
            </AppSurfaceStatGrid>
          </ListDetailPanel>
        </MotionTabPanel>
      );
    }

    if (tabId === 'tools') {
      return (
        <MotionTabPanel key={`${keyPrefix}-external-actions`} className="studio-agent-motion-panel">
          <ListDetailPanel className="studio-panel studio-panel--detail" eyebrow="Actions" title="External actions" subtitle="Declared actions are external-owned and read-only until a dedicated approval and audit route exists.">
            <div className="app-stack-3">
              <div className="studio-inline-wrap">
                <DataBadge tone="warning">Read-only v1</DataBadge>
                <DataBadge>Revocable</DataBadge>
                <DataBadge>No native action install</DataBadge>
              </div>
              {renderExternalSectionGroup(
                tabSections.tools,
                'No external actions declared',
                'Actions appear here when the manifest exposes safe external-agent-owned sections.',
              )}
            </div>
          </ListDetailPanel>
        </MotionTabPanel>
      );
    }

    if (tabId === 'memory') {
      return (
        <MotionTabPanel key={`${keyPrefix}-external-memory`} className="studio-agent-motion-panel">
          <ListDetailPanel className="studio-panel studio-panel--detail" eyebrow="Memory" title="External memory" subtitle="Memory is owned by the external runtime and rendered read-only in Studio v1.">
            <div className="app-stack-3">
              {renderMemorySection()}
              {tabSections.memory.filter((section) => section.id !== memorySection?.id).map(renderExternalSectionPanel)}
            </div>
          </ListDetailPanel>
        </MotionTabPanel>
      );
    }

    if (tabId === 'artifacts') {
      return (
        <MotionTabPanel key={`${keyPrefix}-external-artifacts`} className="studio-agent-motion-panel">
          <ListDetailPanel
            className="studio-panel studio-panel--detail"
            eyebrow="Artifacts"
            title="External artifacts"
            subtitle="Artifacts are generated or owned by the external runtime and shown read-only in Studio."
          >
            <div className="app-stack-3">
              {renderExternalSectionGroup(
                tabSections.artifacts,
                'No artifacts exposed',
                'Artifacts appear here when the external manifest exposes artifact, resource, or output sections.',
              )}
            </div>
          </ListDetailPanel>
        </MotionTabPanel>
      );
    }

    return (
      <MotionTabPanel key={`${keyPrefix}-external-results`} className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail"
          eyebrow="Results"
          title="Connected-agent activity"
          subtitle="Runs, health, usage, logs, and manifest refresh state."
          actions={(
            <div className="app-inline-actions app-inline-actions--tight">
              <AppButton type="button" tone="secondary" onClick={refreshManifest} disabled={isRefreshingManifest || isRevoked}>
                {isRefreshingManifest ? 'Refreshing...' : 'Refresh manifest'}
              </AppButton>
              <AppButton type="button" tone="danger" onClick={disconnectAgent} disabled={isDisconnecting || isRevoked}>
                {isDisconnecting ? 'Disconnecting...' : 'Disconnect'}
              </AppButton>
            </div>
          )}
        >
          <AppSurfaceStatGrid>
            <AppSurfaceStat label="Last manifest refresh" value={formatOptionalTimestamp(externalAgent?.last_manifest_refresh_at, 'Not refreshed')} hint="Latest backend handshake" />
            <AppSurfaceStat label="Status" value={humanizeToken(connectionState, 'Unverified')} hint="Current connection state" />
            <AppSurfaceStat label="Public send" value="Disabled" hint="Customer channels come later" />
            <AppSurfaceStat label="External objects" value={objectTypes.length > 0 ? String(objectTypes.length) : 'None'} hint={objectTypes.length > 0 ? objectTypes.join(', ') : 'No object types declared'} />
          </AppSurfaceStatGrid>
          <div className="app-stack-3">
            {tabSections.analytics.length > 0 ? tabSections.analytics.map(renderExternalSectionPanel) : (
              <EmptyPanel
                title="No results sections"
                body="Logs, tasks, events, runs, usage, and health sections appear here when the manifest exposes them."
                actions={(
                  <AppButton type="button" tone="secondary" onClick={refreshManifest} disabled={isRefreshingManifest || isRevoked}>
                    {isRefreshingManifest ? 'Refreshing...' : 'Refresh manifest'}
                  </AppButton>
                )}
              />
            )}
          </div>
        </ListDetailPanel>
      </MotionTabPanel>
    );
  }

  if (!externalAgent) {
    return (
      <div className="studio-agent-detail-empty" aria-label="Connected agent detail">
        <strong>Select a connected agent</strong>
        <span>Connection status, owner test chat, endpoints, and declared sections will appear here.</span>
      </div>
    );
  }

  return (
    <div className="app-stack-4 studio-agent-detail-motion">
      <div className="studio-agent-detail-layout studio-agent-detail-layout--with-topbar">
        <div className="studio-agent-detail-topbar">
          <nav className="studio-agent-detail-tabs studio-agent-detail-tabs--topbar" role="tablist" aria-label="Connected Agent sections">
            {primaryTabs.map((tab) => {
              const SectionIcon = EXTERNAL_AGENT_SECTION_ICONS[tab.id];
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  className={joinClassNames(
                    'studio-agent-detail-tabs__button',
                    activeTopTab === tab.id && 'studio-agent-detail-tabs__button--active',
                  )}
                  aria-label={tab.label}
                  aria-selected={activeTopTab === tab.id}
                  title={tab.label}
                  onClick={() => selectExternalAgentSection(tab.id)}
                >
                  <SectionIcon size={15} strokeWidth={2} aria-hidden="true" />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </nav>
          <AppButton type="button" tone="secondary" onClick={() => openDetailModal()}>
            Manage connection
          </AppButton>
        </div>

        <AppModal
          open={isDetailModalOpen}
          title="Manage connected agent"
          description="Inspect external-owned sections, manifest state, and connection controls without giving native ownership."
          className="studio-agent-setup-modal"
          onOpenChange={(open) => {
            if (!open) {
              closeDetailModal();
            } else {
              setIsDetailModalOpen(true);
            }
          }}
          actions={(
            <AppButton type="button" tone="ghost" onClick={closeDetailModal}>
              Close
            </AppButton>
          )}
        >
          <div className="studio-agent-setup-modal__layout">
            <nav className="studio-agent-setup-modal__nav" aria-label="Connected agent sections">
              {setupTabs.map((tab) => {
                const SectionIcon = EXTERNAL_AGENT_SECTION_ICONS[tab.id];
                return (
                  <button
                    key={tab.id}
                    type="button"
                    className={joinClassNames(
                      'studio-agent-setup-modal__nav-item',
                      detailModalTab === tab.id && 'studio-agent-setup-modal__nav-item--active',
                    )}
                    aria-current={detailModalTab === tab.id ? 'page' : undefined}
                    onClick={() => setDetailModalTab(tab.id as ExternalAgentSetupTabId)}
                  >
                    <SectionIcon size={15} strokeWidth={2} aria-hidden="true" />
                    <span>{tab.label}</span>
                  </button>
                );
              })}
            </nav>
            <div className="studio-agent-setup-modal__content">
              <AnimatePresence mode="wait" initial={false}>
            {isDetailModalOpen && detailModalTab === 'knowledge' && (
              <MotionTabPanel key="external-knowledge" className="studio-agent-motion-panel">
                <ListDetailPanel
                  className="studio-panel studio-panel--detail"
                  eyebrow="Knowledge"
                  title="External knowledge"
                  subtitle="Knowledge is owned by the external runtime. Native Studio uploads stay unavailable."
                >
                  <div className="app-stack-3">
                    {renderKnowledgeSection()}
                    {tabSections.knowledge.filter((section) => section.id !== knowledgeSection?.id).map(renderExternalSectionPanel)}
                  </div>
                </ListDetailPanel>
              </MotionTabPanel>
            )}

            {isDetailModalOpen && detailModalTab === 'ai' && (
              <MotionTabPanel key="external-model" className="studio-agent-motion-panel">
                <ListDetailPanel className="studio-panel studio-panel--detail" eyebrow="Model" title="External agent owns its AI route" subtitle="AI route switching is disabled because this agent runs outside the native Studio runtime.">
                  <AppSurfaceStatGrid>
                    <AppSurfaceStat label="External agent" value={providerLabel} hint="Declared by manifest" />
                    <AppSurfaceStat label="Route" value="External endpoint" hint="AI route is outside native Studio" />
                    <AppSurfaceStat label="Platform control" value="Read-only" hint="No native AI route switching" />
                  </AppSurfaceStatGrid>
                </ListDetailPanel>
              </MotionTabPanel>
            )}

            {isDetailModalOpen && detailModalTab === 'tools' && (
              <MotionTabPanel key="external-actions" className="studio-agent-motion-panel">
                <ListDetailPanel className="studio-panel studio-panel--detail" eyebrow="Actions" title="External actions" subtitle="Declared actions are external-owned and read-only until a dedicated approval and audit route exists.">
                  <div className="app-stack-3">
                    <div className="studio-inline-wrap">
                      <DataBadge tone="warning">Read-only v1</DataBadge>
                      <DataBadge>Revocable</DataBadge>
                      <DataBadge>No native action install</DataBadge>
                    </div>
                    {renderExternalSectionGroup(
                      tabSections.tools,
                      'No external actions declared',
                      'Actions appear here when the manifest exposes safe external-agent-owned sections.',
                    )}
                  </div>
                </ListDetailPanel>
              </MotionTabPanel>
            )}

            {isDetailModalOpen && detailModalTab === 'memory' && (
              <MotionTabPanel key="external-memory" className="studio-agent-motion-panel">
                <ListDetailPanel className="studio-panel studio-panel--detail" eyebrow="Memory" title="External memory" subtitle="Memory is owned by the external runtime and rendered read-only in Studio v1.">
                  <div className="app-stack-3">
                    {renderMemorySection()}
                    {tabSections.memory.filter((section) => section.id !== memorySection?.id).map(renderExternalSectionPanel)}
                  </div>
                </ListDetailPanel>
              </MotionTabPanel>
            )}

            {isDetailModalOpen && detailModalTab === 'artifacts' && (
              <MotionTabPanel key="external-artifacts" className="studio-agent-motion-panel">
                <ListDetailPanel
                  className="studio-panel studio-panel--detail"
                  eyebrow="Artifacts"
                  title="External artifacts"
                  subtitle="Artifacts are generated or owned by the external runtime and shown read-only in Studio."
                >
                  <div className="app-stack-3">
                    {renderExternalSectionGroup(
                      tabSections.artifacts,
                      'No artifacts exposed',
                      'Artifacts appear here when the external manifest exposes artifact, resource, or output sections.',
                    )}
                  </div>
                </ListDetailPanel>
              </MotionTabPanel>
            )}

            {isDetailModalOpen && detailModalTab === 'analytics' && (
              <MotionTabPanel key="external-results" className="studio-agent-motion-panel">
                <ListDetailPanel
                  className="studio-panel studio-panel--detail"
                  eyebrow="Results"
                  title="Connected-agent activity"
                  subtitle="Runs, health, usage, logs, and manifest refresh state."
                  actions={(
                    <div className="app-inline-actions app-inline-actions--tight">
                      <AppButton type="button" tone="secondary" onClick={refreshManifest} disabled={isRefreshingManifest || isRevoked}>
                        {isRefreshingManifest ? 'Refreshing...' : 'Refresh manifest'}
                      </AppButton>
                      <AppButton type="button" tone="danger" onClick={disconnectAgent} disabled={isDisconnecting || isRevoked}>
                        {isDisconnecting ? 'Disconnecting...' : 'Disconnect'}
                      </AppButton>
                    </div>
                  )}
                >
                  <AppSurfaceStatGrid>
                    <AppSurfaceStat label="Last manifest refresh" value={formatOptionalTimestamp(externalAgent.last_manifest_refresh_at, 'Not refreshed')} hint="Latest backend handshake" />
                    <AppSurfaceStat label="Status" value={humanizeToken(connectionState, 'Unverified')} hint="Current connection state" />
                    <AppSurfaceStat label="Public send" value="Disabled" hint="Customer channels come later" />
                    <AppSurfaceStat label="External objects" value={objectTypes.length > 0 ? String(objectTypes.length) : 'None'} hint={objectTypes.length > 0 ? objectTypes.join(', ') : 'No object types declared'} />
                  </AppSurfaceStatGrid>
                  <div className="app-stack-3">
                    {tabSections.analytics.length > 0 ? tabSections.analytics.map(renderExternalSectionPanel) : (
                      <EmptyPanel
                        title="No results sections"
                        body="Logs, tasks, events, runs, usage, and health sections appear here when the manifest exposes them."
                        actions={(
                          <AppButton type="button" tone="secondary" onClick={refreshManifest} disabled={isRefreshingManifest || isRevoked}>
                            {isRefreshingManifest ? 'Refreshing...' : 'Refresh manifest'}
                          </AppButton>
                        )}
                      />
                    )}
                  </div>
                </ListDetailPanel>
              </MotionTabPanel>
            )}
              </AnimatePresence>
            </div>
          </div>
        </AppModal>

        <div className="studio-agent-detail-content">
          {localError ? (
            <div className="studio-agent-inline-error" role="alert">{localError}</div>
          ) : null}
          <AnimatePresence mode="wait" initial={false}>
            {isExternalAgentSetupTabId(activeTab) ? renderSetupTabContent(activeTab, 'inline') : null}
            {activeTab === 'overview' && (
              <MotionTabPanel key="external-overview" className="studio-agent-motion-panel">
                <ListDetailPanel
                  className="studio-panel studio-panel--detail"
                  hideHeaderText
                  eyebrow="Connected Agent"
                  title={displayName}
                  subtitle="A workspace-owned connection to an external agent. Empyralis owns routing, trust, and audit; the external runtime owns execution."
                  actions={(
                    <div className="app-inline-actions app-inline-actions--tight">
                      <AppButton type="button" tone="secondary" onClick={refreshManifest} disabled={isRefreshingManifest || isRevoked}>
                        {isRefreshingManifest ? 'Refreshing...' : 'Refresh manifest'}
                      </AppButton>
                    </div>
                  )}
                >
                  <div className="studio-agent-overview">
                    <div className="studio-agent-overview__readiness-hero">
                      <div className="studio-agent-overview__hero-copy">
                        <div className="studio-agent-overview__state-row">
                          <span className="studio-agent-overview__state-dot" data-tone={isVerified ? 'live' : isRevoked ? 'danger' : 'warning'} />
                          <DataBadge tone={isVerified ? 'success' : isRevoked ? 'danger' : 'warning'}>
                            {humanizeToken(connectionState, 'Unverified')}
                          </DataBadge>
                        </div>
                        <h3>{isVerified ? 'Connection verified' : isRevoked ? 'Connection revoked' : 'Verification needed'}</h3>
                        <p>Connected agents start workspace-only, approval-gated, and revocable. Public customer send is not enabled here.</p>
                        <div className="studio-agent-overview__chips" aria-label={`${displayName} boundary`}>
                          <span>{AGENT_STUDIO_OBJECT_LABELS.connected_external_agent}</span>
                          <span>External-owned</span>
                          <span>Workspace-only</span>
                          <span>{providerLabel}</span>
                        </div>
                      </div>
                    </div>
                    <AppSurfaceStatGrid>
                      <AppSurfaceStat label="External agent" value={providerLabel} hint="Declared connection type" />
                      <AppSurfaceStat label="Trust state" value={humanizeToken(trustState, 'Unverified')} hint="Verified after manifest refresh" />
                      <AppSurfaceStat label="Chat" value={hasChat ? 'Available' : 'Not exposed'} hint="Declared chat support" />
                      <AppSurfaceStat label="Endpoint" value={readString(endpointRefs.chat_url) ? 'Empyralis bridge' : 'Missing'} hint="External endpoint never called from browser" />
                      <AppSurfaceStat label="Protocols" value={protocols.length > 0 ? protocols.join(', ') : 'Custom HTTP'} hint="Adapter hints only" />
                      <AppSurfaceStat
                        label="Agent Computer"
                        value={localConnectorStatusLabel(localConnector)}
                        hint={localConnectorStatusHint(localConnector)}
                      />
                    </AppSurfaceStatGrid>
                    {localConnector.required ? (
                      <ListDetailPanel
                        className="studio-panel studio-panel--detail"
                        eyebrow="Agent Computer bridge"
                        title={readString(localConnector.agent_computer_label, 'Agent Computer required')}
                        subtitle="Local network endpoints stay blocked unless they are routed through a verified Agent Computer bridge."
                      >
                        <div className="studio-agent-overview__grid">
                          <div className="studio-agent-overview__card">
                            <div className="studio-agent-overview__card-icon"><ShieldCheck size={15} aria-hidden="true" /></div>
                            <div>
                              <strong>{humanizeToken(localConnector.binding_state, 'Needs Agent Computer')}</strong>
                              <span>{readString(localConnector.binding_message, 'Choose an Agent Computer before local runtime access is possible.')}</span>
                              <span>{localConnector.proxy_available ? 'Agent Computer bridge enabled' : 'Agent Computer bridge not enabled yet'}</span>
                            </div>
                          </div>
                          <div className="studio-agent-overview__card">
                            <div className="studio-agent-overview__card-icon"><Cable size={15} aria-hidden="true" /></div>
                            <div>
                              <strong>{humanizeToken(localConnector.mode, 'Agent Computer bridge')}</strong>
                              <span>Uses an approved Agent Computer bridge for local runtime access.</span>
                              <span>{readString(localConnector.attachment_kind) ? 'Agent Computer selected' : 'No Agent Computer selected'}</span>
                            </div>
                          </div>
                        </div>
                      </ListDetailPanel>
                    ) : null}
                    <ListDetailPanel
                      className="studio-panel studio-panel--detail"
                      eyebrow="Manifest"
                      title="Declared sections"
                      subtitle="Declared sections stay limited to this connected-agent surface and do not grant native Studio permissions."
                    >
                      {capabilityLabels.length > 0 ? (
                        <div className="studio-inline-wrap">
                          {capabilityLabels.map((label) => <DataBadge key={label}>{label}</DataBadge>)}
                        </div>
                      ) : (
                        <EmptyPanel title="No declared sections verified" body="Refresh the manifest after connecting the endpoint." />
                      )}
                    </ListDetailPanel>
                    <ListDetailPanel
                      className="studio-panel studio-panel--detail"
                      eyebrow="External sections"
                      title="External-agent surfaces"
                      subtitle="These read-only sections are schema-rendered by Studio. No external frontend code runs here."
                    >
                      {externalSections.length > 0 ? (
                        <div className="studio-agent-overview__grid">
                          {externalSections.map((section) => (
                            <div key={section.id} className="studio-agent-overview__card">
                              <div className="studio-agent-overview__card-icon"><FileText size={15} aria-hidden="true" /></div>
                              <div>
                                <strong>{section.title}</strong>
                                <span>{humanizeToken(section.category)} · {humanizeToken(section.displayKind)} · {humanizeToken(section.interaction, 'Read only')}</span>
                                {section.description ? <span>{section.description}</span> : null}
                                {section.capabilityRequired ? <span>Requires approved access</span> : null}
                                {section.actionsEndpointRef ? <span>Actions declared, approval support not enabled</span> : null}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <EmptyPanel title="No external sections declared" body="This connected agent has not exposed external-agent-owned Studio sections." />
                      )}
                    </ListDetailPanel>
                  </div>
                </ListDetailPanel>
              </MotionTabPanel>
            )}

            {activeTab === 'chat' && (
              <MotionTabPanel key="external-chat" className="studio-agent-motion-panel">
                <ListDetailPanel
                  className="studio-panel studio-panel--detail studio-panel--chat"
                  eyebrow="Chat"
                  title="Owner test chat"
                  subtitle="Workspace-only chat through Empyralis routing. No customer channel send."
                >
                  {!hasChat || !isVerified || isRevoked ? (
                    <EmptyPanel
                      title="Owner test chat is not available"
                      body={isRevoked ? 'This connection is revoked.' : !isVerified ? 'Refresh and verify the manifest before chatting.' : 'The manifest does not expose chat.'}
                    />
                  ) : (
                    <div className="studio-external-chat">
                      <div className="studio-external-chat__transcript" aria-live="polite">
                        {chatSession.messages.length === 0 ? (
                          <EmptyPanel title="No owner chat yet" body="Send a message to this connected agent. It stays inside Studio." />
                        ) : chatSession.messages.map((message) => (
                          <div key={message.id} className={joinClassNames(
                            'studio-external-chat__message',
                            message.role === 'user' && 'studio-external-chat__message--user',
                          )}>
                            <span>{message.role === 'user' ? 'You' : displayName}</span>
                            <p>{message.content}</p>
                          </div>
                        ))}
                      </div>
                      <div className="studio-external-chat__composer">
                        <AppTextarea
                          value={messageDraft}
                          rows={3}
                          placeholder="Message this connected agent inside Studio..."
                          onChange={(event) => setMessageDraft(event.currentTarget.value)}
                        />
                        <AppButton type="button" onClick={() => { void sendPrivateChatTurn(); }} disabled={isSending || !messageDraft.trim() || !isVerified || isRevoked}>
                          {isSending ? 'Sending...' : 'Send'}
                        </AppButton>
                      </div>
                    </div>
                  )}
                </ListDetailPanel>
              </MotionTabPanel>
            )}

          </AnimatePresence>
        </div>
      </div>
    </div>
  );
});

ConnectedExternalAgentDetailView.displayName = 'ConnectedExternalAgentDetailView';
