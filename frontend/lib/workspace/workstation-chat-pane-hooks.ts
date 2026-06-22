'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import type { WorkstationChatMessageRecord } from '@/lib/workspace/chat-message';
import type { TimelineProjectionEvent } from '@/lib/workspace/codex-chat/timeline-reducer';
import type {
  ProviderCatalogRecord,
  ProviderProfileRecord,
  VaultCredentialRecord,
  WorkstationAgentTraceEvent,
  WorkstationAgentTraceRecord,
  WorkstationSageMemoryRecord,
  WorkstationSessionRecord,
} from '@/lib/workspace/workstation-client';

type ChatQueryCache = {
  peek<T>(key: string): T | null | undefined;
};

export type CanonicalChatThreadState = {
  threadId: string;
  title: string;
  messages: WorkstationChatMessageRecord[];
  session: WorkstationSessionRecord | null;
};

export type CanonicalRunSummary = Record<string, unknown> & {
  run_id?: string | null;
  status?: string | null;
  created_at?: string | null;
};

export type CanonicalApprovalSummary = Record<string, unknown> & {
  approval_id?: string | null;
  id?: string | null;
  status?: string | null;
  prompt?: string | null;
};

type LiveTraceTransport = 'external' | 'trace-stream';

export type LiveTraceState = {
  traceId: string | null;
  transport: LiveTraceTransport;
  trace: WorkstationAgentTraceRecord | null;
  events: WorkstationAgentTraceEvent[];
};

export type LiveActivityStepState = {
  id: string;
  kind: string;
  label: string;
  detail: string;
  status: string;
  toolCallId: string | null;
  createdAt: string;
};

export type SageMemoryCategoryRecord = {
  id: string;
  label: string;
  description: string;
  count: number;
};

export type SageMemorySnapshot = {
  items: WorkstationSageMemoryRecord[];
  categories: SageMemoryCategoryRecord[];
  summary: Record<string, unknown>;
  updatedAt: string | null;
};

export type SageProfileSnapshot = {
  profile: {
    user_name: string;
    identity_summary: string;
    communication_style: string;
    recurring_responsibility: string;
    standing_rules: string[];
    standing_rules_text: string;
  };
  bootstrap: {
    complete: boolean;
    answered_count: number;
    total_count: number;
    progress_label: string;
    current_question: {
      id: string;
      prompt: string;
      placeholder: string;
    } | null;
  };
};

export type SageSetupSurfaceState = 'loading' | 'required' | 'unavailable' | 'ready';

export type RecentThreadSummary = {
  threadId: string;
  title: string;
  updatedAt: string | null;
};

export type SendFailureNotice = {
  message: string;
  retryable: boolean;
  retryDraft?: string | null;
  actions?: {
    label: string;
    target: 'gateway' | 'hardware' | 'integrations' | 'approvals';
    url?: string;
  }[];
};

export type ChatMachineTrust = 'personal' | 'agent';
export type ChatAutonomyMode = 'default' | 'auto_review' | 'autopilot';
export type ChatReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export type ChatModelOption = {
  id: string;
  label: string;
  providerId: string | null;
  providerLabel: string | null;
  supportsReasoning: boolean;
  reasoningLevels: ChatReasoningEffort[];
  contextWindowTokens: number | null;
  routeProviderId?: string | null;
  routeModelId?: string | null;
  defaultReasoningEffort?: ChatReasoningEffort | null;
  uiSection?: 'empyralis' | 'local_ai' | 'my_api_key' | 'my_ai_account' | 'system';
};

export type SageMemoryDraft = {
  entryId: string | null;
  category: string;
  title: string;
  content: string;
  pinned: boolean;
};

export type SageToolPolicyRecord = {
  key: string;
  enabled: boolean;
};

export type GatewayReadinessDoctorPayload = Record<string, unknown> & {
  status?: string | null;
  browser?: Record<string, unknown> | null;
  browser_attach?: Record<string, unknown> | null;
};

function readPersistedActiveThread(storageKey: string): string | null {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return null;
  }
  try {
    const persisted = window.localStorage.getItem(storageKey);
    if (typeof persisted !== 'string') {
      return null;
    }
    const normalized = persisted.trim();
    return normalized.length > 0 ? normalized : null;
  } catch {
    return null;
  }
}

type ThreadStateOptions = {
  queryClient: ChatQueryCache;
  activeThreadQueryKey: string;
  recentThreadsQueryKey: string;
  workspaceStorageKey: string;
  primaryThreadId: string;
  threadQueryKey: (threadId: string) => string;
};

export function useChatThreadState(options: ThreadStateOptions) {
  const [activeThreadId, setActiveThreadId] = useState<string>(() => {
    const cachedThreadId = options.queryClient.peek<string>(options.activeThreadQueryKey);
    const persistedThreadId = readPersistedActiveThread(options.workspaceStorageKey);
    return cachedThreadId ?? persistedThreadId ?? options.primaryThreadId;
  });
  const [thread, setThread] = useState<CanonicalChatThreadState>(() =>
    options.queryClient.peek<CanonicalChatThreadState>(options.threadQueryKey(activeThreadId)) ?? {
      threadId: activeThreadId,
      title: 'Chat',
      messages: [],
      session: null,
    },
  );
  const [recentThreads, setRecentThreads] = useState<RecentThreadSummary[]>(
    () => options.queryClient.peek<RecentThreadSummary[]>(options.recentThreadsQueryKey) ?? [{
      threadId: options.primaryThreadId,
      title: 'Primary thread',
      updatedAt: null,
    }],
  );
  const activeThreadIdRef = useRef(activeThreadId);
  const threadRef = useRef(thread);

  useEffect(() => {
    activeThreadIdRef.current = activeThreadId;
  }, [activeThreadId]);

  useEffect(() => {
    threadRef.current = thread;
  }, [thread]);

  return {
    activeThreadId,
    setActiveThreadId,
    activeThreadIdRef,
    thread,
    setThread,
    threadRef,
    recentThreads,
    setRecentThreads,
  };
}

export function useChatComposerState() {
  const [draft, setDraft] = useState('');
  const [attachments, setAttachments] = useState<any[]>([]);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [sendFailureNotice, setSendFailureNotice] = useState<SendFailureNotice | null>(null);
  const [isSending, setIsSending] = useState(false);

  return {
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
  };
}

export function useChatRunAndApprovalState(queryClient: ChatQueryCache, runsQueryKey: string, approvalsQueryKey: string) {
  const [runs, setRuns] = useState<CanonicalRunSummary[]>(
    () => queryClient.peek<CanonicalRunSummary[]>(runsQueryKey) ?? [],
  );
  const [approvals, setApprovals] = useState<CanonicalApprovalSummary[]>(
    () => queryClient.peek<CanonicalApprovalSummary[]>(approvalsQueryKey) ?? [],
  );

  return {
    runs,
    setRuns,
    approvals,
    setApprovals,
  };
}

export function useChatStreamRunState() {
  const [pendingUserMessage, setPendingUserMessageState] = useState<WorkstationChatMessageRecord | null>(null);
  const [streamingAssistantText, setStreamingAssistantText] = useState('');
  const [liveTimelineEvents, setLiveTimelineEvents] = useState<TimelineProjectionEvent[]>([]);
  const [showProjectedAssistant, setShowProjectedAssistant] = useState(false);
  const [timelineSettled, setTimelineSettled] = useState(false);
  const [liveActivitySteps, setLiveActivitySteps] = useState<LiveActivityStepState[]>([]);
  const [liveTrace, setLiveTrace] = useState<LiveTraceState | null>(null);

  const pendingUserMessageRef = useRef<WorkstationChatMessageRecord | null>(pendingUserMessage);
  const streamingAssistantTextRef = useRef(streamingAssistantText);
  const liveActivityStepsRef = useRef<LiveActivityStepState[]>(liveActivitySteps);
  const lastTurnActivityAtRef = useRef<number>(0);

  useEffect(() => {
    pendingUserMessageRef.current = pendingUserMessage;
  }, [pendingUserMessage]);

  useEffect(() => {
    streamingAssistantTextRef.current = streamingAssistantText;
  }, [streamingAssistantText]);

  useEffect(() => {
    liveActivityStepsRef.current = liveActivitySteps;
  }, [liveActivitySteps]);

  const setPendingUserMessage = useCallback((message: WorkstationChatMessageRecord | null) => {
    pendingUserMessageRef.current = message;
    setPendingUserMessageState(message);
  }, []);

  const markTurnActivity = useCallback(() => {
    lastTurnActivityAtRef.current = Date.now();
  }, []);

  return {
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
  };
}

type MemoryProfileStateOptions = {
  queryClient: ChatQueryCache;
  profileQueryKey: string;
  memoryQueryKey: string;
  defaultProfile: SageProfileSnapshot;
  defaultMemory: SageMemorySnapshot;
};

export function useChatMemoryProfileState(options: MemoryProfileStateOptions) {
  const [profileSnapshot, setProfileSnapshot] = useState<SageProfileSnapshot>(
    () => options.queryClient.peek<SageProfileSnapshot>(options.profileQueryKey) ?? options.defaultProfile,
  );
  const [sageSetupState, setSageSetupState] = useState<SageSetupSurfaceState>('loading');
  const [sageSetupMessage, setSageSetupMessage] = useState<string | null>(null);
  const [bootstrapAnswer, setBootstrapAnswer] = useState('');
  const [memorySnapshot, setMemorySnapshot] = useState<SageMemorySnapshot>(
    () => options.queryClient.peek<SageMemorySnapshot>(options.memoryQueryKey) ?? options.defaultMemory,
  );

  return {
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
  };
}

export function useChatProviderModelState(disconnectedOption: ChatModelOption) {
  const [selectedExecutionPlacement] = useState<'local'>('local');
  const [machineTrust, setMachineTrust] = useState<ChatMachineTrust>('personal');
  const [autonomyMode, setAutonomyMode] = useState<ChatAutonomyMode>('default');
  const [modelOptions, setModelOptions] = useState<ChatModelOption[]>([disconnectedOption]);
  const [selectedModel, setSelectedModel] = useState<string>('default');
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogRecord[]>([]);
  const [providerProfiles, setProviderProfiles] = useState<ProviderProfileRecord[]>([]);
  const [toolPolicy, setToolPolicy] = useState<SageToolPolicyRecord[]>([]);
  const [connectorCredentials, setConnectorCredentials] = useState<VaultCredentialRecord[]>([]);
  const [browserGatewayDoctor, setBrowserGatewayDoctor] = useState<GatewayReadinessDoctorPayload | null>(null);
  const [reasoningEffort, setReasoningEffort] = useState<ChatReasoningEffort>('medium');
  const [isPersistingModelSelection, setIsPersistingModelSelection] = useState(false);

  return {
    selectedExecutionPlacement,
    machineTrust,
    setMachineTrust,
    autonomyMode,
    setAutonomyMode,
    modelOptions,
    setModelOptions,
    selectedModel,
    setSelectedModel,
    providerCatalog,
    setProviderCatalog,
    providerProfiles,
    setProviderProfiles,
    toolPolicy,
    setToolPolicy,
    connectorCredentials,
    setConnectorCredentials,
    browserGatewayDoctor,
    setBrowserGatewayDoctor,
    reasoningEffort,
    setReasoningEffort,
    isPersistingModelSelection,
    setIsPersistingModelSelection,
  };
}

export function useChatUiPanelsState() {
  const [titlebarActionsHost, setTitlebarActionsHost] = useState<HTMLElement | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmittingBootstrap, setIsSubmittingBootstrap] = useState(false);
  const [isRetryingSageSetup, setIsRetryingSageSetup] = useState(false);
  const [hasEnteredConversationFlow, setHasEnteredConversationFlow] = useState(false);
  const [smallModelWarningVisible, setSmallModelWarningVisible] = useState(false);
  const [resolvingApprovalId, setResolvingApprovalId] = useState<string | null>(null);
  const [mutatingMemory, setMutatingMemory] = useState<string | null>(null);
  const [memoryFilter, setMemoryFilter] = useState<string>('all');
  const [isApprovalsSheetOpen, setIsApprovalsSheetOpen] = useState(false);
  const [isMemorySheetOpen, setIsMemorySheetOpen] = useState(false);

  return {
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
    resolvingApprovalId,
    setResolvingApprovalId,
    mutatingMemory,
    setMutatingMemory,
    memoryFilter,
    setMemoryFilter,
    isApprovalsSheetOpen,
    setIsApprovalsSheetOpen,
    isMemorySheetOpen,
    setIsMemorySheetOpen,
  };
}

export function useChatMemoryEditorState(defaultDraft: SageMemoryDraft) {
  const [memoryDraft, setMemoryDraft] = useState<SageMemoryDraft>(defaultDraft);
  const [pendingDeleteMemoryId, setPendingDeleteMemoryId] = useState<string | null>(null);
  return {
    memoryDraft,
    setMemoryDraft,
    pendingDeleteMemoryId,
    setPendingDeleteMemoryId,
  };
}

export type ModelCatalogHelpers = {
  normalizeProviderCatalogRecords: (payload: unknown) => ProviderCatalogRecord[];
  normalizeProviderProfiles: (payload: unknown) => ProviderProfileRecord[];
  normalizeChatModelOptions: (payload: unknown) => ChatModelOption[];
  hostedCreditsFallbackProvider: () => ProviderCatalogRecord;
  workspaceDefaultModelOption: (providers: ProviderCatalogRecord[]) => ChatModelOption;
};

export function refreshProviderModelCatalog(
  payload: unknown,
  helpers: ModelCatalogHelpers,
): {
  providers: ProviderCatalogRecord[];
  profiles: ProviderProfileRecord[];
  options: ChatModelOption[];
} {
  const normalizedProviders = helpers.normalizeProviderCatalogRecords(
    (payload as { catalogPayload?: unknown })?.catalogPayload,
  );
  const normalizedProfiles = helpers.normalizeProviderProfiles(
    (payload as { profilesPayload?: unknown })?.profilesPayload,
  );
  const providers = normalizedProviders.length > 0
    ? normalizedProviders
    : [helpers.hostedCreditsFallbackProvider()];
  const options = helpers.normalizeChatModelOptions(
    normalizedProviders.length > 0
      ? (payload as { catalogPayload?: unknown })?.catalogPayload
      : { providers },
  );
  return {
    providers,
    profiles: normalizedProfiles,
    options: options.length > 0 ? options : [helpers.workspaceDefaultModelOption(providers)],
  };
}

export function resolveContextWindowFromOptions(
  option: ChatModelOption | null | undefined,
): number | null {
  if (!option) {
    return null;
  }
  const resolved = option.contextWindowTokens;
  if (typeof resolved !== 'number' || !Number.isFinite(resolved) || resolved <= 0) {
    return null;
  }
  return Math.floor(resolved);
}
