'use client';

// The founder invited someone and nothing appeared anywhere for them to see
// it (MAN). members-data.ts already had useMyPendingWorkspaceInvites /
// joinPendingWorkspaceInvite / declinePendingWorkspaceInvite with zero
// callers -- this is that caller. Mounted in app/(account)/layout.tsx,
// ABOVE the workspace-scoped shell (FleetShell/WorkspaceSwitcher only
// render once the user is already a member of a workspace), so a person
// with ONLY a pending invite -- no membership anywhere yet -- still sees
// it the moment they land in the app.
//
// Both actions stay neutral (.fleet-btn, no --accent) rather than making
// "Join" the accent button: this banner can render above ANY page, several
// of which already spend their own one accent-filled action elsewhere on
// screen (e.g. a project's "New task"). Two accent buttons visible at once
// is the craft-doctrine bug MembersSection.tsx's own "Send invite" button
// already avoids for the same reason -- see its comment.

import { useState } from 'react';
import { Check, X } from 'lucide-react';

import { useAccountShell } from '@/lib/shell/account-shell-context';
import {
  declinePendingWorkspaceInvite,
  settlePendingInviteJoin,
  useMyPendingWorkspaceInvites,
  type MyPendingWorkspaceInvite,
} from '@/lib/workspace/fleet/members-data';

function roleLabel(role: string): string {
  if (role === 'owner') return 'owner';
  if (role === 'member') return 'member';
  return 'viewer';
}

export function PendingWorkspaceInvitesBanner() {
  const { invites, loading, refresh } = useMyPendingWorkspaceInvites();
  const { state } = useAccountShell();
  // An invited account no longer gets a workspace of its own at signup (see
  // auth.register_user), so this is a real, ordinary state: signed in, one
  // pending invite, nothing else. `/` sends a memberless account to
  // /workspaces/new, and rendering a thin strip above a "Create a workspace"
  // form would push the person at the exact extra workspace this whole fix
  // exists to stop them acquiring. When the invite IS the only thing they
  // can act on, it gets the page.
  const isOnlyThingToDo = state.workspaceMemberships.length === 0;
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errorById, setErrorById] = useState<Record<string, string>>({});

  // Never render while unresolved or empty -- CLAUDE.md's "no dead
  // controls" extends to a banner: an empty one with a border is still a
  // control nobody can act on.
  if (loading || invites.length === 0) return null;

  function landIn(workspaceId: string) {
    const target = String(workspaceId || "").trim();
    window.location.assign(target ? `/w/${encodeURIComponent(target)}` : "/");
  }

  function clearError(id: string) {
    setErrorById((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  async function handleJoin(invite: MyPendingWorkspaceInvite) {
    setBusyId(invite.id);
    clearError(invite.id);
    // settlePendingInviteJoin (members-data.ts) owns the whole
    // joined/failed/unconfirmed decision, including the "the response was
    // lost, ask the server what actually happened" check that used to live
    // here inline. WorkspaceSwitcher answers the same invites and now shares
    // that one implementation rather than growing a second copy of it.
    const settled = await settlePendingInviteJoin(invite.id, invite.workspace_id);
    setBusyId(null);
    if (settled.outcome === 'joined') {
      // A brand-new membership means the account shell's server-resolved
      // workspace list (loadAccountShellSession) is stale -- a full
      // navigation rather than trying to splice a membership into client
      // state the shell otherwise treats as fixed for the session. Rare,
      // cold action; not worth a second state-sync mechanism.
      //
      // Land IN the workspace, not back on whatever page this was answered
      // from: an invitee with no workspace of their own answers this on
      // /workspaces/new, and reloading there would put "Create a workspace"
      // in front of someone who just joined one.
      landIn(settled.workspace_id);
      return;
    }
    setErrorById((prev) => ({ ...prev, [invite.id]: settled.error }));
  }

  async function handleDecline(invite: MyPendingWorkspaceInvite) {
    setBusyId(invite.id);
    clearError(invite.id);
    const result = await declinePendingWorkspaceInvite(invite.id);
    setBusyId(null);
    if (!result.ok) {
      setErrorById((prev) => ({ ...prev, [invite.id]: result.error }));
      return;
    }
    await refresh();
  }

  const rows = invites.map((invite) => (
    <div key={invite.id} className="pending-invite__row">
      <span className="pending-invite__text">
        <span className="pending-invite__title">You&apos;ve been invited to {invite.workspace_name}</span>
        <span className="pending-invite__meta">
          Join as {roleLabel(invite.role)}
          {errorById[invite.id] ? ` — ${errorById[invite.id]}` : ''}
        </span>
      </span>
      <div className="pending-invite__actions">
        <button
          type="button"
          className="pending-invite__btn"
          disabled={busyId === invite.id}
          onClick={() => void handleDecline(invite)}
        >
          <X size={14} strokeWidth={1.75} /> Decline
        </button>
        <button
          type="button"
          className="pending-invite__btn"
          disabled={busyId === invite.id}
          onClick={() => void handleJoin(invite)}
        >
          <Check size={14} strokeWidth={1.75} /> {busyId === invite.id ? 'Joining…' : 'Join'}
        </button>
      </div>
    </div>
  ));

  // The banner deliberately styles itself out of chrome.css (global, loaded by
  // the root layout) rather than fleet-theme.css: fleet-theme is imported by
  // FleetShell alone, and this renders ABOVE the shell -- including on
  // /workspaces/new, which is precisely where a memberless invitee lands. Its
  // old .fleet-card/.fleet-btn classes had no styles at all there, so the one
  // control that accepts an invite rendered as bare unstyled text.
  if (isOnlyThingToDo) {
    return (
      <div className="pending-invite-landing" role="region" aria-label="Pending workspace invites">
        <div className="pending-invite-landing__card">{rows}</div>
      </div>
    );
  }

  return (
    <div className="pending-invite-strip" role="region" aria-label="Pending workspace invites">
      {rows}
    </div>
  );
}
