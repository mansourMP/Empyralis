'use client';

import { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { CommandSheet } from '@/lib/ui/command-sheet';
import {
  FormField,
  FormGrid,
  FormInput,
  FormReadout,
  FormSelect,
  FormTextarea,
} from '@/lib/ui/form-controls';
import { ModalSection } from '@/lib/ui/modal';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { StateBanner } from '@/lib/ui/state-banner';
import type {
  ConnectedExternalAgentRecord,
  DeployedAgentRecord,
} from '@/lib/workspace/workstation-client';
import { WorkspaceChannelPairingSurface } from '@/lib/workspace/workspace-channel-pairing-surface';
import type {
  LaunchReadinessItem,
  ProviderCatalogSnapshot,
  RuntimeAttachmentSnapshot,
  StudioTemplate,
  TelegramReadinessSnapshot,
  WizardMode,
  WizardState,
} from './types';
import {
  CREATE_AGENT_WIZARD_STEPS,
  CUSTOM_STUDIO_TEMPLATE,
  DEFAULT_SAFE_MONTHLY_COST_CAP_USD,
  DEFAULT_STUDIO_TEMPLATE,
  DEPLOYED_AGENT_WIZARD_STEPS,
  STUDIO_AI_TIER_OPTIONS,
  STUDIO_APPROVAL_MODE_OPTIONS,
  STUDIO_DEFAULT_RUNTIME_PLACEMENT,
  STUDIO_RUNTIME_ACCESS_MODE_OPTIONS,
  STUDIO_RUNTIME_OPTIONS,
  STUDIO_TOOL_OPTIONS,
} from './constants';
import {
  AgentLaunchChecklist,
  AgentSafetySummary,
  ContextPresetControl,
  RetentionPresetControl,
  RuntimeModeSelector,
} from './components';
import {
  readString,
  readRecord,
  readNumber,
  normalizeRuntimeAttachments,
  selfHostedNodeGateReason,
  selfHostedNodeHealthLabel,
  formatRelativeTime,
  formatUsdPer1k,
  formatContextWindow,
  humanizeToken,
  parseKnowledgeSources,
  studioTemplateById,
  applyStudioTemplate,
  readProviderCatalogItems,
  normalizeProviderCatalog,
  normalizeTelegramReadiness,
  selectedProviderId,
  selectedModelId,
  providerCatalogById,
  formatDeploymentModelLabel,
  inferAiTierFromProviderModel,
  normalizeWizardAiTier,
  runtimeTargetForPlacement,
  runtimeSupplierForPlacement,
  applyApprovalModeToWizardState,
  pickStudioModelForTier,
  resolveProviderModelForTier,
  applyProviderCatalogDefaults,
  buildWizardState,
  buildCreateDraftWizardState,
  buildChannelPayload,
  buildDeploymentConfig,
  summarizeStudioErrorMessage,
} from './utils';

type CreateAgentKind = 'native' | 'external';
type ExternalConnectionMode = 'public_https' | 'agent_computer';

type ExternalProviderKind =
  | 'openclaw'
  | 'nemoclaw'
  | 'custom_http'
  | 'hermes'
  | 'mcp'
  | 'a2a'
  | 'custom';

type ExternalAgentFormState = {
  name: string;
  providerKind: ExternalProviderKind;
  connectionMode: ExternalConnectionMode;
  agentComputerId: string;
  baseUrl: string;
  manifestUrl: string;
  chatUrl: string;
  eventsUrl: string;
  artifactsUrl: string;
  secretRef: string;
  manifestJson: string;
};

const EXTERNAL_PROVIDER_OPTIONS: Array<{ value: ExternalProviderKind; label: string; hint: string }> = [
  { value: 'openclaw', label: 'OpenClaw', hint: 'Use this for OpenClaw-style local or remote agent runtimes.' },
  { value: 'hermes', label: 'Hermes', hint: 'Use this for Hermes-compatible agent runtimes.' },
  { value: 'nemoclaw', label: 'NemoClaw', hint: 'Use this for NemoClaw-compatible agent runtimes.' },
  { value: 'custom_http', label: 'Custom HTTPS agent', hint: 'Use this for a provider-neutral external agent manifest.' },
  { value: 'mcp', label: 'MCP server', hint: 'Use this for a governed remote MCP capability surface.' },
  { value: 'a2a', label: 'A2A agent', hint: 'Use this for Agent-to-Agent compatible runtimes.' },
  { value: 'custom', label: 'Other', hint: 'Use this when the provider is not listed yet.' },
];

const EMPTY_EXTERNAL_AGENT_FORM: ExternalAgentFormState = {
  name: 'OpenClaw assistant',
  providerKind: 'openclaw',
  connectionMode: 'agent_computer',
  agentComputerId: '',
  baseUrl: '',
  manifestUrl: '',
  chatUrl: '',
  eventsUrl: '',
  artifactsUrl: '',
  secretRef: '',
  manifestJson: '',
};

function buildExternalAgentEndpoints(form: ExternalAgentFormState): Record<string, string> {
  const endpoints: Record<string, string> = {};
  const fields: Array<[keyof ExternalAgentFormState, string]> = [
    ['baseUrl', 'base_url'],
    ['manifestUrl', 'manifest_url'],
    ['chatUrl', 'chat_url'],
    ['eventsUrl', 'events_url'],
    ['artifactsUrl', 'artifacts_url'],
  ];

  for (const [field, endpointKey] of fields) {
    const value = readString(form[field]).trim();
    if (value) {
      endpoints[endpointKey] = value;
    }
  }

  return endpoints;
}

function localRuntimeBaseUrl(providerKind: ExternalProviderKind, explicitBaseUrl: string): string {
  const value = explicitBaseUrl.trim();
  if (value) {
    return value;
  }
  if (providerKind === 'openclaw') {
    return 'http://127.0.0.1:18789';
  }
  return '';
}

function runtimeAttachmentOptionId(item: RuntimeAttachmentSnapshot): string {
  return item.attachmentId || item.runtimeProfileId || item.runtimeNodeId;
}

function runtimeAttachmentDisplayKey(item: RuntimeAttachmentSnapshot): string {
  return `${readString(item.label, 'Agent Computer').trim().toLowerCase()}::${readString(item.nodeKind, item.attachmentKind).trim().toLowerCase()}`;
}

function runtimeAttachmentRank(item: RuntimeAttachmentSnapshot): number {
  return (item.online ? 2 : 0) + (item.healthy ? 1 : 0);
}

function dedupeRuntimeAttachmentOptions(items: RuntimeAttachmentSnapshot[]): RuntimeAttachmentSnapshot[] {
  const byDisplayKey = new Map<string, RuntimeAttachmentSnapshot>();
  for (const item of items) {
    const displayKey = runtimeAttachmentDisplayKey(item);
    const current = byDisplayKey.get(displayKey);
    if (!current || runtimeAttachmentRank(item) > runtimeAttachmentRank(current)) {
      byDisplayKey.set(displayKey, item);
    }
  }
  return Array.from(byDisplayKey.values());
}

function agentComputerProxyReady(item: RuntimeAttachmentSnapshot): boolean {
  return item.online && item.healthy && item.capabilities.includes('external_agent_proxy');
}

function agentComputerReadinessLabel(item: RuntimeAttachmentSnapshot): string {
  if (!item.online) {
    return 'Offline';
  }
  if (!item.healthy) {
    return 'Needs attention';
  }
  if (!item.capabilities.includes('external_agent_proxy')) {
    return 'Local bridge unavailable';
  }
  return 'Ready';
}

function localExternalProviderReadinessLabel(providerKind: ExternalProviderKind): string {
  if (providerKind === 'openclaw') {
    return 'OpenClaw can use the selected Agent Computer bridge without exposing a local URL.';
  }
  if (providerKind === 'hermes' || providerKind === 'nemoclaw') {
    return 'Adapter slot only. Configure the local adapter on the selected Agent Computer before connecting.';
  }
  return 'Configure the local adapter on the selected Agent Computer before connecting.';
}

function buildLocalExternalAgentManifest(
  form: ExternalAgentFormState,
  agentComputerId: string,
  manifest: Record<string, unknown>,
): Record<string, unknown> {
  const baseUrl = localRuntimeBaseUrl(form.providerKind, form.baseUrl);
  const healthUrl = form.providerKind === 'openclaw' && baseUrl
    ? `${baseUrl.replace(/\/+$/, '')}/health`
    : '';
  const endpoints: Record<string, string> = {};
  if (baseUrl) {
    endpoints.base_url = baseUrl;
  }
  if (healthUrl) {
    endpoints.health_url = healthUrl;
  }
  if (form.chatUrl.trim()) {
    endpoints.chat_url = form.chatUrl.trim();
  }
  const capabilities = Array.from(new Set([
    ...((Array.isArray(manifest.capabilities) ? manifest.capabilities : []) as string[]),
    'health',
    ...(form.chatUrl.trim() ? ['chat'] : []),
  ]));
  return {
    ...manifest,
    provider_kind: externalAgentProviderKindForCreate(form.providerKind),
    protocols: Array.isArray(manifest.protocols) ? manifest.protocols : [{ kind: form.providerKind, version: '1' }],
    capabilities,
    endpoints: {
      ...endpoints,
      ...(readRecord(manifest.endpoints) as Record<string, string>),
    },
    local_connector: {
      required: true,
      mode: 'agent_computer_proxy',
      reason: 'Local runtime stays on the selected Agent Computer.',
      agent_computer_id: agentComputerId,
      agent_computer_capability: 'external_agent_proxy',
    },
    surface_sections: Array.isArray(manifest.surface_sections)
      ? manifest.surface_sections
      : healthUrl
        ? [{
            id: 'runtime_status',
            title: 'Runtime status',
            description: 'Health reported through the selected Agent Computer.',
            category: 'configuration',
            priority: 10,
            display_kind: 'key_value',
            icon: 'key_value',
            capability_required: 'health',
            data_endpoint_ref: 'health_url',
          }]
        : [],
  };
}

function parseExternalAgentManifestJson(value: string): Record<string, unknown> {
  const trimmed = value.trim();
  if (!trimmed) {
    return {};
  }
  const parsed = JSON.parse(trimmed);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('Manifest JSON must be a JSON object.');
  }
  return parsed as Record<string, unknown>;
}

function externalAgentProviderKindForCreate(providerKind: ExternalProviderKind): string {
  return ['custom_http', 'mcp', 'a2a'].includes(providerKind) ? 'custom' : providerKind;
}

function externalAgentManifestForCreate(
  providerKind: ExternalProviderKind,
  manifest: Record<string, unknown>,
): Record<string, unknown> {
  if (!['custom_http', 'mcp', 'a2a'].includes(providerKind)) {
    return manifest;
  }
  if (Array.isArray(manifest.protocols) || readString(manifest.protocol)) {
    return manifest;
  }
  return {
    ...manifest,
    protocols: [{ kind: providerKind, version: '1' }],
  };
}

export interface AgentWizardProps {
  open: boolean;
  mode: WizardMode;
  onClose: () => void;
  initialState?: WizardState;
  templateId?: string;
  onSuccess: (record: DeployedAgentRecord) => void;
  onExternalSuccess?: (record: ConnectedExternalAgentRecord) => void;
  workspaceId: string;
  bootstrap: any;
  services: any;
  providerCatalog: ProviderCatalogSnapshot[];
  runtimeAttachments: RuntimeAttachmentSnapshot[];
  isLoadingProviderCatalog: boolean;
  isLoadingRuntimeAttachments: boolean;
  selectedAgent?: DeployedAgentRecord | null;
}

export function AgentWizard({
  open,
  mode,
  onClose,
  initialState,
  templateId,
  onSuccess,
  onExternalSuccess,
  workspaceId,
  bootstrap,
  services,
  providerCatalog,
  runtimeAttachments,
  isLoadingProviderCatalog,
  isLoadingRuntimeAttachments,
  selectedAgent,
}: AgentWizardProps) {
  const [wizardStepIndex, setWizardStepIndex] = useState(0);
  const [wizardState, setWizardState] = useState<WizardState>(() => initialState ?? buildWizardState(null));
  const [wizardErrorMessage, setWizardErrorMessage] = useState<string | null>(null);
  const [isSubmittingWizard, setIsSubmittingWizard] = useState(false);
  const [createAgentKind, setCreateAgentKind] = useState<CreateAgentKind>('native');
  const [externalAgentForm, setExternalAgentForm] = useState<ExternalAgentFormState>(EMPTY_EXTERNAL_AGENT_FORM);
  const [isTelegramSetupOpen, setIsTelegramSetupOpen] = useState(false);
  const [selectedTelegramReadiness, setSelectedTelegramReadiness] = useState<TelegramReadinessSnapshot | null>(null);
  const [isLoadingTelegramReadiness, setIsLoadingTelegramReadiness] = useState(false);

  const activeWizardSteps = mode === 'create' ? CREATE_AGENT_WIZARD_STEPS : DEPLOYED_AGENT_WIZARD_STEPS;
  const wizardStep = activeWizardSteps[Math.min(wizardStepIndex, activeWizardSteps.length - 1)] ?? activeWizardSteps[0];
  const isExternalCreate = mode === 'create' && createAgentKind === 'external';

  const providerCatalogIndex = useMemo(
    () => providerCatalogById(providerCatalog),
    [providerCatalog],
  );

  const selectedProviderCatalog = useMemo(
    () => providerCatalogIndex[wizardState.providerId] ?? null,
    [providerCatalogIndex, wizardState.providerId],
  );

  const selectedProviderModelCatalog = useMemo(
    () => selectedProviderCatalog?.models.find((item) => item.id === wizardState.modelId) ?? null,
    [selectedProviderCatalog, wizardState.modelId],
  );

  const selfHostedNodeOptions = useMemo(
    () => runtimeAttachments
      .filter((item) => item.attachmentKind === 'self_hosted_business_node' && item.runtimeProfileId)
      .sort((left, right) => left.label.localeCompare(right.label)),
    [runtimeAttachments],
  );

  const agentComputerOptions = useMemo(
    () => dedupeRuntimeAttachmentOptions(
      runtimeAttachments
        .filter((item) => item.attachmentKind === 'local_companion' || item.capabilities.includes('external_agent_proxy')),
    ).sort((left, right) => left.label.localeCompare(right.label)),
    [runtimeAttachments],
  );

  const selectedSelfHostedNode = useMemo(
    () => selfHostedNodeOptions.find((item) => item.runtimeProfileId === wizardState.selfHostedRuntimeProfileId) ?? null,
    [selfHostedNodeOptions, wizardState.selfHostedRuntimeProfileId],
  );

  const selectedAgentComputer = useMemo(
    () => agentComputerOptions.find((item) => runtimeAttachmentOptionId(item) === externalAgentForm.agentComputerId) ?? null,
    [agentComputerOptions, externalAgentForm.agentComputerId],
  );

  const selfHostedWizardNodeBlocker = useMemo(() => {
    if (wizardState.runtimePlacement !== 'customer_hosted') {
      return null;
    }
    return selfHostedNodeGateReason(selectedSelfHostedNode);
  }, [selectedSelfHostedNode, wizardState.runtimePlacement]);

  const selectedStudioTemplate = useMemo(
    () => studioTemplateById(templateId),
    [templateId],
  );

  const selectedWizardConnector = useMemo(
    () => selectedTelegramReadiness?.connectors.find((item) => item.id === wizardState.telegramConnectorId) ?? null,
    [selectedTelegramReadiness, wizardState.telegramConnectorId],
  );

  const selectedRuntimeAccessModeOption = useMemo(
    () => STUDIO_RUNTIME_ACCESS_MODE_OPTIONS.find((option) => option.value === wizardState.runtimeAccessMode) ?? STUDIO_RUNTIME_ACCESS_MODE_OPTIONS[0],
    [wizardState.runtimeAccessMode],
  );

  const hasGatewayOnlineTarget = useMemo(
    () => bootstrap.runtime.runtimeTargets.some((target: any) => target.id === 'local_companion' && target.online),
    [bootstrap.runtime.runtimeTargets],
  );
  const hasCloudComputerAvailableTarget = useMemo(
    () => bootstrap.runtime.runtimeTargets.some((target: any) => target.id === 'sage_cloud_computer' && target.available),
    [bootstrap.runtime.runtimeTargets],
  );
  const editAgentId = readString(selectedAgent?.id);

  async function loadTelegramReadiness(agentId?: string | null) {
    setIsLoadingTelegramReadiness(true);
    try {
      const payload = await services.client.getDeployedAgentTelegramReadiness({
        deployedAgentId: agentId || undefined,
        allowMissing: true,
      });
      const nextReadiness = normalizeTelegramReadiness(payload as any);
      setSelectedTelegramReadiness(nextReadiness);
    } catch (error) {
      setSelectedTelegramReadiness(null);
    } finally {
      setIsLoadingTelegramReadiness(false);
    }
  }

  useEffect(() => {
    if (open) {
      if (initialState) {
        setWizardState(initialState);
      } else if (mode === 'edit' && selectedAgent) {
        setWizardState(buildWizardState(selectedAgent));
      } else if (mode === 'create') {
        const template = studioTemplateById(templateId);
        setWizardState(applyProviderCatalogDefaults({
          ...applyStudioTemplate(buildWizardState(null), template),
          customerChannel: 'draft',
          telegramEnabled: false,
          telegramConnectorId: '',
          telegramEndpointKey: '',
          runtimePlacement: STUDIO_DEFAULT_RUNTIME_PLACEMENT,
          runtimeTarget: runtimeTargetForPlacement(STUDIO_DEFAULT_RUNTIME_PLACEMENT),
          runtimeSupplierKind: runtimeSupplierForPlacement(STUDIO_DEFAULT_RUNTIME_PLACEMENT),
          selfHostedRuntimeProfileId: '',
          selfHostedPrivacyAccepted: false,
          selfHostedSafetyAccepted: false,
          computerAutomationEnabled: false,
        }, providerCatalog));
      }
      setCreateAgentKind('native');
      setExternalAgentForm(EMPTY_EXTERNAL_AGENT_FORM);
      setWizardStepIndex(0);
      setWizardErrorMessage(null);
      setIsTelegramSetupOpen(false);
      void loadTelegramReadiness(mode === 'edit' ? editAgentId : null);
    }
  }, [open, mode, initialState, editAgentId, templateId]);

  useEffect(() => {
    if (!open || !isTelegramSetupOpen) {
      return;
    }
    const connectors = selectedTelegramReadiness?.connectors ?? [];
    if (connectors.length === 0) {
      return;
    }
    const nextConnector = connectors[0];
    setWizardState((current) => ({
      ...current,
      telegramEnabled: true,
      telegramConnectorId: current.telegramConnectorId.trim() || nextConnector.id,
      telegramEndpointKey: current.telegramEndpointKey.trim() || (nextConnector.endpointKey ?? ''),
    }));
    setIsTelegramSetupOpen(false);
  }, [isTelegramSetupOpen, open, selectedTelegramReadiness]);

  useEffect(() => {
    if (!open || !isTelegramSetupOpen) {
      return;
    }
    const agentId = mode === 'edit' ? editAgentId : undefined;
    const intervalId = window.setInterval(() => {
      void loadTelegramReadiness(agentId);
    }, 3000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [isTelegramSetupOpen, open, mode, editAgentId]);

  function setWizardField<K extends keyof WizardState>(field: K, value: WizardState[K]) {
    setWizardErrorMessage(null);
    setWizardState((current) => ({
      ...current,
      [field]: value,
    }));
  }

  function setExternalAgentField<K extends keyof ExternalAgentFormState>(field: K, value: ExternalAgentFormState[K]) {
    setWizardErrorMessage(null);
    setExternalAgentForm((current) => ({
      ...current,
      [field]: value,
    }));
  }

  async function persistWizard() {
    if (isExternalCreate) {
      const name = externalAgentForm.name.trim();
      if (!name) {
        setWizardErrorMessage('Name the external agent before connecting it.');
        return;
      }

      let manifest: Record<string, unknown>;
      try {
        manifest = parseExternalAgentManifestJson(externalAgentForm.manifestJson);
      } catch (error) {
        setWizardErrorMessage(error instanceof Error ? error.message : 'Manifest JSON is invalid.');
        return;
      }
      const selectedAgentComputerId = externalAgentForm.agentComputerId.trim();
      if (externalAgentForm.connectionMode === 'agent_computer' && !selectedAgentComputerId) {
        setWizardErrorMessage('Choose an Agent Computer before connecting a local runtime.');
        return;
      }
      if (externalAgentForm.connectionMode === 'agent_computer' && selectedAgentComputer && !agentComputerProxyReady(selectedAgentComputer)) {
        setWizardErrorMessage(`Selected Agent Computer is not ready: ${agentComputerReadinessLabel(selectedAgentComputer)}.`);
        return;
      }
      if (
        externalAgentForm.connectionMode === 'agent_computer'
        && externalAgentForm.providerKind !== 'openclaw'
        && !localRuntimeBaseUrl(externalAgentForm.providerKind, externalAgentForm.baseUrl)
        && !externalAgentForm.chatUrl.trim()
      ) {
        setWizardErrorMessage('Configure this provider on the selected Agent Computer before connecting it.');
        return;
      }
      const manifestForCreate = externalAgentForm.connectionMode === 'agent_computer'
        ? buildLocalExternalAgentManifest(externalAgentForm, selectedAgentComputerId, manifest)
        : externalAgentManifestForCreate(externalAgentForm.providerKind, manifest);
      const endpointsForCreate = externalAgentForm.connectionMode === 'agent_computer'
        ? buildExternalAgentEndpoints({ ...externalAgentForm, manifestUrl: '', eventsUrl: '', artifactsUrl: '' })
        : buildExternalAgentEndpoints(externalAgentForm);

      setIsSubmittingWizard(true);
      setWizardErrorMessage(null);
      try {
        const created = await services.client.createConnectedExternalAgent({
          name,
          providerKind: externalAgentProviderKindForCreate(externalAgentForm.providerKind),
          endpoints: endpointsForCreate,
          manifest: manifestForCreate,
          secretRef: externalAgentForm.secretRef.trim() || null,
        });
        const createdRecord = readRecord(created) as ConnectedExternalAgentRecord;
        if (!readString(createdRecord.id)) {
          throw new Error('External agent was connected, but Studio did not receive an agent id.');
        }
        onExternalSuccess?.(createdRecord);
      } catch (error) {
        setWizardErrorMessage(error instanceof Error ? error.message : 'The external agent could not be connected.');
      } finally {
        setIsSubmittingWizard(false);
      }
      return;
    }

    const stateForSave = mode === 'create' ? buildCreateDraftWizardState(wizardState) : wizardState;
    if (mode === 'create') {
      const name = stateForSave.name.trim();
      if (!name) {
        setWizardErrorMessage('Name the agent before creating it.');
        return;
      }
      setIsSubmittingWizard(true);
      setWizardErrorMessage(null);
      try {
        const route = resolveProviderModelForTier(stateForSave.aiTier, providerCatalog);
        const created = await services.client.createDeployedAgent({
          name,
          avatar: null,
          persona: stateForSave.persona,
          systemPrompt: stateForSave.systemPrompt,
          channels: {},
          knowledgeSources: parseKnowledgeSources(stateForSave.knowledgeSourceText),
          runtimeTarget: 'cloud',
          billingPlan: 'free',
          config: {
            studio_agent_mode: 'text_agent',
            runtime_placement: 'managed_cloud',
            runtime_target: 'cloud',
            runtime_supplier: 'empyralis',
            tool_policy: {
              enabled_tools: [],
            },
            memory_policy: {
              memory_enabled: false,
              context_budget_preset: 'standard',
              retention_preset: 'standard',
            },
            escalation_policy: {
              preset: 'standard',
              handoff_mode: 'notify_owner',
            },
            commerce_policy: {
              monthly_cost_cap_usd: Number(DEFAULT_SAFE_MONTHLY_COST_CAP_USD),
            },
            computer_automation: {
              enabled: false,
            },
          },
          metadata: {
            customer_channel: stateForSave.customerChannel,
            preferred_customer_channel: stateForSave.customerChannel,
            runtime_placement: 'managed_cloud',
            runtime_supplier: 'empyralis',
            computer_automation_enabled: false,
            selected_tool_ids: [],
            memory_enabled: false,
            monthly_cost_cap_usd: Number(DEFAULT_SAFE_MONTHLY_COST_CAP_USD),
            escalation_preset: 'standard',
            handoff_mode: 'notify_owner',
          },
          provider: route.providerId || null,
          model: route.modelId || null,
        });
        onSuccess(created as DeployedAgentRecord);
      } catch (error) {
        setWizardErrorMessage(error instanceof Error ? error.message : 'The agent could not be created.');
      } finally {
        setIsSubmittingWizard(false);
      }
      return;
    }

    const dailyMessageLimit = stateForSave.dailyMessageLimit.trim();
    const monthlyCostCapUsd = stateForSave.monthlyCostCapUsd.trim();
    const route = stateForSave.aiSource === 'empyralis_credits'
      ? resolveProviderModelForTier(stateForSave.aiTier, providerCatalog)
      : { providerId: stateForSave.providerId, modelId: stateForSave.modelId };
    const resolvedProviderId = route.providerId || stateForSave.providerId || null;
    const resolvedModelId = route.modelId || stateForSave.modelId || null;

    if (dailyMessageLimit) {
      const parsedLimit = Number(dailyMessageLimit);
      if (!Number.isInteger(parsedLimit) || parsedLimit <= 0) {
        setWizardErrorMessage('Daily message limit must be a whole number greater than zero.');
        return;
      }
      if (!stateForSave.upgradeCtaLabel.trim()) {
        setWizardErrorMessage('Add a limit help label when a daily message limit is enabled.');
        return;
      }
      if (!stateForSave.upgradeCtaUrl.trim()) {
        setWizardErrorMessage('Add a limit help URL when a daily message limit is enabled.');
        return;
      }
    }
    if (monthlyCostCapUsd) {
      const parsedCap = Number(monthlyCostCapUsd);
      if (!Number.isFinite(parsedCap) || parsedCap <= 0) {
        setWizardErrorMessage('Monthly cost cap must be a number greater than zero.');
        return;
      }
    }
    if (stateForSave.computerAutomationEnabled) {
      if (!stateForSave.computerAutomationAllowedDomains.trim()) {
        setWizardErrorMessage('Computer Automation needs at least one allowed domain.');
        return;
      }
      const parsedSessions = Number(stateForSave.computerAutomationMaxSessions.trim());
      if (!Number.isFinite(parsedSessions) || parsedSessions < 1) {
        setWizardErrorMessage('Computer Automation needs at least one allowed session.');
        return;
      }
    }
    if (stateForSave.runtimePlacement === 'customer_hosted') {
      if (!stateForSave.selfHostedRuntimeProfileId.trim()) {
        setWizardErrorMessage('Server/VPS mode requires selecting a runtime.');
        return;
      }
      if (selfHostedWizardNodeBlocker) {
        setWizardErrorMessage(selfHostedWizardNodeBlocker);
        return;
      }
      if (!stateForSave.selfHostedPrivacyAccepted) {
        setWizardErrorMessage('Accept the privacy contract before saving a Server/VPS Business Agent.');
        return;
      }
      if (!stateForSave.selfHostedSafetyAccepted) {
        setWizardErrorMessage('Accept the safety contract before saving a Server/VPS Business Agent.');
        return;
      }
    }
    if (stateForSave.customerChannel === 'telegram' && stateForSave.telegramEnabled && !stateForSave.telegramConnectorId.trim()) {
      setWizardErrorMessage('Choose a Telegram connected app before saving a live-ready Business Agent.');
      return;
    }

    const approvalPolicy = applyApprovalModeToWizardState(stateForSave.approvalMode, {
      escalationPreset: stateForSave.escalationPreset,
      handoffMode: stateForSave.handoffMode,
    });
    const resolvedRuntimeTarget = runtimeTargetForPlacement(stateForSave.runtimePlacement);

    const payload = {
      name: stateForSave.name.trim(),
      avatar: stateForSave.avatar.trim() || null,
      persona: stateForSave.persona.trim(),
      systemPrompt: stateForSave.systemPrompt.trim(),
      channels: buildChannelPayload(stateForSave),
      knowledgeSources: parseKnowledgeSources(stateForSave.knowledgeSourceText),
      runtimeTarget: resolvedRuntimeTarget,
      runtimeProfileId: stateForSave.runtimePlacement === 'customer_hosted'
        ? stateForSave.selfHostedRuntimeProfileId.trim() || null
        : null,
      billingPlan: stateForSave.billingPlan,
      provider: resolvedProviderId,
      model: resolvedModelId,
      config: buildDeploymentConfig({
        ...stateForSave,
        runtimeTarget: resolvedRuntimeTarget,
        escalationPreset: approvalPolicy.escalationPreset,
        handoffMode: approvalPolicy.handoffMode,
      }),
      metadata: {
        public_tier: stateForSave.aiTier,
        model_tier: stateForSave.aiTier,
        empyralis_model_tier: stateForSave.aiTier,
        runtime_placement: stateForSave.runtimePlacement,
        runtime_supplier: runtimeSupplierForPlacement(stateForSave.runtimePlacement),
        computer_automation_enabled: stateForSave.computerAutomationEnabled,
        approval_mode: stateForSave.approvalMode,
        customer_channel: stateForSave.customerChannel,
        self_hosted_runtime_profile_id: stateForSave.runtimePlacement === 'customer_hosted'
          ? stateForSave.selfHostedRuntimeProfileId.trim() || null
          : null,
        self_hosted_privacy_contract_accepted: stateForSave.runtimePlacement === 'customer_hosted'
          ? stateForSave.selfHostedPrivacyAccepted
          : null,
        self_hosted_safety_contract_accepted: stateForSave.runtimePlacement === 'customer_hosted'
          ? stateForSave.selfHostedSafetyAccepted
          : null,
      },
    };

    if (!payload.name) {
      setWizardErrorMessage('A Business Agent needs a public name before it can be saved.');
      return;
    }

    setIsSubmittingWizard(true);
    setWizardErrorMessage(null);
    try {
      const agentId = editAgentId;
      if (!agentId) {
        throw new Error('Select a Business Agent before editing it.');
      }
      const updated = await services.client.updateDeployedAgent({
        deployedAgentId: agentId,
        ...payload,
      });
      onSuccess(updated as DeployedAgentRecord);
    } catch (error) {
      setWizardErrorMessage(error instanceof Error ? error.message : 'The Business Agent could not be saved.');
    } finally {
      setIsSubmittingWizard(false);
    }
  }

  return (
    <CommandSheet
      open={open}
      title={isExternalCreate ? 'Connect external agent' : mode === 'create' ? 'Create your agent' : 'Edit Business Agent'}
      description={
        isExternalCreate
          ? 'Register a hosted agent or connect a local runtime through a paired Agent Computer.'
          : mode === 'create'
          ? 'Name it, describe the job, then connect knowledge, apps, and channels later.'
          : 'Adjust the worker profile, knowledge, customer channel, and safety behavior.'
      }
      onClose={onClose}
      actions={(
        <div className="app-inline-actions">
          {!isExternalCreate && wizardStepIndex > 0 ? (
            <AppButton
              type="button"
              tone="secondary"
              onClick={() => setWizardStepIndex((current) => Math.max(0, current - 1))}
              disabled={isSubmittingWizard}
            >
              Back
            </AppButton>
          ) : null}
          {!isExternalCreate && wizardStepIndex < activeWizardSteps.length - 1 ? (
            <AppButton
              type="button"
              onClick={() => setWizardStepIndex((current) => Math.min(activeWizardSteps.length - 1, current + 1))}
              disabled={isSubmittingWizard}
            >
              Continue
            </AppButton>
          ) : (
            <AppButton
              type="button"
              onClick={() => {
                void persistWizard();
              }}
              disabled={isSubmittingWizard}
            >
              {isExternalCreate ? 'Connect external agent' : mode === 'create' ? 'Create agent' : 'Save changes'}
            </AppButton>
          )}
        </div>
      )}
    >
      <div data-deployed-agent-wizard="root" className="deployed-agents-wizard">
        {!isExternalCreate && activeWizardSteps.length > 1 ? (
          <div className="deployed-agents-wizard__steps">
            {activeWizardSteps.map((step, index) => (
              <button
                type="button"
                key={step.id}
                data-deployed-agent-wizard-step={step.id}
                className="deployed-agents-wizard__step"
                data-active={index === wizardStepIndex ? 'true' : 'false'}
                disabled={isSubmittingWizard}
                onClick={() => setWizardStepIndex(index)}
              >
                <strong className="deployed-agents-wizard__step-title">{step.label}</strong>
              </button>
            ))}
          </div>
        ) : null}

        {summarizeStudioErrorMessage(wizardErrorMessage) ? (
          <StateBanner tone="warning" title="Agent setup needs attention">
            {summarizeStudioErrorMessage(wizardErrorMessage)}
          </StateBanner>
        ) : null}

        {mode === 'create' ? (
          <div className="deployed-agents-wizard__create-draft">
            <div className="deployed-agents-wizard__kind-switch" role="group" aria-label="Agent type">
              <button
                type="button"
                className={joinClassNames(
                  'deployed-agents-wizard__kind-option',
                  createAgentKind === 'native' && 'deployed-agents-wizard__kind-option--selected',
                )}
                aria-pressed={createAgentKind === 'native'}
                disabled={isSubmittingWizard}
                onClick={() => setCreateAgentKind('native')}
              >
                <strong>Native</strong>
                <span>Managed in Studio</span>
              </button>
              <button
                type="button"
                className={joinClassNames(
                  'deployed-agents-wizard__kind-option',
                  createAgentKind === 'external' && 'deployed-agents-wizard__kind-option--selected',
                )}
                aria-pressed={createAgentKind === 'external'}
                disabled={isSubmittingWizard}
                onClick={() => setCreateAgentKind('external')}
              >
                <strong>External runtime</strong>
                <span>Connect hosted or local agent systems</span>
              </button>
            </div>
            {isExternalCreate ? (
              <div className="deployed-agents-wizard__quickstart">
                <p className="deployed-agents-wizard__connection-note">
                  Connect a hosted agent by HTTPS, or route a local agent runtime through a paired Agent Computer.
                </p>
                <div className="deployed-agents-wizard__kind-switch" role="group" aria-label="External connection path">
                  <button
                    type="button"
                    className={joinClassNames(
                      'deployed-agents-wizard__kind-option',
                      externalAgentForm.connectionMode === 'agent_computer' && 'deployed-agents-wizard__kind-option--selected',
                    )}
                    aria-pressed={externalAgentForm.connectionMode === 'agent_computer'}
                    disabled={isSubmittingWizard}
                    onClick={() => setExternalAgentField('connectionMode', 'agent_computer')}
                  >
                    <strong>Local runtime on Agent Computer</strong>
                    <span>Use this when the runtime lives on your hardware and Empyralis should connect through the paired Agent Computer.</span>
                  </button>
                  <button
                    type="button"
                    className={joinClassNames(
                      'deployed-agents-wizard__kind-option',
                      externalAgentForm.connectionMode === 'public_https' && 'deployed-agents-wizard__kind-option--selected',
                    )}
                    aria-pressed={externalAgentForm.connectionMode === 'public_https'}
                    disabled={isSubmittingWizard}
                    onClick={() => setExternalAgentField('connectionMode', 'public_https')}
                  >
                    <strong>Hosted runtime</strong>
                    <span>A public HTTPS agent with manifest endpoints.</span>
                  </button>
                </div>
                <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                  <FormField label="Agent name">
                    <FormInput
                      value={externalAgentForm.name}
                      onChange={(event) => setExternalAgentField('name', event.currentTarget.value)}
                      placeholder="OpenClaw assistant"
                    />
                  </FormField>
                  <FormField label="External agent type">
                    <FormSelect
                      value={externalAgentForm.providerKind}
                      onChange={(event) => setExternalAgentField('providerKind', event.currentTarget.value as ExternalProviderKind)}
                    >
                      {EXTERNAL_PROVIDER_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </FormSelect>
                  </FormField>
                </FormGrid>
                {externalAgentForm.connectionMode === 'agent_computer' ? (
                  <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                    <FormField
                      label="Agent Computer"
                      hint="The selected Agent Computer must be online, trusted, approved, and ready for local external agents."
                    >
                      <FormSelect
                        value={externalAgentForm.agentComputerId}
                        onChange={(event) => setExternalAgentField('agentComputerId', event.currentTarget.value)}
                      >
                        <option value="">Choose Agent Computer</option>
                        {agentComputerOptions.map((computer) => (
                          <option
                            key={runtimeAttachmentOptionId(computer)}
                            value={runtimeAttachmentOptionId(computer)}
                            disabled={!agentComputerProxyReady(computer)}
                          >
                            {computer.label} · {agentComputerReadinessLabel(computer)}
                          </option>
                        ))}
                      </FormSelect>
                    </FormField>
                    <FormReadout
                      label="Runtime bridge"
                      value={agentComputerOptions.length > 0 ? 'Agent Computer bridge' : 'No paired Agent Computer available'}
                    />
                    <FormReadout
                      label="Runtime readiness"
                      value={localExternalProviderReadinessLabel(externalAgentForm.providerKind)}
                    />
                  </FormGrid>
                ) : (
                  <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                    <FormField label="Base URL">
                      <FormInput
                        value={externalAgentForm.baseUrl}
                        onChange={(event) => setExternalAgentField('baseUrl', event.currentTarget.value)}
                        placeholder="https://agent.example.com"
                      />
                    </FormField>
                    <FormField label="Manifest URL">
                      <FormInput
                        value={externalAgentForm.manifestUrl}
                        onChange={(event) => setExternalAgentField('manifestUrl', event.currentTarget.value)}
                        placeholder="https://agent.example.com/.well-known/agent-manifest.json"
                      />
                    </FormField>
                  </FormGrid>
                )}
                <details className="deployed-agents-wizard__advanced">
                  <summary>{externalAgentForm.connectionMode === 'agent_computer' ? 'Advanced local diagnostics' : 'More settings'}</summary>
                  <div className="deployed-agents-wizard__advanced-body">
                    {externalAgentForm.connectionMode === 'agent_computer' ? (
                      <FormField label="Local adapter address" hint="Advanced diagnostics only. This address is reached by the selected Agent Computer, never by the browser or cloud server.">
                        <FormInput
                          value={externalAgentForm.baseUrl}
                          onChange={(event) => setExternalAgentField('baseUrl', event.currentTarget.value)}
                          placeholder={externalAgentForm.providerKind === 'openclaw' ? 'Use default OpenClaw adapter' : 'Local adapter address'}
                        />
                      </FormField>
                    ) : null}
                    <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                      <FormField label="Chat endpoint" hint="Optional public HTTPS chat endpoint.">
                        <FormInput
                          value={externalAgentForm.chatUrl}
                          onChange={(event) => setExternalAgentField('chatUrl', event.currentTarget.value)}
                          placeholder="https://agent.example.com/chat"
                        />
                      </FormField>
                      <FormField label="Events endpoint" hint="Optional events or run history endpoint.">
                        <FormInput
                          value={externalAgentForm.eventsUrl}
                          onChange={(event) => setExternalAgentField('eventsUrl', event.currentTarget.value)}
                          placeholder="https://agent.example.com/events"
                        />
                      </FormField>
                      <FormField label="Artifacts endpoint" hint="Optional artifacts/resources endpoint.">
                        <FormInput
                          value={externalAgentForm.artifactsUrl}
                          onChange={(event) => setExternalAgentField('artifactsUrl', event.currentTarget.value)}
                          placeholder="https://agent.example.com/artifacts"
                        />
                      </FormField>
                    </FormGrid>
                    <FormField label="Secret reference" hint="Stored secret reference only. Do not paste raw keys.">
                      <FormInput
                        value={externalAgentForm.secretRef}
                        onChange={(event) => setExternalAgentField('secretRef', event.currentTarget.value)}
                        placeholder="vault://external-agents/openclaw-prod"
                      />
                    </FormField>
                    <FormField label="Manifest JSON" hint="Optional manifest object for immediate Studio sections.">
                      <FormTextarea
                        rows={5}
                        value={externalAgentForm.manifestJson}
                        onChange={(event) => setExternalAgentField('manifestJson', event.currentTarget.value)}
                        placeholder={'{\n  "capabilities": ["chat", "artifacts"],\n  "surface_sections": []\n}'}
                      />
                    </FormField>
                  </div>
                </details>
              </div>
            ) : (
            <div className="deployed-agents-wizard__quickstart">
              <FormField label="Agent name">
                <FormInput
                  value={wizardState.name}
                  onChange={(event) => setWizardField('name', event.currentTarget.value)}
                  placeholder="New agent"
                />
              </FormField>
              <FormField label="What should it do?">
                <FormTextarea
                  rows={8}
                  value={wizardState.systemPrompt}
                  onChange={(event) => setWizardField('systemPrompt', event.currentTarget.value)}
                  placeholder={selectedStudioTemplate.systemPrompt}
                />
              </FormField>
            </div>
            )}
          </div>
        ) : (
          <ModalSection title={wizardStep.label} description={wizardStep.description}>
            {wizardStep.id === 'overview' ? (
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="Business Agent name" hint="The public name customers will see.">
                  <FormInput
                    value={wizardState.name}
                    onChange={(event) => setWizardField('name', event.currentTarget.value)}
                    placeholder="Bluebird Cafe"
                  />
                </FormField>
                <FormField label="Avatar URL" hint="Optional public avatar or brand mark.">
                  <FormInput
                    value={wizardState.avatar}
                    onChange={(event) => setWizardField('avatar', event.currentTarget.value)}
                    placeholder="https://example.com/avatar.png"
                  />
                </FormField>
                <FormField label="Tone" hint="Optional voice and style for this Business Agent.">
                  <FormTextarea
                    rows={3}
                    value={wizardState.persona}
                    onChange={(event) => setWizardField('persona', event.currentTarget.value)}
                    placeholder="Friendly, concise, formal, warm, luxury, clinic front desk..."
                  />
                  <div className="deployed-agents-wizard__memory-toggle">
                    <div className="sage-tool-row__copy">
                      <strong className="sage-tool-row__title">Enable persistent memory</strong>
                      <p className="sage-tool-row__description">Business Agent remembers useful customer facts across conversations.</p>
                    </div>
                    <button
                      type="button"
                      className={joinClassNames('sage-tool-toggle', wizardState.memoryEnabled && 'sage-tool-toggle--enabled')}
                      role="switch"
                      aria-checked={wizardState.memoryEnabled}
                      onClick={() => setWizardField('memoryEnabled', !wizardState.memoryEnabled)}
                    >
                      <span className="sage-tool-toggle__thumb" />
                    </button>
                  </div>
                </FormField>
                <FormField label="Purpose and behavior" hint="Core customer instructions this Business Agent follows.">
                  <FormTextarea
                    rows={6}
                    value={wizardState.systemPrompt}
                    onChange={(event) => setWizardField('systemPrompt', event.currentTarget.value)}
                    placeholder="Answer menu questions, check specials and availability, confirm orders clearly, and escalate edge cases to a human."
                  />
                </FormField>
                <FormField label="Files and sources" hint="Menu, catalog, FAQ, website, or Google Sheet references, one per line.">
                  <FormTextarea
                    rows={6}
                    value={wizardState.knowledgeSourceText}
                    onChange={(event) => setWizardField('knowledgeSourceText', event.currentTarget.value)}
                    placeholder={'kb://menu\nsheet://daily-menu'}
                  />
                  <ContextPresetControl
                    value={wizardState.contextBudgetPreset}
                    onSelect={(nextValue) => setWizardField('contextBudgetPreset', nextValue)}
                  />
                  </FormField>
                </FormGrid>
            ) : null}

          {wizardStep.id === 'knowledge' ? (
            <div className="app-stack-3">
              <FormField label="Files and sources" hint="Paste a sheet, text document, URL, or leave empty and add it later.">
                <FormTextarea
                  rows={5}
                  value={wizardState.knowledgeSourceText}
                  onChange={(event) => setWizardField('knowledgeSourceText', event.currentTarget.value)}
                  placeholder={selectedStudioTemplate.knowledgePlaceholder}
                />
              </FormField>
              <FormField label="Instructions" hint="Specific rules this Business Agent must follow when answering customers.">
                <FormTextarea
                  data-deployed-agent-instructions-input="true"
                  rows={6}
                  value={wizardState.systemPrompt}
                  onChange={(event) => setWizardField('systemPrompt', event.currentTarget.value)}
                  placeholder={selectedStudioTemplate.systemPrompt}
                />
              </FormField>
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="Public intro" hint="Optional customer-facing intro for mini-app or channel entry points.">
                  <FormTextarea
                    rows={3}
                    value={wizardState.welcomeIntro}
                    onChange={(event) => setWizardField('welcomeIntro', event.currentTarget.value)}
                    placeholder="Quickly ask questions, check availability, and get help."
                  />
                </FormField>
                <FormField label="Core value" hint="One sentence explaining what this Business Agent does for customers.">
                  <FormTextarea
                    rows={3}
                    value={wizardState.welcomeCoreValue}
                    onChange={(event) => setWizardField('welcomeCoreValue', event.currentTarget.value)}
                    placeholder={selectedStudioTemplate.outcome}
                  />
                </FormField>
              </FormGrid>
            </div>
          ) : null}

          {wizardStep.id === 'tools' ? (
            <div className="app-stack-3">
              <FormField label="Allowed actions" hint="Keep this narrow. Add only the actions and playbooks needed for the job.">
                <div className="deployed-agents-wizard__tool-grid">
                  {STUDIO_TOOL_OPTIONS.map((tool) => {
                    const selected = wizardState.selectedToolIds.includes(tool.id);
                    return (
                      <button
                        key={tool.id}
                        type="button"
                        className={joinClassNames(
                          'deployed-agents-wizard__tool-card',
                          selected && 'deployed-agents-wizard__tool-card--selected',
                        )}
                        aria-pressed={selected}
                        onClick={() => {
                          setWizardState((current) => {
                            const nextSelectedToolIds = current.selectedToolIds.includes(tool.id)
                              ? current.selectedToolIds.filter((item) => item !== tool.id)
                              : [...current.selectedToolIds, tool.id];
                            return {
                              ...current,
                              selectedToolIds: nextSelectedToolIds,
                            };
                          });
                        }}
                      >
                        <span>{selected ? 'Enabled' : 'Disabled'}</span>
                        <strong>{tool.label}</strong>
                        <small>{tool.description}</small>
                      </button>
                    );
                  })}
                </div>
              </FormField>
            </div>
          ) : null}

          {wizardStep.id === 'channels' ? (
            <div className="app-stack-3">
              {selectedTelegramReadiness ? (
                <StateBanner
                  tone={selectedTelegramReadiness.readyForLive ? 'success' : selectedTelegramReadiness.blockers.length > 0 ? 'warning' : 'neutral'}
                  title={selectedTelegramReadiness.readyForLive ? 'Telegram launch path is ready' : 'Telegram launch checks'}
                  detail={selectedTelegramReadiness.nextAction ?? 'Connected app checks are in progress.'}
                >
                  {selectedTelegramReadiness.blockers.length > 0
                    ? selectedTelegramReadiness.blockers.map((item) => item.message).join(' · ')
                    : selectedTelegramReadiness.warnings.map((item) => item.message).join(' · ') || `${selectedTelegramReadiness.connectors.length} connected app${selectedTelegramReadiness.connectors.length === 1 ? '' : 's'} checked.`}
                </StateBanner>
              ) : isLoadingTelegramReadiness ? (
                <SkeletonBlock height="5rem" />
              ) : null}
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="Telegram state" hint="Enable Telegram when this Business Agent is ready for live customer conversations.">
                  <FormSelect
                    value={wizardState.telegramEnabled ? 'enabled' : 'disabled'}
                    onChange={(event) => {
                      const enabled = event.currentTarget.value === 'enabled';
                      setWizardState((current) => ({
                        ...current,
                        telegramEnabled: enabled,
                        customerChannel: enabled
                          ? 'telegram'
                          : (current.customerChannel === 'telegram' ? 'draft' : current.customerChannel),
                      }));
                    }}
                  >
                    <option value="disabled">Keep disabled</option>
                    <option value="enabled">Ready for live deploy</option>
                  </FormSelect>
                </FormField>
                <FormField label="Telegram connected app" hint="Bind to one workspace Telegram bot.">
                  <FormSelect
                    value={wizardState.telegramConnectorId}
                    onChange={(event) => {
                      const nextConnectorId = event.currentTarget.value;
                      const nextConnector = selectedTelegramReadiness?.connectors.find((item) => item.id === nextConnectorId) ?? null;
                      setWizardState((current) => ({
                        ...current,
                        telegramConnectorId: nextConnectorId,
                        telegramEndpointKey: nextConnector?.endpointKey ?? '',
                      }));
                    }}
                    disabled={!wizardState.telegramEnabled || isLoadingTelegramReadiness}
                  >
                    <option value="">
                      {isLoadingTelegramReadiness
                        ? 'Checking Telegram connected apps…'
                        : selectedTelegramReadiness?.connectors.length
                          ? 'Select a Telegram bot'
                          : 'No Telegram connected apps available'}
                    </option>
                    {(selectedTelegramReadiness?.connectors ?? []).map((connector) => (
                      <option key={connector.id} value={connector.id}>
                        {connector.label}
                      </option>
                    ))}
                  </FormSelect>
                </FormField>
              </FormGrid>
              {!isLoadingTelegramReadiness && (selectedTelegramReadiness?.connectors.length ?? 0) === 0 ? (
                <div className="app-stack-3">
                  <StateBanner tone="neutral" title="No Telegram bot connected yet.">
                    Build needs one Telegram bot before this Business Agent can go live.
                  </StateBanner>
                  <div className="app-inline-actions">
                    <AppButton
                      type="button"
                      tone="secondary"
                      onClick={() => {
                        const nextOpenState = !isTelegramSetupOpen;
                        setIsTelegramSetupOpen(nextOpenState);
                        if (nextOpenState) {
                          void loadTelegramReadiness(mode === 'edit' ? editAgentId : null);
                        }
                      }}
                    >
                      {isTelegramSetupOpen ? 'Hide Telegram setup' : 'Connect Telegram bot'}
                    </AppButton>
                  </div>
                  {isTelegramSetupOpen ? (
                    <WorkspaceChannelPairingSurface featureId="settings" />
                  ) : null}
                </div>
              ) : null}
              <FormGrid columns="repeat(auto-fit, minmax(12rem, 1fr))">
                <FormReadout label="Inbound binding key" value={selectedWizardConnector?.endpointKey || wizardState.telegramEndpointKey || 'Select a connector'} />
                <FormReadout label="Bot username" value={selectedWizardConnector?.botUsername || 'Not exposed by connector'} />
                <FormReadout
                  label="Webhook path"
                  value={selectedWizardConnector?.webhookPath || readString(readRecord(selectedTelegramReadiness?.webhook).path_template, 'Awaiting connector')}
                />
                <FormReadout
                  label="Webhook status"
                  value={humanizeToken(readRecord(selectedTelegramReadiness?.webhook).status, 'Checking')}
                />
              </FormGrid>
            </div>
          ) : null}

          {wizardStep.id === 'memory' ? (
            <div className="app-stack-3">
              <div className="deployed-agents-wizard__memory-toggle deployed-agents-wizard__memory-toggle--panel">
                <div className="sage-tool-row__copy">
                  <strong className="sage-tool-row__title">Persistent customer memory</strong>
                  <p className="sage-tool-row__description">
                    Remember useful customer facts across conversations.
                  </p>
                </div>
                <button
                  type="button"
                  className={joinClassNames('sage-tool-toggle', wizardState.memoryEnabled && 'sage-tool-toggle--enabled')}
                  role="switch"
                  aria-checked={wizardState.memoryEnabled}
                  onClick={() => setWizardField('memoryEnabled', !wizardState.memoryEnabled)}
                >
                  <span className="sage-tool-toggle__thumb" />
                </button>
              </div>
              {wizardState.memoryEnabled ? (
                <RetentionPresetControl
                  value={wizardState.retentionPreset}
                  onSelect={(nextValue) => setWizardField('retentionPreset', nextValue)}
                />
              ) : null}
            </div>
          ) : null}

          {wizardStep.id === 'safety' ? (
            <div className="app-stack-3">
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="Human handoff" hint="When the Business Agent should involve a human.">
                  <FormSelect
                    value={wizardState.escalationPreset}
                    onChange={(event) => setWizardField('escalationPreset', event.currentTarget.value)}
                  >
                    <option value="conservative">Escalate early</option>
                    <option value="standard">Standard</option>
                    <option value="autonomous">More autonomous</option>
                  </FormSelect>
                </FormField>
                <FormField label="Handoff mode" hint="Where human handoff should go.">
                  <FormSelect
                    value={wizardState.handoffMode}
                    onChange={(event) => setWizardField('handoffMode', event.currentTarget.value)}
                  >
                    <option value="notify_owner">Notify owner</option>
                    <option value="pause_thread">Pause customer thread</option>
                    <option value="summary_only">Create summary only</option>
                  </FormSelect>
                </FormField>
              </FormGrid>
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="Owner notification destination" hint="Email, Telegram chat, or internal queue for handoff.">
                  <FormInput
                    value={wizardState.ownerNotificationDestination}
                    onChange={(event) => setWizardField('ownerNotificationDestination', event.currentTarget.value)}
                    placeholder="owner@example.com"
                  />
                </FormField>
                <FormField label="Paused message" hint="What customers see when the assistant is paused.">
                  <FormInput
                    value={wizardState.pausedMessage}
                    onChange={(event) => setWizardField('pausedMessage', event.currentTarget.value)}
                    placeholder="A human will follow up shortly."
                  />
                </FormField>
              </FormGrid>
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="Sensitive-topic safety" hint="Enable for health, legal, finance, or restricted customer cases.">
                  <FormSelect
                    value={wizardState.healthSafetyEnabled ? 'enabled' : 'disabled'}
                    onChange={(event) => setWizardField('healthSafetyEnabled', event.currentTarget.value === 'enabled')}
                  >
                    <option value="disabled">Disabled</option>
                    <option value="enabled">Enabled</option>
                  </FormSelect>
                </FormField>
                <FormField label="Safety assistant name" hint="Optional name used in regulated-domain handoffs.">
                  <FormInput
                    value={wizardState.healthSafetyAssistantName}
                    onChange={(event) => setWizardField('healthSafetyAssistantName', event.currentTarget.value)}
                    placeholder="Safety reviewer"
                  />
                </FormField>
              </FormGrid>
            </div>
          ) : null}

          {wizardStep.id === 'test' ? (
            <div className="app-stack-3">
              <StateBanner
                tone="neutral"
                title="Draft review"
                detail="This is the pre-launch checklist. Create the Business Agent, test it inside Studio, then deploy from the detail panel."
              />
              <FormGrid columns="repeat(auto-fit, minmax(12rem, 1fr))">
                <FormReadout label="Template" value={selectedStudioTemplate.title} />
                <FormReadout label="Business Agent" value={wizardState.name || 'Not named'} />
                <FormReadout label="Channel" value={wizardState.customerChannel === 'telegram' ? 'Telegram bot' : wizardState.customerChannel === 'draft' ? 'Draft only' : humanizeToken(wizardState.customerChannel, 'Draft only')} />
                <FormReadout label="Knowledge" value={wizardState.knowledgeSourceText.trim() ? 'Source added' : 'Add later'} />
                <FormReadout label="Actions" value={`${wizardState.selectedToolIds.length} enabled`} />
                <FormReadout label="Memory" value={wizardState.memoryEnabled ? 'Enabled' : 'Disabled'} />
              </FormGrid>
            </div>
          ) : null}

          {wizardStep.id === 'deploy' ? (
            <div className="app-stack-3">
              <StateBanner
                tone="neutral"
                title="Launch contract for this Business Agent"
                detail="Defaults are safe for production. Change these only when the worker needs a different runtime, channel, or spending cap."
              />
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="AI tier" hint="Product-level capability for this Business Agent.">
                  <FormSelect
                    value={wizardState.aiTier}
                    onChange={(event) => {
                      const nextTier = normalizeWizardAiTier(event.currentTarget.value);
                      const route = resolveProviderModelForTier(nextTier, providerCatalog);
                      setWizardState((current) => ({
                        ...current,
                        aiTier: nextTier,
                        providerId: current.aiSource === 'empyralis_credits' ? route.providerId || current.providerId : current.providerId,
                        modelId: current.aiSource === 'empyralis_credits' ? route.modelId || current.modelId : current.modelId,
                      }));
                    }}
                  >
                    {STUDIO_AI_TIER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </FormSelect>
                  <div className="app-surface-field-help">
                    {STUDIO_AI_TIER_OPTIONS.find((item) => item.value === wizardState.aiTier)?.hint}
                  </div>
                </FormField>
                <FormField label="Where it runs" hint="Business Agents start as Cloud workers. Agent Computer options require explicit setup.">
                  <RuntimeModeSelector
                    value={wizardState.runtimePlacement}
                    options={STUDIO_RUNTIME_OPTIONS}
                    hasGatewayOnlineTarget={hasGatewayOnlineTarget}
                    hasCloudComputerAvailableTarget={hasCloudComputerAvailableTarget}
                    onSelect={(nextRuntime) => {
                      setWizardState((current) => ({
                        ...current,
                        runtimePlacement: nextRuntime,
                        runtimeTarget: runtimeTargetForPlacement(nextRuntime),
                      }));
                    }}
                  />
                </FormField>
                <FormField label="Customer handoff mode" hint="How much routine customer work this Business Agent handles before human handoff.">
                  <FormSelect
                    value={wizardState.approvalMode}
                    onChange={(event) => {
                      const nextMode = readString(event.currentTarget.value) as WizardState['approvalMode'];
                      const normalizedMode = nextMode === 'guarded' || nextMode === 'autonomous' ? nextMode : 'balanced';
                      const mapping = applyApprovalModeToWizardState(normalizedMode, {
                        escalationPreset: wizardState.escalationPreset,
                        handoffMode: wizardState.handoffMode,
                      });
                      setWizardState((current) => ({
                        ...current,
                        approvalMode: normalizedMode,
                        escalationPreset: mapping.escalationPreset,
                        handoffMode: mapping.handoffMode,
                      }));
                    }}
                  >
                    {STUDIO_APPROVAL_MODE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </FormSelect>
                  <div className="app-surface-field-help">
                    {STUDIO_APPROVAL_MODE_OPTIONS.find((item) => item.value === wizardState.approvalMode)?.hint}
                  </div>
                </FormField>
                <FormField label="Customer Channels" hint="Primary live channel for this Business Agent.">
                  <FormSelect
                    value={wizardState.customerChannel}
                    onChange={(event) => {
                      const nextChannel = readString(event.currentTarget.value) as WizardState['customerChannel'];
                      setWizardState((current) => ({
                        ...current,
                        customerChannel: nextChannel,
                        telegramEnabled: nextChannel === 'telegram' ? current.telegramEnabled : false,
                      }));
                    }}
                  >
                    <option value="telegram">Telegram bot</option>
                    <option value="draft">Draft only</option>
                    <option value="whatsapp" disabled>WhatsApp Business soon</option>
                    <option value="web_widget" disabled>Web chat soon</option>
                  </FormSelect>
                </FormField>
              </FormGrid>
              <AgentSafetySummary
                approvalMode={wizardState.approvalMode}
                memoryEnabled={wizardState.memoryEnabled}
                monthlyCostCapUsd={wizardState.monthlyCostCapUsd}
                dailyMessageLimit={wizardState.dailyMessageLimit}
              />
              <AgentLaunchChecklist
                hasGatewayOnlineTarget={hasGatewayOnlineTarget}
                hasCloudComputerAvailableTarget={hasCloudComputerAvailableTarget}
                state={wizardState}
              />
              {wizardState.runtimePlacement === 'customer_hosted' ? (
                <div className="app-stack-3">
                  <StateBanner
                    tone="neutral"
                    title="Server/VPS execution boundary"
                    detail="This mode runs only on your selected customer-owned runtime. Platform-hosted compute is not used as fallback."
                  />
                  <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                    <FormField label="Server/VPS runtime" hint="Choose an enrolled Server/VPS runtime in this workspace.">
                      <FormSelect
                        value={wizardState.selfHostedRuntimeProfileId}
                        onChange={(event) => setWizardField('selfHostedRuntimeProfileId', event.currentTarget.value)}
                        disabled={isLoadingRuntimeAttachments}
                      >
                        <option value="">
                          {isLoadingRuntimeAttachments ? 'Loading runtimes...' : selfHostedNodeOptions.length > 0 ? 'Select a runtime' : 'No enrolled runtimes found'}
                        </option>
                        {selfHostedNodeOptions.map((node) => (
                          <option key={node.runtimeProfileId} value={node.runtimeProfileId}>
                            {node.label} ({humanizeToken(node.nodeKind, 'node')})
                          </option>
                        ))}
                      </FormSelect>
                    </FormField>
                    <FormReadout label="Server health" value={selfHostedNodeHealthLabel(selectedSelfHostedNode)} />
                    <FormReadout label="Last status update" value={selectedSelfHostedNode?.heartbeatAt ? formatRelativeTime(selectedSelfHostedNode.heartbeatAt) : 'n/a'} />
                    <FormReadout label="Available controls" value={selectedSelfHostedNode?.capabilities.length ? `${selectedSelfHostedNode.capabilities.length} available` : 'n/a'} />
                  </FormGrid>
                  <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                    <FormField label="Privacy contract" hint="Required before a Server/VPS Business Agent can be saved.">
                      <FormSelect
                        value={wizardState.selfHostedPrivacyAccepted ? 'accepted' : 'pending'}
                        onChange={(event) => setWizardField('selfHostedPrivacyAccepted', event.currentTarget.value === 'accepted')}
                      >
                        <option value="pending">Not accepted</option>
                        <option value="accepted">Accepted</option>
                      </FormSelect>
                    </FormField>
                    <FormField label="Safety contract" hint="Required before a Server/VPS Business Agent can be saved.">
                      <FormSelect
                        value={wizardState.selfHostedSafetyAccepted ? 'accepted' : 'pending'}
                        onChange={(event) => setWizardField('selfHostedSafetyAccepted', event.currentTarget.value === 'accepted')}
                      >
                        <option value="pending">Not accepted</option>
                        <option value="accepted">Accepted</option>
                      </FormSelect>
                    </FormField>
                  </FormGrid>
                  {selfHostedWizardNodeBlocker ? (
                    <StateBanner tone="warning" title="Server/VPS setup required" detail={selfHostedWizardNodeBlocker} />
                  ) : (
                    <StateBanner tone="success" title="Server/VPS ready" detail="Runtime selection and health checks passed for this draft." />
                  )}
                </div>
              ) : null}
              <details className="app-stack-3">
                <summary>Computer Automation Add-on</summary>
                <p className="app-surface-field-help">
                  Off by default. Enable only when this Business Agent must operate websites or apps without APIs.
                </p>
                <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                  <FormField label="Automation" hint="Customer messages cannot start Agent Computer sessions while this is off.">
                    <FormSelect
                      value={wizardState.computerAutomationEnabled ? 'enabled' : 'disabled'}
                      onChange={(event) => setWizardState((current) => ({
                        ...current,
                        computerAutomationEnabled: event.currentTarget.value === 'enabled',
                      }))}
                    >
                      <option value="disabled">Off</option>
                      <option value="enabled">On</option>
                    </FormSelect>
                  </FormField>
                  <FormField label="Access mode" hint={selectedRuntimeAccessModeOption?.hint ?? 'Choose the computer access boundary for this agent.'}>
                    <FormSelect
                      value={wizardState.runtimeAccessMode}
                      disabled={!wizardState.computerAutomationEnabled}
                      onChange={(event) => setWizardField('runtimeAccessMode', event.currentTarget.value as WizardState['runtimeAccessMode'])}
                    >
                      {STUDIO_RUNTIME_ACCESS_MODE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </FormSelect>
                  </FormField>
                  <FormField label="Computer add-on" hint="Choose the computer environment for this add-on.">
                    <FormSelect
                      value={wizardState.computerAutomationRuntimeClass}
                      disabled={!wizardState.computerAutomationEnabled}
                      onChange={(event) => setWizardField('computerAutomationRuntimeClass', event.currentTarget.value as WizardState['computerAutomationRuntimeClass'])}
                    >
                      <option value="virtual_browser">Cloud browser</option>
                      <option value="virtual_desktop">Cloud desktop</option>
                      <option value="virtual_code_sandbox">Cloud code sandbox</option>
                      <option value="local_browser">Browser on my computer</option>
                      <option value="local_desktop">Desktop on my computer</option>
                    </FormSelect>
                  </FormField>
                  <FormField label="Allowed domains" hint="Comma-separated domains. Required when automation is on.">
                    <FormInput
                      value={wizardState.computerAutomationAllowedDomains}
                      disabled={!wizardState.computerAutomationEnabled}
                      onChange={(event) => setWizardField('computerAutomationAllowedDomains', event.currentTarget.value)}
                      placeholder="supplier.example.com, portal.example.com"
                    />
                  </FormField>
                  <FormField label="Max active sessions" hint="Many Business Agents can be registered; active sessions stay capped and the rest queue.">
                    <FormInput
                      type="number"
                      min="1"
                      value={wizardState.computerAutomationMaxSessions}
                      disabled={!wizardState.computerAutomationEnabled}
                      onChange={(event) => setWizardField('computerAutomationMaxSessions', event.currentTarget.value)}
                    />
                  </FormField>
                  <FormField label="Daily budget" hint="Stops automation when the daily cloud-computer budget is hit.">
                    <FormInput
                      type="number"
                      min="0"
                      step="0.01"
                      value={wizardState.computerAutomationDailyBudgetUsd}
                      disabled={!wizardState.computerAutomationEnabled}
                      onChange={(event) => setWizardField('computerAutomationDailyBudgetUsd', event.currentTarget.value)}
                    />
                  </FormField>
                  <FormField label="Monthly budget" hint="Stops automation when the monthly cloud-computer budget is hit.">
                    <FormInput
                      type="number"
                      min="0"
                      step="0.01"
                      value={wizardState.computerAutomationMonthlyBudgetUsd}
                      disabled={!wizardState.computerAutomationEnabled}
                      onChange={(event) => setWizardField('computerAutomationMonthlyBudgetUsd', event.currentTarget.value)}
                    />
                  </FormField>
                </FormGrid>
              </details>
              <details className="app-stack-3">
                <summary>AI model and cost details</summary>
                <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                  <FormField label="AI model provider" hint="Choose the AI model provider for this Business Agent.">
                    <FormSelect
                      data-deployed-agent-provider-select="true"
                      value={wizardState.providerId}
                      onChange={(event) => {
                        const nextProviderId = event.currentTarget.value;
                        const nextProvider = providerCatalogIndex[nextProviderId] ?? null;
                        const nextModelId = nextProvider?.defaultModel && nextProvider.models.some((item: any) => item.id === nextProvider.defaultModel)
                          ? nextProvider.defaultModel
                          : nextProvider?.models[0]?.id || '';
                        setWizardState((current) => ({
                          ...current,
                          providerId: nextProviderId,
                          modelId: nextModelId,
                          aiTier: inferAiTierFromProviderModel(nextProviderId, nextModelId),
                        }));
                      }}
                      disabled={isLoadingProviderCatalog || providerCatalog.length === 0}
                    >
                      <option value="">
                        {isLoadingProviderCatalog ? 'Loading AI model providers…' : 'Select an AI model provider'}
                      </option>
                      {providerCatalog.map((provider) => (
                        <option key={provider.id} value={provider.id}>
                          {provider.label}
                        </option>
                      ))}
                    </FormSelect>
                  </FormField>
                  <FormField label="Model" hint="Choose the model this Business Agent should use.">
                    <FormSelect
                      data-deployed-agent-model-select="true"
                      value={wizardState.modelId}
                      onChange={(event) => {
                        const nextModelId = event.currentTarget.value;
                        setWizardState((current) => ({
                          ...current,
                          modelId: nextModelId,
                          aiTier: inferAiTierFromProviderModel(current.providerId, nextModelId),
                        }));
                      }}
                      disabled={!selectedProviderCatalog || selectedProviderCatalog.models.length === 0}
                    >
                      <option value="">
                        {selectedProviderCatalog ? 'Select a model' : 'Select an AI model provider first'}
                      </option>
                      {(selectedProviderCatalog?.models ?? []).map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.label}
                        </option>
                      ))}
                    </FormSelect>
                  </FormField>
                </FormGrid>
                <FormGrid columns="repeat(auto-fit, minmax(12rem, 1fr))">
                  <FormReadout label="AI route state" value={humanizeToken(selectedProviderCatalog?.state, isLoadingProviderCatalog ? 'Loading' : 'Unknown')} />
                  <FormReadout label="Privacy profile" value={selectedProviderCatalog?.privacyPosture || 'n/a'} />
                  <FormReadout label="Information limit" value={formatContextWindow(selectedProviderModelCatalog?.contextWindowTokens)} />
                </FormGrid>
                <FormReadout
                  label="AI route features"
                  value={
                    selectedProviderModelCatalog?.capabilityLabels.map((label) => humanizeToken(label, label)).join(', ')
                    || selectedProviderCatalog?.capabilityLabels.map((label) => humanizeToken(label, label)).join(', ')
                    || 'No capability labels yet'
                  }
                />
              </details>
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="Budget cap (USD)" hint="Automatic monthly pause threshold for this Business Agent.">
                  <FormInput
                    value={wizardState.monthlyCostCapUsd}
                    onChange={(event) => setWizardField('monthlyCostCapUsd', event.currentTarget.value)}
                    inputMode="decimal"
                    placeholder="250"
                  />
                </FormField>
                <FormField label="Daily message limit" hint="Per external user, reset at UTC midnight. Leave blank to disable free-tier limits.">
                  <FormInput
                    value={wizardState.dailyMessageLimit}
                    onChange={(event) => setWizardField('dailyMessageLimit', event.currentTarget.value)}
                    inputMode="numeric"
                    placeholder="25"
                  />
                </FormField>
              </FormGrid>
              <FormGrid columns="repeat(auto-fit, minmax(14rem, 1fr))">
                <FormField label="Limit help label" hint="Required when message limits are active.">
                  <FormInput
                    value={wizardState.upgradeCtaLabel}
                    onChange={(event) => setWizardField('upgradeCtaLabel', event.currentTarget.value)}
                    placeholder="Continue on Empyralis"
                  />
                </FormField>
                <FormField label="Limit help URL" hint="Where limited users go when they need help or more messages.">
                  <FormInput
                    value={wizardState.upgradeCtaUrl}
                    onChange={(event) => setWizardField('upgradeCtaUrl', event.currentTarget.value)}
                    placeholder="https://app.empyralis.com/help"
                  />
                </FormField>
              </FormGrid>
            </div>
          ) : null}
          </ModalSection>
        )}
      </div>
    </CommandSheet>
  );
}
