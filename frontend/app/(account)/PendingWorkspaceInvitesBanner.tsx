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

import {
  declinePendingWorkspaceInvite,
  fetchMyPendingWorkspaceInviteIds,
  joinPendingWorkspaceInvite,
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
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errorById, setErrorById] = useState<Record<string, string>>({});

  // Never render while unresolved or empty -- CLAUDE.md's "no dead
  // controls" extends to a banner: an empty one with a border is still a
  // control nobody can act on.
  if (loading || invites.length === 0) return null;

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
    const result = await joinPendingWorkspaceInvite(invite.id);
    if (result.ok) {
      setBusyId(null);
      // A brand-new membership means the account shell's server-resolved
      // workspace list (loadAccountShellSession) is stale -- reload rather
      // than trying to splice a membership into client state the shell
      // otherwise treats as fixed for the session. Rare, cold action; not
      // worth a second state-sync mechanism.
      window.location.reload();
      return;
    }
    if (result.ambiguous) {
      // The join request itself never produced a response -- the server may
      // already have processed it. Before ever telling the person it
      // failed, check the actual source of truth: if this invite no longer
      // shows up as pending, the join went through and we must not report
      // failure on a success.
      const stillPending = await fetchMyPendingWorkspaceInviteIds()
        .then((ids) => ids.includes(invite.id))
        .catch(() => true); // can't verify -- fall through to the honest error below
      if (!stillPending) {
        setBusyId(null);
        window.location.reload();
        return;
      }
    }
    setBusyId(null);
    setErrorById((prev) => ({ ...prev, [invite.id]: result.error }));
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

  return (
    <div
      className="fleet-card"
      role="region"
      aria-label="Pending workspace invites"
      style={{ margin: 'var(--space-3) var(--space-4) 0', padding: 'var(--space-2) var(--space-4)' }}
    >
      <div className="fleet-list">
        {invites.map((invite) => (
          <div key={invite.id} className="fleet-list-row" style={{ cursor: 'default' }}>
            <span className="fleet-list-row-main">
              <span className="fleet-list-row-title">You&apos;ve been invited to {invite.workspace_name}</span>
              <span className="fleet-list-row-desc">
                As {roleLabel(invite.role)}
                {errorById[invite.id] ? ` — ${errorById[invite.id]}` : ''}
              </span>
            </span>
            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <button
                type="button"
                className="fleet-btn"
                disabled={busyId === invite.id}
                onClick={() => void handleDecline(invite)}
              >
                <X size={14} strokeWidth={1.75} /> Decline
              </button>
              <button
                type="button"
                className="fleet-btn"
                disabled={busyId === invite.id}
                onClick={() => void handleJoin(invite)}
              >
                <Check size={14} strokeWidth={1.75} /> {busyId === invite.id ? 'Joining…' : 'Join'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
