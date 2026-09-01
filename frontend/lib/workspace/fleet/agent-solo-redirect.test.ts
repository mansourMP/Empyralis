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

import { shouldRedirectToSoloAgent, suppressSoloRedirectFromSearch } from "./agent-solo-redirect";

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

// --- Bug A, 2026-09-01: the agent detail page's back control ---
//
// FleetAgentDetail's back control (`backHref`, [agentId]/[tab]/page.tsx)
// points at `${base}/agents?list=1`. Before this fix, `?list=1` meant
// nothing — the only query-derived suppression `agents/page.tsx` recognized
// was `?new=1` — so landing back on `/agents` with exactly one agent
// re-ran `shouldRedirectToSoloAgent` with `suppressed` false,
// `router.replace` fired straight back into that same agent, and the
// agents list (and "New agent" with it) was permanently unreachable for
// any workspace holding exactly one agent — every new customer. Run this
// file against the pre-fix `suppressSoloRedirectFromSearch` (only checking
// `new`) and this assertion fails with real output, exactly the bug the
// founder reported.
assert(
  suppressSoloRedirectFromSearch("?list=1") === true,
  "the back-destination's ?list=1 suppresses the solo redirect",
);
// The mapping feeds straight into the same `suppressed` input as ?new=1 —
// this is what stops the redirect firing for the exact scenario above:
// cardOpen never opens (AgentCreateCard is not part of this flow at all).
assert(
  shouldRedirectToSoloAgent({
    loading: false,
    hasSoloTarget: true,
    cardOpen: false,
    suppressed: suppressSoloRedirectFromSearch("?list=1"),
  }) === false,
  "the back-destination (?list=1, cardOpen never opened) is not redirected away from the agents list",
);
// ?new=1 keeps working unchanged — the second suppression source doesn't
// crowd out the first.
assert(
  suppressSoloRedirectFromSearch("?new=1") === true,
  "?new=1 still suppresses (unchanged behaviour, now routed through the shared helper)",
);
// An ordinary /agents visit, and any unrelated query string, must NOT be
// silently treated as a suppression — only these two explicit params do.
assert(
  suppressSoloRedirectFromSearch("") === false,
  "no query string means no suppression",
);
assert(
  suppressSoloRedirectFromSearch("?foo=1") === false,
  "an unrelated query param is not mistaken for a suppression flag",
);
assert(
  suppressSoloRedirectFromSearch("?list=0") === false,
  "?list=0 (or anything other than the literal \"1\") does not suppress",
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
