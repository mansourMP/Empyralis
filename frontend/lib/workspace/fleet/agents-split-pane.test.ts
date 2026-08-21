/**
 * agents-split-pane.ts — one pane at a time at phone width, and the CSS that
 * actually does it.
 *
 * The behavioural half below is small on purpose; the rule is small. The
 * half that matters is the SOURCE SCAN at the bottom: the bug was never in
 * a function, it was a stylesheet with no narrow-width rule at all, and a
 * layout that rendered a hardcoded class string. Neither is reachable by
 * asserting on a return value, and both type-check perfectly while the
 * surface is unusable on a phone.
 *
 * Run: npx tsx lib/workspace/fleet/agents-split-pane.test.ts
 */

import { readFileSync } from "node:fs";

import { agentsMobilePane, agentsSplitClassName, type AgentsSplitPane } from "./agents-split-pane";

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

// ── EXACTLY ONE PANE, in every reachable state ───────────────────────────
// Never both (the bug: 320px of list beside 55px of clipped detail) and
// never neither (a blank screen, which would pass a "not both" test).
const PANES: AgentsSplitPane[] = ["list", "detail"];
const ROUTES: (string | null)[] = [null, "", "ainstall_abc123"];

for (const activeAgentId of ROUTES) {
  const visible = PANES.filter((p) => agentsMobilePane(activeAgentId) === p);
  assert(visible.length === 1, `exactly one visible pane for agentId=${JSON.stringify(activeAgentId)}`);
}

assert(agentsMobilePane(null) === "list", "the bare /agents index shows the picker");
assert(agentsMobilePane("") === "list", "an empty agent id is not a selection");
assert(agentsMobilePane("ainstall_abc") === "detail", "a selected agent shows its own surface");

// ── The className the layout actually renders ────────────────────────────
// fleet-content--split is UNCONDITIONAL: this rule may only ever add a
// modifier that a max-width:768px block reads. A version that dropped the
// base class would silently restyle every desktop width.
for (const activeAgentId of ROUTES) {
  const cls = agentsSplitClassName(activeAgentId);
  assert(cls.startsWith("fleet-content fleet-content--split"), `desktop split shell intact for ${JSON.stringify(activeAgentId)}`);
}
assert(
  agentsSplitClassName(null) === "fleet-content fleet-content--split",
  "no modifier at the index — the list is the default pane",
);
assert(
  agentsSplitClassName("ainstall_abc") === "fleet-content fleet-content--split fleet-agents-split--detail-open",
  "the modifier appears only once an agent is selected",
);

// ── Wired, not just built ────────────────────────────────────────────────
const layoutSource = readFileSync(
  new URL("../../../app/(account)/w/[workspaceId]/agents/layout.tsx", import.meta.url),
  "utf8",
);
assert(layoutSource.length > 500, "CANARY: the agents layout source was actually read");
assert(
  /agents-split-pane/.test(layoutSource),
  "agents/layout.tsx decides its container class through the shared rule",
);
// The reintroduction guard. A hardcoded class string here is exactly what
// was live, renders perfectly at every desktop width, and is invisible to
// every behavioural test in this repo.
assert(
  !/className="fleet-content fleet-content--split"/.test(layoutSource),
  "agents/layout.tsx does not hardcode the split class back in",
);

// ── The CSS half — the actual fix, and the part no unit test can render ──
const css = readFileSync(new URL("./fleet-theme.css", import.meta.url), "utf8");
assert(css.length > 100_000, "CANARY: fleet-theme.css was actually read");

// Isolate the narrow-width blocks so a desktop rule mentioning these
// selectors can never satisfy the assertions below.
const narrowBlocks = css
  .split("@media (max-width: 768px)")
  .slice(1)
  .map((chunk) => chunk.slice(0, chunk.indexOf("\n}\n") + 1))
  .join("\n");
assert(narrowBlocks.length > 1000, "CANARY: the <=768px blocks were actually isolated");

assert(
  /\.fleet-agents-detail-pane\s*\{[^}]*display:\s*none/.test(narrowBlocks),
  "the detail pane is hidden by default at phone width",
);
assert(
  /\.fleet-agents-split--detail-open\s+\.fleet-agents-conversation-list\s*\{[^}]*display:\s*none/.test(narrowBlocks),
  "…and the list is hidden once an agent is selected — one pane, not two",
);
assert(
  /\.fleet-agents-split--detail-open\s+\.fleet-agents-detail-pane\s*\{[^}]*display:\s*flex/.test(narrowBlocks),
  "the selected agent's pane comes back as FLEX (its height:100% chain binds against a column flex parent)",
);
// The 320px floor is the whole reason the detail pane was 55px wide. A
// display toggle alone does not fix it: the list itself still overflows a
// <320px viewport.
assert(
  /\.fleet-agents-conversation-list,\s*\n\s*\.fleet-agents-detail-pane\s*\{[^}]*min-width:\s*0/.test(narrowBlocks),
  "both panes drop the desktop 320px floor at phone width",
);

// The list must NEVER be the pane that disappears unconditionally — that
// would make picking an agent impossible on a phone, which is worse than
// the bug being fixed here.
assert(
  // Anchored at the start of a selector line, so the legitimate
  // `.fleet-agents-split--detail-open .fleet-agents-conversation-list` rule
  // above cannot satisfy it by accident.
  !/(^|\n)\s*\.fleet-agents-conversation-list\s*\{[^}]*display:\s*none/.test(narrowBlocks),
  "the list is never hidden except when an agent is actually selected",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
