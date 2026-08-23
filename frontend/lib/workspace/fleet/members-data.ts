"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";
import { MutateNetworkError } from "@/lib/workspace/mutation-outcome";

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

import { refresh as refreshAuthSession } from "@/lib/auth/auth-client";
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
  const res = await fleetAuthorizedFetch(path, { credentials: "include" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

async function mutateJson(path: string, method: string, body?: Record<string, unknown>): Promise<any> {
  let res: Response;
  try {
    res = await fleetAuthorizedFetch(path, {
      method,
      credentials: "include",
      headers: buildCookieAuthHeaders(method, { "Content-Type": "application/json" }),
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new MutateNetworkError(e instanceof Error ? e.message : "Network request failed.");
  }
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

export type MyPendingWorkspaceInvite = {
  id: string;
  workspace_id: string;
  workspace_name: string;
  role: WorkspaceRole;
  invited_by_user_id: string | null;
  created_at: number | string | null;
};

function normalizeMyPendingInvite(raw: any): MyPendingWorkspaceInvite {
  const workspaceId = String(raw?.workspace_id || "");
  const rawName = String(raw?.workspace_name || "").trim();
  return {
    id: String(raw?.id || ""),
    workspace_id: workspaceId,
    // NEVER fall back to the id. This line used to read
    // `raw?.workspace_name || raw?.workspace_id`, which put "You've been
    // invited to ws_b5c1fa225ae6" in the invite banner and defeated
    // WorkspaceSwitcher's own "Untitled workspace" guard one layer above --
    // by the time that guard ran, the id was already sitting in the name.
    // An id is an address; if there is no name, say so in words.
    workspace_name: !rawName || rawName === workspaceId ? "Untitled workspace" : rawName,
    role: (String(raw?.role || "viewer").toLowerCase() as WorkspaceRole),
    invited_by_user_id: raw?.invited_by_user_id ?? null,
    created_at: raw?.created_at ?? null,
  };
}

/** The INVITEE's own side of an invite — "someone invited me and I have no
 *  signal anywhere" (MAN). Counterpart to useWorkspacePendingInvites above,
 *  which only an existing member of the target workspace can call and is
 *  therefore useless to the person being invited. Server route:
 *  GET /workspaces/invites/pending (routes_workspaces.
 *  list_my_pending_workspace_invites_route), scoped to the caller's own
 *  authenticated email — no workspace_id in the path, because the caller
 *  isn't a member of the target workspace(s) yet. */
export function useMyPendingWorkspaceInvites() {
  const [invites, setInvites] = useState<MyPendingWorkspaceInvite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getJson("/api/workspaces/invites/pending");
      const items = Array.isArray(data?.items) ? data.items.map(normalizeMyPendingInvite) : [];
      setInvites(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load your pending invites.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { invites, loading, error, refresh };
}

/** Standalone read of the caller's own pending invite ids, outside the hook
 *  above — used to verify a real outcome after an ambiguous
 *  joinPendingWorkspaceInvite failure (PendingWorkspaceInvitesBanner.tsx):
 *  if an invite no longer appears here, it is no longer pending, which
 *  means the join the client couldn't confirm actually went through. */
export async function fetchMyPendingWorkspaceInviteIds(): Promise<string[]> {
  const data = await getJson("/api/workspaces/invites/pending");
  const items = Array.isArray(data?.items) ? data.items : [];
  return items.map((item: any) => String(item?.id || "")).filter(Boolean);
}

/** Accepting an invite CHANGES the caller's own memberships, and the access
 *  token carries a `membership_version` claim that auth._validated_bearer_
 *  context compares against the live one on every request. Granting the
 *  membership bumps that version, so the token the browser is holding one
 *  millisecond after a successful accept is stale: the very next call 401s
 *  and the app bounces to /login — reporting failure immediately after a
 *  success, which is the exact shape CLAUDE.md's outcome-honesty law forbids.
 *  Observed live, not reasoned about: POST .../join returned 200 and the
 *  account-shell fetch behind it returned 401 twice.
 *
 *  Re-minting the token here, before anything else reads the session, is the
 *  whole fix. refresh() is single-flighted in auth-client, so two accepts
 *  racing share one call. It is best-effort on purpose: the membership is
 *  already committed server-side, so a failed refresh must never be reported
 *  as a failed accept — the worst case is one stale-token 401 that the
 *  ordinary refresh-and-retry path then handles.
 *
 *  Shared by BOTH accept paths (the in-app banner and the emailed
 *  /join/{token} link) rather than copied into each — a second copy is the
 *  one the next branch forgets. */
async function refreshSessionAfterMembershipChange(): Promise<void> {
  try {
    await refreshAuthSession();
  } catch {
    /* see above — the accept already happened */
  }
}

/** Accept an invite from the in-app pending-invites list — no signed token
 *  involved (there's no email link here), so the server's whole
 *  authorization story is the caller's authenticated email matching the
 *  invite's own email, exactly like acceptWorkspaceInvite below. */
export async function joinPendingWorkspaceInvite(inviteId: string): Promise<AcceptInviteResult> {
  let data: any;
  try {
    data = await mutateJson(`/api/workspaces/invites/${encodeURIComponent(inviteId)}/join`, "POST");
  } catch (e) {
    return {
      ok: false,
      error: e instanceof Error ? e.message : "Could not join this workspace.",
      ambiguous: e instanceof MutateNetworkError,
    };
  }
  // Deliberately OUTSIDE the try above: the membership is already committed
  // at this point, so nothing after this line may ever be reported as the
  // join failing.
  await refreshSessionAfterMembershipChange();
  return {
    ok: true,
    workspace_id: String(data?.workspace_id || ""),
    role: (String(data?.role || "viewer").toLowerCase() as WorkspaceRole),
  };
}

/** THE one implementation of "what actually happened when this person tried
 *  to join" — shared by both in-app callers (the memberless invitee's
 *  PendingWorkspaceInvitesBanner and the existing-member's
 *  WorkspaceSwitcher). A second copy is the one the next branch forgets,
 *  and this particular logic is exactly the kind that rots quietly: its
 *  whole job is the failure paths nobody exercises by hand.
 *
 *  THREE OUTCOMES, NEVER TWO (CLAUDE.md's outcome-honesty law):
 *
 *    joined       the membership exists. Land the person in it.
 *    failed       nothing was committed. Safe to say so, safe to retry.
 *    unconfirmed  the request produced no response AND we could not read
 *                 the pending list afterwards to find out. Reporting this
 *                 as "failed" invites a retry of something that may already
 *                 have happened; reporting it as "joined" navigates into a
 *                 workspace this account may not be a member of. It gets
 *                 its own words.
 *
 *  The ambiguous branch is why this is not just joinPendingWorkspaceInvite:
 *  a lost response is not a rejection, so before ever showing failure we
 *  ask the source of truth — if the invite is no longer pending, the join
 *  went through and we must not report failure on a success. */
export type SettledInviteJoin =
  | { outcome: "joined"; workspace_id: string }
  | { outcome: "failed"; error: string }
  | { outcome: "unconfirmed"; error: string };

export async function settlePendingInviteJoin(
  inviteId: string,
  fallbackWorkspaceId: string,
): Promise<SettledInviteJoin> {
  const result = await joinPendingWorkspaceInvite(inviteId);
  if (result.ok) {
    return { outcome: "joined", workspace_id: result.workspace_id || fallbackWorkspaceId };
  }
  if (!result.ambiguous) {
    return { outcome: "failed", error: result.error };
  }

  // No response came back. The server may already have committed this.
  let stillPending: boolean;
  try {
    stillPending = (await fetchMyPendingWorkspaceInviteIds()).includes(inviteId);
  } catch {
    return {
      outcome: "unconfirmed",
      error: "Couldn't confirm whether you joined — reload before trying again.",
    };
  }
  if (!stillPending) {
    return { outcome: "joined", workspace_id: fallbackWorkspaceId };
  }
  // Verified: the invite is still pending, so nothing was committed. This
  // is a genuine failure and may be retried safely.
  return { outcome: "failed", error: result.error };
}

/** Decline is a REAL, recorded state (server: status becomes 'declined'),
 *  never a silent client-side dismissal off the list — CLAUDE.md's own rule
 *  against collapsing distinct facts into one. */
export async function declinePendingWorkspaceInvite(inviteId: string): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    await mutateJson(`/api/workspaces/invites/${encodeURIComponent(inviteId)}/decline`, "POST");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Could not decline this invite." };
  }
}

/** The three states create_workspace_invite_route reports back, mirroring
 *  workspace_invite_email_service's own DELIVERY_* constants exactly. They
 *  are kept apart on purpose: "the provider isn't set up" and "the send
 *  failed" call for different words, and neither may be shown as "sent". */
export type InviteEmailDeliveryStatus =
  | "sent"
  | "not_configured"
  | "failed"
  // The inviter has not verified their own email address, so the backend
  // deliberately did not send mail to the invitee under our name. NOT a
  // failure — the invite and its link exist and work. See
  // workspace_invite_email_service.DELIVERY_WITHHELD_UNVERIFIED_SENDER.
  | "withheld_unverified_sender";

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
  if (
    status === "sent" ||
    status === "not_configured" ||
    status === "failed" ||
    status === "withheld_unverified_sender"
  ) {
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
  if (delivery.status === "withheld_unverified_sender") {
    // Names the cause and the one action that changes it. Deliberately not
    // phrased as a failure: the invite is real and the link below works, so
    // "didn't send" would be both wrong and alarming.
    return "Verify your own email to send invites by email — share this link for now.";
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
  // `ambiguous: true` means the request itself never produced a response —
  // the accept may well have gone through server-side; the client just
  // never found out. `ambiguous: false` means the server answered directly
  // (a token it rejected, an email mismatch, etc.) — a real, final fact.
  // Callers must not show the same words for both: see
  // frontend/app/join/[token]/page.tsx, which uses `ambiguous` to decide
  // whether to verify the real outcome before ever telling the person it
  // failed. The accept route itself deliberately treats a genuine replay of
  // an already-accepted token as still-invalid (see
  // test_accept_invite_succeeds_when_invitee_signup_already_auto_accepted_it),
  // so this ambiguity has to be resolved client-side rather than by leaning
  // on the endpoint being safe to blindly retry.
  | { ok: false; error: string; ambiguous: boolean };

/** Accept a workspace invite link. Any authenticated user may call this —
 *  the route itself gates on the caller's authenticated email matching the
 *  invite's email exactly (accept_workspace_invite_route), not on role. */
export async function acceptWorkspaceInvite(token: string): Promise<AcceptInviteResult> {
  let data: any;
  try {
    data = await mutateJson("/api/workspaces/invites/accept", "POST", { token });
  } catch (e) {
    return {
      ok: false,
      error: e instanceof Error ? e.message : "Could not accept this invite.",
      ambiguous: e instanceof MutateNetworkError,
    };
  }
  // Outside the try, for the same reason as joinPendingWorkspaceInvite above.
  await refreshSessionAfterMembershipChange();
  return {
    ok: true,
    workspace_id: String(data?.workspace_id || ""),
    role: (String(data?.role || "viewer").toLowerCase() as WorkspaceRole),
  };
}

/** Best-effort, UNVERIFIED read of the `workspace_id` an invite token
 *  targets — decodes the base64url JSON payload segment of the
 *  header.payload.signature token without checking its HMAC signature.
 *  This is safe to do purely because nothing here is trusted: the value is
 *  used only to pick which workspace to re-check membership against after
 *  an ambiguous accept failure (see /join/[token]/page.tsx) — the real
 *  grant only ever happens inside accept_workspace_invite_route, which
 *  verifies the signature server-side. Returns null on any malformed token
 *  rather than throwing, since this is a UI hint, not a security check. */
export function unverifiedWorkspaceIdFromInviteToken(token: string): string | null {
  try {
    const [, payloadSegment] = String(token || "").split(".");
    if (!payloadSegment) return null;
    const padded = payloadSegment.replace(/-/g, "+").replace(/_/g, "/");
    const padding = "=".repeat((4 - (padded.length % 4)) % 4);
    const json = typeof window !== "undefined" && typeof window.atob === "function"
      ? window.atob(padded + padding)
      : Buffer.from(padded + padding, "base64").toString("utf-8");
    const payload = JSON.parse(json);
    const workspaceId = typeof payload?.workspace_id === "string" ? payload.workspace_id.trim() : "";
    return workspaceId || null;
  } catch {
    return null;
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
