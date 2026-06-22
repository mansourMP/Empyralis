'use client';

import { useState } from 'react';

import { FormInput, FormSelect } from '@/lib/ui/form-controls';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import type {
  DetailConfigDraft,
  ProviderCatalogSnapshot,
  WizardState,
} from './types';
import {
  STUDIO_AI_SOURCE_OPTIONS,
} from './constants';
import {
  pickStudioModelForTier,
  providerReadyForStudio,
} from './utils';

const PLATFORM_MODEL_LABELS: Record<WizardState['aiTier'], string> = {
  light: 'Light',
  pro: 'Pro',
  max: 'Max',
};

const REASONING_OPTIONS: ReadonlyArray<{
  value: WizardState['aiTier'];
  label: string;
  hint: string;
}> = [
  { value: 'light', label: 'Medium', hint: 'Lower cost for routine replies.' },
  { value: 'pro', label: 'Balanced', hint: 'Best default for customer support.' },
  { value: 'max', label: 'High', hint: 'More reasoning when accuracy matters.' },
];

const AI_ROUTE_DETAILS: Record<WizardState['aiSource'], {
  audience: string;
  status: string;
  description: string;
}> = {
  empyralis_credits: {
    audience: 'Recommended',
    status: 'Workspace default',
    description: 'Best for normal Studio users. The agent inherits the workspace AI route managed in Connections.',
  },
  workspace_api_key: {
    audience: 'Bring your own key',
    status: 'Workspace credential',
    description: 'Best when the business already has an API provider account and wants usage billed there.',
  },
  local_model: {
    audience: 'Developer/local',
    status: 'Agent Computer route',
    description: 'Best for technical workspaces that operate a local or self-hosted model runtime.',
  },
  subscription_passthrough: {
    audience: 'Approved route',
    status: 'Workspace gateway',
    description: 'Best only when this workspace has an approved AI gateway route.',
  },
};

export function AgentAiSettingsSections({
  value,
  providerCatalog,
  isLoadingProviderCatalog,
  onSelectTier,
  onSelectAiSource,
  onSelectProvider,
  onSelectModel,
  onRefreshProviderModels,
  onSaveProviderCredential,
  isSavingProviderCredential = false,
}: {
  value: DetailConfigDraft;
  providerCatalog: ProviderCatalogSnapshot[];
  isLoadingProviderCatalog: boolean;
  onSelectTier: (nextTier: WizardState['aiTier']) => void;
  onSelectAiSource: (nextSource: WizardState['aiSource']) => void;
  onSelectProvider: (nextProviderId: string) => void;
  onSelectModel: (nextModelId: string) => void;
  onRefreshProviderModels?: (providerId: string) => void;
  onSaveProviderCredential?: (providerId: string, apiKey: string) => Promise<void>;
  isSavingProviderCredential?: boolean;
}) {
  const [providerCredentialDraft, setProviderCredentialDraft] = useState('');
  const [providerCredentialError, setProviderCredentialError] = useState<string | null>(null);
  const usesEmpyralisCredits = value.aiSource === 'empyralis_credits';
  const usesWorkspaceApiKey = value.aiSource === 'workspace_api_key';
  const usesLocalModel = value.aiSource === 'local_model';
  const usesSubscriptionPassthrough = value.aiSource === 'subscription_passthrough';
  const selectedProvider = providerCatalog.find((provider) => provider.id === value.providerId) ?? null;
  const selectedModel = selectedProvider?.models.find((model) => model.id === value.modelId) ?? null;
  const providerReady = usesEmpyralisCredits || usesSubscriptionPassthrough || providerReadyForStudio(selectedProvider);
  const readyProviders = providerCatalog.filter((provider) => providerReadyForStudio(provider));
  const localProviders = providerCatalog.filter((provider) =>
    provider.localSelfHostedCompatible || ['codex', 'codex_cli', 'local', 'local_ollama', 'ollama'].includes(provider.id),
  );
  const localProvider = localProviders[0] ?? null;
  const firstReadyProvider = readyProviders[0] ?? providerCatalog[0] ?? null;
  const selectableProviders = usesLocalModel ? localProviders : providerCatalog;
  const selectedReasoning = REASONING_OPTIONS.find((option) => option.value === value.aiTier) ?? REASONING_OPTIONS[1];

  function selectProviderModel(providerId: string, modelId: string) {
    onSelectProvider(providerId);
    onSelectModel(modelId);
  }

  function selectAiSource(nextSource: WizardState['aiSource']) {
    onSelectAiSource(nextSource);
    if (nextSource === 'empyralis_credits') {
      selectProviderModel('', '');
      return;
    }
    if (nextSource === 'workspace_api_key' && firstReadyProvider) {
      selectProviderModel(firstReadyProvider.id, firstReadyProvider.defaultModel || firstReadyProvider.models[0]?.id || '');
      return;
    }
    if (nextSource === 'local_model' && localProvider) {
      selectProviderModel(localProvider.id, localProvider.defaultModel || localProvider.models[0]?.id || '');
      return;
    }
    selectProviderModel('', '');
  }

  function handleAiSourceChange(nextSource: WizardState['aiSource']) {
    if (nextSource === 'workspace_api_key' && !firstReadyProvider) {
      onSelectAiSource(nextSource);
      return;
    }
    selectAiSource(nextSource);
  }

  async function handleProviderCredentialSave() {
    const providerId = selectedProvider?.id ?? value.providerId;
    const apiKey = providerCredentialDraft.trim();
    if (!providerId || !apiKey || !onSaveProviderCredential) {
      return;
    }
    setProviderCredentialError(null);
    try {
      await onSaveProviderCredential(providerId, apiKey);
      setProviderCredentialDraft('');
    } catch (error) {
      setProviderCredentialError(error instanceof Error ? error.message : 'API key could not be saved.');
    }
  }

  const selectedSourceOption = STUDIO_AI_SOURCE_OPTIONS.find((option) => option.value === value.aiSource)
    ?? STUDIO_AI_SOURCE_OPTIONS[0];
  const selectedRouteDetail = AI_ROUTE_DETAILS[selectedSourceOption.value];
  const providerLabel = usesEmpyralisCredits
    ? 'Workspace default'
    : usesSubscriptionPassthrough
      ? selectedProvider?.label ?? 'Workspace gateway'
      : selectedProvider?.label ?? (usesLocalModel ? 'Connect Agent Computer' : 'Choose provider');
  const providerHint = usesEmpyralisCredits
    ? 'Uses the workspace AI route from Connections. Most agents should inherit this.'
    : usesSubscriptionPassthrough
      ? 'Uses an approved workspace AI gateway when available.'
      : usesLocalModel
        ? 'Uses a local or self-hosted model provider from an Agent Computer.'
        : 'Uses a workspace credential shared across this workspace (not an agent-private secret).';
  const modelLabel = usesEmpyralisCredits
    ? `${PLATFORM_MODEL_LABELS[value.aiTier]} model`
    : selectedModel?.label ?? (usesSubscriptionPassthrough ? 'Workspace gateway default' : 'Choose model');
  const modelHint = usesEmpyralisCredits
    ? 'The exact provider route stays managed by Empyralis.'
    : selectedProvider
      ? `${selectedProvider.label} model used for this Business Agent.`
      : 'Choose a provider before selecting a model.';

  return (
    <div
      className={joinClassNames('studio-ai-settings', isLoadingProviderCatalog && 'studio-ai-settings--loading')}
      aria-busy={isLoadingProviderCatalog}
    >
      <section className="studio-ai-settings__route-overview" aria-label="AI route options">
        <section className="studio-ai-settings__inheritance">
          <span>Default behavior</span>
          <h3>Use workspace default</h3>
          <p>Provider setup belongs in Connections → AI & Runtime. Studio agents only override the route when there is a specific business reason.</p>
        </section>
        <section className="studio-ai-settings__selected-card">
          <div className="studio-ai-settings__selected-copy">
            <span>Selected route</span>
            <h3>{selectedSourceOption.title}</h3>
            <p>{selectedSourceOption.hint}</p>
          </div>
          <div className="studio-ai-settings__selected-copy">
            <span>{selectedRouteDetail.audience}</span>
            <strong>{modelLabel}</strong>
            <p>{providerLabel} · {selectedRouteDetail.status}</p>
          </div>
          <div className="studio-ai-settings__selected-actions">
            <AppButton type="button" tone="secondary" disabled>
              Active
            </AppButton>
          </div>
        </section>

        <div className="studio-ai-settings__route-grid">
          {STUDIO_AI_SOURCE_OPTIONS.map((option) => {
            const routeDetail = AI_ROUTE_DETAILS[option.value];
            const selected = value.aiSource === option.value;
            return (
              <button
                key={option.value}
                type="button"
                className={joinClassNames(
                  'studio-ai-settings__route-option',
                  selected && 'studio-ai-settings__route-option--selected',
                )}
                aria-pressed={selected}
                onClick={() => handleAiSourceChange(option.value)}
              >
                <span className="studio-ai-settings__route-option-head">
                  <span>{routeDetail.audience}</span>
                  <span>{selected ? 'Selected' : routeDetail.status}</span>
                </span>
                <strong>{option.title}</strong>
                <p>{routeDetail.description}</p>
              </button>
            );
          })}
        </div>

        <section className="studio-ai-settings__settings-card">
          <div className="studio-ai-settings__setting-row">
            <div className="studio-ai-settings__setting-copy">
              <span>Provider</span>
              <strong>{providerLabel}</strong>
              <p>{providerHint}</p>
            </div>
            <div className="studio-ai-settings__setting-control">
              {usesEmpyralisCredits || usesSubscriptionPassthrough ? (
                <div className="studio-ai-settings__static-value">{providerLabel}</div>
              ) : selectableProviders.length > 0 ? (
                <>
                  <FormSelect
                    value={value.providerId}
                    onChange={(event) => {
                      const nextProviderId = event.currentTarget.value;
                      const provider = selectableProviders.find((item) => item.id === nextProviderId);
                      const nextModelId = provider ? pickStudioModelForTier(provider, value.aiTier) : '';
                      selectProviderModel(nextProviderId, nextModelId);
                    }}
                  >
                    <option value="">Select provider</option>
                    {selectableProviders.map((provider) => (
                      <option key={provider.id} value={provider.id}>{provider.label}</option>
                    ))}
                  </FormSelect>
                  {selectedProvider && onRefreshProviderModels ? (
                    <AppButton type="button" tone="secondary" onClick={() => onRefreshProviderModels(selectedProvider.id)}>
                      Refresh
                    </AppButton>
                  ) : null}
                </>
              ) : (
                <div className="studio-ai-settings__static-value">
                  {usesLocalModel ? 'Connect Agent Computer first' : 'Provider catalog unavailable'}
                </div>
              )}
            </div>
          </div>

          {usesWorkspaceApiKey ? (
            <div className="studio-ai-settings__setting-row">
              <div className="studio-ai-settings__setting-copy">
                <span>API key</span>
                <strong>{selectedProvider ? `${selectedProvider.label} key` : 'Choose provider first'}</strong>
                <p>
                  {selectedProvider
                    ? 'Saved in the workspace vault as a workspace-level credential. Replace here to rotate the shared key for this provider.'
                    : 'Select a provider above, then paste the key without leaving Studio.'}
                </p>
                {providerCredentialError ? (
                  <p className="studio-ai-settings__inline-error">{providerCredentialError}</p>
                ) : null}
              </div>
              <div className="studio-ai-settings__setting-control studio-ai-settings__credential-control">
                <FormInput
                  type="password"
                  value={providerCredentialDraft}
                  placeholder={selectedProvider ? `Paste ${selectedProvider.label} API key` : 'Choose provider first'}
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  data-1p-ignore="true"
                  data-lpignore="true"
                  disabled={!selectedProvider || isSavingProviderCredential}
                  onChange={(event) => setProviderCredentialDraft(event.currentTarget.value)}
                />
                <AppButton
                  type="button"
                  tone="secondary"
                  disabled={!selectedProvider || !providerCredentialDraft.trim() || isSavingProviderCredential || !onSaveProviderCredential}
                  onClick={() => {
                    void handleProviderCredentialSave();
                  }}
                >
                  {providerReady ? 'Replace key' : 'Save API key'}
                </AppButton>
              </div>
            </div>
          ) : null}

          <div className="studio-ai-settings__setting-row">
            <div className="studio-ai-settings__setting-copy">
              <span>Model</span>
              <strong>{modelLabel}</strong>
              <p>{modelHint}</p>
            </div>
            <div className="studio-ai-settings__setting-control">
              {usesEmpyralisCredits ? (
                <FormSelect
                  value={value.aiTier}
                  onChange={(event) => onSelectTier(event.currentTarget.value as WizardState['aiTier'])}
                >
                  {Object.entries(PLATFORM_MODEL_LABELS).map(([tier, label]) => (
                    <option key={tier} value={tier}>{label}</option>
                  ))}
                </FormSelect>
              ) : !usesSubscriptionPassthrough && selectedProvider ? (
                <FormSelect
                  value={value.modelId}
                  onChange={(event) => onSelectModel(event.currentTarget.value)}
                >
                  {selectedProvider.models.map((model) => (
                    <option key={model.id} value={model.id}>{model.label}</option>
                  ))}
                </FormSelect>
              ) : (
                <div className="studio-ai-settings__static-value">{providerReady ? modelLabel : 'Setup needed'}</div>
              )}
            </div>
          </div>

          <div className="studio-ai-settings__setting-row">
            <div className="studio-ai-settings__setting-copy">
              <span>Reasoning</span>
              <strong>{selectedReasoning.label}</strong>
              <p>{selectedReasoning.hint}</p>
            </div>
            <div className="studio-ai-settings__setting-control">
              <FormSelect
                value={value.aiTier}
                onChange={(event) => onSelectTier(event.currentTarget.value as WizardState['aiTier'])}
              >
                {REASONING_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </FormSelect>
            </div>
          </div>
        </section>
      </section>
    </div>
  );
}
