"use client";

/**
 * The project's People tab — the fourth peer beside Tasks, Documents and
 * Agents (2026-08-15). It sits next to Agents on purpose: founder's own
 * framing, that a teammate and an agent belong in the same place, so a
 * project answers "who works on this" in one row of tabs rather than
 * scattering humans into Settings and agents into a rail.
 *
 * NOT A SECOND MEMBERS SURFACE. Everything here reads the same two sources
 * the project page already had in flight — the workspace member list and
 * this project's real `project_memberships` rows (MAN-115,
 * project-members-data.ts) — passed IN as props rather than fetched again,
 * so opening this tab costs no request. Adding somebody is still exactly one
 * control, `ProjectMemberAdd`, sitting in the toolbar directly above this
 * list where it already lived; it was not moved, cloned, or replaced. A tab
 * that grew its own invite form would be the second surface this file's
 * whole point is to avoid.
 *
 * TWO WAYS TO BE ON A PROJECT, AND THE LIST SAYS WHICH. A workspace OWNER
 * reaches every project through auth.enforce_project_access's role bypass
 * and never gets a `project_memberships` row; everyone else is here because
 * somebody granted them access explicitly. Rendering only the explicit rows
 * would drop the owner from their own project — the exact trap
 * MemberAvatarStack.tsx's header already flags and declines to walk into —
 * and rendering them merged with no distinction would claim a grant exists
 * that does not. So the two are unioned and each row states its own basis.
 */

import { useMemo } from "react";

import { MemberAvatar } from "./MemberAvatarStack";
import type { ProjectMember } from "./project-members-data";
import type { WorkspaceMember } from "./members-data";

type PersonRow = {
  userId: string;
  name: string;
  email: string;
  /** How this person reaches the project — the fact the two sources
   *  disagree about, kept rather than flattened. */
  basis: "owner" | "granted";
};

/** Owners first (they can act on everything), then explicit grants in the
 *  order the backend returned them. A person who is BOTH a workspace owner
 *  and carries an explicit row appears once, as an owner: the bypass is the
 *  broader access, so it is the true answer to "what can they do here". */
export function projectPeopleRows(
  workspaceMembers: readonly WorkspaceMember[],
  projectMembers: readonly ProjectMember[],
): PersonRow[] {
  const owners = workspaceMembers.filter((m) => m.role === "owner");
  const seen = new Set(owners.map((o) => o.user_id));
  const rows: PersonRow[] = owners.map((o) => ({
    userId: o.user_id,
    name: o.display_name || o.email || "Unknown",
    email: o.email || "",
    basis: "owner",
  }));
  for (const member of projectMembers) {
    if (!member.user_id || seen.has(member.user_id)) continue;
    seen.add(member.user_id);
    // The workspace list carries the better display name/email when it has
    // this person; the project row is the fallback (and the only source for
    // somebody added straight into the project).
    const fromWorkspace = workspaceMembers.find((m) => m.user_id === member.user_id);
    rows.push({
      userId: member.user_id,
      name: fromWorkspace?.display_name || member.display_name || fromWorkspace?.email || member.email || "Unknown",
      email: fromWorkspace?.email || member.email || "",
      basis: "granted",
    });
  }
  return rows;
}

export function ProjectPeople({
  workspaceMembers,
  projectMembers,
  loading,
  error,
}: {
  workspaceMembers: WorkspaceMember[];
  projectMembers: ProjectMember[];
  loading: boolean;
  error?: string | null;
}) {
  const rows = useMemo(
    () => projectPeopleRows(workspaceMembers, projectMembers),
    [workspaceMembers, projectMembers],
  );

  // "Couldn't load" and "nobody here" are different facts and never share a
  // screen (CLAUDE.md) — a failed read must not render as a confident empty
  // list telling an owner their project has no people on it.
  if (error && rows.length === 0) {
    return (
      <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>
        {error}
      </div>
    );
  }

  if (loading && rows.length === 0) {
    return (
      <div className="fleet-list" aria-busy="true" aria-label="Loading people">
        {[0, 1, 2].map((i) => (
          <div key={i} className="fleet-list-row" style={{ cursor: "default" }}>
            <div className="fleet-skeleton-bar" style={{ width: 28, height: 28, borderRadius: 999 }} />
            <span className="fleet-list-row-main">
              <span className="fleet-skeleton-bar" style={{ width: `${40 + i * 12}%`, height: 12 }} />
              <span className="fleet-skeleton-bar" style={{ width: 140, height: 10, opacity: 0.7 }} />
            </span>
            <div className="fleet-skeleton-bar" style={{ width: 62, height: 18, borderRadius: 999 }} />
          </div>
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="fleet-empty">
        <div className="fleet-empty-title">Nobody on this project yet</div>
        {/* Points at the control that already exists rather than growing a
            second one. An empty state may teach — it has nothing else to
            show — but it still must not duplicate a button sitting a few
            pixels above it. */}
        <div className="fleet-empty-desc">Use the “+” above to add a teammate or invite someone new.</div>
      </div>
    );
  }

  return (
    <div className="fleet-list">
      {rows.map((person, i) => (
        <div key={person.userId} className="fleet-list-row" style={{ cursor: "default" }}>
          <MemberAvatar name={person.name} tintIndex={i} size="sm" />
          <span className="fleet-list-row-main">
            <span className="fleet-list-row-title">{person.name}</span>
            {person.email && person.email !== person.name ? (
              <span className="fleet-list-row-desc">{person.email}</span>
            ) : null}
          </span>
          {/* Labels, never lectures: two words that say which of the two
              real access paths this person is on. */}
          <span className="fleet-badge" style={{ marginLeft: 0 }}>
            {person.basis === "owner" ? "Workspace owner" : "Project member"}
          </span>
        </div>
      ))}
    </div>
  );
}
