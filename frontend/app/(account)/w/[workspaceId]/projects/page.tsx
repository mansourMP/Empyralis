"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Bot, FolderKanban, Loader2 } from "lucide-react";

import { useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { HeaderAction } from "@/lib/workspace/fleet/Breadcrumbs";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { FleetToolbar } from "@/lib/workspace/fleet/FleetToolbar";
import { FleetRightPanel, PanelSection, PanelRow, usePanelOpenState } from "@/lib/workspace/fleet/FleetRightPanel";
import { TintTile } from "@/lib/workspace/fleet/fleet-indicators";
import { CreateFirstAgentEmpty } from "@/lib/workspace/fleet/first-agent-empty";
import { FleetListSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";

export default function ProjectsPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const { projects, loading, error, refresh } = useFleetProjects(workspaceId);
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const [dialogOpen, setDialogOpen] = useState(false);
  const [panelOpen, togglePanel] = usePanelOpenState("projects");

  const totalAgents = projects.reduce((sum, p) => sum + (p.agent_count ?? 0), 0);

  return (
    <main className="fleet-content fleet-content--with-panel">
      <HeaderAction>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => setDialogOpen(true)}>
          <span className="fleet-btn-plus">+</span>
          New project
        </button>
      </HeaderAction>

      <div className="fleet-content-toolbar">
        {projects.length > 0 && <FleetToolbar panelOpen={panelOpen} onTogglePanel={togglePanel} />}
      </div>

      <div className="fleet-content-with-panel">
        <div className="fleet-content-main">
          {loading && projects.length === 0 ? (
            <FleetListSkeleton rows={4} />
          ) : error && projects.length === 0 ? (
            <FleetSurfaceError title="Couldn’t load projects" message={error} onRetry={refresh} />
          ) : projects.length === 0 ? (
            <CreateFirstAgentEmpty
              workspaceId={workspaceId}
              onCreated={refresh}
              title="No projects yet"
              desc="Projects keep your agents organized. Create your first agent and its project is set up for you."
            />
          ) : (
            <div className="fleet-list">
              {projects.map((p) => (
                <Link key={p.id} href={`${base}/projects/${encodeURIComponent(p.id)}`} className="fleet-list-row">
                  <TintTile accent>
                    <FolderKanban size={15} strokeWidth={1.75} />
                  </TintTile>
                  <span className="fleet-list-row-main">
                    <span className="fleet-list-row-title">{p.name || p.id}</span>
                    {p.description && <span className="fleet-list-row-desc">{p.description}</span>}
                  </span>
                  <span className="fleet-list-row-meta fleet-list-row-meta--icon">
                    <Bot size={13} strokeWidth={1.75} />
                    <span className="fleet-list-row-meta-num">{p.agent_count ?? 0}</span>
                    {(p.agent_count ?? 0) === 1 ? "agent" : "agents"}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>

        <FleetRightPanel open={panelOpen}>
          <PanelSection title="Properties">
            <PanelRow label="Projects" value={projects.length} icon={<FolderKanban size={15} strokeWidth={1.75} />} />
            <PanelRow label="Agents" value={totalAgents} icon={<Bot size={15} strokeWidth={1.75} />} />
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

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/projects`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ name: name.trim(), description: description.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the project.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fleet-detail-backdrop" onClick={onClose}>
      <div className="fleet-small-dialog" role="dialog" aria-modal="true" aria-label="New project" onClick={(e) => e.stopPropagation()}>
        <div className="fleet-small-dialog-header">
          <div className="fleet-detail-section-title">New project</div>
        </div>
        <div className="fleet-small-dialog-body">
          <div>
            <label className="fleet-wizard-label">Name</label>
            <input
              className="fleet-wizard-input"
              value={name}
              onChange={(e) => setName(e.currentTarget.value)}
              placeholder="e.g. Customer Support"
              autoFocus
            />
          </div>
          <div>
            <label className="fleet-wizard-label">Description (optional)</label>
            <input
              className="fleet-wizard-input"
              value={description}
              onChange={(e) => setDescription(e.currentTarget.value)}
              placeholder="What lives in this project?"
            />
          </div>
          {error && <p className="fleet-channel-expand-error">{error}</p>}
        </div>
        <div className="fleet-small-dialog-footer">
          <button type="button" className="fleet-btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={create} disabled={busy || !name.trim()}>
            {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
