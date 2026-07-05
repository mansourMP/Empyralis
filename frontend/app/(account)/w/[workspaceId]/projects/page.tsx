"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FolderKanban } from "lucide-react";

import { useFleetProjects } from "@/lib/workspace/fleet/fleet-data";

export default function ProjectsPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const { projects, loading, error } = useFleetProjects(workspaceId);
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  return (
    <main className="fleet-content">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Projects</h1>
          <p className="fleet-subtitle">
            {loading ? "Loading…" : `${projects.length} ${projects.length === 1 ? "project" : "projects"}`}
          </p>
        </div>
      </div>

      {error && <div className="fleet-page-state-body">{error}</div>}

      {!loading && projects.length === 0 && (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No projects yet</div>
          <div className="fleet-empty-desc">Agents you create are grouped into projects.</div>
        </div>
      )}

      <div className="fleet-list">
        {projects.map((p) => (
          <Link key={p.id} href={`${base}/projects/${encodeURIComponent(p.id)}`} className="fleet-list-row">
            <span className="fleet-list-row-icon">
              <FolderKanban size={16} strokeWidth={1.75} />
            </span>
            <span className="fleet-list-row-main">
              <span className="fleet-list-row-title">{p.name || p.id}</span>
              {p.description && <span className="fleet-list-row-desc">{p.description}</span>}
            </span>
            <span className="fleet-list-row-meta">
              {p.agent_count ?? 0} {(p.agent_count ?? 0) === 1 ? "agent" : "agents"}
            </span>
          </Link>
        ))}
      </div>
    </main>
  );
}
