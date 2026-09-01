"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Bot, FolderKanban, Loader2, X } from "lucide-react";

import { useFleetAgents, useFleetProjects, useFleetWorkspace } from "@/lib/workspace/fleet/fleet-data";
import { HeaderAction, useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import { breadcrumbCount, findSageAgent, formatNumber } from "@/lib/workspace/fleet/fleet-presentation";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { formatProjectWorkSummary } from "@/lib/workspace/fleet/project-work-summary";
import { composerSubmitButtonClass, createButtonClass } from "@/lib/workspace/fleet/create-accent";
import { planFirstAgentPrompt } from "@/lib/workspace/fleet/workspace-first-run";
import { FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow } from "@/lib/workspace/fleet/FleetRightPanel";
import { UsageStat, bucketSeries, type UsageBucket } from "@/lib/workspace/fleet/fleet-sparkline";
import { formatUsd } from "@/lib/ui/money";
import { CopyLinkButton } from "@/lib/ui/CopyLinkButton";

// Agents/Cost/Tokens/Last-active/Status columns — and the "Has agents" /
// "Empty" filter and the "Agent count"/"Cost"/"Last active" sort modes that
// used to sit beside them — are ALL GONE, 2026-08-30 (founder hard rule: "an
// agent is completely independent of any project... that's the hard rule!").
// Every one of them was derived client-side by grouping `agents` on
// `a.project_id` (a nullable, never-backfilled column — the comment that
// used to sit here literally said "agents carry project_id", stated as
// fact). That is not a project fact; it is an agent fact laundered through
// a project row, and cost/tokens/last-active/status all carried the same
// lie the count did, not just the count itself. What replaces them: the
// Work column below, built from `task_count`/`document_count` — real
// project-owned facts, already returned by this exact endpoint
// (routes_fleet.fleet_projects), via project-work-summary.ts (a pure
// module the 2026-08-19 workspace-home rebuild wrote and wired in, then
// orphaned the day FleetHome itself was deleted — this page is that
// module's first live caller since).
type FilterState = { state: string };

// Same URL-backed view state as the Agents/Project-detail lists — so
// browser-back restores the filtered view instead of resetting it.
function readFiltersFromLocation(): FilterState {
  if (typeof window === "undefined") return { state: "active" };
  const sp = new URLSearchParams(window.location.search);
  return { state: sp.get("state") || "active" };
}

export default function ProjectsPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const [dialogOpen, setDialogOpen] = useState(false);

  const [filterState, setFilterState] = useState<FilterState>(() => readFiltersFromLocation());
  const { state: stateFilter } = filterState;

  // Archive is only a real option if what you archived stays findable —
  // otherwise it is deletion with extra steps and no undo. The default view
  // is unchanged (active only, filtered server-side); switching to Archived
  // refetches with include_archived and shows just those, from where the
  // project's own settings popover offers Restore.
  const showArchived = stateFilter === "archived";
  const { projects: allProjects, loading, error, refresh } = useFleetProjects(workspaceId, showArchived);
  const projects = useMemo(
    () => (showArchived ? allProjects.filter((p) => p.archived) : allProjects),
    [allProjects, showArchived],
  );

  // U3-E: the count lives on the breadcrumb line itself ("Projects · 1
  // project"), not a second toolbar row.
  useBreadcrumbBadge(
    "projects",
    useMemo(
      () => <span className="fleet-breadcrumb-count">· {breadcrumbCount(projects.length, "project", "projects", "Projects")}</span>,
      [projects.length],
    ),
  );

  // The updater is now PURE, and the URL write is an effect on the result.
  // It used to call router.replace() from inside the setState updater, and
  // React may run an updater during render — so the first time this filter
  // row was actually exercised in a browser the console logged "Cannot
  // update a component (Router) while rendering a different component
  // (ProjectsPage)". Same URL-backed view state as before, same
  // .replace-not-.push; only where the navigation happens changed.
  const updateFilters = useCallback((patch: Partial<FilterState>) => {
    setFilterState((prev) => ({ ...prev, ...patch }));
  }, []);

  const filtersHydrated = useRef(false);
  useEffect(() => {
    // Skip the first pass: filterState was seeded FROM the URL, so writing
    // it back on mount is a redundant navigation.
    if (!filtersHydrated.current) {
      filtersHydrated.current = true;
      return;
    }
    const sp = new URLSearchParams();
    if (filterState.state !== "active") sp.set("state", filterState.state);
    const qs = sp.toString();
    router.replace(`${base}/projects${qs ? `?${qs}` : ""}`);
  }, [filterState, router, base]);

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

  // Workspace-wide agent facts only, never per-project: the Properties
  // drawer's own "Agents" total (a genuine workspace-scoped count). Same
  // Sage exclusion as the Agents list — the Operator never counts as a row
  // anywhere. Agent CREATION is not this page's business (see the header
  // action below) — this is read-only, for the Properties panel.
  const { agents: allAgents } = useFleetAgents(workspaceId);
  const sageAgent = useMemo(() => findSageAgent(allAgents), [allAgents]);
  const agents = useMemo(
    () => (sageAgent ? allAgents.filter((a) => a.agent_id !== sageAgent.agent_id) : allAgents),
    [allAgents, sageAgent],
  );

  // ── The zero-projects first-run state ─────────────────────────────────────
  // This page is where a brand-new customer LANDS (/w/{id} redirects here),
  // so it is the one screen that has to answer "how do I make my first
  // project" on a genuinely empty workspace — see workspace-first-run.ts for
  // the ("full" | "none") rule and for the agent-creation band that used to
  // also live here and was removed (founder ruling: a project's own
  // surfaces are about that project, never about creating an agent).
  //
  // `projectsSettled` is STICKY on purpose and must not be simplified back
  // to `!loading`. fleet-data.ts's shared cache sets `loading` true again on
  // EVERY 30s background refetch (runSharedFetch), so a live `!loading`
  // would flash "No projects yet" at an established workspace twice a
  // minute. "Nothing here" and "haven't been told yet" are different facts;
  // this says which one we are in.
  const [projectsSettled, setProjectsSettled] = useState(false);
  useEffect(() => {
    if (!loading) setProjectsSettled(true);
  }, [loading]);
  // Archived is a filtered VIEW, never the workspace's own emptiness — a
  // workspace whose only project is archived still has that project.
  // `allProjects` (unfiltered by the archive toggle) is what the plan is
  // asked about.
  const firstAgentPrompt = useMemo(
    () =>
      showArchived
        ? "none"
        : planFirstAgentPrompt({ projectsKnown: projectsSettled, projectCount: allProjects.length }),
    [showArchived, projectsSettled, allProjects.length],
  );

  // Workspace-scoped only — the Properties drawer's "Spend today"/"Tokens
  // today" sparklines below. This used to also build a per-agent cost/token
  // map (`usageByAgent`) to roll up into a per-PROJECT "Cost this month" /
  // "Tokens" column; that column is gone with the rest of the agent-derived
  // stats (see the FilterState comment above), so there is nothing left
  // that reads a per-agent breakdown of this response.
  const [workspaceTotals, setWorkspaceTotals] = useState<{ usd_cost?: number; total_tokens?: number } | null>(null);
  const [workspaceBuckets, setWorkspaceBuckets] = useState<UsageBucket[]>([]);
  useEffect(() => {
    let cancelled = false;
    fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        setWorkspaceTotals(d?.totals || null);
        if (Array.isArray(d?.buckets)) setWorkspaceBuckets(d.buckets);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);
  // Properties drawer — closed by default, an overlay over the sheet, same
  // contract as Agents/Project-detail's drawer.
  const [panelOpen, setPanelOpen] = useState(false);

  const filters: ToolbarFilter[] = [
    {
      key: "state", label: "Show", value: stateFilter, onChange: (v) => updateFilters({ state: v }),
      options: [
        { value: "active", label: "Active" },
        { value: "archived", label: "Archived" },
      ],
    },
  ];

  // No sort control: "Name" was the only genuine dimension left once
  // Agent count/Cost/Last active were removed as agent-derived, and a
  // single-option dropdown that already matches the list's own default
  // order picks nothing — a dead control (CLAUDE.md: "a control that
  // cannot be used in the current state is not rendered"). Alphabetical by
  // name, unconditionally.
  const shown = useMemo(
    () => [...projects].sort((a, b) => (a.name || "").localeCompare(b.name || "")),
    [projects],
  );

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
      {/* WHO OWNS THE VIEW'S ONE ACCENT FILL is create-accent.ts's answer, not
          a literal here. This used to be an unconditional `--accent-fill`,
          which put it and the zero-projects empty state's own filled button
          on screen together on every brand-new workspace — "two
          accent-filled buttons in one view is a bug", the same defect
          agents/page.tsx already fixed for its own "New agent". `listIsEmpty`
          is `firstAgentPrompt === "full"` — the zero-projects empty state's
          own presence, and ONLY that: an agent band used to also compete for
          this fill once a project existed and no agent did (removed, founder
          ruling — see workspace-first-run.ts), so past that one true-empty
          case this header is always the primary action again. */}
      <HeaderAction>
        <button
          type="button"
          className={createButtonClass("header", {
            listIsEmpty: firstAgentPrompt === "full",
            composerOpen: dialogOpen,
          })}
          onClick={() => setDialogOpen(true)}
        >
          <span className="fleet-btn-plus">+</span>
          New project
        </button>
      </HeaderAction>

      <div className="fleet-content-toolbar">
        {/* `|| showArchived` below is load-bearing: an EMPTY archived view
            must still render the filter that got you here, or the only way
            back to Active is the browser's back button. */}
        <FleetToolbar
          filters={projects.length > 0 || showArchived ? filters : undefined}
          panelOpen={panelOpen}
          onTogglePanel={() => setPanelOpen((v) => !v)}
          usageWorkspaceId={workspaceId}
        />
      </div>

      <div className="fleet-content-with-panel">
        <div className="fleet-content-main">
          {/* Agent creation used to be offered from here too — a band above
              the list once a project existed and the workspace had no
              agent (CreateFirstAgentEmpty variant="band"). DELETED
              2026-09-01: founder, on seeing it live, "inside this project I
              am seeing a button that says create your first agent even
              though project is something that must be related to the
              projects, not agents." This page's list is projects; agent
              creation lives in the Agents section of the rail. */}
          {loading && projects.length === 0 ? (
            // Reuses `.fleet-projects-list`/`.fleet-projects-list-header`/
            // `.fleet-project-row`'s real 2-column grid so the column-title
            // row (never reserved by the old bare `FleetListSkeleton`) and
            // each row's per-column x-offsets land in the same place the
            // real list renders into, not just the same row height.
            <div className="fleet-projects-list" aria-busy="true" aria-label="Loading">
              <div className="fleet-projects-list-header" aria-hidden>
                <span>Project</span>
                <span className="is-right">Work</span>
              </div>
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="fleet-project-row" style={{ cursor: "default" }}>
                  <span className="fleet-skeleton-bar" style={{ width: `${45 + (i % 3) * 12}%`, height: 13 }} />
                  <span className="fleet-skeleton-bar" style={{ width: 96, height: 12, marginLeft: "auto" }} />
                </div>
              ))}
            </div>
          ) : error && projects.length === 0 ? (
            <FleetSurfaceError title="Couldn’t load projects" message={error} onRetry={refresh} />
          ) : projects.length === 0 && showArchived ? (
            // "Create your first agent" is the wrong offer here — nothing
            // is missing, the archive is simply empty.
            <div className="fleet-page-state-body">No archived projects.</div>
          ) : projects.length === 0 ? (
            // THE ZERO-PROJECTS LANDING SCREEN — the literal first screen a
            // brand-new signup sees (/w/{id} redirects here, and a fresh
            // workspace starts with no projects now that the "General"
            // bootstrap is gone — see workspace-first-run.ts). This used to
            // be CreateFirstAgentEmpty's "full" variant ("Create your first
            // agent"), which put the one accent-filled control on this page
            // on the one thing Empyralis is not selling (founder: launching
            // on "track your issues and your documents", agents deliberately
            // kept out of the pitch) — and its own copy had gone false too,
            // describing a shared "General" project this ruling deleted.
            // Agent creation stays reachable from the rail's own Agents
            // entry; it just isn't this page's primary action any more.
            <div className="fleet-empty">
              <div className="fleet-empty-icon">
                <FolderKanban size={20} strokeWidth={1.75} />
              </div>
              <div className="fleet-empty-title">No projects yet</div>
              <div className="fleet-empty-desc">A project holds your team's tasks and documents.</div>
              <div className="fleet-empty-actions">
                {/* Opens the SAME NewProjectDialog the header's own "New
                    project" does — one composer, two doors, so there is
                    nothing here for a sibling composer to go blind to.
                    `listIsEmpty: true` mirrors the header's own
                    `firstAgentPrompt === "full"` read of this exact branch,
                    so create-accent.ts never sees two owners at once. */}
                <button
                  type="button"
                  className={createButtonClass("empty_state", { listIsEmpty: true, composerOpen: dialogOpen })}
                  onClick={() => setDialogOpen(true)}
                >
                  Create your first project
                </button>
              </div>
            </div>
          ) : (
            <div className="fleet-projects-list">
              <div className="fleet-projects-list-header" aria-hidden>
                <span>Project</span>
                <span className="is-right">Work</span>
                <span />
              </div>
              {shown.map((p) => {
                // Real project-owned facts (routes_fleet.fleet_projects
                // composes both alongside the row itself, same one-query
                // shape as the old agent_count) — never agent-derived, so
                // this carries no trace of the "agents carry project_id"
                // lie the removed columns did.
                const workSummary = formatProjectWorkSummary(p.task_count, p.document_count);
                const hasWork = (p.task_count ?? 0) > 0 || (p.document_count ?? 0) > 0;
                return (
                  <Link key={p.id} href={`${base}/projects/${encodeURIComponent(p.id)}`} className="fleet-project-row">
                    <span className="fleet-project-cell-name">
                      <ProjectIcon icon={p.icon} tint={p.tint} />
                      <span className="fleet-project-cell-name-text">
                        <span className="fleet-project-cell-name-title">{p.name || p.id}</span>
                        {p.description && <span className="fleet-project-cell-name-desc">{p.description}</span>}
                        <span className="fleet-agent-meta-mobile">{workSummary}</span>
                      </span>
                    </span>
                    <span className={`fleet-agent-cell-right fleet-cell-secondary${hasWork ? "" : " fleet-cell-muted"}`}>
                      {workSummary}
                    </span>
                    {/* Same button as the project detail toolbar's own —
                        works from a row too, without opening the project
                        first. Its own click handler stops this row's <Link>
                        from also navigating (same nested-interactive
                        pattern TasksList.tsx's assignee button already uses
                        inside its row anchor). */}
                    <CopyLinkButton
                      path={`${base}/projects/${encodeURIComponent(p.id)}`}
                      label={p.name || "this project"}
                    />
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
              formattedTotal={formatUsd(workspaceTotals?.usd_cost ?? 0)}
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

/**
 * Compact project composer — the same small centred "paper" shell
 * TaskComposer (TaskComposer.tsx) established, not a parallel one.
 *
 * 2026-09-01, reverted from a near-full-viewport panel (founder: "while I
 * am going to create project the user interface of it is changed... this
 * is completely bullshit... creating projects user interface must be
 * something like that is compact like tasks, except some buttons are not
 * going to be there, like to do priority and some other things"). This now
 * renders through TaskComposer's OWN classes — .fleet-composer-backdrop/
 * .fleet-composer/-head/-crumb/-close/-paper/-title/-desc/-error/-foot —
 * rather than a project-sized copy of them, so the two composers can never
 * drift apart in size or spacing again by construction.
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
 * .fleet-composer-chips row here at all — building chips for fields the
 * backend silently drops would be exactly the "control whose own label
 * admits it does nothing" CLAUDE.md rules out. Name and description are
 * the whole surface, which is why this ends up SHORTER than a task's
 * composer despite sharing its exact width and type scale.
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
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/projects`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ name: clean, description: description.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the project.");
    } finally {
      setBusy(false);
    }
  }, [busy, description, name, onCreated, workspaceId]);

  return (
    <div
      className="fleet-composer-backdrop"
      onMouseDown={onClose}
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
        className="fleet-composer"
        role="dialog"
        aria-modal="true"
        aria-label="New project"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="fleet-composer-head">
          <span className="fleet-composer-crumb">
            {workspace?.name ? <span className="fleet-composer-crumb-project">{workspace.name}</span> : null}
            {workspace?.name ? <span aria-hidden>›</span> : null}
            <span>New project</span>
          </span>
          <button type="button" className="fleet-composer-close" onClick={onClose} aria-label="Close">
            <X size={14} strokeWidth={2} />
          </button>
        </div>

        {/* Real heading structure per CLAUDE.md craft doctrine — visually
            hidden because the title input directly below IS the heading
            visually (its placeholder communicates intent exactly like
            Linear's "Project name" gray placeholder), but a dialog's
            accessible name still deserves a real h2, not just aria-label on
            the wrapping div. */}
        <h2 className="fleet-sr-only">New project</h2>

        {/* The paper — same two bare fields as TaskComposer, nothing else:
            no .fleet-composer-chips row follows, because a project has no
            status/priority/assignee to chip. */}
        <div className="fleet-composer-paper">
          <input
            ref={nameRef}
            className="fleet-composer-title"
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
            className="fleet-composer-desc"
            value={description}
            rows={3}
            onChange={(e) => setDescription(e.currentTarget.value)}
            placeholder="Add description…"
          />
        </div>

        {error ? (
          <p className="fleet-composer-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="fleet-composer-foot">
          <button type="button" className="fleet-btn" onClick={onClose} disabled={busy}>Cancel</button>
          {/* Routed through create-accent.ts rather than hardcoding the filled
              class — same class out, but it is now VISIBLE to that module's
              own guard that this composer obeys the rule. Its header names
              exactly this shape as what let TaskComposer and DocumentComposer
              stay filled while the header button behind them was filled too. */}
          <button type="button" className={composerSubmitButtonClass()} onClick={() => void create()} disabled={busy || !name.trim()}>
            {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Create project"}
          </button>
        </div>
      </div>
    </div>
  );
}
