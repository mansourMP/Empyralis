/**
 * Rail spaces (primary-rail-space.ts), proven against HAND-WRITTEN expected
 * values — the derivation builds from SETTINGS_SECTIONS, so the expectations
 * here are typed out literally instead of imported from it: the expected set
 * and the actual set come from two different sources, per the house rule a
 * self-confirming test is worth nothing.
 *
 * Run: npx tsx lib/workspace/fleet/primary-rail-space.test.ts
 */

import { railSpaceFromPathname, settingsSpaceLinks, spaceBackHref } from "./primary-rail-space";

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

// ── Which pathnames are the Settings space ────────────────────────────────
const bare = railSpaceFromPathname("/w/ws1/settings");
assert(bare?.kind === "settings", "/w/{id}/settings is the Settings space");
assert(bare?.workspaceId === "ws1", "the space carries its workspace id");
assert(bare?.activeSection === null, "bare /settings names no section yet (the redirect moment)");

const account = railSpaceFromPathname("/w/ws1/settings/account");
assert(account?.kind === "settings", "/settings/account is the Settings space");
assert(account?.activeSection === "account", "the URL's own section is the active one");

assert(
  railSpaceFromPathname("/w/ws1/settings/bogus")?.activeSection === null,
  "an unrecognized section segment is not an active section (the page falls back too)",
);

// Encoded workspace ids round-trip: decoded in the space, re-encoded in hrefs.
const encoded = railSpaceFromPathname("/w/my%20ws/settings/account");
assert(encoded?.workspaceId === "my ws", "the workspace segment is decoded once");

// Not spaces: the flat rail's own destinations, project pages, foreign paths.
assert(railSpaceFromPathname("/w/ws1/inbox") === null, "Inbox is not a space");
assert(railSpaceFromPathname("/w/ws1/projects/p1") === null, "a project page is not a space — the rail stays flat there (2026-08-15)");
assert(railSpaceFromPathname("/w/ws1") === null, "the workspace root is not a space");
assert(railSpaceFromPathname("/login") === null, "paths outside /w are never a space");
assert(railSpaceFromPathname("/w/ws1/settingsx") === null, "a segment merely starting with 'settings' is not the space");

// ── The Settings pick-list ────────────────────────────────────────────────
const links = settingsSpaceLinks(account!);
assert(
  links.map((l) => l.key).join(",") === "account,workspace,connections,shortcuts",
  `exactly the four Settings destinations, in order — got ${links.map((l) => l.key).join(",")}`,
);
assert(
  links.map((l) => l.label).join("|") === "Account|Workspace|Connections|Keyboard shortcuts",
  "labels are the human words, no mechanism vocabulary",
);
assert(
  links.map((l) => l.href).join(" ") ===
    "/w/ws1/settings/account /w/ws1/settings/workspace /w/ws1/settings/connections /w/ws1/settings/shortcuts",
  "every row is a concrete href — real links, cmd-clickable",
);
assert(
  links.filter((l) => l.active).map((l) => l.key).join(",") === "account",
  "exactly one row is active: the URL's own section",
);
assert(links.every((l) => Boolean(l.icon)), "every Settings row carries an icon");

// Bare /settings: the DEFAULT section reads as active (that is where the
// redirect lands), never a list with no current row.
const bareLinks = settingsSpaceLinks(bare!);
assert(
  bareLinks.filter((l) => l.active).map((l) => l.key).join(",") === "workspace",
  "bare /settings marks the default section (workspace) active",
);

// Encoded workspace ids are re-encoded in hrefs.
assert(
  settingsSpaceLinks(encoded!)[0]?.href === "/w/my%20ws/settings/account",
  "hrefs re-encode the workspace id",
);

// ── The Back row's target ─────────────────────────────────────────────────
assert(spaceBackHref(account!, null) === "/w/ws1", "no known origin → the workspace root");
assert(
  spaceBackHref(account!, "/w/ws1/projects/p1") === "/w/ws1/projects/p1",
  "Back returns to where the person came from",
);
assert(
  spaceBackHref(account!, "/w/ws1") === "/w/ws1",
  "the workspace root itself is a valid origin",
);
assert(
  spaceBackHref(account!, "/w/OTHER/inbox") === "/w/ws1",
  "an origin in another workspace is never honoured",
);
assert(
  spaceBackHref(account!, "/w/ws10/inbox") === "/w/ws1",
  "a workspace id that merely starts with ours is another workspace (prefix boundary)",
);
assert(
  spaceBackHref(account!, "/w/ws1/settings/workspace") === "/w/ws1",
  "an origin inside the space itself falls back to the root — Back always leaves the space",
);
assert(
  spaceBackHref(encoded!, null) === "/w/my%20ws",
  "the fallback root re-encodes the workspace id",
);

// ── Wired, not just built ─────────────────────────────────────────────────
// The house failure mode is complete, tested code with zero callers. The
// component must import THIS module and derive the space from the live
// pathname — a hand-inlined copy of the regex would pass every assertion
// above while drifting freely.
import { readFileSync } from "node:fs";
const railSource = readFileSync(new URL("./PrimaryRail.tsx", import.meta.url), "utf8");
assert(
  railSource.includes('from "./primary-rail-space"'),
  "PrimaryRail imports the real space module",
);
assert(
  /railSpaceFromPathname\s*\(/.test(railSource),
  "PrimaryRail derives the space from the pathname via railSpaceFromPathname",
);
assert(
  /settingsSpaceLinks\s*\(/.test(railSource),
  "PrimaryRail renders the Settings pick-list from settingsSpaceLinks",
);
assert(
  /spaceBackHref\s*\(/.test(railSource),
  "PrimaryRail's Back row resolves through spaceBackHref",
);

// And the in-content sidebar is actually GONE: SettingsShell must not render
// a second pick-list beside the content.
const settingsShellSource = readFileSync(new URL("./SettingsShell.tsx", import.meta.url), "utf8");
assert(
  !/<GroupedRail\b/.test(settingsShellSource) && !/GroupedRail["']/.test(settingsShellSource),
  "SettingsShell no longer renders an in-content section sidebar — the rail is where you pick",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
