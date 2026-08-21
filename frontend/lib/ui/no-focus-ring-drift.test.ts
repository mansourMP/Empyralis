/**
 * THE PRODUCT DRAWS NO FOCUS RING. Founder decision, 2026-08-21.
 *
 * This is a drift test, not a behaviour test, and it exists because the ring
 * has been handed back to him THREE times — violet, then neutral grey, then a
 * thinner neutral grey. Each pass believed it was fixing the previous one's
 * complaint (colour, then contrast, then weight); the complaint was never any
 * of those. His last word: "I don't need that ring of yours in my platform."
 *
 * A behavioural test cannot catch the fourth attempt. A ring is one CSS
 * declaration that compiles, renders, and looks deliberate — and the last two
 * that survived a token sweep did so precisely because they never referenced
 * --focus-ring at all (a hand-rolled `0 0 0 2px rgba(180,180,180,.12)` on dark
 * fields, and two 1px composer rings built with color-mix). So this scans for
 * the SHAPE — any :focus / :focus-visible / :focus-within rule that paints a
 * visible outline or a spread box-shadow — regardless of which colour it
 * reaches for.
 *
 * WHAT IS ALLOWED, AND WHY EACH ONE IS NOT A RING:
 *   outline: 0 | none            suppression. the point of this decision.
 *   box-shadow: none             same.
 *   var(--app-shadow-focus)      resolves to `none` (lib/ui/chrome.css).
 *   a blurred drop shadow        elevation, not a rectangle around a control
 *                                (`0 20px 60px -28px`). A ring is what has a
 *                                zero blur and a spread; depth is not.
 *   `0 0 0 1000px ... inset`     Chrome's autofill-background override. It
 *                                fills the field, it does not outline it.
 *
 * CANARY: if the scan stops finding CSS files, or stops finding any :focus
 * rules at all, that is its own loud failure — never a silent green. This
 * codebase has shipped a drift test that scanned a directory containing zero
 * matching files and reported success for weeks
 * (__tests__/exec-file-timeout-child-leak.test.ts, recorded in CLAUDE.md).
 *
 * If a control genuinely becomes unusable without an indicator, the answer is
 * to report it to the founder — not to add a ring and an allowlist entry here.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { strict as assert } from "node:assert";
import test from "node:test";

const ROOT = join(__dirname, "..", "..");

function cssFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".next") || entry === ".git") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) cssFiles(full, out);
    else if (entry.endsWith(".css")) out.push(full);
  }
  return out;
}

/** A ring: zero-offset, zero-blur, non-zero spread — i.e. a hard edge traced
 *  around the control. `0 0 0 1000px ... inset` is excluded by the caller. */
const SPREAD_RING = /(^|,)\s*0\s+0\s+0\s+([1-9]\d*)px\b/;
/** A drawn outline is anything that is not the `0` / `none` suppression. */
const DRAWN_OUTLINE = /^(?!0\b|none\b)/;

type Finding = { file: string; line: number; selector: string; decl: string };

function scan(): { findings: Finding[]; focusRules: number; files: number } {
  const files = cssFiles(ROOT);
  const findings: Finding[] = [];
  let focusRules = 0;

  for (const file of files) {
    const src = readFileSync(file, "utf8");
    for (const rule of src.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = rule[1].replace(/\/\*[\s\S]*?\*\//g, "").trim();
      const body = rule[2];
      if (!/:focus(-visible|-within)?\b/.test(selector)) continue;
      focusRules += 1;
      const line = src.slice(0, rule.index).split("\n").length;
      const at = (decl: string): Finding => ({
        file: relative(ROOT, file),
        line,
        selector: selector.split(/\s+/).join(" ").slice(0, 90),
        decl,
      });

      for (const d of body.matchAll(/outline\s*:\s*([^;]+);/g)) {
        const value = d[1].trim();
        if (DRAWN_OUTLINE.test(value)) findings.push(at(`outline: ${value}`));
      }
      for (const d of body.matchAll(/box-shadow\s*:\s*([^;]+);/g)) {
        const value = d[1].replace(/\s+/g, " ").trim();
        if (/\binset\b/.test(value)) continue; // fills, never outlines
        if (SPREAD_RING.test(value)) findings.push(at(`box-shadow: ${value}`));
      }
    }
  }
  return { findings, focusRules, files: files.length };
}

test("the CSS scan actually reaches this codebase (canary)", () => {
  const { files, focusRules } = scan();
  assert.ok(files > 10, `expected to find the app's CSS; found ${files} files — the scan is looking in the wrong place and can only report a false green`);
  assert.ok(focusRules > 50, `expected plenty of :focus rules to inspect; found ${focusRules} — the rule parser has stopped matching and this test now enforces nothing`);
});

test("no :focus rule draws a ring", () => {
  const { findings } = scan();
  const report = findings
    .map((f) => `  ${f.file}:${f.line}\n    ${f.selector}\n    -> ${f.decl}`)
    .join("\n");
  assert.equal(
    findings.length,
    0,
    `Focus ring(s) reintroduced. The founder has rejected this three times and the\n` +
      `decision is recorded in CLAUDE.md and beside --focus-ring in theme-tokens.css.\n` +
      `A control shows focus through its OWN styling (border steps up, caret blinks),\n` +
      `never a rectangle drawn around it.\n\n${report}\n`,
  );
});

test("--app-shadow-focus paints nothing", () => {
  const chrome = readFileSync(join(ROOT, "lib", "ui", "chrome.css"), "utf8");
  const values = [...chrome.matchAll(/--app-shadow-focus\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.ok(values.length >= 3, `expected every theme block to define --app-shadow-focus; found ${values.length}`);
  for (const value of values) {
    assert.equal(value, "none", `--app-shadow-focus must stay 'none' — found '${value}'`);
  }
});

test("AgentCreateCard does not focus itself on open", () => {
  const card = readFileSync(
    join(ROOT, "lib", "workspace", "fleet", "AgentCreateCard.tsx"),
    "utf8",
  );
  const code = card.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  assert.ok(
    !/nameRef\.current\?\.focus\(\)/.test(code),
    "AgentCreateCard focuses its Name field on open again. Programmatic .focus() " +
      "satisfies :focus-visible exactly like a real Tab press, so this is what put " +
      "a focused state on screen before the customer had touched anything — the " +
      "screen the founder kept screenshotting. Removed 2026-08-21; do not restore.",
  );
});
