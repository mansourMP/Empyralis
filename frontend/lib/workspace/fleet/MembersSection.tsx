"use client";

// Workspace members + invites — Settings section (Multiplayer Projects Phase
// 1, MAN-114). See members-data.ts's file header for the full backend
// contract (routes_workspaces.py). "Invite" now actually sends the email
// (workspace_invite_email_service) and the response says whether it went.
// The link stays on screen either way — it is the fallback when the provider
// is unset or the send fails, and a fallback that hides itself on a good day
// is one nobody can find on a bad one.

import { useMemo, useState } from "react";
import { Check, Copy, Plus, UserPlus } from "lucide-react";

import {
  createWorkspaceInvite,
  buildWorkspaceInviteJoinUrl,
  inviteDeliveryHint,
  inviteEmailDelivery,
  useOwnWorkspaceRole,
  useWorkspaceMembers,
  useWorkspacePendingInvites,
  WORKSPACE_ROLE_ORDER,
  WORKSPACE_ROLES,
  type InviteEmailDelivery,
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

export function MembersSection({ workspaceId }: { workspaceId: string }) {
  const { members, loading, error, refresh } = useWorkspaceMembers(workspaceId);
  const { invites, loading: invitesLoading, refresh: refreshInvites } = useWorkspacePendingInvites(workspaceId);
  // Synchronous, from the account shell bootstrap — see members-data.ts's own
  // doc comment on this hook for why (it replaced a redundant GET /api/auth/me
  // + members-list match that used to sit empty for seconds on a hard
  // refresh, MAN). Also now gates the Invite trigger below: creating an
  // invite is server-enforced owner-only (create_workspace_invite_route's
  // minimum_role="owner"), so a non-owner must never see a button that only
  // ever ends in a 403 — CLAUDE.md's "no dead controls" rule, applied the
  // same way ProjectMemberAdd.tsx already applies it to its own trigger.
  const ownRole = useOwnWorkspaceRole(workspaceId);
  const canInvite = ownRole === "owner";

  const [inviteOpen, setInviteOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("member");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [freshLink, setFreshLink] = useState<string | null>(null);
  const [delivery, setDelivery] = useState<InviteEmailDelivery | null>(null);
  const [copied, setCopied] = useState(false);

  // Only offer roles at or below the caller's own — the invite form itself
  // should not dangle an option the server will just reject. This only ever
  // renders once canInvite is true (ownRole === "owner"), so the WORKSPACE_
  // ROLES fallback below is for the one moment ownRole can still be null
  // (no membership row on record for this account), never a loading state.
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
    setDelivery(null);
    try {
      const created = await createWorkspaceInvite(workspaceId, clean, role);
      setFreshLink(buildWorkspaceInviteJoinUrl(created.token));
      setDelivery(inviteEmailDelivery(created));
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
      <h2 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>Members</h2>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Everyone with access to this workspace — and every project in it (there&apos;s no separate per-project
        membership yet).
      </p>

      {error ? <div className="fleet-page-state-body" role="alert" style={{ color: "var(--offline-text)" }}>{error}</div> : null}

      {canInvite ? (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "var(--space-3)" }}>
          <button
            type="button"
            className="fleet-btn fleet-btn--accent"
            onClick={() => { setInviteOpen((v) => !v); setInviteError(null); }}
          >
            <UserPlus size={14} strokeWidth={1.75} /> Invite
          </button>
        </div>
      ) : null}

      {canInvite && inviteOpen ? (
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
            {/* Neutral, not accent: the "Invite" trigger above already
                spends this view's one accent-filled action (craft doctrine).
                Two accent buttons in one view is a bug. */}
            <button type="submit" className="fleet-btn" disabled={inviting || !email.trim()}>
              <Plus size={14} strokeWidth={1.75} /> {inviting ? "Sending…" : "Send invite"}
            </button>
          </form>

          {inviteError ? (
            <p className="fleet-list-row-desc" style={{ color: "var(--offline-text)", marginTop: 8 }}>{inviteError}</p>
          ) : null}

          {freshLink ? (
            <div className="fleet-card" style={{ borderColor: "var(--accent)", marginTop: 10, padding: "var(--space-3)" }}>
              <div className="fleet-list-row-title">
                {inviteDeliveryHint(delivery ?? { status: "failed", email: "" })}
              </div>
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

      {/* Reuses the real row markup (avatar + title/desc + role badge) so
          each placeholder row is shaped like a real member row, and renders
          more than one row — a real member list is almost never exactly
          one row (the workspace owner alone is already one, and this page
          exists because there's usually more than one). */}
      {loading ? (
        <div className="fleet-list" aria-busy="true" aria-label="Loading">
          {[0, 1, 2].map((i) => (
            <div key={i} className="fleet-list-row" style={{ cursor: "default" }}>
              <div className="fleet-skeleton-bar" style={{ width: 28, height: 28, borderRadius: 999 }} />
              <span className="fleet-list-row-main">
                <span className="fleet-skeleton-bar" style={{ width: `${40 + i * 12}%`, height: 12 }} />
                <span className="fleet-skeleton-bar" style={{ width: 140, height: 10, opacity: 0.7 }} />
              </span>
              <div className="fleet-skeleton-bar" style={{ width: 46, height: 18, borderRadius: 999 }} />
            </div>
          ))}
        </div>
      ) : members.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-icon">
            <UserPlus size={20} strokeWidth={1.75} />
          </div>
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
          <h3 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)", fontSize: "var(--text-sm)" }}>
            Pending invites
          </h3>
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
