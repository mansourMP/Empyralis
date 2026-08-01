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
