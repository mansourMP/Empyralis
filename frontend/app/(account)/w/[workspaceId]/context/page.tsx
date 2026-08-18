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
 *
 * TREE | ACTIVITY — a second, in-page view, not a second route. GitHub's
 * own repo view puts "Code" and "Commits" side by side the same way; here
 * the founder asked for it directly ("what changed anywhere this week").
 * Plain component state, not URL/localStorage state — the same posture
 * TaskViewOptions' own `layout` field takes for board-vs-list: a display
 * mode the reader picks per visit, not a navigation destination worth
 * bookmarking or a preference worth remembering across sessions. Defaults
 * to Tree so nobody who never touches the toggle sees any change at all.
 */

import { useCallback, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Library } from "lucide-react";

import { useFleetAgents, useFleetProjects } from "@/lib/workspace/fleet/fleet-data";
import { useWorkspaceMembers } from "@/lib/workspace/fleet/members-data";
import {
  useFleetWorkspaceDocuments,
  type FleetDocument,
  type FleetDocumentActivityEntry,
} from "@/lib/workspace/fleet/documents-data";
import { WorkspaceDocumentsTree } from "@/lib/workspace/fleet/DocumentsList";
import { DocumentActivityFeed } from "@/lib/workspace/fleet/document-activity-feed";
import { FleetRowsSkeleton, FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";
import { useBreadcrumbBadge } from "@/lib/workspace/fleet/Breadcrumbs";
import { breadcrumbCount } from "@/lib/workspace/fleet/fleet-presentation";

type ContextSurface = "tree" | "activity";

export default function ContextPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const [surface, setSurface] = useState<ContextSurface>("tree");

  const { documents, loading, error, refresh } = useFleetWorkspaceDocuments(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const { agents } = useFleetAgents(workspaceId);
  const { members } = useWorkspaceMembers(workspaceId);

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

  // Same degrade-to-the-projects-list call as hrefFor above, keyed off the
  // activity entry's own project_id/document_id instead of a FleetDocument's
  // project_id/id — two different row shapes reaching the same document URL.
  const hrefForActivity = useCallback(
    (entry: FleetDocumentActivityEntry) => {
      const projectId = String(entry.project_id || "").trim();
      if (!projectId) return `${base}/projects`;
      return `${base}/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(entry.document_id)}`;
    },
    [base],
  );

  return (
    // --wide, not the default reading column. Measured in a real browser
    // before changing it: plain `.fleet-content` centers a shrink-to-fit
    // box (its own `flex: 0 1 auto` + `margin-inline: auto` disable
    // stretch, per that rule's own comment), so `.fleet-doc-tree`'s natural
    // content width rendered as a narrow column with ~486px of dead margin
    // on BOTH sides at 1680px wide — not the lopsided one-sided strip
    // CLAUDE.md's `.fleet-task-page-body` entry describes, but the same
    // root cause: a page shell built for a reading column handed to a
    // page shape (a wide file tree, `.fleet-doc-tree`'s own cap is 1400px)
    // that isn't one. `--wide` zeroes the outer margin so `.fleet-content-
    // main`'s own 1140px cap and left alignment — the same pairing
    // Projects/Agents/My work already use for their own list/table content
    // — actually apply. Same call as those pages, not a new pattern.
    <main className="fleet-content fleet-content--wide">
      <div className="fleet-content-main">
        {/* Tree | Activity — see the file header. A plain button pair, not
            real links (this is a display mode the reader picks per visit,
            same posture TaskViewOptions' own layout toggle takes — see
            that file's header on why layout is excluded from "is anything
            non-default"). Shown even while the tree is still loading/empty:
            switching to Activity does not depend on the tree ever having
            resolved, and a control that only appears once the OTHER view
            has data would be a surprise the first time someone reaches for
            it before that. */}
        <div className="fleet-doc-context-toolbar">
          <div className="fleet-segmented" role="tablist" aria-label="Context view">
            <button
              type="button"
              role="tab"
              aria-selected={surface === "tree"}
              className={`fleet-segmented-btn${surface === "tree" ? " fleet-segmented-btn--active" : ""}`}
              onClick={() => setSurface("tree")}
            >
              Tree
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={surface === "activity"}
              className={`fleet-segmented-btn${surface === "activity" ? " fleet-segmented-btn--active" : ""}`}
              onClick={() => setSurface("activity")}
            >
              Activity
            </button>
          </div>
        </div>

        {surface === "activity" ? (
          // The WORKSPACE feed — projectId omitted, "one less filter" than
          // the project-scoped feed on the project's own Documents view.
          // The founder's own addition: "what changed anywhere this week."
          <DocumentActivityFeed
            workspaceId={workspaceId}
            hrefForDocument={hrefForActivity}
            agents={agents}
            members={members}
            projects={projectList}
          />
        ) : /* "Could not load" and "there is nothing here" are DIFFERENT FACTS
            and never share a screen (CLAUDE.md's standing rule) — an
            unreachable backend must never render as a confident empty state
            telling somebody their workspace holds no documents. */
        error && documents.length === 0 ? (
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
