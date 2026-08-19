/**
 * The workspace-agents rail — the top-level Agents item's compact rail is a
 * COMPOSITION of agent-count-shape.ts's planAgentCountShape, not a second
 * rule — this file imports the real function (showsWorkspaceAgentsRail),
 * same discipline project-agents-rail-shape.test.ts already applies to its
 * own project-scoped twin, so the expected shape and the actual shape come
 * from two different places.
 *
 * Run: npx tsx lib/workspace/fleet/workspace-agents-rail-shape.test.ts
 */

import { showsWorkspaceAgentsRail, workspaceAgentsSpaceIsActive } from "./workspace-agents-rail-shape";
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

assert(showsWorkspaceAgentsRail(0) === false, "zero real agents workspace-wide -> no rail");
assert(showsWorkspaceAgentsRail(1) === false, "exactly one real agent -> no rail (worse than no rail)");
assert(showsWorkspaceAgentsRail(2) === true, "two real agents -> the rail renders");
assert(showsWorkspaceAgentsRail(5) === true, "five real agents -> still just the rail, no further split");
assert(showsWorkspaceAgentsRail(200) === true, "a large fleet across many projects is still just the rail");
assert(showsWorkspaceAgentsRail(-1) === false, "a defensively-negative count never renders a rail");

// The composition claim itself, proven rather than assumed.
for (let n = -1; n <= 20; n++) {
  assert(
    showsWorkspaceAgentsRail(n) === (planAgentCountShape(n) === "fleet"),
    `showsWorkspaceAgentsRail(${n}) agrees with planAgentCountShape(${n}) === "fleet"`,
  );
}

// workspaceAgentsSpaceIsActive is what PrimaryRail (rail-morph) and the
// workspace Agents page (grid-hide) both call — the assertion that matters
// is not "hidden when agents>=2" in isolation, it's that the two surfaces
// can never disagree because they share one function.

// Off the Agents route at all: never active, regardless of count.
for (let n = -1; n <= 20; n++) {
  assert(
    workspaceAgentsSpaceIsActive(false, n) === false,
    `workspaceAgentsSpaceIsActive(false, ${n}) is always false off the Agents route`,
  );
}

// On the Agents route: the composition claim, proven rather than assumed.
for (let n = -1; n <= 20; n++) {
  assert(
    workspaceAgentsSpaceIsActive(true, n) === showsWorkspaceAgentsRail(n),
    `workspaceAgentsSpaceIsActive(true, ${n}) agrees with showsWorkspaceAgentsRail(${n})`,
  );
}

assert(workspaceAgentsSpaceIsActive(true, 0) === false, "0 agents on the Agents route -> not active (empty state)");
assert(workspaceAgentsSpaceIsActive(true, 1) === false, "1 agent on the Agents route -> not active (solo redirect)");
assert(workspaceAgentsSpaceIsActive(true, 2) === true, "2 agents on the Agents route -> active (rail morphs, grid hides)");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
