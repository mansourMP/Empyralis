'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Check, ExternalLink, X } from 'lucide-react';

import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';

export type VpsProviderId = 'digitalocean' | 'hetzner' | 'vultr';
type VpsStep = 'provider' | 'access' | 'plans' | 'region' | 'progress';
type VpsProgressStage = 'idle' | 'creating' | 'installing' | 'connecting' | 'connected' | 'failed';

export type VpsProviderCard = {
  id: VpsProviderId;
  label: string;
  price: string;
  tagline: string;
  accountMethod: string;
  tokenUrl: string;
  logoSrc: string;
  features: string[];
};

type VpsConnection = {
  provider: VpsProviderId;
  tokenId: string;
  accountLabel: string;
  connectedAt: string;
};

type VpsRegion = {
  id: string;
  label: string;
};

type VpsPlan = {
  id: string;
  slug: string;
  label: string;
  vcpus: number;
  memory_mb: number;
  disk_gb: number;
  price_monthly: number;
  price_label: string;
  recommended?: boolean;
};

type VpsOAuthStartResponse = {
  provider?: string;
  oauth_redirect?: string;
};

type VpsTokenResponse = {
  provider?: string;
  token_id?: string;
};

type VpsProviderRegionsPayload = {
  provider?: string;
  default_region?: string;
  regions?: VpsRegion[];
};

type VpsProviderPlansPayload = {
  provider?: string;
  plans?: VpsPlan[];
};

type VpsProvisionResponse = {
  vps_id?: string;
  provider_resource_id?: string;
};

type VpsProvisionStatusPayload = {
  status?: 'provisioning' | 'registering' | 'connected' | 'failed' | 'deleted' | string;
};

type CloudVpsSetupPanelProps = {
  open: boolean;
  workspaceId: string;
  initialProviderId?: VpsProviderId | null;
  onClose: () => void;
  onConnected: () => Promise<void> | void;
};

const FULL_ACCESS_WARNING_VERSION = '2026-06-06';

export const CLOUD_VPS_PROVIDERS: Record<VpsProviderId, VpsProviderCard> = {
  digitalocean: {
    id: 'digitalocean',
    label: 'DigitalOcean',
    price: '$12/mo',
    tagline: 'Simplest setup',
    accountMethod: 'OAuth login',
    tokenUrl: 'https://cloud.digitalocean.com/account/api/oauth_apps',
    logoSrc: '/brand-assets/infrastructure/digitalocean.svg',
    features: ['OAuth', 'Ubuntu 24.04', 'Global'],
  },
  hetzner: {
    id: 'hetzner',
    label: 'Hetzner',
    price: '€4/mo',
    tagline: 'Best value',
    accountMethod: 'API token',
    tokenUrl: 'https://console.hetzner.cloud/projects',
    logoSrc: '/brand-assets/infrastructure/hetzner.svg',
    features: ['API token', 'Ubuntu 24.04', 'EU'],
  },
  vultr: {
    id: 'vultr',
    label: 'Vultr',
    price: '$12/mo',
    tagline: 'Global regions',
    accountMethod: 'API token',
    tokenUrl: 'https://my.vultr.com/settings/#settingsapi',
    logoSrc: '/brand-assets/infrastructure/vultr.svg',
    features: ['API token', 'Ubuntu 24.04', '25 regions'],
  },
};

export const CLOUD_VPS_PROVIDER_IDS: VpsProviderId[] = ['digitalocean', 'hetzner', 'vultr'];

const PROVIDERS = CLOUD_VPS_PROVIDERS;
const PROVIDER_IDS = CLOUD_VPS_PROVIDER_IDS;

const PROGRESS_STEPS: Array<{ id: VpsProgressStage; label: string }> = [
  { id: 'creating', label: 'Creating server...' },
  { id: 'installing', label: 'Installing Agent Computer...' },
  { id: 'connecting', label: 'Connecting...' },
  { id: 'connected', label: 'Connected ✓' },
];

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function readString(record: Record<string, unknown> | null, ...keys: string[]): string {
  if (!record) {
    return '';
  }
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return '';
}

function normalizeRegions(payload: VpsProviderRegionsPayload | null): VpsRegion[] {
  return Array.isArray(payload?.regions)
    ? payload.regions.filter((region) => Boolean(region?.id && region?.label))
    : [];
}

function normalizePlans(payload: VpsProviderPlansPayload | null): VpsPlan[] {
  return Array.isArray(payload?.plans)
    ? payload.plans.filter((plan) => Boolean(plan?.id && plan?.label && plan?.price_label))
    : [];
}

function connectionStorageKey(workspaceId: string): string {
  return `empyralis:vps-provider-connections:${workspaceId}`;
}

function loadStoredConnections(workspaceId: string): Partial<Record<VpsProviderId, VpsConnection>> {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(connectionStorageKey(workspaceId));
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== 'object') {
      return {};
    }
    const next: Partial<Record<VpsProviderId, VpsConnection>> = {};
    for (const providerId of PROVIDER_IDS) {
      const record = readRecord((parsed as Record<string, unknown>)[providerId]);
      const tokenId = readString(record, 'tokenId', 'token_id');
      if (tokenId) {
        next[providerId] = {
          provider: providerId,
          tokenId,
          accountLabel: readString(record, 'accountLabel', 'account_label') || `${PROVIDERS[providerId].label} account`,
          connectedAt: readString(record, 'connectedAt', 'connected_at') || new Date().toISOString(),
        };
      }
    }
    return next;
  } catch {
    return {};
  }
}

function saveStoredConnections(workspaceId: string, connections: Partial<Record<VpsProviderId, VpsConnection>>) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(connectionStorageKey(workspaceId), JSON.stringify(connections));
}

function tokenPayload(providerId: VpsProviderId, token: string): Record<string, string> {
  if (providerId === 'vultr') {
    return { api_key: token };
  }
  return { api_token: token };
}

function progressRank(stage: VpsProgressStage): number {
  return PROGRESS_STEPS.findIndex((step) => step.id === stage);
}

function progressStepDone(step: VpsProgressStage, current: VpsProgressStage): boolean {
  return progressRank(current) > progressRank(step);
}

function progressStepActive(step: VpsProgressStage, current: VpsProgressStage): boolean {
  return step === current;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function CloudVpsSetupPanel({ open, workspaceId, initialProviderId = null, onClose, onConnected }: CloudVpsSetupPanelProps) {
  const services = useWorkspaceServices();
  const [step, setStep] = useState<VpsStep>('provider');
  const [connections, setConnections] = useState<Partial<Record<VpsProviderId, VpsConnection>>>({});
  const [selectedProvider, setSelectedProvider] = useState<VpsProviderId | null>(null);
  const [apiToken, setApiToken] = useState('');
  const [tokenId, setTokenId] = useState('');
  const [plans, setPlans] = useState<VpsPlan[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState('');
  const [regions, setRegions] = useState<VpsRegion[]>([]);
  const [selectedRegionId, setSelectedRegionId] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingPlans, setLoadingPlans] = useState(false);
  const [loadingRegions, setLoadingRegions] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progressStage, setProgressStage] = useState<VpsProgressStage>('idle');
  const [vpsId, setVpsId] = useState<string | null>(null);
  const [providerResourceId, setProviderResourceId] = useState<string | null>(null);
  const [cleanupBusy, setCleanupBusy] = useState(false);

  const provider = selectedProvider ? PROVIDERS[selectedProvider] : null;
  const selectedPlan = useMemo(
    () => plans.find((plan) => plan.id === selectedPlanId) ?? null,
    [plans, selectedPlanId],
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    const storedConnections = loadStoredConnections(workspaceId);
    const requestedProvider = initialProviderId && PROVIDERS[initialProviderId] ? initialProviderId : null;
    setConnections(storedConnections);
    setStep('provider');
    setSelectedProvider(null);
    setApiToken('');
    setTokenId('');
    setPlans([]);
    setSelectedPlanId('');
    setRegions([]);
    setSelectedRegionId('');
    setBusy(false);
    setError(null);
    setProgressStage('idle');
    setVpsId(null);
    setProviderResourceId(null);
    setCleanupBusy(false);
    if (requestedProvider) {
      setSelectedProvider(requestedProvider);
      const connection = storedConnections[requestedProvider];
      if (connection?.tokenId) {
        void prepareServerChoices(requestedProvider, connection.tokenId);
      } else {
        setStep('access');
      }
    }
  }, [initialProviderId, open, workspaceId]);

  useEffect(() => {
    function handleVpsOAuthMessage(event: MessageEvent) {
      const payload = readRecord(event.data);
      if (readString(payload, 'type') !== 'empyralis:vps-oauth') {
        return;
      }
      if (readString(payload, 'provider') !== 'digitalocean') {
        return;
      }
      const callbackError = readString(payload, 'error');
      if (callbackError) {
        setError(callbackError);
        return;
      }
      const nextTokenId = readString(payload, 'token_id');
      if (!nextTokenId) {
        setError('DigitalOcean did not return a stored credential.');
        return;
      }
      const accountLabel = readString(payload, 'account_email', 'email', 'account_name') || 'DigitalOcean account';
      saveConnection('digitalocean', nextTokenId, accountLabel);
      void prepareServerChoices('digitalocean', nextTokenId);
    }
    window.addEventListener('message', handleVpsOAuthMessage);
    return () => window.removeEventListener('message', handleVpsOAuthMessage);
  }, [workspaceId]);

  function saveConnection(providerId: VpsProviderId, nextTokenId: string, accountLabel: string) {
    const nextConnection: VpsConnection = {
      provider: providerId,
      tokenId: nextTokenId,
      accountLabel,
      connectedAt: new Date().toISOString(),
    };
    setConnections((current) => {
      const next = { ...current, [providerId]: nextConnection };
      saveStoredConnections(workspaceId, next);
      return next;
    });
    setTokenId(nextTokenId);
  }

  function disconnectProvider(providerId: VpsProviderId) {
    setConnections((current) => {
      const next = { ...current };
      delete next[providerId];
      saveStoredConnections(workspaceId, next);
      return next;
    });
    if (selectedProvider === providerId) {
      setTokenId('');
      setPlans([]);
      setSelectedPlanId('');
      setStep('access');
    }
  }

  async function loadRegions(providerId: VpsProviderId): Promise<VpsRegion[]> {
    setLoadingRegions(true);
    setError(null);
    try {
      const payload = await services.client.requestJson<VpsProviderRegionsPayload>({
        path: `/api/hardware/vps/regions?provider=${encodeURIComponent(providerId)}`,
      });
      const nextRegions = normalizeRegions(payload);
      if (!nextRegions.length) {
        throw new Error('Regions are unavailable for this provider.');
      }
      setRegions(nextRegions);
      setSelectedRegionId(String(payload?.default_region || nextRegions[0]?.id || ''));
      return nextRegions;
    } catch (regionError) {
      setRegions([]);
      setSelectedRegionId('');
      setError(regionError instanceof Error ? regionError.message : 'Could not load VPS regions.');
      return [];
    } finally {
      setLoadingRegions(false);
    }
  }

  async function loadPlans(providerId: VpsProviderId, nextTokenId: string): Promise<VpsPlan[]> {
    setLoadingPlans(true);
    setError(null);
    try {
      const payload = await services.client.requestJson<VpsProviderPlansPayload>({
        path: `/api/hardware/vps/plans?provider=${encodeURIComponent(providerId)}&token_id=${encodeURIComponent(nextTokenId)}&workspace_id=${encodeURIComponent(workspaceId)}`,
      });
      const nextPlans = normalizePlans(payload);
      if (!nextPlans.length) {
        throw new Error('Plans are unavailable for this provider.');
      }
      const recommended = nextPlans.find((plan) => plan.recommended) ?? nextPlans[0];
      setPlans(nextPlans);
      setSelectedPlanId(recommended?.id ?? nextPlans[0]?.id ?? '');
      return nextPlans;
    } catch (plansError) {
      setPlans([]);
      setSelectedPlanId('');
      setError(plansError instanceof Error ? plansError.message : 'Could not load server plans.');
      return [];
    } finally {
      setLoadingPlans(false);
    }
  }

  async function prepareServerChoices(providerId: VpsProviderId, nextTokenId: string) {
    setSelectedProvider(providerId);
    setTokenId(nextTokenId);
    setStep('plans');
    const [nextPlans] = await Promise.all([
      loadPlans(providerId, nextTokenId),
      loadRegions(providerId),
    ]);
    if (!nextPlans.length) {
      disconnectProvider(providerId);
      setStep('access');
    }
  }

  async function selectProvider(providerId: VpsProviderId) {
    setSelectedProvider(providerId);
    setApiToken('');
    setError(null);
    setPlans([]);
    setSelectedPlanId('');
    setRegions([]);
    setSelectedRegionId('');
    setProgressStage('idle');
    const connection = connections[providerId];
    if (connection?.tokenId) {
      await prepareServerChoices(providerId, connection.tokenId);
      return;
    }
    setTokenId('');
    setStep('access');
  }

  async function startDigitalOceanOAuth() {
    setError(null);
    setBusy(true);
    try {
      const payload = await services.client.requestJson<VpsOAuthStartResponse>({
        path: `/api/hardware/vps/oauth/digitalocean/start?workspace_id=${encodeURIComponent(workspaceId)}`,
      });
      const redirect = String(payload?.oauth_redirect || '').trim();
      if (!redirect) {
        throw new Error('DigitalOcean OAuth URL was not returned.');
      }
      const popup = window.open(redirect, 'empyralis-digitalocean-oauth', 'width=720,height=780');
      if (!popup) {
        window.location.assign(redirect);
      } else {
        popup.focus();
      }
    } catch (oauthError) {
      setError(oauthError instanceof Error ? oauthError.message : 'Could not start DigitalOcean login.');
    } finally {
      setBusy(false);
    }
  }

  async function verifyApiToken() {
    if (!selectedProvider) {
      setError('Choose a provider first.');
      return;
    }
    if (!apiToken.trim()) {
      setError('Enter an API token to continue.');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const payload = await services.client.requestJson<VpsTokenResponse>({
        path: '/api/hardware/vps/tokens',
        init: {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            provider: selectedProvider,
            credentials: tokenPayload(selectedProvider, apiToken),
          }),
        },
      });
      const nextTokenId = String(payload?.token_id || '').trim();
      if (!nextTokenId) {
        throw new Error('Stored provider credential id was not returned.');
      }
      saveConnection(selectedProvider, nextTokenId, `${PROVIDERS[selectedProvider].label} account`);
      await prepareServerChoices(selectedProvider, nextTokenId);
    } catch (tokenError) {
      setError(tokenError instanceof Error ? tokenError.message : 'Could not verify provider account.');
    } finally {
      setBusy(false);
    }
  }

  function goBack() {
    if (step === 'access') {
      setStep('provider');
      return;
    }
    if (step === 'plans') {
      setStep('provider');
      return;
    }
    if (step === 'region') {
      setStep('plans');
    }
  }

  async function createServer() {
    if (!selectedProvider) {
      setError('Choose a provider first.');
      setStep('provider');
      return;
    }
    if (!tokenId) {
      setError('Connect the provider account first.');
      setStep('access');
      return;
    }
    if (!selectedPlanId) {
      setError('Choose a plan first.');
      setStep('plans');
      return;
    }
    if (!selectedRegionId) {
      setError('Choose a region first.');
      setStep('region');
      return;
    }
    setBusy(true);
    setError(null);
    setProgressStage('creating');
    setStep('progress');
    try {
      const payload = await services.client.requestJson<VpsProvisionResponse>({
        path: '/api/hardware/vps/provision',
        init: {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            provider: selectedProvider,
            token_id: tokenId,
            region: selectedRegionId,
            size: selectedPlanId,
            runtime_access_mode: 'full_access',
            autonomous_agent_setup_warning_acknowledged: true,
            metadata: {
              autonomous_agent_setup_warning_version: FULL_ACCESS_WARNING_VERSION,
            },
          }),
        },
      });
      const nextVpsId = String(payload?.vps_id || '').trim();
      if (!nextVpsId) {
        throw new Error('VPS provisioning id was not returned.');
      }
      setVpsId(nextVpsId);
      setProviderResourceId(String(payload?.provider_resource_id || '').trim() || null);
      setProgressStage('installing');
      void pollProvisionStatus(nextVpsId);
    } catch (provisionError) {
      setProgressStage('failed');
      setError(provisionError instanceof Error ? provisionError.message : 'Could not create Agent Computer.');
    } finally {
      setBusy(false);
    }
  }

  async function pollProvisionStatus(nextVpsId: string) {
    const deadline = Date.now() + 300_000;
    while (Date.now() < deadline) {
      await wait(5_000);
      try {
        const payload = await services.client.requestJson<VpsProvisionStatusPayload>({
          path: `/api/hardware/vps/${encodeURIComponent(nextVpsId)}/status`,
        });
        const status = String(payload?.status || '').toLowerCase();
        if (status === 'connected') {
          setProgressStage('connected');
          window.setTimeout(() => {
            void onConnected();
          }, 900);
          return;
        }
        if (status === 'failed') {
          setProgressStage('failed');
          setError('Setup failed — server was created but could not connect.');
          return;
        }
        setProgressStage(status === 'registering' ? 'connecting' : 'installing');
      } catch (pollError) {
        setError(pollError instanceof Error ? pollError.message : 'Could not check VPS setup status.');
      }
    }
    setProgressStage('failed');
    setError('Setup failed — server was created but could not connect.');
  }

  async function deleteFailedServer() {
    if (!vpsId) {
      return;
    }
    setCleanupBusy(true);
    setError(null);
    try {
      await services.client.requestJson<Record<string, unknown>>({
        path: `/api/hardware/vps/${encodeURIComponent(vpsId)}`,
        init: {
          method: 'DELETE',
          headers: { accept: 'application/json' },
        },
      });
      setError('Server deleted.');
      setProviderResourceId(null);
    } catch (cleanupError) {
      setError(cleanupError instanceof Error ? cleanupError.message : 'Could not delete the server.');
    } finally {
      setCleanupBusy(false);
    }
  }

  if (!open) {
    return null;
  }

  if (step === 'provider') {
    return (
      <div className="cloud-vps-provider-modal" role="dialog" aria-modal="true" aria-label="Choose cloud provider">
        <button className="cloud-vps-provider-modal__scrim" type="button" aria-label="Close cloud provider setup" onClick={onClose} />
        <section className="cloud-vps-provider-modal__panel">
          <header className="cloud-vps-provider-modal__header">
            <h2>Choose your cloud provider</h2>
            <button className="cloud-vps-provider-modal__close" type="button" onClick={onClose} aria-label="Close">
              <X size={18} strokeWidth={2} />
            </button>
          </header>

          <div className="cloud-vps-provider-list">
            {PROVIDER_IDS.map((providerId) => {
              const item = PROVIDERS[providerId];
              const connection = connections[providerId];
              return (
                <article key={providerId} className="cloud-vps-provider-row">
                  <button className="cloud-vps-provider-row__button" type="button" onClick={() => void selectProvider(providerId)}>
                    <span className="cloud-vps-provider-row__logo" aria-hidden="true">
                      <img src={item.logoSrc} alt="" />
                    </span>
                    <span className="cloud-vps-provider-row__copy">
                      <span className="cloud-vps-provider-row__title">
                        <strong>{item.label}</strong>
                        <span>{`· ${item.tagline}`}</span>
                        {connection ? <em>{`connected · ${connection.accountLabel}`}</em> : null}
                      </span>
                      <span className="cloud-vps-provider-row__features">
                        {item.features.map((feature) => (
                          <span key={feature}>
                            <Check size={13} strokeWidth={2} aria-hidden="true" />
                            {feature}
                          </span>
                        ))}
                      </span>
                    </span>
                    <span className="cloud-vps-provider-row__side">
                      <strong>{item.price}</strong>
                      <span>{connection ? 'Add server →' : 'Connect →'}</span>
                    </span>
                  </button>
                  {connection ? (
                    <button
                      className="cloud-vps-provider-row__disconnect"
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        disconnectProvider(providerId);
                      }}
                    >
                      Disconnect
                    </button>
                  ) : null}
                </article>
              );
            })}
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="cloud-vps-flow-modal" role="dialog" aria-modal="true" aria-label="Cloud VPS setup">
      <button
        className="cloud-vps-flow-modal__scrim"
        type="button"
        aria-label="Close Cloud VPS setup"
        onClick={onClose}
        disabled={step === 'progress'}
      />
      <section className="cloud-vps-flow-modal__panel">
        <header className="cloud-vps-flow-modal__header">
          {step !== 'progress' ? (
            <button className="cloud-vps-flow-modal__icon-button" type="button" onClick={goBack} aria-label="Back">
              <ArrowLeft size={18} strokeWidth={2} />
            </button>
          ) : <span className="cloud-vps-flow-modal__icon-spacer" aria-hidden="true" />}
          {step !== 'progress' ? (
            <button className="cloud-vps-flow-modal__icon-button" type="button" onClick={onClose} aria-label="Close">
              <X size={18} strokeWidth={2} />
            </button>
          ) : <span className="cloud-vps-flow-modal__icon-spacer" aria-hidden="true" />}
        </header>

        {step === 'access' && provider ? (
          <section className="cloud-vps-flow-modal__content">
            <div className="cloud-vps-flow-modal__title">
              <span className="cloud-vps-flow-modal__logo" aria-hidden="true">
                <img src={provider.logoSrc} alt="" />
              </span>
              <h2>{`Connect your ${provider.label} account`}</h2>
            </div>
            {provider.id === 'digitalocean' ? (
              <AppButton tone="primary" type="button" onClick={() => void startDigitalOceanOAuth()} disabled={busy}>
                {busy ? 'Opening DigitalOcean' : 'Log in with DigitalOcean'}
              </AppButton>
            ) : (
              <div className="cloud-vps-access-form">
                <label className="app-form-field">
                  <span className="app-form-field__label">API Token</span>
                  <input
                    className="app-field"
                    type="password"
                    value={apiToken}
                    onChange={(event) => setApiToken(event.target.value)}
                    placeholder="Paste API token"
                  />
                </label>
                <a className="cloud-vps-token-link" href={provider.tokenUrl} target="_blank" rel="noreferrer">
                  <span>{`Create token at ${provider.label}`}</span>
                  <ExternalLink size={13} strokeWidth={2} aria-hidden="true" />
                </a>
                <AppButton tone="primary" type="button" onClick={() => void verifyApiToken()} disabled={busy || loadingPlans}>
                  {busy || loadingPlans ? 'Verifying token' : 'Verify token →'}
                </AppButton>
              </div>
            )}
            {error ? <p className="cloud-vps-panel__error">{error}</p> : null}
          </section>
        ) : null}

        {step === 'plans' && provider ? (
          <section className="cloud-vps-flow-modal__content">
            <div className="cloud-vps-panel__heading">
              <h2>Choose your server plan</h2>
            </div>
            {loadingPlans ? <p className="cloud-vps-panel__muted">Loading plans...</p> : null}
            <div className="cloud-vps-plan-list">
              {plans.map((plan) => (
                <button
                  key={plan.id}
                  type="button"
                  className={joinClassNames('cloud-vps-plan-row', selectedPlanId === plan.id && 'is-selected')}
                  onClick={() => setSelectedPlanId(plan.id)}
                >
                  <span className="cloud-vps-plan-row__radio" aria-hidden="true" />
                  <strong>{plan.label}</strong>
                  <span>{plan.price_label}</span>
                  {plan.recommended ? <em>Recommended</em> : null}
                </button>
              ))}
            </div>
            <p className="cloud-vps-panel__note">2GB handles most tasks. Choose more for heavy code or models.</p>
            <div className="cloud-vps-panel__footer">
              <AppButton tone="primary" type="button" onClick={() => setStep('region')} disabled={!selectedPlanId || loadingPlans}>
                Continue →
              </AppButton>
            </div>
            {error ? <p className="cloud-vps-panel__error">{error}</p> : null}
          </section>
        ) : null}

        {step === 'region' && provider ? (
          <section className="cloud-vps-flow-modal__content">
            <div className="cloud-vps-panel__heading">
              <h2>Choose region</h2>
            </div>
            <label className="app-form-field">
              <span className="app-form-field__label">Region</span>
              <select
                className="app-field"
                value={selectedRegionId}
                onChange={(event) => setSelectedRegionId(event.target.value)}
                disabled={loadingRegions}
              >
                {regions.map((region) => (
                  <option key={region.id} value={region.id}>
                    {`${region.label} · ${region.id}`}
                  </option>
                ))}
              </select>
            </label>
            {selectedPlan ? (
              <p className="cloud-vps-panel__note">{`${selectedPlan.label} · ${selectedPlan.price_label}`}</p>
            ) : null}
            <div className="cloud-vps-panel__footer">
              <AppButton tone="primary" type="button" onClick={() => void createServer()} disabled={busy || loadingRegions || !selectedRegionId}>
                {busy ? 'Creating server' : 'Create server →'}
              </AppButton>
            </div>
            {error ? <p className="cloud-vps-panel__error">{error}</p> : null}
          </section>
        ) : null}

        {step === 'progress' ? (
          <section className="cloud-vps-flow-modal__content">
            <div className="cloud-vps-panel__heading">
              <h2>Creating Agent Computer</h2>
              <p>Leave this open while Empyralis installs and connects the server.</p>
            </div>
            <div className="cloud-vps-progress" aria-live="polite">
              {PROGRESS_STEPS.map((item) => (
                <div
                  key={item.id}
                  className={joinClassNames(
                    'cloud-vps-progress__item',
                    progressStepActive(item.id, progressStage) && 'is-active',
                    progressStepDone(item.id, progressStage) && 'is-done',
                    progressStage === 'failed' && 'is-failed',
                  )}
                >
                  <span className="cloud-vps-progress__dot">
                    {progressStepDone(item.id, progressStage) || item.id === 'connected' && progressStage === 'connected' ? (
                      <Check size={12} strokeWidth={2.4} aria-hidden="true" />
                    ) : null}
                  </span>
                  <span>{item.label}</span>
                </div>
              ))}
            </div>
            {providerResourceId ? <p className="cloud-vps-panel__note">{`Provider server: ${providerResourceId}`}</p> : null}
            {error ? <p className="cloud-vps-panel__error">{error}</p> : null}
            {progressStage === 'failed' ? (
              <div className="cloud-vps-panel__footer">
                <AppButton tone="secondary" type="button" onClick={() => void deleteFailedServer()} disabled={!vpsId || cleanupBusy}>
                  {cleanupBusy ? 'Deleting server' : 'Delete server'}
                </AppButton>
              </div>
            ) : null}
          </section>
        ) : null}
      </section>
    </div>
  );
}
