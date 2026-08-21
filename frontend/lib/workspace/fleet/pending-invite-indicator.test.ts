/**
 * Imports the REAL rule (planPendingInviteIndicator) rather than restating
 * its thresholds — same discipline as agent-count-shape.test.ts, for the
 * reason CLAUDE.md gives: "a check that derives its own expectations from
 * the thing it checks is blind, and reports 'passed'."
 *
 * Run: npx tsx lib/workspace/fleet/pending-invite-indicator.test.ts
 */

import { planPendingInviteIndicator } from "./pending-invite-indicator";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

const inviteA = { id: "inv_a", workspace_id: "ws_acme" };
const inviteB = { id: "inv_b", workspace_id: "ws_beta" };

// The whole point of the feature: zero pending invites leaves the switcher
// exactly as it is today. No badge, no section, no count.
{
  const plan = planPendingInviteIndicator({ loading: false, invites: [], memberWorkspaceIds: ["ws_mine"] });
  assert(plan.show === false, "zero invites -> nothing renders");
  assert(plan.count === 0, "zero invites -> count is 0");
  assert(plan.invites.length === 0, "zero invites -> no rows");
}

// "We have not asked yet" is not "you have zero invites".
{
  const plan = planPendingInviteIndicator({ loading: true, invites: [inviteA], memberWorkspaceIds: [] });
  assert(plan.show === false, "loading suppresses the badge even when invites are already in hand");
  assert(plan.count === 0, "loading -> count 0, never a half-resolved number");
}

// One real invite, held by someone who already has their own workspace —
// the exact case the banner cannot cover.
{
  const plan = planPendingInviteIndicator({ loading: false, invites: [inviteA], memberWorkspaceIds: ["ws_mine"] });
  assert(plan.show === true, "one pending invite -> the indicator shows");
  assert(plan.count === 1, "one pending invite -> count 1");
  assert(plan.invites.length === 1 && plan.invites[0]!.id === "inv_a", "the row carried through is the invite itself");
}

// Two invites count as two.
{
  const plan = planPendingInviteIndicator({ loading: false, invites: [inviteA, inviteB], memberWorkspaceIds: ["ws_mine"] });
  assert(plan.count === 2, "two pending invites -> count 2");
  assert(plan.invites.length === 2, "two pending invites -> two rows");
}

// An invite for a workspace already in the switcher is not an invite —
// offering Join there is a control that can only fail.
{
  const plan = planPendingInviteIndicator({ loading: false, invites: [inviteA], memberWorkspaceIds: ["ws_acme"] });
  assert(plan.show === false, "an invite to a workspace the caller is already in is dropped");
  assert(plan.count === 0, "...and does not count toward the badge");
}
{
  const plan = planPendingInviteIndicator({
    loading: false,
    invites: [inviteA, inviteB],
    memberWorkspaceIds: ["ws_acme"],
  });
  assert(plan.count === 1, "only the already-joined one is dropped; the other still counts");
  assert(plan.invites[0]!.id === "inv_b", "the surviving row is the one still pending");
}

// Defensive shapes: a nullish list, a nullish membership list, an id-less
// row, and a duplicate row must never produce a badge that overstates.
{
  const plan = planPendingInviteIndicator({ loading: false, invites: null, memberWorkspaceIds: null });
  assert(plan.show === false && plan.count === 0, "a nullish invite list reads as nothing, never as a crash");
}
{
  const plan = planPendingInviteIndicator({ loading: false, invites: [inviteA], memberWorkspaceIds: null });
  assert(plan.count === 1, "a nullish membership list filters nothing out");
}
{
  const plan = planPendingInviteIndicator({
    loading: false,
    invites: [{ id: "", workspace_id: "ws_acme" }],
    memberWorkspaceIds: [],
  });
  assert(plan.show === false, "a row with no id has no action available, so it is not shown");
}
{
  const plan = planPendingInviteIndicator({ loading: false, invites: [inviteA, inviteA], memberWorkspaceIds: [] });
  assert(plan.count === 1, "a duplicated invite counts once, never twice for one real invite");
}

// show and invites can never disagree — the badge and the section are one
// decision, so a rendered badge always has rows behind it.
for (const memberships of [[], ["ws_mine"], ["ws_acme"], ["ws_acme", "ws_beta"]]) {
  const plan = planPendingInviteIndicator({
    loading: false,
    invites: [inviteA, inviteB],
    memberWorkspaceIds: memberships,
  });
  assert(plan.show === plan.invites.length > 0, `show tracks row count for memberships [${memberships.join(",")}]`);
  assert(plan.count === plan.invites.length, `count tracks row count for memberships [${memberships.join(",")}]`);
}

console.log(`pending-invite-indicator: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
