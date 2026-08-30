"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import {
  ACCOUNT_COLUMN_LABELS,
  buildAccountsSearchParams,
  formatAccountRow,
  parseAccountsQuery,
  toggleAccountSort,
  type Account,
  type AccountSortColumn,
  type AccountsResponse,
} from "@/lib/accounts";
import { EmptyState, ErrorState, ForbiddenState, LoadingState } from "@/lib/components/PageStates";
import { SortableHeader } from "@/lib/components/SortableHeader";
import { formatDate } from "@/lib/format";
import { useOperatorResource } from "@/lib/use-operator-resource";
import { planOperatorView } from "@/lib/view-state";

const HAS_AGENTS_OPTIONS: { label: string; value: string }[] = [
  { label: "Any", value: "" },
  { label: "With agents", value: "true" },
  { label: "No agents", value: "false" },
];

const ACTIVE_WITHIN_OPTIONS: { label: string; value: string }[] = [
  { label: "Any time", value: "" },
  { label: "Last 24 hours", value: "1" },
  { label: "Last 7 days", value: "7" },
  { label: "Last 30 days", value: "30" },
  { label: "Last 90 days", value: "90" },
];

const MIN_MEMBERS_OPTIONS: { label: string; value: string }[] = [
  { label: "Any size", value: "" },
  { label: "2+ members", value: "2" },
  { label: "5+ members", value: "5" },
  { label: "10+ members", value: "10" },
];

// useSearchParams() needs a Suspense boundary above it (Next.js App Router
// requirement -- the URL isn't known during any static prerender pass) --
// the boundary lives here, inside this same client-component file, rather
// than splitting into a server-component wrapper file: Suspense itself is a
// React primitive, not something that requires a Server Component to use.
export default function AccountsPage() {
  return (
    <Suspense fallback={<main className="ops-main"><LoadingState rows={1} /></main>}>
      <AccountsPageInner />
    </Suspense>
  );
}

function AccountsPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const query = useMemo(() => parseAccountsQuery(new URLSearchParams(searchParams.toString())), [searchParams]);
  const apiUrl = useMemo(() => `/api/operator/accounts?${buildAccountsSearchParams(query).toString()}`, [query]);
  const { data, status, loading, error, refresh } = useOperatorResource<AccountsResponse>(apiUrl);
  const view = planOperatorView({ loading, status, error, data });

  // Debounced search box -- every other filter is a discrete select and
  // navigates immediately on change; free text needs a pause so each
  // keystroke doesn't fire its own fetch.
  const [searchDraft, setSearchDraft] = useState(query.search);
  useEffect(() => setSearchDraft(query.search), [query.search]);
  useEffect(() => {
    if (searchDraft === query.search) return;
    const handle = setTimeout(() => {
      pushQuery({ search: searchDraft, offset: 0 });
    }, 350);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft]);

  function pushQuery(patch: Partial<typeof query>) {
    const next = { ...query, ...patch };
    const params = buildAccountsSearchParams(next);
    router.push(`${pathname}${params.toString() ? `?${params.toString()}` : ""}`);
  }

  function handleSort(column: AccountSortColumn) {
    pushQuery(toggleAccountSort(query, column));
  }

  const accounts: Account[] = view.kind === "ready" ? view.data.accounts : [];
  const totalMatching = view.kind === "ready" ? view.data.total_matching : 0;
  const hasNextPage = view.kind === "ready" && query.offset + query.limit < totalMatching;
  const hasPrevPage = query.offset > 0;

  return (
    <main className="ops-main">
      <div className="ops-page-header">
        <div>
          <h1 className="ops-page-title">Accounts</h1>
          <p className="ops-page-subtitle">
            Every workspace on the platform, sortable and filterable — the primary instrument for &ldquo;who signed up
            and who is actually using it.&rdquo;
          </p>
        </div>
        {view.kind === "ready" && (
          <span className="ops-page-meta">
            {totalMatching.toLocaleString("en-US")} matching workspace{totalMatching === 1 ? "" : "s"}
          </span>
        )}
      </div>

      <div className="ops-filters">
        <input
          className="ops-input"
          type="text"
          placeholder="Search by workspace name"
          value={searchDraft}
          onChange={(e) => setSearchDraft(e.target.value)}
          aria-label="Search workspaces by name"
        />
        <span className="ops-filter-label">Members</span>
        <select
          className="ops-select"
          value={query.minMembers === null ? "" : String(query.minMembers)}
          onChange={(e) => pushQuery({ minMembers: e.target.value === "" ? null : Number(e.target.value), offset: 0 })}
        >
          {MIN_MEMBERS_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <span className="ops-filter-label">Agents</span>
        <select
          className="ops-select"
          value={query.hasAgents === null ? "" : String(query.hasAgents)}
          onChange={(e) => pushQuery({ hasAgents: e.target.value === "" ? null : e.target.value === "true", offset: 0 })}
        >
          {HAS_AGENTS_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <span className="ops-filter-label">Active</span>
        <select
          className="ops-select"
          value={query.activeWithinDays === null ? "" : String(query.activeWithinDays)}
          onChange={(e) => pushQuery({ activeWithinDays: e.target.value === "" ? null : Number(e.target.value), offset: 0 })}
        >
          {ACTIVE_WITHIN_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {view.kind === "loading" && <LoadingState rows={1} />}
      {view.kind === "forbidden" && <ForbiddenState />}
      {view.kind === "error" && <ErrorState message={view.message} onRetry={refresh} />}

      {view.kind === "ready" && (
        <>
          <div className="ops-table-wrap">
            <table className="ops-table">
              <thead>
                <tr>
                  <SortableHeader column="name" label={ACCOUNT_COLUMN_LABELS.name} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <th>Owner</th>
                  <SortableHeader column="created_at" label={ACCOUNT_COLUMN_LABELS.created_at} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <SortableHeader column="last_active_at" label={ACCOUNT_COLUMN_LABELS.last_active_at} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <SortableHeader column="member_count" label={ACCOUNT_COLUMN_LABELS.member_count} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <SortableHeader column="project_count" label={ACCOUNT_COLUMN_LABELS.project_count} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <SortableHeader column="task_count" label={ACCOUNT_COLUMN_LABELS.task_count} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <SortableHeader column="document_count" label={ACCOUNT_COLUMN_LABELS.document_count} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <SortableHeader column="agent_count" label={ACCOUNT_COLUMN_LABELS.agent_count} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <SortableHeader column="agents_with_runs" label={ACCOUNT_COLUMN_LABELS.agents_with_runs} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                  <SortableHeader column="total_platform_cost_usd" label={ACCOUNT_COLUMN_LABELS.total_platform_cost_usd} activeColumn={query.sortBy} direction={query.sortDir} onSort={handleSort} />
                </tr>
              </thead>
              <tbody>
                {accounts.length === 0 ? (
                  <tr>
                    <td colSpan={10}>
                      <EmptyState>No workspaces match these filters.</EmptyState>
                    </td>
                  </tr>
                ) : (
                  accounts.map((account) => {
                    const row = formatAccountRow(account);
                    return (
                      <tr key={row.workspaceId}>
                        <td>
                          <span className={`ops-activity-dot${row.hasActivity ? " ops-activity-dot--active" : ""}`} aria-hidden="true" />
                          <Link className="ops-row-link" href={`/accounts/${encodeURIComponent(row.workspaceId)}`}>
                            {row.name}
                          </Link>
                        </td>
                        <td className="ops-cell-muted">{row.ownerLabel}</td>
                        <td className="ops-cell-muted">{formatDate(row.createdAt)}</td>
                        <td className="ops-cell-muted">{formatDate(account.last_active_at, { never: "Never" })}</td>
                        <td>{row.memberCount}</td>
                        <td>{row.projectCount}</td>
                        <td>{row.taskCount}</td>
                        <td>{row.documentCount}</td>
                        <td>{row.agentCount}</td>
                        <td>{row.agentsWithRuns}</td>
                        <td>{row.platformCost}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <div className="ops-filters" style={{ marginTop: "var(--space-3)", marginBottom: 0, justifyContent: "flex-end" }}>
            <span className="ops-filter-label">
              {totalMatching === 0 ? "0 of 0" : `${query.offset + 1}–${Math.min(query.offset + query.limit, totalMatching)} of ${totalMatching}`}
            </span>
            <button
              type="button"
              className="ops-btn"
              disabled={!hasPrevPage}
              onClick={() => pushQuery({ offset: Math.max(0, query.offset - query.limit) })}
            >
              Previous
            </button>
            <button
              type="button"
              className="ops-btn"
              disabled={!hasNextPage}
              onClick={() => pushQuery({ offset: query.offset + query.limit })}
            >
              Next
            </button>
          </div>
        </>
      )}
    </main>
  );
}
