'use client';

import { useCallback, useEffect, useState } from 'react';
import { Plug, RefreshCw, Trash2 } from 'lucide-react';

import { DataPaneError } from '@/lib/workspace/data-pane-error';
import { PlatformNotification } from '@/lib/ui/platform-notification';
import { AppButton } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import {
  WorkstationSurfaceRoot,
} from '@/lib/workspace/workstation-surface-primitives';

// ── All MCP apps known to the platform ────────────────────────────────────────
// Mirrors server/mcp/apps.py; APP_BY_SERVER for fallback labels.

const ALL_APPS: { id: string; label: string; provider: string }[] = [
  { id: 'gmail',            label: 'Gmail',              provider: 'google_workspace' },
  { id: 'google_calendar',  label: 'Google Calendar',    provider: 'google_workspace' },
  { id: 'google_drive',     label: 'Google Drive',       provider: 'google_workspace' },
  { id: 'slack',            label: 'Slack',              provider: 'slack' },
  { id: 'notion',           label: 'Notion',             provider: 'notion' },
];

type AppStatus = {
  id: string;
  label: string;
  provider: string;
  connected: boolean;
};

// ── Component ─────────────────────────────────────────────────────────────────

export function WorkstationSageConnectorsPane({
  connectorIds,
  className,
  surface = 'sage',
  // Accepted but no-ops — kept for backward compat with studio-integrations-pane
  showProviders: _showProviders = true,
  showTools: _showTools = true,
}: {
  showProviders?: boolean;
  showTools?: boolean;
  connectorIds?: string[];
  className?: string;
  surface?: 'sage' | 'studio';
} = {}) {
  const services = useWorkspaceServices();

  const [apps, setApps] = useState<AppStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState<string | null>(null);

  const loadApps = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const payload = await services.client.listApps();
      const remoteApps: Record<string, unknown>[] = Array.isArray(
        (payload as Record<string, unknown>)?.apps,
      )
        ? ((payload as Record<string, unknown>).apps as Record<string, unknown>[])
        : [];

      // Build a set of which server_ids are connected
      const connectedIds = new Set(
        remoteApps.filter((a) => a.connected).map((a) => String(a.id ?? '')),
      );

      // Merge with our static catalog so labels are always available
      const merged: AppStatus[] = ALL_APPS.map((a) => ({
        ...a,
        connected: connectedIds.has(a.id),
      }));

      setApps(merged);
    } catch (err) {
      setError(err);
    } finally {
      setIsLoading(false);
    }
  }, [services.client]);

  useEffect(() => {
    void loadApps();
  }, [loadApps]);

  // Filter if caller passed connectorIds
  const visibleApps = connectorIds && connectorIds.length > 0
    ? apps.filter((a) => connectorIds.includes(a.id))
    : apps;

  // ── Actions ───────────────────────────────────────────────────────────────

  const handleConnect = async (provider: string) => {
    try {
      const result = await services.client.getOAuthStartUrl(provider);
      const url = (result as Record<string, unknown>)?.url;
      if (typeof url === 'string' && url) {
        window.location.href = url;
      } else {
        setStatusMessage(`Could not start OAuth flow for ${provider}.`);
      }
    } catch (err) {
      setStatusMessage(
        err instanceof Error ? err.message : 'Failed to start connection.',
      );
    }
  };

  const handleDisconnect = async (provider: string) => {
    setDisconnecting(provider);
    try {
      await services.client.disconnectApp(provider);
      setStatusMessage(`Disconnected ${provider}.`);
      await loadApps();
    } catch (err) {
      setStatusMessage(
        err instanceof Error ? err.message : 'Failed to disconnect.',
      );
    } finally {
      setDisconnecting(null);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <WorkstationSurfaceRoot surface={surface}>
      {statusMessage ? (
        <PlatformNotification
          tone="info"
          title="Connectors"
          detail={statusMessage}
          onClose={() => setStatusMessage(null)}
        />
      ) : null}

      {isLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 16 }}>
          <SkeletonBlock width="50%" />
          <SkeletonBlock width="70%" />
          <SkeletonBlock width="40%" />
        </div>
      ) : error ? (
        <DataPaneError
          error={error}
          onRetry={() => { void loadApps(); }}
          label="Connectors"
        />
      ) : (
        <div style={{ padding: 16 }}>
          <p style={{ color: 'var(--muted)', fontSize: '0.85rem', marginBottom: 16 }}>
            Connect apps so Sage can access Gmail, Calendar, Drive, Slack, and Notion
            on your behalf. Disconnect at any time.
          </p>

          {visibleApps.length === 0 ? (
            <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 32 }}>
              No apps available.
            </p>
          ) : (
            visibleApps.map((app) => (
              <div
                key={app.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '12px 16px',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius)',
                  marginBottom: 8,
                }}
              >
                <Plug
                  size={18}
                  style={{
                    flexShrink: 0,
                    color: app.connected ? 'var(--success)' : 'var(--muted)',
                    opacity: app.connected ? 1 : 0.4,
                  }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>
                    {app.label}
                  </div>
                  <div style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>
                    {app.connected ? 'Connected' : 'Not connected'}
                  </div>
                </div>
                {app.connected ? (
                  <AppButton
                    tone="danger"
                    onClick={() => { void handleDisconnect(app.provider); }}
                    disabled={disconnecting === app.provider}
                  >
                    <Trash2 size={14} style={{ marginRight: 4 }} />
                    {disconnecting === app.provider ? '...' : 'Disconnect'}
                  </AppButton>
                ) : (
                  <AppButton
                    tone="primary"
                    onClick={() => { void handleConnect(app.provider); }}
                  >
                    <RefreshCw size={14} style={{ marginRight: 4 }} />
                    Connect
                  </AppButton>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </WorkstationSurfaceRoot>
  );
}
