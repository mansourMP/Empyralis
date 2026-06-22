'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';

import { DataPaneError } from '@/lib/workspace/data-pane-error';
import { DataBadge } from '@/lib/ui/data-table';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import type { WorkstationSageHeartbeatRecord } from '@/lib/workspace/workstation-client';
import { useWorkspaceServices, useWorkstationStreamState } from '@/lib/workspace/workspace-services';
import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';
import {
  WorkstationSurfaceCard,
  WorkstationSurfaceListItem,
  WorkstationSurfaceNotice,
  WorkstationSurfaceRoot,
  WorkstationSurfaceStat,
  WorkstationSurfaceStatGrid,
} from '@/lib/workspace/workstation-surface-primitives';

type HeartbeatScheduleItem = {
  id: string;
  name: string;
  scheduleKind: string;
  nextRunAt: string | null;
  wakeMode: string;
  delivery: string;
  pendingHeartbeat: boolean;
  timezone: string;
};

type LaneQueueItem = {
  id: string;
  label: string;
  status: string;
  runId: string | null;
  summary: string | null;
};

type LaneQueueLaneSnapshot = {
  concurrency: number;
  pendingCount: number;
  activeCount: number;
  pending: LaneQueueItem[];
  active: LaneQueueItem[];
};

type ProductLaneId = 'now' | 'waiting' | 'scheduled' | 'needs_ok' | 'done';

type ProductQueueItem = {
  id: string;
  label: string;
  lane: string | null;
  status: string;
  statusLabel: string;
  summary: string | null;
  scheduledFor: string | null;
  runId: string | null;
};

type ProductQueueOverview = {
  queuedCount: number;
  runningNowCount: number;
  blockedOnApprovalCount: number;
  doneCount: number;
  quietHours: {
    active: boolean;
    label: string;
    nextAllowedAt: string | null;
  };
  lanes: Record<ProductLaneId, ProductQueueItem[]>;
};

type HeartbeatSnapshot = {
  recurringResponsibility: string;
  bootstrapComplete: boolean;
  progressLabel: string;
  quietHoursLabel: string;
  quietHoursStart: number;
  quietHoursEnd: number;
  pendingWakeups: number;
  claimedWakeups: number;
  planTier: string;
  exactJobs: HeartbeatScheduleItem[];
  nextAction: HeartbeatScheduleItem | null;
  laneQueue: {
    running: boolean;
    acceptingNewWork: boolean;
    draining: boolean;
    pendingCount: number;
    activeCount: number;
    maxTotalConcurrency: number;
    lanes: Record<string, LaneQueueLaneSnapshot>;
  };
  queueOverview: ProductQueueOverview;
};

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeHeartbeatSnapshot(payload: unknown): HeartbeatSnapshot {
  const record = payload && typeof payload === 'object' ? payload as WorkstationSageHeartbeatRecord : {};
  const profile = record.profile && typeof record.profile === 'object' ? record.profile as Record<string, unknown> : {};
  const bootstrap = record.bootstrap && typeof record.bootstrap === 'object' ? record.bootstrap as Record<string, unknown> : {};
  const quietHours = record.quiet_hours && typeof record.quiet_hours === 'object' ? record.quiet_hours as Record<string, unknown> : {};
  const reminders = record.reminders && typeof record.reminders === 'object' ? record.reminders as Record<string, unknown> : {};
  const nextActionRecord = record.next_scheduled_action && typeof record.next_scheduled_action === 'object'
    ? record.next_scheduled_action as Record<string, unknown>
    : null;
  const wakeQueue = record.wake_queue && typeof record.wake_queue === 'object' ? record.wake_queue as Record<string, unknown> : {};
  const laneQueue = record.lane_queue && typeof record.lane_queue === 'object' ? record.lane_queue as Record<string, unknown> : {};
  const queueOverview = record.queue_overview && typeof record.queue_overview === 'object'
    ? record.queue_overview as Record<string, unknown>
    : {};
  const policy = record.policy && typeof record.policy === 'object' ? record.policy as Record<string, unknown> : {};
  const items = Array.isArray(reminders.items)
    ? reminders.items.flatMap((item) => {
      if (!item || typeof item !== 'object') {
        return [];
      }
      const candidate = item as Record<string, unknown>;
      return [{
        id: readString(candidate.id),
        name: readString(candidate.name) || 'Scheduled action',
        scheduleKind: readString(candidate.schedule_kind) || 'cron',
        nextRunAt: readString(candidate.next_run_at) || null,
        wakeMode: readString(candidate.wake_mode) || 'now',
        delivery: readString(candidate.delivery) || 'announce',
        pendingHeartbeat: Boolean(candidate.pending_heartbeat),
        timezone: readString(candidate.timezone) || 'local',
      }];
    })
    : [];
  const nextAction = nextActionRecord
    ? {
      id: readString(nextActionRecord.id),
      name: readString(nextActionRecord.name) || 'Scheduled action',
      scheduleKind: readString(nextActionRecord.schedule_kind) || 'cron',
      nextRunAt: readString(nextActionRecord.next_run_at) || null,
      wakeMode: readString(nextActionRecord.wake_mode) || 'now',
      delivery: readString(nextActionRecord.delivery) || 'announce',
      pendingHeartbeat: Boolean(nextActionRecord.pending_heartbeat),
      timezone: readString(nextActionRecord.timezone) || 'local',
    }
    : null;
  const laneEntries = ['main', 'cron', 'subagent', 'system'].reduce<Record<string, LaneQueueLaneSnapshot>>((acc, lane) => {
    const laneRecord = laneQueue.lanes && typeof laneQueue.lanes === 'object'
      ? (laneQueue.lanes as Record<string, unknown>)[lane]
      : null;
    const lanePayload = laneRecord && typeof laneRecord === 'object' ? laneRecord as Record<string, unknown> : {};
    const normalizeItems = (value: unknown): LaneQueueItem[] => (
      Array.isArray(value)
        ? value.flatMap((item) => {
          if (!item || typeof item !== 'object') {
            return [];
          }
          const recordItem = item as Record<string, unknown>;
          return [{
            id: readString(recordItem.id),
            label: readString(recordItem.label) || 'Queued work',
            status: readString(recordItem.status) || 'queued',
            runId: readString(recordItem.run_id) || null,
            summary: readString(recordItem.summary) || null,
          }];
        })
        : []
    );
    acc[lane] = {
      concurrency: readNumber(lanePayload.concurrency, 1),
      pendingCount: readNumber(lanePayload.pending_count, 0),
      activeCount: readNumber(lanePayload.active_count, 0),
      pending: normalizeItems(lanePayload.pending),
      active: normalizeItems(lanePayload.active),
    };
    return acc;
  }, {});
  const normalizeProductItems = (value: unknown): ProductQueueItem[] => (
    Array.isArray(value)
      ? value.flatMap((item) => {
        if (!item || typeof item !== 'object') {
          return [];
        }
        const recordItem = item as Record<string, unknown>;
        return [{
          id: readString(recordItem.id),
          label: readString(recordItem.label) || 'Queued work',
          lane: readString(recordItem.lane) || null,
          status: readString(recordItem.status) || 'queued',
          statusLabel: readString(recordItem.status_label) || 'Waiting',
          summary: readString(recordItem.summary) || null,
          scheduledFor: readString(recordItem.scheduled_for) || null,
          runId: readString(recordItem.run_id) || null,
        }];
      })
      : []
  );
  const overviewLanes = queueOverview.lanes && typeof queueOverview.lanes === 'object'
    ? queueOverview.lanes as Record<string, unknown>
    : {};
  return {
    recurringResponsibility: readString(profile.recurring_responsibility),
    bootstrapComplete: Boolean(bootstrap.complete),
    progressLabel: readString(bootstrap.progress_label) || '0/5',
    quietHoursLabel: readString(quietHours.label) || '22:00–07:00',
    quietHoursStart: readNumber(quietHours.start_hour, 22),
    quietHoursEnd: readNumber(quietHours.end_hour, 7),
    pendingWakeups: readNumber(wakeQueue.pending_count),
    claimedWakeups: readNumber(wakeQueue.claimed_count),
    planTier: readString(policy.plan_tier) || 'default',
    exactJobs: items,
    nextAction,
    laneQueue: {
      running: Boolean(laneQueue.running),
      acceptingNewWork: laneQueue.accepting_new_work !== false,
      draining: Boolean(laneQueue.draining),
      pendingCount: readNumber(laneQueue.pending_count, 0),
      activeCount: readNumber(laneQueue.active_count, 0),
      maxTotalConcurrency: readNumber(laneQueue.max_total_concurrency, 4),
      lanes: laneEntries,
    },
    queueOverview: {
      queuedCount: readNumber(queueOverview.queued_count, 0),
      runningNowCount: readNumber(queueOverview.running_now_count, 0),
      blockedOnApprovalCount: readNumber(queueOverview.blocked_on_approval_count, 0),
      doneCount: readNumber(queueOverview.done_count, 0),
      quietHours: {
        active: Boolean(queueOverview.quiet_hours && typeof queueOverview.quiet_hours === 'object' && (queueOverview.quiet_hours as Record<string, unknown>).active),
        label: (() => {
          const label = readString(
            queueOverview.quiet_hours && typeof queueOverview.quiet_hours === 'object'
              ? (queueOverview.quiet_hours as Record<string, unknown>).label
              : '',
          );
          return label || 'Background work can run now';
        })(),
        nextAllowedAt: readString(
          queueOverview.quiet_hours && typeof queueOverview.quiet_hours === 'object'
            ? (queueOverview.quiet_hours as Record<string, unknown>).next_allowed_at
            : '',
        ) || null,
      },
      lanes: {
        now: normalizeProductItems(overviewLanes.now),
        waiting: normalizeProductItems(overviewLanes.waiting),
        scheduled: normalizeProductItems(overviewLanes.scheduled),
        needs_ok: normalizeProductItems(overviewLanes.needs_ok),
        done: normalizeProductItems(overviewLanes.done),
      },
    },
  };
}

function formatTimestamp(value: string | null): string {
  if (!value) {
    return 'No next run scheduled';
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    return value;
  }
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function laneTone(laneId: ProductLaneId): 'neutral' | 'success' | 'warning' | 'danger' | 'accent' {
  if (laneId === 'now') {
    return 'success';
  }
  if (laneId === 'needs_ok') {
    return 'warning';
  }
  if (laneId === 'done') {
    return 'accent';
  }
  return 'neutral';
}

function laneStatusText(item: ProductQueueItem): string {
  if (item.scheduledFor) {
    return formatTimestamp(item.scheduledFor);
  }
  if (item.statusLabel) {
    return item.statusLabel;
  }
  if (item.runId) {
    return `Run ${item.runId}`;
  }
  return item.status;
}

function workerLaneLabel(laneId: string): string {
  switch (laneId) {
    case 'main':
      return 'Main agent';
    case 'cron':
      return 'Scheduled work';
    case 'subagent':
      return 'Worker agents';
    case 'system':
      return 'System';
    default:
      return laneId;
  }
}

function workerLaneDescription(laneId: string): string {
  switch (laneId) {
    case 'main':
      return 'Direct Sage turns and user-visible work.';
    case 'cron':
      return 'Recurring tasks and wakeups.';
    case 'subagent':
      return 'Delegated worker-agent jobs.';
    case 'system':
      return 'Background maintenance and platform work.';
    default:
      return 'Workspace work lane.';
  }
}

export function WorkstationSageHeartbeatPane() {
  const { bootstrap, routeManifest } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const streamState = useWorkstationStreamState();
  const [snapshot, setSnapshot] = useState<HeartbeatSnapshot | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const activityRefreshTimerRef = useRef<number | null>(null);

  const refresh = async (showLoading = false) => {
    if (showLoading) {
      setIsLoading(true);
    }
    setError(null);
    const payload = await services.client.getSageHeartbeat();
    setSnapshot(normalizeHeartbeatSnapshot(payload));
    setIsLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    void refresh(true).catch((loadError) => {
      if (!cancelled) {
        setError(loadError);
        setIsLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [services.client]);

  useEffect(() => {
    if (streamState.activity.version === 0) {
      return;
    }
    if (activityRefreshTimerRef.current !== null) {
      window.clearTimeout(activityRefreshTimerRef.current);
    }
    activityRefreshTimerRef.current = window.setTimeout(() => {
      activityRefreshTimerRef.current = null;
      void refresh(false).catch(() => {});
    }, 750);
    return () => {
      if (activityRefreshTimerRef.current !== null) {
        window.clearTimeout(activityRefreshTimerRef.current);
        activityRefreshTimerRef.current = null;
      }
    };
  }, [services.client, streamState.activity.version]);

  const upcomingItems = useMemo(
    () => snapshot?.exactJobs ?? [],
    [snapshot],
  );
  const queueSummaryRows = useMemo(() => {
    if (!snapshot) {
      return [];
    }
    const laneCopy: Array<{ id: ProductLaneId; title: string; empty: string }> = [
      { id: 'now', title: 'Now', empty: 'Nothing is running right now.' },
      { id: 'waiting', title: 'Waiting', empty: 'No queued work is waiting.' },
      { id: 'scheduled', title: 'Scheduled', empty: 'No recurring work is scheduled yet.' },
      { id: 'needs_ok', title: 'Approvals', empty: 'No actions are blocked on approval.' },
      { id: 'done', title: 'Done', empty: 'No recent governed work yet.' },
    ];
    return laneCopy.map(({ id, title, empty }) => {
      const items = snapshot.queueOverview.lanes[id] ?? [];
      const preview = items.slice(0, 2).map((item) => {
        const detail = item.summary || item.statusLabel || (item.runId ? `Run ${item.runId}` : '');
        return detail ? `${item.label} — ${detail}` : item.label;
      });
      return {
        id,
        title,
        count: items.length,
        subtitle: items.length === 1 ? '1 work item' : `${items.length} work items`,
        description: preview.length > 0 ? preview.join(' · ') : empty,
      };
    });
  }, [snapshot]);
  const liveCount = snapshot?.queueOverview.runningNowCount ?? 0;
  const queuedCount = snapshot?.queueOverview.queuedCount ?? 0;
  const approvalCount = snapshot?.queueOverview.blockedOnApprovalCount ?? 0;
  const scheduledCount = snapshot?.exactJobs.length ?? 0;
  const totalVisibleWork = liveCount + queuedCount + approvalCount + scheduledCount;
  const queueStateLabel = snapshot?.laneQueue.draining
    ? 'Draining'
    : snapshot?.queueOverview.quietHours.active
      ? 'Quiet hours'
      : snapshot?.laneQueue.acceptingNewWork
        ? 'Ready'
        : 'Paused';
  const chatHref = routeManifest.routeIndex.chat?.href ?? `/w/${encodeURIComponent(bootstrap.workspace.id)}/sage`;
  const approvalsHref = routeManifest.routeIndex.approvals?.href ?? `/w/${encodeURIComponent(bootstrap.workspace.id)}/approvals`;
  const workerLaneRows = useMemo(() => {
    if (!snapshot) {
      return [];
    }
    return Object.entries(snapshot.laneQueue.lanes).map(([laneId, lane]) => ({
      id: laneId,
      title: workerLaneLabel(laneId),
      description: workerLaneDescription(laneId),
      concurrency: lane.concurrency,
      activeCount: lane.activeCount,
      pendingCount: lane.pendingCount,
      items: [...lane.active, ...lane.pending].slice(0, 3),
    }));
  }, [snapshot]);

  return (
    <WorkstationSurfaceRoot surface="sage-tasks">
      <main className="sage-work-board">
        {error ? <DataPaneError error={error} onRetry={() => void refresh(false)} label="Tasks" /> : null}

        {isLoading || !snapshot ? (
          <div className="app-stack-3">
            <SkeletonBlock height="7rem" />
            <SkeletonBlock height="10rem" />
            <SkeletonBlock height="14rem" />
          </div>
        ) : (
          <>
            <section className="sage-work-hero" aria-label="Task queue overview">
              <div className="sage-work-hero__copy">
                <span className="app-data-badge app-data-badge--accent">{queueStateLabel}</span>
                <h2>Tasks</h2>
                <p>{snapshot.recurringResponsibility || 'Sage has no recurring responsibility set.'}</p>
                <div className="sage-work-hero__actions">
                  <button
                    type="button"
                    className="app-link-button"
                    onClick={() => {
                      void refresh(true).catch((loadError) => {
                        setError(loadError);
                        setIsLoading(false);
                      });
                    }}
                  >
                    Refresh
                  </button>
                  <Link href={chatHref} className="app-link-button">
                    Open chat
                  </Link>
                  <Link href={approvalsHref} className="app-link-button">
                    Approvals
                  </Link>
                </div>
              </div>
              <div className="sage-work-hero__metrics" aria-label="Current task counts">
                <div>
                  <strong>{totalVisibleWork}</strong>
                  <span>Total</span>
                </div>
                <div>
                  <strong>{liveCount}</strong>
                  <span>Live</span>
                </div>
                <div>
                  <strong>{approvalCount}</strong>
                  <span>Needs OK</span>
                </div>
              </div>
            </section>

            <WorkstationSurfaceStatGrid>
              <WorkstationSurfaceStat
                label="Running now"
                value={snapshot.queueOverview.runningNowCount}
                hint={snapshot.queueOverview.lanes.now[0]?.label || 'Nothing active right now.'}
              />
              <WorkstationSurfaceStat
                label="Waiting"
                value={snapshot.queueOverview.queuedCount}
                hint={`${snapshot.queueOverview.blockedOnApprovalCount} need your OK`}
              />
              <WorkstationSurfaceStat
                label="Next scheduled action"
                value={snapshot.nextAction ? formatTimestamp(snapshot.nextAction.nextRunAt) : 'None'}
                hint={snapshot.nextAction?.name || 'No recurring work scheduled yet.'}
              />
              <WorkstationSurfaceStat
                label="Quiet hours"
                value={snapshot.queueOverview.quietHours.active ? 'Active' : snapshot.quietHoursLabel}
                hint={snapshot.queueOverview.quietHours.label}
              />
            </WorkstationSurfaceStatGrid>

            <WorkstationSurfaceCard
              title="Worker lanes"
              description={`Sage can run ${snapshot.laneQueue.maxTotalConcurrency} concurrent work items across the main lane, scheduled lane, worker-agent lane, and system lane.`}
            >
              <div className="sage-worker-lane-grid">
                {workerLaneRows.map((lane) => (
                  <section key={lane.id} className="sage-worker-lane-card">
                    <header className="sage-worker-lane-card__header">
                      <div>
                        <h3>{lane.title}</h3>
                        <p>{lane.description}</p>
                      </div>
                      <DataBadge tone={lane.activeCount > 0 ? 'success' : lane.pendingCount > 0 ? 'warning' : 'neutral'}>
                        {lane.activeCount > 0 ? 'Live' : lane.pendingCount > 0 ? 'Waiting' : 'Idle'}
                      </DataBadge>
                    </header>
                    <div className="sage-worker-lane-card__stats">
                      <span><strong>{lane.activeCount}</strong> active</span>
                      <span><strong>{lane.pendingCount}</strong> waiting</span>
                      <span><strong>{lane.concurrency}</strong> max</span>
                    </div>
                    {lane.items.length > 0 ? (
                      <div className="sage-worker-lane-card__items">
                        {lane.items.map((item) => (
                          <article key={item.id || `${lane.id}:${item.label}`} className="sage-task-row">
                            <span className="sage-task-row__title">{item.label}</span>
                            <span className="sage-task-row__meta">{item.status}{item.runId ? ` · ${item.runId}` : ''}</span>
                            {item.summary ? <p>{item.summary}</p> : null}
                          </article>
                        ))}
                      </div>
                    ) : (
                      <WorkstationSurfaceNotice>No active or queued work in this lane.</WorkstationSurfaceNotice>
                    )}
                  </section>
                ))}
              </div>
            </WorkstationSurfaceCard>

            <WorkstationSurfaceCard
              title="Live Work Board"
              description={snapshot.laneQueue.draining
                ? 'Queue is draining for shutdown.'
                : snapshot.laneQueue.acceptingNewWork
                  ? 'Current work, waiting work, approvals, scheduled items, and recent completions.'
                  : 'Queue is paused.'}
            >
              {queueSummaryRows.some((row) => row.count > 0) ? (
                <div className="sage-task-lane-grid">
                  {queueSummaryRows.map((row) => {
                    const laneItems = snapshot.queueOverview.lanes[row.id] ?? [];
                    return (
                      <section key={row.id} className="sage-task-lane" aria-label={row.title}>
                        <header className="sage-task-lane__header">
                          <div>
                            <h3>{row.title}</h3>
                            <p>{row.subtitle}</p>
                          </div>
                          <DataBadge tone={laneTone(row.id)}>{row.count}</DataBadge>
                        </header>
                        {laneItems.length > 0 ? (
                          <div className="sage-task-lane__items">
                            {laneItems.slice(0, 4).map((item) => (
                              <article key={item.id} className="sage-task-row">
                                <span className="sage-task-row__title">{item.label}</span>
                                <span className="sage-task-row__meta">{laneStatusText(item)}</span>
                                {item.summary ? <p>{item.summary}</p> : null}
                              </article>
                            ))}
                          </div>
                        ) : (
                          <WorkstationSurfaceNotice>{row.description}</WorkstationSurfaceNotice>
                        )}
                      </section>
                    );
                  })}
                </div>
              ) : (
                <WorkstationSurfaceNotice tone="neutral">
                  No task work is active right now.
                </WorkstationSurfaceNotice>
              )}
            </WorkstationSurfaceCard>

            <WorkstationSurfaceCard
              title="Schedule"
              description={`${snapshot.queueOverview.quietHours.label}. Plan tier ${snapshot.planTier}.`}
            >
              <div className="sage-schedule-grid">
                <section className="sage-schedule-primary">
                  <span className="app-meta-label">Next</span>
                  <strong>{snapshot.nextAction ? snapshot.nextAction.name : 'Nothing scheduled'}</strong>
                  <p>{snapshot.nextAction ? formatTimestamp(snapshot.nextAction.nextRunAt) : 'No next run scheduled'}</p>
                </section>
                <div className="sage-schedule-list">
                  {upcomingItems.length > 0 ? (
                    upcomingItems.slice(0, 6).map((item) => (
                      <WorkstationSurfaceListItem
                        key={item.id || `${item.name}-${item.nextRunAt ?? 'none'}`}
                        title={item.name}
                        subtitle={formatTimestamp(item.nextRunAt)}
                        description={`${item.scheduleKind} · ${item.wakeMode} · ${item.delivery}${item.pendingHeartbeat ? ' · waiting for heartbeat' : ''}`}
                      />
                    ))
                  ) : (
                    <WorkstationSurfaceNotice tone="neutral">
                      No reminders or recurring schedules are active yet.
                    </WorkstationSurfaceNotice>
                  )}
                </div>
              </div>
            </WorkstationSurfaceCard>
          </>
        )}
      </main>
    </WorkstationSurfaceRoot>
  );
}

export function WorkstationSageWorkCenterPane() {
  const { bootstrap, routeManifest } = useWorkspaceBoundary();
  const services = useWorkspaceServices();
  const streamState = useWorkstationStreamState();
  const [snapshot, setSnapshot] = useState<HeartbeatSnapshot | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const activityRefreshTimerRef = useRef<number | null>(null);

  const refresh = async (showLoading = false) => {
    if (showLoading) {
      setIsLoading(true);
    }
    setError(null);
    const payload = await services.client.getSageHeartbeat();
    setSnapshot(normalizeHeartbeatSnapshot(payload));
    setIsLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    void refresh(true).catch((loadError) => {
      if (!cancelled) {
        setError(loadError);
        setIsLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [services.client]);

  useEffect(() => {
    if (streamState.activity.version === 0) {
      return;
    }
    if (activityRefreshTimerRef.current !== null) {
      window.clearTimeout(activityRefreshTimerRef.current);
    }
    activityRefreshTimerRef.current = window.setTimeout(() => {
      activityRefreshTimerRef.current = null;
      void refresh(false).catch(() => {});
    }, 750);
    return () => {
      if (activityRefreshTimerRef.current !== null) {
        window.clearTimeout(activityRefreshTimerRef.current);
        activityRefreshTimerRef.current = null;
      }
    };
  }, [services.client, streamState.activity.version]);

  const liveCount = snapshot?.queueOverview.runningNowCount ?? 0;
  const queuedCount = snapshot?.queueOverview.queuedCount ?? 0;
  const scheduledCount = snapshot?.exactJobs.length ?? 0;
  const approvalCount = snapshot?.queueOverview.blockedOnApprovalCount ?? 0;
  const queueStateLabel = snapshot?.laneQueue.draining
    ? 'Draining'
    : snapshot?.queueOverview.quietHours.active
      ? 'Quiet hours'
      : snapshot?.laneQueue.acceptingNewWork
        ? 'Ready'
        : 'Paused';
  const activeItems = snapshot?.queueOverview.lanes.now ?? [];
  const waitingItems = snapshot?.queueOverview.lanes.waiting ?? [];
  const approvalItems = snapshot?.queueOverview.lanes.needs_ok ?? [];
  const recentItems = snapshot?.queueOverview.lanes.done ?? [];
  const hasAnyTask = activeItems.length > 0
    || waitingItems.length > 0
    || approvalItems.length > 0
    || recentItems.length > 0
    || scheduledCount > 0;
  const sageHref = routeManifest.routeIndex.chat?.href ?? `/w/${encodeURIComponent(bootstrap.workspace.id)}/sage`;

  return (
    <WorkstationSurfaceRoot surface="sage-work-center">
      <main className="sage-work-board">
        {error ? <DataPaneError error={error} onRetry={() => void refresh(false)} label="Tasks" /> : null}

        {isLoading || !snapshot ? (
          <div className="app-stack-3">
            <SkeletonBlock height="7rem" />
            <SkeletonBlock height="10rem" />
            <SkeletonBlock height="14rem" />
          </div>
        ) : (
          <>
            <section className="sage-work-hero" aria-label="Tasks overview">
              <div className="sage-work-hero__copy">
                <span className="app-data-badge app-data-badge--accent">{queueStateLabel}</span>
                <h2>Tasks</h2>
                <p>Scheduled and active work Sage is handling for you.</p>
                <div className="sage-work-hero__actions">
                  <button
                    type="button"
                    className="app-link-button"
                    onClick={() => {
                      void refresh(true).catch((loadError) => {
                        setError(loadError);
                        setIsLoading(false);
                      });
                    }}
                  >
                    Refresh
                  </button>
                </div>
              </div>
            </section>

            <WorkstationSurfaceStatGrid>
              <WorkstationSurfaceStat
                label="Active"
                value={snapshot.queueOverview.runningNowCount}
                hint={snapshot.queueOverview.lanes.now[0]?.label || 'Nothing active right now.'}
              />
              <WorkstationSurfaceStat
                label="Waiting"
                value={snapshot.queueOverview.queuedCount}
                hint={waitingItems[0]?.label || 'Nothing waiting.'}
              />
              <WorkstationSurfaceStat
                label="Needs OK"
                value={approvalCount}
                hint={approvalItems[0]?.label || 'No approvals needed.'}
              />
              <WorkstationSurfaceStat
                label="Scheduled"
                value={snapshot.exactJobs.length}
                hint={snapshot.nextAction ? `Next: ${snapshot.nextAction.name}` : 'No scheduled tasks yet.'}
              />
            </WorkstationSurfaceStatGrid>

            {!hasAnyTask ? (
              <section className="sage-task-empty-state" aria-label="No tasks">
                <h3>No tasks yet.</h3>
                <p>Ask Sage in chat to remind you, check something later, or run a recurring update.</p>
                <Link href={sageHref} className="app-link-button app-link-button--primary">
                  Open Sage
                </Link>
              </section>
            ) : null}

            {activeItems.length > 0 ? (
              <WorkstationSurfaceCard
                title="Active now"
                description="Tasks Sage is handling right now."
              >
                <div className="sage-task-list">
                  {activeItems.slice(0, 6).map((item) => (
                    <WorkstationSurfaceListItem
                      key={item.id}
                      title={item.label}
                      subtitle={laneStatusText(item)}
                      description={item.summary || undefined}
                    />
                  ))}
                </div>
              </WorkstationSurfaceCard>
            ) : null}

            {waitingItems.length > 0 || approvalItems.length > 0 ? (
              <WorkstationSurfaceCard
                title="Waiting"
                description="Tasks paused until time, capacity, or your approval is available."
              >
                <div className="sage-task-list">
                  {[...approvalItems, ...waitingItems].slice(0, 8).map((item) => (
                    <WorkstationSurfaceListItem
                      key={item.id}
                      title={item.label}
                      subtitle={item.statusLabel || 'Waiting'}
                      description={item.summary || undefined}
                    />
                  ))}
                </div>
              </WorkstationSurfaceCard>
            ) : null}

            <WorkstationSurfaceCard
              title="Scheduled"
              description={snapshot.nextAction ? `Next: ${snapshot.nextAction.name}` : 'Tasks you ask Sage to run later will appear here.'}
            >
              <div className="sage-schedule-grid">
                <section className="sage-schedule-primary">
                  <span className="app-meta-label">Next</span>
                  <strong>{snapshot.nextAction ? snapshot.nextAction.name : 'Nothing scheduled'}</strong>
                  <p>{snapshot.nextAction ? formatTimestamp(snapshot.nextAction.nextRunAt) : 'No next run scheduled'}</p>
                </section>
                <div className="sage-schedule-list">
                  {snapshot.exactJobs.length > 0 ? (
                    snapshot.exactJobs.slice(0, 6).map((item) => (
                      <WorkstationSurfaceListItem
                        key={item.id || `${item.name}-${item.nextRunAt ?? 'none'}`}
                        title={item.name}
                        subtitle={formatTimestamp(item.nextRunAt)}
                        description={item.delivery === 'announce' ? 'Sage will notify you.' : 'Scheduled task.'}
                      />
                    ))
                  ) : (
                    <WorkstationSurfaceNotice tone="neutral">
                      No scheduled tasks yet.
                    </WorkstationSurfaceNotice>
                  )}
                </div>
              </div>
            </WorkstationSurfaceCard>

            {recentItems.length > 0 ? (
              <WorkstationSurfaceCard
                title="Recent"
                description="Tasks Sage finished recently."
              >
                <div className="sage-task-list">
                  {recentItems.slice(0, 6).map((item) => (
                    <WorkstationSurfaceListItem
                      key={item.id}
                      title={item.label}
                      subtitle={laneStatusText(item)}
                      description={item.summary || undefined}
                    />
                  ))}
                </div>
              </WorkstationSurfaceCard>
            ) : null}
          </>
        )}
      </main>
    </WorkstationSurfaceRoot>
  );
}
