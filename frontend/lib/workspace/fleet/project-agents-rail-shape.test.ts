/**
 * project-as-spine nav: the project Agents section's compact rail is a
 * COMPOSITION of agent-count-shape.ts's planAgentCountShape, not a second
 * rule — this file imports the real function (showsProjectAgentsRail),
 * same discipline agent-count-shape.test.ts already applies to
 * planAgentCountShape itself, so the expected shape and the actual shape
 * come from two different places.
 *
 * Run: npx tsx lib/workspace/fleet/project-agents-rail-shape.test.ts
 */

import { showsProjectAgentsRail, projectAgentsSpaceIsActive } from "./project-agents-rail-shape";
import { planAgentCountShape } from "./agent-count-shape";

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

assert(showsProjectAgentsRail(0) === false, "zero agents in the project -> no rail");
assert(showsProjectAgentsRail(1) === false, "exactly one agent -> no rail (worse than no rail)");
assert(showsProjectAgentsRail(2) === true, "two agents -> the rail renders");
assert(showsProjectAgentsRail(5) === true, "five agents -> still just the rail, no further split");
assert(showsProjectAgentsRail(200) === true, "a large project fleet is still just the rail");
assert(showsProjectAgentsRail(-1) === false, "a defensively-negative count never renders a rail");

// The composition claim itself, proven rather than assumed: for every count
// in a realistic domain, showsProjectAgentsRail must agree exactly with
// "the underlying mode is fleet" — if this ever drifts, a second, parallel
// rule crept in.
for (let n = -1; n <= 20; n++) {
  assert(
    showsProjectAgentsRail(n) === (planAgentCountShape(n) === "fleet"),
    `showsProjectAgentsRail(${n}) agrees with planAgentCountShape(${n}) === "fleet"`,
  );
}

// projectAgentsSpaceIsActive is what PrimaryRail (rail-morph) and
// ProjectDetailPage (tab-strip-hide) both call — the assertion that matters
// here is not "hidden when agents>=2" in isolation, it's that the two
// surfaces can never disagree because they share one function.

// Off the Agents route at all: never active, regardless of count — this is
// what keeps Tasks/Documents untouched at every agent count.
for (let n = -1; n <= 20; n++) {
  assert(
    projectAgentsSpaceIsActive(false, n) === false,
    `projectAgentsSpaceIsActive(false, ${n}) is always false off the Agents route`,
  );
}

// On the Agents route: the composition claim, proven rather than assumed —
// for every count in a realistic domain, being "active" must agree exactly
// with showsProjectAgentsRail, which is itself proven above to agree with
// "the underlying mode is fleet". If this ever drifts, the rail and the tab
// strip have started reading two different rules.
for (let n = -1; n <= 20; n++) {
  assert(
    projectAgentsSpaceIsActive(true, n) === showsProjectAgentsRail(n),
    `projectAgentsSpaceIsActive(true, ${n}) agrees with showsProjectAgentsRail(${n})`,
  );
}

assert(projectAgentsSpaceIsActive(true, 0) === false, "0 agents on the Agents route -> not active (empty state, tab strip stays)");
assert(projectAgentsSpaceIsActive(true, 1) === false, "1 agent on the Agents route -> not active (solo redirect, tab strip stays)");
assert(projectAgentsSpaceIsActive(true, 2) === true, "2 agents on the Agents route -> active (rail morphs, tab strip hides)");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
