"use client";

import { useParams } from "next/navigation";
import { AlertTriangle, Inbox as InboxIcon } from "lucide-react";

import { useFleetAgents, useWorkspaceActivity } from "@/lib/workspace/fleet/fleet-data";
import { CreateFirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";

// One workspace-wide feed: activity ledger + escalations + notifications,
// newest first. Replaces the old activity / tasks / notifications panes.
function isEscalation(e: any): boolean {
  const s = `${e?.event_class || ""} ${e?.action || ""}`.toLowerCase();
  return s.includes("escalat") || s.includes("review") || s.includes("approval");
}

export default function InboxPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const { events, loading, error } = useWorkspaceActivity(workspaceId, 50);
  const { agents, loading: agentsLoading, refresh: refreshAgents } = useFleetAgents(workspaceId);
  const freshWorkspace = !agentsLoading && agents.length === 0;

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Inbox</h1>
          <p className="fleet-subtitle">Everything your agents did — escalations, activity, and alerts, newest first.</p>
        </div>
      </div>

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
      ) : events.length === 0 ? (
        <div className="fleet-work-empty">
          <InboxIcon size={26} strokeWidth={1.5} />
          <div className="fleet-work-empty-title">Nothing here yet</div>
          <div className="fleet-work-empty-desc">
            As your agents handle work, their activity and anything needing your
            attention will show up here.
          </div>
        </div>
      ) : (
        <div className="fleet-list">
          {events.map((event: any) => {
            const esc = isEscalation(event);
            return (
              <div key={event.event_id || event.id || event.created_at} className="fleet-list-row" style={{ cursor: "default" }}>
                {esc ? (
                  <AlertTriangle size={14} strokeWidth={1.75} style={{ color: "var(--accent)", flexShrink: 0 }} />
                ) : (
                  <span className="fleet-detail-dot" style={{ background: "var(--text-muted)" }} />
                )}
                <span className="fleet-list-row-main">
                  <span className="fleet-list-row-title">{event.title || event.action || "Event"}</span>
                  <span className="fleet-list-row-desc">
                    {event.event_class}
                    {event.action ? ` · ${event.action}` : ""}
                    {event.status ? ` · ${event.status}` : ""}
                  </span>
                </span>
                <span className="fleet-list-row-meta">
                  {event.created_at ? new Date(event.created_at).toLocaleString() : ""}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </main>
  );
}
