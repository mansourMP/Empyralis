/**
 * The RENDERING RULE for pending workspace invites on the workspace
 * switcher — Linear's own mechanic ("If you see a number next to Create or
 * join a workspace, it's possible you have a pending workspace invite"),
 * adopted for the one case PendingWorkspaceInvitesBanner.tsx structurally
 * cannot cover.
 *
 * WHY A SECOND SURFACE AT ALL
 * ---------------------------
 * The banner renders ABOVE the workspace shell and gets the whole page when
 * the invite is the only thing an account can act on. That is the MEMBERLESS
 * invitee. A person who ALREADY has a workspace, working inside it, learns
 * about a second invite only by email today — which is the founder's own
 * reported experience ("I didn't even know of it at first"). The switcher is
 * where "which workspaces am I in" already lives, so it is where "and one
 * more is waiting" belongs.
 *
 *   count === 0  →  the switcher is BYTE-IDENTICAL to today. No badge, no
 *                   section, no divider. CLAUDE.md's "no dead controls":
 *                   an indicator showing nothing is still a control nobody
 *                   can act on.
 *   count >= 1   →  a count next to the workspace name, and an
 *                   "Invitations" group inside the popover with the real
 *                   Join/Decline actions.
 *
 * WHILE LOADING, NOTHING SHOWS. A badge that appears a beat after the rail
 * paints is worse than one that appears on the next render — and "we have
 * not asked yet" is not "you have zero invites", which is the same
 * two-different-facts-one-signal law this codebase keeps re-learning.
 *
 * INVITES FOR A WORKSPACE YOU ARE ALREADY IN ARE DROPPED. The server scopes
 * /workspaces/invites/pending to the caller's email and to status='pending',
 * so this should be empty in practice — but the membership list and the
 * invite list are two independent reads that can disagree for one render
 * after a join, and offering "Join" for a workspace already in the switcher
 * above it is a control that can only fail. Filtering is one line and makes
 * that state unreachable rather than merely unlikely.
 *
 * Pure + tested for the same reason agent-count-shape.ts and channel-doors.ts
 * are: the expected shape (the test) and the actual shape (this file) come
 * from two different places, and a renderer switching on a MODE cannot
 * disagree with the rule it is rendering.
 */

/** Structurally minimal — this rule needs an id and a target workspace, and
 *  MyPendingWorkspaceInvite (members-data.ts) satisfies it by shape, so the
 *  module never imports the network layer to state a rendering rule. */
export type PendingInviteLike = {
  id: string;
  workspace_id: string;
};

export type PendingInvitePlan<T extends PendingInviteLike> = {
  /** Renders the count badge AND the popover's Invitations group. Never
   *  true with an empty `invites` — the two can't drift apart. */
  show: boolean;
  /** What the badge prints. 0 whenever `show` is false. */
  count: number;
  /** The rows to render, already filtered. Empty whenever `show` is false. */
  invites: T[];
};

export function planPendingInviteIndicator<T extends PendingInviteLike>(input: {
  /** True while the pending-invites read has not resolved yet. */
  loading: boolean;
  invites: readonly T[] | null | undefined;
  /** Workspace ids the caller is already a member of. */
  memberWorkspaceIds: readonly string[] | null | undefined;
}): PendingInvitePlan<T> {
  const empty: PendingInvitePlan<T> = { show: false, count: 0, invites: [] };
  if (input.loading) return empty;

  const already = new Set(
    (input.memberWorkspaceIds ?? []).map((id) => String(id || "").trim()).filter(Boolean),
  );
  const seen = new Set<string>();
  const invites = (input.invites ?? []).filter((invite) => {
    const id = String(invite?.id || "").trim();
    // A row with no id has no action available (join/decline are keyed by
    // it), and a duplicate would double the count for one real invite.
    if (!id || seen.has(id)) return false;
    if (already.has(String(invite?.workspace_id || "").trim())) return false;
    seen.add(id);
    return true;
  });

  if (invites.length === 0) return empty;
  return { show: true, count: invites.length, invites };
}
