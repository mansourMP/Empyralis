/**
 * THE THESIS, IN ONE IMPORT.
 *
 * `planInboxNeedsYou` below is `frontend/lib/workspace/fleet/inbox-needs-
 * you.ts` — the same 249-line file the web Inbox renders from, imported
 * unchanged. Not vendored, not extracted into a package, not re-implemented.
 * Change the ranking there and both apps change together; there is no second
 * copy to drift, and therefore no test whose only job is to check that a
 * hand-port still matches.
 *
 * That matters most at exactly the point the Swift port got wrong. The
 * module ranks on `Date.parse`, and this backend emits TWO date shapes:
 *
 *   notifications      "2026-08-29 06:49:50.180524+00:00"   SPACE, 6 digits
 *   activity ledger    "2026-08-29T06:51:00.518261Z"        ISO-T, 6 digits
 *
 * Swift's ISO8601DateFormatter accepts neither reliably (it wants 0 or 3
 * fractional digits, and rejects the space outright), so the hand-ported
 * `inboxSortMillis` returned nil for every real row, every item sorted as 0,
 * and the ranking silently degraded to input order. Here the ORIGINAL
 * `sortMillis` runs, so there is nothing to re-derive and nothing to get
 * wrong — provided the JS engine parses both, which is verified at runtime
 * rather than assumed (see dateParsingReport below).
 *
 * This module's own job is only the part that genuinely differs per client:
 * fetching, and answering "where does this row live". Nothing here re-ranks,
 * re-groups or re-labels anything.
 */
import {
  inboxNeedsYouCount,
  planInboxNeedsYou,
  type InboxBlockedRunShape,
  type InboxNeedsYouGroups,
  type InboxNotificationShape,
  type InboxTaskShape,
} from '@shared/workspace/fleet/inbox-needs-you';
import { timeAgo } from '@shared/workspace/fleet/fleet-presentation';

import { apiRequest } from './client';

export { inboxNeedsYouCount, timeAgo };
export type { InboxNeedsYouGroups, InboxNotificationShape };

type FleetAgentRow = { agent_id?: string; label?: string; name?: string };

export type InboxData = {
  groups: InboxNeedsYouGroups;
  /** Raw notifications, kept beside the plan so a row can be shown as read.
   *  `InboxNeedsYouItem` deliberately does not carry read state — its ids are
   *  documented as stable and prefixed (`notification:<id>`), so matching on
   *  that is the intended seam rather than a change to the shared module. */
  notifications: InboxNotificationShape[];
  fetchedAt: number;
};

/**
 * Local-first: the last good answer, so returning to this tab paints real
 * content on the first frame instead of a spinner over data we already have.
 * A refresh that fails leaves this untouched — the screen keeps showing what
 * is true and reports the failure separately, rather than blanking.
 */
let snapshot: InboxData | null = null;
export function cachedInbox(): InboxData | null {
  return snapshot;
}

export async function fetchInbox(workspaceId: string, token: string): Promise<InboxData> {
  const ws = encodeURIComponent(workspaceId);

  // `unread_only=false` on purpose: Linear keeps read rows in the list,
  // dimmed. Fetching only unread would make the read treatment unreachable
  // and would make marking something read look like deleting it. The shared
  // module already supports this — its own comment says it trusts the caller
  // rather than re-filtering on is_read, "so a future 'show read too' view
  // isn't silently dropped here". This is that view.
  const [notificationsRes, tasksRes, activityRes, agentsRes] = await Promise.all([
    apiRequest<{ notifications?: InboxNotificationShape[] }>(
      `/api/w/${ws}/fleet/notifications?limit=50&unread_only=false`,
      { token },
    ),
    apiRequest<{ tasks?: InboxTaskShape[] }>(`/api/w/${ws}/fleet/tasks`, { token }),
    apiRequest<{ items?: InboxBlockedRunShape[] }>(
      `/api/activity/timeline?workspace_id=${ws}&limit=50&event_class=blocked_action`,
      { token },
    ),
    apiRequest<{ agents?: FleetAgentRow[] }>(`/api/w/${ws}/fleet/agents`, { token }),
  ]);

  const notifications = notificationsRes?.notifications ?? [];
  const tasks = tasksRes?.tasks ?? [];
  const blockedRuns = activityRes?.items ?? [];
  const agents = agentsRes?.agents ?? [];

  const agentNames = new Map<string, string>();
  agents.forEach((agent) => {
    const id = String(agent.agent_id || '').trim();
    const label = String(agent.label || agent.name || '').trim();
    if (id && label) agentNames.set(id, label);
  });

  const groups = planInboxNeedsYou({
    stuckTasks: tasks,
    notifications,
    blockedRuns,
    userId: currentUserId,
    // Phase 1 has no task or agent detail screen, so there is nowhere for a
    // row to open. The shared module's own contract covers this exactly:
    // a null href renders as plain, unclickable text rather than a link to
    // nowhere. Returning a route that does not exist would be a dead
    // control wearing a URL — its words, and this product's law.
    taskHrefFor: () => null,
    agentHrefFor: () => null,
    agentNameFor: (installId) => agentNames.get(String(installId || '').trim()) ?? null,
  });

  snapshot = { groups, notifications, fetchedAt: Date.now() };
  return snapshot;
}

/** Set before the first fetch; the shared module needs it to tell MY stuck
 *  task from a teammate's (dropping that check is how a teammate's work ends
 *  up filed under your name). */
let currentUserId: string | null = null;
export function setInboxUser(userId: string | null): void {
  if (userId !== currentUserId) snapshot = null;
  currentUserId = userId;
}

export async function markNotificationRead(
  workspaceId: string,
  notificationId: string,
  token: string,
): Promise<void> {
  await apiRequest(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/notifications/${encodeURIComponent(
      notificationId,
    )}/read`,
    { method: 'POST', token },
  );
}

/**
 * A RUNTIME CHECK, NOT AN ASSUMPTION. The whole shared-module claim rests on
 * this engine parsing what this backend actually emits, and "Hermes probably
 * handles it" is exactly the shape of belief that produced the Swift bug.
 * Rendered on the Inbox screen in dev so the answer is observed, not argued.
 */
export function dateParsingReport(): { label: string; ok: boolean }[] {
  const samples: [string, string][] = [
    ['notifications (space, 6dp)', '2026-08-29 06:49:50.180524+00:00'],
    ['activity ledger (ISO-T, 6dp)', '2026-08-29T06:51:00.518261Z'],
    ['tasks (space, 6dp, offset)', '2026-08-12 09:15:00.123456+00:00'],
  ];
  return samples.map(([label, value]) => ({
    label,
    ok: Number.isFinite(Date.parse(value)),
  }));
}
