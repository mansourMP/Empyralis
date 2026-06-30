'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { createPortal } from 'react-dom';
import { ArrowDown, Check, ChevronDown, ChevronRight, Monitor } from 'lucide-react';

import { CommandSheet } from '@/lib/ui/command-sheet';
import { ConfirmDialog } from '@/lib/ui/confirm-dialog';
import { FormField, FormGrid, FormInput, FormSection, FormTextarea } from '@/lib/ui/form-controls';
import { AppButton, AppNotice, AppShinyText } from '@/lib/ui/primitives';
import { PlatformNotification } from '@/lib/ui/platform-notification';
import { ScrollRegion } from '@/lib/ui/scroll-region';
import {
  ChatComposer,
  type ComposerCapabilityItem,
  type ComposerPreRunCostEstimate,
  type ComposerSlashCommand,
} from '@/lib/workspace/chat-composer';
import {
  SAGE_COMMAND_CATALOG,
  SAGE_WORKSPACE_COMMAND_CATALOG,
  type SageCommandMetadata,
  type SageWorkspaceCommandMetadata,
  resolveSageCommandBySlash,
} from '@/lib/workspace/sage-command-catalog';
import type {
  WorkstationChatArtifactReference,
  WorkstationChatMessageRecord,
} from '@/lib/workspace/chat-message';
import { CodexChatCell } from '@/lib/workspace/codex-chat/cell-components';
import type { CodexTranscriptCell, TimelineProjectionEvent } from '@/lib/workspace/codex-chat/cells';
import {
  resolveModelContextWindow,
} from '@/lib/workspace/model-capabilities';
import { useWorkstationDesktopBridge } from '@/lib/workspace/workstation-desktop-bridge';
import type { WorkspaceBootstrapRuntimeTarget } from '@/lib/workspace/workspace-bootstrap';
import {
  emitWorkstationChatHistoryInvalidated,
  emitWorkstationChatThreadSelected,
  subscribeWorkstationChatNewThreadRequested,
  subscribeWorkstationChatThreadSelected,
} from '@/lib/workspace/workstation-chat-thread-events';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { subscribeWorkstationProviderChanged } from '@/lib/workspace/workstation-provider-events';
import {
  useWorkspaceServices,
  useWorkstationActivityVersion,
  useWorkstationStreamSelector,
} from '@/lib/workspace/workspace-services';
import {
  type SageChatAttachment,
  WorkstationClientError,
  type WorkstationAgentTraceEvent,
  type WorkstationAgentTraceRecord,
  type ProviderCatalogModelRecord,
  type ProviderCatalogRecord,
  type ProviderProfileRecord,
  type WorkstationSageMemoryRecord,
  type WorkstationSageProfileRecord,
  type WorkstationSessionActor,
  type WorkstationSessionRecord,
  type WorkstationTurnStreamAbortHandle,
  type WorkstationTurnResponse,
  type WorkspaceAiRoutePayload,
} from '@/lib/workspace/workstation-client';
import {
  type CanonicalChatThreadState,
  type CanonicalRunSummary,
  type ChatAutonomyMode,
  type ChatMachineTrust,
  type ChatModelOption,
  type ChatReasoningEffort,
  type GatewayReadinessDoctorPayload,
  type LiveActivityStepState,
  type SageMemoryCategoryRecord,
  type LiveTraceState,
  type RecentThreadSummary,
  type SageMemoryDraft,
  type SageMemorySnapshot,
  type SageProfileSnapshot,
  type SageSetupSurfaceState,
  type SendFailureNotice,
  refreshProviderModelCatalog,
  useChatComposerState,
  useChatMemoryEditorState,
  useChatMemoryProfileState,
  useChatProviderModelState,
  useChatRunState,
  useChatStreamRunState,
  useChatThreadState,
  useChatUiPanelsState,
} from '@/lib/workspace/workstation-chat-pane-hooks';
import {
  loadChatMemorySnapshot,
  loadChatProfileSnapshot,
} from '@/lib/workspace/workstation-chat-memory-loaders';
import {
  safeTimelineEventsFromTraceReplay,
  useWorkstationTimelineProjection,
} from '@/lib/workspace/workstation-chat-timeline-projection';
import {
  PRIMARY_THREAD_ID,
  CHAT_READ_TIMEOUT_MS,
  SAGE_SETUP_TIMEOUT_MS,
  CHAT_THINKING_RECOVERY_MS,
  ACTIVE_THREAD_QUERY_KEY,
  RUNS_QUERY_KEY,
  SAGE_MEMORY_QUERY_KEY,
  SAGE_PROFILE_QUERY_KEY,
  RECENT_THREADS_QUERY_KEY,
  activeThreadStorageKey,
  readPersistedActiveThread,
  persistActiveThread,
  threadQueryKey,
  readString,
  isSmallOllamaSelection,
  readNumber,
  readObject,
  mergeTraceEvents,
  isTerminalTraceEvent,
  normalizeTraceStreamEvent,
  buildLiveTraceRecord,
  isTextEditingTarget,
  isSyntheticTranscriptMessage,
  isConnectorSetupIntervention,
  connectorSetupNoticeFromInterventions,
  isProviderRuntimeGateMessage,
  isProviderGateSystemCell,
  isProviderGateTranscriptCell,
  normalizeCanonicalChatThread,
  normalizeCanonicalRunItems,
  normalizeProviderCatalogRecords,
  normalizeProviderProfiles,
  sortProviderProfiles,
  profileMetadataRecord,
  normalizeTimelineItems,
  normalizeRecentThreadsFromThreadList,
  deriveRecentThreads,
  summarizeThreadForHistory,
  readExecutionTarget,
  resolveRuntimeTrustZone,
  buildChatPermissionPolicyContext,
  EMPYRALIS_TIER_SET,
  USER_OWNED_SECTION_LABELS,
  isProviderEligibleForModelSelector,
  workspaceDefaultModelOption,
  disconnectedModelOption,
  providerPathLabel,
  providerSummaryLabel,
  providerFailureMessageForProvider,
  providerFailureNoticeForProvider,
  providerReadyForChat,
  modelOptionDisplayLabel,
  normalizeChatModelOptions,
  normalizeHostedCreditStateForChat,
  buildPreRunCostEstimate,
  formatContextWindowLabel,
  reasoningLabel,
  findProviderFailureIntervention,
  stripInternalToolMarkup,
  createCanonicalAssistantMessage,
  createPendingUserMessage,
  createClientTurnRequestId,
  createIncompleteAssistantMessage,
  canonicalIncludesMessage,
  projectedAssistantLooksSynthetic,
  upsertLiveActivityStep,
  normalizeStepEvent,
  settleLiveActivitySteps,
  localCompanionTarget,
  localDevicePlatformLabel,
  summarizeRuntimeCard,
  classifyStatusNotice,
  isLocalCompanionGateMessage,
  browserReadinessPill,
  normalizeSageMemorySnapshot,
  defaultSageProfileSnapshot,
  normalizeSageProfileSnapshot,
  humanizeSageSetupFailure,
  withTimeout,
  hostedCreditsFallbackProvider,
  normalizeSageToolPolicy,
  defaultSageMemoryDraft,
  isTransientBackgroundReadError,
  shouldSuppressBackgroundRefreshNotice,
  resolveProviderModelContext,
  resolvePersistedSelectedModelId
} from '@/lib/workspace/workstation-chat-pane-model';
import type {
  SageReadinessPill,
  GatewayReadinessRegistration,
  ChatRuntimeTrustZone
} from '@/lib/workspace/workstation-chat-pane-model';

const SAGE_EMPTY_STATE_PROMPTS = [
  'Draft a clear plan for today',
  'Summarize an uploaded file',
  'Prepare for my next meeting',
  'Triage important emails',
  'Create a weekly report',
] as const;

const SAGE_MODEL_PICKER_PROVIDERS = [
  { id: 'empyralis', label: 'Empyralis', aliases: [] },
  { id: 'anthropic', label: 'Anthropic', aliases: ['anthropic'] },
  { id: 'openai', label: 'OpenAI', aliases: ['openai'] },
  { id: 'codex', label: 'Codex CLI', aliases: ['openai_codex', 'codex_cli'] },
  { id: 'google', label: 'Google', aliases: ['google', 'gemini', 'google_gemini'] },
  { id: 'deepseek', label: 'DeepSeek', aliases: ['deepseek'] },
  { id: 'ollama', label: 'Ollama', aliases: ['ollama', 'ollama_cloud'] },
] as const;

type SageModelPickerProviderId = (typeof SAGE_MODEL_PICKER_PROVIDERS)[number]['id'];
const SAGE_MODEL_PICKER_PROVIDER_IMAGES: Record<SageModelPickerProviderId, string | null> = {
  empyralis: null,
  anthropic: '/brand-assets/providers/anthropic.svg?v=3',
  openai: '/brand-assets/providers/openai.svg?v=3',
  codex: '/brand-assets/providers/openai.svg?v=3',
  google: '/brand-assets/providers/gemini.svg?v=3',
  deepseek: '/brand-assets/providers/deepseek.svg?v=3',
  ollama: '/brand-assets/providers/ollama.svg?v=3',
};

const SAGE_MODEL_PICKER_OFFICIAL_MODEL_IDS: Record<SageModelPickerProviderId, readonly string[]> = {
  empyralis: [
    'light',
    'pro',
    'max',
  ],
  anthropic: [
    'claude-opus-4-7',
    'claude-sonnet-4-6',
    'claude-haiku-4-5',
  ],
  openai: [
    'gpt-5-5-pro',
    'gpt-5-5',
    'gpt-5-4',
    'gpt-5-4-mini',
  ],
  codex: [
    'gpt-5-4',
    'gpt-5-3-codex',
    'gpt-5-2',
  ],
  google: [
    'gemini-3-pro-preview',
    'gemini-3-flash-preview',
    'gemini-2-5-pro',
    'gemini-2-5-flash',
  ],
  deepseek: [
    'deepseek-v4-pro',
    'deepseek-v4-flash',
    'deepseek-chat',
    'deepseek-reasoner',
  ],
  ollama: [
    'gpt-oss-120b',
    'gpt-oss-20b',
    'llama3-2',
    'llama3',
  ],
};

type SageCompanyModelOption = {
  id: 'light' | 'pro' | 'max';
  label: string;
  optionId: string;
  selected: boolean;
  disabled: boolean;
};

type SageModelPickerModel = {
  id: string;
  modelId: string;
  label: string;
  description: string;
  optionId: string;
  selected: boolean;
};

type SageModelPickerProviderPanel = {
  id: SageModelPickerProviderId;
  label: string;
  image: string | null;
  models: SageModelPickerModel[];
};

function normalizedProviderToken(value: unknown): string {
  return readString(value).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

function normalizedModelPickerToken(value: unknown): string {
  return readString(value).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function SageModelPickerProviderMark({
  provider,
}: {
  provider: Pick<SageModelPickerProviderPanel, 'image' | 'label'>;
}) {
  if (provider.image) {
    return (
      <span className="sage-canvas-model__provider-mark" aria-hidden="true">
        <img src={provider.image} alt="" />
      </span>
    );
  }
  return (
    <span className="sage-canvas-model__provider-mark sage-canvas-model__provider-mark--fallback" aria-hidden="true">
      {provider.label.charAt(0).toUpperCase()}
    </span>
  );
}

function officialModelRank(
  providerId: SageModelPickerProviderId,
  model: Pick<SageModelPickerModel, 'label'>,
): number {
  const officialModels = SAGE_MODEL_PICKER_OFFICIAL_MODEL_IDS[providerId];
  const label = normalizedModelPickerToken(model.label);
  return officialModels.findIndex((officialModel) => {
    const officialId = normalizedModelPickerToken(officialModel);
    return label === officialId;
  });
}

function providerMatchesPickerProvider(
  provider: ProviderCatalogRecord,
  pickerProvider: (typeof SAGE_MODEL_PICKER_PROVIDERS)[number],
): boolean {
  const credentialPlane = readString(provider.credential_plane).toLowerCase();
  if (pickerProvider.id === 'empyralis') {
    return credentialPlane === 'platform_runtime';
  }
  if (credentialPlane === 'platform_runtime') {
    return false;
  }
  const providerId = normalizedProviderToken(provider.id);
  const providerLabel = normalizedProviderToken(provider.label);
  return pickerProvider.aliases.some((alias) =>
    providerId === alias
    || providerId.startsWith(`${alias}_`)
    || providerId.endsWith(`_${alias}`)
    || providerLabel === alias
    || providerLabel.includes(alias));
}

function catalogModelDescription(
  model: ProviderCatalogModelRecord,
  _providerLabel: string,
): string {
  return readString(model.description)
    || readString(model.summary)
    || readString(model.family);
}

interface SageChatEmptyStateProps {
  modelLabel: string;
  providerGateVisible: boolean;
  integrationsHref: string;
  onSelectPrompt: (prompt: string) => void;
}

function SageChatEmptyState({
  modelLabel,
  providerGateVisible,
  integrationsHref,
  onSelectPrompt,
}: SageChatEmptyStateProps) {
  return (
    <div className="app-chat-empty-state app-chat-empty-state--sage">
      <div className="app-chat-empty-state__content">
        <div className="app-chat-empty-state__identity">
          <h1 className="app-chat-empty-state__title">Sage</h1>
          <p className="app-chat-empty-state__model">{modelLabel}</p>
        </div>

        {providerGateVisible ? (
          <p className="app-chat-empty-state__provider-notice">
            No tier available —{' '}
            <Link className="app-chat-empty-state__provider-link" href={`${integrationsHref}?section=ai-runtime`}>
              add one in Settings
            </Link>
          </p>
        ) : (
          <div
            className="app-chat-empty-state__suggestions app-chat-empty-state__suggestions--composer-clearance"
            aria-label="Suggested prompts"
          >
            {SAGE_EMPTY_STATE_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                className="app-chat-empty-state__prompt"
                onClick={() => {
                  onSelectPrompt(prompt);
                }}
              >
                {prompt}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function legacyTraceIdForReplay(message: WorkstationChatMessageRecord): string | null {
  if (message.role.trim().toLowerCase() !== 'assistant') {
    return null;
  }
  const metadata = readObject(message.metadata);
  const transcriptEvents = Array.isArray(metadata.transcript_events) ? metadata.transcript_events : [];
  if (transcriptEvents.length > 0) {
    return null;
  }
  const contextUsed = readObject(metadata.context_used);
  return readString(metadata.trace_id) || readString(contextUsed.trace_id) || null;
}

const TYPED_EXECUTION_STREAM_EVENTS = new Set([
  'thinking',
  'tool_call',
  'tool_result',
  'bash_output',
  'response',
]);

const CHAT_TRANSCRIPT_BOTTOM_THRESHOLD_PX = 96;

function typedTimelineEventFromStreamEvent(event: { event: string; payload: Record<string, unknown> }): TimelineProjectionEvent | null {
  const eventType = readString(event.event).toLowerCase();
  if (!TYPED_EXECUTION_STREAM_EVENTS.has(eventType)) {
    return null;
  }
  return {
    type: 'typed',
    payload: {
      ...event.payload,
      event_type: eventType,
    },
  };
}

function isChatTranscriptNearBottom(element: HTMLElement | null): boolean {
  if (!element) {
    return true;
  }
  const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
  return remaining <= CHAT_TRANSCRIPT_BOTTOM_THRESHOLD_PX;
}

function transcriptCellScrollKey(cell: CodexTranscriptCell): string {
  const record = cell as unknown as Record<string, unknown>;
  const id = readString(record.id);
  const activity = readObject(record.activity);
  const content = readString(record.content)
    || readString(record.text)
    || readString(record.output)
    || readString(activity.label)
    || readString(activity.detail);
  const status = readString(record.status) || readString(activity.status) || readString(activity.type);
  const streaming = record.isStreaming === true ? 'streaming' : '';
  return `${cell.kind}:${id}:${content.length}:${status}:${streaming}`;
}

export function WorkstationChatPane() {
  const { bootstrap, routeManifest, hasCapability } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const activityVersion = useWorkstationActivityVersion();
  const activityConnectionState = useWorkstationStreamSelector((state) => state.activity.connectionState);
  const notificationsConnectionState = useWorkstationStreamSelector((state) => state.notifications.connectionState);
  const desktop = useWorkstationDesktopBridge();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [workspaceCommandPaletteOpen, setWorkspaceCommandPaletteOpen] = useState(false);
  const [legacyTraceEventsByTraceId, setLegacyTraceEventsByTraceId] = useState<Record<string, TimelineProjectionEvent[]>>({});
  const pendingLegacyTraceIdsRef = useRef<Set<string>>(new Set());
  const transcriptScrollRef = useRef<HTMLDivElement | null>(null);
  const transcriptShouldStickRef = useRef(true);
  const transcriptForceStickRef = useRef(false);
  const transcriptScrollFrameRef = useRef<number | null>(null);
  const [showTranscriptJump, setShowTranscriptJump] = useState(false);
  const actor = useMemo<WorkstationSessionActor>(() => ({
    type: 'user',
    id: bootstrap.account.id,
    display_name: bootstrap.account.displayName ?? bootstrap.account.email,
  }), [bootstrap.account.displayName, bootstrap.account.email, bootstrap.account.id]);

  const {
    activeThreadId,
    setActiveThreadId,
    activeThreadIdRef,
    thread,
    setThread,
    threadRef,
    recentThreads,
    setRecentThreads,
  } = useChatThreadState({
    queryClient: services.queryClient,
    activeThreadQueryKey: ACTIVE_THREAD_QUERY_KEY,
    recentThreadsQueryKey: RECENT_THREADS_QUERY_KEY,
    workspaceStorageKey: activeThreadStorageKey(bootstrap.workspace.id),
    primaryThreadId: PRIMARY_THREAD_ID,
    threadQueryKey,
  });
  const {
    draft,
    setDraft,
    attachments,
    setAttachments,
    statusMessage,
    setStatusMessage,
    sendFailureNotice,
    setSendFailureNotice,
    isSending,
    setIsSending,
  } = useChatComposerState();
  const isSendingRef = useRef(isSending);
  useEffect(() => { isSendingRef.current = isSending; }, [isSending]);
  const { runs, setRuns } = useChatRunState(
    services.queryClient,
    RUNS_QUERY_KEY,
  );
  const {
    profileSnapshot,
    setProfileSnapshot,
    sageSetupState,
    setSageSetupState,
    sageSetupMessage,
    setSageSetupMessage,
    bootstrapAnswer,
    setBootstrapAnswer,
    memorySnapshot,
    setMemorySnapshot,
  } = useChatMemoryProfileState({
    queryClient: services.queryClient,
    profileQueryKey: SAGE_PROFILE_QUERY_KEY,
    memoryQueryKey: SAGE_MEMORY_QUERY_KEY,
    defaultProfile: defaultSageProfileSnapshot(),
    defaultMemory: normalizeSageMemorySnapshot(null),
  });
  const {
    pendingUserMessage,
    setPendingUserMessage,
    pendingUserMessageRef,
    streamingAssistantText,
    setStreamingAssistantText,
    streamingAssistantTextRef,
    liveTimelineEvents,
    setLiveTimelineEvents,
    showProjectedAssistant,
    setShowProjectedAssistant,
    timelineSettled,
    setTimelineSettled,
    liveActivitySteps,
    setLiveActivitySteps,
    liveActivityStepsRef,
    liveTrace,
    setLiveTrace,
    markTurnActivity,
    lastTurnActivityAtRef,
  } = useChatStreamRunState();
  const {
    selectedExecutionPlacement,
    machineTrust,
    setMachineTrust,
    autonomyMode,
    modelOptions,
    setModelOptions,
    selectedModel,
    setSelectedModel,
    providerCatalog,
    setProviderCatalog,
    providerProfiles,
    setProviderProfiles,
    setToolPolicy,
    browserGatewayDoctor,
    setBrowserGatewayDoctor,
    reasoningEffort,
    setReasoningEffort,
    isPersistingModelSelection,
    setIsPersistingModelSelection,
  } = useChatProviderModelState(disconnectedModelOption());
  const {
    titlebarActionsHost,
    setTitlebarActionsHost,
    isLoading,
    setIsLoading,
    isSubmittingBootstrap,
    setIsSubmittingBootstrap,
    isRetryingSageSetup,
    setIsRetryingSageSetup,
    hasEnteredConversationFlow,
    setHasEnteredConversationFlow,
    smallModelWarningVisible,
    setSmallModelWarningVisible,
    mutatingMemory,
    setMutatingMemory,
    isMemorySheetOpen,
    setIsMemorySheetOpen,
  } = useChatUiPanelsState();
  const {
    memoryDraft,
    setMemoryDraft,
    pendingDeleteMemoryId,
    setPendingDeleteMemoryId,
  } = useChatMemoryEditorState(defaultSageMemoryDraft());
  const [billingSummary, setBillingSummary] = useState<Record<string, unknown> | null>(null);
  const [workspaceAiRoute, setWorkspaceAiRoute] = useState<WorkspaceAiRoutePayload | null>(null);
  const [sageAgentComputerSelection, setSageAgentComputerSelection] = useState<Record<string, unknown> | null>(null);
  const submitInFlightRef = useRef(false);
  const streamAbortHandleRef = useRef<WorkstationTurnStreamAbortHandle | null>(null);
  const streamAbortRequestedRef = useRef(false);
  const streamInFlightRef = useRef(false);
  const activeTurnRequestIdRef = useRef<string | null>(null);
  const streamBufferRef = useRef("");
  const rafHandleRef = useRef<number | null>(null);

  useEffect(() => {
    setTitlebarActionsHost(document.getElementById('workstation-titlebar-brand-actions-slot'));
    return () => {
      setTitlebarActionsHost(null);
    };
  }, [setTitlebarActionsHost]);

  const updatePendingUserMessage = useCallback((message: WorkstationChatMessageRecord | null) => {
    pendingUserMessageRef.current = message;
    setPendingUserMessage(message);
  }, []);

  useEffect(() => {
    if (!pendingUserMessage) {
      return;
    }
    const canonicalMessages = thread.messages.filter((message) => !isSyntheticTranscriptMessage(message));
    if (canonicalIncludesMessage(canonicalMessages, pendingUserMessage)) {
      updatePendingUserMessage(null);
    }
  }, [pendingUserMessage, thread.messages, updatePendingUserMessage]);


  const refreshProviderCatalog = useCallback(async () => {
    const payload = await services.queryClient.run('chat:provider-catalog', async () => {
      const profileRequest = services.client.listProviderProfiles().catch(() => ({ items: [] }));
      const aiRouteRequest = services.client.getWorkspaceAiRoute().catch(() => null);
      const catalogRequest = (async () => {
        try {
          return await services.client.listProviderCatalog();
        } catch {
          return await services.client.listProviders();
        }
      })();
      const [catalogPayload, profilesPayload, aiRoutePayload] = await Promise.all([catalogRequest, profileRequest, aiRouteRequest]);
      return {
        catalogPayload,
        profilesPayload,
        aiRoutePayload,
      };
    }).catch(() => null);

    if (!payload || typeof payload !== 'object') {
      setProviderCatalog((current) => (current.length > 0 ? current : [hostedCreditsFallbackProvider()]));
      setProviderProfiles((current) => current);
      setModelOptions((current) => (
        current.some((option) => option.providerId)
          ? current
          : normalizeChatModelOptions({ providers: [hostedCreditsFallbackProvider()] })
      ));
      setWorkspaceAiRoute(null);
      return;
    }

    const nextCatalog = refreshProviderModelCatalog(payload, {
      normalizeProviderCatalogRecords,
      normalizeProviderProfiles,
      normalizeChatModelOptions,
      hostedCreditsFallbackProvider,
      workspaceDefaultModelOption,
    });
    setProviderCatalog(nextCatalog.providers);
    setProviderProfiles(nextCatalog.profiles);
    setModelOptions(nextCatalog.options);
    setWorkspaceAiRoute(
      payload.aiRoutePayload && typeof payload.aiRoutePayload === 'object'
        ? payload.aiRoutePayload as WorkspaceAiRoutePayload
        : null,
    );
  }, [services.client, services.queryClient]);

  const refreshToolingState = useCallback(async () => {
    const payload = await services.queryClient.run('chat:tooling-state', async () => {
      const toolPolicyPayload = await services.client.getSageToolPolicy().catch(() => ({ tools: [] }));
      return {
        toolPolicyPayload,
      };
    }).catch(() => null);

    if (!payload || typeof payload !== 'object') {
      return;
    }

    setToolPolicy(normalizeSageToolPolicy((payload as { toolPolicyPayload?: unknown }).toolPolicyPayload));
  }, [services.client, services.queryClient]);

  const refreshBrowserGatewayReadiness = useCallback(async () => {
    const doctorPayload = await services.queryClient.run('chat:gateway-readiness', async () => {
      const registrationsPayload = await services.client.requestJson<Record<string, unknown>>({
        path: `/api/gateway/registrations?workspace_id=${encodeURIComponent(bootstrap.workspace.id)}`,
        allowStatuses: [404],
      });
      const registrations = Array.isArray(registrationsPayload?.items)
        ? registrationsPayload.items.filter((item): item is GatewayReadinessRegistration => Boolean(item) && typeof item === 'object')
        : [];
      const selectedGateway = registrations.find((item) =>
        readString(item.connection_status || item.status).toLowerCase() === 'online',
      ) ?? registrations[0] ?? null;
      const gatewayId = readString(selectedGateway?.gateway_id) || null;
      if (!gatewayId) {
        return null;
      }
      return await services.client.requestJson<GatewayReadinessDoctorPayload>({
        path: `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/doctor`,
        allowStatuses: [403, 404],
      });
    }).catch(() => null);
    setBrowserGatewayDoctor(doctorPayload && typeof doctorPayload === 'object' ? doctorPayload : null);
  }, [bootstrap.workspace.id, services.client, services.queryClient]);

  const refreshSageAgentComputerSelection = useCallback(async () => {
    const payload = await services.client.getSageAgentComputerSelection().catch(() => null);
    setSageAgentComputerSelection(payload && typeof payload === 'object' ? payload : null);
  }, [services.client]);

  useEffect(() => {
    void refreshSageAgentComputerSelection();
  }, [refreshSageAgentComputerSelection]);

  const refreshBillingSummary = useCallback(async () => {
    const payload = await services.queryClient.run('chat:billing-summary', async () => {
      return await services.client.getBillingSummary();
    }).catch(() => null);
    setBillingSummary(payload && typeof payload === 'object' ? payload : null);
  }, [services.client, services.queryClient]);

  const persistSelectedModelPreference = useCallback(async (nextModelId: string) => {
    const targetOption = nextModelId === 'default'
      ? null
      : modelOptions.find((option) => option.id === nextModelId) ?? null;
    const hostedTier = targetOption
      && targetOption.uiSection === 'empyralis'
      && EMPYRALIS_TIER_SET.has(readString(targetOption.id).toLowerCase())
      ? readString(targetOption.id).toLowerCase()
      : null;
    if (hostedTier) {
      await services.client.updateWorkspaceAiRouteDefault({
        routeId: `empyralis_managed:${hostedTier}`,
        kind: 'empyralis_managed',
        modelPreset: hostedTier,
      });
      return true;
    }

    const sortedProfiles = sortProviderProfiles(providerProfiles).filter((profile) => {
      const providerId = readString(profile.provider);
      return providerId && providerCatalog.some((provider) =>
        readString(provider.id) === providerId && isProviderEligibleForModelSelector(provider));
    });

    if (sortedProfiles.length === 0) {
      return false;
    }

    const targetProviderId = readString(targetOption?.providerId || targetOption?.routeProviderId);
    const targetProfile = targetProviderId
      ? sortedProfiles.find((profile) => readString(profile.provider) === targetProviderId && profile.enabled !== false) ?? null
      : null;

    if (nextModelId !== 'default' && (!targetOption || !targetProviderId || !targetProfile)) {
      return false;
    }

    await Promise.all(sortedProfiles.map((profile) => {
      const optionIsSelected = readString(profile.id) === readString(targetProfile?.id);
      const selectedTier = optionIsSelected
        && targetOption
        && targetOption.uiSection === 'empyralis'
        && EMPYRALIS_TIER_SET.has(readString(targetOption.id).toLowerCase())
        ? readString(targetOption.id).toLowerCase()
        : null;
      const metadata = {
        ...profileMetadataRecord(profile),
        chat_model_selection: optionIsSelected ? 'explicit' : 'default',
        chat_model_tier: selectedTier,
      };
      return services.client.upsertProviderProfile({
        id: readString(profile.id) || null,
        provider: readString(profile.provider),
        label: readString(profile.label) || `Sage ${readString(profile.provider)}`,
        credentialId: readString(profile.credential_id) || null,
        authMode: readString(profile.auth_mode) || null,
        priority: Number(profile.priority ?? 100),
        enabled: profile.enabled !== false,
        model: optionIsSelected
          ? readString(targetOption?.routeModelId || targetOption?.id) || readString(profile.model) || null
          : readString(profile.model) || null,
        metadata,
      });
    }));

    return true;
  }, [modelOptions, providerCatalog, providerProfiles, services.client]);

  const writeThreadState = (nextThread: CanonicalChatThreadState) => {
    const normalizedMessages = nextThread.messages.filter((message) => !isSyntheticTranscriptMessage(message));
    const mergedThread: CanonicalChatThreadState = {
      ...nextThread,
      messages: normalizedMessages,
    };
    services.queryClient.set(threadQueryKey(mergedThread.threadId), mergedThread);
    services.queryClient.set(ACTIVE_THREAD_QUERY_KEY, nextThread.threadId);
    persistActiveThread(bootstrap.workspace.id, mergedThread.threadId);
    activeThreadIdRef.current = mergedThread.threadId;
    setActiveThreadId(mergedThread.threadId);
    setThread(mergedThread);

    const pendingMessage = pendingUserMessageRef.current;
    if (pendingMessage && canonicalIncludesMessage(mergedThread.messages, pendingMessage)) {
      updatePendingUserMessage(null);
    }
  };

  const writeOverview = ({
    nextRuns,
  }: {
    nextRuns: CanonicalRunSummary[];
  }) => {
    services.queryClient.set(RUNS_QUERY_KEY, nextRuns);
    setRuns(nextRuns);
  };

  const writeMemorySnapshot = (nextSnapshot: SageMemorySnapshot) => {
    services.queryClient.set(SAGE_MEMORY_QUERY_KEY, nextSnapshot);
    setMemorySnapshot(nextSnapshot);
  };

  const writeProfileSnapshot = (nextSnapshot: SageProfileSnapshot) => {
    services.queryClient.set(SAGE_PROFILE_QUERY_KEY, nextSnapshot);
    setProfileSnapshot(nextSnapshot);
    setSageSetupState(nextSnapshot.bootstrap.complete ? 'ready' : 'required');
    setSageSetupMessage(null);
  };

  const writeRecentThreads = (items: RecentThreadSummary[]) => {
    services.queryClient.set(RECENT_THREADS_QUERY_KEY, items);
    setRecentThreads(items);
  };

  const loadThread = async (requestedThreadId = activeThreadId) => {
    const cachedThread = services.queryClient.peek<CanonicalChatThreadState>(threadQueryKey(requestedThreadId));
    const payload = await services.queryClient.run(
      `chat:canonical:thread-load:${requestedThreadId}`,
      async () => withTimeout(
        services.client.getThread({
          threadId: requestedThreadId,
          allowMissing: true,
        }),
        CHAT_READ_TIMEOUT_MS,
        'Conversation history took too long to load.',
      ),
    );
    if (payload === null) {
      if (cachedThread && cachedThread.messages.length > 0) {
        if (activeThreadIdRef.current === requestedThreadId) {
          setThread(cachedThread);
        }
        return cachedThread;
      }
      if (thread.threadId === requestedThreadId && thread.messages.length > 0) {
        return thread;
      }
    }
    const nextThread = normalizeCanonicalChatThread(payload, requestedThreadId);
    nextThread.session = cachedThread?.session ?? null;
    services.queryClient.set(threadQueryKey(nextThread.threadId), nextThread);
    if (activeThreadIdRef.current === requestedThreadId) {
      writeThreadState(nextThread);
    }
    return nextThread;
  };

  const loadOverview = async () => {
    const runsFallback = services.queryClient.peek<CanonicalRunSummary[]>(RUNS_QUERY_KEY) ?? runs;
    const recentThreadsFallback = services.queryClient.peek<RecentThreadSummary[]>(RECENT_THREADS_QUERY_KEY) ?? recentThreads;

    const runsRequest = services.client.listRuns({
      limit: 12,
    }).then(normalizeCanonicalRunItems).catch((error) => {
      if (isTransientBackgroundReadError(error)) {
        return runsFallback;
      }
      throw error;
    });
    const timelineRequest = services.client.listActivityTimeline({
      limit: 40,
    }).then(normalizeTimelineItems).catch((error) => {
      if (isTransientBackgroundReadError(error)) {
        return [] satisfies Record<string, unknown>[];
      }
      throw error;
    });
    const threadListRequest = services.client.listThreads({
      includeTurns: true,
      limit: 80,
    }).then(normalizeRecentThreadsFromThreadList).catch((error) => {
      if (isTransientBackgroundReadError(error)) {
        return recentThreadsFallback;
      }
      throw error;
    });

    await withTimeout(services.queryClient.run('chat:canonical:overview', async () => {
      const [nextRuns, timelineItems, threadItems] = await Promise.all([
        runsRequest,
        timelineRequest,
        threadListRequest,
      ]);
      writeOverview({ nextRuns });
      if (threadItems.length > 0) {
        writeRecentThreads(threadItems);
        return;
      }
      if (timelineItems.length > 0) {
        writeRecentThreads(deriveRecentThreads(timelineItems, activeThreadIdRef.current));
        return;
      }
      if (recentThreadsFallback.length > 0) {
        writeRecentThreads(recentThreadsFallback);
        return;
      }
      writeRecentThreads(deriveRecentThreads([], activeThreadIdRef.current));
    }), CHAT_READ_TIMEOUT_MS, 'Activity overview took too long to load.');
  };

  const loadMemory = async () => {
    const cachedSnapshot = services.queryClient.peek<SageMemorySnapshot>(SAGE_MEMORY_QUERY_KEY) ?? memorySnapshot;
    return loadChatMemorySnapshot({
      services,
      memoryQueryKey: SAGE_MEMORY_QUERY_KEY,
      timeoutMs: CHAT_READ_TIMEOUT_MS,
      cachedSnapshot,
      normalizeSnapshot: normalizeSageMemorySnapshot,
      writeSnapshot: writeMemorySnapshot,
      withTimeout,
      shouldUseFallback: isTransientBackgroundReadError,
    });
  };

  const loadProfile = async () => {
    const cachedProfile = services.queryClient.peek<SageProfileSnapshot>(SAGE_PROFILE_QUERY_KEY) ?? profileSnapshot;
    return loadChatProfileSnapshot({
      services,
      timeoutMs: SAGE_SETUP_TIMEOUT_MS,
      cachedProfile,
      normalizeSnapshot: (payload) => payload === null
        ? cachedProfile
        : normalizeSageProfileSnapshot(payload),
      writeSnapshot: writeProfileSnapshot,
      setSetupState: setSageSetupState,
      setSetupMessage: setSageSetupMessage,
      humanizeFailure: humanizeSageSetupFailure,
      withTimeout,
      shouldUseFallback: isTransientBackgroundReadError,
    });
  };

  const refreshCanonicalState = async (requestedThreadId = activeThreadId) => {
    const threadFallback = services.queryClient.peek<CanonicalChatThreadState>(threadQueryKey(requestedThreadId))
      ?? normalizeCanonicalChatThread(null, requestedThreadId);
    const [nextThread] = await Promise.all([
      loadThread(requestedThreadId).catch(() => threadFallback),
      Promise.allSettled([
        loadOverview(),
        loadMemory(),
        loadProfile(),
      ]),
    ]);
    return nextThread;
  };

  // ── SSE: live refresh when channel messages arrive on sage-main ──
  useEffect(() => {
    const workspaceId = bootstrap.workspace.id;
    if (!workspaceId) return;
    let es: EventSource | null = null;
    try {
      es = services.client.openSageTurnStream(workspaceId);
      es.addEventListener('new_turn', () => {
        // Only refresh if user is not mid-turn (no pending message, not sending)
        if (!pendingUserMessageRef.current && !isSendingRef.current) {
          refreshCanonicalState('sage-main').catch(() => {});
        }
      });
      es.addEventListener('error', () => {
        // EventSource will auto-reconnect; no action needed
      });
    } catch {
      // SSE not supported or connection failed — silently degrade
    }
    return () => {
      try { es?.close(); } catch {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootstrap.workspace.id]);

  const submitBootstrapResponse = async () => {
    const answer = bootstrapAnswer.trim();
    if (!answer || isSubmittingBootstrap) {
      return;
    }
    setIsSubmittingBootstrap(true);
    setStatusMessage(null);
    try {
      const payload = await services.client.answerSageProfileBootstrap({ answer });
      const nextProfile = normalizeSageProfileSnapshot(payload);
      writeProfileSnapshot(nextProfile);
      setBootstrapAnswer('');
      setStatusMessage(nextProfile.bootstrap.complete ? 'Sage is ready.' : null);
    } catch (error) {
      setSageSetupState('unavailable');
      setSageSetupMessage(humanizeSageSetupFailure(error, 'save'));
      setStatusMessage(null);
    } finally {
      setIsSubmittingBootstrap(false);
    }
  };

  const retrySageSetup = async () => {
    if (isRetryingSageSetup) {
      return;
    }
    setIsRetryingSageSetup(true);
    setSageSetupState('loading');
    setSageSetupMessage(null);
    setStatusMessage(null);
    try {
      await loadProfile();
    } finally {
      setIsRetryingSageSetup(false);
    }
  };

  useEffect(() => () => {
    if (transcriptScrollFrameRef.current !== null) {
      window.cancelAnimationFrame(transcriptScrollFrameRef.current);
      transcriptScrollFrameRef.current = null;
    }
  }, []);

  useEffect(() => {
    const handleCommandShortcut = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || isTextEditingTarget(event.target)) {
        return;
      }
      const isCommandK = event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey);
      if (!isCommandK) {
        return;
      }
      event.preventDefault();
      setWorkspaceCommandPaletteOpen((current) => !current);
    };
    window.addEventListener('keydown', handleCommandShortcut);
    return () => {
      window.removeEventListener('keydown', handleCommandShortcut);
    };
  }, []);

  const openCreateMemory = (categoryId?: string) => {
    setMemoryDraft({
      ...defaultSageMemoryDraft(),
      category: categoryId || 'profile_fact',
    });
    setIsMemorySheetOpen(true);
  };

  const openEditMemory = (entry: WorkstationSageMemoryRecord) => {
    setMemoryDraft({
      entryId: readString(entry.id) || null,
      category: readString(entry.category) || 'profile_fact',
      title: readString(entry.title),
      content: readString(entry.content),
      pinned: Boolean(entry.pinned),
    });
    setIsMemorySheetOpen(true);
  };

  const submitMemoryDraft = async () => {
    if (mutatingMemory) {
      return;
    }
    const category = readString(memoryDraft.category);
    const title = readString(memoryDraft.title);
    const content = readString(memoryDraft.content);
    if (!category || !title || !content) {
      setStatusMessage('Memory entries need a category, title, and content.');
      return;
    }
    setMutatingMemory(memoryDraft.entryId || 'new');
    setStatusMessage(null);
    try {
      const payload = memoryDraft.entryId
        ? await services.client.updateSageMemoryEntry({
          entryId: memoryDraft.entryId,
          category,
          title,
          content,
          pinned: memoryDraft.pinned,
        })
        : await services.client.createSageMemoryEntry({
          category,
          title,
          content,
          pinned: memoryDraft.pinned,
        });
      writeMemorySnapshot(normalizeSageMemorySnapshot(payload));
      setIsMemorySheetOpen(false);
      setMemoryDraft(defaultSageMemoryDraft());
      setStatusMessage(memoryDraft.entryId ? 'Memory corrected.' : 'Memory saved.');
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Memory update failed.');
    } finally {
      setMutatingMemory(null);
    }
  };

  const toggleMemoryPinned = async (entry: WorkstationSageMemoryRecord) => {
    const entryId = readString(entry.id);
    if (!entryId || mutatingMemory) {
      return;
    }
    setMutatingMemory(entryId);
    setStatusMessage(null);
    try {
      const payload = await services.client.setSageMemoryEntryPinned({
        entryId,
        pinned: !Boolean(entry.pinned),
      });
      writeMemorySnapshot(normalizeSageMemorySnapshot(payload));
      setStatusMessage(Boolean(entry.pinned) ? 'Memory unpinned.' : 'Memory pinned.');
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Could not update memory pin state.');
    } finally {
      setMutatingMemory(null);
    }
  };

  const confirmDeleteMemory = async () => {
    if (!pendingDeleteMemoryId || mutatingMemory) {
      return;
    }
    setMutatingMemory(pendingDeleteMemoryId);
    setStatusMessage(null);
    try {
      const payload = await services.client.deleteSageMemoryEntry({
        entryId: pendingDeleteMemoryId,
      });
      writeMemorySnapshot(normalizeSageMemorySnapshot(payload));
      setPendingDeleteMemoryId(null);
      setStatusMessage('Memory forgotten.');
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Could not forget memory.');
    } finally {
      setMutatingMemory(null);
    }
  };

  const startNewThread = async (seed?: {
    title?: string;
    sourceRunId?: string | null;
    sourceThreadId?: string | null;
  }) => {
    if (isSending) {
      return;
    }
    setHasEnteredConversationFlow(true);
    const nextThreadId = `thread-${Date.now()}`;
    const previousThread = summarizeThreadForHistory(threadRef.current, activeThreadIdRef.current || activeThreadId);
    setDraft('');
    setStatusMessage(null);
    updatePendingUserMessage(null);
    setStreamingAssistantText('');
    setShowProjectedAssistant(false);
    setTimelineSettled(false);
    setLiveTimelineEvents([]);
    setLiveActivitySteps([]);
    setLiveTrace(null);
    setIsLoading(true);
    try {
      activeThreadIdRef.current = nextThreadId;
      setActiveThreadId(nextThreadId);
      const emptyThread = normalizeCanonicalChatThread(null, nextThreadId);
      emptyThread.session = null;
      services.queryClient.set(threadQueryKey(nextThreadId), emptyThread);
      setThread(emptyThread);
      const nextRecentThreads = [
        previousThread,
        ...recentThreads.filter((item) => item.threadId !== nextThreadId && item.threadId !== previousThread?.threadId),
      ].filter((item): item is RecentThreadSummary => item !== null).slice(0, 8);
      writeRecentThreads(nextRecentThreads);
      if (readString(seed?.sourceRunId) || readString(seed?.sourceThreadId)) {
        setStatusMessage('Started a new thread from recent activity.');
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Could not start a new thread.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    const cachedThread = services.queryClient.peek<CanonicalChatThreadState>(threadQueryKey(activeThreadId));
    if (cachedThread) {
      setThread(cachedThread);
    }
  }, [activeThreadId, services]);

  useEffect(() => {
    if (activityVersion === 0) {
      return;
    }
    const refreshes: Array<Promise<unknown>> = [loadOverview()];
    if (!isSending) {
      refreshes.push(refreshCanonicalState(activeThreadId));
    }
    void Promise.all(refreshes).catch((error) => {
      if (shouldSuppressBackgroundRefreshNotice(error)) {
        return;
      }
      setStatusMessage(error instanceof Error ? error.message : 'Chat refresh failed.');
    });
  }, [activeThreadId, activityVersion, isSending]);

  useEffect(() => {
    if (activityConnectionState !== 'closed' && notificationsConnectionState !== 'closed') {
      return;
    }
    setStatusMessage((current) => current ?? 'Connection lost. Live updates paused.');
  }, [activityConnectionState, notificationsConnectionState]);

  useEffect(() => {
    persistActiveThread(bootstrap.workspace.id, activeThreadId);
  }, [activeThreadId, bootstrap.workspace.id]);

  useEffect(() => subscribeWorkstationChatThreadSelected((detail) => {
    if (detail.workspaceId !== bootstrap.workspace.id) {
      return;
    }
    const nextThreadId = readString(detail.threadId);
    const forceOpen = detail.force === true;
    if (!nextThreadId || (nextThreadId === activeThreadIdRef.current && !forceOpen)) {
      return;
    }
    setHasEnteredConversationFlow(true);
    setStatusMessage(null);
    setSendFailureNotice(null);
    setShowProjectedAssistant(false);
    setTimelineSettled(false);
    setLiveTimelineEvents([]);
    transcriptForceStickRef.current = true;
    transcriptShouldStickRef.current = true;
    setShowTranscriptJump(false);
    activeThreadIdRef.current = nextThreadId;
    setActiveThreadId(nextThreadId);
    setIsLoading(true);
    void refreshCanonicalState(nextThreadId)
      .catch((error) => {
        setStatusMessage(error instanceof Error ? error.message : 'Could not open this thread.');
      })
      .finally(() => {
        setIsLoading(false);
      });
  }), [bootstrap.workspace.id]);

  useEffect(() => subscribeWorkstationChatNewThreadRequested((detail) => {
    if (detail.workspaceId !== bootstrap.workspace.id) {
      return;
    }
    void startNewThread();
  }), [bootstrap.workspace.id, startNewThread]);

  useEffect(() => {
    const rememberedThreadId = readPersistedActiveThread(bootstrap.workspace.id) ?? PRIMARY_THREAD_ID;
    if (rememberedThreadId !== activeThreadId) {
      setActiveThreadId(rememberedThreadId);
    }
  }, [activeThreadId, bootstrap.workspace.id]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined;
    }
    const storageKey = activeThreadStorageKey(bootstrap.workspace.id);
    const handleStorage = (event: StorageEvent) => {
      if (event.key !== storageKey) {
        return;
      }
      const nextThreadId = readString(event.newValue);
      if (!nextThreadId || nextThreadId === activeThreadId) {
        return;
      }
      setActiveThreadId(nextThreadId);
    };
    window.addEventListener('storage', handleStorage);
    return () => {
      window.removeEventListener('storage', handleStorage);
    };
  }, [activeThreadId, bootstrap.workspace.id]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        setIsLoading(true);
        await refreshCanonicalState(activeThreadId);
        if (!cancelled) {
          setStatusMessage(null);
        }
      } catch (error) {
        if (!cancelled) {
          if (shouldSuppressBackgroundRefreshNotice(error)) {
            setStatusMessage(null);
            return;
          }
          setStatusMessage(error instanceof Error ? error.message : 'Chat is unavailable right now.');
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeThreadId, bootstrap.workspace.id]);

  useEffect(() => {
    if (isSending) {
      return undefined;
    }
    const traceIds = Array.from(new Set(
      thread.messages
        .map(legacyTraceIdForReplay)
        .filter((traceId): traceId is string => Boolean(traceId)),
    ));
    const missingTraceIds = traceIds.filter((traceId) => (
      legacyTraceEventsByTraceId[traceId] === undefined
      && !pendingLegacyTraceIdsRef.current.has(traceId)
    ));
    if (missingTraceIds.length === 0) {
      return undefined;
    }

    let cancelled = false;
    missingTraceIds.forEach((traceId) => {
      pendingLegacyTraceIdsRef.current.add(traceId);
    });

    void Promise.all(missingTraceIds.map(async (traceId): Promise<[string, TimelineProjectionEvent[]]> => {
      try {
        const replay = await services.client.getTraceReplay({ traceId, allowMissing: true });
        return [traceId, safeTimelineEventsFromTraceReplay(replay)];
      } catch (error) {
        void error;
        // Legacy replay is a best-effort compatibility path. Chat history should
        // stay usable even when old trace detail is unavailable.
        return [traceId, []];
      } finally {
        pendingLegacyTraceIdsRef.current.delete(traceId);
      }
    })).then((entries) => {
      if (cancelled) {
        return;
      }
      setLegacyTraceEventsByTraceId((current) => {
        const next = { ...current };
        for (const [traceId, events] of entries) {
          next[traceId] = events;
        }
        return next;
      });
    });

    return () => {
      cancelled = true;
    };
  }, [isSending, legacyTraceEventsByTraceId, services.client, thread.messages]);

  const projectionOptions = useMemo(() => ({
    threadMessages: thread.messages,
    pendingUserMessage,
    isSending,
    liveTimelineEvents,
    legacyTraceEventsByTraceId,
    showProjectedAssistant,
    isSyntheticTranscriptMessage,
    canonicalIncludesMessage,
    isProviderGateTranscriptCell,
    isProviderGateSystemCell,
    projectedAssistantLooksSynthetic,
    readString,
  }), [
    thread.messages,
    pendingUserMessage,
    isSending,
    liveTimelineEvents,
    legacyTraceEventsByTraceId,
    showProjectedAssistant,
    isSyntheticTranscriptMessage,
    canonicalIncludesMessage,
    isProviderGateTranscriptCell,
    isProviderGateSystemCell,
    projectedAssistantLooksSynthetic,
    readString,
  ]);

  const {
    projectedTimelineProjection,
    projectedTimelineCells,
    projectedSystemCells,
    projectedAssistantCell,
    pinnedTimelineCells,
    pendingApprovalCells,
    visibleTranscriptCells,
  } = useWorkstationTimelineProjection(projectionOptions);
  const unifiedAgentActivityState = isSending
    ? (projectedTimelineProjection.agentActivityState === 'idle'
        ? 'thinking'
        : (projectedTimelineProjection.agentActivityState === 'done' || projectedTimelineProjection.agentActivityState === 'error') && !timelineSettled
          ? 'finalizing'
          : projectedTimelineProjection.agentActivityState)
    : 'idle';
  const chatActivityBusy = unifiedAgentActivityState !== 'idle'
    && unifiedAgentActivityState !== 'done'
    && unifiedAgentActivityState !== 'error';

  const hasConversationContent = visibleTranscriptCells.length > 0
    || Boolean(liveTrace);
  const showConversationContext = hasConversationContent || hasEnteredConversationFlow;
  const showFirstImpression = !showConversationContext;
  const bootstrapQuestion = profileSnapshot.bootstrap.current_question;
  const bootstrapComplete = profileSnapshot.bootstrap.complete;
  // Keep Sage chat usable even when profile bootstrap is incomplete.
  // Setup remains available in Memory instead of owning the first impression.
  const showSetupCardsInChat = false;
  const showSageSetupLoadingCard = showSetupCardsInChat && sageSetupState === 'loading';
  const showSageSetupUnavailableCard = showSetupCardsInChat && sageSetupState === 'unavailable';
  const showBootstrapCard = showSetupCardsInChat && sageSetupState === 'required' && !bootstrapComplete;
  const showBlankTranscript = !isLoading
    && visibleTranscriptCells.length === 0
    && !liveTrace;
  const transcriptScrollSignature = useMemo(
    () => [...visibleTranscriptCells, ...pinnedTimelineCells].map(transcriptCellScrollKey).join('|'),
    [pinnedTimelineCells, visibleTranscriptCells],
  );
  const scrollTranscriptToLatest = useCallback((behavior: ScrollBehavior = 'auto') => {
    const element = transcriptScrollRef.current;
    if (!element) {
      return;
    }
    transcriptShouldStickRef.current = true;
    setShowTranscriptJump(false);
    if (transcriptScrollFrameRef.current !== null) {
      window.cancelAnimationFrame(transcriptScrollFrameRef.current);
    }
    transcriptScrollFrameRef.current = window.requestAnimationFrame(() => {
      transcriptScrollFrameRef.current = null;
      element.scrollTo({
        top: element.scrollHeight,
        behavior,
      });
    });
  }, []);
  const handleTranscriptScroll = useCallback(() => {
    const atBottom = isChatTranscriptNearBottom(transcriptScrollRef.current);
    transcriptShouldStickRef.current = atBottom;
    if (atBottom) {
      setShowTranscriptJump(false);
    } else if (hasConversationContent) {
      setShowTranscriptJump(true);
    }
  }, [hasConversationContent]);
  useEffect(() => {
    const element = transcriptScrollRef.current;
    if (!element) {
      return;
    }
    const shouldFollow = transcriptForceStickRef.current || transcriptShouldStickRef.current;
    transcriptForceStickRef.current = false;
    if (shouldFollow) {
      scrollTranscriptToLatest('auto');
      return;
    }
    if (hasConversationContent && !isChatTranscriptNearBottom(element)) {
      setShowTranscriptJump(true);
    }
  }, [hasConversationContent, scrollTranscriptToLatest, transcriptScrollSignature]);
  const latestRun = runs[0];
  const assistantTurnCount = useMemo(
    () => thread.messages.filter((message) => message.role !== 'user').length,
    [thread.messages],
  );
  const runtimeCard = useMemo(
    () => summarizeRuntimeCard(bootstrap.runtime.runtimeTargets),
    [bootstrap.runtime.runtimeTargets],
  );
  const localRuntimeTarget = useMemo(
    () => localCompanionTarget(bootstrap.runtime.runtimeTargets),
    [bootstrap.runtime.runtimeTargets],
  );
  const localCompanionOnline = Boolean(
    localRuntimeTarget
    && localRuntimeTarget.online,
  );
  const localCompanionConnected = Boolean(
    localRuntimeTarget
    && localRuntimeTarget.available
    && localRuntimeTarget.online
    && localRuntimeTarget.healthy,
  );
  const gatewayReadinessOnline = readString(browserGatewayDoctor?.status).toLowerCase() === 'healthy';
  const localToolingOnline = localCompanionConnected;
  const gatewayToolingOnline = useMemo(
    () => localToolingOnline || gatewayReadinessOnline,
    [gatewayReadinessOnline, localToolingOnline],
  );
  const persistedSelectedModelId = useMemo(
    () => resolvePersistedSelectedModelId({
      providers: providerCatalog,
      profiles: providerProfiles,
      modelOptions,
    }),
    [modelOptions, providerCatalog, providerProfiles],
  );
  const selectedModelOption = useMemo(
    () => modelOptions.find((option) => option.id === selectedModel) ?? modelOptions[0] ?? {
      id: 'default',
      label: 'Platform AI',
      providerId: null,
      providerLabel: null,
      supportsReasoning: false,
      reasoningLevels: ['low', 'medium', 'high'],
      contextWindowTokens: null,
      routeProviderId: null,
      routeModelId: null,
      defaultReasoningEffort: 'medium',
      uiSection: 'system',
    },
    [modelOptions, selectedModel],
  );
  const effectiveSelectedModel = useMemo(
    () => modelOptions.some((option) => option.id === selectedModel)
      ? selectedModel
      : selectedModelOption.id,
    [modelOptions, selectedModel, selectedModelOption.id],
  );
  const selectedProviderContext = useMemo(
    () => resolveProviderModelContext({
      providers: providerCatalog,
      selectedModelId: effectiveSelectedModel,
      selectedModelLabel: selectedModelOption.label,
      selectedProviderId: selectedModelOption.providerId,
      modelOptions,
    }),
    [effectiveSelectedModel, modelOptions, providerCatalog, selectedModelOption.label, selectedModelOption.providerId],
  );
  const selectedProviderRecord = useMemo(
    () => {
      const routeProviderId = readString(selectedProviderContext.providerId);
      const catalogProviderId = readString(selectedModelOption.providerId);
      return providerCatalog.find((provider) => readString(provider.id) === routeProviderId)
        ?? providerCatalog.find((provider) => readString(provider.id) === catalogProviderId)
        ?? null;
    },
    [providerCatalog, selectedModelOption.providerId, selectedProviderContext.providerId],
  );
  const activeProviderSummary = useMemo(() => {
    const providerLabelById = new Map(
      providerCatalog.map((provider) => [readString(provider.id), readString(provider.label) || readString(provider.id)] as const),
    );
    const liveProviderId = readString(liveTrace?.trace?.provider);
    const liveModelId = readString(liveTrace?.trace?.model);
    if (liveProviderId) {
      const liveProvider = providerCatalog.find((provider) => readString(provider.id) === liveProviderId) ?? null;
      const label = providerSummaryLabel({
        provider: liveProvider,
        providerLabel: providerLabelById.get(liveProviderId) || liveProviderId,
        modelLabel: liveModelId || null,
      }) || providerLabelById.get(liveProviderId) || liveProviderId;
      return {
        label,
        connected: true,
      };
    }
    const selectedProviderLabel = selectedModelOption.uiSection === 'empyralis'
      ? `${selectedModelOption.label} · Workspace AI`
      : providerSummaryLabel({
          provider: selectedProviderRecord,
          providerLabel: selectedProviderContext.providerLabel,
          modelLabel: selectedProviderContext.modelLabel,
        }) || readString(selectedProviderContext.providerLabel);
    if (selectedProviderRecord) {
      const ready = providerReadyForChat(selectedProviderRecord, {
        gatewayToolingOnline,
      });
      return {
        label: ready
          ? selectedProviderLabel
          : `${selectedProviderLabel} · setup needed`,
        connected: ready,
      };
    }
    if (selectedProviderContext.providerLabel) {
      return {
        label: `${selectedProviderLabel} · setup needed`,
        connected: false,
      };
    }
    return {
      label: 'No AI model - Set up AI',
      connected: false,
    };
  }, [
    liveTrace?.trace?.model,
    liveTrace?.trace?.provider,
    gatewayToolingOnline,
    providerCatalog,
    selectedProviderRecord,
    selectedProviderContext.modelLabel,
    selectedProviderContext.providerLabel,
    selectedModelOption.label,
    selectedModelOption.uiSection,
  ]);
  const runtimeStatus = useMemo(() => {
    const providerPath = providerPathLabel(selectedProviderRecord);
    const providerId = readString(selectedProviderRecord?.id).toLowerCase();
    const credentialPlane = readString(selectedProviderRecord?.credential_plane).toLowerCase();
    const localProvider = providerId === 'ollama'
      || providerId === 'openai-codex'
      || selectedProviderRecord?.local_only === true
      || credentialPlane === 'local_runtime';
    if (!selectedProviderRecord) {
      if (selectedModelOption.uiSection === 'empyralis') {
        return { label: 'Workspace AI', tone: 'success' as const };
      }
      if (selectedModelOption.uiSection === 'local_ai') {
        return {
          label: localToolingOnline ? 'Agent Computer' : 'Agent Computer offline',
          tone: localToolingOnline ? 'success' as const : 'warning' as const,
        };
      }
      if (selectedModelOption.uiSection === 'my_api_key') {
        return { label: 'My API Key', tone: 'neutral' as const };
      }
      if (selectedModelOption.uiSection === 'my_ai_account') {
        return { label: 'My AI Account', tone: 'neutral' as const };
      }
      return { label: 'No AI model', tone: 'warning' as const };
    }
    if (localProvider && !localToolingOnline) {
      return { label: 'Agent Computer offline', tone: 'warning' as const };
    }
    if (localProvider) {
      return { label: providerPath ?? 'Agent Computer', tone: 'success' as const };
    }
    if (providerPath === 'Workspace AI') {
      return { label: 'Workspace AI', tone: 'success' as const };
    }
    if (providerPath === 'Your AI account' || providerPath === 'Ollama Cloud') {
      return { label: providerPath, tone: 'neutral' as const };
    }
    return { label: 'Cloud AI', tone: 'neutral' as const };
  }, [localToolingOnline, selectedModelOption.uiSection, selectedProviderRecord]);
  const workspaceAiRouteLabel = useMemo(() => {
    const route = workspaceAiRoute?.workspaceDefault;
    if (!route || typeof route !== 'object') {
      return null;
    }
    return readString(route.label) || null;
  }, [workspaceAiRoute]);
  const runtimeTrustZone = useMemo<ChatRuntimeTrustZone>(
    () => resolveRuntimeTrustZone(localRuntimeTarget, machineTrust),
    [localRuntimeTarget, machineTrust],
  );
  const permissionPolicyContext = useMemo(
    () => buildChatPermissionPolicyContext({
      mode: autonomyMode,
      runtimeTrustZone,
      machineTrust,
    }),
    [autonomyMode, machineTrust, runtimeTrustZone],
  );
  const integrationsHref = useMemo(
    () => routeManifest.routeIndex.integrations?.href ?? `/w/${encodeURIComponent(bootstrap.workspace.id)}/integrations`,
    [bootstrap.workspace.id, routeManifest.routeIndex.integrations],
  );
  const settingsHref = useMemo(
    () => routeManifest.routeIndex.settings?.href ?? `/w/${encodeURIComponent(bootstrap.workspace.id)}/settings`,
    [bootstrap.workspace.id, routeManifest.routeIndex.settings],
  );
  const hardwareHref = useMemo(
    () => routeManifest.routeIndex.hardware?.href ?? `/w/${encodeURIComponent(bootstrap.workspace.id)}/hardware`,
    [bootstrap.workspace.id, routeManifest.routeIndex.hardware],
  );
  const requestedPrefillPrompt = useMemo(
    () => readString(searchParams.get('prompt')),
    [searchParams],
  );
  useEffect(() => {
    if (!requestedPrefillPrompt || draft.trim()) {
      return;
    }
    setDraft(requestedPrefillPrompt);
    if (typeof window !== 'undefined') {
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.delete('prompt');
      window.history.replaceState(window.history.state, '', nextUrl.toString());
    }
  }, [draft, requestedPrefillPrompt, setDraft]);
  const sageSlashCommands = useMemo<ComposerSlashCommand[]>(
    () => (
      SAGE_COMMAND_CATALOG.map((command) => ({
        id: command.id,
        slash: command.slash,
        title: command.title,
        description: command.description,
        category: 'Sage',
        keywords: command.keywords,
        icon: command.icon,
      }))
    ),
    [],
  );
  const workspaceCommandItems = useMemo(
    () => SAGE_WORKSPACE_COMMAND_CATALOG.flatMap((command) => {
      const route = routeManifest.routeIndex[command.routeId];
      if (!route) {
        return [];
      }
      return [{
        ...command,
        href: route.href,
      }];
    }),
    [routeManifest.routeIndex],
  );
  const composerModelOptions = useMemo(
    () => {
      const providerById = new Map(
        providerCatalog.map((provider) => [readString(provider.id), provider] as const),
      );
      const groupedOptions: ({ value: string; label: string; disabled: boolean } | { label: string; options: { value: string; label: string; disabled: boolean }[] })[] = [];
      const sectionOrder: Array<'empyralis' | 'local_ai' | 'my_api_key' | 'my_ai_account'> = [
        'empyralis',
        'local_ai',
        'my_api_key',
        'my_ai_account',
      ];
      for (const section of sectionOrder) {
        const sectionItems = modelOptions
          .filter((option) => option.id !== 'default' && option.uiSection === section)
          .map((option) => ({
            value: option.id,
            label: modelOptionDisplayLabel(option, providerById.get(readString(option.providerId)) ?? null),
            disabled: !option.id,
          }));
        if (sectionItems.length === 0) {
          continue;
        }
        groupedOptions.push({
          label: section === 'empyralis' ? 'Workspace AI' : USER_OWNED_SECTION_LABELS[section],
          options: sectionItems,
        });
      }
      if (groupedOptions.length === 0) {
        const defaultOption = modelOptions.find((option) => option.id === 'default') ?? null;
        if (defaultOption) {
          return [{
            value: defaultOption.id,
            label: modelOptionDisplayLabel(defaultOption, providerById.get(readString(defaultOption.providerId)) ?? null),
            disabled: !defaultOption.id,
          }];
        }
      }
      return groupedOptions;
    },
    [modelOptions, providerCatalog],
  );
  const reasoningOptions = useMemo(
    () => selectedModelOption.reasoningLevels.map((value) => ({
      value,
      label: reasoningLabel(value),
    })),
    [selectedModelOption.reasoningLevels],
  );
  const nextStepTitle = latestRun
    ? 'Task is in progress'
    : 'Sage is ready for the next turn';
  const nextStepMeta = latestRun
    ? readString(latestRun.status) || 'unknown'
    : 'Idle';
  const handleSlashCommandSelect = useCallback((composerCommand: ComposerSlashCommand) => {
    const command = SAGE_COMMAND_CATALOG.find((item) => item.id === composerCommand.id) as SageCommandMetadata | undefined;
    if (!command) {
      return;
    }
    setDraft('');
    switch (command.actionKind) {
      case 'open_usage':
        router.push(`${settingsHref}?section=usage`);
        return;
      case 'open_tools':
        router.push(`${integrationsHref}?section=apps`);
        return;
      case 'open_runtime':
        router.push(`${integrationsHref}?section=ai-runtime`);
        return;
      case 'run_doctor':
        void refreshBrowserGatewayReadiness();
        setStatusMessage('Sage setup check refreshed. Open Agent Computer status for readiness checks.');
        router.push(hardwareHref);
        return;
      case 'open_status':
      default:
        setStatusMessage(`${runtimeStatus.label}. Open Agent Computer status for readiness checks.`);
        router.push(hardwareHref);
    }
  }, [hardwareHref, integrationsHref, refreshBrowserGatewayReadiness, router, runtimeStatus.label, setDraft, setStatusMessage, settingsHref]);
  const handleWorkspaceCommandSelect = useCallback((command: SageWorkspaceCommandMetadata) => {
    setWorkspaceCommandPaletteOpen(false);
    const href = routeManifest.routeIndex[command.routeId]?.href ?? null;
    if (href) {
      router.push(href);
    }
  }, [routeManifest.routeIndex, router]);
  const memoryMeta = assistantTurnCount > 0
    ? `${assistantTurnCount} Sage repl${assistantTurnCount === 1 ? 'y' : 'ies'} retained`
    : 'The first turn will establish memory';
  const memoryItems = memorySnapshot.items;
  const pendingDeleteMemory = pendingDeleteMemoryId
    ? memoryItems.find((item) => readString(item.id) === pendingDeleteMemoryId) ?? null
    : null;
  const [modelCanvasPickerOpen, setModelCanvasPickerOpen] = useState(false);
  const [modelPickerSubpanel, setModelPickerSubpanel] = useState<'model' | 'provider' | null>(null);
  const [hardwareCanvasPickerOpen, setHardwareCanvasPickerOpen] = useState(false);
  const [activeModelPickerProviderId, setActiveModelPickerProviderId] = useState<SageModelPickerProviderId>('empyralis');
  const [expandedModelPickerProviderIds, setExpandedModelPickerProviderIds] = useState<readonly SageModelPickerProviderId[]>([]);
  const modelCanvasPickerRef = useRef<HTMLDivElement | null>(null);
  const hardwareCanvasPickerRef = useRef<HTMLDivElement | null>(null);
  const statusNotice = useMemo(
    () => (statusMessage ? classifyStatusNotice(statusMessage) : null),
    [statusMessage],
  );
  const sageAgentComputerSelectionRecord = useMemo(
    () => readObject(sageAgentComputerSelection?.selection),
    [sageAgentComputerSelection],
  );
  const sageAgentComputerGatewayRecord = useMemo(
    () => readObject(sageAgentComputerSelection?.gateway),
    [sageAgentComputerSelection],
  );
  const sageAgentComputerSelectionMetadata = useMemo(
    () => readObject(sageAgentComputerSelectionRecord.metadata),
    [sageAgentComputerSelectionRecord],
  );
  const sageAgentComputerGatewayMetadata = useMemo(
    () => readObject(sageAgentComputerGatewayRecord.metadata),
    [sageAgentComputerGatewayRecord],
  );
  const selectedSageAgentComputerId = (
    readString(sageAgentComputerGatewayRecord.gateway_id)
    || readString(sageAgentComputerSelectionRecord.selected_gateway_id)
    || readString(sageAgentComputerSelection?.selected_gateway_id)
  );
  const localRuntimeTargetId = localCompanionOnline ? readString(localRuntimeTarget?.id) : null;
  const selectedHardwareRuntimeTarget = useMemo(() => {
    return localCompanionConnected && localRuntimeTargetId ? localRuntimeTargetId : 'cloud';
  }, [localCompanionConnected, localRuntimeTargetId]);
  const agentComputerDeviceLabel = useMemo(
    () => (
      readString(localRuntimeTarget?.sampleAttachmentLabel || localRuntimeTarget?.label)
      || localDevicePlatformLabel(desktop.platform, desktop.localCompanion.label)
      || 'Mac'
    ),
    [desktop.localCompanion.label, desktop.platform, localRuntimeTarget],
  );
  const agentComputerHeaderLabel = useMemo(() => {
    if (localCompanionConnected) {
      return agentComputerDeviceLabel;
    }
    if (localRuntimeTarget) {
      return 'Offline';
    }
    return 'Not selected';
  }, [agentComputerDeviceLabel, localCompanionConnected, localRuntimeTarget]);
  const agentComputerMenuStatus = useMemo(() => {
    if (localCompanionConnected) {
      return {
        label: 'Connected',
        detail: agentComputerDeviceLabel,
        tone: 'ready' as const,
      };
    }
    if (localCompanionOnline) {
      return {
        label: 'Online',
        detail: agentComputerDeviceLabel,
        tone: 'warning' as const,
      };
    }
    return {
      label: 'Not connected',
      detail: 'Connect Agent Computer before hardware permissions are active.',
      tone: 'setup' as const,
    };
  }, [agentComputerDeviceLabel, localCompanionConnected, localCompanionOnline]);
  const canvasModelOptions = useMemo<SageCompanyModelOption[]>(
    () => ([
      { id: 'light', label: 'Light' },
      { id: 'pro', label: 'Pro' },
      { id: 'max', label: 'Max' },
    ] as const).map((tier) => {
      const catalogOption = modelOptions.find((option) => option.id === tier.id && option.uiSection === 'empyralis') ?? null;
      return {
        id: tier.id,
        label: catalogOption?.label ?? tier.label,
        optionId: catalogOption?.id ?? '',
        selected: catalogOption?.id === effectiveSelectedModel,
        disabled: !catalogOption,
      };
    }),
    [effectiveSelectedModel, modelOptions],
  );
  const selectedCanvasModelLabel = useMemo(
    () => canvasModelOptions.find((option) => option.id === effectiveSelectedModel)?.label
      ?? selectedModelOption.label
      ?? 'Platform AI',
    [canvasModelOptions, effectiveSelectedModel, selectedModelOption.label],
  );
  const modelPickerProviderPanels = useMemo<SageModelPickerProviderPanel[]>(
    () => SAGE_MODEL_PICKER_PROVIDERS.flatMap((pickerProvider): SageModelPickerProviderPanel[] => {
      if (pickerProvider.id === 'empyralis') {
        return [{
          id: pickerProvider.id,
          label: pickerProvider.label,
          image: SAGE_MODEL_PICKER_PROVIDER_IMAGES[pickerProvider.id],
          models: canvasModelOptions.map((model) => ({
            id: `empyralis:${model.id}`,
            modelId: model.id,
            label: model.label,
            description: '',
            optionId: model.optionId,
            selected: model.selected,
          })),
        }];
      }
      const matchingProviders = providerCatalog.filter((provider) =>
        providerMatchesPickerProvider(provider, pickerProvider));
      const models = matchingProviders.flatMap((provider) => {
        const providerId = readString(provider.id);
        const providerLabel = readString(provider.label) || pickerProvider.label;
        const providerModels = Array.isArray(provider.models)
          ? provider.models.filter((item): item is ProviderCatalogModelRecord => Boolean(item) && typeof item === 'object')
          : [];

        return providerModels.flatMap((model) => {
          const modelId = readString(model.id);
          if (!providerId || !modelId) {
            return [];
          }
          const matchingOption = modelOptions.find((option) =>
            readString(option.routeProviderId || option.providerId) === providerId
            && readString(option.routeModelId || option.id) === modelId) ?? null;
          if (!matchingOption) {
            return [];
          }
          const selectedRouteProviderId = readString(selectedModelOption.routeProviderId || selectedModelOption.providerId);
          const selectedRouteModelId = readString(selectedModelOption.routeModelId || selectedModelOption.id);
          const selected = matchingOption.id === effectiveSelectedModel
            || (selectedRouteProviderId === providerId && selectedRouteModelId === modelId);
          return [{
            id: `${providerId}:${modelId}`,
            modelId,
            label: readString(model.label) || modelId,
            description: catalogModelDescription(model, providerLabel),
            optionId: matchingOption.id,
            selected,
          }];
        });
      });
      if (models.length === 0) {
        return [];
      }
      return [{
        id: pickerProvider.id,
        label: pickerProvider.label,
        image: SAGE_MODEL_PICKER_PROVIDER_IMAGES[pickerProvider.id],
        models,
      }];
    }),
    [canvasModelOptions, effectiveSelectedModel, modelOptions, providerCatalog, selectedModelOption.id, selectedModelOption.providerId, selectedModelOption.routeModelId, selectedModelOption.routeProviderId],
  );
  const selectedModelPickerProviderId = useMemo<SageModelPickerProviderId>(() => {
    if (selectedModelOption.uiSection === 'empyralis') {
      return 'empyralis';
    }
    const routeProviderId = readString(selectedModelOption.routeProviderId || selectedModelOption.providerId);
    const selectedProvider = routeProviderId
      ? providerCatalog.find((provider) => readString(provider.id) === routeProviderId) ?? null
      : null;
    const matchingDefinition = selectedProvider
      ? SAGE_MODEL_PICKER_PROVIDERS.find((pickerProvider) =>
        providerMatchesPickerProvider(selectedProvider, pickerProvider))
      : null;
    return matchingDefinition?.id ?? 'empyralis';
  }, [providerCatalog, selectedModelOption.providerId, selectedModelOption.routeProviderId, selectedModelOption.uiSection]);
  useEffect(() => {
    if (!modelCanvasPickerOpen) {
      setModelPickerSubpanel(null);
      setExpandedModelPickerProviderIds([]);
      return;
    }
    setModelPickerSubpanel(null);
    setExpandedModelPickerProviderIds([]);
    setActiveModelPickerProviderId(selectedModelPickerProviderId);
  }, [modelCanvasPickerOpen, selectedModelPickerProviderId]);
  const activeModelPickerProvider = modelPickerProviderPanels.find((provider) =>
    provider.id === activeModelPickerProviderId) ?? modelPickerProviderPanels[0] ?? null;
  const activeModelPickerModelView = useMemo(() => {
    if (!activeModelPickerProvider) {
      return {
        expanded: false,
        hiddenCount: 0,
        visibleModels: [] as SageModelPickerModel[],
      };
    }
    const selectableModels = activeModelPickerProvider.models.filter((model) => Boolean(model.optionId));
    const rankedModels = selectableModels
      .map((model, index) => ({
        index,
        model,
        rank: officialModelRank(activeModelPickerProvider.id, model),
      }))
      .filter((item) => item.rank >= 0)
      .sort((left, right) => left.rank - right.rank || left.index - right.index)
      .map((item) => item.model);
    const initialModels = rankedModels.length > 0
      ? rankedModels
      : selectableModels.slice(0, 4);
    const initialModelIds = new Set(initialModels.map((model) => model.id));
    const overflowModels = selectableModels.filter((model) => !initialModelIds.has(model.id));
    const expanded = expandedModelPickerProviderIds.includes(activeModelPickerProvider.id);
    return {
      expanded,
      hiddenCount: overflowModels.length,
      visibleModels: expanded ? [...initialModels, ...overflowModels] : initialModels,
    };
  }, [activeModelPickerProvider, expandedModelPickerProviderIds]);
  useEffect(() => {
    if (!hardwareCanvasPickerOpen && !modelCanvasPickerOpen) {
      return undefined;
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (
        hardwareCanvasPickerRef.current?.contains(target)
        || modelCanvasPickerRef.current?.contains(target)
      ) {
        return;
      }
      setModelCanvasPickerOpen(false);
      setModelPickerSubpanel(null);
      setHardwareCanvasPickerOpen(false);
    };
    window.addEventListener('pointerdown', handlePointerDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [hardwareCanvasPickerOpen, modelCanvasPickerOpen]);
  const handleReasoningEffortChange = useCallback((nextValue: string) => {
    if (selectedModelOption.reasoningLevels.includes(nextValue as ChatReasoningEffort)) {
      setReasoningEffort(nextValue as ChatReasoningEffort);
    }
  }, [selectedModelOption.reasoningLevels, setReasoningEffort]);
  const seedDraftIfEmpty = useCallback((nextDraft: string) => {
    setDraft((current) => (current.trim() ? current : nextDraft));
  }, [setDraft]);
  const handleComposerFilesSelected = useCallback(async (selectedFiles: File[]) => {
    if (selectedFiles.length === 0) {
      return;
    }
    
    const textTypes = /\.(md|txt|json|csv|xml|yaml|yml|toml|ini|cfg|log|sh|bash|py|js|ts|jsx|tsx|rb|go|rs|java|c|cpp|h|hpp|swift|php|pl|lua|r|sql|css|scss|less|html|htm|svg|env|gitignore|dockerfile|Makefile|cmake|gradle|tf|hcl)$/i;
    const imageTypes = /\.(png|jpg|jpeg|gif|webp|bmp)$/i;
    const parts: string[] = [];
    const names: string[] = [];

    for (const file of selectedFiles) {
      names.push(file.name.trim());
      if (textTypes.test(file.name)) {
        try {
          const text = await file.text();
          const truncated = text.length > 10000 ? text.slice(0, 10000) + "\n...[truncated]" : text;
          parts.push(truncated);
        } catch {
          parts.push("(Could not read " + file.name + ")");
        }
      } else {
        // Images and other binary files — do not inject anything into the draft.
        // The user must describe what they uploaded or type their request manually.
      }
    }

    const content = parts.join("\n\n");
    seedDraftIfEmpty(content ? content + "\n\n" : "");
  }, [seedDraftIfEmpty]);
  const sageCapabilityItems = useMemo<ComposerCapabilityItem[]>(() => {
    const openApps = () => {
      router.push(`${integrationsHref}?section=apps`);
    };
    const openComputer = () => {
      router.push(hardwareHref);
    };
    return [
      {
        id: 'connect_apps',
        title: 'Apps',
        detail: 'Connect and manage your apps',
        onSelect: openApps,
      },
      {
        id: 'use_my_computer',
        title: 'Use my computer',
        detail: 'Local files, apps, browser, terminal, or personal channels',
        onSelect: openComputer,
      },
    ];
  }, [hardwareHref, integrationsHref, router]);
  const defaultReasoningEffort = useMemo<ChatReasoningEffort>(
    () => (selectedModelOption.defaultReasoningEffort
      && selectedModelOption.reasoningLevels.includes(selectedModelOption.defaultReasoningEffort)
      ? selectedModelOption.defaultReasoningEffort
      : selectedModelOption.reasoningLevels.includes('medium')
      ? 'medium'
      : selectedModelOption.reasoningLevels[0] ?? 'medium'),
    [selectedModelOption.defaultReasoningEffort, selectedModelOption.reasoningLevels],
  );
  const contextReasoningLabel = useMemo(() => {
    switch (reasoningEffort) {
      case 'medium':
        return 'Normal';
      case 'high':
      case 'xhigh':
        return 'Extended thinking';
      case 'minimal':
        return 'Minimal';
      case 'low':
        return 'Low';
      case 'none':
        return 'None';
      default:
        return reasoningLabel(reasoningEffort);
    }
  }, [reasoningEffort]);
  const contextWindowLabel = useMemo(
    () => formatContextWindowLabel(selectedModelOption.contextWindowTokens),
    [selectedModelOption.contextWindowTokens],
  );
  const contextDeviceLabel = useMemo(() => {
    if (!desktop.localCompanion.present || !desktop.localCompanion.online) {
      return null;
    }
    return `${localDevicePlatformLabel(desktop.platform, desktop.localCompanion.label)} connected`;
  }, [desktop.localCompanion.label, desktop.localCompanion.online, desktop.localCompanion.present, desktop.platform]);
  const readinessPills = useMemo<SageReadinessPill[]>(() => {
    const pills: SageReadinessPill[] = [];
    if (!selectedProviderContext.providerLabel) {
      pills.push({
        id: 'provider',
        label: 'AI: Connect model',
        tone: 'danger',
        target: 'integrations',
      });
    }
    if (!localToolingOnline) {
      pills.push({
        id: 'gateway',
        label: 'Agent Computer: Offline',
        tone: 'danger',
        target: 'integrations',
      });
    }
    const browserPill = browserReadinessPill(browserGatewayDoctor, {
      gatewayOnline: gatewayReadinessOnline,
    });
    if (browserPill) {
      pills.push(browserPill);
    }
    return pills;
  }, [browserGatewayDoctor, localToolingOnline, selectedProviderContext.providerLabel]);
  const hostedCreditState = useMemo(
    () => normalizeHostedCreditStateForChat(billingSummary),
    [billingSummary],
  );
  const preRunCostEstimate = useMemo<ComposerPreRunCostEstimate | null>(
    () => buildPreRunCostEstimate({
      selectedModelOption,
      selectedExecutionPlacement,
      draft,
      hostedCreditState,
    }),
    [draft, hostedCreditState, selectedExecutionPlacement, selectedModelOption],
  );
  const showContextStrip = false;
  const showHeaderReadinessStrip = false;

  useEffect(() => {
    let cancelled = false;
    void refreshProviderCatalog()
      .catch(() => undefined)
      .finally(() => {
        if (cancelled) {
          return;
        }
      });
    void refreshToolingState().catch(() => undefined);
    void refreshBrowserGatewayReadiness().catch(() => undefined);
    void refreshBillingSummary().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [refreshBillingSummary, refreshBrowserGatewayReadiness, refreshProviderCatalog, refreshToolingState]);

  useEffect(() => subscribeWorkstationProviderChanged((detail) => {
    if (detail.workspaceId !== bootstrap.workspace.id) {
      return;
    }
    void refreshProviderCatalog();
    void refreshToolingState();
    void refreshBrowserGatewayReadiness();
    void refreshBillingSummary();
  }), [bootstrap.workspace.id, refreshBillingSummary, refreshBrowserGatewayReadiness, refreshProviderCatalog, refreshToolingState]);

  useEffect(() => {
    if (typeof document === 'undefined') {
      return () => {};
    }
    const handleFocus = () => {
      void refreshProviderCatalog();
      void refreshToolingState();
      void refreshBrowserGatewayReadiness();
      void refreshBillingSummary();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void refreshProviderCatalog();
        void refreshToolingState();
        void refreshBrowserGatewayReadiness();
        void refreshBillingSummary();
      }
    };
    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [refreshBillingSummary, refreshBrowserGatewayReadiness, refreshProviderCatalog, refreshToolingState]);

  useEffect(() => {
    if (isPersistingModelSelection) {
      return;
    }
    const nextSelectedModel = modelOptions.some((option) => option.id === persistedSelectedModelId)
      ? persistedSelectedModelId
      : modelOptions[0]?.id ?? '';
    if (nextSelectedModel !== selectedModel) {
      setSelectedModel(nextSelectedModel);
    }
  }, [isPersistingModelSelection, modelOptions, persistedSelectedModelId, selectedModel]);

  useEffect(() => {
    if (!selectedModelOption.reasoningLevels.includes(reasoningEffort)) {
      setReasoningEffort(defaultReasoningEffort);
    }
  }, [defaultReasoningEffort, reasoningEffort, selectedModelOption.reasoningLevels]);

  const handleModelChange = useCallback((nextModelId: string) => {
    if (!nextModelId || nextModelId === selectedModel || isSending || isPersistingModelSelection) {
      return;
    }
    const previousModelId = selectedModel;
    const nextOption = modelOptions.find((option) => option.id === nextModelId) ?? null;
    setSelectedModel(nextModelId);
    if (
      nextOption?.defaultReasoningEffort
      && nextOption.reasoningLevels.includes(nextOption.defaultReasoningEffort)
    ) {
      setReasoningEffort(nextOption.defaultReasoningEffort);
    }
    setStatusMessage(null);
    setSendFailureNotice(null);
    setIsPersistingModelSelection(true);
    void persistSelectedModelPreference(nextModelId)
      .then(async (persisted) => {
        if (!persisted) {
          setSelectedModel(previousModelId);
          setStatusMessage('This AI connection cannot save a workspace model preference yet.');
          return;
        }
        services.queryClient.invalidate('chat:provider-catalog');
        await refreshProviderCatalog();
      })
      .catch((error) => {
        setSelectedModel(previousModelId);
        setStatusMessage(error instanceof Error ? error.message : 'Could not update the model preference.');
      })
      .finally(() => {
        setIsPersistingModelSelection(false);
      });
  }, [
    isPersistingModelSelection,
    isSending,
    persistSelectedModelPreference,
    refreshProviderCatalog,
    selectedModel,
    setReasoningEffort,
    services.queryClient,
    modelOptions,
  ]);

  const finalizePartialAssistantResponse = useCallback((threadId: string) => {
    const partialText = readString(streamingAssistantTextRef.current);
    if (!partialText) {
      return;
    }
    const currentThread = threadRef.current;
    const incompleteMessage = createIncompleteAssistantMessage(partialText, threadId, {
      trace_id: readString(liveTrace?.traceId),
      effective_provider: readString(liveTrace?.trace?.provider),
      effective_model: readString(liveTrace?.trace?.model),
    });
    if (!incompleteMessage) {
      return;
    }
    writeThreadState({
      ...currentThread,
      threadId,
      messages: [...currentThread.messages, incompleteMessage],
      session: currentThread.session,
    });
    setStreamingAssistantText('');
  }, [liveTrace?.trace?.model, liveTrace?.trace?.provider, liveTrace?.traceId]);

  const stopStreamingResponse = useCallback(() => {
    if (
      !streamInFlightRef.current
      && !submitInFlightRef.current
      && !streamAbortHandleRef.current
      && !activeTurnRequestIdRef.current
    ) {
      return;
    }
    activeTurnRequestIdRef.current = null;
    streamAbortRequestedRef.current = true;
    streamAbortHandleRef.current?.abort();
    streamAbortHandleRef.current = null;
    setShowProjectedAssistant(false);
    setTimelineSettled(true);
    finalizePartialAssistantResponse(activeThreadIdRef.current);
    setLiveActivitySteps((current) => settleLiveActivitySteps(current, 'done'));
    streamInFlightRef.current = false;
    submitInFlightRef.current = false;
    setIsSending(false);
  }, [finalizePartialAssistantResponse]);

  useEffect(() => {
    if (!isSending) {
      return undefined;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return;
      }
      event.preventDefault();
      stopStreamingResponse();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isSending, stopStreamingResponse]);

  useEffect(() => {
    if (!isSending) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      if (!activeTurnRequestIdRef.current) {
        stopStreamingResponse();
        return;
      }
      const lastActivityAt = lastTurnActivityAtRef.current;
      const inactiveFor = lastActivityAt > 0 ? Date.now() - lastActivityAt : 0;
      if (inactiveFor < CHAT_THINKING_RECOVERY_MS) {
        return;
      }
      stopStreamingResponse();
      setSendFailureNotice({
        message: 'Sage stopped waiting on a stalled response. Your draft is restored so you can try again.',
        retryable: true,
        retryDraft: pendingUserMessageRef.current?.content ?? draft,
      });
    }, 5_000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [draft, isSending, stopStreamingResponse]);

  const handleDraftChange = useCallback((nextDraft: string) => {
    setDraft(nextDraft);
    if (nextDraft.trim()) {
      setSendFailureNotice(null);
      setStatusMessage(null);
    }
  }, [setDraft, setSendFailureNotice, setStatusMessage]);

  const sendMessage = async () => {
    const outboundMessage = draft.trim();
    if ((!outboundMessage && attachments.length === 0) || isSending || submitInFlightRef.current) {
      return;
    }
    const sessionCommand = resolveSageCommandBySlash(outboundMessage);
    if (sessionCommand) {
      setDraft('');
      switch (sessionCommand.actionKind) {
        case 'open_usage':
          router.push(`${settingsHref}?section=usage`);
          break;
        case 'open_tools':
          router.push(`${integrationsHref}?section=apps`);
          break;
        case 'open_runtime':
          router.push(`${integrationsHref}?section=ai-runtime`);
          break;
        case 'run_doctor':
          void refreshBrowserGatewayReadiness();
          setStatusMessage('Sage setup check refreshed. Open Agent Computer status for readiness checks.');
          router.push(hardwareHref);
          break;
        case 'open_status':
        default:
          setStatusMessage(`${runtimeStatus.label}. Open Agent Computer status for readiness checks.`);
          router.push(hardwareHref);
      }
      return;
    }
    const displayMessage = outboundMessage || "";
    submitInFlightRef.current = true;
    transcriptForceStickRef.current = true;
    transcriptShouldStickRef.current = true;
    setShowTranscriptJump(false);
    const resolvedProviderId = readString(selectedProviderContext.providerId) || null;
    const resolvedModelId = readString(selectedProviderContext.modelId)
      || (effectiveSelectedModel === 'default' ? null : effectiveSelectedModel);
    if (isSmallOllamaSelection(
      resolvedProviderId,
      resolvedModelId || effectiveSelectedModel,
      selectedProviderContext.modelLabel || selectedModelOption.label,
    )) {
      const warningKey = [
        'sage-small-ollama-model-warning',
        bootstrap.workspace.id,
        resolvedModelId || effectiveSelectedModel,
      ].join(':');
      try {
        if (window.sessionStorage.getItem(warningKey) !== '1') {
          window.sessionStorage.setItem(warningKey, '1');
          setSmallModelWarningVisible(true);
        }
      } catch {
        setSmallModelWarningVisible(true);
      }
    }
    setDraft('');
    setAttachments([]);
    setHasEnteredConversationFlow(true);
    setSendFailureNotice(null);
    setStatusMessage(null);

    const requestedThreadId = activeThreadId;
    const clientRequestId = createClientTurnRequestId();
    activeTurnRequestIdRef.current = clientRequestId;
    lastTurnActivityAtRef.current = Date.now();
    const assertTurnStillActive = () => {
      if (activeTurnRequestIdRef.current !== clientRequestId || streamAbortRequestedRef.current) {
        throw new WorkstationClientError('Turn stopped by user.', 0, null, 'stream_aborted', {
          retryable: true,
        });
      }
    };
    const pendingMessage = createPendingUserMessage(displayMessage, requestedThreadId, clientRequestId);
    const streamAbortHandle: WorkstationTurnStreamAbortHandle = {
      signal: new AbortController().signal,
      abort: () => undefined,
    };
    const streamAbortController = new AbortController();
    streamAbortHandle.signal = streamAbortController.signal;
    streamAbortHandle.abort = () => {
      streamAbortController.abort();
    };
    setIsSending(true);
    streamInFlightRef.current = false;
    streamAbortRequestedRef.current = false;
    streamAbortHandleRef.current = streamAbortHandle;
    setStatusMessage(null);
    setSendFailureNotice(null);
    updatePendingUserMessage(pendingMessage);
    setStreamingAssistantText('');
    setShowProjectedAssistant(true);
    setTimelineSettled(false);
    setLiveTimelineEvents([
      {
        type: 'user',
        payload: {
          content: displayMessage,
          attachments,
        },
      },
      {
        type: 'step',
        payload: {
          id: `thinking:${clientRequestId}`,
          kind: 'thinking',
          label: 'Thinking',
          detail: '',
          status: 'running',
        },
      },
    ]);
    setLiveActivitySteps([]);
    setLiveTrace({
      traceId: null,
      transport: 'external',
      trace: buildLiveTraceRecord({
        traceId: null,
        workspaceId: bootstrap.workspace.id,
        threadId: requestedThreadId,
        rootAgentId: 'sage',
      }),
      events: [],
    });

    try {
      await new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => {
          resolve();
        });
      });
      assertTurnStillActive();

      let session = await services.client.createSession({
        actor,
        threadId: requestedThreadId,
        channel: 'web',
        source: 'workstation_chat_pane',
        forceNew: false,
        existingSession: thread.session,
      });
      assertTurnStillActive();
      const persistedThreadPayload = await services.client.persistUserTurn({
        actor,
        sessionId: String(session.session_id),
        threadId: requestedThreadId,
        message: displayMessage,
        channel: 'web',
        metadata: {
          source: 'workstation_chat_pane',
        },
        clientRequestId,
      });
      assertTurnStillActive();
      const persistedThreadState: CanonicalChatThreadState = {
        ...normalizeCanonicalChatThread(persistedThreadPayload, requestedThreadId),
        session,
      };
      writeThreadState(persistedThreadState);
      emitWorkstationChatHistoryInvalidated({
        workspaceId: bootstrap.workspace.id,
        threadId: persistedThreadState.threadId,
      });

      let observedTraceId: string | null = null;
      let observedThreadId = requestedThreadId;
      let terminalTraceSeen = false;
      let observedFinalReply: string | null = null;
      const onTraceEvent = (traceEvent: WorkstationAgentTraceEvent) => {
        observedTraceId = readString(traceEvent.trace_id) || observedTraceId;
        terminalTraceSeen = terminalTraceSeen || isTerminalTraceEvent(readString(traceEvent.event_type));
        setLiveTimelineEvents((current) => [...current, {
          type: 'trace',
          payload: traceEvent as unknown as Record<string, unknown>,
        }]);
        setLiveTrace((current) => {
          const nextEvents = mergeTraceEvents(current?.events ?? [], [traceEvent]);
          const traceId = readString(traceEvent.trace_id) || current?.traceId || observedTraceId;
          const currentTrace = current?.trace ?? buildLiveTraceRecord({
            traceId,
            workspaceId: bootstrap.workspace.id,
            threadId: observedThreadId,
            rootAgentId: readString(traceEvent.agent_id) || 'sage',
          });
          return {
            traceId,
            transport: current?.transport ?? 'external',
            trace: {
              ...currentTrace,
              id: traceId ?? currentTrace.id ?? null,
              thread_id: readString(currentTrace.thread_id) || observedThreadId,
              root_agent_id: readString(currentTrace.root_agent_id) || readString(traceEvent.agent_id) || 'sage',
            },
            events: nextEvents,
          };
        });
        const eventType = readString(traceEvent.event_type);
        const eventData = readObject(traceEvent.data);
        if (eventType === 'reasoning.summary.delta') {
          const delta = readString(eventData.delta);
          if (delta) {
            setLiveActivitySteps((current) => {
              const thinkingIndex = current.findIndex((item) => item.kind === 'thinking');
              if (thinkingIndex < 0) {
                return current;
              }
              const updated = [...current];
              updated[thinkingIndex] = {
                ...updated[thinkingIndex],
                detail: delta,
              };
              return updated;
            });
          }
          return;
        }
        if (eventType === 'tool.started') {
          const toolName = readString(eventData.tool_name) || 'Using tool';
          setLiveActivitySteps((current) => upsertLiveActivityStep(current, {
            id: readString(traceEvent.tool_call_id) || `${toolName}:${current.length}`,
            kind: 'tool',
            label: toolName,
            detail: 'Running',
            status: 'active',
            toolCallId: readString(traceEvent.tool_call_id) || null,
            createdAt: new Date().toISOString(),
          }));
          return;
        }
        if (eventType === 'tool.result') {
          setLiveActivitySteps((current) => upsertLiveActivityStep(current, {
            id: readString(traceEvent.tool_call_id) || `tool:${current.length}`,
            kind: 'tool',
            label: '',
            detail: eventData.status === 'error' ? 'Failed' : 'Completed',
            status: eventData.status === 'error' ? 'error' : 'done',
            toolCallId: readString(traceEvent.tool_call_id) || null,
            createdAt: new Date().toISOString(),
          }));
          return;
        }
        if (eventType === 'search.query') {
          const query = readString(eventData.query);
          setLiveActivitySteps((current) => upsertLiveActivityStep(current, {
            id: readString(traceEvent.tool_call_id) || `search:${query || current.length}`,
            kind: 'search',
            label: 'Searching',
            detail: query,
            status: 'active',
            toolCallId: readString(traceEvent.tool_call_id) || null,
            createdAt: new Date().toISOString(),
          }));
          return;
        }
        if (eventType === 'search.results') {
          setLiveActivitySteps((current) => upsertLiveActivityStep(current, {
            id: readString(traceEvent.tool_call_id) || `search:${current.length}`,
            kind: 'search',
            label: 'Search',
            detail: 'Completed',
            status: 'done',
            toolCallId: readString(traceEvent.tool_call_id) || null,
            createdAt: new Date().toISOString(),
          }));
        }
      };

      streamInFlightRef.current = true;
      const { response, session: streamedSession } = await services.client.submitTurnStreamWithSessionRetry({
        actor,
        threadId: requestedThreadId,
        message: displayMessage,
        channel: 'web',
        source: 'workstation_chat_pane',
        runtimeTarget: selectedHardwareRuntimeTarget,
        provider: resolvedProviderId,
        model: resolvedModelId,
        reasoningEffort,
        existingSession: session,
        clientRequestId,
        abortHandle: streamAbortHandle,
        policyContext: permissionPolicyContext,
        attachments,
        onEvent: (event) => {
          if (activeTurnRequestIdRef.current !== clientRequestId || streamAbortRequestedRef.current) {
            return;
          }
          markTurnActivity();
          if (event.event === 'trace') {
            const traceEvent = normalizeTraceStreamEvent(event.payload);
            if (traceEvent) {
              onTraceEvent(traceEvent);
            }
            return;
          }

          if (event.event === 'step') {
            setLiveTimelineEvents((current) => [...current, {
              type: 'step',
              payload: event.payload,
            }]);
            const step = normalizeStepEvent(event.payload);
            if (step) {
              setLiveActivitySteps((current) => upsertLiveActivityStep(current, step));
            }
            return;
          }

          const typedTimelineEvent = typedTimelineEventFromStreamEvent(event);
          if (typedTimelineEvent) {
            setLiveTimelineEvents((current) => [...current, typedTimelineEvent]);
            if (event.event === 'response') {
              const delta = readString(event.payload.delta)
                || readString(event.payload.text)
                || readString(event.payload.content);
              if (delta) {
                setStreamingAssistantText((current) => stripInternalToolMarkup(`${current}${delta}`));
              }
            }
            return;
          }

          if (event.event === 'chunk') {
            const delta = readString(event.payload.delta);
            if (delta) {
              setLiveTimelineEvents((current) => [...current, {
                type: 'chunk',
                payload: { delta },
              }]);
              streamBufferRef.current += delta;
              if (rafHandleRef.current === null) {
                rafHandleRef.current = requestAnimationFrame(() => {
                  setStreamingAssistantText((current) =>
                    stripInternalToolMarkup(`${current}${streamBufferRef.current}`)
                  );
                  streamBufferRef.current = "";
                  rafHandleRef.current = null;
                });
              }
              setLiveActivitySteps((current) => {
                const thinkingIndex = current.findIndex((item) => item.kind === 'thinking');
                if (thinkingIndex < 0) {
                  return current;
                }
                const updated = [...current];
                updated[thinkingIndex] = {
                  ...updated[thinkingIndex],
                  label: 'Writing output',
                  detail: 'Streaming response',
                };
                return updated;
              });
            }
            return;
          }

          if (event.event === 'final') {
            setLiveTimelineEvents((current) => [...current, {
              type: 'final',
              payload: event.payload,
            }]);
            setTimelineSettled(true);
            const finalReply = readString(event.payload.reply)
              || readString(event.payload.content);
            const visibleFinalReply = stripInternalToolMarkup(finalReply);
            if (visibleFinalReply && !isProviderRuntimeGateMessage(visibleFinalReply)) {
              // Flush any remaining rAF-buffered chunks before setting final text
              if (rafHandleRef.current !== null) {
                cancelAnimationFrame(rafHandleRef.current);
                rafHandleRef.current = null;
              }
              if (streamBufferRef.current) {
                setStreamingAssistantText((current) =>
                  stripInternalToolMarkup(`${current}${streamBufferRef.current}`)
                );
                streamBufferRef.current = "";
              } else {
                // Only use the final payload if nothing was streamed yet.
                // Otherwise keep the streamed text — it IS the complete reply.
                setStreamingAssistantText((current) => {
                  if (current && !isProviderRuntimeGateMessage(current)) {
                    return current;
                  }
                  return visibleFinalReply;
                });
              }
              setStatusMessage(null);
              setSendFailureNotice(null);
            }
            const finalThreadId = readString(event.payload.thread_id);
            if (finalThreadId) {
              observedThreadId = finalThreadId;
            }
            const metadata = readObject(event.payload.metadata);
            const finalTraceId = readString(metadata.trace_id);
            if (finalTraceId) {
              observedTraceId = finalTraceId;
              // Persist live timeline events so the thinking trace
              // survives a page refresh and is visible on past turns.
              setLiveTimelineEvents((current) => {
                if (current.length > 0) {
                  setLegacyTraceEventsByTraceId((prev) => ({
                    ...prev,
                    [finalTraceId]: current,
                  }));
                }
                return current;
              });
            }
          }
        },
      });
      assertTurnStillActive();
      session = streamedSession;

      const responseMetadata =
        response.metadata && typeof response.metadata === 'object'
          ? { ...(response.metadata as Record<string, unknown>) }
          : {};
      const traceId = readString(responseMetadata.trace_id) || observedTraceId;
      if (traceId) {
        responseMetadata.trace_id = traceId;
      }
      const normalizedResponse: WorkstationTurnResponse = {
        ...response,
        reply: readString(response.reply) || observedFinalReply || undefined,
        thread_id: String(response.thread_id ?? observedThreadId ?? requestedThreadId),
        metadata: responseMetadata,
      };
      const responseExecutionTarget = readExecutionTarget(responseMetadata);
      const providerFailureIntervention = findProviderFailureIntervention(normalizedResponse.interventions ?? []);
      const providerFailureRecord = providerCatalog.find((provider) =>
        readString(provider.id).toLowerCase() === readString(responseMetadata.effective_provider || responseMetadata.provider).toLowerCase(),
      ) ?? selectedProviderRecord;
      const nextThreadId = String(normalizedResponse.thread_id ?? requestedThreadId);
      const nextMessages = (
        persistedThreadState.threadId === nextThreadId && persistedThreadState.messages.length > 0
          ? persistedThreadState.messages
          : threadRef.current.messages
      );
      const assistantMessage = createCanonicalAssistantMessage(normalizedResponse, nextThreadId);
      const assistantMessageVisible = Boolean(
        assistantMessage
        && !isSyntheticTranscriptMessage(assistantMessage),
      );
      const hasVisibleAssistantReply = Boolean(
        assistantMessageVisible
        || (
          typeof normalizedResponse.reply === 'string'
          && normalizedResponse.reply.trim()
          && !isProviderRuntimeGateMessage(normalizedResponse.reply)
        ),
      );

      setLiveTrace((current) => {
        if (!traceId && !current) {
          return null;
        }
        const currentTrace = current?.trace ?? buildLiveTraceRecord({
          traceId,
          workspaceId: bootstrap.workspace.id,
          threadId: nextThreadId,
          rootAgentId: current?.trace?.root_agent_id ? String(current.trace.root_agent_id) : 'sage',
        });
        return {
          traceId,
          transport: normalizedResponse.run_id && traceId && !terminalTraceSeen ? 'trace-stream' : (current?.transport ?? 'external'),
          trace: {
            ...currentTrace,
            id: traceId ?? currentTrace.id ?? null,
            thread_id: nextThreadId,
          },
          events: current?.events ?? [],
        };
      });

      const responseMessage = assistantMessageVisible ? assistantMessage : null;
      const immediateMessages = responseMessage
        ? [...nextMessages, responseMessage]
        : nextMessages;
      writeThreadState({
        ...(persistedThreadState.threadId === nextThreadId ? persistedThreadState : thread),
        threadId: nextThreadId,
        messages: immediateMessages,
        session,
      });
      setShowProjectedAssistant(false);
      setTimelineSettled(true);
      setStreamingAssistantText('');
      setLiveActivitySteps((current) => settleLiveActivitySteps(
        current,
        normalizedResponse.status === 'incomplete' ? 'error' : 'done',
      ));
      services.streams.touchActivity();
      const canonicalThread = await refreshCanonicalState(nextThreadId)
        .catch(() => null);
      const canonicalMessages = canonicalThread?.messages ?? [];
      const canonicalHasAssistant = canonicalMessages.some((message) => (
        message.role === 'assistant'
        && readString(message.content)
        && readString(message.content) === readString(responseMessage?.content)
      ));
      if (canonicalThread && (!responseMessage || canonicalHasAssistant)) {
        writeThreadState({
          ...canonicalThread,
          session,
        });
        emitWorkstationChatHistoryInvalidated({
          workspaceId: bootstrap.workspace.id,
          threadId: canonicalThread.threadId,
        });
      } else if (responseMessage) {
        writeThreadState({
          ...thread,
          threadId: nextThreadId,
          messages: immediateMessages,
          session,
        });
        emitWorkstationChatHistoryInvalidated({
          workspaceId: bootstrap.workspace.id,
          threadId: nextThreadId,
        });
      }
      const hasProviderFailure = Boolean(providerFailureIntervention);
      const providerGateDetected = hasProviderFailure
        || isProviderRuntimeGateMessage(readString(normalizedResponse.reply));
      const responseInterventions = Array.isArray(normalizedResponse.interventions)
        ? normalizedResponse.interventions
        : [];
      const connectorSetupNotice = hasVisibleAssistantReply
        ? null
        : connectorSetupNoticeFromInterventions(responseInterventions);
      const connectorSetupInterventionOnly = responseInterventions.length > 0
        && responseInterventions.every(isConnectorSetupIntervention);
      const needsUserIntervention = responseInterventions.length > 0
        && !hasVisibleAssistantReply;
      setStatusMessage(
        providerGateDetected
          ? null
          : needsUserIntervention && !hasProviderFailure && !connectorSetupInterventionOnly
            ? 'Sage needs your input before it can continue.'
            : null,
      );
      setSendFailureNotice(
        providerGateDetected
          ? providerFailureNoticeForProvider(
              providerFailureRecord,
              providerFailureMessageForProvider(providerFailureRecord),
            )
          : connectorSetupNotice,
      );
      if (normalizedResponse.status !== 'incomplete') {
        services.queryClient.invalidate('chat:billing-summary');
        services.queryClient.invalidate('chat:credit-usage-history');
        await refreshBillingSummary();
      }
    } catch (error) {
      updatePendingUserMessage(null);
      const normalizedError = error instanceof WorkstationClientError ? error : null;
      const aborted = normalizedError?.code === 'stream_aborted'
        || streamAbortRequestedRef.current
        || activeTurnRequestIdRef.current !== clientRequestId;
      const partialStreamText = readString(streamingAssistantTextRef.current);
      const incompleteWithPartial = normalizedError?.code === 'stream_incomplete' && Boolean(partialStreamText);
      if (aborted || incompleteWithPartial) {
        setShowProjectedAssistant(false);
        setTimelineSettled(true);
        finalizePartialAssistantResponse(activeThreadIdRef.current);
        setLiveActivitySteps((current) => settleLiveActivitySteps(current, 'done'));
        setSendFailureNotice(incompleteWithPartial
          ? {
              message: 'Response interrupted before completion.',
              retryable: true,
              retryDraft: outboundMessage,
            }
          : null);
      } else {
        setStreamingAssistantText('');
        setShowProjectedAssistant(false);
        setTimelineSettled(true);
        setLiveTimelineEvents([]);
        setLiveActivitySteps([]);
        setLiveTrace(null);
        const rawMessage = error instanceof WorkstationClientError || error instanceof Error
          ? error.message
          : 'Could not send this message.';
        const normalizedRawMessage = rawMessage.toLowerCase();
        const creditExhausted = normalizedError?.status === 402
          || normalizedRawMessage.includes('reached your ai limit')
          || normalizedRawMessage.includes('ai limit');
        const providerNeedsAttention = !creditExhausted && (
          normalizedRawMessage.includes('provider')
          || normalizedRawMessage.includes('credential')
          || normalizedRawMessage.includes('api key')
          || normalizedRawMessage.includes('ollama')
          || normalizedRawMessage.includes('selected for chat')
          || normalizedRawMessage.includes('not available')
        );
        const localComputerNeedsAttention = isLocalCompanionGateMessage(rawMessage) || normalizedRawMessage.includes('gateway offline');
        const authNeedsAttention = normalizedError?.status === 401 || normalizedError?.status === 403;
        const rateLimitFailure = normalizedError?.status === 429 || /rate.?limit|capacity/i.test(normalizedRawMessage);
        const timeoutFailure = /timed out|too long to respond|request timeout/i.test(normalizedRawMessage);
        const transportFailure = /failed to fetch|could not connect|network error|transport failure|connection/i.test(normalizedRawMessage);
        const serverFailure = (typeof normalizedError?.status === 'number' && normalizedError.status >= 500)
          || /bad gateway|gateway timeout|service unavailable|internal server error|server error/i.test(normalizedRawMessage);
        const noticeMessage = isLocalCompanionGateMessage(rawMessage)
          ? 'Agent Computer is needed for this request. Connect Agent Computer and try again.'
          : creditExhausted
            ? "You've reached your AI limit. Open AI & Setup →"
            : providerNeedsAttention
            ? 'The selected AI path is not ready. Use the workspace AI route, connect your own AI account, connect Agent Computer, or choose another model in Connections.'
            : authNeedsAttention
                ? 'Your session needs attention before Sage can continue. Refresh the page or sign in again.'
                : rateLimitFailure
                  ? 'Sage is temporarily at capacity. Try again in a moment or switch AI model.'
                  : timeoutFailure
                    ? 'Sage took too long to respond. Try again or switch AI model.'
                    : transportFailure
                      ? 'The request could not reach the server. Check your connection and try again.'
                      : serverFailure
                        ? 'Sage hit a temporary server issue before it could reply. Try again in a moment.'
                        : "Sage couldn't complete that turn. Try again or choose another AI model in Connections.";
        const providerNotice = providerNeedsAttention
          ? providerFailureNoticeForProvider(selectedProviderRecord, noticeMessage)
          : null;
        setSendFailureNotice({
          message: providerNotice?.message ?? noticeMessage,
          retryable: error instanceof WorkstationClientError
            ? error.retryable
            : (creditExhausted ? false : true),
          actions: creditExhausted
            ? [{ label: 'Open AI & Setup', target: 'integrations' }]
            : localComputerNeedsAttention
              ? [{ label: 'Open Hardware', target: 'hardware' }]
              : authNeedsAttention
                  ? undefined
                  : providerNotice?.actions,
          retryDraft: outboundMessage,
        });
      }
      if (aborted || incompleteWithPartial) {
        setLiveActivitySteps((current) => settleLiveActivitySteps(current, 'done'));
      }
    } finally {
      if (activeTurnRequestIdRef.current === clientRequestId || activeTurnRequestIdRef.current === null) {
        submitInFlightRef.current = false;
        streamInFlightRef.current = false;
        streamAbortHandleRef.current = null;
        streamAbortRequestedRef.current = false;
        setIsSending(false);
      }
      if (activeTurnRequestIdRef.current === clientRequestId) {
        activeTurnRequestIdRef.current = null;
      }
    }
  };

  const sageModelControl = (
    <div className="sage-canvas-model sage-canvas-model--composer" ref={modelCanvasPickerRef}>
      <button
        type="button"
        className="sage-canvas-model__trigger sage-canvas-model__trigger--composer"
        aria-label={`Choose model and reasoning. Current model: ${selectedCanvasModelLabel}`}
        aria-expanded={modelCanvasPickerOpen}
        disabled={isPersistingModelSelection}
        onClick={() => {
          setHardwareCanvasPickerOpen(false);
          setModelPickerSubpanel(null);
          setModelCanvasPickerOpen((current) => !current);
        }}
      >
        <span>{selectedCanvasModelLabel}</span>
        <ChevronDown size={14} strokeWidth={1.9} aria-hidden="true" />
      </button>
      {modelCanvasPickerOpen ? (
        <div className="sage-canvas-model__menu sage-canvas-model__menu--composer" role="dialog" aria-label="Model and reasoning">
          {isSending ? (
            <div className="sage-canvas-model__busy-note" role="status">
              Stop the current response before switching models.
            </div>
          ) : null}
          <div className="sage-canvas-model__section-title">Reasoning</div>
          <div className="sage-canvas-model__options" role="listbox" aria-label="Reasoning">
            {reasoningOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className={`sage-canvas-model__option${option.value === reasoningEffort ? ' sage-canvas-model__option--selected' : ''}`}
                disabled={isSending || isPersistingModelSelection}
                onClick={() => {
                  handleReasoningEffortChange(option.value);
                }}
                role="option"
                aria-selected={option.value === reasoningEffort}
              >
                <span>{option.label}</span>
                {option.value === reasoningEffort ? (
                  <Check size={16} strokeWidth={2} aria-hidden="true" />
                ) : null}
              </button>
            ))}
          </div>

          <div className="sage-canvas-model__divider" aria-hidden="true" />

          <button
            type="button"
            className={`sage-canvas-model__nav-row${modelPickerSubpanel === 'model' ? ' sage-canvas-model__nav-row--active' : ''}`}
            disabled={isSending || isPersistingModelSelection}
            onPointerEnter={() => {
              if (!isSending && !isPersistingModelSelection) {
                setModelPickerSubpanel('model');
              }
            }}
            onPointerMove={() => {
              if (!isSending && !isPersistingModelSelection) {
                setModelPickerSubpanel('model');
              }
            }}
            onMouseEnter={() => {
              if (!isSending && !isPersistingModelSelection) {
                setModelPickerSubpanel('model');
              }
            }}
            onFocus={() => {
              if (!isSending && !isPersistingModelSelection) {
                setModelPickerSubpanel('model');
              }
            }}
            onClick={() => {
              setModelPickerSubpanel('model');
            }}
          >
            <span>{selectedCanvasModelLabel}</span>
            <ChevronRight size={16} strokeWidth={1.9} aria-hidden="true" />
          </button>

          {activeModelPickerProvider ? (
            <button
              type="button"
              className={`sage-canvas-model__nav-row${modelPickerSubpanel === 'provider' ? ' sage-canvas-model__nav-row--active' : ''}`}
              disabled={isSending || isPersistingModelSelection}
              onPointerEnter={() => {
                if (!isSending && !isPersistingModelSelection) {
                  setModelPickerSubpanel('provider');
                }
              }}
              onPointerMove={() => {
                if (!isSending && !isPersistingModelSelection) {
                  setModelPickerSubpanel('provider');
                }
              }}
              onMouseEnter={() => {
                if (!isSending && !isPersistingModelSelection) {
                  setModelPickerSubpanel('provider');
                }
              }}
              onFocus={() => {
                if (!isSending && !isPersistingModelSelection) {
                  setModelPickerSubpanel('provider');
                }
              }}
              onClick={() => {
                setModelPickerSubpanel('provider');
              }}
            >
              <span className="sage-canvas-model__provider-copy">
                <SageModelPickerProviderMark provider={activeModelPickerProvider} />
                <span>{activeModelPickerProvider.label}</span>
              </span>
              <ChevronRight size={16} strokeWidth={1.9} aria-hidden="true" />
            </button>
          ) : null}

          {modelPickerSubpanel === 'model' && activeModelPickerProvider ? (
            <div className="sage-canvas-model__subpanel" role="dialog" aria-label={`${activeModelPickerProvider.label} models`}>
              <div className="sage-canvas-model__section-title">Model</div>
              <div className="sage-canvas-model__model-list" role="listbox" aria-label={`${activeModelPickerProvider.label} models`}>
                {activeModelPickerModelView.visibleModels.length > 0 ? activeModelPickerModelView.visibleModels.map((model) => {
                  const canSelectModel = Boolean(model.optionId);
                  return (
                    <button
                      key={model.id}
                      className={`sage-canvas-model__model-row${model.selected ? ' sage-canvas-model__model-row--selected' : ''}`}
                      type="button"
                      disabled={!canSelectModel || isSending || isPersistingModelSelection}
                      onClick={() => {
                        if (!model.optionId) {
                          return;
                        }
                        setModelCanvasPickerOpen(false);
                        setModelPickerSubpanel(null);
                        handleModelChange(model.optionId);
                      }}
                      role="option"
                      aria-selected={model.selected}
                    >
                      <span className="sage-canvas-model__model-copy">
                        <strong>{model.label}</strong>
                        {model.description ? <small>{model.description}</small> : null}
                      </span>
                      {model.selected ? (
                        <Check size={16} strokeWidth={2} aria-hidden="true" />
                      ) : null}
                    </button>
                  );
                }) : (
                  <div className="sage-canvas-model__model-empty">
                    No models available for this provider.
                  </div>
                )}
              </div>
              {!activeModelPickerModelView.expanded && activeModelPickerModelView.hiddenCount > 0 ? (
                <button
                  type="button"
                  className="sage-canvas-model__see-more"
                  disabled={isSending || isPersistingModelSelection}
                  onClick={() => {
                    setExpandedModelPickerProviderIds((current) =>
                      current.includes(activeModelPickerProvider.id)
                        ? current
                        : [...current, activeModelPickerProvider.id]);
                  }}
                >
                  See more
                </button>
              ) : null}
            </div>
          ) : null}

          {modelPickerSubpanel === 'provider' ? (
            <div className="sage-canvas-model__subpanel" role="dialog" aria-label="Model providers">
              <div className="sage-canvas-model__section-title">Provider</div>
              <div className="sage-canvas-model__provider-list" role="listbox" aria-label="Providers">
                {modelPickerProviderPanels.map((provider) => (
                  <button
                    key={provider.id}
                    type="button"
                    className={`sage-canvas-model__provider-option${provider.id === activeModelPickerProviderId ? ' sage-canvas-model__provider-option--active' : ''}`}
                    disabled={isSending || isPersistingModelSelection}
                    onClick={() => {
                      setActiveModelPickerProviderId(provider.id);
                    }}
                    role="option"
                    aria-selected={provider.id === activeModelPickerProviderId}
                  >
                    <span className="sage-canvas-model__provider-copy">
                      <SageModelPickerProviderMark provider={provider} />
                      <span>{provider.label}</span>
                    </span>
                    {provider.id === activeModelPickerProviderId ? (
                      <Check size={16} strokeWidth={2} aria-hidden="true" />
                    ) : null}
                  </button>
                ))}
              </div>
              <div className="sage-canvas-model__divider" aria-hidden="true" />
              <button
                type="button"
                className="sage-canvas-model__provider-option sage-canvas-model__provider-option--settings"
                disabled={isSending || isPersistingModelSelection}
                onClick={() => {
                  setModelCanvasPickerOpen(false);
                  setModelPickerSubpanel(null);
                  router.push(`${integrationsHref}?section=ai-runtime`);
                }}
              >
                <span>AI settings</span>
                <ChevronRight size={16} strokeWidth={1.9} aria-hidden="true" />
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );

  const hardwareDotTone = localCompanionConnected ? 'online' : localCompanionOnline ? 'warning' : 'offline';
  const sageCanvasControls = (
    <div className="sage-canvas-controls" aria-label="Hardware status">
      <div className="sage-canvas-hardware" ref={hardwareCanvasPickerRef}>
        <button
          type="button"
          className="sage-canvas-hardware__trigger"
          aria-label={`Agent Computer: ${agentComputerHeaderLabel}`}
          aria-expanded={hardwareCanvasPickerOpen}
          disabled={isSending}
          onClick={() => {
            setHardwareCanvasPickerOpen((current) => !current);
          }}
        >
          <span className={`sage-canvas-hardware__dot sage-canvas-hardware__dot--${hardwareDotTone}`} aria-hidden="true" />
          <span>{agentComputerDeviceLabel || agentComputerHeaderLabel}</span>
        </button>
        {hardwareCanvasPickerOpen ? (
          <div
            className="sage-canvas-hardware__menu"
            role="dialog"
            aria-label="Agent Computer"
          >
            <div className="sage-canvas-hardware__option sage-canvas-hardware__option--status">
              <span className="sage-canvas-hardware__option-copy">
                <Monitor className="sage-canvas-hardware__option-icon" size={16} strokeWidth={1.9} aria-hidden="true" />
                <span className="sage-canvas-hardware__option-label">
                  <strong>{agentComputerMenuStatus.label}</strong>
                  <small>{agentComputerMenuStatus.detail}</small>
                </span>
              </span>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );

  return (
    <>
      {titlebarActionsHost ? createPortal(sageCanvasControls, titlebarActionsHost) : null}
      <main
        data-workstation-surface="chat"
        data-workstation-chat="pane"
        data-agent-activity-state={unifiedAgentActivityState}
        className={`app-chat-page app-chat-page--surface${showFirstImpression ? ' app-chat-page--first-impression' : ''}`}
      >
        <section className={`app-chat-thread app-chat-thread--surface${showBlankTranscript || showFirstImpression ? ' app-chat-thread--blank' : ''}`}>
          {showContextStrip ? (
            <div className="app-chat-context-strip" aria-label="Sage conversation context">
              {activeProviderSummary.connected ? (
                <AppShinyText>{activeProviderSummary.label}</AppShinyText>
              ) : (
                <>
                  <button
                  type="button"
                  className="app-chat-context-strip__link"
                  onClick={() => {
                    router.push(integrationsHref);
                  }}
                >
                    {activeProviderSummary.label}
                  </button>
                </>
              )}
              {selectedProviderContext.providerLabel === 'Ollama' && contextWindowLabel ? (
                <>
                  <span aria-hidden="true">·</span>
                  <span>Local</span>
                </>
              ) : null}
              {selectedProviderContext.providerLabel && reasoningEffort !== defaultReasoningEffort ? (
                <>
                  <span aria-hidden="true">·</span>
                  <span>{contextReasoningLabel}</span>
                </>
              ) : null}
              {contextDeviceLabel ? (
                <>
                  <span aria-hidden="true">·</span>
                  <span
                    className={`app-chat-context-strip__device${desktop.localCompanion.online ? ' app-chat-context-strip__device--online' : ' app-chat-context-strip__device--offline'}`}
                  >
                    <span className="app-chat-context-strip__device-dot" aria-hidden="true" />
                    <span>{contextDeviceLabel}</span>
                  </span>
                </>
              ) : null}
            </div>
          ) : null}
          {showHeaderReadinessStrip && readinessPills.length > 0 ? (
            <div className="app-chat-readiness-strip" aria-label="Sage readiness">
              {readinessPills.map((pill) => (
                <button
                  key={pill.id}
                  type="button"
                  className={`app-chat-readiness-pill app-chat-readiness-pill--${pill.tone}`}
                  onClick={() => {
                    if (pill.target === 'gateway') {
                      router.push(hardwareHref);
                      return;
                    }
                    if (pill.target === 'hardware') {
                      router.push(hardwareHref);
                      return;
                    }
                    router.push(integrationsHref);
                  }}
                >
                  <span className="app-chat-readiness-pill__dot" aria-hidden="true" />
                  <span>{pill.label}</span>
                </button>
              ))}
            </div>
          ) : null}
          <ScrollRegion
            ref={transcriptScrollRef}
            className="app-chat-thread__scroll"
            aria-label="Sage conversation"
            onScroll={handleTranscriptScroll}
          >
            <div className="app-chat-thread__body">
              {showSageSetupLoadingCard ? (
                <AppNotice tone="neutral" className="app-chat-status-notice">
                  <div className="app-chat-status-notice__copy">
                    <strong>Loading Sage setup</strong>
                    <span>Checking your profile and bootstrap progress before Sage starts the conversation.</span>
                  </div>
                </AppNotice>
              ) : null}

              {showSageSetupUnavailableCard ? (
                <AppNotice tone="warning" className="app-chat-status-notice">
                  <div className="app-chat-status-notice__copy">
                    <strong>Sage setup is temporarily unavailable</strong>
                    <span>{sageSetupMessage || 'Sage setup could not be loaded right now.'}</span>
                  </div>
                  <div className="app-chat-status-notice__actions">
                    <AppButton
                      type="button"
                      tone="primary"
                      onClick={() => {
                        void retrySageSetup();
                      }}
                      disabled={isRetryingSageSetup}
                    >
                      {isRetryingSageSetup ? 'Retrying…' : 'Retry'}
                    </AppButton>
                  </div>
                </AppNotice>
              ) : null}

              {showBootstrapCard ? (
                <AppNotice tone="warning" className="app-chat-status-notice">
                  <div className="app-chat-status-notice__copy">
                    <strong>Set up Sage</strong>
                    <span>
                      {bootstrapQuestion?.prompt || 'Add your identity workspace so Sage can carry the right context into future turns.'}
                    </span>
                    <span>
                      {`Progress ${profileSnapshot.bootstrap.progress_label}. Structured profile is the runtime authority; USER.md, IDENTITY.md, SOUL.md, and HEARTBEAT.md stay as projections.`}
                    </span>
                  </div>
                  <form
                    className="app-stack-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submitBootstrapResponse();
                    }}
                  >
                    <FormField label="Answer">
                      <FormInput
                        value={bootstrapAnswer}
                        onChange={(event) => {
                          setBootstrapAnswer(event.currentTarget.value);
                        }}
                        placeholder={bootstrapQuestion?.placeholder || 'Add the next answer for Sage'}
                        disabled={isSubmittingBootstrap}
                      />
                    </FormField>
                    <div className="app-chat-status-notice__actions">
                      <AppButton
                        type="submit"
                        tone="primary"
                        disabled={isSubmittingBootstrap || !bootstrapAnswer.trim()}
                      >
                        {isSubmittingBootstrap ? 'Saving…' : 'Save and continue'}
                      </AppButton>
                    </div>
                  </form>
                </AppNotice>
              ) : null}

              {showBlankTranscript ? (
                <SageChatEmptyState
                  modelLabel={selectedModelOption.label}
                  providerGateVisible={!activeProviderSummary.connected}
                  integrationsHref={integrationsHref}
                  onSelectPrompt={setDraft}
                />
              ) : null}

              {visibleTranscriptCells.map((cell, index) => (
                <CodexChatCell
                  key={`${cell.kind}:${cell.id}:${index}`}
                  cell={cell}
                />
              ))}

              {pinnedTimelineCells.map((cell, index) => (
                <CodexChatCell
                  key={`pinned:${cell.kind}:${cell.id}:${index}`}
                  cell={cell}
                />
              ))}
            </div>
          </ScrollRegion>
          {showTranscriptJump ? (
            <button
              type="button"
              className="app-chat-thread__jump"
              onClick={() => {
                transcriptForceStickRef.current = true;
                scrollTranscriptToLatest('smooth');
              }}
            >
              <ArrowDown size={14} strokeWidth={2} aria-hidden="true" />
              <span>Latest</span>
            </button>
          ) : null}
        </section>

        {sendFailureNotice ? (
          <PlatformNotification
            tone="warning"
            title="System status"
            detail={sendFailureNotice.message}
            action={(sendFailureNotice.actions ?? [])[0]
              ? {
                  label: (sendFailureNotice.actions ?? [])[0]?.label ?? 'Open',
                  onClick: () => {
                    const action = (sendFailureNotice.actions ?? [])[0];
                    setSendFailureNotice(null);
                    if (action?.target === 'integrations') {
                      // Use the backend-owned ai_setup_url when available,
                      // fall back to hardcoded path for older responses.
                      const setupUrl = action.url || `${integrationsHref}?section=ai-runtime`;
                      router.push(setupUrl);
                      return;
                    }
                    if (action?.target === 'hardware') {
                      router.push(hardwareHref);
                      return;
                    }
                    router.push(
                      action?.target === 'gateway'
                        ? hardwareHref
                        : integrationsHref,
                    );
                  },
                }
              : sendFailureNotice.retryable
                ? {
                    label: 'Try again',
                    onClick: () => {
                      if (sendFailureNotice.retryDraft) {
                        setDraft(sendFailureNotice.retryDraft);
                      }
                      setSendFailureNotice(null);
                    },
                  }
                : undefined}
            onClose={() => {
              setSendFailureNotice(null);
            }}
          />
        ) : null}

        {!sendFailureNotice && statusMessage ? (
          <PlatformNotification
            tone={statusNotice?.tone ?? 'neutral'}
            title={statusNotice?.title ?? 'Notice'}
            detail={statusNotice?.body ?? statusMessage}
            action={{
              label: statusNotice?.actionLabel ?? (statusNotice?.requiresLocalAccess ? 'Got it' : 'Dismiss'),
              onClick: () => {
                if (statusNotice?.actionTarget === 'gateway') {
                  setStatusMessage(null);
                  router.push(hardwareHref);
                  return;
                }
                if (statusNotice?.actionTarget === 'hardware') {
                  setStatusMessage(null);
                  router.push(hardwareHref);
                  return;
                }
                if (statusNotice?.actionTarget === 'integrations') {
                  setStatusMessage(null);
                  router.push(integrationsHref);
                  return;
                }
                setStatusMessage(null);
              },
            }}
            onClose={() => {
              setStatusMessage(null);
            }}
          />
        ) : null}

      <ChatComposer
        draft={draft}
        onDraftChange={handleDraftChange}
        onSubmit={() => {
          void sendMessage();
        }}
        onStop={stopStreamingResponse}
        slashCommands={sageSlashCommands}
        onSlashCommandSelect={handleSlashCommandSelect}
        capabilityItems={sageCapabilityItems}
        attachments={attachments}
        onRemoveAttachment={(attachment) => {
          setAttachments((current) => current.filter((a) => a.file_id !== attachment.file_id));
        }}
        onFilesSelected={handleComposerFilesSelected}
        modelControl={sageModelControl}
        modelControlOpen={modelCanvasPickerOpen}
        onComposerMenuOpen={() => {
          setModelCanvasPickerOpen(false);
          setModelPickerSubpanel(null);
          setHardwareCanvasPickerOpen(false);
        }}
        reasoningEffort={reasoningEffort}
        reasoningOptions={reasoningOptions}
        onReasoningEffortChange={handleReasoningEffortChange}
        onVoiceTranscribe={async (audio) => {
          const payload = await services.client.transcribeSpeech(audio);
          const transcript = typeof payload.transcript === 'string' ? payload.transcript.trim() : '';
          if (!transcript) {
            throw new Error('No speech detected.');
          }
          return transcript;
        }}
        contextWindowLabel={contextWindowLabel}
        busy={chatActivityBusy}
        controlsDisabled={isPersistingModelSelection}
        sendDisabled={false}
        placeholder="Message Sage..."
        providerGateVisible={!activeProviderSummary.connected}
        providerSummary={{
          label: activeProviderSummary.label,
          actionLabel: 'Set up AI',
        }}
        smallModelWarning={smallModelWarningVisible
          ? "You're using a small model. For best results with tools and complex tasks, we recommend switching to a larger model (7B+)."
          : null}
        preRunCostEstimate={preRunCostEstimate}
        onDismissSmallModelWarning={() => {
          setSmallModelWarningVisible(false);
        }}
      />

      <CommandSheet
        open={workspaceCommandPaletteOpen}
        title="Go to"
        description="Workspace navigation and surfaces. Agent commands stay in the chat composer with /."
        onClose={() => {
          setWorkspaceCommandPaletteOpen(false);
        }}
        actions={(
          <AppButton
            type="button"
            tone="secondary"
            onClick={() => {
              setWorkspaceCommandPaletteOpen(false);
            }}
          >
            Close
          </AppButton>
        )}
      >
        <div className="sage-workspace-command-palette">
          {workspaceCommandItems.map((command) => (
            <button
              key={command.id}
              type="button"
              className="sage-workspace-command-palette__item"
              disabled={!command.href}
              onClick={() => {
                handleWorkspaceCommandSelect(command);
              }}
            >
              <span>
                <strong>{command.title}</strong>
                <small>{command.description}</small>
              </span>
              <span className="sage-workspace-command-palette__hint">Open</span>
            </button>
          ))}
        </div>
      </CommandSheet>

      <CommandSheet
        open={isMemorySheetOpen}
        title={memoryDraft.entryId ? 'Correct memory' : 'Save memory'}
        description="Memory saves are explicit here. Save only facts or context Sage should carry into future turns."
        onClose={() => {
          setIsMemorySheetOpen(false);
          setMemoryDraft(defaultSageMemoryDraft());
        }}
        actions={(
          <>
            <AppButton
              type="button"
              tone="secondary"
              onClick={() => {
                setIsMemorySheetOpen(false);
                setMemoryDraft(defaultSageMemoryDraft());
              }}
              disabled={Boolean(mutatingMemory)}
            >
              Cancel
            </AppButton>
            <AppButton
              type="button"
              tone="primary"
              onClick={() => {
                void submitMemoryDraft();
              }}
              disabled={Boolean(mutatingMemory)}
            >
              {mutatingMemory ? 'Saving…' : memoryDraft.entryId ? 'Save correction' : 'Save memory'}
            </AppButton>
          </>
        )}
      >
        <FormSection
          title="Memory entry"
          description="Choose the category carefully so Sage can use this memory the right way."
        >
          <FormGrid columns="repeat(2, minmax(0, 1fr))">
            <FormField label="Category">
              <select
                className="app-select"
                value={memoryDraft.category}
                onChange={(event) => {
                  const nextCategory = event.currentTarget.value;
                  setMemoryDraft((current) => ({
                    ...current,
                    category: nextCategory,
                  }));
                }}
              >
                {memorySnapshot.categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.label}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Pin now">
              <div className="app-inline-actions app-inline-actions--tight">
                <AppButton
                  type="button"
                  tone={memoryDraft.pinned ? 'primary' : 'secondary'}
                  onClick={() => {
                    setMemoryDraft((current) => ({
                      ...current,
                      pinned: !current.pinned,
                    }));
                  }}
                >
                  {memoryDraft.pinned ? 'Pinned' : 'Pin memory'}
                </AppButton>
              </div>
            </FormField>
          </FormGrid>
          <FormGrid columns="1fr">
            <FormField label="Title" hint="Short enough to scan quickly in future turns.">
              <FormInput
                value={memoryDraft.title}
                onChange={(event) => {
                  const nextValue = event.currentTarget.value;
                  setMemoryDraft((current) => ({
                    ...current,
                    title: nextValue,
                  }));
                }}
                placeholder="Example: Preferred working style"
              />
            </FormField>
            <FormField label="Content" hint="Keep it factual and explicit. Avoid dumping raw notes.">
              <FormTextarea
                rows={5}
                value={memoryDraft.content}
                onChange={(event) => {
                  const nextValue = event.currentTarget.value;
                  setMemoryDraft((current) => ({
                    ...current,
                    content: nextValue,
                  }));
                }}
                placeholder="Example: Prefers concise status updates and direct next steps."
              />
            </FormField>
          </FormGrid>
        </FormSection>
      </CommandSheet>

      <ConfirmDialog
        open={Boolean(pendingDeleteMemory)}
        title="Forget memory?"
        body={pendingDeleteMemory
          ? `Sage will remove "${readString(pendingDeleteMemory.title) || 'this memory'}" from explicit carry-forward memory.`
          : 'Sage will remove this memory.'}
        confirmLabel="Forget memory"
        busy={Boolean(mutatingMemory)}
        onConfirm={() => {
          void confirmDeleteMemory();
        }}
        onCancel={() => {
          setPendingDeleteMemoryId(null);
        }}
      />
      </main>
    </>
  );
}
