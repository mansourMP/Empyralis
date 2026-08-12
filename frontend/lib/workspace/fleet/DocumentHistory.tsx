"use client";

/**
 * A document's revision history -- the human-facing gap this closes.
 * project_document_revisions (patch-native diffs, changed_by_type
 * human/agent/external_agent/system) shipped with exactly ONE production
 * caller: mcp_server.py's empyralis_list_document_revisions, an MCP tool
 * only an agent could call. An agent editing a project document could
 * already see who touched it and when; a human looking at the same
 * document over the same table could not (CLAUDE.md: "built, tested, and
 * never wired"). GET .../documents/{id}/revisions
 * (routes_fleet.fleet_list_document_revisions) + useFleetDocumentRevisions
 * (documents-data.ts) are the wiring; this file is the surface.
 *
 * STRUCTURE mirrors TaskDetailView.tsx's own Activity section (a plain
 * `.fleet-task-page-section` with an `<h2>`, below the main content) --
 * the same "who did what, when" shape a task's comment/activity feed
 * already uses, extended here to a document's own write history instead of
 * a comment thread. Unlike Activity, THERE IS NO EMPTY STATE: a document
 * with zero or one revision (freshly created, never edited since) renders
 * NOTHING here at all -- CLAUDE.md's "no dead controls" applies to a whole
 * section exactly as it applies to a single button, and "History" over an
 * empty list would be a heading admitting it has nothing to show.
 *
 * READ-ONLY, ON PURPOSE. There is no restore/rollback control anywhere in
 * this file -- CLAUDE.md: "a surface must earn its place"; the founder
 * asked for tracked history, and fleet_list_document_revisions' own
 * docstring already makes the identical call for the route this reads.
 *
 * ATTRIBUTION reuses TaskDetailView.tsx's resolveCommentAuthor/
 * TaskActorBadge shape rather than inventing a fourth: `changed_by_type` is
 * the EXACT vocabulary add_task_comment's own author_type already
 * established (human / agent / external_agent), plus "system" -- a value
 * project_documents_repository.py's docstring reserves but nothing
 * produces today, handled defensively rather than assumed unreachable, the
 * same posture resolveCommentAuthor takes for its own "unattributed"
 * fallback. A HUMAN write's `changed_by_display_name` is never populated
 * (routes_fleet.py's fleet_create_document/fleet_patch_document pass
 * changed_by_type="human" only -- see CLAUDE.md on not touching that write
 * path here) so a human actor resolves against the `members` prop by
 * `changed_by_id`, exactly like TaskActorBadge resolves a task's
 * created_by. An AGENT write is the same story against `agents`. An
 * EXTERNAL agent write (MCP) always carries changed_by_display_name (see
 * mcp_server.py's document tools), so that one never needs a lookup.
 */

import { AgentSigil } from "./fleet-indicators";
import { MemberAvatar } from "./MemberAvatarStack";
import { formatDateTime, timeAgo } from "./fleet-presentation";
import { useFleetDocumentRevisions, type FleetDocumentRevision } from "./documents-data";
import type { FleetAgent } from "./fleet-data";
import type { WorkspaceMember } from "./members-data";
import "./document-detail.css";

function stamp(value: string): string {
  return formatDateTime(value, { dateStyle: "medium", timeStyle: "short" });
}

type ResolvedRevisionActor = {
  kind: "human" | "agent" | "external_agent" | "other";
  label: string;
  /** AgentSigil's seed / MemberAvatar's tint key -- stable per actor so the
   *  same person or agent draws the same mark on every revision, matching
   *  TaskActorBadge's own convention. */
  seed: string;
  role?: string;
  memberIndex: number;
};

/** `ext_agent_5f3a2b1c9d0e4f11` -> `5f3a2b1c` -- same short-id fallback
 *  resolveCommentAuthor's own externalAgentShortId uses, only reached when
 *  an external agent write somehow carries no display name at all. */
function externalAgentShortId(id: string): string {
  return id.replace(/^ext_agent_/, "").slice(0, 8);
}

function resolveRevisionActor(
  revision: FleetDocumentRevision,
  agents: FleetAgent[],
  members: WorkspaceMember[],
): ResolvedRevisionActor {
  const id = revision.changed_by_id || "";
  const snapshot = revision.changed_by_display_name || "";

  if (revision.changed_by_type === "agent") {
    const agent = agents.find((a) => a.agent_id === id);
    const label = agent?.label || snapshot || "Agent";
    return { kind: "agent", label, seed: id || label, memberIndex: 0 };
  }

  if (revision.changed_by_type === "human") {
    const memberIndex = members.findIndex((m) => m.user_id === id);
    if (memberIndex >= 0) {
      const member = members[memberIndex];
      return {
        kind: "human",
        label: member.display_name || member.email || "Someone",
        seed: id,
        role: member.role,
        memberIndex,
      };
    }
    // `members` is the WORKSPACE roster (useWorkspaceMembers), not a
    // project-scoped list, so this is only reached for someone who has
    // since left the workspace entirely. Degrades to a plain label, same
    // as resolveCommentAuthor does when a human commenter isn't in
    // `members` either.
    return { kind: "human", label: snapshot || "Someone", seed: id || "human", memberIndex: -1 };
  }

  if (revision.changed_by_type === "external_agent") {
    const label = snapshot || (id ? `External agent ${externalAgentShortId(id)}` : "External agent");
    return { kind: "external_agent", label, seed: id || label, memberIndex: 0 };
  }

  // "system" (reserved, unproduced today) or the backend's own "unknown"
  // fallback for an empty column -- said plainly rather than guessed at.
  return {
    kind: "other",
    label: snapshot || (revision.changed_by_type === "system" ? "System" : "Unknown"),
    seed: id || revision.id,
    memberIndex: 0,
  };
}

function RevisionActorBadge({ actor }: { actor: ResolvedRevisionActor }) {
  if (actor.kind === "human") {
    return (
      <span className="fleet-doc-history-actor">
        <MemberAvatar name={actor.label} role={actor.role as any} size="xs" tintIndex={Math.max(actor.memberIndex, 0)} />
        <span>{actor.label}</span>
      </span>
    );
  }
  if (actor.kind === "other") {
    return <span className="fleet-doc-history-actor fleet-doc-history-actor--muted">{actor.label}</span>;
  }
  return (
    <span className="fleet-doc-history-actor" title={actor.kind === "external_agent" ? "External agent" : "Agent"}>
      <AgentSigil seed={actor.seed} size={13} />
      <span>{actor.label}</span>
    </span>
  );
}

/** One diff line's visual class -- unified-diff convention (`+`/`-` body
 *  lines, `+++`/`---` file headers, `@@` hunk headers) plus the same
 *  convention for the title-only diff _compute_document_diff emits when
 *  just the title changed ("- title: old" / "+ title: new"), which shares
 *  the identical `+`/`-` prefix and is correctly styled as add/remove by
 *  the same rule. Order matters: `+++`/`---` must be checked BEFORE the
 *  bare `+`/`-` check, or a file header would be miscolored as a changed
 *  line. */
function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "fleet-doc-diff-line fleet-doc-diff-line--meta";
  if (line.startsWith("@@")) return "fleet-doc-diff-line fleet-doc-diff-line--hunk";
  if (line.startsWith("+")) return "fleet-doc-diff-line fleet-doc-diff-line--add";
  if (line.startsWith("-")) return "fleet-doc-diff-line fleet-doc-diff-line--remove";
  return "fleet-doc-diff-line";
}

/** Renders the stored unified diff readably -- added/removed lines, not a
 *  raw blob (the founder's own ask). A blank line renders as a non-breaking
 *  space so it still occupies a row instead of collapsing to nothing. */
function RevisionDiff({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <pre className="fleet-doc-history-diff">
      {lines.map((line, i) => (
        <div key={i} className={diffLineClass(line)}>
          {line.length ? line : " "}
        </div>
      ))}
    </pre>
  );
}

export function DocumentHistory({
  workspaceId,
  documentId,
  updatedAt,
  agents,
  members,
}: {
  workspaceId: string;
  documentId: string;
  /** The document's own `updated_at`, passed through purely as a refresh
   *  trigger (see useFleetDocumentRevisions' own doc) -- when a save lands
   *  this changes, and history re-fetches without the reader having to
   *  reload the page to see the edit they just made. */
  updatedAt: string | null;
  /** This project's agents (useFleetAgents) -- resolves an "agent" revision
   *  the same way TaskActorBadge resolves a task's assignee/creator. */
  agents: FleetAgent[];
  /** The workspace roster (useWorkspaceMembers) -- resolves a "human"
   *  revision's changed_by_id to a real name. Deliberately the WORKSPACE
   *  list, not project-scoped project-members-data.ts's ProjectMember: a
   *  workspace OWNER edits every project without an explicit
   *  project_memberships row (auth.enforce_project_access's own bypass),
   *  so a project-scoped list would resolve the single most common human
   *  editor -- the owner -- to nothing. Same source TaskDetailView.tsx
   *  already threads down for its own actor resolution. */
  members: WorkspaceMember[];
}) {
  const { revisions, loading } = useFleetDocumentRevisions(workspaceId, documentId, updatedAt);

  // A document that was only ever created (one revision, or the fetch
  // hasn't resolved yet) gets no History section at all -- see file header
  // on why this is not an empty state. `revisions` also comes back with a
  // SINGLE entry for a document nobody has edited since creation, which is
  // just as much "nothing to show" as zero: revision #1 IS the document as
  // it stands right now, so a one-item history list would only ever repeat
  // what the reader is already looking at above.
  if (loading || revisions.length <= 1) return null;

  return (
    <section className="fleet-task-page-section fleet-doc-history" aria-label="History">
      <h2 className="fleet-task-page-section-title">History</h2>
      <ul className="fleet-doc-history-list">
        {revisions.map((revision) => {
          const actor = resolveRevisionActor(revision, agents, members);
          return (
            <li key={revision.id} className="fleet-doc-history-item">
              <div className="fleet-doc-history-head">
                <RevisionActorBadge actor={actor} />
                {revision.created_at ? (
                  <span className="fleet-doc-history-time" title={stamp(revision.created_at)}>
                    {" · "}
                    {timeAgo(revision.created_at) || stamp(revision.created_at)}
                  </span>
                ) : null}
              </div>
              {revision.diff ? (
                <RevisionDiff diff={revision.diff} />
              ) : (
                <p className="fleet-doc-history-empty-diff">No text changes recorded for this revision.</p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
