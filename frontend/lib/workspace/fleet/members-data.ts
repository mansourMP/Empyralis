"use client";

// Workspace members + invites (Multiplayer Projects Phase 1 — MAN-114).
//
// Backend contract (verified file:line, server_modules/routes_workspaces.py):
//   GET  /workspaces/{workspace_id}/invites        list_workspace_pending_invites_route  :958
//   POST /workspaces/{workspace_id}/invites         create_workspace_invite_route         :904
//   GET  /workspaces/{workspace_id}/members         list_workspace_members_route          :986
//   POST /workspaces/invites/accept                 accept_workspace_invite_route         :1012
// Mounted under "/api" (server.py:395), so these hit the same
// /api/[...path]/route.ts proxy every other live page in this directory
// already uses (see McpServersSection.tsx's file header for the same note).
//
// There is no outbound email sender anywhere in this codebase — an invite is
// never emailed. create_workspace_invite mints a signed, expiring token; the
// owner copies a /join/{token} link and shares it however they like. See
// frontend/app/join/[token]/page.tsx for the accept side.
//
// "Project member" == "workspace member" for now (MAN-70 ruling) — there is
// no per-project ACL table yet, so a project's member list is just this
// workspace's member list.

import { useCallback, useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

export type WorkspaceRole = "viewer" | "member" | "owner";

// Ascending privilege — mirrors auth.py:83's RBAC_ROLE_ORDER exactly (used
// client-side only to grey out roles above the caller's own; the server is
// the real gate, see _require_invite_role / create_workspace_invite).
export const WORKSPACE_ROLE_ORDER: Record<WorkspaceRole, number> = {
  viewer: 0,
  member: 1,
  owner: 2,
};

export const WORKSPACE_ROLES: WorkspaceRole[] = ["viewer", "member", "owner"];

export type WorkspaceMember = {
  user_id: string;
  email: string;
  display_name: string | null;
  role: WorkspaceRole;
  joined_at: string | null;
};

export type WorkspacePendingInvite = {
  id: string;
  workspace_id: string;
  email: string;
  role: WorkspaceRole;
  status: string;
  invited_by_user_id: string | null;
  created_at: number | string | null;
};

async function getJson(path: string): Promise<any> {
  const res = await fetch(path, { credentials: "include" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

async function mutateJson(path: string, method: string, body?: Record<string, unknown>): Promise<any> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: buildCookieAuthHeaders(method, { "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

function normalizeMember(raw: any): WorkspaceMember {
  return {
    user_id: String(raw?.user_id || ""),
    email: String(raw?.email || ""),
    display_name: raw?.display_name ? String(raw.display_name) : null,
    role: (String(raw?.role || "viewer").toLowerCase() as WorkspaceRole),
    joined_at: raw?.joined_at ?? null,
  };
}

function normalizeInvite(raw: any): WorkspacePendingInvite {
  return {
    id: String(raw?.id || ""),
    workspace_id: String(raw?.workspace_id || ""),
    email: String(raw?.email || ""),
    role: (String(raw?.role || "viewer").toLowerCase() as WorkspaceRole),
    status: String(raw?.status || "pending"),
    invited_by_user_id: raw?.invited_by_user_id ?? null,
    created_at: raw?.created_at ?? null,
  };
}

/** Current members of a workspace (== current members of every project in
 *  it, per the MAN-70 ruling above). Used by both the Settings members
 *  section and the project detail page's avatar stack. */
export function useWorkspaceMembers(workspaceId: string) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await getJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/members`);
      const items = Array.isArray(data?.items) ? data.items.map(normalizeMember) : [];
      setMembers(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load members.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { members, loading, error, refresh };
}

/** Pending (not yet accepted, not revoked) invites — owner-visible list so an
 *  owner can see what's outstanding without re-minting a link they already
 *  sent. viewer-minimum-gated server-side (list_workspace_pending_invites_route),
 *  so any member can see this, not just the owner who created them. */
export function useWorkspacePendingInvites(workspaceId: string) {
  const [invites, setInvites] = useState<WorkspacePendingInvite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!workspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await getJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/invites`);
      const items = Array.isArray(data?.items) ? data.items.map(normalizeInvite) : [];
      setInvites(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load pending invites.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { invites, loading, error, refresh };
}

export type CreatedWorkspaceInvite = {
  invite: {
    id: string;
    workspace_id: string;
    email: string;
    role: WorkspaceRole;
    status: string;
    created_at: number | string | null;
  };
  token: string;
  expires_at: number;
};

/** Owner-only (server-enforced, see create_workspace_invite_route's
 *  minimum_role="owner"). Mints a signed, expiring token — never sends an
 *  email, there is no mailer anywhere in this platform. The caller turns
 *  `token` into a /join/{token} link and shares it however they like. */
export async function createWorkspaceInvite(
  workspaceId: string,
  email: string,
  role: WorkspaceRole,
): Promise<CreatedWorkspaceInvite> {
  const data = await mutateJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/invites`, "POST", {
    email,
    role,
  });
  return data as CreatedWorkspaceInvite;
}

export type AcceptInviteResult =
  | { ok: true; workspace_id: string; role: WorkspaceRole }
  | { ok: false; error: string };

/** Accept a workspace invite link. Any authenticated user may call this —
 *  the route itself gates on the caller's authenticated email matching the
 *  invite's email exactly (accept_workspace_invite_route), not on role. */
export async function acceptWorkspaceInvite(token: string): Promise<AcceptInviteResult> {
  try {
    const data = await mutateJson("/api/workspaces/invites/accept", "POST", { token });
    return {
      ok: true,
      workspace_id: String(data?.workspace_id || ""),
      role: (String(data?.role || "viewer").toLowerCase() as WorkspaceRole),
    };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Could not accept this invite." };
  }
}

/** The shareable link an owner copies out of the Settings members section.
 *  /join/[token] (frontend/app/join/[token]/page.tsx) is the only frontend
 *  route that knows how to redeem a workspace_invite_v1 token — do not point
 *  this at /invite/[code], which is an unrelated, pre-existing feature (pilot
 *  program signup codes, server_modules/pilot_invite_service.py). */
export function buildWorkspaceInviteJoinUrl(token: string): string {
  const path = `/join/${encodeURIComponent(token)}`;
  if (typeof window === "undefined" || !window.location?.origin) return path;
  return `${window.location.origin}${path}`;
}
