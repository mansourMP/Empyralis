"use client";

import { useParams } from "next/navigation";
import { AlertTriangle, Inbox as InboxIcon } from "lucide-react";

import { useWorkspaceActivity } from "@/lib/workspace/fleet/fleet-data";

// One workspace-wide feed: activity ledger + escalations + notifications,
// newest first. Replaces the old activity / tasks / notifications panes.
function isEscalation(e: any): boolean {
  const s = `${e?.event_class || ""} ${e?.action || ""}`.toLowerCase();
  return s.includes("escalat") || s.includes("review") || s.includes("approval");
}

export default function InboxPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const { events, loading } = useWorkspaceActivity(workspaceId, 50);

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Inbox</h1>
          <p className="fleet-subtitle">Everything your agents did — escalations, activity, and alerts, newest first.</p>
        </div>
      </div>

      {loading && events.length === 0 ? (
        <div className="fleet-list" aria-label="Loading activity">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="fleet-list-row"><div className="fleet-skeleton-bar" style={{ width: "40%", height: 12 }} /></div>
          ))}
        </div>
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
