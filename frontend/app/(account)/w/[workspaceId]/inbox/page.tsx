"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, Inbox as InboxIcon } from "lucide-react";

import { fetchActivityTrace, markInboxSeenNow, useFleetAgents, useWorkspaceActivity, type WorkspaceActivityEvent } from "@/lib/workspace/fleet/fleet-data";
import { timeAgo } from "@/lib/workspace/fleet/fleet-presentation";
import { CreateFirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";

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
  const { agents, loading: agentsLoading, refresh: refreshAgents } = useFleetAgents(workspaceId);
  const freshWorkspace = !agentsLoading && agents.length === 0;

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

  const isEmptyState = (loading && events.length === 0) || (error && events.length === 0) || (events.length === 0 && freshWorkspace);

  return (
    <main className={isEmptyState ? "fleet-content" : "fleet-content fleet-content--split"}>
      {loading && events.length === 0 ? (
        <FleetListSkeleton rows={6} />
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
                  onClick={() => selectRow(event)}
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
                <h1 className="fleet-inbox-detail-title">{selected.title || selected.action || "Event"}</h1>
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
