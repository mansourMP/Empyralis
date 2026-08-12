"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, ChevronLeft, Inbox as InboxIcon } from "lucide-react";

import { fetchActivityTrace, markInboxSeenNow, useFleetAgents, useWorkspaceActivity, type WorkspaceActivityEvent } from "@/lib/workspace/fleet/fleet-data";
import { findSageAgent, timeAgo } from "@/lib/workspace/fleet/fleet-presentation";
import { CreateFirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";

// review_required is an explicit backend flag — a stronger signal than the
// string heuristic below, which stays as a fallback for event shapes that
// predate it.
function isEscalation(e: WorkspaceActivityEvent): boolean {
  if (e.review_required) return true;
  const s = `${e.event_class || ""} ${e.action || ""}`.toLowerCase();
  return s.includes("escalat") || s.includes("review") || s.includes("approval");
}

export default function InboxPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const { events, loading, error } = useWorkspaceActivity(workspaceId, 50);
  // Keeps Sage/the Operator in `agents` itself — an Inbox event CAN be
  // attributed to Sage's own install_id (sage_activity is a real event
  // class), and agentNameByInstall below must still resolve its name rather
  // than falling back to "Unnamed agent". Only the "is this workspace
  // actually fresh" check needs the REAL (Sage-excluded) count: every
  // fleet_list_agents response carries the workspace's Operator install
  // from the moment the workspace exists, so the raw length is NEVER zero
  // for a real workspace — freshWorkspace used to read that raw length, so
  // the onboarding empty state below could never fire for a brand-new
  // account. Confirmed empirically 2026-08-13: a fresh account's Inbox
  // showed the generic "You're all caught up" panel instead.
  const { agents, loading: agentsLoading, refresh: refreshAgents } = useFleetAgents(workspaceId);
  const sageAgent = useMemo(() => findSageAgent(agents), [agents]);
  const realAgentCount = useMemo(
    () => agents.filter((a) => a.agent_id !== sageAgent?.agent_id).length,
    [agents, sageAgent],
  );
  const freshWorkspace = !agentsLoading && realAgentCount === 0;

  // The rail's Inbox count is "unseen since last visit" (see fleet-data.ts) —
  // stamp on mount and again on every poll while this page is actually on
  // screen, so items that arrive while the reader is already looking never
  // count as unread the next time they check the rail from elsewhere.
  useEffect(() => {
    if (events.length > 0 && workspaceId) markInboxSeenNow(workspaceId);
  }, [events, workspaceId]);

  const agentNameByInstall = useMemo(
    () => new Map(agents.map((a) => [a.agent_id, a.label || "Unnamed agent"])),
    [agents],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Mobile only (see fleet-theme.css's fleet-inbox--detail-open): whether
  // the pushed-in detail view is showing over the list. Deliberately
  // separate from selectedId — the auto-landing effect below sets that on
  // load same as desktop, but a reader arriving on a phone should still see
  // the list first, not jump straight into a detail they didn't tap.
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  // Session-scoped "seen" set — there's no persistent read-state on activity
  // ledger rows (a write-path concern, out of scope here), so the unread dot
  // tracks what this reader has opened this session rather than fabricating
  // durable state.
  const [viewedIds, setViewedIds] = useState<Set<string>>(new Set());

  const selectRow = (event: WorkspaceActivityEvent) => {
    if (!event.id) return;
    setSelectedId(event.id);
    setViewedIds((prev) => (prev.has(event.id!) ? prev : new Set(prev).add(event.id!)));
  };

  // Land on the top item once the first real page of events loads — a
  // Linear-style inbox reader is never staring at a blank right pane when
  // there's something to read. A later poll never overrides a reader's own
  // selection.
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

  // The plumbing this turn generated (memory_loaded, tool calls, …) — hidden
  // from the list, shown here as the selected item's own detail. Excludes
  // the selected row itself so its summary isn't repeated a second time.
  const traceSubEvents = selected ? trace.filter((t) => t.id && t.id !== selected.id) : [];

  // Genuinely loading (not yet resolved either way) is its OWN branch, not
  // folded into "empty" — the real content once resolved is the SPLIT
  // layout (list + detail pane), so the loading placeholder must render
  // inside that same two-column shell too, or the page visibly goes from
  // one full-width column to two the instant the fetch resolves.
  const stillLoading = loading && events.length === 0;
  const isEmptyState = !stillLoading && ((error && events.length === 0) || (events.length === 0 && freshWorkspace));

  const splitClassName = `fleet-content fleet-content--split${mobileDetailOpen ? " fleet-inbox--detail-open" : ""}`;

  return (
    <main className={isEmptyState ? "fleet-content" : splitClassName}>
      {stillLoading ? (
        <>
          {/* rowHeight matches .fleet-inbox-row's real height (12px top/bottom
              padding + a 13px title line + the 1px divider = ~44px) — see
              FleetListSkeleton's MAN-113 note; an un-pinned skeleton row snaps
              taller the moment the real inbox list swaps in. Rendered inside
              the real `.fleet-inbox-list`/`.fleet-inbox-detail` split shell
              (not a full-width standalone list) since that's the shape this
              page always resolves into once data arrives — a reader "lands
              on the top item" (see the effect above), so the detail pane
              gets its own placeholder too, not a blank void. */}
          <div className="fleet-inbox-list" aria-busy="true" aria-label="Loading inbox">
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
      ) : error && events.length === 0 ? (
        <FleetSurfaceError title="Couldn’t load your inbox" message={error} />
      ) : events.length === 0 && freshWorkspace ? (
        <CreateFirstAgentEmpty
          workspaceId={workspaceId}
          onCreated={refreshAgents}
          title="Your inbox is empty"
          desc="This is where your agents' activity and anything needing your attention shows up. Create your first agent to get started."
        />
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
                <div className="fleet-work-empty-title">You’re all caught up</div>
                <div className="fleet-work-empty-desc">
                  Nothing needs your attention right now. As your agents work, meaningful activity shows up here.
                </div>
              </div>
            ) : (
              <>
                <button type="button" className="fleet-inbox-back" onClick={() => setMobileDetailOpen(false)}>
                  <ChevronLeft size={16} strokeWidth={2} />
                  Inbox
                </button>
                {/* MAN-145 title-dedup follow-up: the breadcrumb's current
                    crumb ("Inbox") is this page's real <h1> now (see
                    Breadcrumbs.tsx) — a second <h1> here, for the selected
                    EVENT's own title, would be a second top-level heading on
                    the page (different text, but still two h1s). Demoted to
                    <h2>: this is the detail pane's own document header (the
                    master-detail analog of TaskDetailView's task title), a
                    real heading one rank below the page's, not chrome. */}
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
  );
}
