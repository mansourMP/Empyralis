'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { buildCookieAuthHeaders } from '@/lib/auth/csrf';

import styles from './hosted-mini-apps.module.css';

type HostedMiniAppManifest = {
  app_id: string;
  label?: string | null;
  description?: string | null;
  icon_url?: string | null;
  category?: string | null;
  publisher_id?: string | null;
  publisher_name?: string | null;
  publisher_domain?: string | null;
  support_url?: string | null;
  privacy_url?: string | null;
  terms_url?: string | null;
  runtime_type?: string | null;
  runtime_mode?: string | null;
  destination_url?: string | null;
  platform_route?: string | null;
  verification_status?: string | null;
  official_claim?: Record<string, unknown> | null;
  memory_scope?: string | null;
  permissions?: string[];
  context_envelope?: {
    default_classes?: string[];
    optional_classes?: string[];
    inherits_sage_memory_by_default?: boolean;
    inherits_specialist_memory_by_default?: boolean;
  } | null;
  hosted_app?: {
    hosted_url?: string | null;
    allowed_origins?: string[];
    embed?: {
      kind?: string | null;
      sandbox?: string[];
      allow?: string[];
      referrer_policy?: string | null;
    } | null;
    bridge?: {
      endpoint?: string | null;
      request_type?: string | null;
      response_type?: string | null;
      ready_type?: string | null;
      allowed_contracts?: Record<string, string[]>;
      permissions?: string[];
      context_envelope?: {
        default_classes?: string[];
        optional_classes?: string[];
        inherits_sage_memory_by_default?: boolean;
        inherits_specialist_memory_by_default?: boolean;
      } | null;
      denied_by_default?: string[];
      launch_token_required?: boolean;
    } | null;
    launch?: {
      token?: string | null;
      expires_at?: number | null;
      bound_origin?: string | null;
      bridge_nonce?: string | null;
    } | null;
  } | null;
};

type HostedMiniAppBridgeConfig = NonNullable<
  NonNullable<HostedMiniAppManifest['hosted_app']>['bridge']
>;

type HostedMiniAppBridgeEvent = {
  type?: string;
  requestId?: string;
  bridgeKind?: string;
  bridgeType?: string;
  requestText?: string;
  target?: Record<string, unknown>;
  contextEnvelope?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
};

type BridgeActivityState = 'idle' | 'pending' | 'success' | 'error';

type BridgeActivity = {
  state: BridgeActivityState;
  requestId: string;
  bridgeKind: string;
  bridgeType: string;
  origin: string;
  requestText?: string;
  error?: string;
  auditId?: string;
  runId?: string;
  threadId?: string;
};

async function fetchManifest(workspaceId: string, appId: string): Promise<HostedMiniAppManifest> {
  const response = await fetch(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/mini-apps/${encodeURIComponent(appId)}/hosted-manifest`,
    {
      credentials: 'include',
      headers: buildCookieAuthHeaders('GET', { accept: 'application/json' }),
    },
  );
  if (!response.ok) {
    throw new Error(`Hosted mini app manifest failed with status ${response.status}.`);
  }
  return response.json() as Promise<HostedMiniAppManifest>;
}

async function dispatchBridgeMessage(
  endpoint: string,
  body: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const response = await fetch(endpoint, {
    method: 'POST',
    credentials: 'include',
    headers: buildCookieAuthHeaders('POST', {
      accept: 'application/json',
      'content-type': 'application/json',
    }),
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null) as
    | { detail?: string; error?: string; message?: string }
    | Record<string, unknown>
    | null;
  if (!response.ok) {
    const detail =
      (typeof payload?.detail === 'string' && payload.detail)
      || (typeof payload?.error === 'string' && payload.error)
      || (typeof payload?.message === 'string' && payload.message)
      || `Hosted mini app bridge failed with status ${response.status}.`;
    throw new Error(detail);
  }
  return (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>;
}

function humanizeToken(value: string): string {
  return value
    .split(/[._-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function appInitials(label: string): string {
  const parts = label.trim().split(/\s+/).filter(Boolean);
  return (parts.length ? parts : ['App'])
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('');
}

function isAllowedBridgeRequest(
  bridgeConfig: HostedMiniAppBridgeConfig | null | undefined,
  bridgeKind: string,
  bridgeType: string,
): { ok: true } | { ok: false; error: string } {
  const normalizedKind = String(bridgeKind || '').trim().toLowerCase();
  const normalizedType = String(bridgeType || '').trim().toLowerCase();
  const allowedContracts = bridgeConfig?.allowed_contracts || {};
  const allowedTypes = allowedContracts[normalizedKind] || [];
  if (!normalizedKind || !normalizedType) {
    return { ok: false, error: 'Hosted mini apps must declare both bridge kind and bridge type.' };
  }
  if (!allowedTypes.length || !allowedTypes.includes(normalizedType)) {
    return { ok: false, error: 'This hosted app requested a bridge contract that is not allowed for this embed.' };
  }
  return { ok: true };
}

export function HostedMiniAppSurface({
  workspaceId,
  appId,
}: {
  workspaceId: string;
  appId: string;
}) {
  const searchParams = useSearchParams();
  const showDetails = searchParams.get('details') === '1';
  const [manifest, setManifest] = useState<HostedMiniAppManifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [, setBridgeActivity] = useState<BridgeActivity | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  useEffect(() => {
    const previousMode = document.body.getAttribute('data-workstation-app-surface');
    document.body.setAttribute('data-workstation-app-surface', 'open');
    return () => {
      if (previousMode) {
        document.body.setAttribute('data-workstation-app-surface', previousMode);
      } else {
        document.body.removeAttribute('data-workstation-app-surface');
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setFrameLoaded(false);
    void fetchManifest(workspaceId, appId)
      .then((payload) => {
        if (!cancelled) {
          setManifest(payload);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load hosted mini app.');
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [appId, workspaceId]);

  const bridgeConfig = manifest?.hosted_app?.bridge;
  const allowedOrigins = useMemo(
    () => new Set((manifest?.hosted_app?.allowed_origins || []).map((origin) => String(origin))),
    [manifest],
  );
  const bridgePermissions = useMemo(
    () => manifest?.hosted_app?.bridge?.permissions || manifest?.permissions || [],
    [manifest],
  );
  const deniedByDefault = useMemo(() => manifest?.hosted_app?.bridge?.denied_by_default || [], [manifest]);
  const defaultEnvelopeClasses = useMemo(
    () => manifest?.hosted_app?.bridge?.context_envelope?.default_classes || manifest?.context_envelope?.default_classes || [],
    [manifest],
  );
  const optionalEnvelopeClasses = useMemo(
    () => manifest?.hosted_app?.bridge?.context_envelope?.optional_classes || manifest?.context_envelope?.optional_classes || [],
    [manifest],
  );

  const postToIframe = useCallback((payload: Record<string, unknown>, targetOrigin: string) => {
    iframeRef.current?.contentWindow?.postMessage(payload, targetOrigin);
  }, []);

  useEffect(() => {
    if (!manifest || !bridgeConfig?.endpoint) {
      return undefined;
    }
    const requestType = bridgeConfig.request_type || 'empyralis.hosted_app.bridge.request';
    const responseType = bridgeConfig.response_type || 'empyralis.hosted_app.bridge.response';

    const handleMessage = (event: MessageEvent<HostedMiniAppBridgeEvent>) => {
      if (event.source !== iframeRef.current?.contentWindow) {
        return;
      }
      if (!allowedOrigins.has(event.origin)) {
        return;
      }
      const payload = event.data;
      if (!payload || payload.type !== requestType) {
        return;
      }
      const requestId = typeof payload.requestId === 'string' ? payload.requestId : '';
      const bridgeKind = String(payload.bridgeKind || '');
      const bridgeType = String(payload.bridgeType || '');
      const localValidation = isAllowedBridgeRequest(bridgeConfig, bridgeKind, bridgeType);
      if (!localValidation.ok) {
        setBridgeActivity({
          state: 'error',
          requestId,
          bridgeKind,
          bridgeType,
          origin: event.origin,
          requestText: typeof payload.requestText === 'string' ? payload.requestText : undefined,
          error: localValidation.error,
        });
        postToIframe(
          {
            type: responseType,
            requestId,
            ok: false,
            error: localValidation.error,
          },
          event.origin,
        );
        return;
      }
      setBridgeActivity({
        state: 'pending',
        requestId,
        bridgeKind,
        bridgeType,
        origin: event.origin,
        requestText: typeof payload.requestText === 'string' ? payload.requestText : undefined,
      });
      void dispatchBridgeMessage(String(bridgeConfig.endpoint), {
        origin: event.origin,
        bridge_kind: bridgeKind,
        bridge_type: bridgeType,
        request_text: payload.requestText,
        target: payload.target,
        context_envelope: payload.contextEnvelope,
        metadata: {
          ...(payload.metadata || {}),
          bridge_nonce: manifest.hosted_app?.launch?.bridge_nonce,
        },
        launch_token: manifest.hosted_app?.launch?.token,
      })
        .then((responsePayload) => {
          const audit =
            typeof responsePayload.audit === 'object' && responsePayload.audit
              ? (responsePayload.audit as { activity_event_id?: unknown })
              : null;
          setBridgeActivity({
            state: 'success',
            requestId,
            bridgeKind,
            bridgeType,
            origin: event.origin,
            requestText: typeof payload.requestText === 'string' ? payload.requestText : undefined,
            auditId: typeof audit?.activity_event_id === 'string' ? audit.activity_event_id : undefined,
            runId: typeof responsePayload.run_id === 'string' ? responsePayload.run_id : undefined,
            threadId: typeof responsePayload.thread_id === 'string' ? responsePayload.thread_id : undefined,
          });
          postToIframe(
            {
              type: responseType,
              requestId,
              ok: true,
              payload: responsePayload,
            },
            event.origin,
          );
        })
        .catch((bridgeError) => {
          const message = bridgeError instanceof Error ? bridgeError.message : 'Hosted mini app bridge failed.';
          setBridgeActivity({
            state: 'error',
            requestId,
            bridgeKind,
            bridgeType,
            origin: event.origin,
            requestText: typeof payload.requestText === 'string' ? payload.requestText : undefined,
            error: message,
          });
          postToIframe(
            {
              type: responseType,
              requestId,
              ok: false,
              error: message,
            },
            event.origin,
          );
        });
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [allowedOrigins, bridgeConfig, manifest, postToIframe]);

  const handleFrameLoad = useCallback(() => {
    setFrameLoaded(true);
    const readyType = bridgeConfig?.ready_type || 'empyralis.hosted_app.bridge.ready';
    const targetOrigin = manifest?.hosted_app?.allowed_origins?.[0];
    if (!targetOrigin) {
      return;
    }
    postToIframe(
      {
        type: readyType,
        appId: manifest?.app_id,
        workspaceId,
        allowedContracts: bridgeConfig?.allowed_contracts || {},
        permissions: bridgePermissions,
        deniedByDefault,
        contextEnvelope: {
          defaultClasses: defaultEnvelopeClasses,
          optionalClasses: optionalEnvelopeClasses,
        },
        memoryScope: manifest?.memory_scope || 'none_by_default',
        launchToken: manifest?.hosted_app?.launch?.token,
        launchTokenExpiresAt: manifest?.hosted_app?.launch?.expires_at,
        bridgeNonce: manifest?.hosted_app?.launch?.bridge_nonce,
      },
      targetOrigin,
    );
  }, [
    bridgeConfig,
    bridgePermissions,
    defaultEnvelopeClasses,
    deniedByDefault,
    manifest,
    optionalEnvelopeClasses,
    postToIframe,
    workspaceId,
  ]);

  const embedSandbox = useMemo(
    () => (manifest?.hosted_app?.embed?.sandbox || []).join(' ') || undefined,
    [manifest],
  );
  const embedAllow = useMemo(
    () => (manifest?.hosted_app?.embed?.allow || []).join('; ') || undefined,
    [manifest],
  );
  const runtimeType = manifest?.runtime_type || manifest?.runtime_mode || 'community';
  const detailItems = useMemo(() => {
    const normalizedRuntime = String(runtimeType || 'community').trim().toLowerCase();
    const items = [
      { label: 'Surface', value: manifest?.hosted_app?.hosted_url ? 'Live app' : 'Draft app' },
      { label: 'Category', value: manifest?.category || 'Applications' },
    ];
    if (normalizedRuntime === 'community') {
      items.push(
        { label: 'Publisher', value: manifest?.publisher_name || manifest?.publisher_id || 'Community publisher' },
        { label: 'Verification', value: humanizeToken(manifest?.verification_status || 'unverified') },
      );
    }
    if (normalizedRuntime === 'link') {
      items.push({
        label: 'Destination URL',
        value: manifest?.destination_url || manifest?.hosted_app?.hosted_url || 'Not reported',
      });
    }
    return items;
  }, [manifest, runtimeType]);

  const appLabel = manifest?.label || humanizeToken(appId);
  const appDescription =
    manifest?.description || 'This app is in the workspace. Add a live surface when it is ready to run.';
  const appHref = `/w/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(appId)}`;
  const appsHref = `/w/${encodeURIComponent(workspaceId)}/applications`;
  const missingHostedManifest = Boolean(error && /status 404/i.test(error));

  if (loading) {
    return (
      <main className={styles.shell}>
        <div className={styles.empty}>Loading application...</div>
      </main>
    );
  }

  if (error && !missingHostedManifest) {
    return (
      <main className={styles.shell}>
        <div className={`${styles.empty} ${styles.error}`}>Application could not load. Try again when ready.</div>
      </main>
    );
  }

  if (missingHostedManifest || !manifest?.hosted_app?.hosted_url) {
    return (
      <main className={styles.shell}>
        <header className={styles.frameHeader}>
          <div className={styles.header}>
            <span className={`${styles.appIcon} ${styles.appIcon_neutral}`} aria-hidden="true">
              {manifest?.icon_url ? <img src={manifest.icon_url} alt="" /> : <span>{appInitials(appLabel)}</span>}
            </span>
            <div>
              <p className={styles.eyebrow}>Application</p>
              <h1 className={styles.title}>{appLabel}</h1>
              <p className={styles.copy}>{appDescription}</p>
            </div>
          </div>
          <div className={styles.linkRow}>
            <Link className={styles.linkSecondary} href={appsHref}>
              Apps
            </Link>
          </div>
        </header>
        <div className={styles.empty}>This app is ready as a workspace app. Add a live surface when it should run.</div>
      </main>
    );
  }

  if (showDetails) {
    return (
      <main className={styles.shell}>
        <header className={styles.frameHeader}>
          <div className={styles.header}>
            <span className={`${styles.appIcon} ${styles.appIcon_neutral}`} aria-hidden="true">
              {manifest.icon_url ? <img src={manifest.icon_url} alt="" /> : <span>{appInitials(appLabel)}</span>}
            </span>
            <div>
              <p className={styles.eyebrow}>App details</p>
              <h1 className={styles.title}>{appLabel}</h1>
              <p className={styles.copy}>{appDescription}</p>
            </div>
          </div>
          <div className={styles.linkRow}>
            <Link className={styles.linkSecondary} href={appsHref}>
              Apps
            </Link>
            <Link className={styles.linkButton} href={appHref}>
              Open app
            </Link>
          </div>
        </header>
        <section className={styles.card} aria-label="Application details">
          <div className={styles.factStack}>
            {detailItems.map((item) => (
              <div key={item.label} className={styles.factBlock}>
                <p className={styles.factLabel}>{item.label}</p>
                <p className={styles.factValue}>{item.value}</p>
              </div>
            ))}
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className={`${styles.shell} ${styles.shellOpen}`}>
      <header className={`${styles.frameHeader} ${styles.frameHeaderOpen}`}>
        <div className={styles.header}>
          <span className={`${styles.appIcon} ${styles.appIcon_neutral}`} aria-hidden="true">
            {manifest.icon_url ? <img src={manifest.icon_url} alt="" /> : <span>{appInitials(appLabel)}</span>}
          </span>
          <div>
            <p className={styles.eyebrow}>App</p>
            <h1 className={styles.title}>{appLabel}</h1>
          </div>
        </div>
        <div className={styles.linkRow}>
          <Link className={styles.linkSecondary} href={appsHref}>
            Back
          </Link>
          <Link className={styles.linkSecondary} href={`${appHref}?details=1`}>
            Details
          </Link>
        </div>
      </header>
      <section className={`${styles.frameShell} ${styles.frameShellOpen}`}>
        <div className={`${styles.frameCard} ${styles.frameCardOpen}`}>
          {!frameLoaded ? (
            <div className={styles.frameLoading} role="status">
              <span className={styles.frameLoadingDot} />
              Loading app...
            </div>
          ) : null}
          <iframe
            ref={iframeRef}
            className={`${styles.frame} ${styles.frameOpen}`}
            src={manifest.hosted_app.hosted_url}
            title={appLabel}
            sandbox={embedSandbox}
            allow={embedAllow}
            scrolling="yes"
            referrerPolicy={(manifest.hosted_app.embed?.referrer_policy as ReferrerPolicy | undefined) || 'origin'}
            onLoad={handleFrameLoad}
          />
        </div>
      </section>
    </main>
  );
}
