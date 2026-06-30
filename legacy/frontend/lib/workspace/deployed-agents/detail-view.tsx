'use client';

import { memo, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react';

import { ListDetailPanel } from '@/lib/ui/list-detail';
import { AnimatePresence, MotionTabPanel } from '@/lib/ui/motion';
import { AppButton, AppTextarea, joinClassNames } from '@/lib/ui/primitives';
import { EmptyPanel } from '@/lib/ui/empty-panel';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { StateBanner } from '@/lib/ui/state-banner';
import { DataBadge } from '@/lib/ui/data-table';
import type {
  DeployedAgentMemoryRecord,
  DeployedAgentRecord,
} from '@/lib/workspace/workstation-client';
import { WorkstationDeployedAgentAnalyticsPane } from '@/lib/workspace/workstation-deployed-agent-analytics-pane';
import type {
  AgentAnalyticsSnapshot,
  AgentOperationalMetrics,
  DetailConfigDraft,
  ProviderCatalogSnapshot,
  RuntimeAttachmentSnapshot,
  SpecialistOverlayTabId,
  TelegramReadinessSnapshot,
} from './types';
import {
  DEFAULT_SAFE_AGENT_PERSONA_VALUES,
  DEFAULT_SAFE_AGENT_SYSTEM_PROMPT_VALUES,
  SPECIALIST_CONNECTOR_CARDS,
} from './constants';
import {
  ContextPresetControl,
  RetentionPresetControl,
} from './components';
import { AgentAiSettingsSections } from './ai-settings';
import { AgentActionCapabilitySections } from './action-settings';
import { AgentIntegrationsSections } from './integration-settings';
import { AgentPlaygroundPanel } from './playground-panel';
import {
  type DeployedAgentTestChatSessionState,
} from '@/lib/workspace/workstation-deployed-agent-test-turn-pane';
import {
  readString,
  readRecord,
  readNumber,
  humanizeToken,
  formatCompactCount,
  formatUsd,
  normalizeToolIds,
  deploymentStateLabel,
  rosterStatusTone,
  testRuntimeModeForPlacement,
  formatDeploymentModelLabel,
  selfHostedNodeHealthLabel,
  truncateExternalUserId,
  formatTimestamp,
  selectedModelId,
  selectedProviderId,
  listEnabledChannels,
  readBudgetCycle,
  inferAiTierFromProviderModel,
  connectorConnected,
  providerCatalogById,
  studioAgentDisplayName,
} from './utils';

const SUPPORTED_KNOWLEDGE_FILE_EXTENSIONS = ['.md', '.markdown', '.txt', '.csv', '.json'];
const KNOWLEDGE_UPLOAD_MAX_BYTES = 2 * 1024 * 1024;
const KNOWLEDGE_UPLOAD_MAX_MB = '2 MB';
const OVERVIEW_RANGE_OPTIONS = ['7d', '30d', '90d'] as const;
type OverviewRange = typeof OVERVIEW_RANGE_OPTIONS[number];

function isSupportedKnowledgeFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return SUPPORTED_KNOWLEDGE_FILE_EXTENSIONS.some((extension) => name.endsWith(extension));
}

function knowledgeSourceLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

export interface AgentDetailViewProps {
  selectedAgent: DeployedAgentRecord | null;
  overlayTab: SpecialistOverlayTabId;
  detailConfigDraft: DetailConfigDraft | null;
  onSaveDetailConfig: () => void;
  isSavingDetailConfig: boolean;
  onUpdateDetailConfig: (next: Partial<DetailConfigDraft>) => void;
  onUploadKnowledgeFile: (file: File) => Promise<void>;
  providerCatalog: ProviderCatalogSnapshot[];
  isLoadingProviderCatalog: boolean;
  onRefreshProviderModels: (providerId: string) => void;
  onSaveProviderCredential: (providerId: string, apiKey: string) => Promise<void>;
  isSavingProviderCredential: boolean;
  workspaceId: string;
  services: any;
  bootstrap: any;
  runtimeAttachments: RuntimeAttachmentSnapshot[];
  selectedAgentAnalytics: AgentAnalyticsSnapshot | null;
  isLoadingAnalytics: boolean;
  selectedAgentMetrics: AgentOperationalMetrics | null;
  selectedTelegramReadiness: TelegramReadinessSnapshot | null;
  onOpenEditWizard: () => void;
  hasGatewayOnlineTarget: boolean;
  hasCloudComputerAvailableTarget: boolean;
  isLoadingDetail: boolean;
  overlayMemoryEntries: DeployedAgentMemoryRecord[];
  isLoadingOverlayMemory: boolean;
  currentStudioSubview: 'agents' | 'inbox' | 'deploy';
  onSelectTab: (tabId: SpecialistOverlayTabId) => void;
  connectorVaultIds: Set<string>;
  selectedAgentModelDeployBlocker: string | null;
  selectedAgentSelfHostedDeployBlocker: string | null;
  selectedStudioTemplate: any;
  onOpenCreateWizard: (templateId: string) => void;
  onDeploy: () => void;
  onPause: () => void;
  isDeploymentBusy: boolean;
  testChatSession: DeployedAgentTestChatSessionState;
  onTestChatSessionChange: Dispatch<SetStateAction<DeployedAgentTestChatSessionState>>;
  onResetTestChatSession: () => void;
}

export const AgentDetailView = memo(({
  selectedAgent,
  overlayTab,
  detailConfigDraft,
  onSaveDetailConfig,
  isSavingDetailConfig,
  onUpdateDetailConfig,
  onUploadKnowledgeFile,
  providerCatalog,
  isLoadingProviderCatalog,
  onRefreshProviderModels,
  onSaveProviderCredential,
  isSavingProviderCredential: isSavingProviderCredentialProp,
  workspaceId,
  services,
  bootstrap,
  runtimeAttachments,
  selectedAgentAnalytics,
  isLoadingAnalytics,
  selectedAgentMetrics,
  selectedTelegramReadiness,
  onOpenEditWizard,
  hasGatewayOnlineTarget,
  hasCloudComputerAvailableTarget,
  isLoadingDetail,
  overlayMemoryEntries,
  isLoadingOverlayMemory,
  currentStudioSubview,
  onSelectTab,
  connectorVaultIds,
  selectedAgentModelDeployBlocker,
  selectedAgentSelfHostedDeployBlocker,
  selectedStudioTemplate,
  onOpenCreateWizard,
  onDeploy,
  onPause,
  isDeploymentBusy,
  testChatSession,
  onTestChatSessionChange,
  onResetTestChatSession,
}: AgentDetailViewProps) => {
  const selectedAgentId = readString(selectedAgent?.id);
  const [knowledgeSourceInput, setKnowledgeSourceInput] = useState('');
  const [knowledgeFileError, setKnowledgeFileError] = useState<string | null>(null);
  const [isUploadingKnowledgeFile, setIsUploadingKnowledgeFile] = useState(false);
  const [overviewRange, setOverviewRange] = useState<OverviewRange>('30d');
  const knowledgeFileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setKnowledgeSourceInput('');
    setKnowledgeFileError(null);
    setIsUploadingKnowledgeFile(false);
  }, [workspaceId, selectedAgentId]);

  const selectedAgentRuntimePlacement = useMemo(() => {
    const config = readRecord(selectedAgent?.config);
    const metadata = readRecord(selectedAgent?.metadata);
    return (
      config.runtime_placement ??
      metadata.runtime_placement ??
      selectedAgent?.runtime_target
    );
  }, [selectedAgent]);

  const selectedKnowledgeSources = Array.isArray(selectedAgent?.knowledge_sources)
    ? selectedAgent.knowledge_sources
    : [];
  const knowledgeSourceCount = selectedKnowledgeSources.length;
  const draftKnowledgeSourceLines = detailConfigDraft
    ? knowledgeSourceLines(detailConfigDraft.knowledgeSourceText)
    : [];
  const visibleKnowledgeSources = detailConfigDraft
    ? draftKnowledgeSourceLines.map((uri, index) => ({ id: `draft-source-${index + 1}`, uri, label: uri, kind: uri.startsWith('knowledge://') ? 'file' : 'source' }))
    : selectedKnowledgeSources;
  const visibleKnowledgeSourceCount = visibleKnowledgeSources.length;

  const selectedAgentMemoryEnabled =
    readRecord(readRecord(selectedAgent?.config).memory_policy).memory_enabled === true ||
    readRecord(selectedAgent?.metadata).memory_enabled === true;
  const selectedAgentConfig = readRecord(selectedAgent?.config);
  const selectedAgentMetadata = readRecord(selectedAgent?.metadata);
  const selectedAgentInstructionsText = readString(
    selectedAgent?.system_prompt ??
    selectedAgentConfig.system_prompt ??
    selectedAgentConfig.instructions ??
    selectedAgentMetadata.system_prompt ??
    selectedAgentMetadata.instructions,
  );
  const selectedAgentPersonaText = readString(
    selectedAgent?.persona ??
    selectedAgentConfig.persona ??
    selectedAgentMetadata.persona,
  );
  const selectedAgentInstructionsAreDefault = DEFAULT_SAFE_AGENT_SYSTEM_PROMPT_VALUES.includes(selectedAgentInstructionsText);
  const selectedAgentPersonaIsDefault = DEFAULT_SAFE_AGENT_PERSONA_VALUES.includes(selectedAgentPersonaText);
  const visibleAgentInstructionsText =
    selectedAgentInstructionsAreDefault ? '' : selectedAgentInstructionsText;
  const visibleAgentPersonaText =
    selectedAgentPersonaIsDefault ? '' : selectedAgentPersonaText;
  const detailInstructionsValue = detailConfigDraft
    ? (DEFAULT_SAFE_AGENT_SYSTEM_PROMPT_VALUES.includes(detailConfigDraft.systemPrompt) ? '' : detailConfigDraft.systemPrompt)
    : visibleAgentInstructionsText;
  const detailPersonaValue = detailConfigDraft
    ? (DEFAULT_SAFE_AGENT_PERSONA_VALUES.includes(detailConfigDraft.persona) ? '' : detailConfigDraft.persona)
    : visibleAgentPersonaText;

  const selectedBudgetCycle = readBudgetCycle(selectedAgent);
  const selectedBudgetBurn = selectedAgentAnalytics?.currentBurnUsd ?? readNumber(selectedBudgetCycle.current_burn_usd);

  const overlayConnectorCards = useMemo(() => {
    if (!selectedAgent) return [];
    return SPECIALIST_CONNECTOR_CARDS.map((connector) => {
      const telegramBinding = readRecord(selectedTelegramReadiness?.configuredBinding);
      const telegramConnected = connector.id === 'telegram'
        ? Boolean(readString(telegramBinding.connector_id) || readString(telegramBinding.label))
        : false;
      const connected = connector.id === 'telegram'
        ? telegramConnected
        : connectorConnected(connector.connectorIds, connectorVaultIds);
      const statusLabel = connector.id === 'telegram'
        ? (selectedTelegramReadiness?.readyForLive ? 'Connected' : connected ? 'Needs attention' : 'Not connected')
        : connected ? 'Connected' : 'Not connected';
      return {
        ...connector,
        connected,
        statusLabel,
      };
    });
  }, [selectedAgent, selectedTelegramReadiness, connectorVaultIds]);

  const selectedAgentNeedsTelegramReadiness = useMemo(() => {
    const channels = readRecord(selectedAgent?.channels);
    return Boolean(readRecord(channels.telegram).enabled);
  }, [selectedAgent]);
  const selectedAgentChannelLabel = useMemo(() => {
    const channels = listEnabledChannels(selectedAgent?.channels);
    return channels.length > 0
      ? channels.map((channel) => humanizeToken(channel, channel)).join(', ')
      : 'No active channels';
  }, [selectedAgent]);
  const providerCatalogIndex = useMemo(
    () => providerCatalogById(providerCatalog),
    [providerCatalog],
  );
  const selectedAgentState = readString(selectedAgent?.deployment_state).toLowerCase();
  const selectedAgentStateLabel = humanizeToken(selectedAgent?.deployment_state, 'Draft');
  const selectedAgentDisplayName = studioAgentDisplayName(selectedAgent);
  const selectedAgentModelLabel = formatDeploymentModelLabel(selectedAgent, providerCatalogIndex);
  const selectedAgentToolCount = normalizeToolIds(
    readRecord(readRecord(selectedAgent?.config).tool_policy).enabled_tools ??
    readRecord(selectedAgent?.metadata).selected_tool_ids,
  ).length;
  const modelReady = !selectedAgentModelDeployBlocker && Boolean(selectedModelId(selectedAgent) || selectedProviderId(selectedAgent));
  const instructionsReady = Boolean(
    readString(readRecord(selectedAgent?.config).instructions).trim() ||
    readString(readRecord(selectedAgent?.metadata).instructions).trim() ||
    readString(readRecord(selectedAgent?.config).system_prompt).trim() ||
    readString(readRecord(selectedAgent?.metadata).system_prompt).trim(),
  );
  const channelReady = listEnabledChannels(selectedAgent?.channels).length > 0;
  const runtimeReady = !selectedAgentSelfHostedDeployBlocker;
  const telegramReady = !selectedAgentNeedsTelegramReadiness || selectedTelegramReadiness?.readyForLive === true;
  const deployBlockers = [
    selectedAgentModelDeployBlocker,
    selectedAgentSelfHostedDeployBlocker,
    selectedAgentNeedsTelegramReadiness && selectedTelegramReadiness?.readyForLive !== true
      ? selectedTelegramReadiness?.nextAction || 'Finish channel setup before launch.'
      : null,
  ].filter((item): item is string => Boolean(item));
  const knowledgeReady = knowledgeSourceCount > 0;
  const launchReady = deployBlockers.length === 0 && modelReady && instructionsReady && knowledgeReady && channelReady && runtimeReady && telegramReady;
  const readinessItems = [
    {
      label: 'Model',
      detail: modelReady ? selectedAgentModelLabel : selectedAgentModelDeployBlocker || 'Connect a model provider.',
      ready: modelReady,
      tab: 'ai' as SpecialistOverlayTabId,
    },
    {
      label: 'Instructions',
      detail: instructionsReady ? 'Behavior document is present.' : 'Add how this agent should answer.',
      ready: instructionsReady,
      tab: 'knowledge' as SpecialistOverlayTabId,
    },
    {
      label: 'Knowledge',
      detail: knowledgeReady ? `${knowledgeSourceCount} trusted source${knowledgeSourceCount === 1 ? '' : 's'}.` : 'No trusted sources connected yet.',
      ready: knowledgeReady,
      tab: 'knowledge' as SpecialistOverlayTabId,
    },
    {
      label: 'Channel',
      detail: channelReady ? selectedAgentChannelLabel : 'No customer channel connected.',
      ready: channelReady && telegramReady,
      tab: 'channels' as SpecialistOverlayTabId,
    },
    {
      label: 'Actions',
      detail: selectedAgentToolCount > 0 ? `${selectedAgentToolCount} permission${selectedAgentToolCount === 1 ? '' : 's'} enabled.` : 'No action permissions enabled.',
      ready: selectedAgentToolCount > 0,
      tab: 'tools' as SpecialistOverlayTabId,
    },
    {
      label: 'Memory',
      detail: selectedAgentMemoryEnabled ? 'Customer memory is enabled.' : 'Customer memory is off.',
      ready: true,
      tab: 'memory' as SpecialistOverlayTabId,
    },
    {
      label: 'Workspace chat',
      detail: selectedAgentId ? 'Private owner chat is ready.' : 'Save the agent before chatting.',
      ready: Boolean(selectedAgentId),
      tab: 'chat' as SpecialistOverlayTabId,
    },
    {
      label: 'Deploy state',
      detail: selectedAgentState === 'live' ? 'Receiving customer traffic.' : launchReady ? 'Ready for go-live.' : 'Resolve setup blockers first.',
      ready: selectedAgentState === 'live' || launchReady,
      tab: 'overview' as SpecialistOverlayTabId,
    },
    {
      label: 'Usage',
      detail: `${formatUsd(selectedBudgetBurn)} used this month.`,
      ready: true,
      tab: 'analytics' as SpecialistOverlayTabId,
    },
  ];
  const launchDetail = selectedAgentState === 'live'
    ? 'This agent can receive customer traffic.'
    : launchReady
      ? 'Production-safe defaults are set. Chat with it privately, then deploy when ready.'
      : deployBlockers[0] || 'Finish the required setup before customer traffic.';
  const overviewConversationCount = selectedAgentMetrics?.conversationCount ?? 0;
  const overviewMessagesMonth = selectedAgentAnalytics?.messageVolumeMonth ?? 0;
  const overviewActiveUsers = selectedAgentAnalytics?.activeUsersLast30d ?? 0;
  const overviewHandoffCount = selectedAgentAnalytics?.escalationSessionCount ?? 0;
  const overviewHandoffRate = selectedAgentAnalytics
    ? `${Math.round(selectedAgentAnalytics.escalationRatePercent)}%`
    : '0%';
  const hasLoggedOutcomes = Boolean(selectedAgentAnalytics?.outcomes.length);
  const overviewTopOutcome = hasLoggedOutcomes && selectedAgentAnalytics?.topOutcome
    ? humanizeToken(selectedAgentAnalytics.topOutcome, 'Outcome')
    : 'No outcomes yet';
  const overviewLatestActivity =
    selectedAgentMetrics?.latestActivityLabel ??
    (selectedAgentAnalytics?.latestMessageAt ? `Latest ${formatTimestamp(selectedAgentAnalytics.latestMessageAt)}` : 'No activity yet');
  const overviewLatestChannel = selectedAgentMetrics?.latestChannel
    ? humanizeToken(selectedAgentMetrics.latestChannel, selectedAgentMetrics.latestChannel)
    : (channelReady ? selectedAgentChannelLabel : 'No channel yet');
  const overviewUsageMonthDate = selectedAgentAnalytics?.usageMonth
    ? Date.parse(`${selectedAgentAnalytics.usageMonth}T00:00:00Z`)
    : Number.NaN;
  const overviewUsageWindow = Number.isFinite(overviewUsageMonthDate)
    ? new Intl.DateTimeFormat(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(overviewUsageMonthDate))
    : 'This month';
  const overviewStatusDetail = selectedAgentState === 'live'
    ? 'Live traffic is enabled. Track work handled, handoffs, and spend.'
    : launchReady
      ? 'Ready for customer traffic. Deployment stays owner controlled.'
      : launchDetail;
  const overviewMetrics = [
    { label: 'People 30d', value: formatCompactCount(overviewActiveUsers) },
    { label: 'Conversations', value: formatCompactCount(overviewConversationCount) },
    { label: 'Messages 30d', value: formatCompactCount(overviewMessagesMonth) },
    { label: 'Cost', value: formatUsd(selectedBudgetBurn ?? 0) },
  ];
  const overviewSetupChecklist = readinessItems.filter((item) => (
    item.label === 'Instructions' ||
    item.label === 'Knowledge' ||
    item.label === 'Channel' ||
    item.label === 'Actions'
  ));
  const overviewRangeDays = overviewRange === '7d' ? 7 : overviewRange === '90d' ? 90 : 30;
  const overviewAllSeries = selectedAgentAnalytics?.activitySeries ?? [];
  const overviewDatedSeries = overviewAllSeries.filter((item) => (
    item.date && Number.isFinite(Date.parse(item.date))
  ));
  const overviewRangeCutoff = Date.now() - ((overviewRangeDays - 1) * 24 * 60 * 60 * 1000);
  const overviewChartSeries = overviewDatedSeries.length > 0
    ? overviewAllSeries.filter((item) => {
        if (!item.date) return false;
        const timestamp = Date.parse(item.date);
        return Number.isFinite(timestamp) && timestamp >= overviewRangeCutoff;
      })
    : overviewAllSeries.slice(-overviewRangeDays);
  const overviewChartMax = Math.max(
    1,
    ...overviewChartSeries.flatMap((item) => [item.conversations, item.messages]),
  );
  const buildOverviewPolyline = (metric: 'conversations' | 'messages') => {
    if (overviewChartSeries.length === 0) {
      return '';
    }
    const lastIndex = overviewChartSeries.length - 1;
    return overviewChartSeries
      .map((item, index) => {
        const x = lastIndex === 0 ? 50 : 4 + ((index / lastIndex) * 92);
        const amount = Math.max(0, item[metric]);
        const y = 78 - ((amount / overviewChartMax) * 58);
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(' ');
  };
  const overviewConversationPolyline = buildOverviewPolyline('conversations');
  const overviewMessagePolyline = buildOverviewPolyline('messages');
  const overviewChartStartLabel = overviewChartSeries[0]?.label || `Last ${overviewRange}`;
  const overviewChartEndLabel = overviewChartSeries[overviewChartSeries.length - 1]?.label || 'Today';
  const overviewWorkSignals = [
    {
      label: 'Last activity',
      value: overviewLatestActivity,
    },
    {
      label: 'Outcome',
      value: overviewTopOutcome,
    },
    {
      label: 'Handoffs',
      value: `${formatCompactCount(overviewHandoffCount)} / ${overviewHandoffRate}`,
    },
    {
      label: 'Channel',
      value: overviewLatestChannel,
    },
  ];
  const addKnowledgeReferenceToDraft = (reference: string) => {
    const cleanReference = reference.trim();
    if (!cleanReference || !detailConfigDraft) {
      return;
    }
    const currentLines = knowledgeSourceLines(detailConfigDraft.knowledgeSourceText);
    if (currentLines.includes(cleanReference)) {
      setKnowledgeSourceInput('');
      return;
    }
    onUpdateDetailConfig({
      knowledgeSourceText: [...currentLines, cleanReference].join('\n'),
    });
    setKnowledgeSourceInput('');
  };
  const handleKnowledgeReferenceSubmit = () => {
    addKnowledgeReferenceToDraft(knowledgeSourceInput);
  };
  const handleKnowledgeFiles = async (files: FileList | File[]) => {
    const nextFiles = Array.from(files);
    if (!nextFiles.length) {
      return;
    }
    if (!detailConfigDraft) {
      setKnowledgeFileError('Open a Business Agent before adding files.');
      return;
    }
    const unsupported = nextFiles.find((file) => !isSupportedKnowledgeFile(file));
    if (unsupported) {
      setKnowledgeFileError(`Unsupported file type: ${unsupported.name}. Use ${SUPPORTED_KNOWLEDGE_FILE_EXTENSIONS.join(', ')}.`);
      return;
    }
    setIsUploadingKnowledgeFile(true);
    setKnowledgeFileError(null);
    try {
      const oversized = nextFiles.find((file) => file.size > KNOWLEDGE_UPLOAD_MAX_BYTES);
      if (oversized) {
        setKnowledgeFileError(`${oversized.name} exceeds ${KNOWLEDGE_UPLOAD_MAX_MB}. Reduce file size and retry.`);
        return;
      }

      for (const file of nextFiles) {
        await onUploadKnowledgeFile(file);
      }
    } catch (error) {
      setKnowledgeFileError(error instanceof Error ? error.message : 'Knowledge file could not be uploaded.');
    } finally {
      setIsUploadingKnowledgeFile(false);
      if (knowledgeFileInputRef.current) {
        knowledgeFileInputRef.current.value = '';
      }
    }
  };

  const selectAgentSection = (tabId: SpecialistOverlayTabId) => {
    onSelectTab(tabId);
  };

  if (!selectedAgent) {
    return (
      <div className="studio-agent-detail-empty" aria-label="Agent detail">
        <strong>Select an agent</strong>
        <span>
          Agent configuration, channels, memory, analytics, and launch state will appear here.
        </span>
      </div>
    );
  }

  return (
    <div className="app-stack-4 studio-agent-detail-motion">
      <div className={joinClassNames(
        'studio-agent-detail-layout',
        currentStudioSubview !== 'agents' && 'studio-agent-detail-layout--single',
      )}>
        <div className="studio-agent-detail-content">
        <AnimatePresence mode="wait" initial={false}>
      {currentStudioSubview === 'agents' && overlayTab === 'knowledge' && (
        <MotionTabPanel key="knowledge" className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail"
          hideHeaderText
          eyebrow="Knowledge"
          title="Agent knowledge"
          subtitle="Instructions and trusted sources this agent should use when answering."
          actions={(
            <div className="app-inline-actions app-inline-actions--tight">
              <AppButton type="button" tone="secondary" onClick={onOpenEditWizard}>
                Edit
              </AppButton>
              {detailConfigDraft ? (
                <AppButton type="button" onClick={onSaveDetailConfig} disabled={isSavingDetailConfig}>
                  {isSavingDetailConfig ? 'Saving…' : 'Save'}
                </AppButton>
              ) : null}
            </div>
          )}
        >
          <div className="studio-agent-knowledge">
            <section className="studio-agent-knowledge__section studio-agent-knowledge__section--instructions">
              <div className="studio-agent-knowledge__block studio-agent-knowledge__block--purpose">
                {detailConfigDraft ? (
                  <>
                    <label className="studio-agent-knowledge__edit-field">
                      <span>Purpose and behavior</span>
                      <AppTextarea
                        rows={5}
                        value={detailInstructionsValue}
                        onChange={(event) => onUpdateDetailConfig({ systemPrompt: event.currentTarget.value })}
                        placeholder="Describe what this Business Agent should do, what it should answer, and when it should hand off to a human."
                      />
                    </label>
                    <label className="studio-agent-knowledge__edit-field">
                      <span>Tone</span>
                      <AppTextarea
                        rows={2}
                        value={detailPersonaValue}
                        onChange={(event) => onUpdateDetailConfig({ persona: event.currentTarget.value })}
                        placeholder="Describe the voice and style this Business Agent should use."
                      />
                    </label>
                    <div className="studio-agent-knowledge__block-actions">
                      <AppButton type="button" onClick={onSaveDetailConfig} disabled={isSavingDetailConfig}>
                        {isSavingDetailConfig ? 'Saving...' : 'Save instructions'}
                      </AppButton>
                    </div>
                  </>
                ) : (
                  <>
                    <p>{visibleAgentInstructionsText || 'No instructions configured yet.'}</p>
                    <small>{visibleAgentPersonaText || 'No tone configured yet.'}</small>
                  </>
                )}
              </div>
            </section>

            <section className="studio-agent-knowledge__section">
              <div className="studio-agent-knowledge__section-head">
                <div>
                  <span>Knowledge</span>
                  <strong>Files and sources</strong>
                </div>
                <div className="studio-agent-knowledge__status-pill">
                  {visibleKnowledgeSourceCount > 0 ? `${visibleKnowledgeSourceCount} source${visibleKnowledgeSourceCount === 1 ? '' : 's'}` : 'No data'}
                </div>
              </div>

              {detailConfigDraft ? (
                <div className="studio-agent-knowledge__source-tools">
                  <button
                    type="button"
                    className="studio-agent-knowledge__dropzone"
                    disabled={isUploadingKnowledgeFile}
                    onClick={() => knowledgeFileInputRef.current?.click()}
                    onDragOver={(event) => {
                      event.preventDefault();
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      void handleKnowledgeFiles(event.dataTransfer.files);
                    }}
                  >
                    <strong>{isUploadingKnowledgeFile ? 'Uploading file...' : 'Drop files here or choose files'}</strong>
                    <span>{`Supports ${SUPPORTED_KNOWLEDGE_FILE_EXTENSIONS.join(', ')} files up to ${KNOWLEDGE_UPLOAD_MAX_MB} (JSON request body limit).`}</span>
                  </button>
                  <input
                    ref={knowledgeFileInputRef}
                    type="file"
                    multiple
                    accept={SUPPORTED_KNOWLEDGE_FILE_EXTENSIONS.join(',')}
                    className="studio-agent-knowledge__file-input"
                    onChange={(event) => {
                      if (event.currentTarget.files) {
                        void handleKnowledgeFiles(event.currentTarget.files);
                      }
                    }}
                  />
                  <div className="studio-agent-knowledge__source-form">
                    <input
                      type="text"
                      value={knowledgeSourceInput}
                      placeholder="Add a website, Google Sheet, file URI, or source reference..."
                      aria-label="Knowledge source reference"
                      onChange={(event) => setKnowledgeSourceInput(event.currentTarget.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          handleKnowledgeReferenceSubmit();
                        }
                      }}
                    />
                    <AppButton
                      type="button"
                      tone="secondary"
                      disabled={!knowledgeSourceInput.trim()}
                      onClick={handleKnowledgeReferenceSubmit}
                    >
                      Add source
                    </AppButton>
                  </div>
                  {knowledgeFileError ? (
                    <small className="studio-agent-knowledge__error" role="alert">{knowledgeFileError}</small>
                  ) : null}
                  <div className="studio-agent-knowledge__block-actions">
                    <AppButton type="button" onClick={onSaveDetailConfig} disabled={isSavingDetailConfig}>
                      {isSavingDetailConfig ? 'Saving...' : 'Save sources'}
                    </AppButton>
                  </div>
                </div>
              ) : null}

              <div className="studio-agent-knowledge__sources">
                {visibleKnowledgeSources.length === 0 ? (
                  <div className="deployed-agents-overlay__empty">No knowledge sources connected yet.</div>
                ) : visibleKnowledgeSources.map((source, index) => {
                  const record = readRecord(source);
                  const label = readString(record.label ?? record.uri ?? record.id, `Knowledge source ${index + 1}`);
                  return (
                    <div key={`${label}-${index}`} className="studio-agent-knowledge__source">
                      <div className="studio-agent-knowledge__source-main">
                        <strong>{label}</strong>
                        <span>{readString(record.kind, humanizeToken(record.type, 'Source'))}</span>
                      </div>
                      <DataBadge tone={index < knowledgeSourceCount ? 'success' : 'neutral'}>
                        {index < knowledgeSourceCount ? 'Saved' : 'Unsaved'}
                      </DataBadge>
                    </div>
                  );
                })}
              </div>
            </section>

            {detailConfigDraft ? (
              <section className="studio-agent-knowledge__section studio-agent-knowledge__section--depth">
                <ContextPresetControl
                  value={detailConfigDraft.contextBudgetPreset}
                  onSelect={(nextValue) => onUpdateDetailConfig({ contextBudgetPreset: nextValue })}
                />
              </section>
            ) : null}
          </div>
        </ListDetailPanel>
        </MotionTabPanel>
      )}

      {currentStudioSubview === 'agents' && overlayTab === 'ai' && (
        <MotionTabPanel key="ai" className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail"
          hideHeaderText
          eyebrow="Model"
          title="AI route"
          subtitle="Uses the workspace AI route by default. Override only when this agent needs a different provider, local model, or budget."
          actions={detailConfigDraft ? (
            <AppButton type="button" onClick={onSaveDetailConfig} disabled={isSavingDetailConfig}>
              {isSavingDetailConfig ? 'Saving…' : 'Save'}
            </AppButton>
          ) : undefined}
        >
          {detailConfigDraft ? (
            <>
              <AgentAiSettingsSections
                value={detailConfigDraft}
                providerCatalog={providerCatalog}
                isLoadingProviderCatalog={isLoadingProviderCatalog}
                onSelectTier={(nextTier) => {
                  onUpdateDetailConfig({ aiTier: nextTier });
                }}
                onSelectAiSource={(nextSource) => {
                  onUpdateDetailConfig({ aiSource: nextSource });
                }}
                onSelectProvider={(nextProviderId) => {
                   onUpdateDetailConfig({ providerId: nextProviderId });
                }}
                onSelectModel={(nextModelId) => {
                   onUpdateDetailConfig({ modelId: nextModelId });
                }}
                onRefreshProviderModels={onRefreshProviderModels}
                onSaveProviderCredential={onSaveProviderCredential}
                isSavingProviderCredential={isSavingProviderCredentialProp}
              />
            </>
          ) : null}
        </ListDetailPanel>
        </MotionTabPanel>
      )}

      {currentStudioSubview === 'agents' && overlayTab === 'channels' && (
        <MotionTabPanel key="channels" className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail"
          hideHeaderText
          eyebrow="Channels"
          title="Agent channels"
          subtitle="Connect where customers talk to this agent."
        >
          <AgentIntegrationsSections
            connectorCards={overlayConnectorCards}
            workspaceId={workspaceId}
            selectedAgentId={selectedAgentId}
            view="channels"
          />
        </ListDetailPanel>
        </MotionTabPanel>
      )}

      {currentStudioSubview === 'agents' && overlayTab === 'tools' && (
        <MotionTabPanel key="tools" className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail"
          hideHeaderText
          eyebrow="Actions"
          title="Agent actions"
          subtitle="Choose what this agent may do with connected services."
          actions={detailConfigDraft ? (
            <AppButton type="button" onClick={onSaveDetailConfig} disabled={isSavingDetailConfig}>
              {isSavingDetailConfig ? 'Saving…' : 'Save'}
            </AppButton>
          ) : undefined}
        >
          {detailConfigDraft ? (
            <>
              <AgentActionCapabilitySections
                selectedToolIds={detailConfigDraft.selectedToolIds}
                onToggleTool={(toolId) => {
                  const nextSelected = detailConfigDraft.selectedToolIds.includes(toolId)
                    ? detailConfigDraft.selectedToolIds.filter((item) => item !== toolId)
                    : [...detailConfigDraft.selectedToolIds, toolId];
                  onUpdateDetailConfig({ selectedToolIds: nextSelected });
                }}
              />
              <AgentIntegrationsSections
                connectorCards={overlayConnectorCards}
                workspaceId={workspaceId}
                selectedAgentId={selectedAgentId}
                view="integrations"
              />
            </>
          ) : null}
        </ListDetailPanel>
        </MotionTabPanel>
      )}

      {currentStudioSubview === 'agents' && overlayTab === 'memory' && (
        <MotionTabPanel key="memory" className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail"
          hideHeaderText
          eyebrow="Memory"
          title="Agent memory"
          subtitle="Control what this agent can carry forward from customer conversations."
          actions={detailConfigDraft ? (
            <AppButton type="button" onClick={onSaveDetailConfig} disabled={isSavingDetailConfig}>
              {isSavingDetailConfig ? 'Saving…' : 'Save'}
            </AppButton>
          ) : undefined}
        >
          {detailConfigDraft ? (
            <>
              <div className="deployed-agents-overlay__toggle-row">
                <div className="sage-tool-row__copy">
                  <strong className="sage-tool-row__title">Persistent memory</strong>
                  <p className="sage-tool-row__description">Remember useful customer facts across conversations.</p>
                </div>
                <button
                  type="button"
                  className={joinClassNames('sage-tool-toggle', detailConfigDraft.memoryEnabled && 'sage-tool-toggle--enabled')}
                  role="switch"
                  aria-checked={detailConfigDraft.memoryEnabled}
                  onClick={() => onUpdateDetailConfig({ memoryEnabled: !detailConfigDraft.memoryEnabled })}
                >
                  <span className="sage-tool-toggle__thumb" />
                </button>
              </div>
              {detailConfigDraft.memoryEnabled ? (
                <div className="studio-agent-memory-settings">
                <RetentionPresetControl
                  value={detailConfigDraft.retentionPreset}
                  onSelect={(nextValue) => onUpdateDetailConfig({ retentionPreset: nextValue })}
                />
                </div>
              ) : null}
              <div className="deployed-agents-overlay__memory-list">
                {isLoadingOverlayMemory && overlayMemoryEntries.length === 0 ? (
                  <>
                    <SkeletonBlock height="4rem" />
                    <SkeletonBlock height="4rem" />
                  </>
                ) : overlayMemoryEntries.length === 0 ? (
                  <div className="deployed-agents-overlay__empty">No customer memory yet. Memory builds as customers chat with this agent.</div>
                ) : overlayMemoryEntries.map((entry) => (
                  <article key={readString(entry.id, `${readString(entry.channel)}-${readString(entry.external_user_id)}`)} className="deployed-agents-overlay__memory-entry">
                    <strong className="deployed-agents-overlay__memory-title">{truncateExternalUserId(entry.external_user_id)}</strong>
                    <p className="deployed-agents-overlay__memory-body deployed-agents-overlay__memory-body--summary">
                      {readString(entry.summary_text, 'No memory summary yet.')}
                    </p>
                    <span className="deployed-agents-overlay__memory-meta">{formatTimestamp(entry.updated_at)}</span>
                  </article>
                ))}
              </div>
            </>
          ) : null}
        </ListDetailPanel>
        </MotionTabPanel>
      )}

      {currentStudioSubview === 'agents' && overlayTab === 'analytics' && (
        <MotionTabPanel key="analytics" className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail"
          hideHeaderText
          eyebrow="Results"
          title="Agent results"
          subtitle="Activity and outcome signals for the selected agent."
        >
          <WorkstationDeployedAgentAnalyticsPane
            agentId={selectedAgentId!}
            workspaceId={workspaceId}
          />
        </ListDetailPanel>
        </MotionTabPanel>
      )}
              {currentStudioSubview === 'agents' && overlayTab === 'chat' && (
        <MotionTabPanel key="chat" className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail studio-panel--chat"
          hideHeaderText
          eyebrow="Chat"
          title="Workspace chat"
          subtitle="Chat privately with this agent. Messages stay in Studio and never send to customer channels."
        >
          {selectedAgentId && workspaceId ? (
            <AgentPlaygroundPanel
              deployedAgentId={selectedAgentId}
              workspaceId={workspaceId}
              client={services.client}
              runtimeMode={testRuntimeModeForPlacement(selectedAgentRuntimePlacement)}
              session={testChatSession}
              onSessionChange={onTestChatSessionChange}
              onResetSession={onResetTestChatSession}
            />
          ) : (
            <EmptyPanel title="Agent is not ready yet" body="Save the agent first, then chat with it here." />
          )}
        </ListDetailPanel>
        </MotionTabPanel>
      )}

      {(currentStudioSubview === 'deploy' || (currentStudioSubview === 'agents' && overlayTab === 'overview')) && (
        <MotionTabPanel key="overview" className="studio-agent-motion-panel">
        <ListDetailPanel
          className="studio-panel studio-panel--detail"
          hideHeaderText
          eyebrow="Command Center"
          title={selectedAgentDisplayName}
          subtitle="Work handled, customer activity, and operating cost."
          actions={(
            <div className="app-inline-actions app-inline-actions--tight">
              {selectedAgentState === 'live' ? (
                <AppButton type="button" tone="secondary" onClick={onPause} disabled={isDeploymentBusy}>
                  {isDeploymentBusy ? 'Pausing...' : 'Pause'}
                </AppButton>
              ) : (
                <AppButton type="button" tone="primary" onClick={onDeploy} disabled={isDeploymentBusy || !launchReady}>
                  {isDeploymentBusy ? 'Deploying...' : 'Deploy'}
                </AppButton>
              )}
              <AppButton type="button" tone="secondary" onClick={onOpenEditWizard}>
                Edit setup
              </AppButton>
            </div>
          )}
        >
          {isLoadingDetail ? (
            <div className="app-stack-3">
              <SkeletonBlock height="6rem" />
              <SkeletonBlock height="12rem" />
            </div>
          ) : (
            <div className="studio-agent-overview">
              <section className="studio-agent-overview__readiness-hero">
                <div className="studio-agent-overview__hero-status">
                  <div className="studio-agent-overview__status-line">
                    <span className={joinClassNames('studio-agent-overview__status-dot', launchReady && 'studio-agent-overview__status-dot--ready')} />
                    <DataBadge tone={selectedAgentState === 'live' ? 'success' : launchReady ? 'success' : 'warning'}>
                      {selectedAgentStateLabel}
                    </DataBadge>
                  </div>
                  <strong>{selectedAgentDisplayName}</strong>
                  <p>{!modelReady ? 'Choose Workspace AI, your AI account, or a local model before launch.' : overviewStatusDetail}</p>
                </div>
              </section>

              <section className="studio-agent-overview__metrics-panel" aria-label="Agent metrics">
                <div className="studio-agent-overview__section-head">
                  <div>
                    <span>Metrics</span>
                    <strong>Traffic and cost</strong>
                  </div>
                  <div className="studio-agent-overview__range-toggle" aria-label="Metrics time range">
                    {OVERVIEW_RANGE_OPTIONS.map((range) => (
                      <button
                        key={range}
                        type="button"
                        className={joinClassNames(
                          'studio-agent-overview__range-button',
                          overviewRange === range && 'studio-agent-overview__range-button--active',
                        )}
                        onClick={() => setOverviewRange(range)}
                      >
                        {range}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="studio-agent-overview__metric-grid">
                  {overviewMetrics.map((item) => (
                    <div key={item.label} className="studio-agent-overview__metric-tile">
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                  ))}
                </div>

                <div className="studio-agent-overview__chart" aria-label="Conversations and messages over time">
                  <div className="studio-agent-overview__chart-head">
                    <div>
                      <span>Conversations vs messages</span>
                      <strong>{overviewUsageWindow}</strong>
                    </div>
                    <div className="studio-agent-overview__chart-legend" aria-label="Chart legend">
                      <span><i className="studio-agent-overview__legend-dot studio-agent-overview__legend-dot--conversations" />Conversations</span>
                      <span><i className="studio-agent-overview__legend-dot studio-agent-overview__legend-dot--messages" />Messages</span>
                    </div>
                  </div>
                  <div className="studio-agent-overview__chart-frame">
                    <svg className="studio-agent-overview__chart-svg" viewBox="0 0 100 90" role="img" aria-label={`${overviewRange} conversations and messages chart`}>
                      <line className="studio-agent-overview__chart-grid" x1="4" y1="20" x2="96" y2="20" />
                      <line className="studio-agent-overview__chart-grid" x1="4" y1="49" x2="96" y2="49" />
                      <line className="studio-agent-overview__chart-axis" x1="4" y1="78" x2="96" y2="78" />
                      <line className="studio-agent-overview__chart-axis" x1="4" y1="20" x2="4" y2="78" />
                      {overviewConversationPolyline ? (
                        <polyline className="studio-agent-overview__chart-line studio-agent-overview__chart-line--conversations" points={overviewConversationPolyline} />
                      ) : null}
                      {overviewMessagePolyline ? (
                        <polyline className="studio-agent-overview__chart-line studio-agent-overview__chart-line--messages" points={overviewMessagePolyline} />
                      ) : null}
                    </svg>
                    {overviewChartSeries.length === 0 ? (
                      <div className="studio-agent-overview__chart-empty">
                        <strong>No time-series data yet</strong>
                        <span>Totals are available, but the analytics endpoint is not returning daily points.</span>
                      </div>
                    ) : null}
                  </div>
                  <div className="studio-agent-overview__chart-labels">
                    <span>{overviewChartStartLabel}</span>
                    <span>Max {formatCompactCount(overviewChartMax)}</span>
                    <span>{overviewChartEndLabel}</span>
                  </div>
                </div>
              </section>

              <section className="studio-agent-overview__activity-panel" aria-label="Agent activity">
                <div className="studio-agent-overview__section-head">
                  <div>
                    <span>Activity</span>
                    <strong>Latest operating signals</strong>
                  </div>
                </div>
                <div className="studio-agent-overview__activity-row">
                  {overviewWorkSignals.map((item) => (
                    <div key={item.label} className="studio-agent-overview__activity-item">
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                  ))}
                </div>
              </section>

              <section className="studio-agent-overview__setup-alert" aria-label="Setup blockers">
                <div className="studio-agent-overview__section-head">
                  <div>
                    <span>Before launch</span>
                    <strong>Setup checklist</strong>
                  </div>
                  <small>{launchDetail}</small>
                </div>
                <div className="studio-agent-overview__blocker-list">
                  {overviewSetupChecklist.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      className={joinClassNames(
                        'studio-agent-overview__blocker-row',
                        item.ready && 'studio-agent-overview__blocker-row--ready',
                      )}
                      onClick={() => selectAgentSection(item.tab)}
                    >
                      <span className="studio-agent-overview__blocker-dot" />
                      <span className="studio-agent-overview__blocker-copy">
                        <strong>{item.label}</strong>
                        <small>{item.detail}</small>
                      </span>
                    </button>
                  ))}
                </div>
              </section>

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

AgentDetailView.displayName = 'AgentDetailView';
