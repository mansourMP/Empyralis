'use client';

import type { PropsWithChildren } from 'react';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import { ArrowLeft, BookOpen, Brain, ChevronRight, Cpu, FolderOpen, LayoutGrid, Link2, ListTodo, Menu, MessageSquare, Monitor, Plus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { logout } from '@/lib/auth/auth-client';
import { useAccountShell } from '@/lib/shell/account-shell-context';
import { joinClassNames } from '@/lib/ui/primitives';
import {
  APPLICATION_TITLEBAR_TABS,
  buildApplicationTabHref,
} from '@/lib/workspace/application-surface-tabs';
import { WorkstationHardwareStatus } from '@/lib/workspace/workstation-hardware-status';
import { WorkstationTitlebar } from '@/lib/workspace/workstation-titlebar';
import type { ConnectedExternalAgentRecord, DeployedAgentRecord } from '@/lib/workspace/workstation-client';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import {
  emitWorkstationChatNewThreadRequested,
  emitWorkstationChatThreadSelected,
  subscribeWorkstationChatHistoryInvalidated,
} from '@/lib/workspace/workstation-chat-thread-events';
import { resolveRouteIdFromHref } from '@/lib/workspace/workspace-shell';
import type { RuntimeAttachmentSnapshot, SpecialistOverlayTabId } from '@/lib/workspace/deployed-agents/types';
import {
  NATIVE_SPECIALIST_OVERLAY_TABS,
  SPECIALIST_OVERLAY_TABS,
} from '@/lib/workspace/deployed-agents/constants';
import {
  studioPaneCache,
  studioAgentDisplayName,
  subscribeStudioPaneCache,
} from '@/lib/workspace/deployed-agents/utils';
import {
  activeThreadStorageKey,
  clearPersistedActiveThread,
  persistActiveThread,
} from '@/lib/workspace/workstation-chat-pane-model';
import {
  resolveExternalProviderBadge,
} from '@/lib/workspace/deployed-agents/external-agent-provider-badges';
import {
  buildWorkspaceRouteHref,
  getWorkspaceNavRouteDefinition,
  type WorkspaceNavDestinationId,
  type WorkspaceRouteId,
} from '../../../shared/nav-manifest';

const CONTEXT_ROUTE_IDS_BY_DESTINATION: Record<WorkspaceNavDestinationId, readonly WorkspaceRouteId[]> = {
  sage: ['chat', 'studio', 'activity', 'integrations', 'memory', 'tasks', 'artifacts'],
  studio: [],
  gateway: [],
  marketplace: ['marketplace'],
  applications: ['applications'],
  hardware: [],
  settings: ['settings'],
};

type ShellSectionId = WorkspaceNavDestinationId;

type SettingsPanelSectionId = 'account' | 'appearance' | 'usage' | 'billing' | 'privacy' | 'transparency';

const SETTINGS_PANEL_ITEMS: readonly {
  id: SettingsPanelSectionId;
  label: string;
}[] = [
  { id: 'account', label: 'Account' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'usage', label: 'Usage' },
  { id: 'billing', label: 'Limits' },
  { id: 'privacy', label: 'Privacy & Safety' },
  { id: 'transparency', label: 'Transparency' },
];

const DISCOVER_FILTERS: readonly {
  id: ShellSectionId;
  label: string;
  filter: MarketplaceTitlebarFilter;
}[] = [
  { id: 'marketplace', label: 'All', filter: 'all' },
  { id: 'marketplace', label: 'Agent templates', filter: 'agent_template' },
  { id: 'marketplace', label: 'Tools', filter: 'tools' },
  { id: 'marketplace', label: 'Apps', filter: 'apps' },
  { id: 'marketplace', label: 'MCP', filter: 'connector' },
  { id: 'marketplace', label: 'Skills', filter: 'skill' },
  { id: 'marketplace', label: 'Bundles', filter: 'bundle' },
];

const SAGE_SIDEBAR_NAV_ITEMS: readonly {
  routeId: WorkspaceRouteId;
  label: string;
  icon: LucideIcon;
}[] = [
  { routeId: 'memory', label: 'Memory', icon: Brain },
  { routeId: 'tasks', label: 'Tasks', icon: ListTodo },
  { routeId: 'artifacts', label: 'Library', icon: BookOpen },
];

const SAGE_SETUP_NAV_ITEMS: readonly {
  id: string;
  label: string;
  section: string;
  icon: LucideIcon;
}[] = [
  { id: 'ai-runtime', label: 'AI setup', section: 'ai-runtime', icon: Cpu },
  { id: 'connectors', label: 'Connectors', section: 'apps', icon: Link2 },
];

const MARKETPLACE_TITLEBAR_FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'agent_template', label: 'Agent templates' },
  { id: 'tools', label: 'Tools' },
  { id: 'apps', label: 'Apps' },
  { id: 'skill', label: 'Skills' },
  { id: 'connector', label: 'MCP' },
  { id: 'bundle', label: 'Bundles' },
] as const;

type MarketplaceTitlebarFilter = typeof MARKETPLACE_TITLEBAR_FILTERS[number]['id'];

function normalizeMarketplaceTitlebarFilter(value: string | null): MarketplaceTitlebarFilter {
  return MARKETPLACE_TITLEBAR_FILTERS.some((filter) => filter.id === value)
    ? value as MarketplaceTitlebarFilter
    : 'all';
}

function buildMarketplaceCategoryHref(workspaceId: string, filter: MarketplaceTitlebarFilter): string {
  const baseHref = buildWorkspaceRouteHref(workspaceId, 'marketplace');
  if (filter === 'all') {
    return baseHref;
  }
  return `${baseHref}?category=${encodeURIComponent(filter)}`;
}

function buildStudioCreateAgentHref(
  workspaceId: string,
  searchParams: { toString: () => string },
): string {
  const params = new URLSearchParams(searchParams.toString());
  params.set('createAgent', '1');
  params.delete('agent');
  params.delete('externalAgent');
  params.delete('agentComputer');
  const query = params.toString();
  return `${buildWorkspaceRouteHref(workspaceId, 'studio')}?${query}`;
}

type ThreadTurnRecord = Record<string, unknown> & {
  role?: string | null;
  content?: string | null;
};

type ThreadRecord = Record<string, unknown> & {
  id?: string | null;
  title?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_turn_at?: string | null;
  turns?: ThreadTurnRecord[] | null;
};

type ChatHistoryItem = {
  id: string;
  title: string;
  occurredAt: string | null;
};

type SageContextGroupLink = {
  id: string;
  label: string;
  detail: string;
  href: string;
  icon: LucideIcon;
};

type PanelStackLevel =
  | 'root'
  | 'assistant'
  | 'studio'
  | 'discover'
  | 'settings'
  | `agent:${string}`
  | `external-agent:${string}`;

type PanelTransitionDirection = 'forward' | 'back';

type AgentDetailPanelTabId = SpecialistOverlayTabId;

const AGENT_DETAIL_NAV_ITEMS: readonly {
  id: AgentDetailPanelTabId;
  label: string;
}[] = NATIVE_SPECIALIST_OVERLAY_TABS;

const PANEL_COLLAPSED_STORAGE_KEY = 'workstation_panel_collapsed_v2';

function readPanelCollapsedPreference(): boolean {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return true;
  }
  try {
    const stored = window.localStorage.getItem(PANEL_COLLAPSED_STORAGE_KEY);
    return stored === null ? true : stored === 'true';
  } catch {
    return true;
  }
}

function writePanelCollapsedPreference(collapsed: boolean): void {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(PANEL_COLLAPSED_STORAGE_KEY, collapsed ? 'true' : 'false');
  } catch {
    // Local storage preferences are non-critical.
  }
}

const SAGE_FOOTER_NAV_ITEMS: readonly {
  id: 'agents' | 'hardware';
  label: string;
  routeId: WorkspaceRouteId;
  icon: LucideIcon;
}[] = [
  { id: 'agents', label: 'Agents', routeId: 'studio', icon: LayoutGrid },
  { id: 'hardware', label: 'Hardware', routeId: 'hardware', icon: Monitor },
];

function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readRecordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => readRecord(item)).filter((item) => Object.keys(item).length > 0);
}

function humanizeAgentState(value: unknown): string {
  const normalized = readString(value, 'draft').replace(/[_-]+/g, ' ');
  return normalized.replace(/\b\w/g, (character) => character.toUpperCase());
}

function buildStudioAgentHref(
  workspaceId: string,
  searchParams: { toString: () => string },
  agentId: string,
): string {
  const params = new URLSearchParams(searchParams.toString());
  params.set('agent', agentId);
  params.delete('externalAgent');
  params.delete('externalSubAgent');
  params.delete('agentComputer');
  params.delete('studioFilter');
  params.delete('tab');
  params.delete('studioTab');
  params.delete('createAgent');
  const query = params.toString();
  return `${buildWorkspaceRouteHref(workspaceId, 'studio')}${query ? `?${query}` : ''}`;
}

function buildStudioAgentTabHref(
  workspaceId: string,
  searchParams: { toString: () => string },
  agentId: string,
  tabId: AgentDetailPanelTabId,
): string {
  const params = new URLSearchParams(searchParams.toString());
  params.set('agent', agentId);
  params.set('tab', tabId);
  params.delete('externalAgent');
  params.delete('externalSubAgent');
  params.delete('agentComputer');
  params.delete('studioFilter');
  params.delete('studioTab');
  params.delete('createAgent');
  const query = params.toString();
  return `${buildWorkspaceRouteHref(workspaceId, 'studio')}${query ? `?${query}` : ''}`;
}

function buildStudioExternalAgentHref(
  workspaceId: string,
  searchParams: { toString: () => string },
  externalAgentId: string,
): string {
  const params = new URLSearchParams(searchParams.toString());
  params.set('externalAgent', externalAgentId);
  params.delete('agent');
  params.delete('externalSubAgent');
  params.delete('agentComputer');
  params.delete('studioFilter');
  params.delete('tab');
  params.delete('studioTab');
  params.delete('createAgent');
  const query = params.toString();
  return `${buildWorkspaceRouteHref(workspaceId, 'studio')}${query ? `?${query}` : ''}`;
}

function buildStudioExternalSubAgentHref(
  workspaceId: string,
  searchParams: { toString: () => string },
  externalAgentId: string,
  externalSubAgentId: string,
): string {
  const params = new URLSearchParams(searchParams.toString());
  params.set('externalAgent', externalAgentId);
  params.set('externalSubAgent', externalSubAgentId);
  params.delete('agent');
  params.delete('agentComputer');
  params.delete('studioFilter');
  params.delete('tab');
  params.delete('studioTab');
  params.delete('createAgent');
  const query = params.toString();
  return `${buildWorkspaceRouteHref(workspaceId, 'studio')}${query ? `?${query}` : ''}`;
}

function buildStudioExternalAgentTabHref(
  workspaceId: string,
  searchParams: { toString: () => string },
  externalAgentId: string,
  tabId: AgentDetailPanelTabId,
): string {
  const params = new URLSearchParams(searchParams.toString());
  params.set('externalAgent', externalAgentId);
  params.set('tab', tabId);
  params.delete('agent');
  params.delete('agentComputer');
  params.delete('studioFilter');
  params.delete('studioTab');
  params.delete('createAgent');
  const query = params.toString();
  return `${buildWorkspaceRouteHref(workspaceId, 'studio')}${query ? `?${query}` : ''}`;
}

function normalizeAgentDetailTab(value: string | null): AgentDetailPanelTabId {
  if (value === 'connectors') {
    return 'tools';
  }
  return AGENT_DETAIL_NAV_ITEMS.some((item) => item.id === value)
    ? value as AgentDetailPanelTabId
    : 'overview';
}

function normalizeExternalAgentDetailTab(
  value: string | null,
  items: readonly {
    id: AgentDetailPanelTabId;
    label: string;
  }[],
): AgentDetailPanelTabId {
  if (value === 'connectors') {
    return items.some((item) => item.id === 'artifacts') ? 'artifacts' : 'analytics';
  }
  return items.some((item) => item.id === value)
    ? value as AgentDetailPanelTabId
    : 'overview';
}

function studioSelectionLevelFromQuery({
  selectedAgentId,
  selectedExternalAgentId,
}: {
  selectedAgentId: string | null;
  selectedExternalAgentId: string | null;
}): PanelStackLevel | null {
  if (selectedAgentId) {
    return `agent:${selectedAgentId}`;
  }
  if (selectedExternalAgentId) {
    return `external-agent:${selectedExternalAgentId}`;
  }
  return null;
}

function studioObjectLevelKind(level: PanelStackLevel): 'agent' | 'external-agent' | null {
  if (level.startsWith('agent:')) {
    return 'agent';
  }
  if (level.startsWith('external-agent:')) {
    return 'external-agent';
  }
  return null;
}

function capabilityEnabled(capabilityManifest: Record<string, unknown>, key: string): boolean {
  if (capabilityManifest[key] === true) {
    return true;
  }
  const capabilities = capabilityManifest.capabilities;
  return Array.isArray(capabilities) && capabilities.map((item) => readString(item).toLowerCase()).includes(key);
}

function externalAgentHasSectionToken(agent: ConnectedExternalAgentRecord | null, tokens: readonly string[]): boolean {
  const sections = readRecordArray(agent?.surface_sections);
  return sections.some((section) => {
    const values = [
      section.id,
      section.category,
      section.capability_required,
      section.capabilityRequired,
      section.display_kind,
      section.displayKind,
      section.title,
    ].map((value) => readString(value).toLowerCase());
    return tokens.some((token) => values.some((value) => value.includes(token)));
  });
}

function externalAgentHasObjectTypeToken(agent: ConnectedExternalAgentRecord | null, tokens: readonly string[]): boolean {
  const objectTypes = Array.isArray(agent?.object_types)
    ? agent.object_types.map((item) => readString(item).toLowerCase()).filter(Boolean)
    : [];
  return objectTypes.some((value) => tokens.some((token) => value.includes(token)));
}

function externalSubAgentsFor(agent: ConnectedExternalAgentRecord | null): Record<string, unknown>[] {
  return readRecordArray(agent?.external_sub_agents).filter((item) => readString(item.id || item.external_id));
}

function externalAgentHasArtifacts(agent: ConnectedExternalAgentRecord | null): boolean {
  const capabilityManifest = readRecord(agent?.capability_manifest);
  return (
    capabilityEnabled(capabilityManifest, 'artifacts')
    || capabilityEnabled(capabilityManifest, 'artifact')
    || capabilityEnabled(capabilityManifest, 'outputs')
    || capabilityEnabled(capabilityManifest, 'resources')
    || externalAgentHasObjectTypeToken(agent, ['external_agent_artifact', 'artifact', 'output', 'resource'])
    || externalAgentHasSectionToken(agent, ['artifact', 'output', 'resource'])
  );
}

function visibleExternalAgentDetailTabs(agent: ConnectedExternalAgentRecord | null): readonly {
  id: AgentDetailPanelTabId;
  label: string;
}[] {
  const capabilityManifest = readRecord(agent?.capability_manifest);
  return SPECIALIST_OVERLAY_TABS.filter((tab) => {
    if (tab.id === 'overview' || tab.id === 'chat' || tab.id === 'analytics') {
      return true;
    }
    if (tab.id === 'channels' || tab.id === 'ai' || tab.id === 'connectors') {
      return false;
    }
    if (tab.id === 'artifacts') {
      return externalAgentHasArtifacts(agent);
    }
    if (tab.id === 'knowledge') {
      return capabilityEnabled(capabilityManifest, 'knowledge_read') || externalAgentHasSectionToken(agent, ['knowledge', 'source']);
    }
    if (tab.id === 'memory') {
      return capabilityEnabled(capabilityManifest, 'memory_read') || externalAgentHasSectionToken(agent, ['memory']);
    }
    if (tab.id === 'tools') {
      return capabilityEnabled(capabilityManifest, 'actions') || capabilityEnabled(capabilityManifest, 'tools') || externalAgentHasSectionToken(agent, ['action', 'tool']);
    }
    return false;
  });
}

function agentStatusTone(value: unknown): 'live' | 'warning' | 'danger' {
  const normalized = readString(value, 'draft').toLowerCase();
  if (['live', 'active', 'running', 'online', 'connected'].includes(normalized)) {
    return 'live';
  }
  if (['blocked', 'failed', 'error', 'offline', 'disabled'].includes(normalized)) {
    return 'danger';
  }
  return 'warning';
}

function readRuntimeAttachmentId(computer: RuntimeAttachmentSnapshot): string {
  return readString(
    computer.attachmentId
    ?? (computer as Record<string, unknown>).id
    ?? (computer as Record<string, unknown>).attachment_id,
  );
}

function runtimeAttachmentDisplayKey(computer: RuntimeAttachmentSnapshot): string {
  return `${readString(computer.label, 'Agent Computer').trim().toLowerCase()}::${readString(computer.nodeKind, computer.attachmentKind).trim().toLowerCase()}`;
}

function runtimeAttachmentRank(computer: RuntimeAttachmentSnapshot): number {
  return (computer.online ? 2 : 0) + (computer.healthy ? 1 : 0);
}

function dedupeRuntimeAttachmentsForDisplay(computers: readonly RuntimeAttachmentSnapshot[]): RuntimeAttachmentSnapshot[] {
  const byDisplayKey = new Map<string, RuntimeAttachmentSnapshot>();
  for (const computer of computers) {
    const displayKey = runtimeAttachmentDisplayKey(computer);
    const current = byDisplayKey.get(displayKey);
    if (!current || runtimeAttachmentRank(computer) > runtimeAttachmentRank(current)) {
      byDisplayKey.set(displayKey, computer);
    }
  }
  return Array.from(byDisplayKey.values());
}

function normalizeThreadItems(payload: unknown): ThreadRecord[] {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const items = (payload as Record<string, unknown>).items;
  return Array.isArray(items)
    ? items.filter((item): item is ThreadRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function parseTimestamp(value: string | null): number {
  if (!value) {
    return Number.NEGATIVE_INFINITY;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function isPlaceholderTitle(title: string): boolean {
  const normalized = title.trim().toLowerCase();
  return normalized === '' || normalized === 'new chat' || normalized === 'chat' || normalized === 'primary thread';
}

function compactHistoryTitle(value: string): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return 'Conversation';
  }
  return normalized.length > 72 ? `${normalized.slice(0, 69).trimEnd()}...` : normalized;
}

function threadHistoryTitle(thread: ThreadRecord): string {
  const explicitTitle = readString(thread.title);
  if (explicitTitle && !isPlaceholderTitle(explicitTitle)) {
    return compactHistoryTitle(explicitTitle);
  }
  const turns = Array.isArray(thread.turns) ? thread.turns : [];
  const firstUserTurn = turns.find((turn) => readString(turn.role).toLowerCase() === 'user');
  const firstContent = readString(firstUserTurn?.content);
  return compactHistoryTitle(firstContent || explicitTitle || 'Conversation');
}

function formatHistoryDate(value: string | null): string {
  if (!value) {
    return 'Recent';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return 'Recent';
  }
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
}

function readActiveThreadId(workspaceId: string): string | null {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return null;
  }
  try {
    return readString(window.localStorage.getItem(activeThreadStorageKey(workspaceId))) || null;
  } catch {
    return null;
  }
}

function toHistoryItems(threads: ThreadRecord[]): ChatHistoryItem[] {
  return threads
    .map((thread) => {
      const id = readString(thread.id);
      if (!id) {
        return null;
      }
      const occurredAt = readString(thread.last_turn_at)
        || readString(thread.updated_at)
        || readString(thread.created_at)
        || null;
      return {
        id,
        title: threadHistoryTitle(thread),
        occurredAt,
      } satisfies ChatHistoryItem;
    })
    .filter((item): item is ChatHistoryItem => item !== null)
    .sort((left, right) => parseTimestamp(right.occurredAt) - parseTimestamp(left.occurredAt));
}

function _normalizeSurfaceLabel(value: unknown): string {
  const raw = String(value || '').trim();
  if (raw === 'sage') return 'Sage';
  if (raw === 'studio') return 'Agents';
  return raw;
}

function AssistantPanelContent({
  chatHref,
  client,
  activeRouteId,
  navigationItems,
  contextLinks,
  setupItems,
  workspaceId,
}: {
  chatHref: string;
  client: {
    listThreads: (options?: { includeTurns?: boolean; limit?: number }) => Promise<Record<string, unknown>>;
  };
  activeRouteId: WorkspaceRouteId | null;
  navigationItems: readonly SageContextLink[];
  contextLinks: readonly SageContextGroupLink[];
  setupItems: readonly SageContextLink[];
  workspaceId: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [items, setItems] = useState<ChatHistoryItem[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(() => readActiveThreadId(workspaceId));
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projectsOpen, setProjectsOpen] = useState(true);
  const [historyRefreshVersion, setHistoryRefreshVersion] = useState(0);

  useEffect(() => subscribeWorkstationChatHistoryInvalidated((detail) => {
    if (detail.workspaceId === workspaceId) {
      setHistoryRefreshVersion((current) => current + 1);
    }
  }), [workspaceId]);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    client.listThreads({ includeTurns: true, limit: 120 })
      .then((payload) => {
        if (!cancelled) {
          setItems(toHistoryItems(normalizeThreadItems(payload)));
          setActiveThreadId(readActiveThreadId(workspaceId));
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'History is unavailable right now.');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, historyRefreshVersion]);

  const openThread = (threadId: string) => {
    persistActiveThread(workspaceId, threadId);
    emitWorkstationChatThreadSelected({ workspaceId, threadId, force: true });
    setActiveThreadId(threadId);
    router.push(chatHref);
  };

  const createThread = () => {
    clearPersistedActiveThread(workspaceId);
    emitWorkstationChatNewThreadRequested({ workspaceId });
    setActiveThreadId(null);
    router.push(chatHref);
  };
  const sidebarItems = useMemo(() => [...navigationItems, ...setupItems], [navigationItems, setupItems]);
  const currentHref = `${pathname ?? ''}${searchParams.toString() ? `?${searchParams.toString()}` : ''}`;
  void contextLinks;

  return (
    <div className="workstation-shell-panel__assistant" data-workstation-shell-panel-content="assistant">
      <button
        type="button"
        className="workstation-shell-panel__primary-action"
        onClick={createThread}
        title="New chat"
      >
        <Plus size={14} aria-hidden="true" />
        <span>New chat</span>
      </button>
      <nav className="workstation-shell-panel__assistant-nav" aria-label="Assistant utilities">
        {sidebarItems.map((item) => {
          const href = item.href ?? buildWorkspaceRouteHref(workspaceId, item.routeId);
          const active = item.href ? href === currentHref : item.routeId === activeRouteId;
          return (
            <Link
              key={item.id}
              href={href}
              prefetch
              aria-current={active ? 'page' : undefined}
              title={item.label}
              className={joinClassNames(
                'workstation-shell-panel__assistant-link',
                active && 'workstation-shell-panel__assistant-link--active',
              )}
            >
              <item.icon size={14} aria-hidden="true" />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
      <section className="workstation-shell-panel__projects" aria-label="Projects">
        <button
          type="button"
          className="workstation-shell-panel__projects-heading"
          aria-expanded={projectsOpen}
          onClick={() => setProjectsOpen((current) => !current)}
        >
          <span>Projects</span>
          <ChevronRight
            className={joinClassNames(
              'workstation-shell-panel__projects-chevron',
              projectsOpen && 'workstation-shell-panel__projects-chevron--open',
            )}
            size={14}
            aria-hidden="true"
          />
        </button>
        {projectsOpen ? (
          <div className="workstation-shell-panel__projects-list">
            <Link href={chatHref} prefetch title="Current project" className="workstation-shell-panel__project-row">
              <FolderOpen size={14} aria-hidden="true" />
              <span>
                <strong>Current project</strong>
              </span>
            </Link>
          </div>
        ) : null}
      </section>
      <div className="workstation-shell-panel__history-list" aria-label="Conversation history">
        {isLoading ? (
          <div className="workstation-shell-panel__state">Loading chats...</div>
        ) : error ? (
          <div className="workstation-shell-panel__state">Chat history could not refresh.</div>
        ) : items.length === 0 ? (
          <div className="workstation-shell-panel__state">No chat history yet.</div>
        ) : items.map((item) => (
          <button
            key={item.id}
            type="button"
            className={joinClassNames(
              'workstation-shell-panel__history-row',
              activeThreadId === item.id && 'workstation-shell-panel__history-row--active',
            )}
            onClick={() => openThread(item.id)}
          >
            <span className="workstation-shell-panel__history-title">{item.title}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

type SageContextLink = {
  id: string;
  label: string;
  routeId: WorkspaceRouteId;
  icon: LucideIcon;
  href?: string;
};

function StudioPanelContent({
  agents,
  connectedExternalAgents,
  selectedAgentId,
  selectedExternalAgentId,
  selectedExternalSubAgentId,
  isAgentListPriming,
  isAgentListUnavailable,
  searchParams,
  onPushAgentDetail,
  onPushExternalAgentDetail,
  workspaceId,
}: {
  agents: readonly DeployedAgentRecord[];
  connectedExternalAgents: readonly ConnectedExternalAgentRecord[];
  selectedAgentId: string | null;
  selectedExternalAgentId: string | null;
  selectedExternalSubAgentId: string | null;
  isAgentListPriming: boolean;
  isAgentListUnavailable: boolean;
  searchParams: { toString: () => string };
  onPushAgentDetail: (agentId: string) => void;
  onPushExternalAgentDetail: (agentId: string) => void;
  workspaceId: string;
}) {
  const router = useRouter();
  const visibleAgents = useMemo(() => agents.filter((agent) => {
    const id = readString(agent.id);
    const state = readString(agent.deployment_state, 'draft').toLowerCase();
    if (!id || ['archived', 'deleted', 'removed'].includes(state)) {
      return false;
    }
    return true;
  }), [agents]);
  const visibleStudioObjectCount = visibleAgents.length + connectedExternalAgents.length;

  const openNativeAgent = (agentId: string) => {
    if (!agentId) {
      return;
    }
    onPushAgentDetail(agentId);
    router.push(buildStudioAgentHref(workspaceId, searchParams, agentId));
  };

  return (
    <div className="workstation-shell-panel__build" data-workstation-shell-panel-content="studio">
      <div className="workstation-shell-panel__build-list" aria-label="Agents">
        {isAgentListUnavailable ? (
          <div className="workstation-shell-panel__state">Agents are unavailable right now.</div>
        ) : isAgentListPriming ? (
          <div className="workstation-shell-panel__state">Loading agents...</div>
        ) : visibleStudioObjectCount === 0 ? (
          <div className="workstation-shell-panel__state">No agents loaded</div>
        ) : (
          <>
            {visibleAgents.length > 0 ? (
              <div className="workstation-shell-panel__build-section-label">Native Agents</div>
            ) : null}
            {visibleAgents.map((agent) => {
              const agentId = readString(agent.id);
              const state = readString(agent.deployment_state, 'draft');
              const stateLabel = humanizeAgentState(state);
              const displayName = studioAgentDisplayName(agent, agentId || 'Agent');
              return (
                <button
                  key={agentId}
                  type="button"
                  className={joinClassNames(
                    'workstation-shell-panel__build-row',
                    selectedAgentId === agentId && 'workstation-shell-panel__build-row--active',
                  )}
                  aria-current={selectedAgentId === agentId ? 'page' : undefined}
                  onClick={() => openNativeAgent(agentId)}
                >
                  <span className={joinClassNames('workstation-shell-panel__agent-dot', `workstation-shell-panel__agent-dot--${agentStatusTone(state)}`)} aria-hidden="true" />
                  <span className="workstation-shell-panel__build-row-label">{displayName}</span>
                  {state.toLowerCase() === 'draft' ? (
                    <span className="workstation-shell-panel__build-badge">{stateLabel}</span>
                  ) : null}
                </button>
              );
            })}
            {connectedExternalAgents.length > 0 ? (
              <div className="workstation-shell-panel__build-section-label">Connected Agents</div>
            ) : null}
            {connectedExternalAgents.map((agent) => {
              const agentId = readString(agent.id);
              const state = readString(agent.connection_state ?? agent.status, 'connected');
              const providerBadge = resolveExternalProviderBadge(agent.provider_kind);
              const subAgents = externalSubAgentsFor(agent);
              const configuredName = readString(agent.name ?? agent.label);
              const displayName = subAgents.length > 0 && configuredName.toLowerCase() === providerBadge.label.toLowerCase()
                ? `${providerBadge.label} Agents`
                : readString(configuredName, agentId || `${providerBadge.label} Agents`);
              return (
                <div key={`external-${agentId}`} className="workstation-shell-panel__external-family">
                  <button
                    type="button"
                    className={joinClassNames(
                      'workstation-shell-panel__build-row',
                      selectedExternalAgentId === agentId && !selectedExternalSubAgentId && 'workstation-shell-panel__build-row--active',
                    )}
                    aria-current={selectedExternalAgentId === agentId && !selectedExternalSubAgentId ? 'page' : undefined}
                    onClick={() => {
                      onPushExternalAgentDetail(agentId);
                      router.push(buildStudioExternalAgentHref(workspaceId, searchParams, agentId));
                    }}
                  >
                    <span className={joinClassNames('workstation-shell-panel__agent-dot', `workstation-shell-panel__agent-dot--${agentStatusTone(state)}`)} aria-hidden="true" />
                    <span className="workstation-shell-panel__build-row-identity">
                      <span className="workstation-shell-panel__build-row-label">{displayName}</span>
                      <span className="studio-agents-nav__provider-badge workstation-shell-panel__provider-badge" title={`${providerBadge.label} connected agent`}>
                        {providerBadge.imageSrc ? (
                          <img src={providerBadge.imageSrc} alt="" aria-hidden="true" />
                        ) : (
                          <span>{providerBadge.initials}</span>
                        )}
                      </span>
                    </span>
                  </button>
                  {subAgents.length > 0 ? (
                    <div className="workstation-shell-panel__external-child-list" role="group" aria-label={`${displayName} sub-agents`}>
                      {subAgents.map((subAgent) => {
                        const subAgentId = readString(subAgent.id || subAgent.external_id);
                        const subAgentName = readString(subAgent.name ?? subAgent.label ?? subAgent.title, subAgentId || 'External sub-agent');
                        const subAgentStatus = readString(subAgent.status);
                        const selected = selectedExternalAgentId === agentId && selectedExternalSubAgentId === subAgentId;
                        return (
                          <button
                            key={`${agentId}:${subAgentId}`}
                            type="button"
                            className={joinClassNames(
                              'workstation-shell-panel__build-row',
                              'workstation-shell-panel__build-row--external-child',
                              selected && 'workstation-shell-panel__build-row--active',
                            )}
                            aria-current={selected ? 'page' : undefined}
                            onClick={() => {
                              onPushExternalAgentDetail(agentId);
                              router.push(buildStudioExternalSubAgentHref(workspaceId, searchParams, agentId, subAgentId));
                            }}
                          >
                            <span className="workstation-shell-panel__external-child-rail" aria-hidden="true" />
                            <span className="workstation-shell-panel__build-row-label">{subAgentName}</span>
                            {subAgentStatus ? (
                              <span className="workstation-shell-panel__build-badge">{humanizeAgentState(subAgentStatus)}</span>
                            ) : null}
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </>
        )}
      </div>

      <button
        type="button"
        className="workstation-shell-panel__new-agent"
        onClick={() => router.push(buildStudioCreateAgentHref(workspaceId, searchParams))}
      >
        <Plus size={14} aria-hidden="true" />
        <span>New agent</span>
      </button>
    </div>
  );
}

function DiscoverPanelContent({
  activeFilter,
  workspaceId,
}: {
  activeFilter: MarketplaceTitlebarFilter;
  workspaceId: string;
}) {
  return (
    <nav className="workstation-shell-panel__plain-list" aria-label="Discover filters" data-workstation-shell-panel-content="discover">
      {DISCOVER_FILTERS.map((item) => {
        const active = item.filter === activeFilter;
        const href = buildMarketplaceCategoryHref(workspaceId, item.filter);
        return (
          <Link
            key={item.label}
            href={href}
            prefetch
            aria-current={active ? 'page' : undefined}
            className={joinClassNames(
              'workstation-shell-panel__plain-link',
              active && 'workstation-shell-panel__plain-link--active',
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

function buildSettingsSectionHref(settingsHref: string, sectionId: SettingsPanelSectionId): string {
  return `${settingsHref}${settingsHref.includes('?') ? '&' : '?'}section=${encodeURIComponent(sectionId)}`;
}

function isSettingsPanelSectionId(value: string | null): value is SettingsPanelSectionId {
  return SETTINGS_PANEL_ITEMS.some((item) => item.id === value);
}

function SettingsPanelContent({
  activeSection,
  settingsHref,
}: {
  activeSection: string | null;
  settingsHref: string;
}) {
  const selectedSection = isSettingsPanelSectionId(activeSection) ? activeSection : 'account';
  return (
    <nav className="workstation-shell-panel__build" aria-label="Settings sections" data-workstation-shell-panel-content="settings">
      <div className="workstation-shell-panel__build-list">
        {SETTINGS_PANEL_ITEMS.map((item) => (
          <Link
            key={item.id}
            href={buildSettingsSectionHref(settingsHref, item.id)}
            prefetch
            aria-current={selectedSection === item.id ? 'page' : undefined}
            className={joinClassNames(
              'workstation-shell-panel__plain-link',
              selectedSection === item.id && 'workstation-shell-panel__plain-link--active',
            )}
          >
            {item.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}

function StudioObjectDetailPanelContent({
  activeTab,
  items,
  buildHref,
}: {
  activeTab: AgentDetailPanelTabId;
  items: readonly {
    id: AgentDetailPanelTabId;
    label: string;
  }[];
  buildHref: (tabId: AgentDetailPanelTabId) => string;
}) {
  const activeItem = items.some((item) => item.id === activeTab) ? activeTab : 'overview';
  return (
    <nav className="workstation-shell-panel__agent-detail-nav" aria-label="Agent detail sections" data-workstation-shell-panel-content="agent-detail">
      {items.map((item) => (
        <Link
          key={item.id}
          href={buildHref(item.id)}
          prefetch
          aria-current={activeItem === item.id ? 'page' : undefined}
          className={joinClassNames(
            'workstation-shell-panel__agent-detail-link',
            activeItem === item.id && 'workstation-shell-panel__agent-detail-link--active',
          )}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

function ShellPanelHeader({
  level,
  title,
  onBack,
  onCollapse,
}: {
  level: PanelStackLevel;
  title: string;
  onBack: () => void;
  onCollapse: () => void;
}) {
  if (level === 'root') {
    return (
      <header className="workstation-shell-left-panel__header">
        <button
          type="button"
          className="workstation-shell-left-panel__header-toggle"
          aria-label="Collapse left panel"
          aria-expanded
          onClick={onCollapse}
        >
          <Menu size={17} aria-hidden="true" />
        </button>
        <strong className="workstation-shell-left-panel__brand-name">Empyralis</strong>
      </header>
    );
  }
  if (level === 'assistant') {
    return (
      <header className="workstation-shell-left-panel__assistant-header">
        <button
          type="button"
          className="workstation-shell-left-panel__header-toggle"
          aria-label="Back to main sections"
          onClick={onBack}
        >
          <ArrowLeft size={17} aria-hidden="true" />
        </button>
        <strong className="workstation-shell-left-panel__brand-name">Empyralis</strong>
        <button
          type="button"
          className="workstation-shell-left-panel__header-toggle"
          aria-label="Collapse left panel"
          aria-expanded
          onClick={onCollapse}
        >
          <Menu size={17} aria-hidden="true" />
        </button>
      </header>
    );
  }
  return (
    <header className="workstation-shell-left-panel__drill-header">
      <button
        type="button"
        className="workstation-shell-left-panel__header-toggle"
        aria-label="Back"
        onClick={onBack}
      >
        <ArrowLeft size={17} aria-hidden="true" />
      </button>
      <strong className="workstation-shell-left-panel__drill-title">{title}</strong>
    </header>
  );
}

function accountInitialForLabel(value: string): string {
  const normalized = value.trim();
  return normalized ? normalized.charAt(0).toUpperCase() : 'A';
}

function workspaceRoleLabel(value: unknown): string {
  const normalized = readString(value, 'member').toLowerCase();
  if (normalized === 'owner') {
    return 'Owner';
  }
  if (normalized === 'admin') {
    return 'Admin';
  }
  return 'Member';
}

function ShellAccountBlock({
  displayName,
  email,
  roleLabel,
  creditsHref,
  settingsHref,
}: {
  displayName: string;
  email: string;
  roleLabel: string;
  creditsHref: string;
  settingsHref: string;
}) {
  const { actions: accountShellActions } = useAccountShell();
  const [open, setOpen] = useState(false);
  const [logoutPending, setLogoutPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const accountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointerDown = (event: PointerEvent) => {
      if (accountRef.current?.contains(event.target as Node)) {
        return;
      }
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    window.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const handleLogout = async () => {
    setLogoutPending(true);
    setError(null);
    try {
      await logout();
      accountShellActions.clearSession();
      window.location.replace('/login');
    } catch {
      setError('Logout could not finish.');
      setLogoutPending(false);
    }
  };

  return (
    <div className="workstation-shell-account" ref={accountRef}>
      {open ? (
        <div className="workstation-shell-account__popover" role="menu" aria-label="Account menu">
          <Link className="workstation-shell-account__menu-row" href={settingsHref} role="menuitem">
            Settings
          </Link>
          <Link className="workstation-shell-account__menu-row" href={creditsHref} role="menuitem">
            <span>Credits</span>
            <span className="workstation-shell-account__menu-muted">Billing</span>
          </Link>
          <button type="button" className="workstation-shell-account__menu-row" role="menuitem" onClick={() => setOpen(false)}>
            Help
          </button>
          <button
            type="button"
            className="workstation-shell-account__menu-row"
            role="menuitem"
            disabled={logoutPending}
            onClick={() => { void handleLogout(); }}
          >
            {logoutPending ? 'Signing out...' : 'Log out'}
          </button>
          {error ? <div className="workstation-shell-account__error">{error}</div> : null}
        </div>
      ) : null}
      <button
        type="button"
        className="workstation-shell-account__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          setOpen((current) => !current);
          setError(null);
        }}
      >
        <span className="workstation-shell-account__avatar" aria-hidden="true">
          {accountInitialForLabel(displayName || email)}
        </span>
        <span className="workstation-shell-account__copy">
          <strong>{displayName}</strong>
          <span>{email}</span>
        </span>
        <span className="workstation-shell-account__plan">{roleLabel}</span>
      </button>
    </div>
  );
}

export function WorkstationKernelShell({
  children,
}: PropsWithChildren) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { bootstrap, routeManifest, workspaceId } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [panelPreferenceLoaded, setPanelPreferenceLoaded] = useState(false);
  const [panelStack, setPanelStack] = useState<PanelStackLevel[]>(['assistant']);
  const [panelTransitionDirection, setPanelTransitionDirection] = useState<PanelTransitionDirection>('forward');
  const [studioCacheRevision, setStudioCacheRevision] = useState(0);

  const activeRouteId = useMemo(
    () => resolveRouteIdFromHref(workspaceId, pathname),
    [pathname, workspaceId],
  );
  const activeDestinationId: WorkspaceNavDestinationId = useMemo(() => {
    if (!activeRouteId) {
      return 'sage';
    }
    return getWorkspaceNavRouteDefinition(activeRouteId).destinationId;
  }, [activeRouteId]);
  const workspaceLabel = bootstrap.workspace.label;
  const chatHref = routeManifest.routeIndex.chat?.href ?? buildWorkspaceRouteHref(workspaceId, 'chat');
  const settingsHref = routeManifest.routeIndex.settings?.href ?? buildWorkspaceRouteHref(workspaceId, 'settings');
  const hardwareHref = routeManifest.routeIndex.hardware?.href ?? buildWorkspaceRouteHref(workspaceId, 'hardware');
  const creditsHref = `${settingsHref}${settingsHref.includes('?') ? '&' : '?'}section=billing`;
  const accountEmail = readString(bootstrap.account.email, 'Account');
  const accountDisplayName = readString(bootstrap.account.displayName, accountEmail);
  const accountRoleLabel = workspaceRoleLabel(bootstrap.membership.role);
  const studioCache = useMemo(
    () => studioPaneCache.get(workspaceId),
    [studioCacheRevision, workspaceId],
  );
  const studioAgents = studioCache?.agents ?? [];
  const studioConnectedExternalAgents = studioCache?.connectedExternalAgents ?? [];
  const selectedStudioAgentId = activeDestinationId === 'studio' ? readString(searchParams.get('agent')) || null : null;
  const selectedStudioExternalAgentId = activeDestinationId === 'studio' ? readString(searchParams.get('externalAgent')) || null : null;
  const selectedStudioExternalSubAgentId = activeDestinationId === 'studio' ? readString(searchParams.get('externalSubAgent')) || null : null;
  const latestPanelLevel = panelStack[panelStack.length - 1] ?? 'assistant';
  const activeStudioAgentId = selectedStudioAgentId ?? (latestPanelLevel.startsWith('agent:') ? latestPanelLevel.slice('agent:'.length) : null);
  const activeStudioExternalAgentId = selectedStudioExternalAgentId ?? (latestPanelLevel.startsWith('external-agent:') ? latestPanelLevel.slice('external-agent:'.length) : null);
  const activeStudioAgent = activeStudioAgentId
    ? studioAgents.find((agent) => readString(agent.id) === activeStudioAgentId) ?? null
    : null;
  const activeStudioExternalAgent = activeStudioExternalAgentId
    ? studioConnectedExternalAgents.find((agent) => readString(agent.id) === activeStudioExternalAgentId) ?? null
    : null;
  const activeStudioExternalSubAgent = activeStudioExternalAgent && selectedStudioExternalSubAgentId
    ? externalSubAgentsFor(activeStudioExternalAgent).find((subAgent) => readString(subAgent.id || subAgent.external_id) === selectedStudioExternalSubAgentId) ?? null
    : null;
  const activeStudioAgentName = activeStudioAgent
    ? readString(activeStudioAgent.name, activeStudioAgentId ?? 'Agent')
    : activeStudioExternalSubAgent
      ? readString(activeStudioExternalSubAgent.name ?? activeStudioExternalSubAgent.label ?? activeStudioExternalSubAgent.title, selectedStudioExternalSubAgentId ?? 'External sub-agent')
      : activeStudioExternalAgent
      ? readString(activeStudioExternalAgent.name ?? activeStudioExternalAgent.label, activeStudioExternalAgentId ?? 'Connected agent')
      : activeStudioAgentId ?? activeStudioExternalAgentId ?? 'Agent';
  const activeExternalAgentDetailTabs = useMemo(
    () => visibleExternalAgentDetailTabs(activeStudioExternalAgent),
    [activeStudioExternalAgent],
  );
  const activeAgentDetailTab = normalizeAgentDetailTab(searchParams.get('tab'));
  const activeExternalAgentDetailTab = normalizeExternalAgentDetailTab(searchParams.get('tab'), activeExternalAgentDetailTabs);
  const isStudioAgentListPriming = studioCache?.isAgentListPriming ?? activeDestinationId === 'studio';
  const isStudioAgentListUnavailable = studioCache?.isAgentListUnavailable ?? false;
  const sageSidebarLinks = useMemo<SageContextLink[]>(() => SAGE_SIDEBAR_NAV_ITEMS.flatMap((item) => {
    const route = routeManifest.routeIndex[item.routeId];
    if (!route) {
      return [];
    }
    return [{
      id: item.routeId,
      label: item.label,
      routeId: item.routeId,
      icon: item.icon,
    }];
  }), [routeManifest.routeIndex]);
  const sageSetupLinks = useMemo<SageContextLink[]>(() => SAGE_SETUP_NAV_ITEMS.map((item) => ({
    id: item.id,
    label: item.label,
    routeId: 'integrations',
    icon: item.icon,
    href: `${buildWorkspaceRouteHref(workspaceId, 'integrations')}?section=${encodeURIComponent(item.section)}`,
  })), [workspaceId]);
  const sageContextLinks = useMemo<SageContextGroupLink[]>(() => {
    const links: SageContextGroupLink[] = [];
    const chatRoute = routeManifest.routeIndex.chat;
    if (chatRoute) {
      links.push({
        id: 'sage-conversation',
        label: 'Sage conversation',
        detail: 'Talk with the main agent',
        href: chatRoute.href,
        icon: MessageSquare,
      });
    }
    const tasksRoute = routeManifest.routeIndex.tasks;
    if (tasksRoute) {
      links.push({
        id: 'sage-plans',
        label: 'Plans and tasks',
        detail: 'Tasks Sage is helping with',
        href: tasksRoute.href,
        icon: ListTodo,
      });
    }
    const memoryRoute = routeManifest.routeIndex.memory;
    if (memoryRoute) {
      links.push({
        id: 'sage-context',
        label: 'Context',
        detail: 'Memory and facts Sage can use',
        href: memoryRoute.href,
        icon: Brain,
      });
    }
    return links;
  }, [routeManifest.routeIndex.chat, routeManifest.routeIndex.memory, routeManifest.routeIndex.tasks]);
  const contextRoutes = useMemo(() => {
    if (activeDestinationId === 'sage') {
      return [];
    }
    const routeIds = CONTEXT_ROUTE_IDS_BY_DESTINATION[activeDestinationId];
    return routeIds.flatMap((routeId) => {
      const route = routeManifest.routeIndex[routeId];
      if (!route) {
        return [];
      }
      return [route];
    });
  }, [activeDestinationId, routeManifest.routeIndex]);

  useEffect(() => {
    return subscribeStudioPaneCache((updatedWorkspaceId) => {
      if (updatedWorkspaceId === workspaceId) {
        setStudioCacheRevision((current) => current + 1);
      }
    });
  }, [workspaceId]);

  useEffect(() => {
    setPanelCollapsed(readPanelCollapsedPreference());
    setPanelPreferenceLoaded(true);
  }, []);

  useEffect(() => {
    if (!panelPreferenceLoaded) {
      return;
    }
    writePanelCollapsedPreference(panelCollapsed);
  }, [panelCollapsed, panelPreferenceLoaded]);

  useEffect(() => {
    if (activeDestinationId !== 'studio') {
      return;
    }
    const selectedStudioObjectLevel = studioSelectionLevelFromQuery({
      selectedAgentId: selectedStudioAgentId,
      selectedExternalAgentId: selectedStudioExternalAgentId,
    });
    setPanelTransitionDirection(selectedStudioObjectLevel ? 'forward' : 'back');
    setPanelStack((current) => {
      const currentLevel = current[current.length - 1] ?? 'root';
      if (selectedStudioObjectLevel) {
        return currentLevel === selectedStudioObjectLevel ? current : ['studio', selectedStudioObjectLevel];
      }
      return currentLevel === 'studio' ? current : ['studio'];
    });
  }, [activeDestinationId, selectedStudioAgentId, selectedStudioExternalAgentId]);

  const isContextRouteActive = (routeId: WorkspaceRouteId): boolean => {
    if (routeId === activeRouteId) {
      return true;
    }
    if (routeId === 'chat' && activeDestinationId === 'sage') {
      return pathname !== null && /\/(sage|chat)$/.test(pathname);
    }
    return false;
  };
  const marketplaceFilter = normalizeMarketplaceTitlebarFilter(searchParams.get('filter') ?? searchParams.get('category'));
  const activeDiscoverFilter = marketplaceFilter;
  const activeSettingsSection = searchParams.get('section');
  const activePanelDestinationId: WorkspaceNavDestinationId = activeDestinationId === 'settings'
    ? 'settings'
    : 'sage';
  const isFooterNavItemActive = (itemId: typeof SAGE_FOOTER_NAV_ITEMS[number]['id']): boolean => {
    if (itemId === 'agents') {
      return activeDestinationId === 'studio';
    }
    return activeDestinationId === 'gateway' || activeDestinationId === 'hardware';
  };
  const panelLevel = panelStack[panelStack.length - 1] ?? 'assistant';
  const visiblePanelLevel: PanelStackLevel = activeDestinationId === 'settings'
    ? 'settings'
    : panelLevel === 'root'
      ? 'assistant'
      : panelLevel;
  const studioAgentDetailActive = activeDestinationId === 'studio' && Boolean(
    selectedStudioAgentId || selectedStudioExternalAgentId,
  );
  const pushPanelLevel = (level: PanelStackLevel) => {
    setPanelTransitionDirection('forward');
    setPanelStack((current) => [...current, level]);
  };
  const replacePanelStack = (levels: PanelStackLevel[]) => {
    setPanelTransitionDirection('forward');
    setPanelStack(levels);
  };
  const popPanelLevel = () => {
    setPanelTransitionDirection('back');
    setPanelStack((current) => current.length > 1 ? current.slice(0, -1) : ['assistant']);
  };
  const clearStudioObjectSelection = () => {
    setPanelTransitionDirection('back');
    setPanelStack(['studio']);
    router.push(buildWorkspaceRouteHref(workspaceId, 'studio'));
  };
  const handlePanelBack = () => {
    if (activeDestinationId === 'settings') {
      setPanelTransitionDirection('back');
      setPanelStack(['assistant']);
      router.push(chatHref);
      return;
    }
    if (studioObjectLevelKind(panelLevel)) {
      clearStudioObjectSelection();
      return;
    }
    popPanelLevel();
  };
  useEffect(() => {
    if (activeDestinationId !== 'settings') {
      return;
    }
    setPanelTransitionDirection('forward');
    setPanelStack(['settings']);
  }, [activeDestinationId]);
  const titlebarNavigation = activeDestinationId === 'sage'
    ? null
    : activeDestinationId === 'marketplace'
      ? null
    : activeDestinationId === 'studio'
      ? null
    : activeDestinationId === 'settings'
      ? null
    : activeDestinationId === 'applications'
      ? APPLICATION_TITLEBAR_TABS.map((tab) => (
        <Link
          key={tab.id}
          href={buildApplicationTabHref(workspaceId, tab.id, searchParams)}
          prefetch
          aria-current="page"
          className={joinClassNames(
            'workstation-titlebar__link',
            'workstation-titlebar__link--active',
          )}
        >
          <span>{tab.label}</span>
        </Link>
      ))
    : contextRoutes.length > 0
      ? contextRoutes.map((route) => (
        <Link
          key={route.id}
          href={route.href}
          prefetch
          aria-current={isContextRouteActive(route.id) ? 'page' : undefined}
          className={joinClassNames(
            'workstation-titlebar__link',
            isContextRouteActive(route.id) && 'workstation-titlebar__link--active',
          )}
        >
          <span>{route.id === 'chat' ? 'Chat' : route.label}</span>
        </Link>
      ))
      : null;

  return (
    <div
      data-workstation-shell="kernel"
      data-workstation-main-layout="single-pane"
      data-workstation-route={activeRouteId ?? 'unknown'}
      data-workstation-destination={activeDestinationId}
      data-workstation-shell-section={activePanelDestinationId}
      data-workstation-shell-sidebar="unified"
      className={joinClassNames(
        'workstation-shell',
        activePanelDestinationId === 'sage' && 'workstation-shell--sage',
        studioAgentDetailActive && 'workstation-shell--studio-agent-detail',
        activeRouteId === 'chat' && 'workstation-shell--chat',
        panelCollapsed && 'workstation-shell--panel-collapsed',
      )}
    >
      <aside className="workstation-shell-left-panel" aria-label="Workspace navigation" data-panel-collapsed={panelCollapsed ? 'true' : 'false'}>
        {panelCollapsed ? (
        <div className="workstation-shell-left-panel__collapsed-rail">
          <button
            type="button"
            className="workstation-shell-left-panel__icon-button"
            aria-label="Expand left panel"
            aria-expanded={false}
            title="Expand"
            onClick={() => setPanelCollapsed(false)}
          >
            <Menu size={17} aria-hidden="true" />
          </button>
        </div>
        ) : null}

        {!panelCollapsed ? (
        <div className="workstation-shell-left-panel__inner" data-panel-level={visiblePanelLevel}>
          <ShellPanelHeader
            level={visiblePanelLevel}
            title={visiblePanelLevel === 'studio'
              ? 'Agents'
              : visiblePanelLevel === 'discover'
              ? 'Discover'
              : visiblePanelLevel === 'settings'
              ? 'Settings'
              : studioObjectLevelKind(visiblePanelLevel)
              ? activeStudioAgentName
              : 'Empyralis'}
            onBack={handlePanelBack}
            onCollapse={() => setPanelCollapsed(true)}
          />

          <div
            key={visiblePanelLevel}
            className="workstation-shell-left-panel__content"
            data-panel-direction={panelTransitionDirection}
          >
            {visiblePanelLevel === 'assistant' ? (
              <AssistantPanelContent
                chatHref={chatHref}
                client={services.client}
                activeRouteId={activeRouteId}
                navigationItems={sageSidebarLinks}
                contextLinks={sageContextLinks}
                setupItems={sageSetupLinks}
                workspaceId={workspaceId}
              />
            ) : visiblePanelLevel === 'studio' ? (
              <StudioPanelContent
                agents={studioAgents}
                connectedExternalAgents={studioConnectedExternalAgents}
                selectedAgentId={selectedStudioAgentId}
                selectedExternalAgentId={selectedStudioExternalAgentId}
                selectedExternalSubAgentId={selectedStudioExternalSubAgentId}
                isAgentListPriming={isStudioAgentListPriming}
                isAgentListUnavailable={isStudioAgentListUnavailable}
                searchParams={searchParams}
                onPushAgentDetail={(agentId) => pushPanelLevel(`agent:${agentId}`)}
                onPushExternalAgentDetail={(agentId) => pushPanelLevel(`external-agent:${agentId}`)}
                workspaceId={workspaceId}
              />
            ) : visiblePanelLevel === 'discover' ? (
              <DiscoverPanelContent
                activeFilter={activeDiscoverFilter}
                workspaceId={workspaceId}
              />
            ) : visiblePanelLevel === 'settings' ? (
              <SettingsPanelContent
                activeSection={activeSettingsSection}
                settingsHref={settingsHref}
              />
            ) : visiblePanelLevel.startsWith('agent:') ? (
              <StudioObjectDetailPanelContent
                activeTab={activeAgentDetailTab}
                items={AGENT_DETAIL_NAV_ITEMS}
                buildHref={(tabId) => buildStudioAgentTabHref(
                  workspaceId,
                  searchParams,
                  visiblePanelLevel.slice('agent:'.length),
                  tabId,
                )}
              />
            ) : visiblePanelLevel.startsWith('external-agent:') ? (
              <StudioObjectDetailPanelContent
                activeTab={activeExternalAgentDetailTab}
                items={activeExternalAgentDetailTabs}
                buildHref={(tabId) => buildStudioExternalAgentTabHref(
                  workspaceId,
                  searchParams,
                  visiblePanelLevel.slice('external-agent:'.length),
                  tabId,
                )}
              />
            ) : (
              <div className="workstation-shell-panel__placeholder">
                <span>Open a workspace section</span>
              </div>
            )}
          </div>

          <div className="workstation-shell-left-panel__footer">
            <nav className="workstation-shell-left-panel__footer-nav" aria-label="Workspace tools">
              {SAGE_FOOTER_NAV_ITEMS.map((item) => {
                const href = routeManifest.routeIndex[item.routeId]?.href
                  ?? buildWorkspaceRouteHref(workspaceId, item.routeId);
                const active = isFooterNavItemActive(item.id);
                return (
                  <Link
                    key={item.id}
                    href={href}
                    prefetch
                    aria-current={active ? 'page' : undefined}
                    title={item.label}
                    onClick={() => {
                      replacePanelStack(item.id === 'agents' ? ['studio'] : ['assistant']);
                    }}
                    className={joinClassNames(
                      'workstation-shell-panel__assistant-link',
                      active && 'workstation-shell-panel__assistant-link--active',
                    )}
                  >
                    <item.icon size={14} aria-hidden="true" />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>
            <ShellAccountBlock
              displayName={accountDisplayName}
              email={accountEmail}
              roleLabel={accountRoleLabel}
              creditsHref={creditsHref}
              settingsHref={settingsHref}
            />
          </div>
        </div>
        ) : null}
      </aside>

      <div className="workstation-shell__main-column" data-workstation-shell-zone="main-canvas">
        <div className="workstation-shell__topbar" data-workstation-main-pane="topbar">
          <WorkstationTitlebar
            surfaceLabel=""
            surfaceControl={null}
            diagnosticsVisible={false}
            onToggleDiagnostics={() => {}}
            actions={(
              <>
                <WorkstationHardwareStatus
                  runtimeTargets={bootstrap.runtime.runtimeTargets}
                  hardwareHref={hardwareHref}
                />
                {activeDestinationId === 'studio' ? (
                  <Link
                    href={buildStudioCreateAgentHref(workspaceId, searchParams)}
                    className="workstation-titlebar__link"
                    title="Add Business Agent"
                    aria-label="Add agent"
                  >
                    <Plus size={16} aria-hidden="true" />
                    <span>Add agent</span>
                  </Link>
                ) : activeDestinationId === 'applications' ? (
                  <Link
                    href={buildApplicationTabHref(workspaceId, 'my_apps', searchParams)}
                    className="workstation-titlebar__link workstation-titlebar__link--icon"
                    title="Create app"
                    aria-label="Create app"
                  >
                    <Plus size={16} aria-hidden="true" />
                  </Link>
                ) : null}
              </>
            )}
            navigation={titlebarNavigation}
          />
        </div>

        <div className="workstation-shell__body" data-workstation-main-pane="content-body">
          <div
            className="workstation-layout"
            data-workstation-destination={activeDestinationId}
            data-workstation-main-zone="main"
          >
            <section
              className="workstation-primary-canvas"
              data-workstation-focus-surface={activeRouteId ?? 'unknown'}
              data-workstation-main-pane="content"
            >
              {children}
            </section>
          </div>
        </div>

      </div>

    </div>
  );
}
