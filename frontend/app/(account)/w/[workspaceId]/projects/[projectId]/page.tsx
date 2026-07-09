"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Bot, Calendar, Zap } from "lucide-react";

import { useFleetAgents, useFleetProjects, type FleetAgent } from "@/lib/workspace/fleet/fleet-data";
import { useBreadcrumbLabel, useBreadcrumbIcon, useBreadcrumbBadge, HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";
import { breadcrumbCount, tintKeyForIndex, TINTS, formatDate, formatNumber } from "@/lib/workspace/fleet/fleet-presentation";
import { ProjectIcon } from "@/lib/workspace/fleet/fleet-project-identity";
import { UsageStat, bucketSeries, type UsageBucket } from "@/lib/workspace/fleet/fleet-sparkline";
import { AgentsList, rememberLastViewedAgent } from "@/lib/workspace/fleet/AgentsList";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow } from "@/lib/workspace/fleet/FleetRightPanel";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton } from "@/lib/workspace/fleet/fleet-states";

const money = (n: number | undefined) => `$${(n ?? 0).toFixed(4)}`;

type SortMode = "last_active" | "status" | "cost" | "name";
const STATUS_RANK: Record<string, number> = { online: 0, unknown: 1, offline: 2 };
const SORT_OPTIONS = [
  { value: "last_active", label: "Last active" },
  { value: "status", label: "Status" },
  { value: "cost", label: "Cost" },
  { value: "name", label: "Name" },
];

type FilterState = { status: string; channel: string; sort: SortMode };

// Same URL-backed view state as the flat agents list (agents/page.tsx) — so
// leaving for an agent's detail and returning (including Esc-to-return's
// browser-back) restores this project's filtered/sorted view too.
function readFiltersFromLocation(): FilterState {
  if (typeof window === "undefined") return { status: "all", channel: "all", sort: "last_active" };
  const sp = new URLSearchParams(window.location.search);
  return {
    status: sp.get("status") || "all",
    channel: sp.get("channel") || "all",
    sort: (sp.get("sort") as SortMode) || "last_active",
  };
}

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const projectId = String(params?.projectId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents, loading, refresh } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const project = projects.find((p) => p.id === projectId);
  useBreadcrumbLabel(projectId, project?.name);
  useBreadcrumbIcon(
    projectId,
    useMemo(
      () => (project ? <ProjectIcon icon={project.icon} tint={project.tint} size={16} /> : null),
      [project],
    ),
  );

  const [filterState, setFilterState] = useState<FilterState>(() => readFiltersFromLocation());
  const { status: statusFilter, channel: channelFilter, sort } = filterState;
  const projectBase = `${base}/projects/${encodeURIComponent(projectId)}`;

  const updateFilters = useCallback((patch: Partial<FilterState>) => {
    setFilterState((prev) => {
      const next = { ...prev, ...patch };
      const sp = new URLSearchParams();
      if (next.status !== "all") sp.set("status", next.status);
      if (next.channel !== "all") sp.set("channel", next.channel);
      if (next.sort !== "last_active") sp.set("sort", next.sort);
      const qs = sp.toString();
      router.replace(`${projectBase}${qs ? `?${qs}` : ""}`);
      return next;
    });
  }, [router, projectBase]);

  const [wizardOpen, setWizardOpen] = useState(false);
  // Properties drawer — closed by default, an overlay over the sheet.
  const [panelOpen, setPanelOpen] = useState(false);
  const [rollup, setRollup] = useState<{ usd_cost: number; total_tokens: number; events: number } | null>(null);
  const [costBuckets, setCostBuckets] = useState<UsageBucket[]>([]);
  const [cost, setCost] = useState<Map<string, number>>(new Map());

  useEffect(() => {
    let cancelled = false;
    // period=day, not month: `totals` is an all-time scope aggregate either
    // way (period only controls how `buckets` are grouped) — day gives the
    // daily granularity the cost sparkline needs; month would collapse to
    // one point.
    fetch(`${base.replace("/w/", "/api/w/")}/fleet/usage?scope=project&id=${encodeURIComponent(projectId)}&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        if (d?.totals) setRollup(d.totals);
        if (Array.isArray(d?.buckets)) setCostBuckets(d.buckets);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [base, projectId]);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const m = new Map<string, number>();
        for (const a of d?.by_agent || []) m.set(a.agent_install_id, a.usd_cost);
        setCost(m);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);

  const inProject = agents.filter((a) => (a.project_id || "").trim() === projectId);
  // U3-E: the count lives on the breadcrumb line itself ("General · 3
  // agents"), not a second toolbar row — the project name appears exactly
  // once, in the crumb this badge attaches to.
  useBreadcrumbBadge(
    projectId,
    useMemo(
      () => <span className="fleet-breadcrumb-count">· {breadcrumbCount(inProject.length, "agent", "agents", project?.name || "")}</span>,
      [inProject.length, project?.name],
    ),
  );
  const filtered = useMemo(() => inProject.filter((a) => {
    if (statusFilter !== "all" && (a.hardware_status || "unknown") !== statusFilter) return false;
    if (channelFilter === "connected" && !a.channel) return false;
    if (channelFilter === "none" && a.channel) return false;
    return true;
  }), [inProject, statusFilter, channelFilter]);
  const shown = useMemo(() => sortAgents(filtered, sort, cost), [filtered, sort, cost]);

  const costByAgent = useMemo(
    () => inProject
      .map((a) => ({ id: a.agent_id, label: a.label || "Unnamed agent", cost: cost.get(a.agent_id) || 0 }))
      .sort((a, b) => b.cost - a.cost),
    [inProject, cost],
  );

  const filters: ToolbarFilter[] = [
    {
      key: "status", label: "Status", value: statusFilter, onChange: (v) => updateFilters({ status: v }),
      options: [
        { value: "all", label: "All statuses" },
        { value: "online", label: "Online" },
        { value: "offline", label: "Offline" },
        { value: "unknown", label: "Not deployed" },
      ],
    },
    {
      key: "channel", label: "Channel", value: channelFilter, onChange: (v) => updateFilters({ channel: v }),
      options: [
        { value: "all", label: "All channels" },
        { value: "connected", label: "Connected" },
        { value: "none", label: "No channel" },
      ],
    },
  ];

  const goToAgent = (agentId: string) => {
    rememberLastViewedAgent(agentId);
    router.push(`${projectBase}/agents/${encodeURIComponent(agentId)}/overview`);
  };

  return (
    <main className="fleet-content fleet-content--with-panel">
      {/* No page-title header here — the breadcrumb (with the project's own
          icon, see useBreadcrumbIcon above, and its count badge, see
          useBreadcrumbBadge above) is the page identity. This stage matches
          the Agents page exactly, all the way down to the two-row header
          (U3-H): top row is breadcrumb + primary action only; the
          view-control cluster is its own row below, under the topbar's
          existing divider. FleetToolbar always renders here (even with 0
          agents) so the Properties toggle stays reachable; filters/sort
          still hide themselves when there's nothing to filter/sort (each is
          independently optional). */}
      <HeaderAction>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
          <span className="fleet-btn-plus">+</span> New agent
        </button>
      </HeaderAction>

      <div className="fleet-content-toolbar">
        <FleetToolbar
          filters={inProject.length > 0 ? filters : undefined}
          sortOptions={inProject.length > 0 ? SORT_OPTIONS : undefined}
          sortValue={sort}
          sortDefault="last_active"
          onSortChange={(v) => updateFilters({ sort: v as SortMode })}
          panelOpen={panelOpen}
          onTogglePanel={() => setPanelOpen((v) => !v)}
          usageHref={`${base}/billing`}
        />
      </div>

      {/* The sheet — full width always, whether the drawer below is open or
          closed. fleet-content-with-panel is just the relative anchor the
          drawer overlays against; it is no longer a flex row splitting width
          with a permanent sibling. */}
      <div className="fleet-content-with-panel">
        <div className="fleet-content-main">
          {loading && inProject.length === 0 ? (
            <FleetListSkeleton rows={4} />
          ) : inProject.length === 0 ? (
            <FirstAgentEmpty
              title="No agents in this project"
              desc="Create one — it’ll be assigned here."
              onCreate={() => setWizardOpen(true)}
            />
          ) : shown.length === 0 ? (
            <div className="fleet-page-state-body">No agents match these filters.</div>
          ) : (
            <AgentsList workspaceId={workspaceId} agents={shown} costByAgent={cost} onSelect={goToAgent} onAgentStoppedChanged={refresh} />
          )}
        </div>

        <FleetRightPanel open={panelOpen} onClose={() => setPanelOpen(false)}>
          <PanelSection title="Properties">
            {project?.description && <PanelRow label="Description" value={project.description} />}
            <UsageStat
              label="Cost this month"
              total={rollup?.usd_cost ?? 0}
              formattedTotal={money(rollup?.usd_cost)}
              values={bucketSeries(costBuckets, "usd_cost")}
            />
            <UsageStat
              label="Tokens"
              total={rollup?.total_tokens ?? 0}
              formattedTotal={formatNumber(rollup?.total_tokens ?? 0)}
              values={bucketSeries(costBuckets, "total_tokens")}
            />
            <PanelRow label="LLM calls" value={formatNumber(rollup?.events ?? 0)} icon={<Zap size={15} strokeWidth={1.75} />} />
            <PanelRow label="Agents" value={inProject.length} icon={<Bot size={15} strokeWidth={1.75} />} />
            <PanelRow label="Created" value={project?.created_at ? formatDate(project.created_at) : "—"} icon={<Calendar size={15} strokeWidth={1.75} />} tone={project?.created_at ? "default" : "muted"} />
          </PanelSection>

          <PanelSection title="Cost by agent">
            {costByAgent.length === 0 ? (
              <div className="fleet-panel-empty">No agents yet.</div>
            ) : (
              costByAgent.map((a, i) => (
                <PanelRow
                  key={a.id}
                  label={a.label}
                  icon={<span className="fleet-tint-pip" style={{ background: TINTS[tintKeyForIndex(i)].fg }} />}
                  value={a.cost > 0 ? money(a.cost) : "—"}
                  tone={a.cost > 0 ? "default" : "muted"}
                />
              ))
            )}
          </PanelSection>

        </FleetRightPanel>
      </div>

      {wizardOpen && (
        <FleetCreateAgentWizard
          workspaceId={workspaceId}
          initialProjectId={projectId}
          onClose={() => setWizardOpen(false)}
          onCreated={() => { setWizardOpen(false); refresh(); }}
        />
      )}
    </main>
  );
}

function sortAgents(agents: FleetAgent[], sort: SortMode, cost: Map<string, number>): FleetAgent[] {
  const list = [...agents];
  if (sort === "name") {
    list.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  } else if (sort === "status") {
    list.sort((a, b) => (STATUS_RANK[a.hardware_status] ?? 1) - (STATUS_RANK[b.hardware_status] ?? 1));
  } else if (sort === "cost") {
    list.sort((a, b) => (cost.get(b.agent_id) || 0) - (cost.get(a.agent_id) || 0));
  } else {
    list.sort((a, b) => {
      const ta = a.last_activity ? new Date(a.last_activity).getTime() : 0;
      const tb = b.last_activity ? new Date(b.last_activity).getTime() : 0;
      return tb - ta;
    });
  }
  return list;
}
