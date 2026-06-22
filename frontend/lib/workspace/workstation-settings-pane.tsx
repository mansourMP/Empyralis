'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Moon, SunMedium } from 'lucide-react';

import { logout, me, type AuthProviderOptions, listAuthProviders } from '@/lib/auth/auth-client';
import {
  AppButton,
  AppNotice,
  AppSurfaceCard,
  AppSurfaceList,
  AppSurfaceListItem,
  AppSurfaceStat,
  AppSurfaceStatGrid,
  joinClassNames,
} from '@/lib/ui/primitives';
import { useAppTheme } from '@/lib/ui/app-theme';
import { FormGrid, FormReadout } from '@/lib/ui/form-controls';
import { useAccountShell } from '@/lib/shell/account-shell-context';
import { DataPaneError } from '@/lib/workspace/data-pane-error';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import { WorkstationPlatformAnalyticsPane } from '@/lib/workspace/workstation-platform-analytics-pane';
import type { ProviderCatalogRecord, ProviderProfileRecord } from '@/lib/workspace/workstation-client';

type SettingsSectionId = 'account' | 'appearance' | 'usage' | 'billing' | 'privacy' | 'transparency';

const SETTINGS_SECTIONS: Array<{
  id: SettingsSectionId;
  label: string;
  eyebrow: string;
  title: string;
  description: string;
}> = [
  {
    id: 'account',
    label: 'Account',
    eyebrow: 'Identity',
    title: 'Account',
    description: 'Profile and plan.',
  },
  {
    id: 'appearance',
    label: 'Appearance',
    eyebrow: 'Display',
    title: 'Appearance',
    description: 'Theme and interface density.',
  },
  {
    id: 'usage',
    label: 'Usage',
    eyebrow: 'Analytics',
    title: 'Usage',
    description: 'Workspace usage.',
  },
  {
    id: 'billing',
    label: 'Limits',
    eyebrow: 'Usage',
    title: 'Usage limits',
    description: 'Workspace limits and current usage.',
  },
  {
    id: 'privacy',
    label: 'Privacy & Safety',
    eyebrow: 'Trust',
    title: 'Privacy & Safety',
    description: 'Approvals, memory, and computer trust.',
  },
  {
    id: 'transparency',
    label: 'Transparency',
    eyebrow: 'Visibility',
    title: 'Transparency',
    description: 'Transparency shows action traces, not private model reasoning.',
  },
];

function humanizeToken(value: string): string {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

type AccountProfilePayload = Record<string, unknown> & {
  user?: Record<string, unknown> | null;
  identity_boundary?: Record<string, unknown> | null;
  current_workspace_entitlements?: Record<string, unknown> | null;
};

type BillingSummaryPayload = Record<string, unknown> & {
  subscription?: Record<string, unknown> | null;
  hosted_sage_ai?: Record<string, unknown> | null;
};

type AccountMethodRecord = Record<string, unknown> & {
  provider?: string | null;
  method_type?: string | null;
  label?: string | null;
  status?: string | null;
  is_primary?: boolean | null;
  can_recover?: boolean | null;
};

type HostedCreditsSnapshot = {
  allowed: boolean;
  message: string;
  monthlyCreditCap: number;
  monthlyCreditsRemaining: number;
};

type AgentComputerNativeRuntime = {
  os: string;
  arch: string;
  release: string;
  hostname: string;
  desktopSession: string;
  systemServiceMode: boolean;
};

type AgentComputerAttachment = {
  attachmentId: string;
  attachmentKind: string;
  label: string;
  online: boolean;
  healthy: boolean;
  status: string;
  statusReason: string | null;
  heartbeatAt: string | null;
  capabilities: string[];
  capabilityReadiness: Record<string, unknown>;
  nativeRuntime: AgentComputerNativeRuntime | null;
  targetSelection: Record<string, unknown>;
};

type AgentComputerTargetSummary = {
  label: string;
  online: boolean;
  healthy: boolean;
  status: string;
  statusLabel: string | null;
  statusReason: string | null;
} | null;

function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readOptionalString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function readNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function readBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function readRecord(value: unknown): Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readField(record: Record<string, unknown>, camelKey: string, snakeKey: string): unknown {
  return record[camelKey] ?? record[snakeKey];
}

function formatCredits(value: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(Math.max(0, Math.round(value)));
}

function normalizeHostedCreditValue(record: Record<string, unknown>, usdKey: string, creditKey: string): number {
  const explicitCredits = readNumber(record[creditKey], Number.NaN);
  if (Number.isFinite(explicitCredits)) {
    return explicitCredits;
  }
  const creditsPerUsd = Math.max(1, readNumber(record.credits_per_usd, 1000));
  return readNumber(record[usdKey], 0) * creditsPerUsd;
}

function normalizeHostedCredits(summary: BillingSummaryPayload | null): HostedCreditsSnapshot {
  const hosted = summary && typeof summary.hosted_sage_ai === 'object'
    ? summary.hosted_sage_ai as Record<string, unknown>
    : {};
  return {
    allowed: hosted.allowed === true,
    message: readString(hosted.message, 'Credits are not active yet.'),
    monthlyCreditCap: normalizeHostedCreditValue(hosted, 'monthly_cap_usd', 'monthly_credit_cap'),
    monthlyCreditsRemaining: normalizeHostedCreditValue(hosted, 'monthly_remaining_usd', 'monthly_credits_remaining'),
  };
}

function normalizeAuthMethods(profile: AccountProfilePayload | null): AccountMethodRecord[] {
  const boundary = profile && typeof profile.identity_boundary === 'object'
    ? profile.identity_boundary as Record<string, unknown>
    : {};
  return Array.isArray(boundary.auth_methods)
    ? boundary.auth_methods.filter((item): item is AccountMethodRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function formatAuthMethodLabel(method: AccountMethodRecord): string {
  const provider = readString(method.provider).toLowerCase();
  if (provider === 'empyralis_password') {
    return 'Email and password';
  }
  if (provider === 'google') {
    return 'Google';
  }
  if (provider === 'oidc') {
    return 'Single sign-on';
  }
  const label = readString(method.label);
  if (label) {
    return label;
  }
  return humanizeToken(provider || readString(method.method_type, 'sign-in'));
}

function formatAuthMethodDetail(method: AccountMethodRecord): string {
  const notes: string[] = [];
  if (method.is_primary === true) {
    notes.push('Primary sign-in');
  }
  if (method.can_recover === true) {
    notes.push('Recovery enabled');
  }
  const status = readString(method.status);
  if (status && status !== 'active') {
    notes.push(humanizeToken(status));
  }
  return notes.join(' · ') || 'Available';
}

function normalizeProviderRecords(payload: unknown): ProviderCatalogRecord[] {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  return Array.isArray(record.providers)
    ? record.providers.filter((item): item is ProviderCatalogRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function normalizeProfileRecords(payload: unknown): ProviderProfileRecord[] {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  return Array.isArray(record.items)
    ? record.items.filter((item): item is ProviderProfileRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function normalizeNativeRuntime(value: unknown): AgentComputerNativeRuntime | null {
  const record = readRecord(value);
  const os = readString(record.os);
  const arch = readString(record.arch);
  const desktopSession = readString(record.desktop_session);
  const systemServiceMode = readBoolean(record.system_service_mode);
  if (!os && !arch && !desktopSession && !systemServiceMode) {
    return null;
  }
  return {
    os,
    arch,
    release: readString(record.release),
    hostname: readString(record.hostname),
    desktopSession,
    systemServiceMode,
  };
}

function normalizeAgentComputerAttachments(payload: unknown): AgentComputerAttachment[] {
  const record = readRecord(payload);
  const attachments = Array.isArray(record.attachments) ? record.attachments : [];
  const agentComputerKinds = new Set(['local_companion', 'cloud_computer', 'self_hosted_business_node']);

  return attachments.flatMap((entry) => {
    const attachment = readRecord(entry);
    const attachmentKind = readString(readField(attachment, 'attachmentKind', 'attachment_kind'));
    if (!agentComputerKinds.has(attachmentKind)) {
      return [];
    }
    const attachmentId = readString(readField(attachment, 'attachmentId', 'attachment_id'), attachmentKind);
    const label = readString(attachment.label, humanizeToken(attachmentKind));
    return [{
      attachmentId,
      attachmentKind,
      label,
      online: readBoolean(attachment.online),
      healthy: readBoolean(attachment.healthy),
      status: readString(attachment.status, 'offline').toLowerCase(),
      statusReason: readOptionalString(readField(attachment, 'statusReason', 'status_reason')),
      heartbeatAt: readOptionalString(readField(attachment, 'heartbeatAt', 'heartbeat_at')),
      capabilities: Array.isArray(attachment.capabilities)
        ? attachment.capabilities.map((item) => readString(item)).filter(Boolean)
        : [],
      capabilityReadiness: readRecord(readField(attachment, 'capabilityReadiness', 'capability_readiness')),
      nativeRuntime: normalizeNativeRuntime(readField(attachment, 'nativeRuntime', 'native_runtime')),
      targetSelection: readRecord(readField(attachment, 'targetSelection', 'target_selection')),
    }];
  });
}

function statusToneClass(status: string, fallbackReady = false): string {
  const normalized = readString(status).toLowerCase();
  if (['ready', 'online', 'healthy', 'ok', 'available'].includes(normalized) || fallbackReady) {
    return 'settings-status--ready';
  }
  if (['degraded', 'stale', 'warning', 'warn', 'reconnecting', 'registered', 'promptable'].includes(normalized)) {
    return 'settings-status--warning';
  }
  if (['blocked', 'revoked', 'error', 'failed', 'fail', 'unhealthy', 'denied', 'restricted'].includes(normalized)) {
    return 'settings-status--danger';
  }
  return 'settings-status--muted';
}

function deriveActiveModelPath(
  providers: ProviderCatalogRecord[],
  profiles: ProviderProfileRecord[],
  hostedCredits: HostedCreditsSnapshot,
): { value: string; hint: string } {
  const enabledProfiles = [...profiles]
    .filter((profile) => profile.enabled !== false)
    .sort((left, right) => Number(left.priority ?? 100) - Number(right.priority ?? 100));
  const selectedProfile = enabledProfiles[0] ?? null;
  if (selectedProfile) {
    const providerId = readString(selectedProfile.provider).toLowerCase();
    const provider = providers.find((item) => readString(item.id).toLowerCase() === providerId) ?? null;
    const credentialPlane = readString(provider?.credential_plane).toLowerCase();
    if (credentialPlane === 'platform_runtime') {
      const selectedModel = readString(selectedProfile.model || provider?.default_model).toLowerCase();
      const tierLabel = selectedModel === 'light' || selectedModel.includes('flash')
        ? 'Light AI'
        : selectedModel === 'max' || selectedModel.includes('reasoner')
          ? 'Max AI'
          : 'Pro AI';
      return {
        value: tierLabel,
        hint: 'Workspace AI',
      };
    }
    const providerLabel = readString(provider?.label, providerId || 'AI model');
    const modelLabel = readString(selectedProfile.model)
      || readString(provider?.default_model)
      || 'Default model';
    const routeLabel = provider?.local_only === true || providerId === 'ollama'
      ? 'This Device'
      : 'Your AI account';
    return {
      value: `${providerLabel} · ${modelLabel}`,
      hint: routeLabel,
    };
  }

  if (hostedCredits.allowed) {
    const hostedProvider = providers.find((provider) => {
      const policy = readString(provider.hosted_sage_ai_policy).toLowerCase();
      return policy === 'enabled_with_cap' || policy === 'allowed';
    }) ?? null;
    const defaultModel = readString(hostedProvider?.default_model).toLowerCase();
    const tierLabel = defaultModel.includes('flash')
      ? 'Light AI'
      : defaultModel.includes('reasoner')
        ? 'Max AI'
        : 'Pro AI';
    return {
      value: tierLabel,
      hint: 'Workspace AI',
    };
  }

  return {
    value: 'No AI model selected',
    hint: 'Choose Workspace AI, connect your own AI account, or connect a computer.',
  };
}

function isSettingsSectionId(value: string | null): value is SettingsSectionId {
  return value === 'account'
    || value === 'appearance'
    || value === 'usage'
    || value === 'billing'
    || value === 'privacy'
    || value === 'transparency';
}

const VISIBILITY_MODES = [
  { value: 'off', label: 'Quiet' },
  { value: 'minimal', label: 'Basic' },
  { value: 'standard', label: 'Normal' },
  { value: 'full', label: 'Detailed' },
  { value: 'enterprise', label: 'Admin' },
] as const;

type VisibilityMode = typeof VISIBILITY_MODES[number]['value'];

type TransparencySettingsPayload = {
  workspace_id: string;
  default_mode: VisibilityMode;
  sage_mode: VisibilityMode;
  studio_test_mode: VisibilityMode;
  customer_mode: 'off' | 'minimal';
  show_trace_ids: boolean;
  show_tool_names: boolean;
  show_memory_usage: boolean;
  show_policy_blocks: boolean;
  show_sources: boolean;
};

const DEFAULT_TRANSPARENCY_SETTINGS: TransparencySettingsPayload = {
  workspace_id: '',
  default_mode: 'standard',
  sage_mode: 'standard',
  studio_test_mode: 'full',
  customer_mode: 'minimal',
  show_trace_ids: true,
  show_tool_names: true,
  show_memory_usage: false,
  show_policy_blocks: true,
  show_sources: true,
};

function normalizeVisibilityMode(value: unknown, fallback: VisibilityMode): VisibilityMode {
  const token = readString(value).toLowerCase();
  return VISIBILITY_MODES.some((mode) => mode.value === token) ? token as VisibilityMode : fallback;
}

function normalizeTransparencySettings(payload: unknown): TransparencySettingsPayload {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  const customerMode = normalizeVisibilityMode(record.customer_mode, DEFAULT_TRANSPARENCY_SETTINGS.customer_mode);
  return {
    workspace_id: readString(record.workspace_id),
    default_mode: normalizeVisibilityMode(record.default_mode, DEFAULT_TRANSPARENCY_SETTINGS.default_mode),
    sage_mode: normalizeVisibilityMode(record.sage_mode, DEFAULT_TRANSPARENCY_SETTINGS.sage_mode),
    studio_test_mode: normalizeVisibilityMode(record.studio_test_mode, DEFAULT_TRANSPARENCY_SETTINGS.studio_test_mode),
    customer_mode: customerMode === 'off' ? 'off' : 'minimal',
    show_trace_ids: record.show_trace_ids !== false,
    show_tool_names: record.show_tool_names !== false,
    show_memory_usage: record.show_memory_usage === true,
    show_policy_blocks: record.show_policy_blocks !== false,
    show_sources: record.show_sources !== false,
  };
}

function TransparencyModeSelect({
  label,
  value,
  allowedModes = VISIBILITY_MODES.map((mode) => mode.value),
  disabled = false,
  onChange,
}: {
  label: string;
  value: VisibilityMode;
  allowedModes?: readonly VisibilityMode[];
  disabled?: boolean;
  onChange: (value: VisibilityMode) => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, color: 'var(--color-muted, #6b7280)' }}>{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(normalizeVisibilityMode(event.target.value, value))}
        style={{
          padding: '4px 8px', fontSize: 13, borderRadius: 6,
          border: '1px solid var(--color-border, #e5e7eb)',
          background: 'var(--color-bg, #fff)', color: 'var(--color-fg, #111)',
        }}
      >
        {VISIBILITY_MODES.filter((mode) => allowedModes.includes(mode.value)).map(({ value: v, label: l }) => (
          <option key={v} value={v}>{l}</option>
        ))}
      </select>
    </div>
  );
}

function AgentComputerDetailsPanel({
  attachments,
  localTarget,
  error,
}: {
  attachments: AgentComputerAttachment[];
  localTarget: AgentComputerTargetSummary;
  error: string | null;
}) {
  const selectedAttachment = attachments.find((attachment) => attachment.attachmentKind === 'local_companion')
    ?? attachments.find((attachment) => attachment.attachmentKind === 'self_hosted_business_node')
    ?? attachments.find((attachment) => attachment.attachmentKind === 'cloud_computer')
    ?? null;
  const status = selectedAttachment?.status ?? localTarget?.status ?? 'offline';
  const isReady = selectedAttachment
    ? selectedAttachment.online && selectedAttachment.healthy
    : Boolean(localTarget?.online && localTarget?.healthy);
  const statusLabel = selectedAttachment
    ? humanizeToken(status)
    : localTarget?.statusLabel || humanizeToken(status);
  const computerLabel = selectedAttachment?.label || localTarget?.label || 'This computer';

  return (
    <article className="settings-detail-card settings-agent-computer-details">
      <div className="settings-detail-card__header">
        <div className="settings-agent-computer-details__copy">
          <strong className="settings-detail-card__title">
            {computerLabel}
          </strong>
          <p className="settings-detail-card__body">
            {isReady ? 'Empyralis can use this computer.' : 'Empyralis cannot use this computer right now.'}
          </p>
        </div>
        <span className={joinClassNames('settings-status', statusToneClass(status, isReady))}>
          {isReady ? 'Connected' : statusLabel}
        </span>
      </div>

      {error ? <AppNotice tone="warning">{error}</AppNotice> : null}
      {!selectedAttachment ? (
        <AppNotice tone="neutral">Connect this computer when you want Sage or an approved agent to use local apps, files, browser, or terminal work.</AppNotice>
      ) : null}
    </article>
  );
}

export function WorkstationSettingsPane() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { bootstrap } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const { state: accountShellState, actions: accountShellActions } = useAccountShell();
  const { resolvedTheme } = useAppTheme();
  const [selectedSection, setSelectedSection] = useState<SettingsSectionId>(() => {
    const requestedSection = searchParams.get('section');
    return isSettingsSectionId(requestedSection) ? requestedSection : 'account';
  });
  const [accountProfile, setAccountProfile] = useState<AccountProfilePayload | null>(null);
  const [billingSummary, setBillingSummary] = useState<BillingSummaryPayload | null>(null);
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogRecord[]>([]);
  const [providerProfiles, setProviderProfiles] = useState<ProviderProfileRecord[]>([]);
  const [transparencySettings, setTransparencySettings] = useState<TransparencySettingsPayload>(DEFAULT_TRANSPARENCY_SETTINGS);
  const [transparencyStatus, setTransparencyStatus] = useState<string | null>(null);
  const [transparencyError, setTransparencyError] = useState<string | null>(null);
  const [savingTransparencyKey, setSavingTransparencyKey] = useState<string | null>(null);
  const [authProviders, setAuthProviders] = useState<AuthProviderOptions>({
    email: { enabled: true },
    google: { enabled: false },
  });
  const [accountDetailsError, setAccountDetailsError] = useState<unknown>(null);
  const [isLoadingAccountDetails, setIsLoadingAccountDetails] = useState(true);
  const [logoutPending, setLogoutPending] = useState(false);

  useEffect(() => {
    const requestedSection = searchParams.get('section');
    if (requestedSection === 'devices') {
      router.replace(pathname.replace(/\/settings(?:\/.*)?$/, '/hardware'), { scroll: false });
      return;
    }
    if (isSettingsSectionId(requestedSection) && requestedSection !== selectedSection) {
      setSelectedSection(requestedSection);
    }
  }, [pathname, router, searchParams, selectedSection]);

  const selectSettingsSection = (sectionId: SettingsSectionId) => {
    setSelectedSection(sectionId);
    const nextParams = new URLSearchParams(searchParams.toString());
    nextParams.set('section', sectionId);
    const nextQuery = nextParams.toString();
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
  };

  const refreshAccountDetails = () => {
    setAccountDetailsError(null);
    setIsLoadingAccountDetails(true);

    void Promise.allSettled([
      me(),
      services.client.getBillingSummary(),
      services.client.listProviderCatalog(),
      services.client.listProviderProfiles(),
      services.client.getWorkspaceTransparencySettings(),
      listAuthProviders(),
    ]).then((results) => {
      const [
        profileResult,
        billingResult,
        catalogResult,
        profilesResult,
        transparencyResult,
        authProvidersResult,
      ] = results;

      if (profileResult.status === 'fulfilled') {
        setAccountProfile((profileResult.value ?? null) as AccountProfilePayload | null);
      }
      if (billingResult.status === 'fulfilled') {
        setBillingSummary((billingResult.value ?? null) as BillingSummaryPayload | null);
      }
      if (catalogResult.status === 'fulfilled') {
        setProviderCatalog(normalizeProviderRecords(catalogResult.value));
      }
      if (profilesResult.status === 'fulfilled') {
        setProviderProfiles(normalizeProfileRecords(profilesResult.value));
      }
      if (transparencyResult.status === 'fulfilled') {
        setTransparencySettings(normalizeTransparencySettings(transparencyResult.value));
      }
      if (authProvidersResult.status === 'fulfilled') {
        setAuthProviders({
          email: { enabled: authProvidersResult.value?.email?.enabled !== false },
          google: { enabled: authProvidersResult.value?.google?.enabled === true },
        });
      }
      const firstFailure = results.find((r) => r.status === 'rejected');
      if (firstFailure) {
        setAccountDetailsError(firstFailure.reason);
      }
      setIsLoadingAccountDetails(false);
    });
  };

  useEffect(() => {
    refreshAccountDetails();
  }, [services.client]);

  const activeSection = SETTINGS_SECTIONS.find((section) => section.id === selectedSection) ?? SETTINGS_SECTIONS[0];
  const currentThemePreference = accountShellState.globalTheme;
  const nextAppearanceTheme = resolvedTheme === 'dark' ? 'light' : 'dark';
  const AppearanceThemeIcon = resolvedTheme === 'dark' ? SunMedium : Moon;
  const accountDisplayName = bootstrap.account.displayName?.trim() || 'Empyralis User';
  const accountEmail = bootstrap.account.email;
  const accountInitial = (accountDisplayName || accountEmail).charAt(0).toUpperCase();
  const hostedCredits = useMemo(() => normalizeHostedCredits(billingSummary), [billingSummary]);
  const authMethods = useMemo(() => normalizeAuthMethods(accountProfile), [accountProfile]);
  const currentPlan = readString(
    (billingSummary?.subscription && typeof billingSummary.subscription === 'object'
      ? (billingSummary.subscription as Record<string, unknown>).label
      : null)
    ?? (accountProfile?.current_workspace_entitlements && typeof accountProfile.current_workspace_entitlements === 'object'
      ? (accountProfile.current_workspace_entitlements as Record<string, unknown>).label
      : null),
    bootstrap.entitlements.label,
  );
  const activeModelPath = useMemo(
    () => deriveActiveModelPath(providerCatalog, providerProfiles, hostedCredits),
    [hostedCredits, providerCatalog, providerProfiles],
  );
  const signInMethodSummary = authMethods.length > 0
    ? authMethods.map((method) => formatAuthMethodLabel(method)).join(' · ')
    : 'Email and password';

  async function handleLogout() {
    setLogoutPending(true);
    try {
      await logout();
      window.location.replace('/login');
    } catch {
      setAccountDetailsError('Logout could not finish. Try again when ready.');
      setLogoutPending(false);
    }
  }

  async function saveTransparencySettingsPatch(
    key: keyof TransparencySettingsPayload,
    value: TransparencySettingsPayload[keyof TransparencySettingsPayload],
  ) {
    const previousSettings = transparencySettings;
    const nextSettings = {
      ...transparencySettings,
      [key]: value,
    };
    setTransparencySettings(nextSettings);
    setSavingTransparencyKey(String(key));
    setTransparencyStatus(null);
    setTransparencyError(null);
    try {
      const payload = await services.client.updateWorkspaceTransparencySettings({
        [key]: value,
      });
      setTransparencySettings(normalizeTransparencySettings(payload));
      setTransparencyStatus('Transparency settings saved.');
    } catch (error) {
      setTransparencySettings(previousSettings);
      setTransparencyError(error instanceof Error ? error.message : 'Transparency settings could not be saved.');
    } finally {
      setSavingTransparencyKey(null);
    }
  }

  return (
    <main data-workstation-surface="settings" className="app-settings-page">
      <div className="settings-workbench settings-workbench--content-only">
        <section className="settings-content">
          <header className="app-settings-main__header">
            <h1 className="app-settings-main__title">{activeSection.title}</h1>
            <p className="app-settings-main__subtitle">{activeSection.description}</p>
          </header>

          {selectedSection === 'account' ? (
            <div className="settings-section-stack">
              <section className="settings-account-card" aria-label="Account profile">
                <div className="settings-account-card__avatar" aria-hidden="true">
                  {accountInitial}
                </div>
                <div className="settings-account-card__body">
                  <p className="settings-account-card__eyebrow">Current account</p>
                  <h2 className="settings-account-card__name">{accountDisplayName}</h2>
                  <p className="settings-account-card__email">{accountEmail}</p>
                  <p className="settings-account-card__summary">
                    Your Empyralis account owns this workspace, its connected apps, and the sign-in methods below.
                  </p>
                </div>
                <div className="settings-account-card__actions">
                  <AppButton type="button" tone="secondary" onClick={() => selectSettingsSection('billing')}>
                    View usage
                  </AppButton>
                  <AppButton type="button" tone="ghost" disabled={logoutPending} onClick={() => void handleLogout()}>
                    {logoutPending ? 'Signing out…' : 'Log out'}
                  </AppButton>
                </div>
              </section>
              {isLoadingAccountDetails ? (
                <div className="app-stack-3">
                  <SkeletonBlock height="5rem" />
                  <SkeletonBlock height="4rem" />
                  <SkeletonBlock height="7rem" />
                </div>
              ) : null}
              {accountDetailsError ? <DataPaneError error={accountDetailsError} onRetry={() => refreshAccountDetails()} label="Account details" /> : null}
              {isLoadingAccountDetails ? null : (
                <>
              <AppSurfaceStatGrid className="settings-account-stat-grid">
                <AppSurfaceStat
                  label="Plan"
                  value={currentPlan}
                  hint="The active workspace plan that governs credits and launch limits."
                />
                <AppSurfaceStat
                  label="Credits status"
                  value={hostedCredits.allowed && hostedCredits.monthlyCreditCap > 0
                    ? `${formatCredits(hostedCredits.monthlyCreditsRemaining)} / ${formatCredits(hostedCredits.monthlyCreditCap)}`
                    : 'Not active'}
                  hint={hostedCredits.allowed ? hostedCredits.message : 'Add credits or connect your own AI account.'}
                />
                <AppSurfaceStat
                  label="Active AI path"
                  value={activeModelPath.value}
                  hint={activeModelPath.hint}
                />
                <AppSurfaceStat
                  label="Sign-in methods"
                  value={String(authMethods.length || 1)}
                  hint={signInMethodSummary}
                />
              </AppSurfaceStatGrid>
              <FormGrid>
                <FormReadout label="Display name" value={bootstrap.account.displayName || 'Not set yet'} />
                <FormReadout label="Email" value={bootstrap.account.email} />
                <FormReadout label="Plan" value={currentPlan} />
                <FormReadout label="Default experience" value="Sage" />
                <FormReadout label="Credits status" value={hostedCredits.allowed ? hostedCredits.message : 'Credits not active'} />
                <FormReadout label="Active AI path" value={activeModelPath.value} />
              </FormGrid>
              <AppSurfaceCard
                title="Sign-in methods"
                description="Account access stays separate from AI model providers and connected apps."
              >
                <AppSurfaceList>
                  {authMethods.length > 0 ? authMethods.map((method) => (
                    <AppSurfaceListItem
                      key={`${readString(method.provider)}:${readString(method.method_type)}:${readString(method.subject)}`}
                      title={formatAuthMethodLabel(method)}
                      subtitle={formatAuthMethodDetail(method)}
                      description={method.is_primary === true
                        ? 'This is the main account access path on this browser account.'
                        : 'Available as an additional way to access this Empyralis account.'}
                    />
                  )) : (
                    <AppSurfaceListItem
                      title="Email and password"
                      subtitle="Primary sign-in"
                      description="This account currently falls back to the Empyralis email sign-in path."
                    />
                  )}
                  <AppSurfaceListItem
                    title="Google sign-in"
                    subtitle={authProviders.google?.enabled === true ? 'Available for this workspace' : 'Not enabled for this workspace'}
                    description="Google is the fastest sign-in path when this workspace has it enabled."
                  />
                </AppSurfaceList>
              </AppSurfaceCard>
                </>
              )}
            </div>
          ) : null}

          {selectedSection === 'appearance' ? (
            <div className="settings-section-stack">
              <AppSurfaceCard
                title="Theme"
                description="Choose how Empyralis renders on this browser."
              >
                <div className="settings-theme-toggle-row">
                  <button
                    type="button"
                    className="settings-theme-toggle"
                    aria-label={`Switch to ${nextAppearanceTheme} mode`}
                    onClick={() => accountShellActions.setGlobalTheme(nextAppearanceTheme)}
                  >
                    <AppearanceThemeIcon size={16} aria-hidden="true" />
                    <span>Switch to {nextAppearanceTheme}</span>
                  </button>
                </div>
                <div className="settings-choice-grid">
                  {[
                    { id: 'system' as const, label: 'System', detail: 'Follow this device.' },
                    { id: 'light' as const, label: 'Light', detail: 'Bright surfaces and high contrast.' },
                    { id: 'dark' as const, label: 'Dark', detail: 'Low-light interface.' },
                  ].map((option) => (
                    <button
                      key={option.id}
                      type="button"
                      className={joinClassNames(
                        'settings-choice-card',
                        currentThemePreference === option.id && 'settings-choice-card--selected',
                      )}
                      aria-pressed={currentThemePreference === option.id}
                      onClick={() => accountShellActions.setGlobalTheme(option.id)}
                    >
                      <strong>{option.label}</strong>
                      <span>{option.detail}</span>
                    </button>
                  ))}
                </div>
              </AppSurfaceCard>
              <AppSurfaceCard
                title="Interface"
                description="Sage keeps the daily surface compact. Developer details expand only when a row or tool asks for them."
              >
                <AppSurfaceList>
                  <AppSurfaceListItem
                    title="Chat first"
                    subtitle="Default"
                    description="The transcript and composer stay central. Chat, History, Connections, Memory, Tasks, and Library stay in the top navigation."
                  />
                  <AppSurfaceListItem
                    title="Progressive details"
                    subtitle="Always available"
                    description="Technical fields remain in expandable rows instead of a separate advanced product mode."
                  />
                </AppSurfaceList>
              </AppSurfaceCard>
            </div>
          ) : null}

          {selectedSection === 'usage' ? <WorkstationPlatformAnalyticsPane /> : null}

          {selectedSection === 'billing' ? (
            <div className="settings-section-stack">
              <AppSurfaceCard
                title="Workspace limits"
                description="Paid plans are not configured yet. This section will show launch limits once they are decided."
              >
                <AppSurfaceList>
                  <AppSurfaceListItem
                    title="Current mode"
                    subtitle="Workspace-first"
                    description="Apps, Sage, Studio, and Agent Computer access stay governed by workspace policy."
                  />
                  <AppSurfaceListItem
                    title="Usage policy"
                    subtitle="Not monetized"
                    description="No paid-plan claims are shown until pricing and billing are ready."
                  />
                  <AppSurfaceListItem
                    title="Next step"
                    subtitle="Define launch limits"
                    description="When limits are approved, this surface can show current usage, caps, and owner controls."
                  />
                </AppSurfaceList>
              </AppSurfaceCard>
            </div>
          ) : null}

          {selectedSection === 'privacy' ? (
            <div className="settings-section-stack">
              <div className="settings-detail-grid">
                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">Approval-gated actions</strong>
                  </div>
                  <p className="settings-detail-card__body">
                    Empyralis pauses for review before high-risk hardware actions or external effects.
                  </p>
                </article>
                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">Explicit memory</strong>
                  </div>
                  <p className="settings-detail-card__body">
                    Memory is saved as structured workspace records with sensitivity classes, not hidden chat transcript scraping.
                  </p>
                </article>
                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">This Device boundary</strong>
                  </div>
                  <p className="settings-detail-card__body">
                    A trusted online computer is required before Empyralis routes work onto this hardware.
                  </p>
                </article>
                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">Cloud Computer boundary</strong>
                  </div>
                  <p className="settings-detail-card__body">
                    Hosted computer work is separate from personal-device access and must be explicitly enabled, metered, and audited.
                  </p>
                </article>
                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">AI model credentials</strong>
                  </div>
                  <p className="settings-detail-card__body">
                    User-provided AI credentials stay private to this workspace, and model choice should only change the reasoning model.
                  </p>
                </article>
                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">External user deletion</strong>
                  </div>
                  <p className="settings-detail-card__body">
                    Build assistants support privacy/delete requests for external channel users, including conversation data and memory.
                  </p>
                </article>
              </div>
            </div>
          ) : null}
          {selectedSection === 'transparency' ? (
            <section className="app-settings-main__body">
              <div className="settings-cards">
                {transparencyStatus ? <AppNotice tone="success">{transparencyStatus}</AppNotice> : null}
                {transparencyError ? <AppNotice tone="warning">{transparencyError}</AppNotice> : null}
                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">Visibility mode</strong>
                  </div>
                  <p className="settings-detail-card__body" style={{ fontSize: 13, color: 'var(--color-muted, #6b7280)', marginBottom: 12 }}>
                    Controls how much activity detail appears during agent turns.
                    Customer-facing agents are limited to Quiet or Basic.
                  </p>
                  <FormGrid>
                    {[
                      { key: 'sage_mode' as const, label: 'Sage (chat)' },
                      { key: 'studio_test_mode' as const, label: 'Studio test' },
                      { key: 'default_mode' as const, label: 'Default' },
                    ].map(({ key, label }) => (
                      <TransparencyModeSelect
                        key={key}
                        label={label}
                        value={transparencySettings[key]}
                        disabled={savingTransparencyKey !== null}
                        onChange={(nextMode) => {
                          void saveTransparencySettingsPatch(key, nextMode);
                        }}
                      />
                    ))}
                  </FormGrid>
                </article>

                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">Customer-facing mode</strong>
                  </div>
                  <p className="settings-detail-card__body" style={{ fontSize: 13, color: 'var(--color-muted, #6b7280)', marginBottom: 12 }}>
                    Business Agents can only use Quiet or Basic mode.
                    They never see internal memory, policy details, or tool arguments.
                  </p>
                  <select
                    value={transparencySettings.customer_mode}
                    disabled={savingTransparencyKey !== null}
                    onChange={(event) => {
                      const nextMode = event.target.value === 'off' ? 'off' : 'minimal';
                      void saveTransparencySettingsPatch('customer_mode', nextMode);
                    }}
                    style={{
                      padding: '4px 8px', fontSize: 13, borderRadius: 6,
                      border: '1px solid var(--color-border, #e5e7eb)',
                      background: 'var(--color-bg, #fff)', color: 'var(--color-fg, #111)',
                    }}
                  >
                    <option value="off">Quiet — final answer only</option>
                    <option value="minimal">Basic — simple status indicators</option>
                  </select>
                </article>

                <article className="settings-detail-card">
                  <div className="settings-detail-card__header">
                    <strong className="settings-detail-card__title">What to show</strong>
                  </div>
                  <p className="settings-detail-card__body" style={{ fontSize: 13, color: 'var(--color-muted, #6b7280)', marginBottom: 12 }}>
                    Fine-grained controls for what appears in activity timelines.
                  </p>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {[
                      { key: 'show_trace_ids' as const, label: 'Show trace IDs' },
                      { key: 'show_tool_names' as const, label: 'Show tool names' },
                      { key: 'show_memory_usage' as const, label: 'Show memory category usage' },
                      { key: 'show_policy_blocks' as const, label: 'Show policy blocks' },
                      { key: 'show_sources' as const, label: 'Show sources' },
                    ].map(({ key, label }) => (
                      <label key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={Boolean(transparencySettings[key])}
                          disabled={savingTransparencyKey !== null}
                          onChange={(event) => {
                            void saveTransparencySettingsPatch(key, event.target.checked);
                          }}
                        />
                        <span>{label}</span>
                      </label>
                    ))}
                  </div>
                </article>

                <p style={{ fontSize: 11, color: 'var(--color-muted-faint, #9ca3af)', marginTop: 12 }}>
                  Transparency settings are stored per-workspace. Customer-facing agents stay capped to Quiet or Basic.
                </p>
              </div>
            </section>
          ) : null}
        </section>
      </div>
    </main>
  );
}
