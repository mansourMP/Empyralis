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
// An invite IS emailed now — create_workspace_invite_route mints the signed,
// expiring token and then hands it to workspace_invite_email_service, which
// sends a /join/{token} link through email_provider_service (Resend). The
// send is best-effort: the token comes back whatever happens, and the
// response's `email_delivery` says which of sent / not_configured / failed
// occurred, so the copy-link fallback appears exactly when it is needed. See
// frontend/app/join/[token]/page.tsx for the accept side.
//
// "Project member" == "workspace member" was the MAN-70 placeholder ruling —
// SUPERSEDED. MAN-115 built the real per-project ACL (project_memberships,
// server_modules/routes_fleet.py:392-500 + projects_repository.py:448-731;
// frontend caller: project-members-data.ts, first wired up by
// ProjectMemberAdd.tsx). useWorkspaceMembers/MemberAvatarStack below were
// deliberately left reading the workspace-wide list rather than being
// switched to the real per-project one — that's a bigger, separate call
// (would need MemberAvatarStack to also union in workspace owners, who
// don't get an explicit project_memberships row) — but the ACL table this
// comment used to say didn't exist now does, and gates real access
// (auth.enforce_project_access).

import { useCallback, useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { useAccountShell } from "@/lib/shell/account-shell-context";

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

/** The caller's own role in ONE specific workspace — read straight off the
 *  account shell bootstrap (frontend/lib/server/load-account-shell-session.ts)
 *  that RootLayout already resolves server-side, before this component (or
 *  any client component) gets its first paint. See account-shell-store.ts's
 *  WorkspaceMembershipRecord.role and workspace-membership-model.ts.
 *
 *  This REPLACES the old useOwnRole(members), which matched a workspace's
 *  member LIST (its own client fetch, GET /api/workspaces/{id}/members)
 *  against a user id from a second, redundant client fetch (GET
 *  /api/auth/me) — two round trips to answer a question the server had
 *  already answered before the page ever mounted. That was MAN's
 *  "3-4 second empty header on refresh" bug (project page's New/invite
 *  controls, ProjectSettings' rename trigger): useCanWriteProject and its
 *  siblings sat on `null` (correctly — CLAUDE.md forbids a control flashing
 *  on then off) until BOTH of those finished, one of them waterfalled
 *  behind whatever else was still loading on the page. The account shell
 *  index has no such wait: it is populated synchronously from a prop on the
 *  very first render (AccountShellProvider's useReducer lazy-initializer),
 *  client AND server, so a gate that reads it resolves in the same paint
 *  the rest of the page does.
 *
 *  `null` only when this account has no membership row for `workspaceId` at
 *  all — shouldn't happen behind the workspace's own route guard, but stays
 *  the safe "don't render" answer if it ever does (same contract as before:
 *  never flash a control that then disappears). */
export function useOwnWorkspaceRole(workspaceId: string): WorkspaceRole | null {
  const { state } = useAccountShell();
  return state.workspaceMembershipIndex[workspaceId]?.role ?? null;
}

/** The caller's own account id, same source as useOwnWorkspaceRole above —
 *  replaces a redundant client-side GET /api/auth/me with data the account
 *  shell bootstrap already carries (`account.id`), synchronously, from the
 *  first render. */
export function useOwnAccountId(): string | null {
  const { state } = useAccountShell();
  return state.account?.id ?? null;
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

/** The three states create_workspace_invite_route reports back, mirroring
 *  workspace_invite_email_service's own DELIVERY_* constants exactly. They
 *  are kept apart on purpose: "the provider isn't set up" and "the send
 *  failed" call for different words, and neither may be shown as "sent". */
export type InviteEmailDeliveryStatus = "sent" | "not_configured" | "failed";

export type InviteEmailDelivery = {
  status: InviteEmailDeliveryStatus;
  email: string;
};

export type CreatedWorkspaceInvite = {
  invite: {
    id: string;
    workspace_id: string;
    email: string;
    role: WorkspaceRole;
    status: string;
    project_id: string | null;
    created_at: number | string | null;
  };
  token: string;
  expires_at: number;
  /** Absent only if the response predates the mailer wiring — read it
   *  through inviteEmailDelivery() below rather than trusting the shape. */
  email_delivery?: InviteEmailDelivery;
};

/** Never throws and never guesses: an unknown or missing `email_delivery`
 *  reads as "failed", which shows the copy-link fallback. The dangerous
 *  default is the other one — telling an owner an email went out when
 *  nothing did is the exact bug this whole path had. */
export function inviteEmailDelivery(created: CreatedWorkspaceInvite | null | undefined): InviteEmailDelivery {
  const raw = created?.email_delivery;
  const status = String(raw?.status || "").toLowerCase();
  const email = String(raw?.email || created?.invite?.email || "");
  if (status === "sent" || status === "not_configured" || status === "failed") {
    return { status: status as InviteEmailDeliveryStatus, email };
  }
  return { status: "failed", email };
}

/** One line for each state. The link itself stays on screen in every case —
 *  it is the fallback, and a fallback that disappears on a good day is one
 *  nobody can find on a bad one. */
export function inviteDeliveryHint(delivery: InviteEmailDelivery): string {
  if (delivery.status === "sent") {
    return delivery.email ? `Invite sent to ${delivery.email}.` : "Invite sent.";
  }
  if (delivery.status === "not_configured") {
    return "Email isn't set up here — share this link instead.";
  }
  return "The invite email didn't send — share this link instead.";
}

/** Owner-only (server-enforced, see create_workspace_invite_route's
 *  minimum_role="owner"). Mints a signed, expiring token AND emails it to
 *  the invitee, reporting the outcome in `email_delivery` — the send is
 *  best-effort by design, so `token` is always usable whatever the mailer
 *  did, and the caller turns it into a /join/{token} link for the fallback.
 *
 *  `projectId` is the MAN-115 follow-up (create_workspace_invite_route,
 *  routes_workspaces.py:919-927): optional, no implicit default. Omitted,
 *  the invite grants workspace membership only. Passed, acceptance ALSO
 *  grants that specific project (projects_repository.grant_invite_project_
 *  access) — this is how ProjectMemberAdd.tsx invites someone straight into
 *  one project instead of the whole workspace. The Settings members section
 *  (MembersSection.tsx) still calls this with no project — that invite
 *  grants workspace access only, unchanged. */
export async function createWorkspaceInvite(
  workspaceId: string,
  email: string,
  role: WorkspaceRole,
  projectId?: string,
): Promise<CreatedWorkspaceInvite> {
  const body: Record<string, unknown> = { email, role };
  if (projectId) body.project_id = projectId;
  const data = await mutateJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/invites`, "POST", body);
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
