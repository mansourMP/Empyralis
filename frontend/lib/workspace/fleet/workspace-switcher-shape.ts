/**
 * ONE WORKSPACE PER ACCOUNT (2026-08-23) — so the workspace switcher is
 * usually not a switcher, and it must stop pretending to be one.
 *
 * WHY THE CREATE PATH WENT
 * ------------------------
 * Measured on production: 152 workspaces own projects, and only 6 have any
 * tasks and 16 any agents. The overwhelming majority are empty shells nobody
 * meant to make. The founder had one he had forgotten creating, consented an
 * MCP Connector to it by mistake, and spent an hour reading a working
 * integration as broken.
 *
 * The two jobs a second workspace would do are both already done better:
 * a teammate is invited into a PROJECT and sees only that project
 * (project_memberships, MAN-115), and another business is isolated by the
 * per-agent CONTEXT GRANT ("An agent belongs to the WORKSPACE. Context is
 * GRANTED, never inherited", 2026-08-20). So the "New workspace" row is gone
 * from this popover. Only the SELF-SERVE UI PATH went: the backend still
 * creates a workspace (signup needs it), /workspaces/new is still the landing
 * for an account that has NO workspace at all (app/page.tsx, onboarding,
 * resolve-settings-route), and every workspace that already exists still
 * works, still switches, and was not touched.
 *
 * THE RULE
 * --------
 *   one workspace, no invites   →  "label"   the name, and nothing to press
 *   anything else               →  "picker"  today's popover, unchanged
 *
 * A rail of one is worse than no rail (agent-count-shape.ts) and a table of
 * one is worse than no table; a PICKER of one is the same claim. With the
 * create row removed, a single-workspace popover would open onto exactly one
 * row that is already checked — a control whose only outcome is closing
 * itself, which "no dead controls" forbids outright.
 *
 * PENDING INVITES KEEP THE PICKER OPEN, and that is not a special case being
 * bolted on — it is the rule reading correctly. A person with one workspace
 * and an invite waiting has something REAL to do in that popover (Join /
 * Decline, the only surface offering it to someone who already has a
 * workspace — see pending-invite-indicator.ts). Collapsing to a label there
 * would take away the one action, which is the mirror image of the dead
 * control being removed.
 *
 * Existing accounts with several workspaces are unaffected: `count > 1` is
 * "picker", exactly as before.
 *
 * Pure + tested for the same reason agent-count-shape.ts, channel-doors.ts
 * and pending-invite-indicator.ts are: the expected shape (the test) and the
 * actual shape (this file) come from two different places, and a renderer
 * switching on a MODE cannot disagree with the rule it renders.
 */

export type WorkspaceSwitcherMode = "label" | "picker";

export type WorkspaceSwitcherShape = {
  mode: WorkspaceSwitcherMode;
  /** The trigger opens a menu. False in "label" mode — there is no menu. */
  interactive: boolean;
  /** The ⌄ affordance. Never true without `interactive`. */
  showsChevron: boolean;
};

export function planWorkspaceSwitcher(input: {
  /** How many workspaces this account is a MEMBER of, including the current one. */
  workspaceCount: number;
  /** Pending invitations the popover would offer Join/Decline for. */
  pendingInviteCount: number;
}): WorkspaceSwitcherShape {
  // Defensive rather than decorative: workspaceCount comes from an array
  // length off the account shell, and a shell that failed to load presents
  // as 0. Zero workspaces cannot reach this component at all (the shell does
  // not mount), but "0 means picker" would be the wrong answer if it ever
  // did — there would be nothing to pick.
  const workspaces = Number.isFinite(input.workspaceCount) ? Math.max(0, Math.trunc(input.workspaceCount)) : 0;
  const invites = Number.isFinite(input.pendingInviteCount) ? Math.max(0, Math.trunc(input.pendingInviteCount)) : 0;

  const picker = workspaces > 1 || invites > 0;
  return {
    mode: picker ? "picker" : "label",
    interactive: picker,
    showsChevron: picker,
  };
}
