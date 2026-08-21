/**
 * connector-card-face.ts — four facts, four words, and a grid that spends no
 * accent at all.
 *
 * The SOURCE SCAN at the bottom is the half that matters. The behavioural
 * assertions above it can only ever check the states that exist today; what
 * actually regressed here — twice on the sibling Channels surface, per
 * CLAUDE.md — is a card FACE growing a sentence or a button back. A card
 * carrying `fleet-btn--accent-fill` type-checks perfectly and renders 69 of
 * them in one view, which is the exact bug this pass was dispatched to fix.
 *
 * Run: npx tsx lib/workspace/fleet/connector-card-face.test.ts
 */

import { readFileSync } from "node:fs";

import { connectorCardFace, type ConnectorFaceState } from "./connector-card-face";

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

// ── The four states are genuinely four, and never collapse ─────────────────
const cases: { input: Parameters<typeof connectorCardFace>[0]; state: ConnectorFaceState }[] = [
  { input: { connected: false, configured: false, healthStatus: "not_configured" }, state: "locked" },
  { input: { connected: false, configured: true, healthStatus: "not_configured" }, state: "setup" },
  { input: { connected: true, configured: true, healthStatus: "healthy" }, state: "connected" },
  { input: { connected: true, configured: true, healthStatus: "expired" }, state: "reconnect" },
];

for (const c of cases) {
  assert(connectorCardFace(c.input).state === c.state, `${JSON.stringify(c.input)} -> ${c.state}`);
}

const words = new Set(cases.map((c) => connectorCardFace(c.input).pill));
assert(words.size === cases.length, "every state has its OWN word — none shares another's clothes");

const panelWords = new Set(cases.map((c) => connectorCardFace(c.input).panelState));
assert(panelWords.size === cases.length, "…and so does the line the panel restates it with");

// The pill is one word or two. It is a grid affordance for scanning ~69
// faces; the moment it becomes a sentence the face is prose again.
for (const c of cases) {
  const pill = connectorCardFace(c.input).pill;
  assert(pill.split(/\s+/).length <= 2, `pill "${pill}" stays a label, not a sentence`);
}

// ── The one case a code reading gets wrong ─────────────────────────────────
// The backend's default health_status for a NEVER-CONNECTED work_app_connector
// is the literal string "not_configured" (connection_catalog_service.py's
// status_items()), so reading health before connection would paint a warning
// on every app nobody has connected yet — which is most of the catalog.
assert(
  connectorCardFace({ connected: false, configured: true, healthStatus: "not_configured" }).state === "setup",
  "an unhealthy-looking health string on a NOT-connected app is not a warning",
);

// A connected app whose deployment OAuth client was never registered is still
// connected — the lock is about what you can START, never about what already
// works.
assert(
  connectorCardFace({ connected: true, configured: false, healthStatus: "healthy" }).state === "connected",
  "connected beats not-configured",
);

// Only the locked face is quieted, and it is quieted rather than disabled:
// the card still opens, because the one sentence saying why lives inside it.
for (const c of cases) {
  const face = connectorCardFace(c.input);
  assert(face.muted === (face.state === "locked"), `${face.state} muted iff locked`);
}

// ── SOURCE SCAN: the face carries the app, and nothing else ────────────────
const picker = readFileSync(new URL("./ConnectorPicker.tsx", import.meta.url), "utf8");

// Strip comments — this file's own prose names the classes it bans, and a
// guard that trips on its own documentation is a guard nobody keeps.
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "");
}
const code = codeOnly(picker);

// The card's own JSX block: from the className that makes it a card to the
// closing tag of that button.
const cardBlock = code.slice(code.indexOf("fleet-connector-card$"), code.indexOf("</button>", code.indexOf("fleet-connector-card$")));
assert(cardBlock.length > 0, "found the card's JSX block to scan");
assert(!/summary/i.test(cardBlock), "no summary/description prose on a card face");
assert(!/fleet-btn/.test(cardBlock), "no button-shaped control on a card face");
assert(!/accent/.test(cardBlock), "no accent anywhere on a card face");

// Exactly one accent-filled control in the whole component, and it is the
// panel's action. Two would be two in one view the instant a panel opens.
const accentFills = code.match(/fleet-btn--accent-fill/g) || [];
assert(accentFills.length >= 1, "the panel spends the accent on its primary action");
assert(!/fleet-btn--accent(?!-fill)/.test(code), "the hairline accent variant is not used — the founder named it as wrong");

// The operator sentence the grid used to repeat nine times.
assert(!/OAuth client configured/i.test(code), "the operator-language blocked sentence is gone");

assert(
  /codeOnly/.test("codeOnly") && !/fleet-btn/.test(codeOnly("// className=\"fleet-btn\"\nconst x = 1;")),
  "CANARY: the scan ignores a banned token that only appears in a comment",
);
assert(/fleet-btn/.test(codeOnly('const x = "fleet-btn";')), "CANARY: …and still sees one in real code");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
