"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import {
  accountDetailHasActivity,
  formatAgentRow,
  formatMemberRow,
  formatSpendRow,
  runsByOutcomeSorted,
  totalPlatformCost,
  type AccountDetail,
} from "@/lib/account-detail";
import { EmptyState, ErrorState, ForbiddenState, LoadingState } from "@/lib/components/PageStates";
import { formatDate, formatTimestamp } from "@/lib/format";
import { formatUsd } from "@/lib/money";
import { useOperatorResource } from "@/lib/use-operator-resource";
import { planOperatorView } from "@/lib/view-state";

export default function AccountDetailPage() {
  const params = useParams<{ workspaceId: string }>();
  const workspaceId = params.workspaceId;
  const { data, status, loading, error, refresh } = useOperatorResource<AccountDetail>(
    `/api/operator/accounts/${encodeURIComponent(workspaceId)}`,
  );
  const view = planOperatorView({ loading, status, error, data });

  return (
    <main className="ops-main">
      <Link href="/accounts" className="ops-back-link">
        ← All accounts
      </Link>

      {view.kind === "loading" && <LoadingState rows={2} />}
      {view.kind === "forbidden" && <ForbiddenState />}
      {view.kind === "error" && (
        <ErrorState
          title="Couldn't load this account"
          message={status === 404 ? "This workspace no longer exists." : view.message}
          onRetry={refresh}
        />
      )}

      {view.kind === "ready" && <AccountDetailView detail={view.data} onRetry={refresh} />}
    </main>
  );
}

function AccountDetailView({ detail, onRetry }: { detail: AccountDetail; onRetry: () => void }) {
  const activated = accountDetailHasActivity(detail);
  const totalCost = totalPlatformCost(detail.spend_breakdown);
  const runOutcomes = runsByOutcomeSorted(detail.runs_by_outcome);

  return (
    <>
      <div className="ops-detail-header">
        <div>
          <h1 className="ops-page-title">
            <span className={`ops-activity-dot${activated ? " ops-activity-dot--active" : ""}`} aria-hidden="true" />
            {detail.name || detail.workspace_id}
          </h1>
          <p className="ops-page-subtitle">
            Owner {detail.owner.email || detail.owner.display_name || "—"} · Created {formatDate(detail.created_at)} ·{" "}
            {detail.workspace_status || "unknown status"}
          </p>
        </div>
        <button type="button" className="ops-btn" onClick={onRetry}>
          Refresh
        </button>
      </div>

      <div className="ops-stat-grid" style={{ marginBottom: "var(--space-2)" }}>
        <div className="ops-stat-card">
          <div className="ops-stat-value">{detail.members.length}</div>
          <div className="ops-stat-label">Members</div>
        </div>
        <div className="ops-stat-card">
          <div className="ops-stat-value">{detail.projects.length}</div>
          <div className="ops-stat-label">Projects</div>
        </div>
        <div className="ops-stat-card">
          <div className="ops-stat-value">{detail.agents.length}</div>
          <div className="ops-stat-label">Agents</div>
        </div>
        <div className={`ops-stat-card${activated ? " ops-stat-card--activation" : ""}`}>
          <div className="ops-stat-value">{formatUsd(totalCost)}</div>
          <div className="ops-stat-label">Total platform cost</div>
        </div>
      </div>

      <div className="ops-detail-grid">
        <section>
          <h2 className="ops-section-title">Members</h2>
          {detail.members.length === 0 ? (
            <EmptyState>No members.</EmptyState>
          ) : (
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Role</th>
                    <th>Last active</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.members.map((m) => {
                    const row = formatMemberRow(m);
                    return (
                      <tr key={row.userId}>
                        <td>{row.label}</td>
                        <td className="ops-cell-muted">{row.role}</td>
                        <td className={row.everActive ? "" : "ops-cell-muted"}>{row.lastActiveAt}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section>
          <h2 className="ops-section-title">Agents</h2>
          {detail.agents.length === 0 ? (
            <EmptyState>No agents installed.</EmptyState>
          ) : (
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Kind</th>
                    <th>Last run</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.agents.map((a) => {
                    const row = formatAgentRow(a);
                    return (
                      <tr key={row.id}>
                        <td>
                          <span className={`ops-activity-dot${row.hasRun ? " ops-activity-dot--active" : ""}`} aria-hidden="true" />
                          {row.label}
                        </td>
                        <td className="ops-cell-muted">{row.kind}</td>
                        <td className={row.hasRun ? "" : "ops-cell-muted"}>{row.lastRunAt}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section>
          <h2 className="ops-section-title">Projects</h2>
          {detail.projects.length === 0 ? (
            <EmptyState>No projects.</EmptyState>
          ) : (
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Tasks</th>
                    <th>Documents</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.projects.map((p) => (
                    <tr key={p.id}>
                      <td>
                        {p.name}
                        {p.archived && (
                          <span className="ops-pill" style={{ marginLeft: 6 }}>
                            archived
                          </span>
                        )}
                      </td>
                      <td>{p.task_count}</td>
                      <td>{p.document_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section>
          <h2 className="ops-section-title">Runs, by outcome</h2>
          {runOutcomes.length === 0 ? (
            <EmptyState>No agent runs yet.</EmptyState>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
              {runOutcomes.map((o) => (
                <span className="ops-pill" key={o.outcome}>
                  {o.outcome}: {o.count}
                </span>
              ))}
            </div>
          )}
        </section>

        <section>
          <h2 className="ops-section-title">Spend breakdown</h2>
          {detail.spend_breakdown.length === 0 ? (
            <EmptyState>No spend recorded.</EmptyState>
          ) : (
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Events</th>
                    <th>Platform cost</th>
                    <th>Credits</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.spend_breakdown.map((s, i) => {
                    const row = formatSpendRow(s);
                    return (
                      <tr key={`${row.label}-${i}`}>
                        <td>{row.label}</td>
                        <td>{row.eventCount}</td>
                        <td>{row.platformCost}</td>
                        <td>{row.creditsDebited}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section>
          <h2 className="ops-section-title">Recent failures</h2>
          {detail.recent_failures.length === 0 ? (
            <EmptyState>No recent failures.</EmptyState>
          ) : (
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Domain</th>
                    <th>Status</th>
                    <th>Error code</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.recent_failures.map((f) => (
                    <tr key={f.id}>
                      <td className="ops-cell-muted">{formatTimestamp(f.created_at)}</td>
                      <td>{f.action_domain || "unknown domain"}</td>
                      <td>{f.status}</td>
                      <td className="ops-cell-muted">{f.error_code || "no error code"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <h2 className="ops-section-title">Recent activity</h2>
      {detail.recent_activity.length === 0 ? (
        <EmptyState>No recent activity.</EmptyState>
      ) : (
        <div className="ops-table-wrap">
          <table className="ops-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Event</th>
                <th>Status</th>
                <th>Title</th>
              </tr>
            </thead>
            <tbody>
              {detail.recent_activity.map((e) => (
                <tr key={e.id}>
                  <td className="ops-cell-muted">{formatTimestamp(e.created_at)}</td>
                  <td className="ops-cell-muted">{e.actor_type || "—"}</td>
                  <td>{e.event_class || e.action || "—"}</td>
                  <td>{e.status || "—"}</td>
                  <td>{e.title || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
