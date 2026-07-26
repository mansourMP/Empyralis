"use client";

// Workspace members + invites — Settings section (Multiplayer Projects Phase
// 1, MAN-114). See members-data.ts's file header for the full backend
// contract (routes_workspaces.py). No email sender exists anywhere on this
// platform, so "Invite" mints a link the owner copies and shares however
// they like — it never sends anything itself.

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Plus, UserPlus } from "lucide-react";

import { me } from "@/lib/auth/auth-client";
import {
  createWorkspaceInvite,
  buildWorkspaceInviteJoinUrl,
  useWorkspaceMembers,
  useWorkspacePendingInvites,
  WORKSPACE_ROLE_ORDER,
  WORKSPACE_ROLES,
  type WorkspaceRole,
} from "@/lib/workspace/fleet/members-data";
import { formatDate } from "@/lib/workspace/fleet/fleet-presentation";
import { MemberAvatar } from "@/lib/workspace/fleet/MemberAvatarStack";

function roleLabel(role: WorkspaceRole): string {
  if (role === "owner") return "Owner";
  if (role === "member") return "Member";
  return "Viewer";
}

/** Invite `created_at` comes back as epoch SECONDS (control_plane_repository
 *  `_ts_or_none`, backed by Python's `int(time.time())`), not an ISO string
 *  like every other timestamp `formatDate`/`timeAgo` in this directory
 *  already handle — `new Date(seconds)` alone would misread it as
 *  milliseconds-since-epoch and print a Jan-1970 date. */
function inviteCreatedAtDate(value: number | string | null): Date | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return new Date(value * 1000);
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** The caller's own role in this workspace, derived from the members list
 *  itself (matched by user id from /api/auth/me) — there is no dedicated
 *  "my role" endpoint, and this avoids inventing one for what is purely a
 *  client-side affordance (greying out roles above your own in the invite
 *  form). The server is the real gate either way: _require_invite_role /
 *  create_workspace_invite_route independently refuse to mint a token for a
 *  role above the inviter's own. */
function useOwnRole(members: ReturnType<typeof useWorkspaceMembers>["members"]): WorkspaceRole | null {
  const [myUserId, setMyUserId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void me().then((data) => {
      if (cancelled) return;
      const user = (data as { user?: { id?: string } } | null)?.user;
      setMyUserId(user?.id ? String(user.id) : null);
    }).catch(() => {
      if (!cancelled) setMyUserId(null);
    });
    return () => { cancelled = true; };
  }, []);

  return useMemo(() => {
    if (!myUserId) return null;
    const mine = members.find((m) => m.user_id === myUserId);
    return mine?.role ?? null;
  }, [members, myUserId]);
}

export function MembersSection({ workspaceId }: { workspaceId: string }) {
  const { members, loading, error, refresh } = useWorkspaceMembers(workspaceId);
  const { invites, loading: invitesLoading, refresh: refreshInvites } = useWorkspacePendingInvites(workspaceId);
  const ownRole = useOwnRole(members);

  const [inviteOpen, setInviteOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("member");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [freshLink, setFreshLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Only offer roles at or below the caller's own — a member who happens to
  // load this page (viewer-minimum-gated GETs) can still see this section,
  // but the invite form itself should not dangle an option the server will
  // just reject. Falls back to allowing everything while ownRole is still
  // loading rather than flashing a form that's briefly wrong either way.
  const allowedRoles = useMemo(() => {
    if (!ownRole) return WORKSPACE_ROLES;
    return WORKSPACE_ROLES.filter((r) => WORKSPACE_ROLE_ORDER[r] <= WORKSPACE_ROLE_ORDER[ownRole]);
  }, [ownRole]);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    const clean = email.trim().toLowerCase();
    if (!clean || inviting) return;
    setInviting(true);
    setInviteError(null);
    setFreshLink(null);
    try {
      const created = await createWorkspaceInvite(workspaceId, clean, role);
      setFreshLink(buildWorkspaceInviteJoinUrl(created.token));
      setEmail("");
      await refreshInvites();
    } catch (e2) {
      setInviteError(e2 instanceof Error ? e2.message : "Could not create this invite.");
    } finally {
      setInviting(false);
    }
  }

  function copyLink() {
    if (!freshLink) return;
    navigator.clipboard?.writeText(freshLink).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  }

  return (
    <>
      <div className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>Members</div>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Everyone with access to this workspace — and every project in it (there's no separate per-project
        membership yet). Invites are shared as a link; there's no email sender on this platform to send one through.
      </p>

      {error ? <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>{error}</div> : null}

      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "var(--space-3)" }}>
        <button
          type="button"
          className="fleet-btn fleet-btn--accent"
          onClick={() => { setInviteOpen((v) => !v); setInviteError(null); }}
        >
          <UserPlus size={14} strokeWidth={1.75} /> Invite
        </button>
      </div>

      {inviteOpen ? (
        <div className="fleet-card" style={{ padding: "var(--space-3)", marginBottom: "var(--space-4)" }}>
          <form onSubmit={handleInvite} style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <input
              className="fleet-wizard-input"
              type="email"
              placeholder="teammate@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{ flex: "2 1 220px" }}
              autoFocus
            />
            <select
              className="fleet-wizard-input"
              value={role}
              onChange={(e) => setRole(e.target.value as WorkspaceRole)}
              style={{ flex: "1 1 120px" }}
            >
              {allowedRoles.map((r) => (
                <option key={r} value={r}>{roleLabel(r)}</option>
              ))}
            </select>
            <button type="submit" className="fleet-btn fleet-btn--accent" disabled={inviting || !email.trim()}>
              <Plus size={14} strokeWidth={1.75} /> {inviting ? "Creating…" : "Create invite link"}
            </button>
          </form>

          {inviteError ? (
            <p className="fleet-list-row-desc" style={{ color: "var(--offline-text)", marginTop: 8 }}>{inviteError}</p>
          ) : null}

          {freshLink ? (
            <div className="fleet-card" style={{ borderColor: "var(--accent)", marginTop: 10, padding: "var(--space-3)" }}>
              <div className="fleet-list-row-title">Copy this link and share it — it won&apos;t be shown again here.</div>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
                <code style={{ flex: 1, overflow: "auto", fontSize: "var(--text-sm)", background: "var(--bg-inset)", padding: "var(--space-2)", borderRadius: "var(--radius-control)" }}>
                  {freshLink}
                </code>
                <button type="button" className="fleet-btn" onClick={copyLink}>
                  {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}
                </button>
              </div>
              <p className="fleet-list-row-desc" style={{ marginTop: 6 }}>
                Only works for the email it was created for, and expires in 7 days.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}

      {loading ? (
        <div className="fleet-list"><div className="fleet-list-row"><div className="fleet-skeleton-bar" style={{ width: "40%", height: 12 }} /></div></div>
      ) : members.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No members yet</div>
          <div className="fleet-empty-desc">Invite someone above to give them access to this workspace.</div>
        </div>
      ) : (
        <div className="fleet-list">
          {members.map((m) => (
            <div key={m.user_id} className="fleet-list-row" style={{ cursor: "default" }}>
              <MemberAvatar name={m.display_name || m.email} role={m.role} size="sm" />
              <span className="fleet-list-row-main">
                <span className="fleet-list-row-title">{m.display_name || m.email}</span>
                <span className="fleet-list-row-desc">{m.email}</span>
              </span>
              <span className="fleet-badge" style={{ marginLeft: 0 }}>{roleLabel(m.role)}</span>
            </div>
          ))}
        </div>
      )}

      {!invitesLoading && invites.length > 0 ? (
        <>
          <div className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)", fontSize: "var(--text-sm)" }}>
            Pending invites
          </div>
          <div className="fleet-list">
            {invites.map((inv) => {
              const createdAt = inviteCreatedAtDate(inv.created_at);
              return (
                <div key={inv.id} className="fleet-list-row" style={{ cursor: "default" }}>
                  <span className="fleet-list-row-main">
                    <span className="fleet-list-row-title">{inv.email}</span>
                    <span className="fleet-list-row-desc">
                      {createdAt ? `Invited ${formatDate(createdAt)}` : "Invited"}
                    </span>
                  </span>
                  <span className="fleet-badge" style={{ marginLeft: 0 }}>{roleLabel(inv.role)}</span>
                </div>
              );
            })}
          </div>
        </>
      ) : null}
    </>
  );
}
