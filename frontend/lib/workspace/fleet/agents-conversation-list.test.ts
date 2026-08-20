/**
 * Pure logic behind the workspace-level Agents conversation list
 * (agents-conversation-list.ts) — imports the REAL functions rather than
 * re-deriving the sort/filter/preview rules here, same discipline every
 * other pure-rule module in this directory follows.
 *
 * Run: npx tsx lib/workspace/fleet/agents-conversation-list.test.ts
 */

import {
  conversationPreview,
  conversationTimestamp,
  matchesConversationQuery,
  planConversationList,
  sortAgentsByRecency,
  type ConversationAgent,
} from "./agents-conversation-list";

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

// ── conversationPreview ─────────────────────────────────────────────────
assert(
  conversationPreview({ agent_id: "a1", label: "Scout", activity_preview: "Replied to a customer question" }) ===
    "Replied to a customer question",
  "a real preview passes through unchanged",
);
assert(
  conversationPreview({ agent_id: "a1", label: "Scout" }) === "No activity yet",
  "no activity_preview at all reads as the honest empty state",
);
assert(
  conversationPreview({ agent_id: "a1", label: "Scout", activity_preview: "   " }) === "No activity yet",
  "a blank/whitespace-only preview reads as the honest empty state",
);
assert(
  conversationPreview({ agent_id: "a1", label: "Scout", activity_preview: "Fleet: created → ainstall_ab12cd" }) ===
    "No activity yet",
  "a raw pre-2026-07-09 internal title is never shown, same guard AgentsList.tsx applies",
);

// ── conversationTimestamp ───────────────────────────────────────────────
assert(conversationTimestamp(null) === "", "no timestamp is a blank string, never a fabricated 'never'");
assert(conversationTimestamp(undefined) === "", "undefined behaves the same as null");
const oneMinuteAgo = new Date(Date.now() - 60_000).toISOString();
assert(!conversationTimestamp(oneMinuteAgo).includes("ago"), "the trailing ' ago' is trimmed for the compact list row");

// ── sortAgentsByRecency ─────────────────────────────────────────────────
const now = Date.now();
const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
const agents: ConversationAgent[] = [
  { agent_id: "old", label: "Old Timer", last_activity: iso(10_000) },
  { agent_id: "new", label: "Newcomer", last_activity: iso(1_000) },
  { agent_id: "mid", label: "Middle", last_activity: iso(5_000) },
  { agent_id: "zeta", label: "Zeta", last_activity: null },
  { agent_id: "alpha", label: "Alpha", last_activity: null },
];
const sorted = sortAgentsByRecency(agents);
assert(
  sorted.map((a) => a.agent_id).join(",") === "new,mid,old,alpha,zeta",
  `most-recently-active first, then never-active agents alphabetically by label — got ${sorted.map((a) => a.agent_id).join(",")}`,
);
assert(sortAgentsByRecency([]).length === 0, "an empty list stays empty");
assert(sortAgentsByRecency(agents) !== agents, "the input array is never mutated in place (a fresh array is returned)");
assert(agents[0]!.agent_id === "old", "sorting does not reorder the caller's own original array");

// ── matchesConversationQuery ────────────────────────────────────────────
const scout: ConversationAgent = { agent_id: "a1", label: "Scout", activity_preview: "Booked a demo call" };
assert(matchesConversationQuery(scout, "") === true, "a blank query matches everything");
assert(matchesConversationQuery(scout, "   ") === true, "a whitespace-only query matches everything");
assert(matchesConversationQuery(scout, "scout") === true, "matches the name, case-insensitively");
assert(matchesConversationQuery(scout, "SCOUT") === true, "matches regardless of the query's own case");
assert(matchesConversationQuery(scout, "demo") === true, "matches the activity preview too, not just the name");
assert(matchesConversationQuery(scout, "billing") === false, "no match on unrelated text");

// ── planConversationList ────────────────────────────────────────────────
const roster: ConversationAgent[] = [
  { agent_id: "support", label: "Support Bot", last_activity: iso(2_000), activity_preview: "Answered a ticket" },
  { agent_id: "sales", label: "Sales Bot", last_activity: iso(9_000), activity_preview: "Sent a quote" },
  { agent_id: "quiet", label: "Quiet Bot", last_activity: null },
];
assert(
  planConversationList(roster, "").map((a) => a.agent_id).join(",") === "support,sales,quiet",
  "with no query, the full roster sorts by recency",
);
assert(
  planConversationList(roster, "bot").map((a) => a.agent_id).join(",") === "support,sales,quiet",
  "'bot' matches every row's own name here, order unchanged",
);
assert(
  planConversationList(roster, "quote").map((a) => a.agent_id).join(",") === "sales",
  "a query narrows to the matching rows before sorting — not sorted-then-truncated",
);
assert(
  planConversationList(roster, "nothing matches this").length === 0,
  "a query with no matches returns an empty list, not a fallback to the full roster",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
