'use client';

import { useEffect, useMemo, useState } from 'react';

import { AppButton } from '@/lib/ui/primitives';
import { DataBadge } from '@/lib/ui/data-table';
import { EmptyPanel } from '@/lib/ui/empty-panel';
import { ListDetailColumns, ListDetailPanel, ListDetailShell } from '@/lib/ui/list-detail';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { StateBanner } from '@/lib/ui/state-banner';
import { useWorkspaceServices, useWorkstationStreamState } from '@/lib/workspace/workspace-services';
import { WorkstationSurfaceRoot } from '@/lib/workspace/workstation-surface-primitives';

type NotificationRecord = Record<string, unknown> & {
  id?: string | null;
  title?: string | null;
  event_type?: string | null;
  summary?: string | null;
  text?: string | null;
  channel?: string | null;
  read_at?: string | null;
  created_at?: string | null;
};

function normalizeNotificationItems(payload: unknown): NotificationRecord[] {
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const items = (payload as Record<string, unknown>).items;
  return Array.isArray(items)
    ? items.filter((item): item is NotificationRecord => Boolean(item) && typeof item === 'object')
    : [];
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function formatTimestamp(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) {
    return 'No timestamp recorded';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function NotificationSkeleton() {
  return (
    <ListDetailColumns
      primary={(
        <ListDetailPanel eyebrow="Feed" title="Loading notifications">
          <SkeletonBlock height="4.8rem" />
          <SkeletonBlock height="4.8rem" />
          <SkeletonBlock height="4.8rem" />
        </ListDetailPanel>
      )}
      secondary={(
        <ListDetailPanel eyebrow="Focus" title="Loading notification detail">
          <SkeletonBlock height="3.2rem" />
          <SkeletonBlock height="3.2rem" />
          <SkeletonBlock height="3.2rem" />
        </ListDetailPanel>
      )}
    />
  );
}

export function WorkstationNotificationsPane() {
  const services = useWorkspaceServices();
  const streamState = useWorkstationStreamState();
  const [items, setItems] = useState<NotificationRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isMarking, setIsMarking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const refresh = async (showLoading = false) => {
    if (showLoading) {
      setIsLoading(true);
    }
    setError(null);
    const payload = await services.client.listNotifications({ limit: 80 });
    setItems(normalizeNotificationItems(payload));
    setIsLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    void refresh(true)
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Notifications are unavailable.');
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [services.client]);

  useEffect(() => {
    if (streamState.notifications.version === 0) {
      return;
    }
    void refresh(false).catch((loadError) => {
      setError(loadError instanceof Error ? loadError.message : 'Notifications are unavailable.');
      setIsLoading(false);
    });
  }, [services.client, streamState.notifications.version]);

  useEffect(() => {
    if (items.length === 0) {
      setSelectedId(null);
      return;
    }
    if (selectedId && items.some((item) => String(item.id ?? '').trim() === selectedId)) {
      return;
    }
    const nextSelection = items.find((item) => !item.read_at) ?? items[0];
    setSelectedId(String(nextSelection.id ?? '').trim() || null);
  }, [items, selectedId]);

  const selectedNotification = useMemo(
    () => items.find((item) => String(item.id ?? '').trim() === selectedId) ?? null,
    [items, selectedId],
  );

  return (
    <WorkstationSurfaceRoot surface="notifications">
      <ListDetailShell
        title="Notifications"
        subtitle="Operator-facing workspace alerts, stream-backed unread state, and the current notification focus."
        actions={(
          <div className="app-inline-actions">
            <AppButton
              type="button"
              tone="secondary"
              onClick={() => {
                void refresh(false);
              }}
            >
              Refresh
            </AppButton>
            <AppButton
              type="button"
              disabled={isMarking || items.length === 0}
              onClick={() => {
                setIsMarking(true);
                setStatus(null);
                void services.client.markNotificationsRead({ markAll: true })
                  .then(() => {
                    const now = new Date().toISOString();
                    setItems((previousItems) =>
                      previousItems.map((item) => ({
                        ...item,
                        read_at: item.read_at ?? now,
                      })),
                    );
                    services.streams.markNotificationsRead({ markAll: true });
                    setStatus('Marked all visible notifications as read.');
                  })
                  .catch((markError) => {
                    setError(markError instanceof Error ? markError.message : 'Could not mark notifications as read.');
                  })
                  .finally(() => {
                    setIsMarking(false);
                  });
              }}
            >
              {isMarking ? 'Marking…' : 'Mark all read'}
            </AppButton>
          </div>
        )}
      >
        <StateBanner
          title="Notification stream"
          detail={`Connection ${streamState.notifications.connectionState} · ${streamState.notifications.unreadCount} unread`}
        >
          Notification state is sourced from the kernel stream manager and canonical reloads.
        </StateBanner>
        {status ? (
          <StateBanner tone="success" title="Notification state updated">
            {status}
          </StateBanner>
        ) : null}
        {error ? (
          <StateBanner tone="danger" title="Notification feed is degraded">
            Refresh notifications when the service is ready.
          </StateBanner>
        ) : null}

        {isLoading ? (
          <NotificationSkeleton />
        ) : (
          <ListDetailColumns
            primary={(
              <ListDetailPanel
                eyebrow="Feed"
                title="Incoming workspace alerts"
                subtitle={`${items.length} notifications currently visible in the feed.`}
              >
                {items.length === 0 ? (
                  <EmptyPanel
                    title="No notifications visible"
                    body="Notifications will appear here when Sage finishes runs, needs attention, or records activity."
                  />
                ) : (
                  <div className="app-stack-3">
                    {items.map((item, index) => {
                      const id = String(item.id ?? `notification-${index}`).trim();
                      const selected = id === selectedId;
                      const unread = !item.read_at;
                      return (
                        <button
                          key={id}
                          type="button"
                          onClick={() => setSelectedId(id)}
                          className={`app-card-button${selected ? ' app-card-button--selected' : ''}`}
                        >
                          <div className="app-inline-actions app-inline-actions--between app-inline-actions--start">
                            <strong className="app-card-button__title">
                              {readString(item.title ?? item.event_type, 'Notification')}
                            </strong>
                            <DataBadge tone={unread ? 'accent' : 'neutral'}>
                              {unread ? 'Unread' : 'Read'}
                            </DataBadge>
                          </div>
                          <span className="app-card-button__subtitle">
                            {readString(item.summary ?? item.text, 'No notification summary is available.')}
                          </span>
                          <span className="app-card-button__meta">
                            {readString(item.channel, 'workspace')} · {formatTimestamp(item.created_at)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </ListDetailPanel>
            )}
            secondary={(
              <ListDetailPanel
                eyebrow="Focus"
                title={selectedNotification ? readString(selectedNotification.title ?? selectedNotification.event_type, 'Notification') : 'No notification selected'}
                subtitle={selectedNotification ? readString(selectedNotification.channel, 'workspace') : 'Choose a notification from the feed to inspect its detail.'}
              >
                {!selectedNotification ? (
                  <EmptyPanel
                    title="No selected notification"
                    body="Select a notification from the list to read the full message and current status."
                  />
                ) : (
                  <>
                    <StateBanner
                      tone={selectedNotification.read_at ? 'neutral' : 'warning'}
                      title={selectedNotification.read_at ? 'Read' : 'Unread'}
                      detail={formatTimestamp(selectedNotification.created_at)}
                    >
                      {readString(selectedNotification.summary ?? selectedNotification.text, 'No notification summary is available.')}
                    </StateBanner>
                    <div className="app-meta-list">
                      <div className="app-meta-item">
                        <span className="app-meta-label">Notification id</span>
                        <span className="app-meta-value app-meta-value--mono">{readString(selectedNotification.id, 'No identifier recorded')}</span>
                      </div>
                      <div className="app-meta-item">
                        <span className="app-meta-label">Message</span>
                        <span className="app-meta-value app-meta-value--body">{readString(selectedNotification.text ?? selectedNotification.summary, 'No notification body is available.')}</span>
                      </div>
                    </div>
                  </>
                )}
              </ListDetailPanel>
            )}
          />
        )}
      </ListDetailShell>
    </WorkstationSurfaceRoot>
  );
}
