"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, ChevronsUpDown, X } from "lucide-react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import {
  declinePendingWorkspaceInvite,
  settlePendingInviteJoin,
  useMyPendingWorkspaceInvites,
  type MyPendingWorkspaceInvite,
} from "./members-data";
import { planPendingInviteIndicator } from "./pending-invite-indicator";
import { planWorkspaceSwitcher } from "./workspace-switcher-shape";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  member: "Member",
  viewer: "Viewer",
};

/**
 * Workspace switcher — MAN-108 Phase 1 bug 4. Server-side, an invited member's
 * membership in the inviting owner's workspace was already correct the moment
 * they accepted (role, permissions, data visibility all check out); what was
 * missing was any UI path to actually get there. The account shell already
 * loads every workspace the signed-in user belongs to
 * (state.workspaceMemberships, from GET /api/auth/account-shell — the same
 * membership list GET /api/workspaces serves) and already knows how to route
 * into one (actions.resolveWorkspaceHref — last-visited route if any,
 * otherwise the membership's own defaultRoute). This just gives that data an
 * actual control: the workspace brand mark/name in the rail header, which
 * used to be inert text, is now the switcher's trigger — the natural spot,
 * since it's the one element that already names "where you are."
 *
 * PENDING INVITES (2026-08-20). PendingWorkspaceInvitesBanner covers the
 * person with NO workspace at all — it renders above the shell, which this
 * switcher never even mounts inside. The person who ALREADY has a workspace
 * and gets invited to a second one had no in-app signal whatsoever; the
 * founder hit exactly that ("I didn't even know of it at first"). Linear
 * puts a count on its own workspace switcher for this; so do we. The
 * rendering rule lives in pending-invite-indicator.ts (pure + tested) and
 * the joined/failed/unconfirmed decision lives in members-data.ts's
 * settlePendingInviteJoin, which the banner calls too — one implementation,
 * two surfaces, no drift.
 *
 * With zero pending invites this component renders byte-identically to what
 * it rendered before that change: no badge, no group, no divider.
 *
 * ONE WORKSPACE PER ACCOUNT (2026-08-23). The "New workspace" row is gone --
 * see workspace-switcher-shape.ts for the decision and the measurements
 * behind it -- and with nothing left to create, a single-workspace popover
 * would open onto one already-checked row and nothing else. So the trigger
 * is not a button at all in that state: `planWorkspaceSwitcher` decides, and
 * the name renders as a plain label. Accounts that already hold several
 * workspaces are untouched and still switch; no stored workspace was
 * renamed, merged or removed by any of this.
 */
export function WorkspaceSwitcher({
  workspaceId,
  workspaceName,
  collapsed,
}: {
  workspaceId: string;
  workspaceName: string;
  collapsed: boolean;
}) {
  const router = useRouter();
  const { state, actions } = useAccountShell();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const memberships = state.workspaceMemberships;

  // Its own read of /workspaces/invites/pending rather than a shared store:
  // the banner above the shell owns the same data, and hoisting it into
  // account-shell context would be a restructure of the shell for a tiny,
  // cold GET. Two reads of a small list on shell mount is the cheaper trade.
  const { invites: pendingInvites, loading: invitesLoading, refresh: refreshInvites } =
    useMyPendingWorkspaceInvites();
  const invitePlan = planPendingInviteIndicator({
    loading: invitesLoading,
    invites: pendingInvites,
    memberWorkspaceIds: memberships.map((membership) => membership.workspace.id),
  });
  // Picker or plain label — the one rule, in one place. Composed rather than
  // recomputed here, so this component and its test cannot hold two opinions.
  const shape = planWorkspaceSwitcher({
    workspaceCount: memberships.length,
    pendingInviteCount: invitePlan.count,
  });

  // A popover open on its last pending invite must not survive that invite
  // being settled: the trigger behind it is a plain label from that render
  // on, so nothing would be left to close it.
  const interactive = shape.interactive;
  useEffect(() => {
    if (!interactive) setOpen(false);
  }, [interactive]);

  const [inviteBusyId, setInviteBusyId] = useState<string | null>(null);
  const [inviteErrorById, setInviteErrorById] = useState<Record<string, string>>({});

  function clearInviteError(id: string) {
    setInviteErrorById((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  async function joinInvite(invite: MyPendingWorkspaceInvite) {
    setInviteBusyId(invite.id);
    clearInviteError(invite.id);
    const settled = await settlePendingInviteJoin(invite.id, invite.workspace_id);
    if (settled.outcome === "joined") {
      // A full navigation, not router.push: the membership list this
      // switcher renders comes from the server-resolved account shell, so a
      // client-side route change would land inside a workspace the shell
      // still believes this account does not belong to.
      window.location.assign(`/w/${encodeURIComponent(settled.workspace_id || invite.workspace_id)}`);
      return;
    }
    // "failed" and "unconfirmed" carry different words from
    // settlePendingInviteJoin and are shown verbatim — never flattened into
    // one "couldn't join", which is the outcome-honesty law's whole point.
    setInviteBusyId(null);
    setInviteErrorById((prev) => ({ ...prev, [invite.id]: settled.error }));
  }

  async function declineInvite(invite: MyPendingWorkspaceInvite) {
    setInviteBusyId(invite.id);
    clearInviteError(invite.id);
    const result = await declinePendingWorkspaceInvite(invite.id);
    setInviteBusyId(null);
    if (!result.ok) {
      setInviteErrorById((prev) => ({ ...prev, [invite.id]: result.error }));
      return;
    }
    await refreshInvites();
  }

  function goTo(targetWorkspaceId: string) {
    setOpen(false);
    if (targetWorkspaceId === workspaceId) return;
    const membership = memberships.find((m) => m.workspace.id === targetWorkspaceId);
    // resolveWorkspaceHref returns the member's last-visited route inside that
    // workspace when we have one on file, so switching back to a workspace
    // drops you where you left off rather than always resetting to Sage.
    const href =
      actions.resolveWorkspaceHref(targetWorkspaceId)
      ?? membership?.defaultRoute
      ?? `/w/${encodeURIComponent(targetWorkspaceId)}/sage`;
    router.push(href);
  }

  const brandLetter = (workspaceName || "E").charAt(0).toUpperCase();

  return (
    <div className="fleet-rail-workspace-switcher" ref={ref}>
      {interactive && open && <div className="fleet-rail-popover-backdrop" onClick={() => setOpen(false)} />}
      {interactive && open && (
        <div className="fleet-rail-workspace-popover" role="menu" aria-label="Switch workspace">
          {memberships.map((membership) => {
            const active = membership.workspace.id === workspaceId;
            // A workspace can exist with no name at all (observed live: one
            // created by an agent on 2026-08-09 whose stored name IS its own
            // id), and the previous `label || id` fallback printed that raw
            // "ws_b5c1fa225ae6" at the owner, who read it as a breach of his
            // account rather than a missing name. An id is an address, never
            // a label — if we don't have a name, say so in words.
            const rawLabel = String(membership.workspace.label || "").trim();
            const label =
              rawLabel && rawLabel !== membership.workspace.id
                ? rawLabel
                : "Untitled workspace";
            return (
              <button
                key={membership.workspace.id}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                className={`fleet-rail-workspace-popover-row${active ? " is-active" : ""}`}
                onClick={() => goTo(membership.workspace.id)}
              >
                <span className="fleet-rail-workspace-popover-mark">{label.charAt(0).toUpperCase()}</span>
                <span className="fleet-rail-workspace-popover-text">
                  <span className="fleet-rail-workspace-popover-name">{label}</span>
                  <span className="fleet-rail-workspace-popover-role">
                    {ROLE_LABEL[membership.role] ?? membership.role}
                  </span>
                </span>
                {active && <Check size={14} strokeWidth={2} aria-hidden="true" />}
              </button>
            );
          })}
          {invitePlan.show && (
            <div className="fleet-rail-workspace-invites" role="group" aria-label="Pending workspace invitations">
              <span className="fleet-rail-workspace-invites-label">
                {invitePlan.count === 1 ? "Invitation" : "Invitations"}
              </span>
              {invitePlan.invites.map((invite) => {
                const name = String(invite.workspace_name || "").trim() || "Untitled workspace";
                const busy = inviteBusyId === invite.id;
                const error = inviteErrorById[invite.id];
                return (
                  <div key={invite.id} className="fleet-rail-workspace-invite-row">
                    <span className="fleet-rail-workspace-popover-mark">{name.charAt(0).toUpperCase()}</span>
                    <span className="fleet-rail-workspace-popover-text">
                      <span className="fleet-rail-workspace-popover-name">{name}</span>
                      <span className="fleet-rail-workspace-popover-role">
                        {error ? error : `Invited as ${ROLE_LABEL[invite.role]?.toLowerCase() ?? invite.role}`}
                      </span>
                    </span>
                    <span className="fleet-rail-workspace-invite-actions">
                      <button
                        type="button"
                        className="fleet-rail-workspace-invite-btn"
                        disabled={busy}
                        aria-label={`Decline the invitation to ${name}`}
                        onClick={() => void declineInvite(invite)}
                      >
                        <X size={13} strokeWidth={1.75} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="fleet-rail-workspace-invite-btn fleet-rail-workspace-invite-btn--join"
                        disabled={busy}
                        onClick={() => void joinInvite(invite)}
                      >
                        {busy ? "Joining…" : "Join"}
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
      <Trigger
        interactive={interactive}
        onOpen={() => setOpen((v) => !v)}
        open={open}
        // Was conditional on `collapsed` — expanded relied on the visible
        // "{brandLetter}{workspaceName}" content to name the button, which
        // the live a11y tree (MAN-145 item 5) showed coming through empty.
        // Unconditional aria-label is the unambiguous fix in both states.
        // In label mode the name is not an action, so it says what it IS.
        label={
          !interactive
            ? workspaceName
            : invitePlan.show
              ? `Switch workspace (current: ${workspaceName}) — ${invitePlan.count} pending ${invitePlan.count === 1 ? "invitation" : "invitations"}`
              : `Switch workspace (current: ${workspaceName})`
        }
        title={collapsed ? workspaceName : undefined}
      >
        <span className="fleet-rail-brand-mark">{brandLetter}</span>
        {!collapsed && (
          <span className="fleet-rail-workspace-name" title={workspaceName}>
            {workspaceName}
          </span>
        )}
        {/* Muted + tabular, never a filled badge — the same treatment the
            Inbox unread count already uses one screen-region away
            (.fleet-rail-item-count), because two different unread-count
            vocabularies inside one rail is worse than a quiet one. In the
            collapsed rail it rides on the brand mark instead (CSS). */}
        {invitePlan.show && (
          <span className="fleet-rail-workspace-invite-count" aria-hidden="true">
            {invitePlan.count}
          </span>
        )}
        {!collapsed && shape.showsChevron && (
          <ChevronsUpDown size={12} strokeWidth={1.75} className="fleet-rail-workspace-chevron" aria-hidden="true" />
        )}
      </Trigger>
    </div>
  );
}

/**
 * ONE element, TWO kinds — and the inert kind is not a disabled button.
 *
 * A `<button disabled>` still announces itself as a control that is
 * temporarily unavailable, which is a lie: with one workspace there is
 * nothing it could ever do. "No dead controls" means the control is not
 * rendered, so in label mode this is a plain `<span>` with no role, no
 * `aria-haspopup`, no `aria-expanded` and no click handler. It keeps the
 * trigger's own class purely for layout — the rail's header geometry is that
 * class — plus a modifier that removes the pointer and the hover wash, since
 * a hover response on something that cannot be pressed is the same lie in
 * CSS.
 */
function Trigger({
  interactive,
  open,
  onOpen,
  label,
  title,
  children,
}: {
  interactive: boolean;
  open: boolean;
  onOpen: () => void;
  label: string;
  title?: string;
  children: React.ReactNode;
}) {
  if (!interactive) {
    return (
      <span className="fleet-rail-workspace-trigger fleet-rail-workspace-trigger--static" title={title} aria-label={label}>
        {children}
      </span>
    );
  }
  return (
    <button
      type="button"
      className="fleet-rail-workspace-trigger"
      onClick={onOpen}
      aria-haspopup="menu"
      aria-expanded={open}
      aria-label={label}
      title={title}
    >
      {children}
    </button>
  );
}
