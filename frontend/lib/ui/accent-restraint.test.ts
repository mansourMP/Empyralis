/**
 * THE ACCENT BELONGS TO THE FILLED PRIMARY BUTTON, AND TO NOTHING ELSE.
 * Founder decision, 2026-08-21: "do that, purple only on primary buttons."
 *
 * This is a drift test, not a behaviour test, and it exists because four
 * separate passes removed individual purple rings and purple kept coming
 * back. It kept coming back because nothing was wrong with any single site —
 * `--accent` is a token that means "important", and every author who wanted
 * something to look important reached for it. Forty-eight declarations were
 * painting with it across the workspace CSS when this test was written; nine
 * were the primary button.
 *
 * So the rule is inverted here rather than enforced instance by instance: a
 * declaration may reference var(--accent…) ONLY if its selector is on the
 * allowlist below, and the allowlist is exclusively filled primary actions.
 * Everything else — selected states, active tabs, badges, dots, links, toggle
 * tracks, hover washes, icon tiles, rings — is neutral.
 *
 * WHY A SOURCE SCAN AND NOT A RENDER TEST. A fortieth accent declaration
 * compiles, renders, and looks deliberate; there is no behaviour to assert
 * against. The same reasoning no-focus-ring-drift.test.ts states for its own
 * shape, and this file is deliberately its sibling in structure.
 *
 * WHAT IS SCANNED, AND THE ONE EXCLUSION.
 *   lib/**, app/**   every .css file. This is the product.
 *   lib/ui/chrome.css   EXCLUDED, with a reason rather than by oversight.
 *     The legacy app-shell, carrying accent declarations whose selectors have
 *     zero consumer anywhere outside that one file (studio-*, marketplace-*,
 *     deployed-agents-*, app-filter-pill…). Its LIVE surfaces were swept by
 *     hand in the same change; bringing the dead remainder under the rule
 *     means deleting dead CSS, which is a different job with a different
 *     risk. Do not quietly widen this to chrome.css without doing that job —
 *     a scan that fails on dead rules is a scan someone will disable.
 *
 *     MEASURED 2026-08-28 by running this scan with EXCLUDED neutralised.
 *     The sage-* dead CSS has since been deleted (532 rules, 3 keyframes,
 *     26,533 -> 22,411 lines), and the widening still fails:
 *         before the sage prune   97 accent declarations
 *         after  the sage prune   85
 *     77 of the 85 are in other dead families (marketplace-pane-* 15,
 *     app-memory-* 9, studio-ai-* 8, app-chat-* 7, studio-agent-* 6,
 *     deployed-agents-* 6, app-studio-* 5, plus a tail) — more deletions.
 *     THE OTHER 8 ARE ON LIVE SELECTORS: app-auth-* (4, .app-auth-kicker
 *     among them, i.e. the login page), cloud-vps-* (4) and one
 *     workstation-hardware-*. Those are a design decision about the accent
 *     rule, not cleanup — so finishing the deletions does not on its own
 *     make this widenable.
 *
 * ALIASES ARE FOLLOWED. --interactive-accent, --app-accent and friends all
 * resolve to --accent, and a rule painting with an alias is painting with the
 * accent. The alias list is DERIVED from the token files rather than typed
 * here, so a new alias is covered the day it is defined — the "expected set
 * and actual set must come from different sources" rule this codebase already
 * learned from preflight._check_rls().
 *
 * CANARIES. If the scan stops finding CSS files, stops resolving aliases, or
 * stops finding the allowlisted primary buttons themselves, that is its own
 * loud failure — never a silent green. This codebase has shipped a drift test
 * that scanned a directory containing zero matching files and reported
 * success for weeks (recorded in CLAUDE.md).
 *
 * IF SOMETHING GENUINELY NEEDS EMPHASIS, the answer is weight, shape or a
 * neutral fill — not an allowlist entry here. The allowlist grows only when a
 * new FILLED PRIMARY BUTTON is added.
 *
 * Run: npx tsx lib/ui/accent-restraint.test.ts
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { strict as assert } from "node:assert";
import test from "node:test";

const ROOT = join(__dirname, "..", "..");

/** The only selectors allowed to paint with the accent. Every one of them is
 *  a FILLED primary action — a solid accent block a person presses, which is
 *  what the founder kept and overruled two attempts to un-fill. A ring, a
 *  border, a tint or a text colour never qualifies, whatever it sits on. */
const PRIMARY_BUTTON_ALLOWLIST: { selector: RegExp; why: string }[] = [
  {
    selector: /^\.fleet-btn--accent-fill\b/,
    why: "the app-wide filled primary action (Create agent, Next, Connect, New task)",
  },
  {
    selector: /^\.fleet-assistant-chat-send\b/,
    why: "Ask AI's filled circular send button — the composer's primary action",
  },
  {
    selector: /^\.fleet-task-detail-composer-send\b/,
    why: "the task comment composer's filled circular send button",
  },
  {
    selector: /^\.continue-page__button--primary\b/,
    why: "the /continue hand-off page's primary CTA",
  },
  {
    selector: /^\.app-get-started-button\b/,
    why: "the marketing/first-launch primary CTA",
  },
  {
    selector: /^\.landing-cta--primary\b/,
    why: "the landing page's two conversion CTAs (the page has no importer today, but the class is a genuine filled primary and is allowlisted on that basis, not on reachability)",
  },
];

/** Token DEFINITIONS are not uses. `--accent-soft: color-mix(… var(--accent) …)`
 *  in a :root/[data-theme] block is the palette declaring itself. */
function isTokenDefinitionSelector(selector: string): boolean {
  return (
    selector.includes(":root") ||
    selector.includes("[data-theme") ||
    selector.startsWith("@")
  );
}

function cssFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".next") || entry === ".git") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) cssFiles(full, out);
    else if (entry.endsWith(".css")) out.push(full);
  }
  return out;
}

const EXCLUDED = join("lib", "ui", "chrome.css");

function scannedFiles(): string[] {
  return [...cssFiles(join(ROOT, "lib")), ...cssFiles(join(ROOT, "app"))].filter(
    (f) => relative(ROOT, f) !== EXCLUDED,
  );
}

/**
 * Every custom property that resolves to --accent, derived from the token
 * files rather than hand-listed. Seeded with `accent` itself and closed
 * transitively, so `--interactive-accent: var(--app-accent)` is caught via
 * `--app-accent: var(--accent)` without anybody noticing the chain.
 */
function accentAliases(): Set<string> {
  const sources = [join(ROOT, "lib", "ui", "theme-tokens.css"), join(ROOT, "lib", "ui", "chrome.css")];
  const definitions: { name: string; value: string }[] = [];
  for (const file of sources) {
    const src = readFileSync(file, "utf8");
    for (const m of src.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;{}]+);/gi)) {
      definitions.push({ name: m[1], value: m[2] });
    }
  }
  const aliases = new Set<string>(["--accent"]);
  // Transitive closure. Bounded by the definition count, so it always halts.
  for (let pass = 0; pass < definitions.length; pass++) {
    let grew = false;
    for (const { name, value } of definitions) {
      if (aliases.has(name)) continue;
      for (const ref of value.matchAll(/var\((--[a-z0-9-]+)/gi)) {
        if (aliases.has(ref[1])) {
          aliases.add(name);
          grew = true;
          break;
        }
      }
    }
    if (!grew) break;
  }
  return aliases;
}

type Finding = { file: string; line: number; selector: string; decl: string };

function scan(): {
  findings: Finding[];
  allowed: Map<string, number>;
  rules: number;
  files: number;
  aliases: Set<string>;
} {
  const aliases = accentAliases();
  const files = scannedFiles();
  const findings: Finding[] = [];
  const allowed = new Map<string, number>();
  let rules = 0;

  const usesAccent = (value: string): boolean =>
    [...value.matchAll(/var\((--[a-z0-9-]+)/gi)].some((m) => aliases.has(m[1]));

  for (const file of files) {
    const src = readFileSync(file, "utf8");
    for (const rule of src.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = rule[1].replace(/\/\*[\s\S]*?\*\//g, "").split(/\s+/).join(" ").trim();
      const body = rule[2].replace(/\/\*[\s\S]*?\*\//g, "");
      if (!selector || isTokenDefinitionSelector(selector)) continue;
      rules += 1;

      for (const d of body.matchAll(/([a-z-]+)\s*:\s*([^;]+)/gi)) {
        const [, property, value] = d;
        if (!usesAccent(value)) continue;

        // A selector list is allowed only if EVERY comma-separated part is.
        const parts = selector.split(",").map((s) => s.trim()).filter(Boolean);
        const entry = PRIMARY_BUTTON_ALLOWLIST.find((a) =>
          parts.every((p) => a.selector.test(p)),
        );
        if (entry) {
          allowed.set(entry.why, (allowed.get(entry.why) ?? 0) + 1);
          continue;
        }
        findings.push({
          file: relative(ROOT, file),
          line: src.slice(0, rule.index).split("\n").length,
          selector: selector.slice(0, 110),
          decl: `${property.trim()}: ${value.replace(/\s+/g, " ").trim().slice(0, 110)}`,
        });
      }
    }
  }
  return { findings, allowed, rules, files: files.length, aliases };
}

test("the scan reaches this codebase (canary)", () => {
  const { files, rules, aliases } = scan();
  assert.ok(
    files > 10,
    `expected to find the app's CSS; found ${files} files — the scan is looking in the wrong place and can only report a false green`,
  );
  assert.ok(
    rules > 500,
    `expected plenty of CSS rules to inspect; found ${rules} — the rule parser has stopped matching and this test now enforces nothing`,
  );
  assert.ok(
    aliases.has("--accent-soft") && aliases.has("--app-accent") && aliases.has("--interactive-accent"),
    `alias resolution has stopped following the token graph; got ${[...aliases].sort().join(", ")}`,
  );
});

test("the allowlisted primary buttons still exist (canary)", () => {
  const { allowed } = scan();
  // Zero allowed uses would mean the app has no accent-filled primary action
  // anywhere — a different bug, and one that would pass a "no violations" test
  // perfectly happily. create-accent.ts asserts the same "never zero" shape
  // for its own rule and for the same reason.
  assert.ok(
    allowed.size >= 3,
    `expected the filled primary buttons to still be painted with the accent; matched ${allowed.size} of ${PRIMARY_BUTTON_ALLOWLIST.length} allowlist entries — either they were neutralised too, or the allowlist selectors no longer match anything`,
  );
});

test("the scanner would still catch a violation (canary)", () => {
  // Proves the matcher is live rather than trivially green: the exact shape a
  // regression takes must be classified as a violation, and the exact shape of
  // an allowlisted primary button must not.
  const aliases = accentAliases();
  const uses = (value: string) =>
    [...value.matchAll(/var\((--[a-z0-9-]+)/gi)].some((m) => aliases.has(m[1]));

  assert.ok(uses("1px solid var(--accent)"), "a direct accent border must read as an accent use");
  assert.ok(uses("var(--accent-soft)"), "a soft accent wash must read as an accent use");
  assert.ok(uses("var(--app-accent-muted)"), "an aliased accent must read as an accent use");
  assert.ok(!uses("var(--text-primary)"), "a neutral must NOT read as an accent use");
  assert.ok(!uses("var(--online-dot)"), "semantic status colour must NOT read as an accent use");

  const allowed = (selector: string) =>
    PRIMARY_BUTTON_ALLOWLIST.some((a) =>
      selector.split(",").map((s) => s.trim()).every((p) => a.selector.test(p)),
    );
  assert.ok(allowed(".fleet-btn--accent-fill"), "the filled primary button must stay allowed");
  assert.ok(
    allowed(".fleet-btn--accent-fill:hover:not(:disabled)"),
    "its hover state must stay allowed",
  );
  assert.ok(
    !allowed(".fleet-btn--accent"),
    "the quiet, non-primary variant must NOT be allowed — it is precisely the control that does not own the view's primary action",
  );
  assert.ok(
    !allowed(".agent-create-option.is-selected"),
    "a selected picker card must NOT be allowed — this is the state the founder screenshotted",
  );
  assert.ok(
    !allowed(".fleet-btn--accent-fill, .fleet-rail-item--active::before"),
    "a selector list must be allowed only when EVERY part of it is",
  );
});

test("nothing but the filled primary button paints with the accent", () => {
  const { findings } = scan();
  const report = findings
    .map((f) => `  ${f.file}:${f.line}\n    ${f.selector}\n    -> ${f.decl}`)
    .join("\n");
  assert.equal(
    findings.length,
    0,
    `The accent escaped the primary button again (${findings.length} declaration(s)).\n\n` +
      `The founder's rule, 2026-08-21: "purple only on primary buttons." It is\n` +
      `recorded in CLAUDE.md and it was reached only after four passes that each\n` +
      `removed one purple ring and watched another appear.\n\n` +
      `If this needs to look important, use WEIGHT (--text-primary against\n` +
      `--text-secondary), SHAPE (a checkmark, a filled dot) or a NEUTRAL fill\n` +
      `(--bg-inset). Do not add an allowlist entry unless what you are adding is\n` +
      `literally a new accent-FILLED primary button.\n\n${report}\n`,
  );
});
