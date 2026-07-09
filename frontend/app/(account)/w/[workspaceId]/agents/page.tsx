"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Bot, DollarSign, Hash, PanelRightClose, PanelRightOpen, Radio } from "lucide-react";

import { useFleetAgents, useFleetProjects, type FleetAgent } from "@/lib/workspace/fleet/fleet-data";
import { findSageAgent } from "@/lib/workspace/fleet/fleet-presentation";
import { AgentsList, rememberLastViewedAgent } from "@/lib/workspace/fleet/AgentsList";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow } from "@/lib/workspace/fleet/FleetRightPanel";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";

type SortMode = "last_active" | "status" | "cost" | "name" | "group";

const STATUS_RANK: Record<string, number> = { online: 0, unknown: 1, offline: 2 };
const SORT_OPTIONS = [
  { value: "last_active", label: "Last active" },
  { value: "status", label: "Status" },
  { value: "cost", label: "Cost" },
  { value: "name", label: "Name" },
  { value: "group", label: "Group by project" },
];

type FilterState = { project: string; status: string; channel: string; sort: SortMode };

// View state lives in the URL, not just useState — so leaving for an agent's
// detail and coming back (including the Esc-to-return browser-back path)
// restores the exact same filtered/sorted view instead of resetting it.
function readFiltersFromLocation(): FilterState {
  if (typeof window === "undefined") return { project: "all", status: "all", channel: "all", sort: "last_active" };
  const sp = new URLSearchParams(window.location.search);
  return {
    project: sp.get("project") || "all",
    status: sp.get("status") || "all",
    channel: sp.get("channel") || "all",
    sort: (sp.get("sort") as SortMode) || "last_active",
  };
}

export default function AgentsPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents: allAgents, loading, error, refresh } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);

  // Sage is the Operator, not a listed worker — it never appears as a row
  // here (contract). Same root cause as its own detail page: Sage isn't a
  // specialist agent, so it's excluded from every agent-list surface.
  const sageAgent = useMemo(() => findSageAgent(allAgents), [allAgents]);
  const agents = useMemo(
    () => (sageAgent ? allAgents.filter((a) => a.agent_id !== sageAgent.agent_id) : allAgents),
    [allAgents, sageAgent],
  );
  const [filterState, setFilterState] = useState<FilterState>(() => readFiltersFromLocation());
  const { project: projectFilter, status: statusFilter, channel: channelFilter, sort } = filterState;

  const updateFilters = useCallback((patch: Partial<FilterState>) => {
    setFilterState((prev) => {
      const next = { ...prev, ...patch };
      const sp = new URLSearchParams();
      if (next.project !== "all") sp.set("project", next.project);
      if (next.status !== "all") sp.set("status", next.status);
      if (next.channel !== "all") sp.set("channel", next.channel);
      if (next.sort !== "last_active") sp.set("sort", next.sort);
      const qs = sp.toString();
      router.replace(`${base}/agents${qs ? `?${qs}` : ""}`);
      return next;
    });
  }, [router, base]);

  const [cost, setCost] = useState<Map<string, number>>(new Map());
  // Same /fleet/usage response the per-agent cost map is built from — the
  // `totals` block is the workspace roll-up (usd_cost, total_tokens, events),
  // fed into the properties drawer below so both live off one fetch.
  const [usageTotals, setUsageTotals] = useState<{ usd_cost?: number; total_tokens?: number; events?: number } | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  // Properties drawer — closed by default, an overlay over the sheet. The
  // page itself shows only agents; everything else (spend, tokens, channel
  // count) lives behind this toggle instead of a permanent strip up top.
  const [panelOpen, setPanelOpen] = useState(false);

  // Onboarding hand-off: /agents?new=1 lands straight in the wizard. Read the
  // flag client-side (no useSearchParams → no Suspense boundary needed),
  // consume it once, and clean the URL so a refresh doesn't reopen it.
  const consumedNew = useRef(false);
  useEffect(() => {
    if (consumedNew.current) return;
    if (new URLSearchParams(window.location.search).get("new") === "1") {
      consumedNew.current = true;
      setWizardOpen(true);
      router.replace(`${base}/agents`);
    }
  }, [router, base]);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const m = new Map<string, number>();
        for (const a of d?.by_agent || []) m.set(a.agent_install_id, a.usd_cost);
        setCost(m);
        setUsageTotals(d?.totals || null);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);

  const projName = useMemo(() => new Map(projects.map((p) => [p.id, p.name || p.id])), [projects]);

  const filtered = useMemo(() => {
    return agents.filter((a) => {
      if (projectFilter !== "all" && (a.project_id || "") !== projectFilter) return false;
      if (statusFilter !== "all" && (a.hardware_status || "unknown") !== statusFilter) return false;
      if (channelFilter === "connected" && !a.channel) return false;
      if (channelFilter === "none" && a.channel) return false;
      return true;
    });
  }, [agents, projectFilter, statusFilter, channelFilter]);
  const shown = useMemo(() => sortAgents(filtered, sort, cost), [filtered, sort, cost]);

  const filters: ToolbarFilter[] = [
    {
      key: "project", label: "Project", value: projectFilter, onChange: (v) => updateFilters({ project: v }),
      options: [{ value: "all", label: "All projects" }, ...projects.map((p) => ({ value: p.id, label: p.name || p.id }))],
    },
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

  const goToAgent = (agentId: string, projectId: string) => {
    rememberLastViewedAgent(agentId);
    router.push(`${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/overview`);
  };

  const activeCount = agents.filter((a) => (a.hardware_status || "").toLowerCase() === "online").length;
  const channelsLiveCount = agents.filter((a) => (a.channel || "").trim().length > 0).length;
  let spendToday = 0;
  for (const v of cost.values()) spendToday += Number(v || 0);

  return (
    <main className="fleet-content fleet-content--with-panel">
      <HeaderAction>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
          <span className="fleet-btn-plus">+</span>
          New agent
        </button>
      </HeaderAction>

      {/* trailingAction keeps the Properties toggle inside FleetToolbar's own
          right-aligned action cluster — same row, same gap, same alignment as
          Filter/Sort. FleetToolbar always renders here (even with 0 agents)
          so the toggle stays reachable; filters/sort still hide themselves
          when there's nothing to filter/sort. */}
      <div className="fleet-content-toolbar">
        <FleetToolbar
          filters={agents.length > 0 ? filters : undefined}
          sortOptions={agents.length > 0 ? SORT_OPTIONS : undefined}
          sortValue={sort}
          sortDefault="last_active"
          onSortChange={(v) => updateFilters({ sort: v as SortMode })}
          trailingAction={
            <button
              type="button"
              className={`fleet-icon-btn${panelOpen ? " is-active" : ""}`}
              onClick={() => setPanelOpen((v) => !v)}
              aria-label="Properties"
              aria-pressed={panelOpen}
              title="Properties"
            >
              {panelOpen ? <PanelRightClose size={16} strokeWidth={1.75} /> : <PanelRightOpen size={16} strokeWidth={1.75} />}
            </button>
          }
        />
      </div>

      {/* The sheet — full width always, whether the drawer below is open or
          closed. The page shows agents; everything else is behind the
          toggle above, not a permanent strip competing with the list. */}
      <div className="fleet-content-with-panel">
        <div className="fleet-content-main">
          {loading && agents.length === 0 ? (
            <FleetListSkeleton rows={6} />
          ) : error && agents.length === 0 ? (
            <FleetSurfaceError title="Couldn’t load agents" message={error} onRetry={refresh} />
          ) : agents.length === 0 ? (
            <FirstAgentEmpty
              title="No agents yet"
              desc="Agents do the work — they handle customer chats, run tasks, and use your tools. Create your first one to get started."
              onCreate={() => setWizardOpen(true)}
            />
          ) : shown.length === 0 ? (
            <div className="fleet-page-state-body">No agents match these filters.</div>
          ) : (
            <AgentsList
              workspaceId={workspaceId}
              agents={shown}
              costByAgent={cost}
              projectNameById={projName}
              groupByProject={sort === "group"}
              onSelect={goToAgent}
              onAgentStoppedChanged={refresh}
            />
          )}
        </div>

        <FleetRightPanel open={panelOpen} onClose={() => setPanelOpen(false)}>
          <PanelSection title="Properties">
            <PanelRow label="Total agents" value={agents.length} icon={<Bot size={15} strokeWidth={1.75} />} />
            <PanelRow label="Active" value={`${activeCount}/${agents.length}`} tone={activeCount > 0 ? "online" : "muted"} />
            <PanelRow label="Spend today" value={`$${spendToday.toFixed(2)}`} icon={<DollarSign size={15} strokeWidth={1.75} />} tone={spendToday > 0 ? "accent" : "muted"} />
            <PanelRow label="Tokens today" value={fmtTokens(usageTotals?.total_tokens || 0)} icon={<Hash size={15} strokeWidth={1.75} />} />
            <PanelRow label="Channels live" value={channelsLiveCount} icon={<Radio size={15} strokeWidth={1.75} />} tone={channelsLiveCount > 0 ? "default" : "muted"} />
          </PanelSection>
        </FleetRightPanel>
      </div>

      {wizardOpen && (
        <FleetCreateAgentWizard
          workspaceId={workspaceId}
          onClose={() => setWizardOpen(false)}
          onCreated={() => {
            setWizardOpen(false);
            refresh();
          }}
        />
      )}
    </main>
  );
}

// Compact absolute-token formatter — 47 → "47", 12_500 → "12.5K", 1_240_000 →
// "1.2M". Keeps the properties-panel row short.
function fmtTokens(n: number): string {
  if (!n || n <= 0) return "0";
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function sortAgents(agents: FleetAgent[], sort: SortMode, cost: Map<string, number>): FleetAgent[] {
  const list = [...agents];
  if (sort === "name") {
    list.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  } else if (sort === "status") {
    list.sort((a, b) => (STATUS_RANK[a.hardware_status] ?? 1) - (STATUS_RANK[b.hardware_status] ?? 1));
  } else if (sort === "cost") {
    list.sort((a, b) => (cost.get(b.agent_id) || 0) - (cost.get(a.agent_id) || 0));
  } else if (sort === "last_active") {
    list.sort((a, b) => {
      const ta = a.last_activity ? new Date(a.last_activity).getTime() : 0;
      const tb = b.last_activity ? new Date(b.last_activity).getTime() : 0;
      return tb - ta;
    });
  } else if (sort === "group") {
    list.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  }
  return list;
}
