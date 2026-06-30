'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type SetStateAction } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { RefreshCw } from 'lucide-react';

import {
  ListDetailPanel,
  ListDetailShell,
} from '@/lib/ui/list-detail';
import { PlatformNotification } from '@/lib/ui/platform-notification';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { StudioIcon } from '@/lib/ui/icons';
import type {
  ConnectedExternalAgentRecord,
  DeployedAgentAnalyticsRecord,
  DeployedAgentConversationDetail,
  DeployedAgentConversationRecord,
  DeployedAgentMemoryRecord,
  DeployedAgentRecord,
  DeployedAgentTelegramReadinessRecord,
} from '@/lib/workspace/workstation-client';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import { WorkstationSplitWorkbench } from '@/lib/workspace/workstation-split-workbench';
import { WorkstationSurfaceRoot } from '@/lib/workspace/workstation-surface-primitives';
import type {
  AgentAnalyticsSnapshot,
  AgentOperationalMetrics,
  ConversationFilters,
  DetailConfigDraft,
  ProviderCatalogSnapshot,
  RuntimeAttachmentSnapshot,
  SpecialistOverlayTabId,
  StudioSubview,
  StudioTemplate,
  TelegramReadinessSnapshot,
  WizardMode,
  WizardState,
} from './deployed-agents/types';
import {
  CUSTOM_STUDIO_TEMPLATE,
  DEFAULT_SAFE_AGENT_PERSONA,
  DEFAULT_SAFE_AGENT_SYSTEM_PROMPT,
  DEFAULT_STUDIO_TEMPLATE,
  normalizeSpecialistOverlayTabId,
  PRIMARY_STUDIO_TEMPLATE_IDS,
  STUDIO_TEMPLATES,
} from './deployed-agents/constants';
import {
  StudioTemplateCard,
} from './deployed-agents/components';
import {
  AgentWizard,
} from './deployed-agents/wizard';
import {
  AgentDetailView,
} from './deployed-agents/detail-view';
import {
  AgentComputerDetailView,
} from './deployed-agents/agent-computer-detail';
import {
  ConnectedExternalAgentDetailView,
  createEmptyExternalAgentChatSession,
  type ExternalAgentChatSessionState,
} from './deployed-agents/external-agent-detail';
import {
  AgentInboxView,
} from './deployed-agents/inbox-view';
import {
  createEmptyDeployedAgentTestChatSession,
  type DeployedAgentTestChatSessionState,
} from './workstation-deployed-agent-test-turn-pane';
import {
  STUDIO_PANE_REFRESH_EVENT,
  studioPaneCache,
  updateStudioPaneCache,
  readString,
  readBoolean,
  readPositiveDecimalString,
  readRecord,
  readItems,
  normalizeRuntimeAttachments,
  selfHostedNodeGateReason,
  normalizeProviderCatalog,
  normalizeAgentAnalytics,
  normalizeTelegramReadiness,
  selectedProviderId,
  selectedModelId,
  providerCatalogById,
  agentModelDeployBlocker,
  normalizeWizardAiTier,
  normalizeRuntimePlacement,
  runtimeTargetForPlacement,
  testRuntimeModeForPlacement,
  inferAiTierFromProviderModel,
  pickStudioModelForTier,
  resolveProviderModelForTier,
  applyProviderCatalogDefaults,
  buildWizardState,
  buildDeploymentConfig,
  buildDetailConfigDraft,
  readBudgetCycle,
  summarizeConversationMetrics,
  buildMetricsPlaceholder,
  matchesConversationFilters,
  upsertAgentRecord,
  summarizeStudioErrorMessage,
  isWizardScopedError,
  listEnabledChannels,
  hasEnabledChannel,
  normalizeTemplateToken,
  studioTemplateById,
  normalizeStudioTemplates,
  parseKnowledgeSources,
  studioAgentDisplayName,
} from './deployed-agents/utils';

function useStableEvent<TArgs extends unknown[], TResult>(
  handler: (...args: TArgs) => TResult,
): (...args: TArgs) => TResult {
  const handlerRef = useRef(handler);

  useEffect(() => {
    handlerRef.current = handler;
  }, [handler]);

  return useCallback((...args: TArgs) => handlerRef.current(...args), []);
}

function runtimeAttachmentIdCandidates(item: RuntimeAttachmentSnapshot): Set<string> {
  const record = item as unknown as Record<string, unknown>;
  const values = [
    item.attachmentId,
    item.runtimeProfileId,
    item.runtimeNodeId,
    item.attachmentKind && item.attachmentId ? `${item.attachmentKind}:${item.attachmentId}` : '',
    item.attachmentKind && item.runtimeProfileId ? `${item.attachmentKind}:${item.runtimeProfileId}` : '',
    item.attachmentKind && item.runtimeNodeId ? `${item.attachmentKind}:${item.runtimeNodeId}` : '',
    record.id,
    record.attachment_id,
    record.runtime_profile_id,
    record.runtime_node_id,
  ];
  return new Set(values.map((value) => readString(value)).filter(Boolean));
}

function normalizeSurfaceAgentComputers(payload: unknown): RuntimeAttachmentSnapshot[] {
  const agentComputers = readRecord(payload).agent_computers;
  if (!Array.isArray(agentComputers)) {
    return [];
  }
  return agentComputers.map((item) => readRecord(item)).map((surface) => {
    const record = readRecord(surface.record);
    const attachmentId = readString(record.attachment_id ?? surface.id);
    const attachmentKind = readString(record.attachment_kind ?? record.node_kind ?? record.kind);
    const runtimeProfileId = readString(record.runtime_profile_id);
    const runtimeNodeId = readString(record.runtime_node_id ?? record.node_id);
    const status = readString(record.status ?? surface.status, 'unknown');
    const capabilityReadiness = readRecord(record.capability_readiness);
    const readyCapabilities = Array.isArray(capabilityReadiness.ready)
      ? capabilityReadiness.ready.map((value) => readString(value)).filter(Boolean)
      : [];
    const requestedCapabilities = Array.isArray(capabilityReadiness.requested)
      ? capabilityReadiness.requested.map((value) => readString(value)).filter(Boolean)
      : [];
    const capabilities = Array.from(new Set([
      ...readyCapabilities,
      ...requestedCapabilities,
      ...(Array.isArray(record.capabilities) ? record.capabilities.map((value) => readString(value)).filter(Boolean) : []),
    ]));
    return {
      attachmentId,
      attachmentKind,
      runtimeProfileId,
      runtimeNodeId,
      label: readString(record.label ?? record.runtime_profile_label ?? surface.name, 'Agent Computer'),
      online: readBoolean(record.online) || status.toLowerCase() === 'online',
      healthy: readBoolean(record.healthy),
      ownerApproved: readBoolean(record.owner_approved ?? record.ownerApproved),
      selfHostedNodeStatus: readString(record.self_hosted_node_status),
      nodeKind: readString(record.node_kind ?? record.kind ?? attachmentKind),
      heartbeatAt: readString(record.last_heartbeat_at ?? record.last_seen_at) || null,
      capabilities,
      status,
    } satisfies RuntimeAttachmentSnapshot;
  }).filter((item) => runtimeAttachmentIdCandidates(item).size > 0);
}

function connectedAgentHasArtifactSurface(agent: ConnectedExternalAgentRecord | null): boolean {
  const capabilityManifest = readRecord(agent?.capability_manifest);
  const capabilities = Array.isArray(capabilityManifest.capabilities)
    ? capabilityManifest.capabilities.map((item) => readString(item).toLowerCase()).filter(Boolean)
    : [];
  const capabilityKeys = ['artifacts', 'artifact', 'outputs', 'resources'];
  if (capabilityKeys.some((key) => capabilityManifest[key] === true || capabilities.includes(key))) {
    return true;
  }
  const objectTypes = Array.isArray(agent?.object_types)
    ? agent.object_types.map((item) => readString(item).toLowerCase()).filter(Boolean)
    : [];
  if (objectTypes.some((item) => ['external_agent_artifact', 'artifact', 'output', 'resource'].some((token) => item.includes(token)))) {
    return true;
  }
  const sections = Array.isArray(agent?.surface_sections)
    ? agent.surface_sections.map((item) => readRecord(item))
    : [];
  return sections.some((section) => ['artifact', 'output', 'resource'].some((token) => [
    section.id,
    section.category,
    section.capability_required,
    section.display_kind,
    section.title,
  ].some((value) => readString(value).toLowerCase().includes(token))));
}

function isConnectedExternalAgentActive(agent: ConnectedExternalAgentRecord): boolean {
  const lifecycle = [
    agent.connection_state,
    agent.trust_state,
    agent.status,
    agent.lifecycle_status,
  ].map((value) => readString(value).toLowerCase());
  return agent.enabled !== false && !lifecycle.some((value) => value === 'revoked' || value === 'disconnected');
}

function StudioAgentStartPanel({
  studioTemplates,
  onOpenCreateWizard,
}: {
  studioTemplates: StudioTemplate[];
  onOpenCreateWizard: (templateId: string) => void;
}) {
  const primaryStudioTemplates = studioTemplates.filter((template) => PRIMARY_STUDIO_TEMPLATE_IDS.has(template.id));
  const additionalStudioTemplates = studioTemplates.filter((template) => !PRIMARY_STUDIO_TEMPLATE_IDS.has(template.id));

  return (
    <div className="studio-agent-start app-stack-4" aria-label="Create Business Agent">
      <ListDetailPanel
        className="studio-panel studio-panel--start"
        eyebrow="Start here"
        title="Create one working Business Agent"
        subtitle="Choose the business job, add the facts it should trust, test privately, then go live."
        actions={(
          <AppButton type="button" tone="primary" onClick={() => onOpenCreateWizard(CUSTOM_STUDIO_TEMPLATE.id)}>
            Add agent
          </AppButton>
        )}
      >
        <div className="studio-agent-start__steps" aria-label="Business Agent creation steps">
          <span>1. Pick job</span>
          <span>2. Add instructions</span>
          <span>3. Add knowledge</span>
          <span>4. Test and deploy</span>
        </div>
      </ListDetailPanel>

      <ListDetailPanel
        className="studio-panel studio-panel--templates"
        eyebrow="Templates"
        title="What do you need?"
        subtitle="Start from a common business workflow, then tune model, actions, memory, and channels after the agent exists."
      >
        <div className="studio-template-grid" data-studio-template-grid="true">
          {primaryStudioTemplates.map((template) => (
            <StudioTemplateCard
              key={template.id}
              template={template}
              onSelect={onOpenCreateWizard}
            />
          ))}
        </div>
        {additionalStudioTemplates.length > 0 ? (
          <details className="app-stack-3">
            <summary>More Business Agent templates</summary>
            <div className="studio-template-grid" data-studio-template-grid="more">
              {additionalStudioTemplates.map((template) => (
                <StudioTemplateCard
                  key={template.id}
                  template={template}
                  onSelect={onOpenCreateWizard}
                />
              ))}
            </div>
          </details>
        ) : null}
      </ListDetailPanel>
    </div>
  );
}

export function WorkstationDeployedAgentsPane({
  initialSubview = 'agents',
}: {
  initialSubview?: StudioSubview;
}) {
  const { workspaceId, bootstrap } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const cachedStudioPane = studioPaneCache.get(workspaceId) ?? null;
  const [hadInitialCache] = useState(() => cachedStudioPane !== null);
  const [currentStudioSubview, setCurrentStudioSubview] = useState<StudioSubview>(initialSubview);
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogSnapshot[]>(() => cachedStudioPane?.providerCatalog ?? []);
  const [studioTemplates, setStudioTemplates] = useState<StudioTemplate[]>(() => [...STUDIO_TEMPLATES]);
  const [agents, setAgents] = useState<DeployedAgentRecord[]>(() => cachedStudioPane?.agents ?? []);
  const [connectedExternalAgents, setConnectedExternalAgents] = useState<ConnectedExternalAgentRecord[]>(() => cachedStudioPane?.connectedExternalAgents ?? []);
  const [externalAgentSurfaceError, setExternalAgentSurfaceError] = useState<string | null>(null);
  const [connectorVaultIds, setConnectorVaultIds] = useState<Set<string>>(() => new Set(cachedStudioPane?.connectorVaultIds ?? []));
  const [agentMetricsById, setAgentMetricsById] = useState<Record<string, AgentOperationalMetrics>>(() => cachedStudioPane?.agentMetricsById ?? {});
  const [agentAnalyticsById, setAgentAnalyticsById] = useState<Record<string, AgentAnalyticsSnapshot>>(() => cachedStudioPane?.agentAnalyticsById ?? {});
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(() => cachedStudioPane?.selectedAgentId ?? null);
  const [selectedExternalAgentId, setSelectedExternalAgentId] = useState<string | null>(() => cachedStudioPane?.selectedExternalAgentId ?? null);
  const [selectedAgentComputerId, setSelectedAgentComputerId] = useState<string | null>(() => cachedStudioPane?.selectedAgentComputerId ?? null);
  const [testChatSessionsByAgentId, setTestChatSessionsByAgentId] = useState<Record<string, DeployedAgentTestChatSessionState>>({});
  const [externalAgentChatSessionsById, setExternalAgentChatSessionsById] = useState<Record<string, ExternalAgentChatSessionState>>({});
  const [overlayAgentId, setOverlayAgentId] = useState<string | null>(null);
  const [overlayTab, setOverlayTab] = useState<SpecialistOverlayTabId>(() => (
    normalizeSpecialistOverlayTabId(searchParams.get('tab') || searchParams.get('studioTab'))
  ));
  const [selectedAgentDetail, setSelectedAgentDetail] = useState<DeployedAgentRecord | null>(null);
  const [selectedAgentAnalytics, setSelectedAgentAnalytics] = useState<AgentAnalyticsSnapshot | null>(null);
  const [selectedTelegramReadiness, setSelectedTelegramReadiness] = useState<TelegramReadinessSnapshot | null>(null);
  const [conversations, setConversations] = useState<DeployedAgentConversationRecord[]>([]);
  const [agentMemoryById, setAgentMemoryById] = useState<Record<string, DeployedAgentMemoryRecord[]>>({});
  const [runtimeAttachments, setRuntimeAttachments] = useState<RuntimeAttachmentSnapshot[]>(() => cachedStudioPane?.runtimeAttachments ?? []);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedTranscript, setSelectedTranscript] = useState<DeployedAgentConversationDetail | null>(null);
  const [isLoadingAgents, setIsLoadingAgents] = useState(true);
  const [hasLoadedAgentListOnce, setHasLoadedAgentListOnce] = useState(false);
  const [isLoadingProviderCatalog, setIsLoadingProviderCatalog] = useState(false);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [isLoadingAnalytics, setIsLoadingAnalytics] = useState(false);
  const [isLoadingTelegramReadiness, setIsLoadingTelegramReadiness] = useState(false);
  const [isLoadingConversations, setIsLoadingConversations] = useState(false);
  const [isLoadingOverlayMemory, setIsLoadingOverlayMemory] = useState(false);
  const [isLoadingTranscript, setIsLoadingTranscript] = useState(false);
  const [isLoadingRuntimeAttachments, setIsLoadingRuntimeAttachments] = useState(false);
  const [isSavingProviderCredential, setIsSavingProviderCredential] = useState(false);
  const [isWizardOpen, setIsWizardOpen] = useState(false);
  const [wizardMode, setWizardMode] = useState<WizardMode>('create');
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>(() => CUSTOM_STUDIO_TEMPLATE.id);
  const handledTemplateDeepLinkRef = useRef<string | null>(null);
  const [detailConfigDraft, setDetailConfigDraft] = useState<DetailConfigDraft | null>(null);
  const [isSavingDetailConfig, setIsSavingDetailConfig] = useState(false);
  const [busyAgentId, setBusyAgentId] = useState<string | null>(null);
  const [busyExternalUserId, setBusyExternalUserId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [conversationFilters, setConversationFilters] = useState<ConversationFilters>({
    channel: 'all',
    escalationState: 'all',
    outcome: 'all',
  });

  const selectedAgent = useMemo(() => {
    if (!selectedAgentId) {
      return null;
    }
    if (selectedAgentDetail && readString(selectedAgentDetail.id) === selectedAgentId) {
      return selectedAgentDetail;
    }
    return agents.find((item) => readString(item.id) === selectedAgentId) ?? null;
  }, [agents, selectedAgentDetail, selectedAgentId]);
  const selectedExternalAgent = useMemo(
    () => connectedExternalAgents.find((item) => readString(item.id) === selectedExternalAgentId) ?? null,
    [connectedExternalAgents, selectedExternalAgentId],
  );
  const selectedAgentComputer = useMemo(
    () => runtimeAttachments.find((item) => runtimeAttachmentIdCandidates(item).has(readString(selectedAgentComputerId))) ?? null,
    [runtimeAttachments, selectedAgentComputerId],
  );
  const selectedAgentMetrics = useMemo(
    () => (selectedAgentId ? agentMetricsById[selectedAgentId] ?? null : null),
    [agentMetricsById, selectedAgentId],
  );
  const selectedAnalytics = useMemo(
    () => selectedAgentAnalytics ?? (selectedAgentId ? agentAnalyticsById[selectedAgentId] ?? null : null),
    [agentAnalyticsById, selectedAgentAnalytics, selectedAgentId],
  );
  const selectedAgentTestChatSession = selectedAgentId
    ? testChatSessionsByAgentId[selectedAgentId] ?? createEmptyDeployedAgentTestChatSession()
    : createEmptyDeployedAgentTestChatSession();
  const handleSelectedAgentTestChatSessionChange = useCallback((updater: SetStateAction<DeployedAgentTestChatSessionState>) => {
    if (!selectedAgentId) {
      return;
    }
    setTestChatSessionsByAgentId((current) => {
      const previous = current[selectedAgentId] ?? createEmptyDeployedAgentTestChatSession();
      const next = typeof updater === 'function'
        ? (updater as (value: DeployedAgentTestChatSessionState) => DeployedAgentTestChatSessionState)(previous)
        : updater;
      return {
        ...current,
        [selectedAgentId]: next,
      };
    });
  }, [selectedAgentId]);
  const handleResetSelectedAgentTestChatSession = useCallback(() => {
    if (!selectedAgentId) {
      return;
    }
    setTestChatSessionsByAgentId((current) => ({
      ...current,
      [selectedAgentId]: createEmptyDeployedAgentTestChatSession(),
    }));
  }, [selectedAgentId]);
  const selectedExternalAgentChatSession = selectedExternalAgentId
    ? externalAgentChatSessionsById[selectedExternalAgentId] ?? createEmptyExternalAgentChatSession()
    : createEmptyExternalAgentChatSession();
  const handleSelectedExternalAgentChatSessionChange = useCallback((updater: SetStateAction<ExternalAgentChatSessionState>) => {
    if (!selectedExternalAgentId) {
      return;
    }
    setExternalAgentChatSessionsById((current) => {
      const previous = current[selectedExternalAgentId] ?? createEmptyExternalAgentChatSession();
      const next = typeof updater === 'function'
        ? (updater as (value: ExternalAgentChatSessionState) => ExternalAgentChatSessionState)(previous)
        : updater;
      return {
        ...current,
        [selectedExternalAgentId]: next,
      };
    });
  }, [selectedExternalAgentId]);
  useEffect(() => {
    setTestChatSessionsByAgentId({});
    setExternalAgentChatSessionsById({});
  }, [workspaceId]);
  const providerCatalogIndex = useMemo(
    () => providerCatalogById(providerCatalog),
    [providerCatalog],
  );
  const selfHostedNodeOptions = useMemo(
    () => runtimeAttachments
      .filter((item) => item.runtimeProfileId)
      .sort((left, right) => left.label.localeCompare(right.label)),
    [runtimeAttachments],
  );
  const selectedAgentRuntimePlacement = useMemo(() => {
    const config = readRecord(selectedAgent?.config);
    const metadata = readRecord(selectedAgent?.metadata);
    const runtimeSupply = readRecord(config.runtime_supply ?? metadata.runtime_supply);
    const placement = readRecord(runtimeSupply.placement);
    return normalizeRuntimePlacement(
      placement.kind
      ?? config.runtime_placement
      ?? metadata.runtime_placement
      ?? selectedAgent?.runtime_target,
    );
  }, [selectedAgent]);
  const selectedAgentSelfHostedProfileId = useMemo(() => {
    const config = readRecord(selectedAgent?.config);
    const metadata = readRecord(selectedAgent?.metadata);
    const binding = readRecord(metadata.self_hosted_runtime_binding);
    return readString(config.runtime_profile_id ?? binding.runtime_profile_id ?? metadata.runtime_profile_id);
  }, [selectedAgent]);
  const selectedAgentSelfHostedNode = useMemo(
    () => selfHostedNodeOptions.find((item) => item.runtimeProfileId === selectedAgentSelfHostedProfileId) ?? null,
    [selectedAgentSelfHostedProfileId, selfHostedNodeOptions],
  );
  const selectedAgentSelfHostedDeployBlocker = useMemo(() => {
    if (selectedAgentRuntimePlacement !== 'customer_hosted') {
      return null;
    }
    if (!selectedAgentSelfHostedProfileId) {
      return 'Server/VPS deployment requires an explicit runtime binding.';
    }
    return selfHostedNodeGateReason(selectedAgentSelfHostedNode);
  }, [selectedAgentRuntimePlacement, selectedAgentSelfHostedNode, selectedAgentSelfHostedProfileId]);
  const selectedAgentModelDeployBlocker = useMemo(
    () => agentModelDeployBlocker(selectedAgent, providerCatalogIndex),
    [providerCatalogIndex, selectedAgent],
  );
  const selectedStudioTemplate = useMemo(
    () => studioTemplateById(selectedTemplateId, studioTemplates),
    [selectedTemplateId, studioTemplates],
  );
  const filteredConversations = useMemo(
    () => conversations.filter((conversation) => matchesConversationFilters(conversation, conversationFilters)),
    [conversationFilters, conversations],
  );
  const selectedConversation = useMemo(
    () => filteredConversations.find((item) => readString(item.session_id) === selectedSessionId) ?? null,
    [filteredConversations, selectedSessionId],
  );
  const channelFilterOptions = useMemo(
    () => Array.from(new Set(conversations.map((item) => readString(item.channel).toLowerCase()).filter(Boolean))),
    [conversations],
  );
  const escalationFilterOptions = useMemo(
    () => Array.from(new Set(conversations.map((item) => readString(item.escalation_state).toLowerCase()).filter(Boolean))),
    [conversations],
  );
  const outcomeFilterOptions = useMemo(
    () => Array.from(new Set(conversations.map((item) => readString(item.outcome).toLowerCase()).filter(Boolean))),
    [conversations],
  );
  const requestedAgentId = useMemo(
    () => String(searchParams.get('agent') || '').trim(),
    [searchParams],
  );
  const requestedExternalAgentId = useMemo(
    () => String(searchParams.get('externalAgent') || '').trim(),
    [searchParams],
  );
  const requestedAgentComputerId = useMemo(
    () => String(searchParams.get('agentComputer') || '').trim(),
    [searchParams],
  );
  const requestedProofAgentTemplateId = useMemo(
    () => String(searchParams.get('proof_agent') || searchParams.get('template') || '').trim(),
    [searchParams],
  );
  const requestedCreateAgent = searchParams.get('createAgent') === '1';
  const requestedOverlayTab = useMemo(
    () => {
      const requestedTab = searchParams.get('tab') || searchParams.get('studioTab');
      if (requestedTab === 'connectors' && requestedExternalAgentId) {
        return connectedAgentHasArtifactSurface(selectedExternalAgent) ? 'artifacts' : 'analytics';
      }
      return normalizeSpecialistOverlayTabId(requestedTab);
    },
    [requestedExternalAgentId, searchParams, selectedExternalAgent],
  );
  const replaceStudioQuery = useCallback((mutate: (params: URLSearchParams) => void) => {
    const params = new URLSearchParams(searchParams.toString());
    mutate(params);
    const query = params.toString();
    router.replace(`${pathname}${query ? `?${query}` : ''}`, { scroll: false });
  }, [pathname, router, searchParams]);

  useEffect(() => {
    if (!searchParams.has('studioFilter')) {
      return;
    }
    replaceStudioQuery((params) => {
      params.delete('studioFilter');
    });
  }, [replaceStudioQuery, searchParams]);

  useEffect(() => {
    const requestedTab = searchParams.get('tab') || searchParams.get('studioTab');
    if (requestedTab !== 'connectors') {
      return;
    }
    if (requestedExternalAgentId && !selectedExternalAgent) {
      return;
    }
    const nextTab = requestedExternalAgentId
      ? connectedAgentHasArtifactSurface(selectedExternalAgent) ? 'artifacts' : 'analytics'
      : 'tools';

    replaceStudioQuery((params) => {
      params.set('tab', nextTab);
      params.delete('studioTab');
    });
  }, [replaceStudioQuery, requestedExternalAgentId, searchParams, selectedExternalAgent]);

  const selectOverlayTab = useCallback((nextTab: SpecialistOverlayTabId) => {
    setOverlayTab(nextTab);
    replaceStudioQuery((params) => {
      if (nextTab === 'overview') {
        params.delete('tab');
        params.delete('studioTab');
      } else {
        params.set('tab', nextTab);
        params.delete('studioTab');
      }
    });
  }, [replaceStudioQuery]);
  const openSelectedAgentDetail = useCallback((agentId: string) => {
    const normalizedAgentId = readString(agentId);
    if (!normalizedAgentId) {
      return;
    }
    setOverlayTab('overview');
    setSelectedExternalAgentId(null);
    setSelectedAgentComputerId(null);
    replaceStudioQuery((params) => {
      params.set('agent', normalizedAgentId);
      params.delete('externalAgent');
      params.delete('agentComputer');
      params.delete('studioFilter');
      params.delete('tab');
      params.delete('studioTab');
      params.delete('createAgent');
    });
  }, [replaceStudioQuery]);
  const closeSelectedAgentDetail = useCallback(() => {
    setOverlayTab('overview');
    setOverlayAgentId(null);
    setSelectedAgentId(null);
    setSelectedExternalAgentId(null);
    setSelectedAgentComputerId(null);
    replaceStudioQuery((params) => {
      params.delete('agent');
      params.delete('externalAgent');
      params.delete('agentComputer');
      params.delete('studioFilter');
      params.delete('tab');
      params.delete('studioTab');
    });
  }, [replaceStudioQuery]);

  useEffect(() => {
    setOverlayTab(requestedOverlayTab);
  }, [requestedOverlayTab]);

  async function refreshAgentAnalytics(items: DeployedAgentRecord[]) {
    if (items.length === 0) {
      setAgentAnalyticsById({});
      updateStudioPaneCache(workspaceId, { agentAnalyticsById: {} });
      return;
    }
    try {
      const payload = await services.client.listDeployedAgentAnalytics();
      const analyticsItems = readItems<DeployedAgentAnalyticsRecord>(payload);
      const nextEntries = analyticsItems
        .map((item) => {
          const deployedAgentId = readString(item.deployed_agent_id);
          const analytics = normalizeAgentAnalytics(item);
          if (!deployedAgentId || !analytics) {
            return null;
          }
          return [deployedAgentId, analytics] as const;
        })
        .filter((entry): entry is readonly [string, AgentAnalyticsSnapshot] => Boolean(entry));
      setAgentAnalyticsById((current) => {
        const nextValue = {
          ...current,
          ...Object.fromEntries(nextEntries),
        };
        updateStudioPaneCache(workspaceId, { agentAnalyticsById: nextValue });
        return nextValue;
      });
    } catch {
      setAgentAnalyticsById((current) => current);
    }
  }

  async function refreshProviderCatalog() {
    setIsLoadingProviderCatalog(true);
    try {
      const payload = await services.client.listProviderCatalog();
      const nextCatalog = normalizeProviderCatalog(payload);
      updateStudioPaneCache(workspaceId, { providerCatalog: nextCatalog });
      setProviderCatalog(nextCatalog);
    } catch (error) {
      setProviderCatalog([]);
      setErrorMessage(error instanceof Error ? error.message : 'AI model catalog is unavailable.');
    } finally {
      setIsLoadingProviderCatalog(false);
    }
  }

  async function refreshProviderModels(providerId: string) {
    const normalizedProviderId = readString(providerId);
    if (!normalizedProviderId) {
      return;
    }
    setIsLoadingProviderCatalog(true);
    try {
      await services.client.refreshWorkspaceProviderModels({ providerId: normalizedProviderId });
      const payload = await services.client.listProviderCatalog();
      const nextCatalog = normalizeProviderCatalog(payload);
      updateStudioPaneCache(workspaceId, { providerCatalog: nextCatalog });
      setProviderCatalog(nextCatalog);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not refresh provider models.');
    } finally {
      setIsLoadingProviderCatalog(false);
    }
  }

  async function loadRuntimeAttachments() {
    setIsLoadingRuntimeAttachments(true);
    try {
      const payload = await services.client.listRuntimeAttachments();
      const nextAttachments = normalizeRuntimeAttachments(payload);
      setRuntimeAttachments(nextAttachments);
      updateStudioPaneCache(workspaceId, { runtimeAttachments: nextAttachments });
    } catch (error) {
      setRuntimeAttachments([]);
      updateStudioPaneCache(workspaceId, { runtimeAttachments: [] });
      setErrorMessage(error instanceof Error ? error.message : 'Server/VPS inventory is unavailable.');
    } finally {
      setIsLoadingRuntimeAttachments(false);
    }
  }

  async function refreshConnectedExternalAgents() {
    try {
      const payload = await services.client.listStudioAgentSurfaces();
      setExternalAgentSurfaceError(null);
      const nextExternalAgents = Array.isArray(payload?.connected_external_agents)
        ? payload.connected_external_agents.filter((item): item is ConnectedExternalAgentRecord => Boolean(item) && typeof item === 'object')
          .filter(isConnectedExternalAgentActive)
        : [];
      const nextSurfaceAgentComputers = normalizeSurfaceAgentComputers(payload);
      setConnectedExternalAgents(nextExternalAgents);
      if (nextSurfaceAgentComputers.length > 0) {
        setRuntimeAttachments((current) => {
          const merged = [...current];
          for (const computer of nextSurfaceAgentComputers) {
            if (!merged.some((item) => {
              const currentIds = runtimeAttachmentIdCandidates(item);
              return Array.from(runtimeAttachmentIdCandidates(computer)).some((id) => currentIds.has(id));
            })) {
              merged.push(computer);
            }
          }
          updateStudioPaneCache(workspaceId, { runtimeAttachments: merged });
          return merged;
        });
      }
      updateStudioPaneCache(workspaceId, { connectedExternalAgents: nextExternalAgents });
      setSelectedExternalAgentId((current) => {
        if (!current || nextExternalAgents.some((item) => readString(item.id) === current)) {
          return current;
        }
        return null;
      });
    } catch (error) {
      setExternalAgentSurfaceError(error instanceof Error ? error.message : 'Studio agent surface contract did not load.');
      try {
        const payload = await services.client.listConnectedExternalAgents();
        const fallbackAgents = readItems<ConnectedExternalAgentRecord>(payload).filter(isConnectedExternalAgentActive);
        setConnectedExternalAgents(fallbackAgents);
        updateStudioPaneCache(workspaceId, { connectedExternalAgents: fallbackAgents });
      } catch (fallbackError) {
        setExternalAgentSurfaceError(fallbackError instanceof Error ? fallbackError.message : 'Connected-agent contract did not load.');
        setConnectedExternalAgents([]);
        updateStudioPaneCache(workspaceId, { connectedExternalAgents: [] });
      }
    }
  }

  async function refreshStudioTemplates() {
    try {
      const payload = await services.client.listStudioTemplates();
      const nextTemplates = normalizeStudioTemplates(payload);
      setStudioTemplates(nextTemplates);
      setSelectedTemplateId((current) => (
        current === CUSTOM_STUDIO_TEMPLATE.id || nextTemplates.some((template) => template.id === current)
          ? current
          : nextTemplates[0]?.id ?? DEFAULT_STUDIO_TEMPLATE.id
      ));
    } catch {
      setStudioTemplates([...STUDIO_TEMPLATES]);
    }
  }

  async function refreshAgents(options: { preserveSelection?: boolean; selectAgentId?: string | null } = {}) {
    setIsLoadingAgents(true);
    setErrorMessage(null);
    try {
      const payload = await services.client.listDeployedAgents();
      const items = readItems<DeployedAgentRecord>(payload);
      setAgents(items);
      setHasLoadedAgentListOnce(true);
      void refreshAgentAnalytics(items);
      setAgentMetricsById((current) => {
        const nextValue = {
          ...current,
          ...Object.fromEntries(
            items
              .map((item) => readString(item.id))
              .filter(Boolean)
              .map((agentId) => [agentId, current[agentId] ?? buildMetricsPlaceholder()]),
          ),
        };
        updateStudioPaneCache(workspaceId, { agents: items, agentMetricsById: nextValue });
        return nextValue;
      });
      const explicitSelection = readString(options.selectAgentId);
      if (explicitSelection) {
        setSelectedAgentId(explicitSelection);
      } else if (!options.preserveSelection) {
        setSelectedAgentId(null);
      } else if (selectedAgentId && !items.some((item) => readString(item.id) === selectedAgentId)) {
        setSelectedAgentId(null);
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Deployed agents could not be loaded.');
    } finally {
      setIsLoadingAgents(false);
    }
  }

  async function loadAgentDetail(agentId: string) {
    setIsLoadingDetail(true);
    try {
      const payload = await services.client.getDeployedAgent({
        deployedAgentId: agentId,
        allowMissing: true,
      });
      const nextDetail = payload as DeployedAgentRecord | null;
      setSelectedAgentDetail(nextDetail);
      updateStudioPaneCache(workspaceId, { selectedAgentDetail: nextDetail });
    } catch (error) {
      setSelectedAgentDetail(null);
      updateStudioPaneCache(workspaceId, { selectedAgentDetail: null });
      setErrorMessage(error instanceof Error ? error.message : 'Deployment detail is unavailable.');
    } finally {
      setIsLoadingDetail(false);
    }
  }

  async function loadAgentAnalytics(agentId: string) {
    setIsLoadingAnalytics(true);
    try {
      const payload = await services.client.getDeployedAgentAnalytics({
        deployedAgentId: agentId,
        allowMissing: true,
      });
      const analytics = normalizeAgentAnalytics(payload as DeployedAgentAnalyticsRecord | null);
      setSelectedAgentAnalytics(analytics);
      updateStudioPaneCache(workspaceId, { selectedAgentAnalytics: analytics });
      if (analytics) {
        setAgentAnalyticsById((current) => ({
          ...current,
          [agentId]: analytics,
        }));
      }
    } catch {
      setSelectedAgentAnalytics(null);
      updateStudioPaneCache(workspaceId, { selectedAgentAnalytics: null });
    } finally {
      setIsLoadingAnalytics(false);
    }
  }

  async function loadTelegramReadiness(agentId?: string | null) {
    setIsLoadingTelegramReadiness(true);
    try {
      const payload = await services.client.getDeployedAgentTelegramReadiness({
        deployedAgentId: agentId || undefined,
        allowMissing: true,
      });
      const nextReadiness = normalizeTelegramReadiness(payload as DeployedAgentTelegramReadinessRecord | null);
      setSelectedTelegramReadiness(nextReadiness);
      updateStudioPaneCache(workspaceId, { selectedTelegramReadiness: nextReadiness });
    } catch {
      setSelectedTelegramReadiness(null);
      updateStudioPaneCache(workspaceId, { selectedTelegramReadiness: null });
    } finally {
      setIsLoadingTelegramReadiness(false);
    }
  }

  async function loadConversations(agentId: string) {
    setIsLoadingConversations(true);
    try {
      const payload = await services.client.listDeployedAgentConversations({
        deployedAgentId: agentId,
        limit: 50,
        offset: 0,
      });
      const items = readItems<DeployedAgentConversationRecord>(payload);
      setConversations(items);
      updateStudioPaneCache(workspaceId, { conversations: items });
      setAgentMetricsById((current) => {
        const nextValue = {
          ...current,
          [agentId]: summarizeConversationMetrics(items, {
            hasMore: readRecord(payload).has_more === true,
          }),
        };
        updateStudioPaneCache(workspaceId, { agentMetricsById: nextValue });
        return nextValue;
      });
      setSelectedSessionId((current) => {
        if (current && items.some((item) => readString(item.session_id) === current)) {
          return current;
        }
        return readString(items[0]?.session_id) || null;
      });
    } catch {
      setConversations([]);
      updateStudioPaneCache(workspaceId, { conversations: [] });
      setSelectedSessionId(null);
    } finally {
      setIsLoadingConversations(false);
    }
  }

  async function loadMemoryEntries(agentId: string) {
    setIsLoadingOverlayMemory(true);
    try {
      const payload = await services.client.listDeployedAgentMemory({
        deployedAgentId: agentId,
        limit: 50,
        offset: 0,
      });
      setAgentMemoryById((current) => ({
        ...current,
        [agentId]: readItems<DeployedAgentMemoryRecord>(payload),
      }));
    } catch {
      setAgentMemoryById((current) => ({
        ...current,
        [agentId]: [],
      }));
    } finally {
      setIsLoadingOverlayMemory(false);
    }
  }

  async function loadTranscript(agentId: string, sessionId: string) {
    setIsLoadingTranscript(true);
    try {
      const payload = await services.client.getDeployedAgentConversationDetail({
        deployedAgentId: agentId,
        sessionId,
        allowMissing: true,
      });
      const nextTranscript = payload as DeployedAgentConversationDetail | null;
      setSelectedTranscript(nextTranscript);
      updateStudioPaneCache(workspaceId, { selectedTranscript: nextTranscript });
    } catch {
      setSelectedTranscript(null);
      updateStudioPaneCache(workspaceId, { selectedTranscript: null });
    } finally {
      setIsLoadingTranscript(false);
    }
  }

  useEffect(() => {
    void refreshAgents();
    void refreshConnectedExternalAgents();
    void refreshStudioTemplates();
    void loadRuntimeAttachments();
    const handle = window.setTimeout(() => {
      void refreshProviderCatalog();
    }, hadInitialCache ? 120 : 260);
    return () => {
      window.clearTimeout(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hadInitialCache, services.client, workspaceId]);

  useEffect(() => {
    setCurrentStudioSubview(initialSubview);
  }, [initialSubview]);

  useEffect(() => {
    if (!requestedAgentId) {
      return;
    }
    if (!agents.some((item) => readString(item.id) === requestedAgentId)) {
      return;
    }
    const shouldStayInInbox = initialSubview === 'inbox';
    setCurrentStudioSubview(shouldStayInInbox ? 'inbox' : 'agents');
    setSelectedAgentId(requestedAgentId);
    setSelectedExternalAgentId(null);
    setSelectedAgentComputerId(null);
    setOverlayAgentId(null);
  }, [agents, initialSubview, requestedAgentId]);

  useEffect(() => {
    if (!requestedExternalAgentId) {
      return;
    }
    if (!connectedExternalAgents.some((item) => readString(item.id) === requestedExternalAgentId)) {
      return;
    }
    setCurrentStudioSubview('agents');
    setSelectedExternalAgentId(requestedExternalAgentId);
    setSelectedAgentId(null);
    setSelectedAgentDetail(null);
    setSelectedAgentComputerId(null);
    setOverlayAgentId(null);
    setOverlayTab('overview');
  }, [connectedExternalAgents, requestedExternalAgentId]);

  useEffect(() => {
    if (!requestedAgentComputerId) {
      return;
    }
    if (!runtimeAttachments.some((item) => runtimeAttachmentIdCandidates(item).has(requestedAgentComputerId))) {
      return;
    }
    setCurrentStudioSubview('agents');
    setSelectedAgentComputerId(requestedAgentComputerId);
    setSelectedAgentId(null);
    setSelectedAgentDetail(null);
    setSelectedExternalAgentId(null);
    setOverlayAgentId(null);
    setOverlayTab('overview');
  }, [requestedAgentComputerId, runtimeAttachments]);

  useEffect(() => {
    if (requestedAgentId || requestedExternalAgentId || requestedAgentComputerId) {
      return;
    }
    setSelectedAgentId(null);
    setSelectedExternalAgentId(null);
    setSelectedAgentComputerId(null);
    setSelectedAgentDetail(null);
    setSelectedAgentAnalytics(null);
    setSelectedTranscript(null);
    setOverlayAgentId(null);
    setOverlayTab('overview');
    updateStudioPaneCache(workspaceId, {
      selectedAgentId: null,
      selectedExternalAgentId: null,
      selectedAgentComputerId: null,
      selectedAgentDetail: null,
      selectedAgentAnalytics: null,
      selectedTranscript: null,
    });
  }, [requestedAgentComputerId, requestedAgentId, requestedExternalAgentId, workspaceId]);

  useEffect(() => {
    const normalizedRequestedTemplateId = normalizeTemplateToken(requestedProofAgentTemplateId);
    if (!normalizedRequestedTemplateId || handledTemplateDeepLinkRef.current === normalizedRequestedTemplateId) {
      return;
    }
    const template = studioTemplateById(normalizedRequestedTemplateId, studioTemplates);
    if (normalizeTemplateToken(template.id) !== normalizedRequestedTemplateId) {
      return;
    }
    handledTemplateDeepLinkRef.current = normalizedRequestedTemplateId;
    setCurrentStudioSubview('agents');
    openCreateWizard(template.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedProofAgentTemplateId, studioTemplates]);

  useEffect(() => {
    if (!requestedCreateAgent) {
      return;
    }
    const params = new URLSearchParams(searchParams.toString());
    params.delete('createAgent');
    const query = params.toString();
    const nextHref = query ? `${pathname}?${query}` : pathname;
    setCurrentStudioSubview('agents');
    openCreateWizard(CUSTOM_STUDIO_TEMPLATE.id);
    router.replace(nextHref, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedCreateAgent]);

  useEffect(() => {
    updateStudioPaneCache(workspaceId, {
      selectedAgentId,
      selectedExternalAgentId,
      selectedAgentComputerId,
      selectedSessionId,
    });
  }, [selectedAgentComputerId, selectedAgentId, selectedExternalAgentId, selectedSessionId, workspaceId]);

  useEffect(() => {
    const agentId = readString(selectedAgentId);
    if (!agentId) {
      setSelectedAgentDetail(null);
      setSelectedAgentAnalytics(null);
      setSelectedTelegramReadiness(null);
      setConversations([]);
      setConversationFilters({
        channel: 'all',
        escalationState: 'all',
        outcome: 'all',
      });
      setSelectedSessionId(null);
      setSelectedTranscript(null);
      updateStudioPaneCache(workspaceId, {
        selectedAgentId: null,
        selectedAgentDetail: null,
        selectedAgentAnalytics: null,
        selectedTelegramReadiness: null,
        conversations: [],
        selectedSessionId: null,
        selectedTranscript: null,
      });
      return;
    }
    const currentDetailAgentId = readString(selectedAgentDetail?.id);
    if (currentDetailAgentId && currentDetailAgentId !== agentId) {
      setSelectedAgentDetail(null);
      setSelectedAgentAnalytics(null);
      setSelectedTelegramReadiness(null);
      setConversations([]);
      setSelectedSessionId(null);
      setSelectedTranscript(null);
      updateStudioPaneCache(workspaceId, {
        selectedAgentDetail: null,
        selectedAgentAnalytics: null,
        selectedTelegramReadiness: null,
        conversations: [],
        selectedSessionId: null,
        selectedTranscript: null,
      });
    }
    setConversationFilters((current) => (
      current.channel === 'all' && current.escalationState === 'all' && current.outcome === 'all'
        ? current
        : {
          channel: 'all',
          escalationState: 'all',
          outcome: 'all',
        }
    ));
    let cancelled = false;
    let timeoutHandle: number | null = null;
    const frameHandle = window.requestAnimationFrame(() => {
      if (cancelled) {
        return;
      }
      void Promise.all([
        loadAgentDetail(agentId),
        loadConversations(agentId),
      ]);
      timeoutHandle = window.setTimeout(() => {
        if (cancelled) {
          return;
        }
        void Promise.all([
          loadAgentAnalytics(agentId),
          loadTelegramReadiness(agentId),
        ]);
      }, 140);
    });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(frameHandle);
      if (timeoutHandle !== null) {
        window.clearTimeout(timeoutHandle);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayAgentId, selectedAgentDetail?.id, selectedAgentId, workspaceId]);

  useEffect(() => {
    const agentId = readString(selectedAgentId);
    const sessionId = readString(selectedSessionId);
    if (!agentId || !sessionId) {
      setSelectedTranscript(null);
      updateStudioPaneCache(workspaceId, { selectedTranscript: null });
      return;
    }
    void loadTranscript(agentId, sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgentId, selectedSessionId]);

  useEffect(() => {
    if (filteredConversations.length === 0) {
      setSelectedSessionId(null);
      return;
    }
    if (selectedSessionId && filteredConversations.some((item) => readString(item.session_id) === selectedSessionId)) {
      return;
    }
    setSelectedSessionId(readString(filteredConversations[0]?.session_id) || null);
  }, [filteredConversations, selectedSessionId]);

  const overlayMemoryTargetAgentId = readString(overlayAgentId || selectedAgentId);
  useEffect(() => {
    const agentId = overlayMemoryTargetAgentId;
    if (!agentId || overlayTab !== 'memory') {
      return;
    }
    void loadMemoryEntries(agentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayMemoryTargetAgentId, overlayTab]);

  useEffect(() => {
    setDetailConfigDraft(buildDetailConfigDraft(selectedAgent));
  }, [selectedAgent]);

  useEffect(() => {
    if (!overlayAgentId) {
      return;
    }
    if (!agents.some((item) => readString(item.id) === overlayAgentId)) {
      setOverlayAgentId(null);
    }
  }, [agents, overlayAgentId]);

  useEffect(() => {
    let cancelled = false;
    void services.client.listConnectorsVault()
      .then((payload) => {
        if (cancelled) {
          return;
        }
        const connectorIds = new Set(
          readItems<Record<string, unknown>>(payload)
            .map((item) => readString(item.connector).toLowerCase())
            .filter(Boolean),
        );
        setConnectorVaultIds(connectorIds);
        updateStudioPaneCache(workspaceId, { connectorVaultIds: Array.from(connectorIds) });
      })
      .catch(() => {
        if (!cancelled) {
          setConnectorVaultIds(new Set());
          updateStudioPaneCache(workspaceId, { connectorVaultIds: [] });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [services.client]);

  function openCreateWizard(templateId: string = selectedTemplateId) {
    const template = studioTemplateById(templateId, studioTemplates);
    setWizardMode('create');
    setSelectedTemplateId(template.id);
    setIsWizardOpen(true);
  }

  function openEditWizard() {
    setWizardMode('edit');
    setIsWizardOpen(true);
  }

  function closeWizard() {
    setIsWizardOpen(false);
    if (selectedAgentId) {
      void loadTelegramReadiness(selectedAgentId);
    }
  }

  async function handleWizardSuccess(record: DeployedAgentRecord) {
    const recordId = readString(record.id);
    setAgents((current) => upsertAgentRecord(current, record));
    setSelectedAgentDetail(record);
    setSelectedAgentId(recordId || null);
    setIsWizardOpen(false);

    if (wizardMode === 'create') {
      setSelectedAgentAnalytics(null);
      setOverlayAgentId(null);
      if (recordId) {
        openSelectedAgentDetail(recordId);
      } else {
        selectOverlayTab('overview');
      }
      setStatusMessage(`Created agent ${studioAgentDisplayName(record)}.`);
      if (recordId) {
        await Promise.all([
          loadAgentAnalytics(recordId),
          loadTelegramReadiness(recordId),
          loadConversations(recordId),
        ]);
      }
    } else {
      await Promise.all([
        refreshAgentAnalytics(upsertAgentRecord(agents, record)),
        loadAgentAnalytics(recordId),
        loadTelegramReadiness(recordId),
      ]);
      setStatusMessage(`Updated ${studioAgentDisplayName(record, 'assistant')} settings.`);
    }
  }

  function handleWizardExternalSuccess(record: ConnectedExternalAgentRecord) {
    const recordId = readString(record.id);
    if (!recordId) {
      setStatusMessage('Connected external agent was added, but Studio did not receive an agent id.');
      return;
    }

    setConnectedExternalAgents((current) => {
      const exists = current.some((item) => readString(item.id) === recordId);
      return exists
        ? current.map((item) => readString(item.id) === recordId ? record : item)
        : [record, ...current];
    });
    setSelectedAgentId(null);
    setSelectedAgentDetail(null);
    setSelectedAgentAnalytics(null);
    setSelectedAgentComputerId(null);
    setSelectedExternalAgentId(recordId);
    setOverlayAgentId(null);
    setOverlayTab('overview');
    setIsWizardOpen(false);
    setCurrentStudioSubview('agents');
    setStatusMessage(`Connected external agent ${readString(record.name || record.label, 'external agent')}.`);
    replaceStudioQuery((params) => {
      params.set('externalAgent', recordId);
      params.delete('agent');
      params.delete('agentComputer');
      params.delete('studioFilter');
      params.delete('tab');
      params.delete('studioTab');
      params.delete('createAgent');
    });
    void refreshConnectedExternalAgents();
  }

  async function handleDeploymentAction(action: 'deploy' | 'pause') {
    const agentId = readString(selectedAgent?.id);
    if (!agentId) {
      return;
    }
    const selectedConfig = readRecord(selectedAgent?.config);
    const selectedMetadata = readRecord(selectedAgent?.metadata);
    const selectedCommercePolicy = readRecord(selectedConfig.commerce_policy);
    const selectedMonthlyCostCap = readPositiveDecimalString(
      selectedCommercePolicy.monthly_cost_cap_usd ?? selectedMetadata.monthly_cost_cap_usd,
    );
    const needsDeploySafeDefaults = action === 'deploy' && Boolean(selectedAgent) && (
      !readString(selectedAgent?.persona)
      || !readString(selectedAgent?.system_prompt)
      || !selectedMonthlyCostCap
    );
    if (action === 'deploy' && selectedAgentSelfHostedDeployBlocker) {
      setErrorMessage(selectedAgentSelfHostedDeployBlocker);
      return;
    }
    if (action === 'deploy' && selectedAgentModelDeployBlocker) {
      selectOverlayTab('ai');
      setErrorMessage(`${selectedAgentModelDeployBlocker} Open Model and choose the workspace default, an API key, a local model, or an enterprise gateway.`);
      return;
    }
    if (
      action === 'deploy'
      && hasEnabledChannel(selectedAgent?.channels, 'telegram')
      && !selectedTelegramReadiness
    ) {
      setErrorMessage('Telegram checks are still loading. Wait for readiness to finish before enabling that customer channel.');
      return;
    }
    if (
      action === 'deploy'
      && hasEnabledChannel(selectedAgent?.channels, 'telegram')
      && selectedTelegramReadiness
      && selectedTelegramReadiness.readyForLive !== true
    ) {
      const firstBlocker = selectedTelegramReadiness.blockers[0];
      const guidance = firstBlocker?.guidance || selectedTelegramReadiness.nextAction || 'Resolve the Telegram readiness blockers before enabling that customer channel.';
      setErrorMessage(firstBlocker?.message ? `${firstBlocker.message} ${guidance}` : guidance);
      return;
    }
    setBusyAgentId(agentId);
    setErrorMessage(null);
    try {
      if (needsDeploySafeDefaults) {
        const currentState = buildWizardState(selectedAgent);
        const safePersona = currentState.persona.trim() || DEFAULT_SAFE_AGENT_PERSONA;
        const safeSystemPrompt = currentState.systemPrompt.trim() || DEFAULT_SAFE_AGENT_SYSTEM_PROMPT;
        const route = resolveProviderModelForTier(currentState.aiTier, providerCatalog);
        const resolvedProviderId = route.providerId || currentState.providerId || selectedProviderId(selectedAgent) || null;
        const resolvedModelId = route.modelId || currentState.modelId || selectedModelId(selectedAgent) || null;
        const patched = await services.client.updateDeployedAgent({
          deployedAgentId: agentId,
          persona: safePersona,
          systemPrompt: safeSystemPrompt,
          provider: resolvedProviderId,
          model: resolvedModelId,
          config: buildDeploymentConfig({
            ...currentState,
            persona: safePersona,
            systemPrompt: safeSystemPrompt,
            providerId: resolvedProviderId || '',
            modelId: resolvedModelId || '',
          }),
        });
        if (patched) {
          const patchedRecord = patched as DeployedAgentRecord;
          setAgents((current) => upsertAgentRecord(current, patchedRecord));
          setSelectedAgentDetail(patchedRecord);
        }
      }
      const payload =
        action === 'deploy'
          ? await services.client.deployDeployedAgent({ deployedAgentId: agentId })
          : await services.client.pauseDeployedAgent({ deployedAgentId: agentId });
      const record = (payload ?? {}) as DeployedAgentRecord;
      setAgents((current) => upsertAgentRecord(current, record));
      setSelectedAgentDetail(record);
      setStatusMessage(
        action === 'deploy'
          ? listEnabledChannels(record.channels).length > 0
            ? `${studioAgentDisplayName(record, 'Assistant')} is now live on its configured channels.`
            : `${studioAgentDisplayName(record, 'Assistant')} is live with safe defaults. Connect a customer channel when ready.`
          : `${studioAgentDisplayName(record, 'Assistant')} is paused and will no longer reply to live customer messages.`,
      );
      await Promise.all([
        refreshAgentAnalytics(upsertAgentRecord(agents, record)),
        loadAgentAnalytics(agentId),
        loadTelegramReadiness(agentId),
      ]);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Deployment state could not be updated.');
    } finally {
      setBusyAgentId(null);
    }
  }

  async function saveDetailConfig() {
    const agentId = readString(selectedAgent?.id);
    if (!agentId || !detailConfigDraft) {
      return;
    }

    const currentState = buildWizardState(selectedAgent);
    const nextState: WizardState = {
      ...currentState,
      persona: detailConfigDraft.persona,
      systemPrompt: detailConfigDraft.systemPrompt,
      aiTier: detailConfigDraft.aiTier,
      aiSource: detailConfigDraft.aiSource,
      providerId: detailConfigDraft.providerId,
      modelId: detailConfigDraft.modelId,
      billingPlan: detailConfigDraft.billingPlan,
      selectedToolIds: detailConfigDraft.selectedToolIds,
      memoryEnabled: detailConfigDraft.memoryEnabled,
      contextBudgetPreset: detailConfigDraft.contextBudgetPreset,
      retentionPreset: detailConfigDraft.retentionPreset,
    };
    const resolvedProviderModel = resolveProviderModelForTier(nextState.aiTier, providerCatalog);
    const resolvedProviderId = nextState.providerId || resolvedProviderModel.providerId;
    const resolvedProvider = providerCatalogIndex[resolvedProviderId] ?? null;
    const resolvedModelId = nextState.modelId && resolvedProvider?.models.some((item) => item.id === nextState.modelId)
      ? nextState.modelId
      : resolvedProvider
        ? pickStudioModelForTier(resolvedProvider, nextState.aiTier)
        : resolvedProviderModel.modelId;
    const nextConfigPayload = buildDeploymentConfig(nextState);
    const currentConfig = readRecord(selectedAgent?.config);
    const currentMetadata = readRecord(selectedAgent?.metadata);
    const mergedConfig = {
      ...currentConfig,
      runtime_supply: readRecord(nextConfigPayload.runtime_supply),
      provider: resolvedProviderId || null,
      model: resolvedModelId || null,
      memory_policy: {
        ...readRecord(currentConfig.memory_policy),
        ...readRecord(nextConfigPayload.memory_policy),
      },
      tool_policy: {
        ...readRecord(currentConfig.tool_policy),
        ...readRecord(nextConfigPayload.tool_policy),
      },
    };
    const mergedMetadata = {
      ...currentMetadata,
      public_tier: nextState.aiTier,
      model_tier: nextState.aiTier,
      empyralis_model_tier: nextState.aiTier,
      billing_source: nextState.aiSource,
    };

    setIsSavingDetailConfig(true);
    setErrorMessage(null);
    try {
      const updated = await services.client.updateDeployedAgent({
        deployedAgentId: agentId,
        persona: nextState.persona.trim(),
        systemPrompt: nextState.systemPrompt.trim(),
        knowledgeSources: parseKnowledgeSources(nextState.knowledgeSourceText),
        provider: resolvedProviderId || null,
        model: resolvedModelId || null,
        billingPlan: nextState.billingPlan,
        config: mergedConfig,
        metadata: mergedMetadata,
      });
      const record = (updated ?? {}) as DeployedAgentRecord;
      setAgents((current) => upsertAgentRecord(current, record));
      setSelectedAgentDetail(record);
      setDetailConfigDraft(buildDetailConfigDraft(record));
      setStatusMessage(`Updated ${studioAgentDisplayName(record, 'assistant')} purpose, AI, actions, and memory settings.`);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'AI, actions, and memory settings could not be saved.');
    } finally {
      setIsSavingDetailConfig(false);
    }
  }

  function updateDetailAiTier(nextTier: WizardState['aiTier']) {
    setDetailConfigDraft((current) => {
      if (!current) {
        return current;
      }
      if (current.aiSource === 'empyralis_credits') {
        const route = resolveProviderModelForTier(nextTier, providerCatalog);
        return {
          ...current,
          aiTier: nextTier,
          providerId: route.providerId || current.providerId,
          modelId: route.modelId || current.modelId,
        };
      }
      const provider = providerCatalogIndex[current.providerId] ?? null;
      return {
        ...current,
        aiTier: nextTier,
        modelId: provider ? pickStudioModelForTier(provider, nextTier) : current.modelId,
      };
    });
  }

  function updateDetailProvider(nextProviderId: string) {
    const nextProvider = providerCatalogIndex[nextProviderId] ?? null;
    setDetailConfigDraft((current) => {
      if (!current) {
        return current;
      }
      const nextModelId = nextProvider
        ? pickStudioModelForTier(nextProvider, current.aiTier)
        : '';
      return {
        ...current,
        providerId: nextProviderId,
        modelId: nextModelId,
      };
    });
  }

  function updateDetailModel(nextModelId: string) {
    setDetailConfigDraft((current) => current ? {
      ...current,
      modelId: nextModelId,
      aiTier: inferAiTierFromProviderModel(current.providerId, nextModelId),
    } : current);
  }

  const studioTitle = currentStudioSubview === 'agents'
    ? 'Business Agents'
    : currentStudioSubview === 'inbox'
      ? 'Business Agent inbox'
      : 'Business Agent launch';
  const studioSubtitle = currentStudioSubview === 'agents'
    ? 'Workers Sage can run for customer, channel, and business workflows.'
    : currentStudioSubview === 'inbox'
      ? 'Customer sessions and handoffs for Business Agents already working.'
      : 'Go-live checks and spending guardrails for Business Agents.';
  const visibleErrorMessage = isWizardScopedError(errorMessage) ? null : summarizeStudioErrorMessage(errorMessage);
  const isRecoverableLoadTimeout = Boolean(visibleErrorMessage && /too long to respond|timed out/i.test(visibleErrorMessage));
  const isWorkspaceLoadAccessError =
    visibleErrorMessage === 'Business Agents cannot load that workspace data right now. Refresh, or check workspace access if it keeps happening.';
  const hasSelectedStudioObject = Boolean(selectedAgent || selectedExternalAgent || selectedAgentComputer);
  const selectedStudioObjectName = selectedAgent
    ? studioAgentDisplayName(selectedAgent, 'Selected agent')
    : selectedExternalAgent
      ? readString(selectedExternalAgent.name ?? selectedExternalAgent.label, 'Connected agent')
      : selectedAgentComputer
        ? readString(selectedAgentComputer.label, 'Agent Computer')
        : 'Selected object';
  const hasVisibleAgentWorkspace = Boolean(hasSelectedStudioObject || agents.length > 0 || connectedExternalAgents.length > 0 || runtimeAttachments.length > 0);
  const isAgentListUnavailable = Boolean(visibleErrorMessage && !isRecoverableLoadTimeout && !isLoadingAgents && agents.length === 0);
  const isAgentListPriming = agents.length === 0 && (
    isLoadingAgents
    || isRecoverableLoadTimeout
    || (!hasLoadedAgentListOnce && !isAgentListUnavailable)
  );
  const visibleGlobalErrorMessage =
    isRecoverableLoadTimeout || (isWorkspaceLoadAccessError && hasVisibleAgentWorkspace)
      ? null
      : visibleErrorMessage;
  const hasGatewayOnlineTarget = useMemo(
    () => bootstrap.runtime.runtimeTargets.some((target) => target.id === 'local_companion' && target.online),
    [bootstrap.runtime.runtimeTargets],
  );
  const hasCloudComputerAvailableTarget = useMemo(
    () => bootstrap.runtime.runtimeTargets.some((target) => target.id === 'sage_cloud_computer' && target.available),
    [bootstrap.runtime.runtimeTargets],
  );

  useEffect(() => {
    updateStudioPaneCache(workspaceId, {
      agents,
      connectedExternalAgents,
      runtimeAttachments,
      agentMetricsById,
      selectedAgentId,
      selectedExternalAgentId,
      selectedAgentComputerId,
      isAgentListPriming,
      isAgentListUnavailable,
    });
  }, [
    agentMetricsById,
    agents,
    connectedExternalAgents,
    isAgentListPriming,
    isAgentListUnavailable,
    runtimeAttachments,
    selectedAgentComputerId,
    selectedAgentId,
    selectedExternalAgentId,
    workspaceId,
  ]);

  async function handleDeleteExternalUserData() {
    const agentId = readString(selectedAgent?.id);
    const selectedTranscriptCustomer = readRecord(selectedTranscript?.customer);
    const selectedExternalUserId = readString(selectedTranscriptCustomer.id || readRecord(selectedConversation?.customer).id);
    const selectedExternalUserLabel = readString(
      selectedTranscriptCustomer.label || readRecord(selectedConversation?.customer).label,
      'this customer',
    );
    const channel = readString(selectedConversation?.channel || selectedTranscript?.channel).toLowerCase();
    if (!agentId || !selectedExternalUserId || !channel) {
      return;
    }
    const confirmed = window.confirm(
      `Delete saved conversation data for ${selectedExternalUserLabel} from this Business Agent? This removes message history, memory summaries, and usage records for that user.`,
    );
    if (!confirmed) {
      return;
    }
    setBusyExternalUserId(selectedExternalUserId);
    setErrorMessage(null);
    try {
      await services.client.deleteDeployedAgentExternalUserData({
        deployedAgentId: agentId,
        externalUserId: selectedExternalUserId,
        channel,
        sessionId: readString(selectedConversation?.session_id || selectedTranscript?.session_id) || undefined,
      });
      setSelectedSessionId(null);
      setSelectedTranscript(null);
      await Promise.all([
        loadConversations(agentId),
        loadAgentAnalytics(agentId),
        loadMemoryEntries(agentId),
      ]);
      setStatusMessage(`Deleted saved data for ${selectedExternalUserLabel} from ${studioAgentDisplayName(selectedAgent, 'this Business Agent')}.`);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Customer data could not be deleted.');
    } finally {
      setBusyExternalUserId(null);
    }
  }

  const handleRefreshAgentsEvent = useStableEvent(() => {
    void Promise.all([
      refreshProviderCatalog(),
      refreshAgents({ preserveSelection: true }),
      refreshConnectedExternalAgents(),
      loadRuntimeAttachments(),
    ]);
  });

  useEffect(() => {
    const handleRefreshRequest = (event: Event) => {
      const detail = (event as CustomEvent<{ workspaceId?: string }>).detail;
      if (detail?.workspaceId && detail.workspaceId !== workspaceId) {
        return;
      }
      handleRefreshAgentsEvent();
    };
    window.addEventListener(STUDIO_PANE_REFRESH_EVENT, handleRefreshRequest);
    return () => {
      window.removeEventListener(STUDIO_PANE_REFRESH_EVENT, handleRefreshRequest);
    };
  }, [handleRefreshAgentsEvent, workspaceId]);

  const handleExternalAgentUpdated = useStableEvent((record: ConnectedExternalAgentRecord) => {
    const recordId = readString(record.id);
    if (!recordId) {
      return;
    }
    if (!isConnectedExternalAgentActive(record)) {
      setConnectedExternalAgents((current) => current.filter((item) => readString(item.id) !== recordId));
      setSelectedExternalAgentId((current) => current === recordId ? null : current);
      replaceStudioQuery((params) => {
        if (params.get('externalAgent') === recordId) {
          params.delete('externalAgent');
          params.delete('tab');
        }
      });
      return;
    }
    setConnectedExternalAgents((current) => {
      const exists = current.some((item) => readString(item.id) === recordId);
      return exists
        ? current.map((item) => readString(item.id) === recordId ? record : item)
        : [record, ...current];
    });
  });

  const handleSaveDetailConfigEvent = useStableEvent(() => {
    void saveDetailConfig();
  });

  const handleUpdateDetailConfig = useStableEvent((next: Partial<DetailConfigDraft>) => {
    setDetailConfigDraft((current) => current ? { ...current, ...next } : current);
  });

  const handleUploadKnowledgeFile = useStableEvent(async (file: File) => {
    const agentId = readString(selectedAgentId);
    if (!agentId) {
      throw new Error('Select a Business Agent before uploading knowledge.');
    }
    const contentText = await file.text();
    const uploaded = await services.client.uploadDeployedAgentKnowledgeFile({
      deployedAgentId: agentId,
      fileName: file.name,
      contentText,
    });
    const uploadedRecord = readRecord(readRecord(uploaded).deployed_agent);
    if (!Object.keys(uploadedRecord).length) {
      throw new Error('Knowledge file uploaded, but the Business Agent record was not returned.');
    }
    const record = uploadedRecord as DeployedAgentRecord;
    setAgents((current) => upsertAgentRecord(current, record));
    setSelectedAgentDetail(record);
    setDetailConfigDraft(buildDetailConfigDraft(record));
    setStatusMessage(`Added ${file.name} to ${studioAgentDisplayName(record, 'Business Agent')} knowledge.`);
  });

  const handleRefreshProviderModelsEvent = useStableEvent((id: string) => {
    void refreshProviderModels(id);
  });

  const handleSaveProviderCredential = useStableEvent(async (providerId: string, apiKey: string) => {
    const normalizedProviderId = readString(providerId);
    const normalizedApiKey = readString(apiKey);
    if (!normalizedProviderId || !normalizedApiKey) {
      throw new Error('Select a provider and paste its API key first.');
    }
    setIsSavingProviderCredential(true);
    setErrorMessage(null);
    try {
      await services.client.upsertWorkspaceProviderCredential({
        provider: normalizedProviderId,
        apiKey: normalizedApiKey,
        baseUrl: null,
        model: null,
      });
      await refreshProviderModels(normalizedProviderId);
      const providerLabel = providerCatalogIndex[normalizedProviderId]?.label ?? normalizedProviderId;
      setStatusMessage(`${providerLabel} API key saved for this workspace.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'API key could not be saved.';
      setErrorMessage(message);
      throw new Error(message);
    } finally {
      setIsSavingProviderCredential(false);
    }
  });

  const handleDeploy = useStableEvent(() => {
    void handleDeploymentAction('deploy');
  });

  const handlePause = useStableEvent(() => {
    void handleDeploymentAction('pause');
  });

  const handleOpenCreateWizard = useStableEvent((templateId: string = selectedTemplateId) => {
    openCreateWizard(templateId);
  });

  const selectedTranscriptCustomer = readRecord(selectedTranscript?.customer);
  const selectedExternalUserId = readString(selectedTranscriptCustomer.id || readRecord(selectedConversation?.customer).id);
  const selectedExternalUserLabel = readString(
    selectedTranscriptCustomer.label || readRecord(selectedConversation?.customer).label,
    'this customer',
  );
  const overlayMemoryAgentId = readString(overlayAgentId || selectedAgentId);
  const overlayMemoryEntries = overlayMemoryAgentId
    ? agentMemoryById[overlayMemoryAgentId] ?? []
    : [];

  return (
    <WorkstationSurfaceRoot surface="deployed-agents">
      <ListDetailShell
        className={joinClassNames(
          'app-studio-shell',
          currentStudioSubview === 'agents' && 'app-studio-shell--agents',
        )}
        title={studioTitle}
        subtitle={studioSubtitle}
      >
        {visibleGlobalErrorMessage ? (
          <PlatformNotification
            tone="danger"
            title="Business Agents are having trouble loading"
            detail="Studio keeps successfully loaded agent data visible while retrying failed requests."
            onClose={() => setErrorMessage(null)}
          >
            {visibleGlobalErrorMessage}
          </PlatformNotification>
        ) : statusMessage ? (
          <PlatformNotification
            tone="success"
            title="Assistant updated"
            detail={statusMessage}
            onClose={() => setStatusMessage(null)}
          />
        ) : externalAgentSurfaceError ? (
          <PlatformNotification
            tone="warning"
            title="Connected-agent surface is degraded"
            detail="Native agents remain available, but connected agents and Agent Computers may be incomplete until the Studio contract loads."
            onClose={() => setExternalAgentSurfaceError(null)}
          >
            {externalAgentSurfaceError}
          </PlatformNotification>
        ) : null}

        <WorkstationSplitWorkbench
          ariaLabel="Agents"
          className={joinClassNames(
            'studio-agents-workbench',
            'studio-agents-workbench--shell-roster',
          )}
        >
          {(currentStudioSubview === 'agents' || currentStudioSubview === 'deploy') && !hasSelectedStudioObject ? (
            isAgentListPriming || isAgentListUnavailable ? (
              <div className="studio-agent-detail-empty" data-loading={isAgentListPriming} aria-label="Agent detail">
                {isAgentListPriming ? (
                  <div className="studio-agent-detail-loading-body app-stack-5">
                    <SkeletonBlock height="8rem" />
                    <div className="studio-agent-overview__grid">
                      <SkeletonBlock height="12rem" />
                      <SkeletonBlock height="12rem" />
                      <SkeletonBlock height="12rem" />
                      <SkeletonBlock height="12rem" />
                    </div>
                  </div>
                ) : isAgentListUnavailable ? (
                  <>
                    <strong>Agent list did not load</strong>
                    <span>Retry the Business Agent list.</span>
                    <AppButton type="button" tone="secondary" onClick={handleRefreshAgentsEvent}>
                      Retry
                    </AppButton>
                  </>
                ) : null}
              </div>
            ) : agents.length === 0 && connectedExternalAgents.length === 0 && runtimeAttachments.length === 0 ? (
              <StudioAgentStartPanel
                studioTemplates={studioTemplates}
                onOpenCreateWizard={handleOpenCreateWizard}
              />
            ) : (
              <div className="app-watermark">
                <StudioIcon className="app-watermark__icon" size={48} />
                <div className="app-watermark__text">Select a native agent, connected agent, or Agent Computer</div>
              </div>
            )
          ) : null}

          {(currentStudioSubview === 'agents' || currentStudioSubview === 'deploy') && selectedAgent && (
              <AgentDetailView
                selectedAgent={selectedAgent}
                overlayTab={overlayTab}
                detailConfigDraft={detailConfigDraft}
                onSaveDetailConfig={handleSaveDetailConfigEvent}
                isSavingDetailConfig={isSavingDetailConfig}
                onUpdateDetailConfig={handleUpdateDetailConfig}
                onUploadKnowledgeFile={handleUploadKnowledgeFile}
                providerCatalog={providerCatalog}
                isLoadingProviderCatalog={isLoadingProviderCatalog}
                onRefreshProviderModels={handleRefreshProviderModelsEvent}
                onSaveProviderCredential={handleSaveProviderCredential}
                isSavingProviderCredential={isSavingProviderCredential}
                workspaceId={workspaceId}
                services={services}
                bootstrap={bootstrap}
                runtimeAttachments={runtimeAttachments}
                selectedAgentAnalytics={selectedAnalytics}
                isLoadingAnalytics={isLoadingAnalytics}
                selectedAgentMetrics={selectedAgentMetrics}
                selectedTelegramReadiness={selectedTelegramReadiness}
                onOpenEditWizard={openEditWizard}
                hasGatewayOnlineTarget={hasGatewayOnlineTarget}
                hasCloudComputerAvailableTarget={hasCloudComputerAvailableTarget}
                isLoadingDetail={isLoadingDetail}
                overlayMemoryEntries={overlayMemoryEntries}
                isLoadingOverlayMemory={isLoadingOverlayMemory}
                currentStudioSubview={currentStudioSubview}
                onSelectTab={selectOverlayTab}
                connectorVaultIds={connectorVaultIds}
                selectedAgentModelDeployBlocker={selectedAgentModelDeployBlocker}
                selectedAgentSelfHostedDeployBlocker={selectedAgentSelfHostedDeployBlocker}
                selectedStudioTemplate={selectedStudioTemplate}
                onOpenCreateWizard={handleOpenCreateWizard}
                onDeploy={handleDeploy}
                onPause={handlePause}
                isDeploymentBusy={busyAgentId === selectedAgentId}
                testChatSession={selectedAgentTestChatSession}
                onTestChatSessionChange={handleSelectedAgentTestChatSessionChange}
                onResetTestChatSession={handleResetSelectedAgentTestChatSession}
              />
          )}

          {(currentStudioSubview === 'agents' || currentStudioSubview === 'deploy') && selectedExternalAgent && (
              <ConnectedExternalAgentDetailView
                externalAgent={selectedExternalAgent}
                overlayTab={overlayTab}
                onSelectTab={selectOverlayTab}
                services={services}
                chatSession={selectedExternalAgentChatSession}
                onChatSessionChange={handleSelectedExternalAgentChatSessionChange}
                onExternalAgentUpdated={handleExternalAgentUpdated}
              />
          )}

          {(currentStudioSubview === 'agents' || currentStudioSubview === 'deploy') && selectedAgentComputer && (
              <AgentComputerDetailView computer={selectedAgentComputer} />
          )}

          {currentStudioSubview === 'inbox' && (
            <AgentInboxView
              selectedAgent={selectedAgent}
              conversations={conversations}
              isLoadingConversations={isLoadingConversations}
              conversationFilters={conversationFilters}
              onUpdateFilters={(next) => setConversationFilters((current) => ({ ...current, ...next }))}
              selectedSessionId={selectedSessionId}
              onSelectSession={setSelectedSessionId}
              selectedConversation={selectedConversation}
              isLoadingTranscript={isLoadingTranscript}
              selectedTranscript={selectedTranscript}
              onDeleteCustomerData={() => { void handleDeleteExternalUserData(); }}
              busyExternalUserId={busyExternalUserId}
              selectedExternalUserId={selectedExternalUserId}
              selectedExternalUserLabel={selectedExternalUserLabel}
              channelFilterOptions={channelFilterOptions}
              escalationFilterOptions={escalationFilterOptions}
              outcomeFilterOptions={outcomeFilterOptions}
              filteredConversations={filteredConversations}
            />
          )}
        </WorkstationSplitWorkbench>

        <AgentWizard
          open={isWizardOpen}
          mode={wizardMode}
          onClose={closeWizard}
          templateId={selectedTemplateId}
          onSuccess={(record) => { void handleWizardSuccess(record); }}
          onExternalSuccess={handleWizardExternalSuccess}
          workspaceId={workspaceId}
          bootstrap={bootstrap}
          services={services}
          providerCatalog={providerCatalog}
          runtimeAttachments={runtimeAttachments}
          isLoadingProviderCatalog={isLoadingProviderCatalog}
          isLoadingRuntimeAttachments={isLoadingRuntimeAttachments}
          selectedAgent={selectedAgent}
        />
      </ListDetailShell>
    </WorkstationSurfaceRoot>
  );
}
