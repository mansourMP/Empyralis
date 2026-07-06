"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { useFleetAgents, useFleetProjects, type FleetAgent } from "@/lib/workspace/fleet/fleet-data";
import { AgentsList } from "@/lib/workspace/fleet/AgentsList";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";

type SortMode = "last_active" | "status" | "cost" | "name" | "group";

const STATUS_RANK: Record<string, number> = { online: 0, unknown: 1, offline: 2 };
const SORT_OPTIONS = [
  { value: "last_active", label: "Last active" },
  { value: "status", label: "Status" },
  { value: "cost", label: "Cost" },
  { value: "name", label: "Name" },
  { value: "group", label: "Group by project" },
];

export default function AgentsPage() {
  const params = useParams();
  const router = useRouter();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { agents, loading, error, refresh } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const [projectFilter, setProjectFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [channelFilter, setChannelFilter] = useState<string>("all");
  const [sort, setSort] = useState<SortMode>("last_active");
  const [cost, setCost] = useState<Map<string, number>>(new Map());
  const [wizardOpen, setWizardOpen] = useState(false);

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
      key: "project", label: "Project", value: projectFilter, onChange: setProjectFilter,
      options: [{ value: "all", label: "All projects" }, ...projects.map((p) => ({ value: p.id, label: p.name || p.id }))],
    },
    {
      key: "status", label: "Status", value: statusFilter, onChange: setStatusFilter,
      options: [
        { value: "all", label: "All statuses" },
        { value: "online", label: "Online" },
        { value: "offline", label: "Offline" },
        { value: "unknown", label: "Not deployed" },
      ],
    },
    {
      key: "channel", label: "Channel", value: channelFilter, onChange: setChannelFilter,
      options: [
        { value: "all", label: "All channels" },
        { value: "connected", label: "Connected" },
        { value: "none", label: "No channel" },
      ],
    },
  ];

  const goToAgent = (agentId: string, projectId: string) =>
    router.push(`${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/overview`);

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Agents</h1>
          <p className="fleet-subtitle">{loading ? "Loading…" : `${shown.length} of ${agents.length} agents`}</p>
        </div>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
          <span className="fleet-btn-plus">+</span>
          New agent
        </button>
      </div>

      {agents.length > 0 && (
        <FleetToolbar filters={filters} sortOptions={SORT_OPTIONS} sortValue={sort} sortDefault="last_active" onSortChange={(v) => setSort(v as SortMode)} />
      )}

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
          agents={shown}
          costByAgent={cost}
          projectNameById={projName}
          groupByProject={sort === "group"}
          onSelect={goToAgent}
        />
      )}

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
