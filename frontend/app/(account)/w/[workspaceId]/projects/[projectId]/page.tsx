"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Bot, Calendar, DollarSign, Hash, Zap } from "lucide-react";

import { useFleetAgents, useFleetProjects, type FleetAgent } from "@/lib/workspace/fleet/fleet-data";
import { useBreadcrumbLabel, HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";
import { timeAgo, tintKeyForIndex, TINTS } from "@/lib/workspace/fleet/fleet-presentation";
import { AgentsList } from "@/lib/workspace/fleet/AgentsList";
import { FleetToolbar, type ToolbarFilter } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow, usePanelOpenState } from "@/lib/workspace/fleet/FleetRightPanel";
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

type ActivityEvent = {
  event_id: string;
  action: string;
  title: string;
  status: string;
  created_at: string;
};

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

  const [statusFilter, setStatusFilter] = useState("all");
  const [channelFilter, setChannelFilter] = useState("all");
  const [sort, setSort] = useState<SortMode>("last_active");
  const [wizardOpen, setWizardOpen] = useState(false);
  const [rollup, setRollup] = useState<{ usd_cost: number; total_tokens: number; events: number } | null>(null);
  const [cost, setCost] = useState<Map<string, number>>(new Map());
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [activityLoading, setActivityLoading] = useState(false);
  const [panelOpen, togglePanel] = usePanelOpenState("project");

  useEffect(() => {
    let cancelled = false;
    fetch(`${base.replace("/w/", "/api/w/")}/fleet/usage?scope=project&id=${encodeURIComponent(projectId)}&period=month`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => { if (!cancelled && d?.totals) setRollup(d.totals); })
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

  // Activity is fetched lazily — only once the panel is opened, so a closed
  // panel (the default) costs nothing extra.
  useEffect(() => {
    if (!panelOpen) return;
    let cancelled = false;
    setActivityLoading(true);
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/project-activity?project_id=${encodeURIComponent(projectId)}`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => { if (!cancelled) setActivity(Array.isArray(d?.events) ? d.events : []); })
      .catch(() => { if (!cancelled) setActivity([]); })
      .finally(() => { if (!cancelled) setActivityLoading(false); });
    return () => { cancelled = true; };
  }, [workspaceId, projectId, panelOpen]);

  const inProject = agents.filter((a) => (a.project_id || "").trim() === projectId);
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

  const goToAgent = (agentId: string) =>
    router.push(`${base}/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/overview`);

  return (
    <main className="fleet-content fleet-content--with-panel">
      <HeaderAction>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setWizardOpen(true)}>
          <span className="fleet-btn-plus">+</span> New agent
        </button>
      </HeaderAction>

      <div className="fleet-content-toolbar">
        {inProject.length > 0 && (
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
            <AgentsList agents={shown} costByAgent={cost} onSelect={goToAgent} />
          )}
        </div>

        <FleetRightPanel open={panelOpen}>
          <PanelSection title="Properties">
            {project?.description && <PanelRow label="Description" value={project.description} />}
            <PanelRow label="Cost this month" value={money(rollup?.usd_cost)} icon={<DollarSign size={15} strokeWidth={1.75} />} tone="accent" />
            <PanelRow label="Tokens" value={(rollup?.total_tokens ?? 0).toLocaleString()} icon={<Hash size={15} strokeWidth={1.75} />} />
            <PanelRow label="LLM calls" value={(rollup?.events ?? 0).toLocaleString()} icon={<Zap size={15} strokeWidth={1.75} />} />
            <PanelRow label="Agents" value={inProject.length} icon={<Bot size={15} strokeWidth={1.75} />} />
            <PanelRow label="Created" value={project?.created_at ? new Date(project.created_at).toLocaleDateString() : "—"} icon={<Calendar size={15} strokeWidth={1.75} />} tone={project?.created_at ? "default" : "muted"} />
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

          <PanelSection title="Activity">
            {activityLoading ? (
              <div className="fleet-panel-empty">Loading…</div>
            ) : activity.length === 0 ? (
              <div className="fleet-panel-empty">No recent activity.</div>
            ) : (
              activity.map((e) => (
                <div key={e.event_id} className="fleet-panel-activity-item">
                  <span className="fleet-panel-activity-title">{e.title || e.action}</span>
                  <span className="fleet-panel-activity-meta">{timeAgo(e.created_at)}</span>
                </div>
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
