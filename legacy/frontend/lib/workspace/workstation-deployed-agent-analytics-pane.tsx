'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

import { EmptyPanel } from '@/lib/ui/empty-panel';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import type {
  DeployedAgentAdminDashboardMessage,
  DeployedAgentAdminDashboardQuestion,
  DeployedAgentAdminDashboardRecord,
  DeployedAgentBusinessInsightAction,
  DeployedAgentBusinessInsightRecord,
  DeployedAgentBusinessInsightsRecord,
  DeployedAgentCustomerEntryRecord,
  DeployedAgentAdminDashboardUserRow,
} from '@/lib/workspace/workstation-client';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';

type DashboardSnapshot = {
  deployedAgentId: string;
  totalUsers: number;
  messagesToday: number;
  messagesThisCalendarMonth: number;
  ordersToday: number;
  revenueTodayUsd: number;
  usersAtLimitToday: number;
  commonQuestions: DeployedAgentAdminDashboardQuestion[];
  customerEntry: DeployedAgentCustomerEntryRecord | null;
  specialistProfile: Record<string, unknown>;
  userRows: DeployedAgentAdminDashboardUserRow[];
  limit: number;
  hasMore: boolean;
  nextCursorLastMessageAt: string | null;
  nextCursorExternalUserId: string | null;
};

type BusinessInsightSnapshot = {
  id: string;
  patternKey: string;
  insightType: string;
  title: string;
  summary: string;
  recommendation: string;
  sensitivity: string;
  status: string;
  channelKey: string;
  eventCount: number;
  confidence: number;
  redactedExamples: string[];
  updatedAt: string | null;
};

type DashboardCursor = {
  lastMessageAt: string;
  externalUserId: string;
};

function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function readItems<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function normalizeDashboard(payload: unknown): DashboardSnapshot {
  const record = (payload && typeof payload === 'object' ? payload : {}) as DeployedAgentAdminDashboardRecord;
  return {
    deployedAgentId: readString(record.deployed_agent_id),
    totalUsers: readNumber(record.total_users),
    messagesToday: readNumber((record as Record<string, unknown>).messages_today),
    messagesThisCalendarMonth: readNumber(record.messages_this_calendar_month),
    ordersToday: readNumber((record as Record<string, unknown>).orders_today),
    revenueTodayUsd: readNumber((record as Record<string, unknown>).revenue_today_usd),
    usersAtLimitToday: readNumber(record.users_at_limit_today),
    commonQuestions: readItems<DeployedAgentAdminDashboardQuestion>((record as Record<string, unknown>).common_questions),
    customerEntry:
      record.customer_entry && typeof record.customer_entry === 'object'
        ? record.customer_entry as DeployedAgentCustomerEntryRecord
        : null,
    specialistProfile:
      record.specialist_profile && typeof record.specialist_profile === 'object'
        ? record.specialist_profile as Record<string, unknown>
        : {},
    userRows: readItems<DeployedAgentAdminDashboardUserRow>(record.user_rows),
    limit: Math.max(1, readNumber(record.limit) || 50),
    hasMore: record.has_more === true,
    nextCursorLastMessageAt: readString((record as Record<string, unknown>).next_cursor_last_message_at) || null,
    nextCursorExternalUserId: readString((record as Record<string, unknown>).next_cursor_external_user_id) || null,
  };
}

function normalizeBusinessInsights(payload: unknown): BusinessInsightSnapshot[] {
  const record = (payload && typeof payload === 'object' ? payload : {}) as DeployedAgentBusinessInsightsRecord;
  return readItems<DeployedAgentBusinessInsightRecord>(record.items).map((item, index) => {
    const rawExamples = readItems<unknown>(item.redacted_examples);
    return {
      id: readString(item.id),
      patternKey: readString(item.pattern_key),
      insightType: readString(item.insight_type, 'business_pattern'),
      title: readString(item.title, 'Business pattern detected'),
      summary: readString(item.summary, 'This insight needs owner review before it changes the agent.'),
      recommendation: readString(item.recommendation, 'Review this recommendation before applying any policy or knowledge update.'),
      sensitivity: readString(item.sensitivity, 'yellow').toLowerCase(),
      status: readString(item.status, 'candidate').toLowerCase(),
      channelKey: readString(item.channel_key, 'all channels'),
      eventCount: readNumber(item.event_count),
      confidence: readNumber(item.confidence),
      redactedExamples: rawExamples
        .map((example) => {
          if (typeof example === 'string') {
            return example.trim();
          }
          if (example && typeof example === 'object') {
            return readString((example as Record<string, unknown>).text)
              || readString((example as Record<string, unknown>).content)
              || readString((example as Record<string, unknown>).example);
          }
          return '';
        })
        .filter(Boolean)
        .slice(0, 3),
      updatedAt: readString(item.updated_at) || null,
    };
  });
}

function truncateExternalUserId(value: unknown): string {
  const token = readString(value);
  if (!token) {
    return 'Unknown user';
  }
  return token.length <= 12 ? token : `${token.slice(0, 12)}…`;
}

function buildDashboardPath({
  workspaceId,
  agentId,
  limit,
  cursor,
}: {
  workspaceId: string;
  agentId: string;
  limit: number;
  cursor?: DashboardCursor | null;
}): string {
  const params = new URLSearchParams({
    workspace_id: workspaceId,
    limit: String(limit),
  });
  if (cursor?.lastMessageAt) {
    params.set('cursor_last_message_at', cursor.lastMessageAt);
  }
  if (cursor?.externalUserId) {
    params.set('cursor_external_user_id', cursor.externalUserId);
  }
  return `/api/deployed-agents/${encodeURIComponent(agentId)}/admin-dashboard?${params.toString()}`;
}

function extractErrorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object') {
    const detail = readString((payload as Record<string, unknown>).detail);
    if (detail) {
      return detail;
    }
    const errorRecord = (payload as Record<string, unknown>).error;
    if (errorRecord && typeof errorRecord === 'object') {
      const message = readString((errorRecord as Record<string, unknown>).message);
      if (message) {
        return message;
      }
    }
  }
  return `Results are not available yet (${status}).`;
}

function compareUserRows(
  left: DeployedAgentAdminDashboardUserRow,
  right: DeployedAgentAdminDashboardUserRow,
): number {
  const leftTime = Date.parse(readString(left.last_message_at));
  const rightTime = Date.parse(readString(right.last_message_at));
  const safeLeftTime = Number.isFinite(leftTime) ? leftTime : 0;
  const safeRightTime = Number.isFinite(rightTime) ? rightTime : 0;
  if (safeLeftTime !== safeRightTime) {
    return safeRightTime - safeLeftTime;
  }
  const leftId = readString(left.external_user_id);
  const rightId = readString(right.external_user_id);
  return rightId.localeCompare(leftId);
}

function mergeUserRows(
  currentRows: DeployedAgentAdminDashboardUserRow[],
  nextRows: DeployedAgentAdminDashboardUserRow[],
): DeployedAgentAdminDashboardUserRow[] {
  const byExternalUserId = new Map<string, DeployedAgentAdminDashboardUserRow>();
  for (const row of [...currentRows, ...nextRows]) {
    const externalUserId = readString(row.external_user_id);
    if (!externalUserId) {
      continue;
    }
    const existing = byExternalUserId.get(externalUserId);
    if (!existing || compareUserRows(row, existing) < 0) {
      byExternalUserId.set(externalUserId, row);
    }
  }
  return Array.from(byExternalUserId.values()).sort(compareUserRows);
}

function formatCount(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatUsd(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPercent(value: number): string {
  const normalized = value > 1 ? value / 100 : value;
  return new Intl.NumberFormat(undefined, {
    style: 'percent',
    maximumFractionDigits: 0,
  }).format(Math.max(0, Math.min(1, normalized)));
}

function formatInsightLabel(value: string): string {
  return value
    .replace(/[_.-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatRelativeTime(value: unknown): string {
  const token = readString(value);
  if (!token) {
    return 'No activity';
  }
  const parsed = Date.parse(token);
  if (!Number.isFinite(parsed)) {
    return token;
  }
  const diffMs = Date.now() - parsed;
  const diffMinutes = Math.round(diffMs / 60000);
  if (Math.abs(diffMinutes) < 60) {
    return `${Math.max(1, Math.abs(diffMinutes))}m ago`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) {
    return `${Math.max(1, Math.abs(diffHours))}h ago`;
  }
  const diffDays = Math.round(diffHours / 24);
  if (Math.abs(diffDays) < 7) {
    return `${Math.max(1, Math.abs(diffDays))}d ago`;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(parsed));
}

function formatTimestamp(value: unknown): string {
  const token = readString(value);
  if (!token) {
    return 'Unknown time';
  }
  const parsed = Date.parse(token);
  if (!Number.isFinite(parsed)) {
    return token;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(parsed));
}

function messageRoleLabel(message: DeployedAgentAdminDashboardMessage): string {
  return readString(message.role).toLowerCase() === 'agent' ? 'Agent' : 'User';
}

function profileBlock(
  profile: Record<string, unknown>,
  key: 'knowledge' | 'live_data' | 'memory' | 'actions' | 'channel',
): Record<string, unknown> {
  const value = profile[key];
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function DashboardSkeleton() {
  return (
    <div className="deployed-agent-analytics">
      <div className="deployed-agent-analytics__stats">
        {Array.from({ length: 6 }).map((_, index) => (
          <div key={`analytics-stat-skeleton-${index}`} className="deployed-agent-analytics__stat">
            <SkeletonBlock height="2rem" width="5rem" />
            <SkeletonBlock height="0.875rem" width="8rem" />
          </div>
        ))}
      </div>
      <div className="deployed-agent-analytics__table">
        <div className="deployed-agent-analytics__table-head">
          <span>User</span>
          <span>Last Active</span>
          <span>Messages</span>
          <span>Memory</span>
          <span aria-hidden="true" />
        </div>
        {Array.from({ length: 5 }).map((_, index) => (
          <div key={`analytics-row-skeleton-${index}`} className="deployed-agent-analytics__table-row deployed-agent-analytics__table-row--skeleton">
            <SkeletonBlock height="1rem" width="9rem" />
            <SkeletonBlock height="1rem" width="6rem" />
            <SkeletonBlock height="1rem" width="4rem" />
            <SkeletonBlock height="1rem" width="4rem" />
            <SkeletonBlock height="1rem" width="1.5rem" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function WorkstationDeployedAgentAnalyticsPane({
  agentId,
  workspaceId,
}: {
  agentId: string;
  workspaceId: string;
}) {
  const services = useWorkspaceServices();
  const [dashboard, setDashboard] = useState<DashboardSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedUsers, setExpandedUsers] = useState<Record<string, boolean>>({});
  const activeRequestControllerRef = useRef<AbortController | null>(null);
  const [businessInsights, setBusinessInsights] = useState<BusinessInsightSnapshot[]>([]);
  const [insightsLoading, setInsightsLoading] = useState(true);
  const [insightsError, setInsightsError] = useState<string | null>(null);
  const [insightAction, setInsightAction] = useState<{
    insightId: string;
    action: DeployedAgentBusinessInsightAction;
  } | null>(null);

  async function loadDashboard(options?: { append?: boolean; cursor?: DashboardCursor | null }) {
    const append = options?.append === true;
    const requestedCursor = options?.cursor ?? null;
    if (!agentId) {
      setDashboard(null);
      setLoading(false);
      return;
    }
    if (append) {
      setLoadingMore(true);
    } else {
      setLoading(true);
    }
    setError(null);
    activeRequestControllerRef.current?.abort();
    const requestController = new AbortController();
    activeRequestControllerRef.current = requestController;
    try {
      const response = await services.transport.request(
        buildDashboardPath({
          workspaceId,
          agentId,
          limit: 50,
          cursor: requestedCursor,
        }),
        {
          method: 'GET',
          signal: requestController.signal,
        },
      );
      let text = '';
      try {
        text = await response.text();
      } catch (error) {
        if (requestController.signal.aborted || (error instanceof Error && /aborted/i.test(error.message))) {
          return;
        }
        throw error;
      }

      let payload: unknown = null;
      if (text.trim()) {
        try {
          payload = JSON.parse(text);
        } catch {
          payload = text;
        }
      }
      if (!response.ok) {
        throw new Error(extractErrorMessage(payload, response.status));
      }
      if (requestController.signal.aborted || activeRequestControllerRef.current !== requestController) {
        return;
      }
      const normalized = normalizeDashboard(payload);
      setDashboard((current) => {
        if (!append || !current) {
          return normalized;
        }
        return {
          ...normalized,
          userRows: mergeUserRows(current.userRows, normalized.userRows),
        };
      });
    } catch (loadError) {
      if (requestController.signal.aborted || activeRequestControllerRef.current !== requestController) {
        return;
      }
      setError(loadError instanceof Error ? loadError.message : 'Results are not available yet.');
      if (!append) {
        setDashboard(null);
      }
    } finally {
      if (activeRequestControllerRef.current === requestController) {
        activeRequestControllerRef.current = null;
      }
      if (requestController.signal.aborted) {
        return;
      }
      if (append) {
        setLoadingMore(false);
      } else {
        setLoading(false);
      }
    }
  }

  async function loadBusinessInsights() {
    if (!agentId) {
      setBusinessInsights([]);
      setInsightsLoading(false);
      return;
    }
    setInsightsLoading(true);
    setInsightsError(null);
    try {
      const payload = await services.client.listDeployedAgentBusinessInsights({
        deployedAgentId: agentId,
        limit: 6,
      });
      setBusinessInsights(normalizeBusinessInsights(payload));
    } catch (loadError) {
      setBusinessInsights([]);
      setInsightsError(loadError instanceof Error ? loadError.message : 'Business insights could not be loaded.');
    } finally {
      setInsightsLoading(false);
    }
  }

  async function reviewBusinessInsight(insightId: string, action: DeployedAgentBusinessInsightAction) {
    if (!insightId || insightAction) {
      return;
    }
    setInsightAction({ insightId, action });
    setInsightsError(null);
    try {
      await services.client.reviewDeployedAgentBusinessInsight({
        deployedAgentId: agentId,
        insightId,
        action,
      });
      await loadBusinessInsights();
    } catch (reviewError) {
      setInsightsError(reviewError instanceof Error ? reviewError.message : 'Business insight review failed.');
    } finally {
      setInsightAction(null);
    }
  }

  useEffect(() => {
    setExpandedUsers({});
    void loadDashboard({ cursor: null });
    void loadBusinessInsights();
    return () => {
      activeRequestControllerRef.current?.abort();
      activeRequestControllerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, workspaceId]);

  const userRows = useMemo(
    () => dashboard?.userRows ?? [],
    [dashboard],
  );
  const customerEntry = dashboard?.customerEntry ?? null;
  const renderBusinessInsights = () => (
    <div className="deployed-agent-analytics__expanded">
      <div className="deployed-agent-analytics__message">
        <div className="deployed-agent-analytics__message-meta">
          <span className="deployed-agent-analytics__role deployed-agent-analytics__role--agent">Owner intelligence</span>
          <span>{businessInsights.length > 0 ? `${businessInsights.length} active` : 'Aggregate only'}</span>
        </div>
        <div className="deployed-agent-analytics__message-body">
          Aggregated, redacted business recommendations. They do not remember individual customers, auto-change prices, or apply policy without owner review.
        </div>
      </div>
      {insightsError ? (
        <div className="deployed-agent-analytics__error">
          <span>{insightsError}</span>
          <AppButton type="button" tone="secondary" onClick={() => { void loadBusinessInsights(); }}>
            Retry
          </AppButton>
        </div>
      ) : null}
      {insightsLoading ? (
        <div className="deployed-agent-analytics__message">
          <SkeletonBlock height="1rem" width="12rem" />
          <SkeletonBlock height="1rem" width="22rem" />
        </div>
      ) : businessInsights.length === 0 ? (
        <EmptyPanel
          title="No owner insights yet"
          body="Cross-customer patterns will appear here after this agent handles enough conversations."
        />
      ) : businessInsights.map((insight, index) => {
        const isBusy = insightAction?.insightId === insight.id;
        const canReview = insight.status === 'candidate';
        const canApply = insight.status === 'approved';
        const canArchive = insight.status !== 'archived';
        return (
          <div key={insight.id || `${insight.patternKey}-${index}`} className="deployed-agent-analytics__message">
            <div className="deployed-agent-analytics__message-meta">
              <span className="deployed-agent-analytics__role deployed-agent-analytics__role--agent">
                {formatInsightLabel(insight.sensitivity)} · {formatInsightLabel(insight.status)}
              </span>
              <span>{formatInsightLabel(insight.insightType)} · {formatPercent(insight.confidence)}</span>
            </div>
            <div className="deployed-agent-analytics__message-body">
              <strong>{insight.title}</strong>
              {' '}
              {insight.summary}
            </div>
            <div className="deployed-agent-analytics__message-body">
              Recommendation: {insight.recommendation}
            </div>
            <div className="deployed-agent-analytics__message-meta">
              <span>{formatCount(insight.eventCount)} events · {insight.channelKey}</span>
              <span>{insight.updatedAt ? formatRelativeTime(insight.updatedAt) : insight.patternKey}</span>
            </div>
            {insight.redactedExamples.length > 0 ? (
              <div className="deployed-agent-analytics__expanded-empty">
                {insight.redactedExamples.join(' · ')}
              </div>
            ) : null}
            <div className="deployed-agent-analytics__footer">
              {canReview ? (
                <>
                  <AppButton
                    type="button"
                    onClick={() => { void reviewBusinessInsight(insight.id, 'approve'); }}
                    disabled={isBusy || !insight.id}
                  >
                    {isBusy && insightAction?.action === 'approve' ? 'Approving…' : 'Approve'}
                  </AppButton>
                  <AppButton
                    type="button"
                    tone="secondary"
                    onClick={() => { void reviewBusinessInsight(insight.id, 'dismiss'); }}
                    disabled={isBusy || !insight.id}
                  >
                    {isBusy && insightAction?.action === 'dismiss' ? 'Dismissing…' : 'Dismiss'}
                  </AppButton>
                </>
              ) : null}
              {canApply ? (
                <AppButton
                  type="button"
                  onClick={() => { void reviewBusinessInsight(insight.id, 'apply'); }}
                  disabled={isBusy || !insight.id}
                >
                  {isBusy && insightAction?.action === 'apply' ? 'Marking…' : 'Mark applied'}
                </AppButton>
              ) : null}
              {canArchive ? (
                <AppButton
                  type="button"
                  tone="secondary"
                  onClick={() => { void reviewBusinessInsight(insight.id, 'archive'); }}
                  disabled={isBusy || !insight.id}
                >
                  {isBusy && insightAction?.action === 'archive' ? 'Archiving…' : 'Archive'}
                </AppButton>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );

  if (loading && !dashboard) {
    return <DashboardSkeleton />;
  }

  if (error && !dashboard) {
    return (
      <div className="deployed-agent-analytics">
        <EmptyPanel
          title="No results yet"
          body="Results will appear here after this agent handles real customer activity. You can refresh if you expect recent activity to be available."
          actions={(
            <AppButton type="button" onClick={() => { void loadDashboard({ cursor: null }); }}>
              Refresh results
            </AppButton>
          )}
        />
      </div>
    );
  }

  if (!dashboard || userRows.length === 0) {
    return (
      <div className="deployed-agent-analytics">
        <div className="deployed-agent-analytics__stats">
          {[
            ['Total Users', dashboard?.totalUsers ?? 0],
            ['Messages Today', dashboard?.messagesToday ?? 0],
            ['Messages This Month', dashboard?.messagesThisCalendarMonth ?? 0],
            ['Orders Today', dashboard?.ordersToday ?? 0],
            ['Revenue Today', formatUsd(dashboard?.revenueTodayUsd ?? 0)],
            ['Users At Limit Today', dashboard?.usersAtLimitToday ?? 0],
          ].map(([label, value]) => (
            <div key={label} className="deployed-agent-analytics__stat">
              <strong className="deployed-agent-analytics__stat-value">
                {typeof value === 'string' ? value : formatCount(Number(value))}
              </strong>
              <span className="deployed-agent-analytics__stat-label">{label}</span>
            </div>
          ))}
        </div>
        {renderBusinessInsights()}
        <EmptyPanel
          title="No users have messaged this agent yet."
          body="Analytics will appear here once customer messages start flowing through this Business Agent."
        />
      </div>
    );
  }

  return (
    <div className="deployed-agent-analytics">
      {error ? (
        <div className="deployed-agent-analytics__error">
          <span>Results could not refresh yet. Try again when activity is available.</span>
          <AppButton type="button" tone="secondary" onClick={() => { void loadDashboard({ cursor: null }); }}>
            Refresh results
          </AppButton>
        </div>
      ) : null}
      <div className="deployed-agent-analytics__stats">
        <div className="deployed-agent-analytics__stat">
          <strong className="deployed-agent-analytics__stat-value">{formatCount(dashboard.totalUsers)}</strong>
          <span className="deployed-agent-analytics__stat-label">Total Users</span>
        </div>
        <div className="deployed-agent-analytics__stat">
          <strong className="deployed-agent-analytics__stat-value">{formatCount(dashboard.messagesToday)}</strong>
          <span className="deployed-agent-analytics__stat-label">Messages Today</span>
        </div>
        <div className="deployed-agent-analytics__stat">
          <strong className="deployed-agent-analytics__stat-value">{formatCount(dashboard.messagesThisCalendarMonth)}</strong>
          <span className="deployed-agent-analytics__stat-label">Messages This Month</span>
        </div>
        <div className="deployed-agent-analytics__stat">
          <strong className="deployed-agent-analytics__stat-value">{formatCount(dashboard.ordersToday)}</strong>
          <span className="deployed-agent-analytics__stat-label">Orders Today</span>
        </div>
        <div className="deployed-agent-analytics__stat">
          <strong className="deployed-agent-analytics__stat-value">{formatUsd(dashboard.revenueTodayUsd)}</strong>
          <span className="deployed-agent-analytics__stat-label">Revenue Today</span>
        </div>
        <div className="deployed-agent-analytics__stat">
          <strong className="deployed-agent-analytics__stat-value">{formatCount(dashboard.usersAtLimitToday)}</strong>
          <span className="deployed-agent-analytics__stat-label">Users At Limit Today</span>
        </div>
      </div>

      {renderBusinessInsights()}

      <div className="deployed-agent-analytics__expanded">
        <div className="deployed-agent-analytics__message">
          <div className="deployed-agent-analytics__message-meta">
            <span className="deployed-agent-analytics__role deployed-agent-analytics__role--agent">Customer entry</span>
            <span>{readString(customerEntry?.qr_target, 'waiting')}</span>
          </div>
          <div className="deployed-agent-analytics__message-body">
            {readString(customerEntry?.entry_url, 'No QR entry URL configured yet.')}
          </div>
          {readString(customerEntry?.qr_image_url) ? (
            <div className="app-stack-2">
              <img
                src={readString(customerEntry?.qr_image_url)}
                alt="Customer entry QR code"
                className="deployed-agent-analytics__qr"
              />
              {readString(customerEntry?.telegram_deep_link) ? (
                <a href={readString(customerEntry?.telegram_deep_link)} target="_blank" rel="noreferrer">
                  Open Telegram entry
                </a>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="deployed-agent-analytics__message">
          <div className="deployed-agent-analytics__message-meta">
            <span className="deployed-agent-analytics__role deployed-agent-analytics__role--agent">Common questions</span>
            <span>{dashboard.commonQuestions.length > 0 ? `${dashboard.commonQuestions.length} tracked` : 'No repeats yet'}</span>
          </div>
          <div className="deployed-agent-analytics__message-body">
            {dashboard.commonQuestions.length > 0
              ? dashboard.commonQuestions.map((item) => `${readString(item.question, 'Question')} (${formatCount(readNumber(item.count) ?? 0)})`).join(' · ')
              : 'Repeated menu and ordering questions will appear here once customers start asking them.'}
          </div>
        </div>

        <div className="deployed-agent-analytics__table">
          <div className="deployed-agent-analytics__table-head">
            <span>Block</span>
            <span>Mode</span>
            <span>Summary</span>
            <span aria-hidden="true" />
            <span aria-hidden="true" />
          </div>
          {(['knowledge', 'live_data', 'memory', 'actions', 'channel'] as const).map((key) => {
            const block = profileBlock(dashboard.specialistProfile, key);
            return (
              <div key={key} className="deployed-agent-analytics__table-row">
                <span className="deployed-agent-analytics__cell">
                  <strong>{readString(block.title, key.replace('_', ' '))}</strong>
                </span>
                <span className="deployed-agent-analytics__cell">{readString(block.mode, 'Configured')}</span>
                <span className="deployed-agent-analytics__cell">{readString(block.summary, 'Configured')}</span>
                <span className="deployed-agent-analytics__cell" />
                <span className="deployed-agent-analytics__cell" />
              </div>
            );
          })}
        </div>
      </div>

      <div className="deployed-agent-analytics__table">
        <div className="deployed-agent-analytics__table-head">
          <span>User</span>
          <span>Last Active</span>
          <span>Messages</span>
          <span>Memory</span>
          <span aria-hidden="true" />
        </div>
        {userRows.map((row, index) => {
          const externalUserId = readString(row.external_user_id, `user-${index}`);
          const truncatedExternalUserId = truncateExternalUserId(externalUserId);
          const expanded = expandedUsers[externalUserId] === true;
          const lastMessages = readItems<DeployedAgentAdminDashboardMessage>(row.last_5_messages);
          return (
            <div key={externalUserId} className="deployed-agent-analytics__row-group">
              <button
                type="button"
                className={joinClassNames(
                  'deployed-agent-analytics__table-row',
                  expanded && 'deployed-agent-analytics__table-row--expanded',
                )}
                onClick={() => {
                  setExpandedUsers((current) => ({
                    ...current,
                    [externalUserId]: !expanded,
                  }));
                }}
              >
                <span className="deployed-agent-analytics__cell deployed-agent-analytics__cell--user">
                  <strong title={externalUserId}>{truncatedExternalUserId}</strong>
                  <span className="deployed-agent-analytics__user-id-secondary" title={externalUserId}>{truncatedExternalUserId}</span>
                </span>
                <span className="deployed-agent-analytics__cell">
                  {formatRelativeTime(row.last_message_at)}
                </span>
                <span className="deployed-agent-analytics__cell">
                  {formatCount(readNumber(row.total_message_count))}
                </span>
                <span className="deployed-agent-analytics__cell">
                  {formatCount(readNumber(row.memory_entry_count))}
                </span>
                <span className="deployed-agent-analytics__cell deployed-agent-analytics__cell--expand">
                  {expanded ? <ChevronDown size={16} strokeWidth={1.9} /> : <ChevronRight size={16} strokeWidth={1.9} />}
                </span>
              </button>
              {expanded ? (
                <div className="deployed-agent-analytics__expanded">
                  {lastMessages.length === 0 ? (
                    <div className="deployed-agent-analytics__expanded-empty">No recent messages recorded for this user yet.</div>
                  ) : (
                    lastMessages.map((message, messageIndex) => (
                      <div
                        key={readString(message.id, `${externalUserId}-message-${messageIndex}`)}
                        className="deployed-agent-analytics__message"
                      >
                        <div className="deployed-agent-analytics__message-meta">
                          <span className={joinClassNames(
                            'deployed-agent-analytics__role',
                            readString(message.role).toLowerCase() === 'agent'
                              ? 'deployed-agent-analytics__role--agent'
                              : 'deployed-agent-analytics__role--user',
                          )}
                          >
                            {messageRoleLabel(message)}
                          </span>
                          <span>{formatTimestamp(message.created_at)}</span>
                        </div>
                        <div className="deployed-agent-analytics__message-body">
                          {readString(message.content, 'No message content available.')}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {dashboard.hasMore ? (
        <div className="deployed-agent-analytics__footer">
          <AppButton
            type="button"
            tone="secondary"
            onClick={() => {
              if (!dashboard.nextCursorLastMessageAt || !dashboard.nextCursorExternalUserId) {
                return;
              }
              void loadDashboard({
                append: true,
                cursor: {
                  lastMessageAt: dashboard.nextCursorLastMessageAt,
                  externalUserId: dashboard.nextCursorExternalUserId,
                },
              });
            }}
            disabled={loadingMore || !dashboard.nextCursorLastMessageAt || !dashboard.nextCursorExternalUserId}
          >
            {loadingMore ? 'Loading…' : 'Load more'}
          </AppButton>
        </div>
      ) : null}
    </div>
  );
}
