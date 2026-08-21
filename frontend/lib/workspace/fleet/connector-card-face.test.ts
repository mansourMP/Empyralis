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

// THE ACCENT IS THE FILL, ASSERTED RATHER THAN REMEMBERED — and these two
// assertions are FLIPPED from what they said when this file was written, on
// the founder's own instruction, given twice: "it's going to be FULL purple
// just like this next button, not like only around it and slightly purple."
//
// They are flipped rather than deleted, deliberately (the same treatment
// CLAUDE.md records for content-security-policy.test.ts): the guard still has
// teeth, it now points the other way, and nobody reading it can mistake the
// overruled hairline shape for the one still intended. The "~69 fills in one
// view" arithmetic the original version enforced is not wrong on its own
// terms — it was simply not this file's call to make.
assert(/fleet-btn--accent-fill/.test(cardBlock), "a card FACE carries the FULL accent fill — the founder's own instruction");
assert(
  !/fleet-btn--accent(?!-fill)/.test(cardBlock),
  "…and never the hairline variant, which he named explicitly as the wrong one",
);

// …and the panel spends it too. Both, now — the fill is simply what Connect
// looks like on this surface, not a scarce resource one control holds.
const panelBlock = code.slice(cardEnd);
assert(/fleet-btn--accent-fill/.test(panelBlock), "the open panel's primary action carries the same fill");

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

// ── PREMIUM IS A MEASUREMENT, NOT AN ADJECTIVE ────────────────────────────
// The founder's words were "slightly bigger and mature… slightly vertically
// deeper, slightly vertically thicker", and every one of those is a value
// that reverts silently the moment someone tidies this CSS. Asserted as
// TOKENS rather than pixels: a literal here would freeze the scale, while the
// real rule is that the card is proportioned from the shared spacing set.
const rowBlock = css.slice(css.indexOf(".fleet-connector-card.fleet-connector-card--row"));
assert(
  /padding:\s*var\(--space-3\)\s+var\(--space-4\)/.test(rowBlock.slice(0, 400)),
  "the card is padded --space-3/--space-4 — deeper than the --space-2 tile it grew out of",
);
assert(
  /\.fleet-connector-grid\.fleet-connector-grid\s*\{[^}]*gap:\s*var\(--space-4\)/.test(css),
  "cards are separated by --space-4, not fleet-theme's 4-up-tile 10px",
);
assert(
  /\.fleet-connector-card--row\s+\.fleet-connector-card-icon\s*\{[^}]*width:\s*40px/.test(css),
  "the logo box scales WITH the card — 32px in a 66px row reads as a favicon adrift",
);
assert(
  !/border-radius:\s*7px/.test(css),
  "the monogram's radius is derived from its own box, never a hand-kept magic number",
);

// THE SEARCH FIELD RECEDES. It was the only filled surface in the view and
// therefore the heaviest object on a screen full of logos — the founder's
// "wtf is this piece of shit at the middle of the screen".
//
// RETARGETED, not weakened: the selector moved from `.fleet-connector-browse`
// (the second card group, which the field used to live inside) to
// `.fleet-connector-search` (its own wrapper at the top of the surface) when
// the field moved above everything it filters. The assertion is the same
// fact about the same control.
assert(
  /\.fleet-connector-search\s*>\s*\.fleet-wizard-input\s*\{[^}]*background:\s*var\(--bg-card\)/.test(css),
  "the search field is drawn on the cards' own surface, not the filled inset",
);
assert(
  !/background:\s*var\(--bg-inset\)/.test(css.slice(css.indexOf(".fleet-connector-search"), css.indexOf(".fleet-connector-search") + 900)),
  "…and the inset fill is not re-added beside it",
);

// ── AND IT IS AT THE TOP ──────────────────────────────────────────────────
// The founder, on a pass that fixed the weight above and left the position
// alone: *"what the fuck, you are putting the search at the middle again —
// isn't it supposed to be at the top?"* Weight and position are two
// different faults; fixing one is not fixing the other, which is exactly how
// this shipped twice. Both are asserted from here on.
{
  // The final return's JSX only — `{heading}` also appears in the loading
  // branch above it, and an indexOf over the whole file would compare the
  // wrong two positions and pass for the wrong reason.
  const tailStart = code.indexOf("const manualComplete");
  assert(tailStart > 0, "CANARY: found the start of ConnectorPicker's main return");
  const tail = code.slice(tailStart);

  const searchAt = tail.indexOf('id="connector-search"');
  const headingAt = tail.indexOf("{heading}");
  const firstGridAt = tail.indexOf('className="fleet-connector-grid"');
  assert(searchAt > 0, "CANARY: the search field is still findable in the rendered tree");
  assert(headingAt > 0, "CANARY: the surface's heading is still rendered by the picker");
  assert(firstGridAt > 0, "CANARY: a card grid is still rendered");

  assert(searchAt < firstGridAt, "the search field is ABOVE every card it filters, not between two groups");
  assert(searchAt < headingAt, "…and above the surface's own heading, which is the founder's own sketch");
}

// BOTH GROUPS ARE FILTERED, and this is the behavioural half of the move.
// The priority row was deliberately exempt while the field sat below it —
// defensible then, a lie now: a filter above a row it does not touch leaves
// nine unrelated apps pinned over the results for "notion".
assert(
  /shownPriority\s*=\s*useMemo\(\(\)\s*=>\s*priorityConnectors\.filter\(matches\)/.test(code),
  "the priority row is filtered by the same predicate — the field's reach matches its position",
);
assert(
  /shownBrowsable\s*=\s*useMemo\(\(\)\s*=>\s*browsableConnectors\.filter\(matches\)/.test(code),
  "…and so is the browsable group, by that same one predicate rather than a second copy of it",
);
// The override is SCOPED — .fleet-wizard-input is the create sequence's own
// form field, where a filled inset is correct. Two jobs, one class.
assert(
  !/^\.fleet-wizard-input\s*\{/m.test(css),
  "…and it never restyles the shared .fleet-wizard-input for every form in the product",
);

// A placeholder LABELS. "Search by name or what it does…" explained how a
// search box works, above a grid of logos.
assert(/placeholder="Search"/.test(code), "the search placeholder is one word");
assert(!/what it does/i.test(code), "…and the sentence that explained searching is gone");

assert(
  /codeOnly/.test("codeOnly") && !/fleet-btn/.test(codeOnly("// className=\"fleet-btn\"\nconst x = 1;")),
  "CANARY: the scan ignores a banned token that only appears in a comment",
);
assert(/fleet-btn/.test(codeOnly('const x = "fleet-btn";')), "CANARY: …and still sees one in real code");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
