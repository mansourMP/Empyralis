"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Bot } from "lucide-react";

import { useFleetAgents, useFleetProjects, type FleetAgent } from "@/lib/workspace/fleet/fleet-data";
import { AgentsList } from "@/lib/workspace/fleet/AgentsList";
import { StatusDot } from "@/lib/workspace/fleet/fleet-indicators";
import { tintKeyForIndex, TINTS } from "@/lib/workspace/fleet/fleet-presentation";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow, usePanelOpenState } from "@/lib/workspace/fleet/FleetRightPanel";
import { FleetCreateAgentWizard } from "@/lib/workspace/fleet/FleetCreateAgentWizard";
import { FirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";

const money = (n: number) => `$${n.toFixed(4)}`;

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
  const [panelOpen, togglePanel] = usePanelOpenState("agents");

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

  const onlineCount = agents.filter((a) => a.hardware_status === "online").length;
  const offlineCount = agents.filter((a) => a.hardware_status === "offline").length;
  const costByAgent = useMemo(
    () => agents
      .map((a) => ({ id: a.agent_id, label: a.label || "Unnamed agent", cost: cost.get(a.agent_id) || 0 }))
      .sort((a, b) => b.cost - a.cost),
    [agents, cost],
  );

  return (
    <main className="fleet-content fleet-content--with-panel">
      <HeaderAction>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
          <span className="fleet-btn-plus">+</span>
          New agent
        </button>
      </HeaderAction>

      <div className="fleet-content-toolbar">
        {agents.length > 0 && (
          <FleetToolbar
            filters={filters}
            sortOptions={SORT_OPTIONS}
            sortValue={sort}
            sortDefault="last_active"
            onSortChange={(v) => setSort(v as SortMode)}
            panelOpen={panelOpen}
            onTogglePanel={togglePanel}
          />
        )}
      </div>

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
              agents={shown}
              costByAgent={cost}
              projectNameById={projName}
              groupByProject={sort === "group"}
              onSelect={goToAgent}
            />
          )}
        </div>

        <FleetRightPanel open={panelOpen}>
          <PanelSection title="Properties">
            <PanelRow label="Agents" value={agents.length} icon={<Bot size={15} strokeWidth={1.75} />} />
            <PanelRow label="Online" value={onlineCount} icon={<StatusDot tone="online" />} tone="online" />
            <PanelRow label="Offline" value={offlineCount} icon={<StatusDot tone="offline" />} tone="offline" />
            <PanelRow
              label="Not deployed"
              value={agents.length - onlineCount - offlineCount}
              icon={<StatusDot tone="unknown" />}
              tone="muted"
            />
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
