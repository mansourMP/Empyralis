"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AlertTriangle, AtSign, ChevronLeft, Clock, Inbox as InboxIcon } from "lucide-react";

import {
  fetchActivityTrace,
  markFleetNotificationRead,
  markInboxSeenNow,
  useFleetAgents,
  useFleetNotifications,
  useFleetWorkspaceTasks,
  useWorkspaceActivity,
  type FleetNotification,
  type WorkspaceActivityEvent,
} from "@/lib/workspace/fleet/fleet-data";
import { useOwnAccountId } from "@/lib/workspace/fleet/members-data";
import { findSageAgent, timeAgo } from "@/lib/workspace/fleet/fleet-presentation";
import { CreateFirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import {
  inboxNeedsYouCount,
  planInboxNeedsYou,
  resolveInboxNeedsYouViewState,
  type InboxNeedsYouGroups,
  type InboxNeedsYouItem,
} from "@/lib/workspace/fleet/inbox-needs-you";

/**
 * INBOX — "what needs you, across every agent, right now."
 *
 * Replaces a page that rendered the raw workspace activity ledger — the
 * founder's own read of it, verbatim: "I totally have no idea what it is."
 * 50 sampled rows were almost entirely `Configured`/`Created`/"<Agent> chat
 * completed"/"<Agent> deleted" — zero of which need a human — while a real
 * task sat in `Needs input`, assigned to him, for 17 days, and the old
 * Inbox never surfaced it once. The ledger answers "what happened, ever";
 * this page answers a narrower, more useful question.
 *
 * DEFAULT TAB is "Needs you": real per-user notifications
 * (task_notification_service.py, previously wired to zero frontend
 * callers), the caller's own tasks stuck in blocked/awaiting_input, and
 * blocked/failed agent runs (activity_ledger_service's own
 * `blocked_action` event class). All the ranking/grouping logic is
 * inbox-needs-you.ts, a pure module with its own plain test — never
 * re-derived here.
 *
 * The raw ledger is NOT deleted — CLAUDE.md's own instruction is explicit
 * that routine activity still deserves a home, just not the front door.
 * "Activity" is the second tab: the OLD Inbox page's own list+detail+trace
 * UI, moved here rather than rewritten. useWorkspaceActivity itself is
 * untouched and still has its other caller (PrimaryRail's badge) —
 * checked before touching it. FleetHome's own "Recent activity" widget
 * was a second caller until FleetHome was deleted outright (the workspace
 * root now redirects into Projects rather than rendering its own
 * dashboard) — nothing replaced it, this Activity tab already covers
 * "what happened" for anyone who wants it.
 *
 * NO NEW TOP-LEVEL RAIL ROW. primary-rail-nav.ts's own header records that
 * an "Activity" rail item was floated and deliberately dropped the same
 * night the flat rail shipped ("an unused top-level route is the failure
 * mode"). A within-page tab is the right altitude for this split.
 *
 * The tab strip lives in `.fleet-content-toolbar`, a SIBLING of `<main>`
 * rather than a child of it — `<main>`'s own class has to switch between
 * the padded reading column (Needs you) and the full-bleed master-detail
 * split (Activity, unchanged from the old page), and `.fleet-content-
 * toolbar` already carries its own horizontal padding (see fleet-theme.css)
 * so it lines up with either shape without needing to live inside one.
 */

// review_required is an explicit backend flag on the raw ledger — a
// stronger signal than the string heuristic below, which stays as a
// fallback for event shapes that predate it. Only used inside the Activity
// tab; the Needs-you tab has no equivalent because it already only shows
// structurally-blocked events (event_class, never text).
function isEscalation(e: WorkspaceActivityEvent): boolean {
  if (e.review_required) return true;
  const s = `${e.event_class || ""} ${e.action || ""}`.toLowerCase();
  return s.includes("escalat") || s.includes("review") || s.includes("approval");
}

type InboxView = "needs-you" | "activity";

export default function InboxPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const [view, setView] = useState<InboxView>("needs-you");

  const { agents, loading: agentsLoading, refresh: refreshAgents } = useFleetAgents(workspaceId);
  const sageAgent = useMemo(() => findSageAgent(agents), [agents]);
  const realAgentCount = useMemo(
    () => agents.filter((a) => a.agent_id !== sageAgent?.agent_id).length,
    [agents, sageAgent],
  );
  const freshWorkspace = !agentsLoading && realAgentCount === 0;
  const agentNameByInstall = useMemo(
    () => new Map(agents.map((a) => [a.agent_id, a.label || "Unnamed agent"])),
    [agents],
  );

  const myAccountId = useOwnAccountId();

  // ── "Needs you" sources — fetched regardless of which tab is active
  // (hooks can't be conditional), each independently tracking its own
  // loading/error so a failure in one never reads as "that source has zero
  // items" (CLAUDE.md: empty and could-not-load are different facts). ────
  const { notifications, loading: notifLoading, error: notifError } = useFleetNotifications(workspaceId, true, 50);
  const { tasks, loading: tasksLoading, error: tasksError } = useFleetWorkspaceTasks(workspaceId);
  const { events: blockedRuns, loading: runsLoading, error: runsError } = useWorkspaceActivity(
    workspaceId,
    30,
    null,
    "blocked_action",
  );

  // Task id → project id, resolved from the SAME list already on screen
  // rather than a second fetch — every href this page builds points at the
  // item's one real home (its task page, or the agent's own Work tab),
  // never a second place it can be opened from.
  const taskProjectById = useMemo(() => new Map(tasks.map((t) => [t.id, t.project_id || ""])), [tasks]);

  const taskHrefFor = useMemo(
    () => (taskId: string | null | undefined) => {
      if (!taskId) return null;
      const projectId = taskProjectById.get(taskId);
      return projectId ? `${base}/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}` : null;
    },
    [base, taskProjectById],
  );
  // The WORKSPACE-level agent route, not the project-scoped twin. An agent
  // belongs to the workspace (CLAUDE.md), and `project_id` is a nullable,
  // never-backfilled column — so the project-scoped URL this used to build
  // resolved to null for exactly the agents whose rows most needed a
  // destination, and the failed-run row rendered as unclickable text.
  // This route takes no project id, so it cannot fail that way.
  const agentHrefFor = useMemo(
    () => (installId: string | null | undefined) => {
      if (!installId) return null;
      return `${base}/agents/${encodeURIComponent(installId)}/work`;
    },
    [base],
  );
  // Null, never a placeholder: "which agent" and "an agent we could not
  // name" are different facts, and the row falls back to its own plain
  // title rather than inventing a name.
  const agentNameFor = useMemo(
    () => (installId: string | null | undefined) =>
      (installId ? agentNameByInstall.get(installId) : null) || null,
    [agentNameByInstall],
  );

  const groups: InboxNeedsYouGroups = useMemo(
    () => planInboxNeedsYou({ stuckTasks: tasks, notifications, blockedRuns, userId: myAccountId, taskHrefFor, agentHrefFor, agentNameFor }),
    [tasks, notifications, blockedRuns, myAccountId, taskHrefFor, agentHrefFor, agentNameFor],
  );
  const needsYouTotal = inboxNeedsYouCount(groups);

  // The rail's Inbox badge is "unseen since last visit" (see fleet-data.ts) —
  // stamp on mount and again whenever the Needs-you tab has real content on
  // screen, same posture the old page took for the raw ledger.
  useEffect(() => {
    if (view === "needs-you" && needsYouTotal > 0 && workspaceId) markInboxSeenNow(workspaceId);
  }, [view, needsYouTotal, workspaceId]);

  const handleOpenNotification = (notification: FleetNotification) => {
    // Best-effort, fire-and-forget: link navigation must never wait on
    // this, and a failed mark-read must never block reaching the task — it
    // just means the notification shows up again next poll, the honest
    // degradation rather than silently losing it.
    void markFleetNotificationRead(workspaceId, notification.id).catch(() => {});
  };

  const allResolved = !notifLoading && !tasksLoading && !runsLoading;
  const anyError = Boolean(notifError || tasksError || runsError);
  const totallyFailed = allResolved && needsYouTotal === 0 && anyError;
  const stillLoadingNeedsYou = !allResolved && needsYouTotal === 0 && !anyError;
  // The one place this decision gets made — see resolveInboxNeedsYouViewState's
  // own doc comment for the bug it replaces (real "needs you" content losing
  // to the "create your first agent" nudge in any workspace with zero agents,
  // which is the ORDINARY state for a customer who has only started tracking
  // tasks, not a rare edge case).
  const needsYouView = resolveInboxNeedsYouViewState({
    stillLoading: stillLoadingNeedsYou,
    totallyFailed,
    freshWorkspace,
    needsYouTotal,
  });

  // ── Activity tab's own state (the old page's, unchanged) ───────────────
  const { events, loading: activityLoading, error: activityError } = useWorkspaceActivity(workspaceId, 50);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [viewedIds, setViewedIds] = useState<Set<string>>(new Set());

  const selectRow = (event: WorkspaceActivityEvent) => {
    if (!event.id) return;
    setSelectedId(event.id);
    setViewedIds((prev) => (prev.has(event.id!) ? prev : new Set(prev).add(event.id!)));
  };

  useEffect(() => {
    if (!selectedId && events.length > 0 && events[0].id) {
      selectRow(events[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, selectedId]);

  const selected = events.find((e) => e.id === selectedId) || null;

  const [trace, setTrace] = useState<WorkspaceActivityEvent[]>([]);
  const [traceLoading, setTraceLoading] = useState(false);
  useEffect(() => {
    if (!selected?.trace_id) {
      setTrace([]);
      return;
    }
    let cancelled = false;
    setTraceLoading(true);
    fetchActivityTrace(workspaceId, selected.trace_id)
      .then((items) => { if (!cancelled) setTrace(items); })
      .finally(() => { if (!cancelled) setTraceLoading(false); });
    return () => { cancelled = true; };
  }, [workspaceId, selected?.trace_id]);

  const traceSubEvents = selected ? trace.filter((t) => t.id && t.id !== selected.id) : [];

  const activityStillLoading = activityLoading && events.length === 0;
  const activityIsEmptyState = !activityStillLoading && (activityError || events.length === 0);
  const activityMainClassName = activityIsEmptyState
    ? "fleet-content"
    : `fleet-content fleet-content--split${mobileDetailOpen ? " fleet-inbox--detail-open" : ""}`;

  return (
    <>
      <div className="fleet-content-toolbar">
        <div className="fleet-segmented" role="tablist" aria-label="Inbox view">
          <button
            type="button"
            role="tab"
            aria-selected={view === "needs-you"}
            className={`fleet-segmented-btn${view === "needs-you" ? " fleet-segmented-btn--active" : ""}`}
            onClick={() => setView("needs-you")}
          >
            Needs you{needsYouTotal > 0 ? ` · ${needsYouTotal}` : ""}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "activity"}
            className={`fleet-segmented-btn${view === "activity" ? " fleet-segmented-btn--active" : ""}`}
            onClick={() => setView("activity")}
          >
            Activity
          </button>
        </div>
      </div>

      {view === "needs-you" ? (
        <main className="fleet-content">
          {needsYouView === "loading" ? (
            <div className="fleet-activity" aria-busy="true" aria-label="Loading inbox">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="fleet-activity-item">
                  <div className="fleet-skeleton-bar" style={{ width: 16, height: 16, borderRadius: 999 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="fleet-skeleton-bar" style={{ width: `${45 + (i % 3) * 15}%`, height: 13 }} />
                  </div>
                </div>
              ))}
            </div>
          ) : needsYouView === "error" ? (
            <FleetSurfaceError
              title="Couldn’t load your inbox"
              message={notifError || tasksError || runsError}
            />
          ) : needsYouView === "onboarding" ? (
            <CreateFirstAgentEmpty
              workspaceId={workspaceId}
              onCreated={refreshAgents}
              title="Your inbox is empty"
              desc="This is where mentions, assignments, and anything your agents need you for shows up. Create your first agent to get started."
            />
          ) : needsYouView === "caught-up" ? (
            <div className="fleet-work-empty">
              <InboxIcon size={26} strokeWidth={1.5} />
              <div className="fleet-work-empty-title">You’re all caught up</div>
              <div className="fleet-work-empty-desc">
                Nothing needs you right now. Mentions, task assignments, stuck work, and failed runs show up here.
              </div>
            </div>
          ) : (
            <div className="fleet-inbox-needsyou">
              <NeedsYouSection
                title="Needs your input"
                items={groups.tasks}
                error={tasksError}
                icon={<Clock size={14} strokeWidth={1.75} style={{ color: "var(--warning-text)", flexShrink: 0 }} />}
              />
              <NeedsYouSection
                title="Notifications"
                items={groups.notifications}
                error={notifError}
                icon={<AtSign size={14} strokeWidth={1.75} style={{ color: "var(--accent)", flexShrink: 0 }} />}
                onOpen={(item) => {
                  const id = item.id.slice("notification:".length);
                  const notification = notifications.find((n) => n.id === id);
                  if (notification) handleOpenNotification(notification);
                }}
              />
              <NeedsYouSection
                title="Failed runs"
                items={groups.runs}
                error={runsError}
                icon={<AlertTriangle size={14} strokeWidth={1.75} style={{ color: "var(--warning-text)", flexShrink: 0 }} />}
              />
            </div>
          )}
        </main>
      ) : (
        <main className={activityMainClassName}>
          {activityStillLoading ? (
            <>
              <div className="fleet-inbox-list" aria-busy="true" aria-label="Loading activity">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="fleet-inbox-row" style={{ cursor: "default" }}>
                    <span className="fleet-skeleton-bar" style={{ width: 7, height: 7, borderRadius: 999 }} />
                    <span className="fleet-skeleton-bar" style={{ width: `${45 + (i % 3) * 15}%`, height: 12 }} />
                    <span className="fleet-skeleton-bar" style={{ width: 30, height: 11, marginLeft: "auto" }} />
                  </div>
                ))}
              </div>
              <div className="fleet-inbox-detail">
                <div className="fleet-skeleton-bar" style={{ width: "50%", height: 18, marginBottom: 6 }} />
                <div className="fleet-skeleton-bar" style={{ width: 160, height: 13, marginBottom: 24, opacity: 0.7 }} />
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div className="fleet-skeleton-bar" style={{ width: "92%", height: 14 }} />
                  <div className="fleet-skeleton-bar" style={{ width: "70%", height: 14 }} />
                </div>
              </div>
            </>
          ) : activityError && events.length === 0 ? (
            <FleetSurfaceError title="Couldn’t load activity" message={activityError} />
          ) : events.length === 0 ? (
            <div className="fleet-work-empty">
              <InboxIcon size={26} strokeWidth={1.5} />
              <div className="fleet-work-empty-title">No activity yet</div>
              <div className="fleet-work-empty-desc">As your agents work, their activity shows up here.</div>
            </div>
          ) : (
            <>
              <div className="fleet-inbox-list">
                {events.map((event) => {
                  const esc = isEscalation(event);
                  const unread = Boolean(event.id) && !viewedIds.has(event.id!);
                  return (
                    <button
                      key={event.id || event.created_at}
                      type="button"
                      className={`fleet-inbox-row${event.id === selectedId ? " is-selected" : ""}`}
                      onClick={() => { selectRow(event); setMobileDetailOpen(true); }}
                    >
                      {esc ? (
                        <AlertTriangle size={14} strokeWidth={1.75} style={{ color: "var(--accent)", flexShrink: 0 }} />
                      ) : (
                        <span className={`fleet-inbox-row-dot${unread ? "" : " is-read"}`} />
                      )}
                      <span className="fleet-inbox-row-title">{event.title || event.action || "Event"}</span>
                      <span className="fleet-inbox-row-time">{event.created_at ? timeAgo(event.created_at) : ""}</span>
                    </button>
                  );
                })}
              </div>

              <div className="fleet-inbox-detail">
                {!selected ? (
                  <div className="fleet-work-empty">
                    <InboxIcon size={26} strokeWidth={1.5} />
                    <div className="fleet-work-empty-title">Nothing selected</div>
                    <div className="fleet-work-empty-desc">Pick an item on the left to see its detail.</div>
                  </div>
                ) : (
                  <>
                    <button type="button" className="fleet-inbox-back" onClick={() => setMobileDetailOpen(false)}>
                      <ChevronLeft size={16} strokeWidth={2} />
                      Activity
                    </button>
                    <h2 className="fleet-inbox-detail-title">{selected.title || selected.action || "Event"}</h2>
                    <div className="fleet-inbox-detail-meta">
                      {[
                        selected.install_id ? agentNameByInstall.get(selected.install_id) || null : null,
                        selected.channel ? `via ${selected.channel}` : null,
                        selected.created_at ? timeAgo(selected.created_at) : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>

                    {selected.summary && <p className="fleet-inbox-detail-summary">{selected.summary}</p>}

                    {traceLoading ? (
                      <div className="fleet-inbox-trace-title">Loading trace…</div>
                    ) : traceSubEvents.length > 0 ? (
                      <div>
                        <div className="fleet-inbox-trace-title">Trace</div>
                        <div className="fleet-inbox-trace">
                          {traceSubEvents.map((t) => (
                            <div key={t.id} className="fleet-inbox-trace-row">
                              <span className="fleet-inbox-trace-row-time">{t.created_at ? timeAgo(t.created_at) : ""}</span>
                              <span className="fleet-inbox-trace-row-title">{t.title || t.action || "Event"}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            </>
          )}
        </main>
      )}
    </>
  );
}

/** One titled group of "needs you" rows. Renders nothing when empty AND
 *  error-free — an empty section with nothing to say would just be noise
 *  above sections that do have content; the page-level empty state covers
 *  "literally nothing anywhere". An error on THIS source specifically is
 *  shown inline, right where the missing rows would have been, rather than
 *  silently rendering as "this section has zero items" — the same honesty
 *  rule the my-work page and the old Inbox both already apply. */
function NeedsYouSection({
  title,
  items,
  error,
  icon,
  onOpen,
}: {
  title: string;
  items: InboxNeedsYouItem[];
  error: string | null;
  icon: React.ReactNode;
  onOpen?: (item: InboxNeedsYouItem) => void;
}) {
  if (items.length === 0 && !error) return null;
  return (
    <section className="fleet-inbox-needsyou-section">
      <div className="fleet-detail-section-title">
        {title}
        {items.length > 0 ? <span className="fleet-mywork-section-count">{items.length}</span> : null}
      </div>
      {error ? (
        <div className="fleet-inbox-section-error" role="alert">
          Couldn’t load this section: {error}
        </div>
      ) : null}
      <div className="fleet-inbox-list">
        {items.map((item) =>
          item.href ? (
            <Link key={item.id} href={item.href} className="fleet-inbox-row" onClick={() => onOpen?.(item)}>
              {icon}
              <span className="fleet-inbox-row-title">
                {item.title}
                {item.detail ? <span className="fleet-inbox-row-detail"> — {item.detail}</span> : null}
              </span>
              <span className="fleet-inbox-row-time">{item.timestamp ? timeAgo(item.timestamp) : ""}</span>
            </Link>
          ) : (
            <div key={item.id} className="fleet-inbox-row fleet-inbox-row--static">
              {icon}
              <span className="fleet-inbox-row-title">
                {item.title}
                {item.detail ? <span className="fleet-inbox-row-detail"> — {item.detail}</span> : null}
              </span>
              <span className="fleet-inbox-row-time">{item.timestamp ? timeAgo(item.timestamp) : ""}</span>
            </div>
          ),
        )}
      </div>
    </section>
  );
}
