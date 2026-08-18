"use client";

/**
 * THE CHANGE FEED — the founder's own ask, GitHub's "Commits" tab mapped
 * onto this product's own git-shaped model (see context/page.tsx's header:
 * org -> workspace, repo -> project, file path -> document.path). His
 * words: "I should see who is pushing, who changed what, who is changing
 * some other things and much more details like this."
 *
 * NO NEW DATA. Every fact here was already recorded by
 * project_document_revisions the day DocumentHistory.tsx shipped
 * (changed_by_type/id/display_name, revision_number, created_at, diff) —
 * the gap was a READ, not a write: the only reader (list_document_revisions)
 * ended `AND document_id = $x`, so a revision was invisible unless you
 * already knew which document to open. useFleetDocumentActivity +
 * GET .../fleet/document-activity (documents-data.ts) close it.
 *
 * ONE COMPONENT, TWO SCOPES. `projectId` present renders the PROJECT feed
 * (every document in one project — the founder's own first ask, GitHub's
 * per-repo commit log); omitted renders the WORKSPACE feed ("what changed
 * anywhere this week" — his own explicit addition, "one less filter" on
 * the identical backend route). A `projects` lookup is supplied only in the
 * workspace scope, to label which project each entry belongs to — the one
 * fact a project-scoped feed does not need to say about itself.
 *
 * REUSES DocumentHistory.tsx's actor/diff rendering WHOLESALE
 * (resolveRevisionActor/RevisionActorBadge/RevisionDiff/stamp, exported
 * from that file for exactly this) — a feed entry
 * (FleetDocumentActivityEntry) is a strict superset of the single-document
 * FleetDocumentRevision shape those functions already take. One
 * implementation of "how do we draw who changed this and what changed,"
 * not a second one built to match a first that quietly drifts.
 *
 * HUMAN VS AGENT MUST READ DISTINCTLY — the two non-negotiable constraints
 * this feed was built under. `changed_by_type` is exactly the axis
 * RevisionActorBadge already renders differently (a member avatar vs. an
 * AgentSigil), so this falls out of reuse rather than needing its own
 * branch.
 *
 * READ-ONLY. No revert/restore control anywhere in this file — a feed
 * that grows a destructive button is the exact failure shape CLAUDE.md
 * documents repeatedly ("no dead controls" cuts both ways: a control that
 * IS live but was never asked for is the same mistake in the other
 * direction). Restoring a revision is a WRITE with real consequences and a
 * separate, deliberate decision — not a checkbox on a read surface.
 */

import Link from "next/link";
import { FolderGit2 } from "lucide-react";

import {
  useFleetDocumentActivity,
  type FleetDocumentActivityEntry,
} from "./documents-data";
import {
  RevisionActorBadge,
  RevisionDiff,
  resolveRevisionActor,
  stamp,
} from "./DocumentHistory";
import { FleetRowsSkeleton, FleetSurfaceError } from "./fleet-states";
import { timeAgo } from "./fleet-presentation";
import type { FleetAgent } from "./fleet-data";
import type { WorkspaceMember } from "./members-data";
import "./document-detail.css";
import "./document-activity-feed.css";

export function DocumentActivityFeed({
  workspaceId,
  projectId,
  hrefForDocument,
  agents,
  members,
  projects,
  identityLookupFailed,
}: {
  workspaceId: string;
  /** Present = one project's feed. Omitted/null = every project this
   *  caller can see (the workspace feed) — see this file's own header. */
  projectId?: string | null;
  /** A feed entry's own `document_id`/`document_path`/`project_id` are
   *  enough to build a real link (CLAUDE.md: "primary navigation is real
   *  links, so cmd-click and middle-click work") without this component
   *  knowing the URL shape of a project's documents route itself — the
   *  same hrefFor-as-a-prop convention WorkspaceDocumentsTree/DocumentsList
   *  already use. */
  hrefForDocument: (entry: FleetDocumentActivityEntry) => string;
  agents: FleetAgent[];
  members: WorkspaceMember[];
  /** Only meaningful in the WORKSPACE scope (projectId omitted) — a
   *  project-scoped feed already knows which project it's showing, so
   *  labelling every row with it would repeat the page's own heading. */
  projects?: { id: string; name: string }[];
  identityLookupFailed?: boolean;
}) {
  const { activity, loading, error, refresh } = useFleetDocumentActivity(workspaceId, projectId);
  const showProjectLabel = !projectId;
  const projectName = (id: string | null) =>
    (projects || []).find((p) => p.id === id)?.name || "";

  if (error && activity.length === 0) {
    return <FleetSurfaceError title="Couldn’t load the change feed" message={error} onRetry={() => void refresh()} />;
  }
  if (loading && activity.length === 0) {
    return <FleetRowsSkeleton rows={6} label="Loading change feed" />;
  }
  if (activity.length === 0) {
    return (
      <div className="fleet-mywork-empty">
        <FolderGit2 size={20} strokeWidth={1.5} aria-hidden="true" />
        <h2>No changes yet</h2>
        <p>
          {projectId
            ? "Once a document in this project is created or edited, the change shows up here — who made it, when, and what changed."
            : "Once a document anywhere in this workspace is created or edited, the change shows up here — who made it, when, and what changed."}
        </p>
      </div>
    );
  }

  return (
    <ul className="fleet-doc-activity-list" aria-label="Change feed">
      {activity.map((entry) => {
        const actor = resolveRevisionActor(entry, agents, members, identityLookupFailed);
        return (
          <li key={entry.id} className="fleet-doc-history-item fleet-doc-activity-item">
            <div className="fleet-doc-history-head">
              <RevisionActorBadge actor={actor} />
              <Link href={hrefForDocument(entry)} className="fleet-doc-activity-target">
                {entry.document_title || entry.document_path || "Untitled document"}
              </Link>
              {showProjectLabel && projectName(entry.project_id) ? (
                <span className="fleet-doc-activity-project">{projectName(entry.project_id)}</span>
              ) : null}
              {entry.created_at ? (
                <span className="fleet-doc-history-time" title={stamp(entry.created_at)}>
                  {" · "}
                  {timeAgo(entry.created_at) || stamp(entry.created_at)}
                </span>
              ) : null}
            </div>
            {entry.document_path ? (
              <span className="fleet-doc-activity-path">{entry.document_path}</span>
            ) : null}
            {entry.diff ? (
              <RevisionDiff diff={entry.diff} />
            ) : (
              <p className="fleet-doc-history-empty-diff">No text changes recorded for this revision.</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
