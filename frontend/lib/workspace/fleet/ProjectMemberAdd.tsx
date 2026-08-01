"use client";

/**
 * The "+" beside the member avatar stack on a project's toolbar — grants a
 * workspace member explicit access to THIS project (project_memberships,
 * MAN-115) or invites someone brand new by email straight into it.
 *
 * Owner-only, matching both backend routes' own gating exactly
 * (fleet_add_project_member and create_workspace_invite_route both require
 * minimum_role="owner" on the workspace) — a non-owner never sees this
 * control at all, not a disabled one. CLAUDE.md: "If a control cannot be
 * used in the current state, it is not rendered."
 *
 * Reuses the established popover idiom verbatim — TaskViewOptions.tsx's own
 * file header names the contract: the box is .fleet-toolbar-popover
 * (position, elevation), dismissal is outside-pointerdown or Esc. Trigger is
 * the same neutral .fleet-icon-btn every other toolbar icon uses — "New
 * task" already owns this view's one accent-filled action per the craft
 * doctrine, so this stays neutral throughout, including its own "Send
 * invite" button.
 *
 * ONE surface for both things the founder asked for — add an existing
 * workspace member, or invite a brand-new person by email — not a second
 * tab or a separate screen. They're both grants into the same real table:
 * an add is a project_memberships row today, an invite is one the moment it
 * gets accepted (projects_repository.grant_invite_project_access). One
 * popover, two short sections, is the whole feature.
 *
 * GROUND TRUTH ON "project member" vs "workspace member" (this task asked
 * for it in writing): members-data.ts's file header used to say these were
 * the same thing under the MAN-70 placeholder ruling. They are not, as of
 * MAN-115 — server_modules/routes_fleet.py:392-500 and
 * projects_repository.py:448-731 build and query a real `project_memberships`
 * Postgres table, and auth.enforce_project_access actually gates on it (a
 * workspace member with no row there, and no owner role, cannot open the
 * project). This component is the first frontend caller of that table.
 * MemberAvatarStack itself was deliberately left reading the workspace-wide
 * list rather than rewired to the real per-project one — see that file's own
 * updated header for why (it would also need to union in workspace owners,
 * who never get an explicit row).
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, Copy, Plus } from "lucide-react";

import { me } from "@/lib/auth/auth-client";
import {
  createWorkspaceInvite,
  buildWorkspaceInviteJoinUrl,
  WORKSPACE_ROLES,
  type WorkspaceMember,
  type WorkspaceRole,
} from "@/lib/workspace/fleet/members-data";
import { useProjectMembers, addProjectMember } from "@/lib/workspace/fleet/project-members-data";
import { MemberAvatar } from "@/lib/workspace/fleet/MemberAvatarStack";

function roleLabel(role: WorkspaceRole): string {
  if (role === "owner") return "Owner";
  if (role === "member") return "Member";
  return "Viewer";
}

/** The caller's own workspace role, derived from the member list the page
 *  already loaded (matched by user id from /api/auth/me) — same derivation
 *  MembersSection.tsx uses for the same reason: there is no dedicated "my
 *  role" endpoint, and the server is the real gate on every mutation below
 *  regardless of what this computes. Undefined while /api/auth/me is still
 *  in flight, so the "+" stays unrendered rather than flashing on then off
 *  for a non-owner. */
function useOwnRole(members: WorkspaceMember[]): WorkspaceRole | null {
  const [myUserId, setMyUserId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void me()
      .then((data) => {
        if (cancelled) return;
        const user = (data as { user?: { id?: string } } | null)?.user;
        setMyUserId(user?.id ? String(user.id) : null);
      })
      .catch(() => {
        if (!cancelled) setMyUserId(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const mine = members.find((m) => m.user_id === myUserId);
  return mine?.role ?? null;
}

export function ProjectMemberAdd({
  workspaceId,
  projectId,
  workspaceMembers,
}: {
  workspaceId: string;
  projectId: string;
  /** The page's own useWorkspaceMembers(workspaceId) list — passed down
   *  rather than fetched again here. MemberAvatarStack right next to this
   *  control already does its own fetch of the exact same endpoint; a third
   *  copy in this file would be a third redundant round trip for the same
   *  data on every load of this page. */
  workspaceMembers: WorkspaceMember[];
}) {
  const ownRole = useOwnRole(workspaceMembers);
  const { members: projectMembers, loading: projectMembersLoading, refresh: refreshProjectMembers } =
    useProjectMembers(workspaceId, projectId);

  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const [addingId, setAddingId] = useState<string | null>(null);
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set());
  const [addError, setAddError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("member");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [freshLink, setFreshLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // FleetToolbar's dismissal contract, verbatim: outside pointerdown, or Esc.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // Server-enforced floor on both routes this popover calls (owner-only) —
  // rendering nothing at all for anyone else, not a disabled "+", is the
  // CLAUDE.md "no dead controls" rule applied to the trigger itself.
  if (ownRole !== "owner") return null;

  const projectMemberIds = new Set(projectMembers.map((m) => m.user_id));
  // Workspace owners already see every project via enforce_project_access's
  // role bypass — never a project_memberships row — so they're excluded
  // from "addable" the same way routes_fleet.py's own list route excludes
  // them from the roster it returns.
  const addable = workspaceMembers.filter(
    (m) => m.role !== "owner" && !projectMemberIds.has(m.user_id) && !addedIds.has(m.user_id),
  );

  async function handleAdd(userId: string) {
    setAddingId(userId);
    setAddError(null);
    try {
      await addProjectMember(workspaceId, projectId, userId);
      setAddedIds((prev) => new Set(prev).add(userId));
      await refreshProjectMembers();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Could not add this member.");
    } finally {
      setAddingId(null);
    }
  }

  async function handleInvite(e: FormEvent) {
    e.preventDefault();
    const clean = email.trim().toLowerCase();
    if (!clean || inviting) return;
    setInviting(true);
    setInviteError(null);
    setFreshLink(null);
    try {
      const created = await createWorkspaceInvite(workspaceId, clean, role, projectId);
      setFreshLink(buildWorkspaceInviteJoinUrl(created.token));
      setEmail("");
    } catch (e2) {
      setInviteError(e2 instanceof Error ? e2.message : "Could not create this invite.");
    } finally {
      setInviting(false);
    }
  }

  function copyLink() {
    if (!freshLink) return;
    navigator.clipboard
      ?.writeText(freshLink)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  }

  return (
    <div className="fleet-member-invite" ref={ref}>
      <button
        type="button"
        className={`fleet-icon-btn${open ? " is-active" : ""}`}
        aria-label="Add people to this project"
        aria-expanded={open}
        title="Add people"
        onClick={() => setOpen((v) => !v)}
      >
        <Plus size={16} strokeWidth={1.75} />
      </button>

      {open && (
        <div className="fleet-toolbar-popover fleet-member-invite-popover">
          <div className="fleet-member-invite-section">
            <h2 className="fleet-member-invite-heading">Add from workspace</h2>
            {projectMembersLoading ? (
              <div className="fleet-member-invite-empty">Loading…</div>
            ) : addable.length === 0 ? (
              <div className="fleet-member-invite-empty">Every workspace member already has access.</div>
            ) : (
              addable.map((m) => (
                <div key={m.user_id} className="fleet-member-invite-row">
                  <MemberAvatar name={m.display_name || m.email} role={m.role} size="sm" />
                  <span className="fleet-member-invite-row-main">
                    <span className="fleet-member-invite-row-name">{m.display_name || m.email}</span>
                  </span>
                  <button
                    type="button"
                    className="fleet-icon-btn fleet-member-invite-row-add"
                    aria-label={`Add ${m.display_name || m.email} to this project`}
                    disabled={addingId === m.user_id}
                    onClick={() => handleAdd(m.user_id)}
                  >
                    <Plus size={14} strokeWidth={1.75} />
                  </button>
                </div>
              ))
            )}
            {addError ? <div className="fleet-member-invite-error">{addError}</div> : null}
          </div>

          <div className="fleet-member-invite-divider" />

          <div className="fleet-member-invite-section">
            <h2 className="fleet-member-invite-heading">Invite by email</h2>
            <form className="fleet-member-invite-form" onSubmit={handleInvite}>
              <div className="fleet-member-invite-input-row">
                <input
                  type="email"
                  className="fleet-member-invite-input"
                  placeholder="teammate@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="off"
                />
                <select
                  className="fleet-member-invite-select"
                  value={role}
                  onChange={(e) => setRole(e.target.value as WorkspaceRole)}
                  aria-label="Invite role"
                >
                  {WORKSPACE_ROLES.map((r) => (
                    <option key={r} value={r}>
                      {roleLabel(r)}
                    </option>
                  ))}
                </select>
              </div>
              <button type="submit" className="fleet-btn fleet-member-invite-submit" disabled={inviting || !email.trim()}>
                {inviting ? "Sending…" : "Send invite"}
              </button>
            </form>
            {inviteError ? <div className="fleet-member-invite-error">{inviteError}</div> : null}
            {freshLink ? (
              <>
                <div className="fleet-member-invite-link-row">
                  <code className="fleet-member-invite-link">{freshLink}</code>
                  <button type="button" className="fleet-icon-btn" onClick={copyLink} aria-label="Copy invite link">
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                  </button>
                </div>
                <div className="fleet-member-invite-hint">No email sender yet — copy this link and share it yourself.</div>
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
