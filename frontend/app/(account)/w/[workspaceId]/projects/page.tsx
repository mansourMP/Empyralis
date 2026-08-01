"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Bot, Loader2, X } from "lucide-react";

import { useFleetAgents, useFleetProjects, useFleetWorkspace, type FleetProject } from "@/lib/workspace/fleet/fleet-data";
import { HeaderAction, useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { breadcrumbCount, deriveStatus, findSageAgent, formatNumber, timeAgo, type AgentStatusTone } from "@/lib/workspace/fleet/fleet-presentation";
// deriveStatus is used below (statsByProject) to classify each agent's tone
// before summarizeStatus() rolls the counts up into one line.
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { CreateFirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow } from "@/lib/workspace/fleet/FleetRightPanel";
import { UsageStat, bucketSeries, type UsageBucket } from "@/lib/workspace/fleet/fleet-sparkline";

// 4 decimal places — same convention as AgentsList/FleetAgentDetail/project
// detail's money(): real per-turn costs are fractions of a cent.
const money = (n: number) => `$${n.toFixed(4)}`;

type ProjectStats = {
  agents: number;
  cost: number;
  tokens: number;
  lastActive: string | null;
  statusCounts: Partial<Record<AgentStatusTone, number>>;
};

// Working, then anything that needs a look, then the calm states — the
// read-top-to-bottom-by-urgency order a reader would want from a summary
// line. Labels are deriveStatus()'s own five words lowercased (plus its
// "Not deployed" for the unknown tone) so this never drifts from the one
// status vocabulary every table already shares. "online" never appears
// here — deriveStatus never returns it, that tone is Hardware's own
// device-reachability chip, a different domain.
const STATUS_SUMMARY_ORDER: { tone: AgentStatusTone; label: string }[] = [
  { tone: "working", label: "working" },
  { tone: "error", label: "error" },
  { tone: "stopped", label: "stopped" },
  { tone: "offline", label: "offline" },
  { tone: "ready", label: "ready" },
  { tone: "unknown", label: "not deployed" },
];

// "4 ready" / "1 working · 3 ready" — every non-zero tone among a project's
// agents, most-urgent first.
function summarizeStatus(counts: Partial<Record<AgentStatusTone, number>>): string {
  return STATUS_SUMMARY_ORDER
    .map(({ tone, label }) => {
      const n = counts[tone] ?? 0;
      return n > 0 ? `${n} ${label}` : null;
    })
    .filter((s): s is string => Boolean(s))
    .join(" · ");
}
type SortMode = "last_active" | "cost" | "agents" | "name";
const SORT_OPTIONS = [
  { value: "last_active", label: "Last active" },
  { value: "cost", label: "Cost" },
  { value: "agents", label: "Agent count" },
  { value: "name", label: "Name" },
];

type FilterState = { agents: string; sort: SortMode };

// Same URL-backed view state as the Agents/Project-detail lists — so
// browser-back restores the filtered/sorted view instead of resetting it.
function readFiltersFromLocation(): FilterState {
  if (typeof window === "undefined") return { agents: "all", sort: "name" };
  const sp = new URLSearchParams(window.location.search);
  return {
    agents: sp.get("agents") || "all",
    sort: (sp.get("sort") as SortMode) || "name",
  };
}

export default function ProjectsPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const { projects, loading, error, refresh } = useFleetProjects(workspaceId);
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const [dialogOpen, setDialogOpen] = useState(false);

  // U3-E: the count lives on the breadcrumb line itself ("Projects · 1
  // project"), not a second toolbar row.
  useBreadcrumbBadge(
    "projects",
    useMemo(
      () => <span className="fleet-breadcrumb-count">· {breadcrumbCount(projects.length, "project", "projects", "Projects")}</span>,
      [projects.length],
    ),
  );

  const [filterState, setFilterState] = useState<FilterState>(() => readFiltersFromLocation());
  const { agents: agentsFilter, sort } = filterState;

  const updateFilters = useCallback((patch: Partial<FilterState>) => {
    setFilterState((prev) => {
      const next = { ...prev, ...patch };
      const sp = new URLSearchParams();
      if (next.agents !== "all") sp.set("agents", next.agents);
      if (next.sort !== "name") sp.set("sort", next.sort);
      const qs = sp.toString();
      router.replace(`${base}/projects${qs ? `?${qs}` : ""}`);
      return next;
    });
  }, [router, base]);

  // Command-palette hand-off: /projects?new=1 lands straight in the "New
  // project" dialog — same convention as agents/page.tsx's ?new=1.
  const consumedNew = useRef(false);
  useEffect(() => {
    if (consumedNew.current) return;
    if (new URLSearchParams(window.location.search).get("new") === "1") {
      consumedNew.current = true;
      setDialogOpen(true);
      router.replace(`${base}/projects`);
    }
  }, [router, base]);

  // Agents/Cost/Tokens/Last-active columns are all derived client-side from
  // data that already exists elsewhere (agents carry project_id; the
  // workspace usage rollup carries per-agent cost+tokens) — no new endpoint.
  // Same Sage exclusion as the Agents list: the Operator never counts as a
  // row anywhere, so a project's agent count matches what you'd see if you
  // clicked into it.
  const { agents: allAgents } = useFleetAgents(workspaceId);
  const sageAgent = useMemo(() => findSageAgent(allAgents), [allAgents]);
  const agents = useMemo(
    () => (sageAgent ? allAgents.filter((a) => a.agent_id !== sageAgent.agent_id) : allAgents),
    [allAgents, sageAgent],
  );

  const [usageByAgent, setUsageByAgent] = useState<Map<string, { cost: number; tokens: number }>>(new Map());
  // Same /fleet/usage response the per-agent cost map is built from — the
  // `totals`/`buckets` blocks feed the restored properties drawer below, so
  // both live off this one fetch instead of a second request.
  const [workspaceTotals, setWorkspaceTotals] = useState<{ usd_cost?: number; total_tokens?: number } | null>(null);
  const [workspaceBuckets, setWorkspaceBuckets] = useState<UsageBucket[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const m = new Map<string, { cost: number; tokens: number }>();
        for (const a of d?.by_agent || []) {
          m.set(a.agent_install_id, { cost: Number(a.usd_cost) || 0, tokens: Number(a.total_tokens) || 0 });
        }
        setUsageByAgent(m);
        setWorkspaceTotals(d?.totals || null);
        if (Array.isArray(d?.buckets)) setWorkspaceBuckets(d.buckets);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);
  // Properties drawer — closed by default, an overlay over the sheet, same
  // contract as Agents/Project-detail's drawer.
  const [panelOpen, setPanelOpen] = useState(false);

  const statsByProject = useMemo(() => {
    const m = new Map<string, ProjectStats>();
    for (const a of agents) {
      const pid = (a.project_id || "").trim();
      if (!pid) continue;
      const cur = m.get(pid) || { agents: 0, cost: 0, tokens: 0, lastActive: null, statusCounts: {} };
      cur.agents += 1;
      const u = usageByAgent.get(a.agent_id);
      if (u) {
        cur.cost += u.cost;
        cur.tokens += u.tokens;
      }
      if (a.last_activity && (!cur.lastActive || new Date(a.last_activity) > new Date(cur.lastActive))) {
        cur.lastActive = a.last_activity;
      }
      const tone = deriveStatus(a.hardware_status || "unknown", Boolean(a.stopped?.active), Boolean(a.current_run_id)).tone;
      cur.statusCounts[tone] = (cur.statusCounts[tone] ?? 0) + 1;
      m.set(pid, cur);
    }
    return m;
  }, [agents, usageByAgent]);

  const filters: ToolbarFilter[] = [
    {
      key: "agents", label: "Agents", value: agentsFilter, onChange: (v) => updateFilters({ agents: v }),
      options: [
        { value: "all", label: "All projects" },
        { value: "has_agents", label: "Has agents" },
        { value: "empty", label: "Empty" },
      ],
    },
  ];

  const filtered = useMemo(() => projects.filter((p) => {
    const count = statsByProject.get(p.id)?.agents ?? 0;
    if (agentsFilter === "has_agents" && count === 0) return false;
    if (agentsFilter === "empty" && count > 0) return false;
    return true;
  }), [projects, statsByProject, agentsFilter]);
  const shown = useMemo(() => sortProjects(filtered, sort, statsByProject), [filtered, sort, statsByProject]);

  return (
    <main className="fleet-content fleet-content--with-panel">
      {/* MAN-145 title-dedup follow-up: this used to render "Projects" three
          times (tab strip, breadcrumb, and this block's own <h1>). The
          breadcrumb's current crumb IS the page's <h1> now (see
          Breadcrumbs.tsx) — it already carries the "· N" count the plain
          title never did, so it's the better survivor. This block is gone,
          not replaced with a styled div: the heading role lives one layer
          up, it isn't lost. */}

      {/* U3-H: top row is breadcrumb (with its count, see useBreadcrumbBadge
          above) + primary action only. The view-control cluster is its own
          row below, under the topbar's existing divider — same FleetToolbar
          cluster the Agents and Project-detail pages use, so the corner
          can't drift, just no longer sharing the top line with the
          breadcrumb (U3-E's "one header bar" reading was wrong). */}
      <HeaderAction>
        <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setDialogOpen(true)}>
          <span className="fleet-btn-plus">+</span>
          New project
        </button>
      </HeaderAction>

      <div className="fleet-content-toolbar">
        <FleetToolbar
          filters={projects.length > 0 ? filters : undefined}
          sortOptions={projects.length > 0 ? SORT_OPTIONS : undefined}
          sortValue={sort}
          sortDefault="name"
          onSortChange={(v) => updateFilters({ sort: v as SortMode })}
          panelOpen={panelOpen}
          onTogglePanel={() => setPanelOpen((v) => !v)}
          usageWorkspaceId={workspaceId}
        />
      </div>

      <div className="fleet-content-with-panel">
        <div className="fleet-content-main">
          {loading && projects.length === 0 ? (
            // rowHeight matches .fleet-project-row's real min-height (52px) —
            // see FleetListSkeleton's MAN-113 note; an un-pinned skeleton row
            // snaps taller the moment the projects list swaps in.
            <FleetListSkeleton rows={4} rowHeight={52} />
          ) : error && projects.length === 0 ? (
            <FleetSurfaceError title="Couldn’t load projects" message={error} onRetry={refresh} />
          ) : projects.length === 0 ? (
            <CreateFirstAgentEmpty
              workspaceId={workspaceId}
              onCreated={refresh}
              title="No projects yet"
              desc="Projects keep your agents organized. Create your first agent and its project is set up for you."
            />
          ) : shown.length === 0 ? (
            <div className="fleet-page-state-body">No projects match these filters.</div>
          ) : (
            <div className="fleet-projects-list">
              <div className="fleet-projects-list-header" aria-hidden>
                <span>Project</span>
                <span className="is-right fleet-col-agents-count">Agents</span>
                <span className="is-right">Cost this month</span>
                <span className="is-right fleet-col-tokens">Tokens</span>
                <span className="is-right fleet-col-last-active">Last active</span>
                <span className="is-right">Status</span>
              </div>
              {shown.map((p) => {
                const stats = statsByProject.get(p.id);
                const agentsCount = stats?.agents ?? 0;
                const cost = stats?.cost ?? 0;
                const tokens = stats?.tokens ?? 0;
                const lastActive = stats?.lastActive ?? null;
                const statusSummary = stats ? summarizeStatus(stats.statusCounts) : "";
                return (
                  // data-tab-title: a ⌘-clicked row opens a background content
                  // tab (see FleetTabs), and without this the new tab is
                  // titled "Project" until someone actually visits it — the
                  // breadcrumb name registry only learns a project's name from
                  // the project's own page.
                  <Link key={p.id} href={`${base}/projects/${encodeURIComponent(p.id)}`} className="fleet-project-row" data-tab-title={p.name}>
                    <span className="fleet-project-cell-name">
                      <ProjectIcon icon={p.icon} tint={p.tint} />
                      <span className="fleet-project-cell-name-text">
                        <span className="fleet-project-cell-name-title">{p.name || p.id}</span>
                        {p.description && <span className="fleet-project-cell-name-desc">{p.description}</span>}
                        <span className="fleet-agent-meta-mobile">
                          {`${agentsCount} ${agentsCount === 1 ? "agent" : "agents"} · ${formatNumber(tokens)} tok · ${lastActive ? timeAgo(lastActive) : "never"}`}
                        </span>
                      </span>
                    </span>
                    {/* Cost this month is the one column that carries full
                        emphasis (Linear discipline: one value per row, not
                        five at equal weight) — Agents/Tokens/Last active/
                        Status all dim to fleet-cell-secondary. A zero/empty
                        value still drops further to fleet-cell-muted, same
                        as before. */}
                    <span className={`fleet-agent-cell-right fleet-col-agents-count fleet-cell-secondary${agentsCount > 0 ? "" : " fleet-cell-muted"}`}>{agentsCount}</span>
                    <span className={`fleet-agent-cell-right${cost > 0 ? "" : " fleet-cell-muted"}`}>{money(cost)}</span>
                    <span className={`fleet-agent-cell-right fleet-col-tokens fleet-cell-secondary${tokens > 0 ? "" : " fleet-cell-muted"}`}>{formatNumber(tokens)}</span>
                    <span className={`fleet-agent-cell-right fleet-col-last-active fleet-cell-secondary${lastActive ? "" : " fleet-cell-muted"}`}>
                      {lastActive ? timeAgo(lastActive) : "never"}
                    </span>
                    <span className={`fleet-agent-cell-right fleet-cell-secondary${statusSummary ? "" : " fleet-cell-muted"}`}>
                      {statusSummary || "—"}
                    </span>
                  </Link>
                );
              })}
            </div>
          )}
        </div>

        <FleetRightPanel open={panelOpen} onClose={() => setPanelOpen(false)}>
          <PanelSection title="Properties">
            <PanelRow label="Projects" value={projects.length} />
            <PanelRow label="Agents" value={agents.length} icon={<Bot size={15} strokeWidth={1.75} />} />
            <UsageStat
              label="Spend today"
              total={workspaceTotals?.usd_cost ?? 0}
              formattedTotal={`$${(workspaceTotals?.usd_cost ?? 0).toFixed(4)}`}
              values={bucketSeries(workspaceBuckets, "usd_cost")}
            />
            <UsageStat
              label="Tokens today"
              total={workspaceTotals?.total_tokens ?? 0}
              formattedTotal={formatNumber(workspaceTotals?.total_tokens ?? 0)}
              values={bucketSeries(workspaceBuckets, "total_tokens")}
            />
          </PanelSection>
        </FleetRightPanel>
      </div>

      {dialogOpen && (
        <NewProjectDialog
          workspaceId={workspaceId}
          onClose={() => setDialogOpen(false)}
          onCreated={() => { setDialogOpen(false); refresh(); }}
        />
      )}
    </main>
  );
}

function sortProjects(projects: FleetProject[], sort: SortMode, stats: Map<string, ProjectStats>): FleetProject[] {
  const list = [...projects];
  if (sort === "agents") {
    list.sort((a, b) => (stats.get(b.id)?.agents ?? 0) - (stats.get(a.id)?.agents ?? 0));
  } else if (sort === "cost") {
    list.sort((a, b) => (stats.get(b.id)?.cost ?? 0) - (stats.get(a.id)?.cost ?? 0));
  } else if (sort === "last_active") {
    list.sort((a, b) => {
      const la = stats.get(a.id)?.lastActive;
      const lb = stats.get(b.id)?.lastActive;
      const ta = la ? new Date(la).getTime() : 0;
      const tb = lb ? new Date(lb).getTime() : 0;
      return tb - ta;
    });
  } else {
    list.sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  }
  return list;
}

/**
 * Full-panel project composer (replaces the old label-over-input small
 * dialog — founder's read: "mine looks like a table that I have to fill").
 * Same "paper, not a form" philosophy TaskComposer already established
 * (.fleet-composer-title/-desc: bare text entry, no border, placeholder as
 * the only label — see TaskComposer.tsx's own header comment for the full
 * rationale), just scaled to a near-full-viewport panel instead of a 640px
 * popover: a project is a bigger, rarer commitment than a task, and
 * Linear's own composer reads as a page, not a card.
 *
 * FleetCreateProjectRequest (server_modules/routes_fleet.py) accepts
 * exactly two fields: name and description. The `projects` table
 * (server_modules/control_plane_repository.py) has no status/priority/
 * lead/members/start/target/labels/dependencies/milestones column — icon
 * and tint exist but are assigned deterministically from the new row's id
 * server-side and were deliberately never made user-choosable (see
 * fleet-project-identity.ts's own comment on why the tint picker was
 * removed: "There is no picker anywhere for a human to choose it, so the
 * hue was never a decision"). So unlike TaskComposer there is no
 * property-chips row here — building chips for fields the backend
 * silently drops would be exactly the "control whose own label admits it
 * does nothing" CLAUDE.md rules out. Name (the heading) and description
 * (the dominant body) are the whole surface.
 */
function NewProjectDialog({
  workspaceId,
  onClose,
  onCreated,
}: {
  workspaceId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement | null>(null);
  const { workspace } = useFleetWorkspace(workspaceId);

  const create = useCallback(async () => {
    const clean = name.trim();
    if (!clean || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/projects`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ name: clean, description: description.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the project.");
    } finally {
      setBusy(false);
    }
  }, [busy, description, name, onCreated, workspaceId]);

  return (
    <div
      className="fleet-detail-backdrop"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          void create();
        }
      }}
    >
      <div
        className="fleet-project-composer"
        role="dialog"
        aria-modal="true"
        aria-label="New project"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="fleet-composer-head fleet-project-composer-head">
          <span className="fleet-composer-crumb">
            {workspace?.name ? <span className="fleet-composer-crumb-project">{workspace.name}</span> : null}
            {workspace?.name ? <span aria-hidden>›</span> : null}
            <span>New project</span>
          </span>
          <button type="button" className="fleet-composer-close" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        <div className="fleet-project-composer-body">
          {/* Real heading structure per CLAUDE.md craft doctrine — visually
              hidden because the big name input directly below IS the
              heading visually (its placeholder communicates intent exactly
              like Linear's "Project name" gray placeholder), but a
              dialog's accessible name still deserves a real h2, not just
              aria-label on the wrapping div. */}
          <h2 className="fleet-sr-only">New project</h2>
          <input
            ref={nameRef}
            className="fleet-composer-title fleet-project-composer-name"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            placeholder="Project name"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                e.preventDefault();
                void create();
              }
            }}
          />
          <textarea
            className="fleet-composer-desc fleet-project-composer-desc"
            value={description}
            onChange={(e) => setDescription(e.currentTarget.value)}
            placeholder="Write a description, a project brief, or collect ideas…"
          />
        </div>

        {error ? (
          <p className="fleet-project-composer-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="fleet-small-dialog-footer">
          <button type="button" className="fleet-btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => void create()} disabled={busy || !name.trim()}>
            {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Create project"}
          </button>
        </div>
      </div>
    </div>
  );
}
