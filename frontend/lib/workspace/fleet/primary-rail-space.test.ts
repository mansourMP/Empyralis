/**
 * Rail spaces (primary-rail-space.ts), proven against HAND-WRITTEN expected
 * values — the derivation builds from SETTINGS_SECTIONS, so the expectations
 * here are typed out literally instead of imported from it: the expected set
 * and the actual set come from two different sources, per the house rule a
 * self-confirming test is worth nothing.
 *
 * TWO SPACES HAVE BEEN DELETED FROM THIS SURFACE and their assertions are
 * INVERTED here rather than removed: `workspace-agents` (2026-08-20) and
 * `project-agents` (2026-08-21). primary-rail-space.ts's own header carries
 * the founder's words for both. project-agents-rail-shape.ts — the count
 * gate that decided whether the project-agents space morphed the rail — was
 * deleted with it; its composition assertions had no subject left to make a
 * claim about, and the rule they composed (planAgentCountShape) keeps its
 * own test.
 *
 * THE AGENTS VIEW-OPTIONS ASSERTION HAS FLIPPED THREE TIMES; read the whole
 * history before flipping it a fourth. It banned importing AgentsBoard/
 * AgentsGroupedList/AgentViewOptions (they were only reachable through a
 * deleted rail space, so an import could only be an accidental resurrection);
 * required them (78e3eabd wired them in as a real opt-in view); banned them
 * again (MAN-370 — that wiring shipped `agentActivityPreviewText`, the
 * lifecycle-verb line CLAUDE.md records the founder rejecting, one click from
 * the grid built to replace it); and now REQUIRES them again, because the
 * founder asked for the views back by name and the lifecycle verb is deleted
 * at the source rather than hidden behind an unwired import.
 *
 * What was constant through all four states, and is the only thing this file
 * is really about, is that the AGENTS PICKER IS NEVER ON THE RAIL. None of
 * these layouts touches the rail; they are three renderings inside the content
 * area, and exactly one is on screen at a time.
 *
 * Run: npx tsx lib/workspace/fleet/primary-rail-space.test.ts
 */

import { railSpaceFromPathname, settingsSpaceLinks, spaceBackHref } from "./primary-rail-space";

// Narrowing helper for the union — throws (fails the run) rather than
// silently skipping if a pathname stops parsing as the Settings space.
function asSettings(space: ReturnType<typeof railSpaceFromPathname>) {
  if (!space || space.kind !== "settings") throw new Error("expected a settings space");
  return space;
}

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
assert(bare?.kind === "settings" && bare.activeSection === null, "bare /settings names no section yet (the redirect moment)");

const account = railSpaceFromPathname("/w/ws1/settings/account");
assert(account?.kind === "settings", "/settings/account is the Settings space");
assert(account?.kind === "settings" && account.activeSection === "account", "the URL's own section is the active one");

const bogus = railSpaceFromPathname("/w/ws1/settings/bogus");
assert(
  bogus?.kind === "settings" && bogus.activeSection === null,
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

// ── THE PROJECT-AGENTS SPACE IS DELETED (2026-08-21) ─────────────────────
// EVERY ASSERTION IN THIS BLOCK IS AN INVERSION OF ONE THAT USED TO PIN THE
// OPPOSITE. It is kept, rather than removed, so nobody re-adds the space
// believing it was simply forgotten.
//
// The space matched /w/{ws}/projects/{pid}/agents and replaced the whole
// rail with "‹ Back / {project} / one row per agent of THAT project" — i.e.
// it asserted, in navigation, that an agent belongs to a project and is
// browsed through it. On 2026-08-20 the founder settled the opposite and
// called it core: an agent belongs to the WORKSPACE, and its reach into
// projects is a per-agent GRANT, never a location ("an agent belongs to the
// workspace not to the project it's correct and I want you to remember
// that"). On this exact navigation shape: "it's fundamentally wrong... what
// we are building is not kind of like Telegram surface."
//
// The ROUTE stays live and unlinked — deleting it strands bookmarks, and
// making it a redirect target risks the "a redirect runs ahead of the
// router" trap CLAUDE.md records. It simply is not a rail space any more.
assert(
  railSpaceFromPathname("/w/ws1/projects/p1/agents") === null,
  "a project's /agents section is NOT a rail space any more — an agent belongs to the workspace, not a project (2026-08-21)",
);
assert(
  railSpaceFromPathname("/w/ws1/projects/p1/agents/") === null,
  "a trailing slash on it is still not a rail space",
);
assert(
  railSpaceFromPathname("/w/ws1/projects/p1/agents/ainstall_ab12/chat") === null,
  "an agent's own page under a project's URL is NOT a rail space either — the route is live and unlinked, not decorated",
);
assert(
  railSpaceFromPathname("/w/ws1/projects/p1/tasks") === null,
  "a project's Tasks view is not a space — nothing under a project morphs the rail now",
);
assert(
  railSpaceFromPathname("/w/ws1/projects/p1/documents/doc1") === null,
  "a project's document page is not a space",
);

// ── The workspace-level /agents tree is NOT a rail space either (2026-08-20)
// It briefly was (2026-08-19's "workspace-agents" kind) — see this file's
// module header for why that was reverted. The bare index, an agent's own
// tab page, and a stray "agentsx" segment must all read as "not a space":
// the primary rail stays flat there, and the picker lives in the content
// area instead (AgentConversationList.tsx, wired by agents/layout.tsx).
assert(railSpaceFromPathname("/w/ws1/agents") === null, "the bare workspace /agents index is not a rail space");
assert(railSpaceFromPathname("/w/ws1/agents/") === null, "a trailing slash on it is still not a rail space");
assert(
  railSpaceFromPathname("/w/ws1/agents/ainstall_ab12/chat") === null,
  "a workspace-level agent's own chat page is not a rail space either",
);
assert(railSpaceFromPathname("/w/ws1/agentsx") === null, "a segment merely starting with 'agents' is not a space");
assert(railSpaceFromPathname("/w/ws1/inbox") === null, "Inbox is still not any space");

// SETTINGS IS THE WHOLE UNION. Anything that ever asks "which kind?" of a
// non-null space can only be answered one way — proven here rather than
// assumed, so a second kind cannot be slipped back in silently.
const everySpace = [
  "/w/ws1/settings",
  "/w/ws1/settings/account",
  "/w/ws1/settings/workspace",
  "/w/ws1/settings/connections",
  "/w/ws1/settings/shortcuts",
  "/w/ws1/settings/bogus",
]
  .map((path) => railSpaceFromPathname(path))
  .filter((space): space is NonNullable<typeof space> => space !== null);
assert(everySpace.length === 6, "every /settings pathname above still resolves to a space");
assert(
  everySpace.every((space) => space.kind === "settings"),
  "'settings' is the only RailSpace kind there is",
);

// ── The Settings pick-list ────────────────────────────────────────────────
const links = settingsSpaceLinks(asSettings(account));
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
const bareLinks = settingsSpaceLinks(asSettings(bare));
assert(
  bareLinks.filter((l) => l.active).map((l) => l.key).join(",") === "workspace",
  "bare /settings marks the default section (workspace) active",
);

// Encoded workspace ids are re-encoded in hrefs.
assert(
  settingsSpaceLinks(asSettings(encoded))[0]?.href === "/w/my%20ws/settings/account",
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
import { existsSync, readFileSync } from "node:fs";
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
// INVERTED, 2026-08-21 — these two used to assert PrimaryRail RENDERED the
// project-agents pick-list and gated it on projectAgentsSpaceIsActive. The
// space is deleted (see the block above for the founder's reasoning), so
// the reintroduction guard is what stands in their place: a component-level
// re-implementation would pass every pathname assertion above while putting
// the rejected shape straight back on screen.
assert(
  !/projectAgentsSpaceLinks/.test(railSource),
  "PrimaryRail no longer renders a project-agents pick-list — an agent belongs to the workspace, not a project",
);
assert(
  !/projectAgentsSpaceIsActive/.test(railSource),
  "PrimaryRail no longer gates a project-agents rail morph",
);
assert(
  !/project-agents-rail-shape/.test(railSource),
  "the deleted count gate is not imported back into the rail",
);

// The content-area half: ProjectDetailPage used to HIDE its own tab strip
// while the rail was morphed, because two surfaces both claiming to be
// "where you pick" is the bug that shape kept producing. With no space to
// morph into, that conditional is gone and the strip (Tasks · Documents —
// never an agent picker) renders unconditionally again.
const projectPageSource = readFileSync(
  new URL("../../../app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx", import.meta.url),
  "utf8",
);
assert(
  !projectPageSource.includes('from "@/lib/workspace/fleet/project-agents-rail-shape"'),
  "ProjectDetailPage no longer imports the deleted project-agents count gate",
);
assert(
  !/projectAgentsSpaceIsActive\s*\(/.test(projectPageSource),
  "ProjectDetailPage no longer hides its tab strip for a rail space that cannot exist",
);
// UPDATE, 2026-08-30: the pane this comment used to describe (a content-area
// list of the project's own agents, at 2+ agents, reusing the workspace
// list pane's `.fleet-conversation-row` markup) is itself deleted now, not
// merely unreachable — the project-scoped `/agents` route it served is gone
// outright (next.config.ts's LEGACY_REDIRECTS sends that URL to the agent's
// real, workspace-level address before this page's `view` can ever resolve
// to "agents"), per the founder's hard rule: an agent is independent of
// every project, full stop, not merely "not on the rail". Removing the
// route without removing the page's own fallback render would have left a
// dead JSX block asserting the opposite of the rule it sat under, which is
// exactly the drift this test exists to catch — so the assertion below is
// INVERTED from what it used to pin, same convention as the block above.
assert(
  !/fleet-conversation-row/.test(projectPageSource),
  "the project detail page no longer renders a content-area agent list — the /agents route it served is deleted, not just unlinked",
);

// ── The workspace-level /agents picker is NOT on the rail (2026-08-20) ────
// Reintroduction guard for the exact thing this file's own module header
// documents being reverted: the primary rail must never again reference a
// workspace-agents space or pick-list.
assert(!/workspace-agents/.test(railSource), "PrimaryRail no longer references a workspace-agents rail space");
assert(!/workspaceAgentsSpaceLinks/.test(railSource), "PrimaryRail no longer renders a workspace-agents pick-list");

// The picker lives in the content area instead — and as of fix/agents-surface
// it is the AGENTS THEMSELVES (as a row list, by default since 2026-08-30;
// cards was the original shape and is gone) rather than a persistent column
// beside a pane. That column was deleted for two independent reasons, either
// of which alone would be enough: it existed to sit beside a CHAT (which left
// the platform entirely), and with the rail already offering "Agents" it was a
// SECOND picker in the content area — the one arrangement this whole file
// exists to forbid. So the invariant this section guards is unchanged; only
// the shape that satisfies it moved.
//
// The stronger, more specific guards for the new surface (the deleted files
// staying deleted, the grid actually being wired, no accent, no focus ring)
// live in agent-card-face.test.ts, beside the module they belong to. What is
// asserted HERE is only the part that is about RAIL SPACES.
const agentsPageSource = readFileSync(
  new URL("../../../app/(account)/w/[workspaceId]/agents/page.tsx", import.meta.url),
  "utf8",
);
assert(agentsPageSource.length > 500, "CANARY: the workspace Agents page source was actually read");
assert(
  !existsSync(new URL("../../../app/(account)/w/[workspaceId]/agents/layout.tsx", import.meta.url)),
  "no agents layout re-wraps the agent's own page in a second picker column",
);
assert(
  !agentsPageSource.includes('from "@/lib/workspace/fleet/AgentCards"') && !existsSync(new URL("./AgentCards.tsx", import.meta.url)),
  "AgentCards.tsx (the deleted Cards layout) is gone and the page carries no import of it",
);
assert(
  agentsPageSource.includes('from "@/lib/workspace/fleet/AgentsGroupedList"'),
  "the workspace Agents index shows the agents in the content area — not a prompt to pick from a list that is elsewhere",
);
assert(
  !/Pick an agent to watch it work/.test(agentsPageSource),
  "…and no longer prompts a pick, which only ever made sense beside the deleted column",
);
// RESTORED 2026-08-29 on the founder's own ask (see this file's header for
// the full flip history, and agents/page.tsx's own header for his words).
// Built-and-never-wired is this codebase's most common defect, so the three
// components are asserted to be IMPORTED AND RENDERED, not merely importable.
for (const component of ["AgentsBoard", "AgentsGroupedList", "AgentViewOptions"] as const) {
  assert(
    agentsPageSource.includes(`from "@/lib/workspace/fleet/${component}"`),
    `the workspace Agents index imports ${component} — the founder asked for these views back`,
  );
  assert(
    new RegExp(`<${component}\\b`).test(agentsPageSource),
    `…and RENDERS it, rather than importing it and never reaching the branch`,
  );
}
// The default must still be List (Cards is gone, 2026-08-30). A layout
// switch that quietly moved everyone off the settled surface would satisfy
// every assertion above.
assert(
  /DEFAULT_AGENT_VIEW_OPTIONS/.test(agentsPageSource) && /readAgentViewOptions\s*\(/.test(agentsPageSource),
  "the layout comes from the shared, persisted vocabulary — not a second opinion grown on the page",
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
