"use client";

// The real per-project ACL (MAN-115) — server_modules/routes_fleet.py:392-500,
// project_memberships (migrations/add_project_memberships.sql). This is the
// first frontend caller of these three routes:
//   GET    /api/w/{workspace_id}/fleet/projects/{project_id}/members
//   POST   /api/w/{workspace_id}/fleet/projects/{project_id}/members
//   DELETE /api/w/{workspace_id}/fleet/projects/{project_id}/members/{user_id}
// (DELETE has no caller here — nothing in this task's scope removes a
// member, only adds one; add it when a real "revoke access" control exists,
// not preemptively.)
//
// members-data.ts's own file header still calls "project member" and
// "workspace member" the same thing under the MAN-70 placeholder ruling —
// that is now stale. MAN-115 built the table this file reads and writes;
// see ProjectMemberAdd.tsx's file header for the full note on that
// contradiction and why MemberAvatarStack was deliberately left alone.
//
// Response shape (routes_fleet.py): {"ok": true, "members"/"member": ...}
// on success, {"ok": false, "error": "..."} on a caught business-logic
// failure — never a raised HTTPException for those. Auth failures (not a
// workspace owner, not a project viewer) DO raise, so this still has to
// check res.ok too, not just body.ok.

import { useCallback, useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { useOwnAccountId, useOwnWorkspaceRole, WORKSPACE_ROLE_ORDER } from "./members-data";

async function getJson(path: string): Promise<any> {
  const res = await fetch(path, { credentials: "include" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

export type ProjectMember = {
  id: string;
  project_id: string;
  user_id: string;
  email: string | null;
  display_name: string | null;
  avatar_url: string | null;
  role: string;
  added_by: string | null;
  created_at: string | null;
};

function normalizeProjectMember(raw: any): ProjectMember {
  return {
    id: String(raw?.id || ""),
    project_id: String(raw?.project_id || ""),
    user_id: String(raw?.user_id || ""),
    email: raw?.email ? String(raw.email) : null,
    display_name: raw?.display_name ? String(raw.display_name) : null,
    avatar_url: raw?.avatar_url ? String(raw.avatar_url) : null,
    role: String(raw?.role || "member"),
    added_by: raw?.added_by ? String(raw.added_by) : null,
    created_at: raw?.created_at ?? null,
  };
}

async function projectMembersRequest(
  workspaceId: string,
  projectId: string,
  method: string,
  body?: Record<string, unknown>,
  suffix = "",
): Promise<any> {
  const path = `/api/w/${encodeURIComponent(workspaceId)}/fleet/projects/${encodeURIComponent(projectId)}/members${suffix}`;
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: buildCookieAuthHeaders(method, { "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(typeof data?.error === "string" ? data.error : `HTTP ${res.status}`);
  }
  return data;
}

/** This project's explicit membership rows only — NOT workspace owners, who
 *  see the project via enforce_project_access's bypass rather than a row
 *  here (see routes_fleet.py's own MAN-115 comment). A caller that wants
 *  "everyone who can actually see this project" unions this with the
 *  workspace's owner list separately, same as the backend does. */
export function useProjectMembers(workspaceId: string, projectId: string) {
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId || !projectId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await projectMembersRequest(workspaceId, projectId, "GET");
      const items = Array.isArray(data?.members) ? data.members.map(normalizeProjectMember) : [];
      setMembers(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this project's members.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { members, loading, error, refresh };
}

/** Grant a workspace member access to this project. Owner-only, server-
 *  enforced (fleet_add_project_member requires minimum_role="owner" on the
 *  WORKSPACE, checked ahead of the project's own viewer floor) — the target
 *  must already be a workspace member. Idempotent: adding an existing
 *  member updates their row rather than erroring. */
export async function addProjectMember(
  workspaceId: string,
  projectId: string,
  userId: string,
): Promise<ProjectMember> {
  const data = await projectMembersRequest(workspaceId, projectId, "POST", { user_id: userId });
  return normalizeProjectMember(data.member);
}

/** The pure decision behind useCanWriteProject below, pulled out so a caller
 *  that already has its own useProjectMembers(workspaceId, projectId) call
 *  in flight (the project detail page — see ProjectMemberAdd.tsx's own
 *  fetch of the same data) can reuse that data directly instead of this
 *  hook triggering a second, redundant GET of the same endpoint. Browser-
 *  measured on the project page before this split: TWO independent fetches
 *  of both /workspaces/{id}/members and this project's /members on every
 *  load, ~250-400ms each, because useCanWriteProject and ProjectMemberAdd
 *  each ran their own useProjectMembers with no cache between them. Same
 *  logic as before, just callable without forcing its own fetch. */
export function deriveCanWriteProject(
  ownRole: ReturnType<typeof useOwnWorkspaceRole>,
  myUserId: string | null,
  projectMembers: ProjectMember[],
  projectMembersLoading: boolean,
): boolean | null {
  if (ownRole === null) return null;
  if (ownRole === "owner") return true;
  if (WORKSPACE_ROLE_ORDER[ownRole] < WORKSPACE_ROLE_ORDER.member) return false;
  if (projectMembersLoading) return null;
  return projectMembers.some((m) => m.user_id === myUserId);
}

/** Can the caller write inside THIS project — create/edit/delete a
 *  document, and by extension anything else gated the same way
 *  fleet_create_document/fleet_patch_document/fleet_delete_document are
 *  (`member` on the workspace, `enforce_project_access`'s own bypass/row
 *  check underneath). Mirrors auth.enforce_project_access's actual policy
 *  (server_modules/auth.py:5236) rather than re-deriving a new one:
 *    - a workspace OWNER always passes, full stop — enforce_project_access's
 *      RBAC_ROLE_ORDER short-circuit never even looks at project_memberships
 *      for one.
 *    - anyone else needs BOTH a workspace role of `member` or higher (the
 *      floor enforce_workspace_access itself checks first, before the
 *      project-row lookup ever runs) AND an explicit project_memberships row
 *      for this exact project (a `member` on a DIFFERENT project in this
 *      workspace grants nothing here).
 *  A `viewer` therefore always reads `false` here, matching the server's own
 *  `minimum_role="member"` floor on every mutating document route — this
 *  hook exists purely to decide whether to RENDER a control that would
 *  otherwise always fail for that reader (CLAUDE.md: "no dead controls"),
 *  never to gate the actual write, which the server still does regardless.
 *
 *  ownRole and myUserId now come from useOwnWorkspaceRole/useOwnAccountId
 *  (members-data.ts) — the account shell bootstrap RootLayout already
 *  resolved server-side, not a client fetch — so BOTH resolve synchronously
 *  on the very first render, same paint as the rest of the page. That fixed
 *  a real bug (MAN: project header's New/invite controls, and this same
 *  hook's Documents-view callers, sitting empty for 3-4s after a hard
 *  refresh): the old useOwnRole(workspaceMembers) needed its own
 *  GET /api/workspaces/{id}/members PLUS a redundant GET /api/auth/me
 *  before it had an answer, and this hook waited on both before returning
 *  anything but `null`.
 *
 *  The one genuine remaining wait is useProjectMembers — whether THIS
 *  account has an explicit project_memberships row is per-project data the
 *  account shell bootstrap has no reason to carry, so it's still a real
 *  fetch. But an owner's answer never depends on it (enforce_project_access's
 *  RBAC short-circuit doesn't look at project_memberships for one either),
 *  and neither does a sub-`member` role's `false` — both branches return
 *  before touching `projectMembersLoading` below, so only the one case that
 *  actually needs the project-row lookup (a `member`-role workspace user
 *  who may or may not have been added to this specific project) still shows
 *  `null` while it resolves. Same "stay unrendered rather than flash on
 *  then off" contract as before, just no longer paid by the two cases that
 *  never needed it.
 *
 *  Single-fetch caller only — the project detail page has its own
 *  useProjectMembers call already (shared with ProjectMemberAdd) and calls
 *  deriveCanWriteProject directly instead of this hook, to avoid a second
 *  fetch of the same data. Use this hook wherever nothing else on the page
 *  already has that data (e.g. the document detail page). */
export function useCanWriteProject(
  workspaceId: string,
  projectId: string,
): boolean | null {
  const ownRole = useOwnWorkspaceRole(workspaceId);
  const myUserId = useOwnAccountId();
  const { members: projectMembers, loading: projectMembersLoading } = useProjectMembers(workspaceId, projectId);
  return deriveCanWriteProject(ownRole, myUserId, projectMembers, projectMembersLoading);
}

/** pending / accepted / declined / revoked — never collapsed to one state.
 *  See control_plane_repository.list_workspace_invites_for_project's
 *  docstring: an owner asking "did this invite land" deserves the real
 *  answer, the same three-states-never-two doctrine as
 *  inviteEmailDelivery/inviteDeliveryHint in members-data.ts. */
export type ProjectInviteStatus = "pending" | "accepted" | "declined" | "revoked";

export type ProjectInviteStatusItem = {
  id: string;
  workspace_id: string;
  email: string;
  role: string;
  status: ProjectInviteStatus;
  /** Persisted at send time (record_workspace_invite_email_delivery) — null
   *  only for an invite created before that wiring existed. */
  email_delivery_status: "sent" | "not_configured" | "failed" | null;
  invited_by_user_id: string | null;
  created_at: number | string | null;
};

function normalizeProjectInviteStatus(raw: any): ProjectInviteStatusItem {
  const status = String(raw?.status || "pending").toLowerCase();
  return {
    id: String(raw?.id || ""),
    workspace_id: String(raw?.workspace_id || ""),
    email: String(raw?.email || ""),
    role: String(raw?.role || "member"),
    status: (["pending", "accepted", "declined", "revoked"].includes(status) ? status : "pending") as ProjectInviteStatus,
    email_delivery_status: raw?.email_delivery_status ? String(raw.email_delivery_status) as any : null,
    invited_by_user_id: raw?.invited_by_user_id ?? null,
    created_at: raw?.created_at ?? null,
  };
}

/** The owner-visible counterpart to ProjectMemberAdd's own "Send invite"
 *  form: what happened to the invites already sent for THIS project. Server
 *  route: GET /workspaces/{workspace_id}/projects/{project_id}/invites
 *  (routes_workspaces.list_project_invite_status_route) — deliberately a
 *  separate endpoint from useWorkspacePendingInvites (workspace-wide,
 *  pending-only, members-data.ts), because this one is project-scoped and
 *  reports every status, not just pending.
 *
 *  `enabled` defaults true but ProjectMemberAdd.tsx passes its own popover
 *  `open` state — this data is only ever shown inside that popover, so
 *  fetching it on every project-page mount (this component renders on every
 *  such page, popover open or not) would be a fetch nobody asked for on
 *  every page load. Mirrors the "a card opens with what is already known,
 *  it does not fetch on click" doctrine in reverse: don't fetch what isn't
 *  being shown, either. */
export function useProjectInviteStatus(workspaceId: string, projectId: string, enabled: boolean = true) {
  const [items, setItems] = useState<ProjectInviteStatusItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId || !projectId || !enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await getJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}/invites`,
      );
      const parsed = Array.isArray(data?.items) ? data.items.map(normalizeProjectInviteStatus) : [];
      setItems(parsed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load invite status.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, projectId, enabled]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { items, loading, error, refresh };
}
