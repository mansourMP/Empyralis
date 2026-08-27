/**
 * MAN-374 — imports the REAL rule (shouldRedirectToSoloAgent) rather than
 * re-deriving the condition here, same discipline agent-count-shape.test.ts
 * already applies to planAgentCountShape. Proven red-before-green: run
 * these assertions against the OLD inline conditions (no `cardOpen` term at
 * all) and the two "wizard open" cases below fail — that is the exact
 * defect a real browser reproduced twice on a disposable stack (create a
 * brand-new workspace's first agent via "Create your first agent",
 * AgentCreateCard's own commit step force-refetches the shared agents
 * cache, and the solo-redirect effect fired straight through the open
 * wizard into the new agent's Chat, skipping the required Channels step).
 *
 * Run: npx tsx lib/workspace/fleet/agent-solo-redirect.test.ts
 */

import { shouldRedirectToSoloAgent } from "./agent-solo-redirect";

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

// The ordinary case this redirect exists for: not loading, exactly one real
// agent, nothing suppressing it, and no create wizard in the way.
assert(
  shouldRedirectToSoloAgent({ loading: false, hasSoloTarget: true, cardOpen: false }) === true,
  "redirects when nothing is holding it back",
);

// --- MAN-374's own defect, pinned so it cannot come back silently ---

// The exact moment this bug fired live: the wizard is open, creating the
// workspace's first agent, and the agent count has just flipped to 1.
assert(
  shouldRedirectToSoloAgent({ loading: false, hasSoloTarget: true, cardOpen: true }) === false,
  "never redirects while AgentCreateCard is open — this is the whole fix",
);
// Same case, but via the command-palette's ?new=1 entry (suppressed=true
// too) — belt AND braces both hold, neither alone is trusted to carry it.
assert(
  shouldRedirectToSoloAgent({ loading: false, hasSoloTarget: true, cardOpen: true, suppressed: true }) === false,
  "still refuses when both guards are true at once",
);

// --- The pre-existing guards, unweakened by adding cardOpen ---

assert(
  shouldRedirectToSoloAgent({ loading: true, hasSoloTarget: true, cardOpen: false }) === false,
  "never redirects while the agent list is still loading (a transient zero must not fire this)",
);
assert(
  shouldRedirectToSoloAgent({ loading: false, hasSoloTarget: false, cardOpen: false }) === false,
  "never redirects with no solo target to redirect to",
);
assert(
  shouldRedirectToSoloAgent({ loading: false, hasSoloTarget: true, cardOpen: false, suppressed: true }) === false,
  "the ?new=1 suppression still works on its own, unrelated to cardOpen",
);

// `suppressed` is optional — the project-scoped page has no ?new=1 concept
// at all and never passes it; omitting it must not be silently treated as
// "always suppressed" or "always unsuppressed" in a way that masks a real
// bug in the other three inputs.
assert(
  shouldRedirectToSoloAgent({ loading: false, hasSoloTarget: true, cardOpen: false }) === true,
  "omitting `suppressed` behaves exactly like passing suppressed: false",
);

// The wizard being open wins over every other input — it is not merely one
// vote among several.
for (const loading of [false, true]) {
  for (const suppressed of [false, true]) {
    assert(
      shouldRedirectToSoloAgent({ loading, hasSoloTarget: true, cardOpen: true, suppressed }) === false,
      `cardOpen:true refuses regardless of loading=${loading}, suppressed=${suppressed}`,
    );
  }
}

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
