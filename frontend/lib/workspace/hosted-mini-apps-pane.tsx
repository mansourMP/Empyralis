'use client';

import Link from 'next/link';
import type { FormEvent } from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  BookOpen,
  Globe2,
  LockKeyhole,
  PackageOpen,
  Plus,
  Save,
  Sparkles,
  X,
} from 'lucide-react';

import { buildCookieAuthHeaders } from '@/lib/auth/csrf';

import styles from './hosted-mini-apps.module.css';

type AppCard = {
  feed_id?: string | null;
  public_app_id?: string | null;
  app_id: string;
  name: string;
  description?: string | null;
  icon_url?: string | null;
  creator?: {
    label?: string | null;
    byline?: string | null;
  } | null;
  blueprint?: {
    version?: number | null;
    revision?: number | null;
    status?: string | null;
    visibility?: string | null;
    created_by?: string | null;
    source?: string | null;
    source_blueprint_id?: string | null;
    published_at?: string | null;
    history_count?: number | null;
  } | null;
  provenance?: {
    kind?: string | null;
    label?: string | null;
    source_blueprint_id?: string | null;
  } | null;
  permission_summary?: string[];
  visibility?: 'private' | 'public' | string;
  visibility_label?: string | null;
  source?: {
    kind?: string | null;
    url?: string | null;
    label?: string | null;
  } | null;
  open?: {
    kind?: 'internal' | 'external' | string;
    url?: string | null;
  } | null;
  details_url?: string | null;
  installed?: boolean;
  install_status?: string | null;
  clone_available?: boolean;
  settings?: {
    ai_enabled?: boolean;
    records_enabled?: boolean;
    sage_enabled?: boolean;
    monthly_credit_limit?: number;
    per_invocation_credit_limit?: number;
  } | null;
};

type AppsPayload = {
  items?: AppCard[];
};

type OfficialMiniApp = {
  app_id: 'flashcards' | 'calorie_tracking';
  label: string;
  icon: LucideIcon;
  accent: 'blue' | 'green';
};

const OFFICIAL_MINI_APPS: readonly OfficialMiniApp[] = [
  {
    app_id: 'flashcards',
    label: 'Flashcards',
    icon: BookOpen,
    accent: 'blue',
  },
  {
    app_id: 'calorie_tracking',
    label: 'Calorie Tracker',
    icon: Activity,
    accent: 'green',
  },
];

async function fetchApps(workspaceId: string): Promise<AppCard[]> {
  const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/apps`, {
    credentials: 'include',
    headers: buildCookieAuthHeaders('GET', { accept: 'application/json' }),
  });
  if (!response.ok) {
    throw new Error(`Apps listing failed with status ${response.status}.`);
  }
  const payload = (await response.json()) as AppsPayload;
  return Array.isArray(payload.items) ? payload.items : [];
}

async function publishApp(
  workspaceId: string,
  payload: { name: string; description?: string; source_url?: string; visibility: 'private' | 'public' },
): Promise<AppCard> {
  const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/apps`, {
    method: 'POST',
    credentials: 'include',
    headers: buildCookieAuthHeaders('POST', {
      accept: 'application/json',
      'content-type': 'application/json',
    }),
    body: JSON.stringify(payload),
  });
  const responsePayload = (await response.json().catch(() => null)) as { detail?: unknown } | AppCard | null;
  if (!response.ok) {
    const detail = responsePayload && 'detail' in responsePayload ? responsePayload.detail : null;
    throw new Error(typeof detail === 'string' ? detail : `Publish failed with status ${response.status}.`);
  }
  if (!responsePayload || !('app_id' in responsePayload)) {
    throw new Error('Publish returned an invalid app.');
  }
  return responsePayload as AppCard;
}

async function updateAppSettings(
  workspaceId: string,
  appId: string,
  payload: {
    name: string;
    description: string;
    source_url: string;
    visibility: 'private' | 'public';
    ai_enabled: boolean;
    records_enabled: boolean;
    sage_enabled: boolean;
    monthly_credit_limit: number;
    per_invocation_credit_limit: number;
  },
): Promise<AppCard> {
  const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/apps/${encodeURIComponent(appId)}/settings`, {
    method: 'PUT',
    credentials: 'include',
    headers: buildCookieAuthHeaders('PUT', {
      accept: 'application/json',
      'content-type': 'application/json',
    }),
    body: JSON.stringify(payload),
  });
  const responsePayload = (await response.json().catch(() => null)) as { detail?: unknown } | AppCard | null;
  if (!response.ok) {
    const detail = responsePayload && 'detail' in responsePayload ? responsePayload.detail : null;
    throw new Error(typeof detail === 'string' ? detail : `Settings failed with status ${response.status}.`);
  }
  if (!responsePayload || !('app_id' in responsePayload)) {
    throw new Error('Settings returned an invalid app.');
  }
  return responsePayload as AppCard;
}

async function requestAppPublication(workspaceId: string, appId: string): Promise<AppCard> {
  const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/apps/${encodeURIComponent(appId)}/publish-request`, {
    method: 'POST',
    credentials: 'include',
    headers: buildCookieAuthHeaders('POST', {
      accept: 'application/json',
      'content-type': 'application/json',
    }),
    body: JSON.stringify({ note: 'Requested from Applications settings.' }),
  });
  const responsePayload = (await response.json().catch(() => null)) as { detail?: unknown } | AppCard | null;
  if (!response.ok) {
    const detail = responsePayload && 'detail' in responsePayload ? responsePayload.detail : null;
    throw new Error(typeof detail === 'string' ? detail : `Publish request failed with status ${response.status}.`);
  }
  if (!responsePayload || !('app_id' in responsePayload)) {
    throw new Error('Publish request returned an invalid app.');
  }
  return responsePayload as AppCard;
}

async function approveAppPublication(workspaceId: string, appId: string): Promise<AppCard> {
  const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/apps/${encodeURIComponent(appId)}/approve-publication`, {
    method: 'POST',
    credentials: 'include',
    headers: buildCookieAuthHeaders('POST', {
      accept: 'application/json',
      'content-type': 'application/json',
    }),
    body: JSON.stringify({ note: 'Approved from Applications settings.' }),
  });
  const responsePayload = (await response.json().catch(() => null)) as { detail?: unknown } | AppCard | null;
  if (!response.ok) {
    const detail = responsePayload && 'detail' in responsePayload ? responsePayload.detail : null;
    if (response.status === 403) {
      throw new Error('Only workspace owners can approve publication.');
    }
    throw new Error(typeof detail === 'string' ? detail : `Approval failed with status ${response.status}.`);
  }
  if (!responsePayload || !('app_id' in responsePayload)) {
    throw new Error('Approval returned an invalid app.');
  }
  return responsePayload as AppCard;
}

function officialMiniAppFor(appId: string): OfficialMiniApp | null {
  return OFFICIAL_MINI_APPS.find((app) => app.app_id === appId) || null;
}

function appInitials(label: string): string {
  const parts = label.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) {
    return 'A';
  }
  return parts
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('');
}

function urlTargetsLocalDevelopment(url: URL): boolean {
  const host = url.hostname.toLowerCase();
  return host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]' || host.endsWith('.local');
}

function blueprintStatusLabel(item: AppCard): string {
  const status = String(item.blueprint?.status || '').replace(/[_-]+/g, ' ').trim();
  if (status) {
    return status.charAt(0).toUpperCase() + status.slice(1);
  }
  return item.visibility === 'public' ? 'Public blueprint' : 'Private blueprint';
}

function blueprintActionLabel(item: AppCard): string {
  const status = String(item.blueprint?.status || '').trim();
  if (status === 'public_discoverable') {
    return 'Published to Discovery';
  }
  if (status === 'publish_requested') {
    return 'Approve and publish';
  }
  return 'Request review';
}

function publicationStatusText(item: AppCard): string {
  const status = String(item.blueprint?.status || '').trim();
  if (status === 'public_discoverable') {
    return 'Published to Discovery';
  }
  if (status === 'publish_requested') {
    return 'Awaiting owner review';
  }
  if (status === 'draft') {
    return 'Draft blueprint';
  }
  return 'Private to workspace';
}

function isReviewApp(item: AppCard): boolean {
  const status = String(item.blueprint?.status || '').trim();
  return item.install_status === 'pending' || status === 'draft' || status === 'publish_requested';
}

export function HostedMiniAppsPane({ workspaceId }: { workspaceId: string }) {
  const searchParams = useSearchParams();
  const [items, setItems] = useState<AppCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(searchParams.get('tab') === 'my_apps');
  const [appName, setAppName] = useState('');
  const [appDescription, setAppDescription] = useState('');
  const [appUrl, setAppUrl] = useState('');
  const [visibility, setVisibility] = useState<'private' | 'public'>('private');
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [settingsAppId, setSettingsAppId] = useState<string | null>(null);
  const [settingsName, setSettingsName] = useState('');
  const [settingsDescription, setSettingsDescription] = useState('');
  const [settingsUrl, setSettingsUrl] = useState('');
  const [settingsVisibility, setSettingsVisibility] = useState<'private' | 'public'>('private');
  const [settingsAiEnabled, setSettingsAiEnabled] = useState(false);
  const [settingsRecordsEnabled, setSettingsRecordsEnabled] = useState(true);
  const [settingsSageEnabled, setSettingsSageEnabled] = useState(false);
  const [settingsMonthlyLimit, setSettingsMonthlyLimit] = useState('500');
  const [settingsInvocationLimit, setSettingsInvocationLimit] = useState('50');
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [publicationSaving, setPublicationSaving] = useState(false);

  const refreshApps = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextItems = await fetchApps(workspaceId);
      setItems(nextItems);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load apps.');
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchApps(workspaceId)
      .then((nextItems) => {
        if (!cancelled) {
          setItems(nextItems);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load apps.');
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  useEffect(() => {
    if (searchParams.get('tab') === 'my_apps' || searchParams.get('applicationTab') === 'my_apps') {
      setCreateOpen(true);
    }
  }, [searchParams]);

  const handleCreateSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setFormError(null);
      setFormSuccess(null);
      const name = appName.trim();
      const description = appDescription.trim();
      const rawUrl = appUrl.trim();
      if (!name) {
        setFormError('Enter an app name.');
        return;
      }
      if (!description) {
        setFormError('Enter a short description.');
        return;
      }
      let parsedUrl: URL | null = null;
      if (rawUrl) {
        try {
          parsedUrl = new URL(rawUrl);
        } catch {
          setFormError('Enter a valid app URL or leave it blank.');
          return;
        }
        if (parsedUrl.protocol !== 'https:' && !(process.env.NODE_ENV !== 'production' && urlTargetsLocalDevelopment(parsedUrl))) {
          setFormError('Apps require an HTTPS URL. Localhost is allowed only in development.');
          return;
        }
      }
      setSaving(true);
      try {
        const created = await publishApp(workspaceId, {
          name,
          description,
          source_url: parsedUrl?.toString(),
          visibility,
        });
        setItems((current) => [created, ...current.filter((item) => item.app_id !== created.app_id)]);
        setAppName('');
        setAppDescription('');
        setAppUrl('');
        setVisibility('private');
        setCreateOpen(false);
        setFormSuccess(created.blueprint?.status === 'publish_requested' ? 'App created. Discovery review requested.' : 'App created.');
      } catch (err) {
        setFormError(err instanceof Error ? err.message : 'Could not publish this app.');
      } finally {
        setSaving(false);
      }
    },
    [appDescription, appName, appUrl, visibility, workspaceId],
  );

  const openSettings = useCallback((item: AppCard) => {
    setSettingsAppId(item.app_id);
    setSettingsName(item.name || item.app_id);
    setSettingsDescription(item.description || '');
    setSettingsUrl(item.source?.url || '');
    setSettingsVisibility(item.visibility === 'public' ? 'public' : 'private');
    setSettingsAiEnabled(Boolean(item.settings?.ai_enabled));
    setSettingsRecordsEnabled(item.settings?.records_enabled !== false);
    setSettingsSageEnabled(Boolean(item.settings?.sage_enabled));
    setSettingsMonthlyLimit(String(item.settings?.monthly_credit_limit ?? 500));
    setSettingsInvocationLimit(String(item.settings?.per_invocation_credit_limit ?? 50));
    setSettingsError(null);
  }, []);

  const closeSettings = useCallback(() => {
    setSettingsAppId(null);
    setSettingsError(null);
  }, []);

  const replaceAppCard = useCallback((updated: AppCard) => {
    setItems((current) => current.map((item) => (item.app_id === updated.app_id ? updated : item)));
  }, []);

  const handleSettingsSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!settingsAppId) {
        return;
      }
      const name = settingsName.trim();
      const description = settingsDescription.trim();
      const rawUrl = settingsUrl.trim();
      if (!name) {
        setSettingsError('Enter an app name.');
        return;
      }
      if (!description) {
        setSettingsError('Enter a short description.');
        return;
      }
      let parsedUrl: URL | null = null;
      if (rawUrl) {
        try {
          parsedUrl = new URL(rawUrl);
        } catch {
          setSettingsError('Enter a valid app URL or leave it blank.');
          return;
        }
        if (parsedUrl.protocol !== 'https:' && !(process.env.NODE_ENV !== 'production' && urlTargetsLocalDevelopment(parsedUrl))) {
          setSettingsError('Apps require an HTTPS URL. Localhost is allowed only in development.');
          return;
        }
      }
      const monthlyLimit = Number(settingsMonthlyLimit);
      const invocationLimit = Number(settingsInvocationLimit);
      if (!Number.isInteger(monthlyLimit) || monthlyLimit < 0 || !Number.isInteger(invocationLimit) || invocationLimit < 0) {
        setSettingsError('Credit limits must be zero or higher.');
        return;
      }
      setSettingsSaving(true);
      setSettingsError(null);
      try {
        const updated = await updateAppSettings(workspaceId, settingsAppId, {
          name,
          description,
          source_url: parsedUrl?.toString() || '',
          visibility: settingsVisibility,
          ai_enabled: settingsAiEnabled,
          records_enabled: settingsRecordsEnabled,
          sage_enabled: settingsSageEnabled,
          monthly_credit_limit: monthlyLimit,
          per_invocation_credit_limit: invocationLimit,
        });
        replaceAppCard(updated);
        setSettingsAppId(null);
      } catch (err) {
        setSettingsError(err instanceof Error ? err.message : 'Could not save settings.');
      } finally {
        setSettingsSaving(false);
      }
    },
    [
      settingsAiEnabled,
      settingsAppId,
      settingsDescription,
      settingsInvocationLimit,
      settingsMonthlyLimit,
      settingsName,
      settingsRecordsEnabled,
      settingsSageEnabled,
      settingsUrl,
      settingsVisibility,
      workspaceId,
      replaceAppCard,
    ],
  );

  const renderedItems = useMemo(() => items.filter((item) => item.installed !== false), [items]);
  const reviewItems = useMemo(() => renderedItems.filter(isReviewApp), [renderedItems]);
  const launcherItems = useMemo(() => renderedItems.filter((item) => !isReviewApp(item)), [renderedItems]);
  const settingsApp = useMemo(
    () => renderedItems.find((item) => item.app_id === settingsAppId && item.installed !== false) || null,
    [renderedItems, settingsAppId],
  );

  const handlePublicationAction = useCallback(async () => {
    if (!settingsApp) {
      return;
    }
    const status = String(settingsApp.blueprint?.status || '').trim();
    if (status === 'public_discoverable') {
      return;
    }
    setPublicationSaving(true);
    setSettingsError(null);
    try {
      const updated = status === 'publish_requested'
        ? await approveAppPublication(workspaceId, settingsApp.app_id)
        : await requestAppPublication(workspaceId, settingsApp.app_id);
      replaceAppCard(updated);
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : 'Could not update Discovery status.');
    } finally {
      setPublicationSaving(false);
    }
  }, [replaceAppCard, settingsApp, workspaceId]);

  const renderAppTile = useCallback((item: AppCard) => {
    const name = item.name || item.app_id;
    const officialApp = officialMiniAppFor(item.app_id);
    const Icon = officialApp?.icon || PackageOpen;
    const accent = officialApp?.accent || 'neutral';
    const openUrl = item.open?.url || `/w/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(item.app_id)}`;
    const launcherContent = (
      <>
        <span className={`${styles.appIcon} ${styles[`appIcon_${accent}`]}`}>
          {item.icon_url ? (
            <img src={item.icon_url} alt="" />
          ) : officialApp ? (
            <Icon aria-hidden="true" />
          ) : (
            <span>{appInitials(name)}</span>
          )}
        </span>
        <span className={styles.appName}>{name}</span>
        {item.blueprint?.status && item.blueprint.status !== 'workspace_private' ? (
          <span className={styles.appStatus}>{blueprintStatusLabel(item)}</span>
        ) : null}
      </>
    );
    const openLabel = `Open ${name}`;
    return (
      <article
        key={item.feed_id || item.app_id}
        className={styles.appTile}
        onContextMenu={(event) => {
          event.preventDefault();
          openSettings(item);
        }}
      >
        {item.open?.kind === 'external' ? (
          <a className={styles.appLauncher} href={openUrl} target="_blank" rel="noreferrer" aria-label={openLabel}>
            {launcherContent}
          </a>
        ) : (
          <Link className={styles.appLauncher} href={openUrl} aria-label={openLabel}>
            {launcherContent}
          </Link>
        )}
      </article>
    );
  }, [openSettings, workspaceId]);

  return (
    <main className={styles.shell}>
      <section className={styles.appsFeedShell} aria-label="Apps">
        <header className={styles.appsFeedHeader}>
          <button
            type="button"
            className={styles.launcherCreateButton}
            onClick={() => {
              setCreateOpen((current) => !current);
              setFormError(null);
              setFormSuccess(null);
            }}
            aria-label="Create app"
          >
            <Plus aria-hidden="true" />
          </button>
        </header>

        {createOpen ? (
          <section className={styles.createAppPanel} aria-label="Create an app">
            <div className={styles.createAppIntro}>
              <div className={styles.emptyIcon}>
                <Sparkles aria-hidden="true" />
              </div>
              <div className={styles.emptyCopyBlock}>
                <p className={styles.emptyTitle}>Publish an app blueprint</p>
                <p className={styles.copy}>Create a private workspace app now, then publish it to Discovery when it is ready to clone.</p>
              </div>
            </div>
            <form className={styles.privateAppForm} onSubmit={handleCreateSubmit}>
              <div className={styles.formGrid}>
                <label className={styles.fieldLabel}>
                  Name
                  <input
                    className={styles.textInput}
                    value={appName}
                    onChange={(event) => {
                      setAppName(event.target.value);
                      setFormError(null);
                      setFormSuccess(null);
                    }}
                    placeholder="Budget Tracker"
                    autoComplete="off"
                  />
                </label>
                <label className={styles.fieldLabel}>
                  App source
                  <input
                    className={styles.textInput}
                    value={appUrl}
                    onChange={(event) => {
                      setAppUrl(event.target.value);
                      setFormError(null);
                      setFormSuccess(null);
                    }}
                    placeholder="Optional website URL"
                    autoComplete="off"
                  />
                </label>
              </div>
              <label className={styles.fieldLabel}>
                Description
                <textarea
                  className={styles.textArea}
                  value={appDescription}
                  onChange={(event) => {
                    setAppDescription(event.target.value);
                    setFormError(null);
                    setFormSuccess(null);
                  }}
                  placeholder="Track daily expenses and monthly spending patterns."
                  rows={3}
                />
              </label>
              <fieldset className={styles.visibilityField}>
                <legend>Visibility</legend>
                <div className={styles.visibilityToggle}>
                  <button
                    type="button"
                    className={visibility === 'private' ? styles.visibilityOptionActive : styles.visibilityOption}
                    onClick={() => setVisibility('private')}
                  >
                    <LockKeyhole aria-hidden="true" />
                    Private
                  </button>
                  <button
                    type="button"
                    className={visibility === 'public' ? styles.visibilityOptionActive : styles.visibilityOption}
                    onClick={() => setVisibility('public')}
                  >
                    <Globe2 aria-hidden="true" />
                    Discovery
                  </button>
                </div>
              </fieldset>
              {formError ? <p className={styles.formError}>{formError}</p> : null}
              {formSuccess ? <p className={styles.formSuccess}>{formSuccess}</p> : null}
              <div className={styles.actionRow}>
                <button type="submit" className={styles.primaryAction} disabled={saving}>
                  <Plus aria-hidden="true" />
                  {saving ? 'Publishing...' : 'Publish App'}
                </button>
                <button type="button" className={styles.secondaryAction} onClick={() => setCreateOpen(false)}>
                  Cancel
                </button>
              </div>
            </form>
          </section>
        ) : null}

        {settingsApp ? (
          <section className={styles.settingsPanel} aria-label="App settings">
            <div className={styles.settingsHeader}>
              <div>
                <p className={styles.commandKicker}>Settings</p>
                <h2 className={styles.settingsTitle}>{settingsApp.name || settingsApp.app_id}</h2>
              </div>
              <button type="button" className={styles.iconButton} onClick={closeSettings} aria-label="Close settings">
                <X aria-hidden="true" />
              </button>
            </div>
            <div className={styles.blueprintSummary}>
              <div>
                <p className={styles.factLabel}>Blueprint</p>
                <p className={styles.factValue}>
                  {blueprintStatusLabel(settingsApp)}
                  {settingsApp.blueprint?.revision ? ` · revision ${settingsApp.blueprint.revision}` : ''}
                </p>
              </div>
              <div>
                <p className={styles.factLabel}>Provenance</p>
                <p className={styles.factValue}>{settingsApp.provenance?.label || 'Created in this workspace'}</p>
              </div>
              <div>
                <p className={styles.factLabel}>Permissions</p>
                <p className={styles.factValue}>{(settingsApp.permission_summary || ['Review permissions before publishing.']).join(' ')}</p>
              </div>
            </div>
            <div className={styles.publicationActions}>
              <span className={styles.statusPill}>{publicationStatusText(settingsApp)}</span>
              <button
                type="button"
                className={styles.secondaryAction}
                disabled={publicationSaving || settingsApp.blueprint?.status === 'public_discoverable'}
                onClick={() => { void handlePublicationAction(); }}
              >
                {publicationSaving ? 'Updating...' : blueprintActionLabel(settingsApp)}
              </button>
            </div>
            <form className={styles.privateAppForm} onSubmit={handleSettingsSubmit}>
              <label className={styles.fieldLabel}>
                Name
                <input
                  className={styles.textInput}
                  value={settingsName}
                  onChange={(event) => {
                    setSettingsName(event.target.value);
                    setSettingsError(null);
                  }}
                  autoComplete="off"
                />
              </label>
              <label className={styles.fieldLabel}>
                Description
                <textarea
                  className={styles.textArea}
                  value={settingsDescription}
                  onChange={(event) => {
                    setSettingsDescription(event.target.value);
                    setSettingsError(null);
                  }}
                  rows={3}
                />
              </label>
              <fieldset className={styles.visibilityField}>
                <legend>Visibility</legend>
                <div className={styles.visibilityToggle}>
                  <button
                    type="button"
                    className={settingsVisibility === 'private' ? styles.visibilityOptionActive : styles.visibilityOption}
                    onClick={() => setSettingsVisibility('private')}
                  >
                    <LockKeyhole aria-hidden="true" />
                    Private
                  </button>
                  <button
                    type="button"
                    className={settingsVisibility === 'public' ? styles.visibilityOptionActive : styles.visibilityOption}
                    onClick={() => setSettingsVisibility('public')}
                  >
                    <Globe2 aria-hidden="true" />
                    Discovery
                  </button>
                </div>
              </fieldset>
              {settingsError ? <p className={styles.formError}>{settingsError}</p> : null}
              <div className={styles.actionRow}>
                <button type="submit" className={styles.primaryAction} disabled={settingsSaving}>
                  <Save aria-hidden="true" />
                  {settingsSaving ? 'Saving...' : 'Save'}
                </button>
                <button type="button" className={styles.secondaryAction} onClick={closeSettings}>
                  Cancel
                </button>
              </div>
            </form>
          </section>
        ) : null}

        {loading ? <div className={styles.emptyState}>Loading apps...</div> : null}
        {error ? (
          <div className={`${styles.emptyState} ${styles.error}`}>
            <p className={styles.emptyTitle}>Apps could not refresh.</p>
            <button type="button" className={styles.secondaryAction} onClick={() => { void refreshApps(); }}>
              Try again
            </button>
          </div>
        ) : null}
        {!loading && !error && !renderedItems.length ? (
          <section className={styles.installedEmpty} aria-label="Apps">
            <div className={styles.commandPanel}>
              <div className={styles.commandIcon}>
                <PackageOpen aria-hidden="true" />
              </div>
              <div className={styles.commandCopy}>
                <p className={styles.commandKicker}>Apps</p>
                <h2 className={styles.commandTitle}>No apps yet</h2>
                <p className={styles.commandText}>Publish a blank app or clone one from Discovery.</p>
              </div>
              <button type="button" className={styles.primaryAction} onClick={() => setCreateOpen(true)}>
                <Plus aria-hidden="true" />
                Create
              </button>
            </div>
          </section>
        ) : null}

        {!loading && !error && reviewItems.length ? (
          <section className={styles.launcherSection} aria-label="Draft and review apps">
            <div className={styles.launcherSectionHeader}>
              <div>
                <p className={styles.commandKicker}>Review</p>
                <h2 className={styles.sectionTitle}>Drafts and review</h2>
              </div>
              <span>{reviewItems.length}</span>
            </div>
            <div className={styles.grid}>
              {reviewItems.map(renderAppTile)}
            </div>
          </section>
        ) : null}

        {!loading && !error && launcherItems.length ? (
          <section className={styles.launcherSection} aria-label="Workspace apps">
            <div className={styles.grid}>
              {launcherItems.map(renderAppTile)}
            </div>
          </section>
        ) : null}
      </section>
    </main>
  );
}
