/**
 * The rail's two-mode boundary: proves activeProjectIdFromPathname (the
 * REAL function PrimaryRail.tsx renders against) draws the line where the
 * founder drew it — any route under /projects/{id} is project mode, the
 * bare /projects list and everything outside it is normal mode.
 *
 * Run: npx tsx lib/workspace/fleet/primary-rail-project-mode.test.ts
 */

import { activeProjectIdFromPathname } from "./primary-rail-project-mode";

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

assert(activeProjectIdFromPathname("/w/ws-1") === null, "workspace root is normal mode");
assert(activeProjectIdFromPathname("/w/ws-1/inbox") === null, "Inbox is normal mode");
assert(activeProjectIdFromPathname("/w/ws-1/projects") === null, "bare projects list is normal mode — no id segment");
assert(
  activeProjectIdFromPathname("/w/ws-1/projects/proj_1") === "proj_1",
  "a project's own bare page is project mode",
);
assert(
  activeProjectIdFromPathname("/w/ws-1/projects/proj_1/tasks") === "proj_1",
  "a project's Tasks section is project mode",
);
assert(
  activeProjectIdFromPathname("/w/ws-1/projects/proj_1/agents/ainstall_9/chat") === "proj_1",
  "a specific agent's chat, nested under its project, is project mode",
);
assert(
  activeProjectIdFromPathname("/w/ws-1/projects/proj_1/agents/ainstall_9/general") === "proj_1",
  "a specific agent's Configure sub-tab is still project mode — only the id segment matters",
);

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
