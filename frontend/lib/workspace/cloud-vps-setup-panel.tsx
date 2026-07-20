'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Check, ExternalLink, X } from 'lucide-react';

import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import { buildCookieAuthHeaders } from '@/lib/auth/csrf';

// Fleet routes never mount WorkstationKernelProvider (the legacy workstation
// shell it depends on is gone), so useWorkspaceServices() throws here. Talk to
// the same /api/hardware/vps/* endpoints directly instead — same contract,
// same CSRF handling, no dependency on a context fleet doesn't provide. See
// FleetAgentDetail.tsx's LegacyMemoryTab comment for the same trade-off made
// elsewhere in the fleet rewrite.
async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = String(init.method || 'GET');
  const headers = buildCookieAuthHeaders(method, { accept: 'application/json', ...(init.headers as Record<string, string> | undefined) });
  const response = await fetch(path, { ...init, headers, credentials: 'include' });
  if (!response.ok) {
    // Surface the backend's own detail message when there is one (e.g. "This
    // AWS connection request expired.") instead of just the status code —
    // the AWS connect/confirm steps in particular rely on this to explain
    // *why* a retryable failure happened. Falls back to the old generic
    // message for a non-JSON or detail-less error body.
    let detail = '';
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = typeof body?.detail === 'string' ? body.detail : '';
    } catch {
      // Not JSON (or empty) — fall through to the generic message below.
    }
    throw new Error(detail || `Request failed with status ${response.status}.`);
  }
  return (await response.json()) as T;
}

export type VpsProviderId = 'digitalocean' | 'hetzner' | 'vultr' | 'google' | 'aws';
// google-project / google-billing are Google-only steps between 'access'
// (Sign in with Google) and 'plans' — Google needs a project chosen and its
// billing verified before anything is provisionable, neither of which any
// other provider here requires (see finishGoogleBootstrap).
type VpsStep = 'provider' | 'access' | 'plans' | 'region' | 'progress' | 'google-project' | 'google-billing';
type VpsProgressStage = 'idle' | 'creating' | 'installing' | 'connecting' | 'connected' | 'failed';

export type VpsProviderCard = {
  id: VpsProviderId;
  label: string;
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
  // Region slugs this plan is actually available in (DigitalOcean today).
  // Empty/absent means "not threaded through for this provider" — treated
  // as no restriction, not "available nowhere".
  regions?: string[];
};

type VpsOAuthStartResponse = {
  provider?: string;
  oauth_redirect?: string;
};

type VpsTokenResponse = {
  provider?: string;
  token_id?: string;
};

type GoogleProject = {
  project_id: string;
  name: string;
};

type GoogleProjectsPayload = {
  projects?: GoogleProject[];
};

type GoogleBillingPayload = {
  project_id?: string;
  billing_enabled?: boolean;
  console_url?: string;
};

type GoogleBootstrapPayload = {
  provider?: string;
  token_id?: string;
  project_id?: string;
  service_account_email?: string;
  workspace_id?: string;
};

type VpsAwsConnectResponse = {
  provider?: string;
  connection_id?: string;
  account_id?: string;
  external_id?: string;
  role_arn?: string;
  role_name?: string;
  empyralis_account_id?: string;
  quick_create_url?: string;
};

type VpsAwsConfirmResponse = {
  provider?: string;
  token_id?: string;
  account_id?: string;
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
  // Both pass through from GET /hardware/vps/{vps_id}/status (see
  // get_hardware_vps_status / _public_record in vps_provisioning_service.py).
  // provider_resource_id is empty until the provider actually creates the
  // resource — with provisioning running as a background task (see
  // run_vps_provisioning_lifecycle), the initial POST response NEVER carries
  // a real one (it's a placeholder record), so this field must be re-read
  // from every status poll, not just the create response. error carries the
  // real failure reason recorded by mark_vps_provision_failed.
  provider_resource_id?: string;
  error?: string;
};

// Builds the message shown when a background provision lands in status
// 'failed'. Two things the old hardcoded string got wrong: (1) it always
// claimed "the server was created" even when provider_resource_id was empty
// — i.e. the provider create call itself failed (e.g. DigitalOcean's
// "missing the required permission tag:create") and nothing was ever created
// or billed; (2) it never showed the actual backend error, just a generic
// "could not connect". hasProviderResource comes from the SAME status poll
// that reported 'failed', not stale state from the initial create response.
function friendlyProvisionFailureMessage(rawError: string, hasProviderResource: boolean): string {
  const detail = rawError.trim();
  if (/tag:create|tag:read|tag:delete/i.test(detail)) {
    return 'Setup failed — the connected DigitalOcean account is missing a permission (tagging). Disconnect and reconnect DigitalOcean, then try again.';
  }
  if (!detail) {
    return hasProviderResource
      ? 'Setup failed — the server was created but could not connect.'
      : 'Setup failed before the server could be created.';
  }
  return hasProviderResource
    ? `Setup failed — the server was created but could not connect: ${detail}`
    : `Setup failed before the server could be created: ${detail}`;
}

// Resumes the wizard after the DigitalOcean/Google OAuth round-trip: both
// providers now navigate this same tab straight to the provider's authorize
// page (no popup — see startDigitalOceanOAuth / startGoogleOAuth below), and
// the backend's callback sends the browser right back here with the result
// in the query string (see _vps_oauth_hardware_redirect_url in
// routes_gateway.py). The Hardware page parses that query string into this
// shape and hands it down as initialOAuthResult.
export type VpsOAuthResumePayload = {
  provider: VpsProviderId;
  tokenId?: string;
  setupId?: string;
  error?: string;
};

type CloudVpsSetupPanelProps = {
  open: boolean;
  workspaceId: string;
  initialProviderId?: VpsProviderId | null;
  initialOAuthResult?: VpsOAuthResumePayload | null;
  onOAuthResultConsumed?: () => void;
  onClose: () => void;
  onConnected: () => Promise<void> | void;
};

const FULL_ACCESS_WARNING_VERSION = '2026-06-06';

export const CLOUD_VPS_PROVIDERS: Record<VpsProviderId, VpsProviderCard> = {
  digitalocean: {
    id: 'digitalocean',
    label: 'DigitalOcean',
    tagline: 'Simplest setup',
    accountMethod: 'OAuth or API token',
    tokenUrl: 'https://cloud.digitalocean.com/account/api/tokens',
    logoSrc: '/brand-assets/infrastructure/digitalocean.svg',
    features: ['OAuth', 'Ubuntu 24.04', 'Global'],
  },
  hetzner: {
    id: 'hetzner',
    label: 'Hetzner',
    tagline: 'Best value',
    accountMethod: 'API token',
    tokenUrl: 'https://console.hetzner.cloud/projects',
    logoSrc: '/brand-assets/infrastructure/hetzner.svg',
    features: ['API token', 'Ubuntu 24.04', 'EU'],
  },
  vultr: {
    id: 'vultr',
    label: 'Vultr',
    tagline: 'Global regions',
    accountMethod: 'API token',
    tokenUrl: 'https://my.vultr.com/settings/#settingsapi',
    logoSrc: '/brand-assets/infrastructure/vultr.svg',
    features: ['API token', 'Ubuntu 24.04', '25 regions'],
  },
  google: {
    id: 'google',
    label: 'Google Cloud',
    tagline: 'Your own GCP project',
    accountMethod: 'Google sign-in',
    // No token-paste fallback exists for Google (see the 'access' step
    // below) — nothing to link a "create a token" affordance to.
    tokenUrl: '',
    logoSrc: '/brand-assets/infrastructure/google-cloud.svg',
    features: ['OAuth only', 'Ubuntu 24.04', 'Your billing'],
  },
  aws: {
    id: 'aws',
    label: 'AWS',
    tagline: 'No API keys',
    accountMethod: 'CloudFormation role',
    // Not used for aws's own connect step (which never renders the generic
    // token-paste form — see the 'access' step below) — kept for type/
    // structural parity with the other three providers.
    tokenUrl: 'https://console.aws.amazon.com/cloudformation/',
    logoSrc: '/brand-assets/infrastructure/aws.svg',
    features: ['No API keys', 'CloudFormation', 'IAM role'],
  },
};

export const CLOUD_VPS_PROVIDER_IDS: VpsProviderId[] = ['digitalocean', 'hetzner', 'vultr', 'google', 'aws'];

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

export function CloudVpsSetupPanel({
  open,
  workspaceId,
  initialProviderId = null,
  initialOAuthResult = null,
  onOAuthResultConsumed,
  onClose,
  onConnected,
}: CloudVpsSetupPanelProps) {
  const [step, setStep] = useState<VpsStep>('provider');
  const [connections, setConnections] = useState<Partial<Record<VpsProviderId, VpsConnection>>>({});
  const [selectedProvider, setSelectedProvider] = useState<VpsProviderId | null>(null);
  const [apiToken, setApiToken] = useState('');
  const [tokenId, setTokenId] = useState('');
  // AWS's "access" step is a 2-stage sub-flow (enter account id -> open
  // CloudFormation & confirm) rather than a single token paste — see the
  // 'access' step JSX and startAwsConnect/confirmAwsConnect below.
  const [awsAccountId, setAwsAccountId] = useState('');
  const [awsSubStep, setAwsSubStep] = useState<'account' | 'stack'>('account');
  const [awsConnectionId, setAwsConnectionId] = useState('');
  const [awsExternalId, setAwsExternalId] = useState('');
  const [awsRoleArn, setAwsRoleArn] = useState('');
  const [awsEmpyralisAccountId, setAwsEmpyralisAccountId] = useState('');
  const [awsQuickCreateUrl, setAwsQuickCreateUrl] = useState('');
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
  // Set when "Create server" is clicked while browsing pre-connect (Vultr) —
  // routes the connect step's success handler straight into creating the
  // server the user already picked, instead of landing back on the plan
  // list. See handleCreateServerClick / finishConnecting.
  const [pendingCreateAfterConnect, setPendingCreateAfterConnect] = useState(false);
  // Google-only bootstrap state (see the module comment on VpsStep) — the
  // 'google-project' / 'google-billing' steps between OAuth and 'plans'.
  // googleSetupId is a short-lived server-side session id (NOT a stored
  // provider connection — see complete_google_oauth_callback on the
  // backend), discarded once finishGoogleBootstrap succeeds.
  const [googleSetupId, setGoogleSetupId] = useState<string | null>(null);
  const [googleProjects, setGoogleProjects] = useState<GoogleProject[]>([]);
  const [loadingGoogleProjects, setLoadingGoogleProjects] = useState(false);
  const [selectedGoogleProjectId, setSelectedGoogleProjectId] = useState('');
  const [googleNewProjectName, setGoogleNewProjectName] = useState('');
  const [creatingGoogleProject, setCreatingGoogleProject] = useState(false);
  const [googleBillingEnabled, setGoogleBillingEnabled] = useState<boolean | null>(null);
  const [googleBillingConsoleUrl, setGoogleBillingConsoleUrl] = useState('');
  const [checkingGoogleBilling, setCheckingGoogleBilling] = useState(false);
  const [bootstrappingGoogle, setBootstrappingGoogle] = useState(false);

  const provider = selectedProvider ? PROVIDERS[selectedProvider] : null;
  const selectedPlan = useMemo(
    () => plans.find((plan) => plan.id === selectedPlanId) ?? null,
    [plans, selectedPlanId],
  );
  // Regions the CHOSEN PLAN actually supports — an empty/absent regions list
  // on the plan means it wasn't threaded through for this provider, treated
  // as no restriction rather than "available nowhere".
  const visibleRegions = useMemo(() => {
    if (!selectedPlan?.regions?.length) return regions;
    const allowed = new Set(selectedPlan.regions);
    return regions.filter((region) => allowed.has(region.id));
  }, [regions, selectedPlan]);

  // If the plan changes underneath the current region choice and that region
  // is no longer valid for it, snap to a region that is — the invalid
  // (plan, region) pair must never be submittable.
  useEffect(() => {
    if (visibleRegions.length === 0) return;
    if (!visibleRegions.some((region) => region.id === selectedRegionId)) {
      setSelectedRegionId(visibleRegions[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleRegions]);

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
    setAwsAccountId('');
    setAwsSubStep('account');
    setAwsConnectionId('');
    setAwsExternalId('');
    setAwsRoleArn('');
    setAwsEmpyralisAccountId('');
    setAwsQuickCreateUrl('');
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
    setPendingCreateAfterConnect(false);
    setGoogleSetupId(null);
    setGoogleProjects([]);
    setSelectedGoogleProjectId('');
    setGoogleNewProjectName('');
    setGoogleBillingEnabled(null);
    setGoogleBillingConsoleUrl('');
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

  // Applies the OAuth result the backend redirected back with — the
  // top-level tab lands back on the Hardware page with the outcome in the
  // query string (initialOAuthResult, consumed by the effect right after
  // this one) — see _vps_oauth_hardware_redirect_url in routes_gateway.py.
  function applyOAuthResult(result: VpsOAuthResumePayload) {
    if (result.provider === 'google') {
      if (result.error) {
        setError(result.error);
        return;
      }
      // Google's callback returns a short-lived setup_id, never a
      // token_id — the account isn't provisionable yet (no project chosen,
      // billing unverified). See loadGoogleProjectsStep / finishGoogleBootstrap.
      if (!result.setupId) {
        setError('Google did not return a sign-in session.');
        return;
      }
      setGoogleSetupId(result.setupId);
      void loadGoogleProjectsStep(result.setupId);
      return;
    }
    if (result.provider !== 'digitalocean') {
      return;
    }
    if (result.error) {
      setError(result.error);
      return;
    }
    if (!result.tokenId) {
      setError('DigitalOcean did not return a stored credential.');
      return;
    }
    saveConnection('digitalocean', result.tokenId, 'DigitalOcean account');
    void finishConnecting('digitalocean', result.tokenId);
  }

  // Resumes the wizard when the browser lands back on this page after the
  // DigitalOcean/Google OAuth round-trip — see applyOAuthResult above. Runs
  // at most once per distinct payload the parent hands down; the parent is
  // expected to clear its own state (and strip the query string) via
  // onOAuthResultConsumed so this doesn't reprocess the same result on every
  // re-render.
  const consumedOAuthResultRef = useRef<VpsOAuthResumePayload | null>(null);
  useEffect(() => {
    if (!open || !initialOAuthResult || consumedOAuthResultRef.current === initialOAuthResult) {
      return;
    }
    consumedOAuthResultRef.current = initialOAuthResult;
    applyOAuthResult(initialOAuthResult);
    onOAuthResultConsumed?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialOAuthResult]);

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
      setPendingCreateAfterConnect(false);
      setStep('access');
    }
  }

  // nextTokenId === '' means "no connected account yet" — only Vultr's
  // plans/regions are reachable without one (verified live: GET
  // https://api.vultr.com/v2/plans and /v2/regions both return 200 with no
  // Authorization header; DigitalOcean's /v2/sizes+/v2/regions and
  // Hetzner's /v1/server_types+/v1/locations all 401 unauthenticated). The
  // backend already knows this (see fetch_public_provider_plans /
  // fetch_public_provider_regions) — omitting token_id/workspace_id here
  // just asks for whatever it can serve without credentials, same as the
  // 'provider' step already implicitly relied on for regions.
  async function loadRegions(providerId: VpsProviderId, nextTokenId: string): Promise<VpsRegion[]> {
    setLoadingRegions(true);
    setError(null);
    try {
      const query = nextTokenId
        ? `provider=${encodeURIComponent(providerId)}&token_id=${encodeURIComponent(nextTokenId)}&workspace_id=${encodeURIComponent(workspaceId)}`
        : `provider=${encodeURIComponent(providerId)}`;
      const payload = await requestJson<VpsProviderRegionsPayload>(`/api/hardware/vps/regions?${query}`);
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
      const query = nextTokenId
        ? `provider=${encodeURIComponent(providerId)}&token_id=${encodeURIComponent(nextTokenId)}&workspace_id=${encodeURIComponent(workspaceId)}`
        : `provider=${encodeURIComponent(providerId)}`;
      const payload = await requestJson<VpsProviderPlansPayload>(`/api/hardware/vps/plans?${query}`);
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
      loadRegions(providerId, nextTokenId),
    ]);
    if (!nextPlans.length) {
      disconnectProvider(providerId);
      setStep('access');
    }
  }

  // Vultr only: browse real plans/regions/prices with no connected account
  // at all — the founder principle is provider -> region -> hardware ->
  // PRICE -> provision without hunting for API keys, and Vultr's public
  // endpoints are what make skipping straight to real prices possible.
  // DigitalOcean/Hetzner have no public price data (see loadPlans/
  // loadRegions above), so they keep the connect-first flow below.
  async function browseProviderPreConnect(providerId: VpsProviderId) {
    setStep('plans');
    const [nextPlans] = await Promise.all([loadPlans(providerId, ''), loadRegions(providerId, '')]);
    if (!nextPlans.length) {
      setStep('access');
    }
  }

  async function selectProvider(providerId: VpsProviderId) {
    setSelectedProvider(providerId);
    setApiToken('');
    setAwsAccountId('');
    setAwsSubStep('account');
    setAwsConnectionId('');
    setAwsExternalId('');
    setAwsRoleArn('');
    setAwsEmpyralisAccountId('');
    setAwsQuickCreateUrl('');
    setError(null);
    setPlans([]);
    setSelectedPlanId('');
    setRegions([]);
    setSelectedRegionId('');
    setProgressStage('idle');
    setPendingCreateAfterConnect(false);
    setGoogleSetupId(null);
    setGoogleProjects([]);
    setSelectedGoogleProjectId('');
    setGoogleNewProjectName('');
    setGoogleBillingEnabled(null);
    setGoogleBillingConsoleUrl('');
    const connection = connections[providerId];
    if (connection?.tokenId) {
      await prepareServerChoices(providerId, connection.tokenId);
      return;
    }
    setTokenId('');
    if (providerId === 'vultr') {
      await browseProviderPreConnect(providerId);
      return;
    }
    setStep('access');
  }

  async function startDigitalOceanOAuth() {
    setError(null);
    setBusy(true);
    // Same-tab navigation, exactly like every other OAuth connector (see
    // routes_connections.py's complete_connection_oauth_callback) — no
    // popup, no window.opener, no postMessage. A popup opened after an
    // await loses the "direct result of a user click" flag and gets
    // silently blocked by Safari (and others), and the earlier popup +
    // same-tab-fallback approach could fire the callback more than once for
    // a single click (the OAuth `code` is single-use, so the duplicate hit
    // 400'd and stranded the user on a blank page). A plain same-tab
    // navigation can't double-fire and can't be popup-blocked.
    try {
      const payload = await requestJson<VpsOAuthStartResponse>(
        `/api/hardware/vps/oauth/digitalocean/start?workspace_id=${encodeURIComponent(workspaceId)}`,
      );
      const redirect = String(payload?.oauth_redirect || '').trim();
      if (!redirect) {
        throw new Error('DigitalOcean OAuth URL was not returned.');
      }
      window.location.assign(redirect);
    } catch (oauthError) {
      setError(oauthError instanceof Error ? oauthError.message : 'Could not start DigitalOcean login.');
      setBusy(false);
    }
    // No `finally { setBusy(false) }` on the success path — the tab is
    // navigating away to the OAuth provider, so there's nothing left here to
    // un-busy; the panel remounts fresh when the browser lands back on the
    // Hardware page after the callback redirect.
  }

  async function startGoogleOAuth() {
    setError(null);
    setBusy(true);
    // Same-tab navigation — see the comment in startDigitalOceanOAuth above.
    try {
      const payload = await requestJson<VpsOAuthStartResponse>(
        `/api/hardware/vps/oauth/google/start?workspace_id=${encodeURIComponent(workspaceId)}`,
      );
      const redirect = String(payload?.oauth_redirect || '').trim();
      if (!redirect) {
        throw new Error('Google sign-in URL was not returned.');
      }
      window.location.assign(redirect);
    } catch (oauthError) {
      setError(oauthError instanceof Error ? oauthError.message : 'Could not start Google sign-in.');
      setBusy(false);
    }
  }

  // Reached right after the DigitalOcean/Google OAuth redirect resumes the
  // wizard with a Google setup_id (see applyOAuthResult above) — lists the
  // Google Cloud projects that account can already see, so the user can pick
  // one (or create a new one below) rather than Empyralis guessing.
  async function loadGoogleProjectsStep(setupId: string) {
    setSelectedProvider('google');
    setStep('google-project');
    setError(null);
    setLoadingGoogleProjects(true);
    try {
      const payload = await requestJson<GoogleProjectsPayload>(
        `/api/hardware/vps/google/projects?setup_id=${encodeURIComponent(setupId)}&workspace_id=${encodeURIComponent(workspaceId)}`,
      );
      const projects = Array.isArray(payload?.projects)
        ? payload.projects.filter((project) => Boolean(project?.project_id))
        : [];
      setGoogleProjects(projects);
      setSelectedGoogleProjectId(projects[0]?.project_id ?? '');
    } catch (projectsError) {
      setError(projectsError instanceof Error ? projectsError.message : 'Could not load your Google Cloud projects.');
    } finally {
      setLoadingGoogleProjects(false);
    }
  }

  async function createNewGoogleProject() {
    if (!googleSetupId) {
      setError('Sign in with Google first.');
      return;
    }
    const projectName = googleNewProjectName.trim();
    if (!projectName) {
      setError('Enter a name for the new project.');
      return;
    }
    setError(null);
    setCreatingGoogleProject(true);
    try {
      const payload = await requestJson<GoogleProject>('/api/hardware/vps/google/projects', {
        method: 'POST',
        headers: { accept: 'application/json', 'content-type': 'application/json' },
        body: JSON.stringify({ workspace_id: workspaceId, setup_id: googleSetupId, project_name: projectName }),
      });
      const projectId = String(payload?.project_id || '').trim();
      if (!projectId) {
        throw new Error('Google did not return a new project id.');
      }
      const nextProject: GoogleProject = { project_id: projectId, name: String(payload?.name || projectName) };
      setGoogleProjects((current) => [...current, nextProject]);
      setSelectedGoogleProjectId(projectId);
      setGoogleNewProjectName('');
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : 'Could not create the Google Cloud project.');
    } finally {
      setCreatingGoogleProject(false);
    }
  }

  async function checkGoogleBilling() {
    if (!googleSetupId || !selectedGoogleProjectId) {
      return;
    }
    setError(null);
    setCheckingGoogleBilling(true);
    try {
      const payload = await requestJson<GoogleBillingPayload>(
        `/api/hardware/vps/google/projects/${encodeURIComponent(selectedGoogleProjectId)}/billing`
          + `?setup_id=${encodeURIComponent(googleSetupId)}&workspace_id=${encodeURIComponent(workspaceId)}`,
      );
      setGoogleBillingEnabled(Boolean(payload?.billing_enabled));
      setGoogleBillingConsoleUrl(String(payload?.console_url || ''));
    } catch (billingError) {
      setGoogleBillingEnabled(null);
      setError(billingError instanceof Error ? billingError.message : 'Could not check the billing status.');
    } finally {
      setCheckingGoogleBilling(false);
    }
  }

  async function continueFromGoogleProjectStep() {
    if (!selectedGoogleProjectId) {
      setError('Choose a Google Cloud project first.');
      return;
    }
    setStep('google-billing');
    setGoogleBillingEnabled(null);
    await checkGoogleBilling();
  }

  // The one-time bootstrap (enable Compute Engine, create a scoped service
  // account, grant Empyralis impersonation rights on it — never a
  // downloaded key) — only reachable once billing_enabled is confirmed true
  // (see the 'google-billing' step below). Its token_id slots into the exact
  // same saveConnection/finishConnecting path DigitalOcean's OAuth and the
  // API-token flows already use, so plans/region/create-server work
  // identically from here on.
  async function finishGoogleBootstrap() {
    if (!googleSetupId || !selectedGoogleProjectId) {
      setError('Choose a Google Cloud project first.');
      return;
    }
    setError(null);
    setBootstrappingGoogle(true);
    try {
      const payload = await requestJson<GoogleBootstrapPayload>('/api/hardware/vps/google/bootstrap', {
        method: 'POST',
        headers: { accept: 'application/json', 'content-type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspaceId,
          setup_id: googleSetupId,
          project_id: selectedGoogleProjectId,
        }),
      });
      const nextTokenId = String(payload?.token_id || '').trim();
      if (!nextTokenId) {
        throw new Error('Google Cloud setup did not return a stored connection.');
      }
      const projectLabel = googleProjects.find((project) => project.project_id === selectedGoogleProjectId)?.name
        || selectedGoogleProjectId;
      saveConnection('google', nextTokenId, `${projectLabel} (${selectedGoogleProjectId})`);
      setGoogleSetupId(null);
      await finishConnecting('google', nextTokenId);
    } catch (bootstrapError) {
      setError(bootstrapError instanceof Error ? bootstrapError.message : 'Could not finish Google Cloud setup.');
    } finally {
      setBootstrappingGoogle(false);
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
      const payload = await requestJson<VpsTokenResponse>('/api/hardware/vps/tokens', {
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
      });
      const nextTokenId = String(payload?.token_id || '').trim();
      if (!nextTokenId) {
        throw new Error('Stored provider credential id was not returned.');
      }
      saveConnection(selectedProvider, nextTokenId, `${PROVIDERS[selectedProvider].label} account`);
      await finishConnecting(selectedProvider, nextTokenId);
    } catch (tokenError) {
      setError(tokenError instanceof Error ? tokenError.message : 'Could not verify provider account.');
    } finally {
      setBusy(false);
    }
  }

  // AWS step 1: the customer's 12-digit account id is enough to derive the
  // expected role ARN (fixed-role-name convention) and get back a pre-filled
  // CloudFormation Quick-Create-Stack URL — nothing is trusted/connected yet.
  async function startAwsConnect() {
    const cleanAccountId = awsAccountId.trim();
    if (!/^\d{12}$/.test(cleanAccountId)) {
      setError('Enter your 12-digit AWS account id.');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const payload = await requestJson<VpsAwsConnectResponse>('/api/hardware/vps/aws/connect', {
        method: 'POST',
        headers: {
          accept: 'application/json',
          'content-type': 'application/json',
        },
        body: JSON.stringify({ workspace_id: workspaceId, account_id: cleanAccountId }),
      });
      const connectionId = String(payload?.connection_id || '').trim();
      const quickCreateUrl = String(payload?.quick_create_url || '').trim();
      if (!connectionId || !quickCreateUrl) {
        throw new Error('AWS connection details were not returned.');
      }
      setAwsConnectionId(connectionId);
      setAwsExternalId(String(payload?.external_id || '').trim());
      setAwsRoleArn(String(payload?.role_arn || '').trim());
      setAwsEmpyralisAccountId(String(payload?.empyralis_account_id || '').trim());
      setAwsQuickCreateUrl(quickCreateUrl);
      setAwsSubStep('stack');
    } catch (connectError) {
      setError(connectError instanceof Error ? connectError.message : 'Could not start the AWS connection.');
    } finally {
      setBusy(false);
    }
  }

  function openAwsCloudFormation() {
    if (!awsQuickCreateUrl) {
      return;
    }
    const popup = window.open(awsQuickCreateUrl, '_blank', 'noopener,noreferrer');
    if (!popup) {
      // Popup blocked — fall back to navigating this tab; the panel state
      // (connection id, role arn, etc.) is untouched either way, so coming
      // back and clicking "Confirm" still works.
      window.location.assign(awsQuickCreateUrl);
    }
  }

  // AWS step 2: after the customer has (supposedly) run the stack, attempt
  // sts:AssumeRole against the role ARN + ExternalId from step 1. A failure
  // here just means the stack isn't done yet (or failed) — retryable, and
  // awsConnectionId/awsRoleArn/etc. stay put so "Confirm" can simply be
  // clicked again without re-entering the account id.
  async function confirmAwsConnect() {
    if (!awsConnectionId) {
      setError('Start the AWS connection first.');
      setAwsSubStep('account');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const payload = await requestJson<VpsAwsConfirmResponse>('/api/hardware/vps/aws/confirm', {
        method: 'POST',
        headers: {
          accept: 'application/json',
          'content-type': 'application/json',
        },
        body: JSON.stringify({ workspace_id: workspaceId, connection_id: awsConnectionId }),
      });
      const nextTokenId = String(payload?.token_id || '').trim();
      if (!nextTokenId) {
        throw new Error('AWS did not return a connected account id.');
      }
      const accountLabel = `AWS ${String(payload?.account_id || awsAccountId).trim()}`;
      saveConnection('aws', nextTokenId, accountLabel);
      await finishConnecting('aws', nextTokenId);
    } catch (confirmError) {
      setError(
        confirmError instanceof Error
          ? confirmError.message
          : 'Could not verify the AWS role yet — make sure the CloudFormation stack finished, then try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  // Called once an account is connected (OAuth callback or "Verify token"),
  // regardless of how we got to the 'access' step. Normal case: the user
  // hadn't picked anything yet, so land on 'plans' the way it's always
  // worked. Pending-create case: the user already picked a plan + region
  // while browsing pre-connect (Vultr) and clicked "Create server", which is
  // what sent them here — resume that instead of making them pick again.
  async function finishConnecting(providerId: VpsProviderId, nextTokenId: string) {
    if (pendingCreateAfterConnect) {
      setPendingCreateAfterConnect(false);
      await refreshChoicesThenCreate(providerId, nextTokenId);
      return;
    }
    await prepareServerChoices(providerId, nextTokenId);
  }

  // Re-fetches plans/regions with the now-real account token (the
  // pre-connect public catalog and the authenticated one can differ —
  // promotional/free plans, account-specific availability) while preserving
  // the plan + region the user already chose if it's still valid, then
  // creates the server. Explicit overrides are passed straight into
  // createServer() rather than relying on selectedPlanId/selectedRegionId/
  // tokenId state, which wouldn't be updated yet in THIS closure — state
  // setters schedule a re-render, they don't mutate the variables already
  // captured here.
  async function refreshChoicesThenCreate(providerId: VpsProviderId, nextTokenId: string) {
    setSelectedProvider(providerId);
    setTokenId(nextTokenId);
    const previousPlanId = selectedPlanId;
    const previousRegionId = selectedRegionId;
    const [nextPlans, nextRegions] = await Promise.all([
      loadPlans(providerId, nextTokenId),
      loadRegions(providerId, nextTokenId),
    ]);
    if (!nextPlans.length) {
      setStep('access');
      return;
    }
    const effectivePlanId = nextPlans.some((plan) => plan.id === previousPlanId)
      ? previousPlanId
      : (nextPlans.find((plan) => plan.recommended)?.id ?? nextPlans[0].id);
    const effectivePlan = nextPlans.find((plan) => plan.id === effectivePlanId) ?? null;
    const effectivePlanRegions = effectivePlan?.regions ?? [];
    const allowedRegionIds = effectivePlanRegions.length ? new Set(effectivePlanRegions) : null;
    const regionStillValid = nextRegions.some((region) => region.id === previousRegionId)
      && (!allowedRegionIds || allowedRegionIds.has(previousRegionId));
    const fallbackRegion = nextRegions.find((region) => !allowedRegionIds || allowedRegionIds.has(region.id));
    const effectiveRegionId = regionStillValid ? previousRegionId : (fallbackRegion?.id ?? nextRegions[0]?.id ?? '');
    setSelectedPlanId(effectivePlanId);
    setSelectedRegionId(effectiveRegionId);
    setStep('region');
    await createServer({ tokenId: nextTokenId, planId: effectivePlanId, regionId: effectiveRegionId });
  }

  function goBack() {
    if (step === 'access') {
      if (pendingCreateAfterConnect) {
        setPendingCreateAfterConnect(false);
        setStep('region');
        return;
      }
      setStep('provider');
      return;
    }
    if (step === 'google-project') {
      setStep('provider');
      return;
    }
    if (step === 'google-billing') {
      setStep('google-project');
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

  // Reached from the 'region' step's button: if the account isn't connected
  // yet (pre-connect Vultr browsing), send the user to connect first and
  // resume creation automatically once they do — "connect only right before
  // the Create Server click" rather than forcing it up front.
  function handleCreateServerClick() {
    if (!tokenId) {
      setPendingCreateAfterConnect(true);
      setStep('access');
      return;
    }
    void createServer();
  }

  // Accepts explicit overrides so refreshChoicesThenCreate can hand it
  // freshly-fetched values in the same tick it computed them, rather than
  // relying on selectedPlanId/selectedRegionId/tokenId state — those
  // wouldn't have re-rendered into this closure yet. The plain "Create
  // server" button click (handleCreateServerClick) calls this with no
  // overrides, which keeps reading current state exactly as before.
  async function createServer(overrides?: { tokenId?: string; planId?: string; regionId?: string }) {
    const effectiveTokenId = overrides?.tokenId ?? tokenId;
    const effectivePlanId = overrides?.planId ?? selectedPlanId;
    const effectiveRegionId = overrides?.regionId ?? selectedRegionId;
    if (!selectedProvider) {
      setError('Choose a provider first.');
      setStep('provider');
      return;
    }
    if (!effectiveTokenId) {
      setError('Connect the provider account first.');
      setStep('access');
      return;
    }
    if (!effectivePlanId) {
      setError('Choose a plan first.');
      setStep('plans');
      return;
    }
    if (!effectiveRegionId) {
      setError('Choose a region first.');
      setStep('region');
      return;
    }
    setBusy(true);
    setError(null);
    setProgressStage('creating');
    setStep('progress');
    try {
      const payload = await requestJson<VpsProvisionResponse>('/api/hardware/vps/provision', {
        method: 'POST',
        headers: {
          accept: 'application/json',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          workspace_id: workspaceId,
          provider: selectedProvider,
          token_id: effectiveTokenId,
          region: effectiveRegionId,
          size: effectivePlanId,
          runtime_access_mode: 'full_access',
          autonomous_agent_setup_warning_acknowledged: true,
          metadata: {
            autonomous_agent_setup_warning_version: FULL_ACCESS_WARNING_VERSION,
          },
        }),
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
    // Tracks the most recently seen provider_resource_id across polls (NOT
    // React state — a state update from inside this loop wouldn't be visible
    // to this same closure until the next render) so the deadline fallback
    // below can also know whether a resource actually got created.
    let lastKnownResourceId = '';
    while (Date.now() < deadline) {
      await wait(5_000);
      try {
        const payload = await requestJson<VpsProvisionStatusPayload>(
          `/api/hardware/vps/${encodeURIComponent(nextVpsId)}/status`,
        );
        const status = String(payload?.status || '').toLowerCase();
        lastKnownResourceId = String(payload?.provider_resource_id || '').trim();
        setProviderResourceId(lastKnownResourceId || null);
        if (status === 'connected') {
          setProgressStage('connected');
          window.setTimeout(() => {
            void onConnected();
          }, 900);
          return;
        }
        if (status === 'failed') {
          setProgressStage('failed');
          setError(friendlyProvisionFailureMessage(String(payload?.error || ''), Boolean(lastKnownResourceId)));
          return;
        }
        setProgressStage(status === 'registering' ? 'connecting' : 'installing');
      } catch (pollError) {
        setError(pollError instanceof Error ? pollError.message : 'Could not check VPS setup status.');
      }
    }
    setProgressStage('failed');
    setError(friendlyProvisionFailureMessage('', Boolean(lastKnownResourceId)));
  }

  async function deleteFailedServer() {
    if (!vpsId) {
      return;
    }
    setCleanupBusy(true);
    setError(null);
    try {
      await requestJson<Record<string, unknown>>(`/api/hardware/vps/${encodeURIComponent(vpsId)}`, {
        method: 'DELETE',
        headers: { accept: 'application/json' },
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
                      <strong>{item.accountMethod}</strong>
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
            {pendingCreateAfterConnect && selectedPlan ? (
              <p className="cloud-vps-panel__note">
                {`Connect to create your ${selectedPlan.label} · ${selectedPlan.price_label} server.`}
              </p>
            ) : null}
{provider.id === 'google' ? (
              // No token-paste fallback for Google (see PROVIDER_CONFIGS
              // ["google"].token_keys on the backend) — OAuth is the only
              // path, so this step is just the sign-in button.
              <>
                <p className="cloud-vps-panel__note">
                  The server is created in your own Google Cloud project — Empyralis never sees or stores your
                  Google password, and you keep full control of billing.
                </p>
                <AppButton tone="primary" type="button" onClick={() => void startGoogleOAuth()} disabled={busy}>
                  {busy ? 'Opening Google sign-in…' : 'Sign in with Google'}
                </AppButton>
              </>
            ) : provider.id === 'aws' ? (
              awsSubStep === 'account' ? (
                <div className="cloud-vps-access-form">
                  <p className="cloud-vps-panel__note">
                    No API key to paste. Empyralis connects to AWS through a CloudFormation-created IAM
                    role scoped to EC2 only — enter your AWS account id to get a ready-to-run setup link.
                  </p>
                  <label className="app-form-field">
                    <span className="app-form-field__label">AWS Account ID</span>
                    <input
                      className="app-field"
                      type="text"
                      inputMode="numeric"
                      autoComplete="off"
                      maxLength={12}
                      value={awsAccountId}
                      onChange={(event) => setAwsAccountId(event.target.value.replace(/[^0-9]/g, ''))}
                      placeholder="123456789012"
                    />
                  </label>
                  <AppButton
                    tone="primary"
                    type="button"
                    onClick={() => void startAwsConnect()}
                    disabled={busy || awsAccountId.trim().length !== 12}
                  >
                    {busy ? 'Preparing setup link' : 'Continue →'}
                  </AppButton>
                </div>
              ) : (
                <div className="cloud-vps-access-form">
                  <p className="cloud-vps-panel__note">
                    Open AWS CloudFormation and create the stack — the role name and external ID are
                    already filled in below, nothing to type or paste there.
                  </p>
                  <dl className="cloud-vps-aws-detail">
                    <dt>Granting access to</dt>
                    <dd>{awsEmpyralisAccountId ? `Empyralis AWS account ${awsEmpyralisAccountId}` : '—'}</dd>
                    <dt>Role (fixed name)</dt>
                    <dd>{awsRoleArn || '—'}</dd>
                    <dt>External ID</dt>
                    <dd>{awsExternalId || '—'}</dd>
                  </dl>
                  <AppButton tone="secondary" type="button" onClick={openAwsCloudFormation} disabled={!awsQuickCreateUrl}>
                    <span>Open AWS CloudFormation</span>
                    <ExternalLink size={13} strokeWidth={2} aria-hidden="true" />
                  </AppButton>
                  <AppButton tone="primary" type="button" onClick={() => void confirmAwsConnect()} disabled={busy}>
                    {busy ? 'Verifying role' : "I've created the stack — Confirm →"}
                  </AppButton>
                  <button
                    type="button"
                    className="cloud-vps-token-link"
                    onClick={() => {
                      setAwsSubStep('account');
                      setError(null);
                    }}
                  >
                    <span>Use a different AWS account</span>
                  </button>
                </div>
              )
            ) : (
              <>
                {provider.id === 'digitalocean' ? (
                  <>
                    <AppButton tone="primary" type="button" onClick={() => void startDigitalOceanOAuth()} disabled={busy}>
                      {busy ? 'Opening DigitalOcean' : 'Log in with DigitalOcean'}
                    </AppButton>
                    <p className="cloud-vps-panel__note">Or connect with a personal access token:</p>
                  </>
                ) : null}
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
              </>
            )}
            {error ? <p className="cloud-vps-panel__error">{error}</p> : null}
          </section>
        ) : null}

        {step === 'google-project' && provider ? (
          <section className="cloud-vps-flow-modal__content">
            <div className="cloud-vps-panel__heading">
              <h2>Choose a Google Cloud project</h2>
              <p>Empyralis creates the server inside this project — you keep full ownership and billing.</p>
            </div>
            {loadingGoogleProjects ? <p className="cloud-vps-panel__muted">Loading your projects...</p> : null}
            {!loadingGoogleProjects && googleProjects.length ? (
              <div className="cloud-vps-plan-list">
                {googleProjects.map((project) => (
                  <button
                    key={project.project_id}
                    type="button"
                    className={joinClassNames(
                      'cloud-vps-plan-row',
                      selectedGoogleProjectId === project.project_id && 'is-selected',
                    )}
                    onClick={() => setSelectedGoogleProjectId(project.project_id)}
                  >
                    <span className="cloud-vps-plan-row__radio" aria-hidden="true" />
                    <strong>{project.name}</strong>
                    <span>{project.project_id}</span>
                  </button>
                ))}
              </div>
            ) : null}
            {!loadingGoogleProjects && !googleProjects.length ? (
              <p className="cloud-vps-panel__muted">No existing projects on this account — create one below.</p>
            ) : null}
            <div className="cloud-vps-access-form">
              <label className="app-form-field">
                <span className="app-form-field__label">Or create a new project</span>
                <input
                  className="app-field"
                  type="text"
                  value={googleNewProjectName}
                  onChange={(event) => setGoogleNewProjectName(event.target.value)}
                  placeholder="Empyralis Agent Computer"
                />
              </label>
              <AppButton
                tone="secondary"
                type="button"
                onClick={() => void createNewGoogleProject()}
                disabled={creatingGoogleProject || !googleNewProjectName.trim()}
              >
                {creatingGoogleProject ? 'Creating project…' : 'Create new project'}
              </AppButton>
            </div>
            <div className="cloud-vps-panel__footer">
              <AppButton
                tone="primary"
                type="button"
                onClick={() => void continueFromGoogleProjectStep()}
                disabled={!selectedGoogleProjectId || loadingGoogleProjects}
              >
                Continue →
              </AppButton>
            </div>
            {error ? <p className="cloud-vps-panel__error">{error}</p> : null}
          </section>
        ) : null}

        {step === 'google-billing' && provider ? (
          <section className="cloud-vps-flow-modal__content">
            <div className="cloud-vps-panel__heading">
              <h2>Billing account</h2>
            </div>
            {checkingGoogleBilling ? <p className="cloud-vps-panel__muted">Checking billing status...</p> : null}
            {!checkingGoogleBilling && googleBillingEnabled === true ? (
              <p className="cloud-vps-panel__note">Billing is linked to this project — ready to continue.</p>
            ) : null}
            {!checkingGoogleBilling && googleBillingEnabled === false ? (
              <>
                <p className="cloud-vps-panel__note">
                  This project has no billing account attached yet. Google requires one before any server can be
                  created, and there is no way for Empyralis to attach one on your behalf — it has to be done in
                  the console.
                </p>
                <a className="cloud-vps-token-link" href={googleBillingConsoleUrl} target="_blank" rel="noreferrer">
                  <span>Attach a billing account in the Google Cloud console</span>
                  <ExternalLink size={13} strokeWidth={2} aria-hidden="true" />
                </a>
                <AppButton tone="secondary" type="button" onClick={() => void checkGoogleBilling()} disabled={checkingGoogleBilling}>
                  I&rsquo;ve added billing — check again
                </AppButton>
              </>
            ) : null}
            <div className="cloud-vps-panel__footer">
              <AppButton
                tone="primary"
                type="button"
                onClick={() => void finishGoogleBootstrap()}
                disabled={bootstrappingGoogle || checkingGoogleBilling || googleBillingEnabled !== true}
              >
                {bootstrappingGoogle ? 'Setting up…' : 'Continue →'}
              </AppButton>
            </div>
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
                {visibleRegions.map((region) => (
                  <option key={region.id} value={region.id}>
                    {`${region.label} · ${region.id}`}
                  </option>
                ))}
              </select>
              {selectedPlan?.regions?.length ? (
                <span className="cloud-vps-panel__note">{`Available where ${selectedPlan.label} is offered.`}</span>
              ) : null}
            </label>
            {selectedPlan ? (
              <p className="cloud-vps-panel__note">{`${selectedPlan.label} · ${selectedPlan.price_label}`}</p>
            ) : null}
            <div className="cloud-vps-panel__footer">
              <AppButton
                tone="primary"
                type="button"
                onClick={handleCreateServerClick}
                disabled={busy || loadingRegions || !selectedRegionId}
              >
                {busy
                  ? 'Creating server'
                  : tokenId
                    ? 'Create server →'
                    : `Connect ${provider.label} & create →`}
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
