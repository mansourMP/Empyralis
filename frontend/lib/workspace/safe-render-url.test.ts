/**
 * safe-render-url.ts unit tests, plus a structural sweep of the render
 * seams that consume it.
 *
 * This repo has no jsdom/RTL (see markdown-lite.test.ts's own header), so
 * this cannot mount <ChatMessage>/<Page> and click a rendered link. Two
 * things it CAN do without one, matching that file's own approach:
 *
 *   1. Call safeExternalHref/safeExternalImageSrc directly with the same
 *      payload shapes markdown-lite.test.ts already exercises against its
 *      own copies of this guard, so all three implementations agree on the
 *      same threat model.
 *   2. STRUCTURALLY scan the known render seams outside the two markdown
 *      renderers — lib/workspace/chat-message.tsx's attachment chip and the
 *      hardware page's CLI-login-URL link — and fail if either one ever
 *      builds an href from data without routing it through this module
 *      first. A behavioural re-render test can only catch the sinks that
 *      exist today; a source scan is what catches the next one skipping the
 *      import. Every entry in SEAMS below is an enumerated, real file with
 *      a written reason it's a seam (matching the allowlist-with-a-verdict
 *      idiom used elsewhere in this codebase, e.g.
 *      test_run_state_scope_fails_closed.py's FailOpenScopeFilterDriftTests)
 *      -- if a new file starts assigning `href={someVariable}` to render
 *      agent/user/channel data, it belongs in this list too.
 *
 * Run: npx tsx lib/workspace/safe-render-url.test.ts
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { safeExternalHref, safeExternalImageSrc } from "./safe-render-url";

let passed = 0;
let failed = 0;

function assertEqual<T>(actual: T, expected: T, label: string): void {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

// ─── safeExternalHref ──────────────────────────────────────────────────────

assertEqual(safeExternalHref("https://example.com/doc"), "https://example.com/doc", "href: https allowed");
assertEqual(safeExternalHref("mailto:a@b.com"), "mailto:a@b.com", "href: mailto allowed");
assertEqual(safeExternalHref("/inside/app"), "/inside/app", "href: same-origin relative path allowed");
assertEqual(safeExternalHref("#section"), "#section", "href: bare anchor allowed");
assertEqual(safeExternalHref(""), null, "href: empty string refused");
assertEqual(safeExternalHref(null), null, "href: null refused");
assertEqual(safeExternalHref(undefined), null, "href: undefined refused");
assertEqual(safeExternalHref("javascript:alert(1)"), null, "href: javascript: refused");
assertEqual(safeExternalHref("javascript:document.title='XSS-FIRED'"), null, "href: javascript: setting document.title refused (the exact payload this suite fires live in the security review)");
assertEqual(safeExternalHref("data:text/html,<script>alert(1)</script>"), null, "href: data: refused");
assertEqual(safeExternalHref("vbscript:msgbox(1)"), null, "href: vbscript: refused");
assertEqual(safeExternalHref("//evil.example.com/x"), null, "href: protocol-relative //host refused (silent host change)");
assertEqual(safeExternalHref("java\tscript:alert(1)"), null, "href: tab-obfuscated javascript: refused");
assertEqual(safeExternalHref("JaVaScRiPt:alert(1)"), null, "href: case-varied javascript: refused");

// ─── safeExternalImageSrc ──────────────────────────────────────────────────

assertEqual(safeExternalImageSrc("https://example.com/pic.png"), "https://example.com/pic.png", "img src: https allowed");
assertEqual(safeExternalImageSrc("/attachments/pic.png"), "/attachments/pic.png", "img src: same-origin relative path allowed");
assertEqual(safeExternalImageSrc("javascript:alert(1)"), null, "img src: javascript: refused");
assertEqual(safeExternalImageSrc("data:image/png;base64,aaaa"), null, "img src: data: refused");
assertEqual(safeExternalImageSrc("mailto:a@b.com"), null, "img src: mailto is not an image scheme");
assertEqual(safeExternalImageSrc("//evil.example.com/x.png"), null, "img src: protocol-relative //host refused");

// ─── Structural: the render seams outside the markdown renderers ─────────

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, "..", "..");

type Seam = { file: string; sinkPattern: RegExp; guardPattern: RegExp; rawSinkPattern: RegExp; label: string };

const SEAMS: Seam[] = [
  {
    // A turn's persisted attachment metadata reaching an <a href> — see
    // this file's own header and the CLAUDE.md entry this security review
    // added: the value is data (a turn's stored metadata), not a literal
    // this component authored.
    file: "lib/workspace/chat-message.tsx",
    sinkPattern: /href=\{(?:href|a\.url)\}/,
    guardPattern: /safeExternalHref\(a\.url\)/,
    rawSinkPattern: /href=\{a\.url\}/,
    label: "chat-message.tsx: attachment chip href is guarded by safeExternalHref",
  },
  {
    // A CLI login flow's own stdout, streamed over the hardware websocket
    // and rendered as a clickable "Open this link and approve" — the app
    // never authored this string either.
    file: "app/(account)/w/[workspaceId]/hardware/[gatewayId]/page.tsx",
    sinkPattern: /href=\{safeUrl\}/,
    guardPattern: /safeExternalHref\(urlText\)/,
    rawSinkPattern: /href=\{urlText\}/,
    label: "hardware page: CLI login URL href is guarded by safeExternalHref",
  },
  {
    // A turn's `metadata.action_href` reaching a Next <Link href>. No
    // producer sets this field anywhere in server_modules today (grepped),
    // so this branch is dead on the live path — but the render seam does
    // not know that, same reasoning as the attachment chip above: turn
    // metadata is data a future producer can populate, not a literal this
    // component authored, and an unguarded `<Link href={actionHref}>`
    // would execute `javascript:`/`data:` the moment anything starts
    // setting the field (security review, 2026-08-13).
    file: "lib/workspace/chat-message.tsx",
    sinkPattern: /href=\{safeActionHref\}/,
    guardPattern: /safeExternalHref\(actionHref\)/,
    rawSinkPattern: /href=\{actionHref\}/,
    label: "chat-message.tsx: provider_error action_href is guarded by safeExternalHref",
  },
];

for (const seam of SEAMS) {
  const source = readFileSync(join(repoRoot, seam.file), "utf8");
  assert(seam.sinkPattern.test(source), `${seam.label} (sink present)`);
  assert(seam.guardPattern.test(source), `${seam.label} (guard present)`);
  // The strongest form of this check: the sanitized value, and ONLY the
  // sanitized value, reaches href in this seam -- an unguarded second href
  // reading the raw field directly would slip past a guard-presence check
  // alone (the guard could be dead code sitting next to a live raw sink).
  assert(!seam.rawSinkPattern.test(source), `${seam.label} (no remaining unguarded raw href)`);
}

// The module itself must never be bypassed by a same-shaped local
// reimplementation quietly drifting from it -- both known callers import it
// rather than declaring their own SAFE_LINK_SCHEMES/cleanUrl copy.
{
  for (const seam of SEAMS) {
    const source = readFileSync(join(repoRoot, seam.file), "utf8");
    assert(
      /from ["']@\/lib\/workspace\/safe-render-url["']/.test(source),
      `${seam.label} (imports safe-render-url rather than a local reimplementation)`,
    );
  }
}

// ─── Summary ───────────────────────────────────────────────────────────────

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
