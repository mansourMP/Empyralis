"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ClipboardList } from "lucide-react";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";
import { useFleetAgents } from "@/lib/workspace/fleet/fleet-data";
import { agentDisplayLabel, timeAgo, WORKSPACE_ASSISTANT_LABEL } from "@/lib/workspace/fleet/fleet-presentation";
import { FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import {
  filterWorkLedgerRows,
  parseWorkLedgerAgentRef,
  planWorkLedgerView,
  sortWorkLedgerRows,
  workLedgerDetailHref,
  workLedgerStatus,
  workLedgerStatusCounts,
  workLedgerSurfaceLabel,
  WORK_LEDGER_STATUS_LABEL,
  WORK_LEDGER_STATUS_COLOR_VAR,
  type WorkLedgerStatus,
  type WorkLedgerStatusFilter,
  type WorkLedgerTraceShape,
} from "@/lib/workspace/fleet/work-ledger";

/**
 * THE WORK LEDGER — "what have all my agents actually done," across every
 * agent, every surface, in one place. See work-ledger.ts's own header for
 * the full trace: why GET /api/agent-traces (routes_agent_traces.py) is the
 * real, already-mounted, already-written source; why this file calls it via
 * plain fleetAuthorizedFetch rather than the orphaned workstation-client.ts
 * factory; and the exact citations for every field this reads.
 *
 * ONE COLUMN, NOT A MASTER-DETAIL SPLIT (unlike WorkTab/Conversations):
 * there is no page that opens one trace by id (only the API does — see
 * work-ledger.ts), so a row's own destination is the agent's Work tab, an
 * ordinary navigation, not a second pane on this page. Reuses
 * .fleet-inbox-row's proven row shape (dot + title + time) under this
 * surface's own full-width list wrapper — see fleet-theme.css's
 * .fleet-work-ledger-list rule for why a fresh reset was needed rather than
 * borrowing .fleet-inbox-needsyou-section's identical one.
 */

const POLL_MS = 15_000;
const FETCH_LIMIT = 100;

type LedgerTrace = WorkLedgerTraceShape & { id: string };

function useWorkspaceAgentTraces(workspaceId: string) {
  const [rows, setRows] = useState<LedgerTrace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const res = await fleetAuthorizedFetch(
        `/api/agent-traces?${new URLSearchParams({ workspace_id: workspaceId, limit: String(FETCH_LIMIT) })}`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items: LedgerTrace[] = Array.isArray(data?.items) ? data.items : [];
      setRows(items);
      setError(null);
    } catch (e) {
      // Keep the last-good rows on a poll failure — same posture
      // useWorkspaceActivity already takes — and let planWorkLedgerView
      // decide what that means for the render (error wins over an already-
      // empty rows array, never silently reread as "no work yet").
      setError(e instanceof Error ? e.message : "Could not load the work ledger");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    setLoading(true);
    void refresh();
    const interval = setInterval(refresh, POLL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  return { rows, loading, error, refresh };
}

const STATUS_FILTERS: WorkLedgerStatusFilter[] = ["all", "working", "waiting", "failed", "done"];

function StatusDot({ status }: { status: WorkLedgerStatus }) {
  return (
    <span
      className="fleet-work-ledger-dot"
      style={{ background: `var(${WORK_LEDGER_STATUS_COLOR_VAR[status]})` }}
      aria-hidden="true"
    />
  );
}

export function WorkLedgerView({ workspaceId }: { workspaceId: string }) {
  const { rows, loading, error, refresh } = useWorkspaceAgentTraces(workspaceId);
  const { agents } = useFleetAgents(workspaceId);
  const [statusFilter, setStatusFilter] = useState<WorkLedgerStatusFilter>("all");

  // Agent name resolution mirrors ConversationsView.tsx's own agentLabel
  // exactly (same fallback to "Unnamed agent" via agentDisplayLabel(undefined))
  // — the one difference is the extra parse step, because a trace's
  // root_agent_id is a routing tag ("specialist:{id}" / a bare assistant
  // literal), never a bare install id the way a conversation's agent_id is.
  const agentNameFor = useCallback(
    (rootAgentId: string | null | undefined): string => {
      const ref = parseWorkLedgerAgentRef(rootAgentId);
      if (ref.kind === "assistant") return WORKSPACE_ASSISTANT_LABEL;
      const agent = agents.find((a) => a.agent_id === ref.installId);
      return agentDisplayLabel(agent);
    },
    [agents],
  );

  const sorted = useMemo(() => sortWorkLedgerRows(rows), [rows]);
  const counts = useMemo(() => workLedgerStatusCounts(sorted), [sorted]);
  const filtered = useMemo(() => filterWorkLedgerRows(sorted, statusFilter), [sorted, statusFilter]);

  const view = planWorkLedgerView({ loading, error, rows: sorted });

  if (view.kind === "loading") {
    return (
      <main className="fleet-content fleet-content--wide">
        <div className="fleet-work-ledger-list" aria-busy="true" aria-label="Loading the work ledger">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="fleet-inbox-row" style={{ cursor: "default" }}>
              <span className="fleet-skeleton-bar" style={{ width: 7, height: 7, borderRadius: 999 }} />
              <span className="fleet-skeleton-bar" style={{ width: `${45 + (i % 3) * 15}%`, height: 12 }} />
              <span className="fleet-skeleton-bar" style={{ width: 30, height: 11, marginLeft: "auto" }} />
            </div>
          ))}
        </div>
      </main>
    );
  }

  if (view.kind === "error") {
    return (
      <main className="fleet-content fleet-content--wide">
        <FleetSurfaceError title="Couldn’t load the work ledger" message={view.message} onRetry={refresh} />
      </main>
    );
  }

  if (view.kind === "empty") {
    return (
      <main className="fleet-content fleet-content--wide">
        <div className="fleet-work-empty">
          <ClipboardList size={26} strokeWidth={1.5} />
          <div className="fleet-work-empty-title">No work yet</div>
          <div className="fleet-work-empty-desc">
            As your agents run — on chat, a channel, or a task — every run shows up here: which agent, where, when, and how it ended.
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="fleet-content fleet-content--wide">
      <div className="fleet-segmented" role="tablist" aria-label="Filter by status">
        {STATUS_FILTERS.map((filter) => (
          <button
            key={filter}
            type="button"
            role="tab"
            aria-selected={statusFilter === filter}
            className={`fleet-segmented-btn${statusFilter === filter ? " fleet-segmented-btn--active" : ""}`}
            onClick={() => setStatusFilter(filter)}
          >
            {filter === "all" ? `All · ${sorted.length}` : `${WORK_LEDGER_STATUS_LABEL[filter]} · ${counts[filter]}`}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="fleet-work-empty">
          <ClipboardList size={26} strokeWidth={1.5} />
          <div className="fleet-work-empty-title">Nothing in this filter</div>
          <div className="fleet-work-empty-desc">No runs currently read as “{WORK_LEDGER_STATUS_LABEL[statusFilter as Exclude<WorkLedgerStatusFilter, "all">] || "this"}.”</div>
        </div>
      ) : (
        <div className="fleet-work-ledger-list">
          {filtered.map((row) => {
            const status = workLedgerStatus(row);
            const who = agentNameFor(row.root_agent_id);
            const surface = workLedgerSurfaceLabel(row.surface);
            const detail = `${surface} · ${WORK_LEDGER_STATUS_LABEL[status]}`;
            const href = workLedgerDetailHref({ workspaceId, rootAgentId: row.root_agent_id });
            const content = (
              <>
                <StatusDot status={status} />
                <span className="fleet-inbox-row-title">
                  {who}
                  <span className="fleet-inbox-row-detail"> — {detail}</span>
                </span>
                <span className="fleet-inbox-row-time">{row.started_at ? timeAgo(row.started_at) : ""}</span>
              </>
            );
            return href ? (
              <Link key={row.id} href={href} className="fleet-inbox-row">
                {content}
              </Link>
            ) : (
              <div key={row.id} className="fleet-inbox-row fleet-inbox-row--static">
                {content}
              </div>
            );
          })}
        </div>
      )}
    </main>
  );
}
