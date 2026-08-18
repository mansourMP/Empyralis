"use client";

/**
 * CONTEXT — every document in the workspace, in one GitHub-shaped tree.
 *
 *   GitHub          Empyralis
 *     org             workspace     this page
 *     repo            project       a top-level group below
 *     file path       document.path specs/api/auth.md
 *
 * The founder's approved mapping, and the reason this surface earns a
 * top-level place when so little else does: CLAUDE.md's positioning says
 * the accumulated CONTEXT a team owns is the durable asset, and until now
 * a document could only be reached by first remembering which project it
 * was filed in. "Where is the auth spec" had no answer anywhere in the
 * product.
 *
 * NOT A NEW BOUNDARY-CROSSING SURFACE. This is workspace DATA (documents),
 * the same class as Projects — never an aggregation of AGENTS, which is
 * the specific thing "an agent belongs to its project and works only
 * there" says a top-level list must not do. Project-visibility filtering
 * is the BACKEND's (fleet_list_documents with no `project_id` returns only
 * the caller's visible projects), never a client-side filter over a wider
 * read.
 *
 * NO NEW ROUTE, NO NEW CONCEPT. It reuses the documents list endpoint with
 * `project_id` omitted, and every row links to the document's own page
 * INSIDE its project — a document has one home, and this page is a lens on
 * it rather than a second place it lives. Same posture "My work" already
 * takes for tasks.
 */

import { useCallback, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Library } from "lucide-react";

import { useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import {
  useFleetWorkspaceDocuments,
  type FleetDocument,
} from "@/lib/workspace/fleet/documents-data";
import { WorkspaceDocumentsTree } from "@/lib/workspace/fleet/DocumentsList";
import { FleetRowsSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { breadcrumbCount } from "@/lib/workspace/fleet/fleet-presentation";

export default function ContextPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const { documents, loading, error, refresh } = useFleetWorkspaceDocuments(workspaceId);
  const { projects } = useFleetProjects(workspaceId);

  const projectList = useMemo(
    () => projects.map((project) => ({ id: project.id, name: project.name })),
    [projects],
  );

  useBreadcrumbBadge(
    "context",
    useMemo(
      () =>
        loading && documents.length === 0 ? null : (
          <span className="fleet-breadcrumb-count">
            · {breadcrumbCount(documents.length, "document", "documents", "No documents")}
          </span>
        ),
      [loading, documents.length],
    ),
  );

  // A document's home is its project. A row for a document carrying no
  // project at all cannot open a document page (the detail route lives
  // under one), so it degrades to the projects list rather than linking at
  // a URL that would 404 — the same call My work makes for a task with no
  // project.
  const hrefFor = useCallback(
    (document: FleetDocument) => {
      const projectId = String(document.project_id || "").trim();
      if (!projectId) return `${base}/projects`;
      return `${base}/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(document.id)}`;
    },
    [base],
  );

  return (
    <main className="fleet-content">
      <div className="fleet-content-main">
        {/* "Could not load" and "there is nothing here" are DIFFERENT FACTS
            and never share a screen (CLAUDE.md's standing rule) — an
            unreachable backend must never render as a confident empty state
            telling somebody their workspace holds no documents. */}
        {error && documents.length === 0 ? (
          <FleetSurfaceError
            title="Couldn’t load your documents"
            message={error}
            onRetry={() => void refresh()}
          />
        ) : loading && documents.length === 0 ? (
          <FleetRowsSkeleton rows={6} label="Loading documents" />
        ) : documents.length === 0 ? (
          // An empty state that TEACHES, which is allowed precisely because
          // there is nothing else to show — and it names where a document
          // comes from, so "why is this empty" is answered without guessing.
          <div className="fleet-mywork-empty">
            <Library size={20} strokeWidth={1.5} aria-hidden="true" />
            <h2>No documents yet</h2>
            <p>
              Documents live in a project — specs, decisions, anything the team and its agents
              should read before starting work.
            </p>
            <Link className="fleet-btn fleet-btn--accent" href={`${base}/projects`}>
              Open a project
            </Link>
          </div>
        ) : (
          <WorkspaceDocumentsTree
            documents={documents}
            projects={projectList}
            hrefFor={hrefFor}
          />
        )}
      </div>
    </main>
  );
}
