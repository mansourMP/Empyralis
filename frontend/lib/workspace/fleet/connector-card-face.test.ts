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

// ── The right-hand slot offers an ACTION only where one exists ─────────────
// A button is a thing to press. A connected app has nothing left to press,
// and a not-connectable one has nothing a customer could press — both keep
// the pill instead. "No dead controls" decided here, once, rather than at the
// render site where the next state added would forget it.
assert(
  connectorCardFace({ connected: false, configured: true, healthStatus: "" }).action === "connect",
  "a connectable app offers Connect",
);
assert(
  connectorCardFace({ connected: true, configured: true, healthStatus: "expired" }).action === "reconnect",
  "an unwell connection offers Reconnect — a different word for a different act",
);
assert(
  connectorCardFace({ connected: true, configured: true, healthStatus: "healthy" }).action === "none",
  "a working connection offers no button",
);
assert(
  connectorCardFace({ connected: false, configured: false, healthStatus: "" }).action === "none",
  "an app this deployment cannot connect offers no button — pressing it could only fail",
);

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

// renderCard's body, up to the block that builds the panel.
const cardStart = code.indexOf("const renderCard");
const cardEnd = code.indexOf("const catalogSize");
const cardBlock = code.slice(cardStart, cardEnd);
assert(cardStart > 0 && cardEnd > cardStart, "found renderCard's body to scan");

// PROSE is the thing that must never come back to a face. The founder's own
// words: "why do we have those written text right there?"
assert(!/summary/i.test(cardBlock), "no summary/description prose on a card face");

// THE ACCENT ARITHMETIC, asserted rather than remembered. The face carries an
// accent-COLOURED button (he asked for Connect in the accent) and must never
// carry a FILLED one: ~69 faces render at once, so one fill here is ~69 fills
// in one view — the exact shape CLAUDE.md calls a bug.
assert(!/accent-fill/.test(cardBlock), "a card FACE never carries the accent FILL — 69 faces would be 69 fills");
assert(/fleet-btn--accent(?!-fill)/.test(cardBlock), "…it carries the hairline accent, so Connect still reads as the action");

// …and the panel is where the one fill lives.
const panelBlock = code.slice(cardEnd);
assert(/fleet-btn--accent-fill/.test(panelBlock), "the open panel spends the accent fill on its primary action");

// TWO REAL BUTTONS, NEVER NESTED. `<button>` inside `<button>` is invalid and
// browsers un-nest it; a div-with-onClick would be mouse-only. The card body
// is its own button whose hit area is stretched in CSS.
assert(/fleet-connector-card-open/.test(cardBlock), "the card body is a real button, not a div with onClick");
assert(/stopPropagation/.test(cardBlock), "the action's click does not travel into the card");

const css = readFileSync(new URL("./connector-cards.css", import.meta.url), "utf8");
assert(
  /\.fleet-connector-card-open::after\s*\{[^}]*inset:\s*0/.test(css),
  "…and the card body's hit area is genuinely stretched over the whole card",
);
assert(
  /\.fleet-connector-grid\.fleet-connector-grid\s*\{[^}]*repeat\(2,/.test(css),
  "two per row at desktop — the founder's own correction, doubled so bundle order cannot decide it",
);

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
