/**
 * Guards the blank-page trap one layer above the one shell-segment-coverage
 * already guards. w/[workspaceId]/error.tsx exists because a render crash
 * anywhere under /w/{workspaceId} used to fall through to Next's bare
 * built-in fallback — a blank white page in a production build, no message,
 * no recovery link (see that file's own header comment: "the reported
 * 'Hardware blank page'"). Nothing above that segment had the same
 * protection: app/page.tsx (root "/"), app/(account)/layout.tsx and every
 * route under it (/workspaces/new, /settings, …) had NO app-authored error
 * boundary at all until app/error.tsx and app/global-error.tsx were added.
 *
 * That gap is exactly where a genuinely dead session lands: a valid cookie
 * for a deleted account resolves through app/page.tsx and (account)/
 * layout.tsx before FleetShell ever mounts, so those two files are the ones
 * a crash there needs a boundary above.
 *
 * Next.js's own error-boundary rule: error.tsx catches a throw in every
 * descendant EXCEPT the layout.tsx in its own segment; only global-error.tsx
 * (which replaces the root layout's own <html>/<body>) can catch a throw in
 * app/layout.tsx itself. Both are required; neither alone closes the gap.
 *
 * WHAT IT CHECKS vs WHAT IT DERIVES — the file paths are fixed by Next.js's
 * own routing convention (this is not a scan that could silently find
 * nothing and pass vacuously), so the canary here instead guards against the
 * files existing but being hollow: present on disk with no real recovery
 * affordance, which is the same "blank page with extra steps" failure this
 * guard exists to catch.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { strict as assert } from "node:assert";
import test from "node:test";

const FRONTEND_ROOT = join(__dirname, "..", "..");
const APP_ROOT = join(FRONTEND_ROOT, "app");
const ROOT_ERROR_PATH = join(APP_ROOT, "error.tsx");
const GLOBAL_ERROR_PATH = join(APP_ROOT, "global-error.tsx");
const WORKSPACE_ERROR_PATH = join(
  APP_ROOT,
  "(account)",
  "w",
  "[workspaceId]",
  "error.tsx",
);

test("canary: the app root actually resolves (app/page.tsx exists)", () => {
  assert.ok(
    existsSync(join(APP_ROOT, "page.tsx")),
    "FRONTEND_ROOT/APP_ROOT path math is wrong — this test would silently check nothing",
  );
});

test("canary: the already-fixed workspace-segment boundary still exists (regression would mean re-opening a known bug)", () => {
  assert.ok(
    existsSync(WORKSPACE_ERROR_PATH),
    "app/(account)/w/[workspaceId]/error.tsx is gone — the 'Hardware blank page' bug it fixed has no guard left",
  );
});

test("app/error.tsx exists — catches a crash in app/page.tsx and every (account) route not already covered by a nested error.tsx", () => {
  assert.ok(
    existsSync(ROOT_ERROR_PATH),
    "No app/error.tsx: a render crash in app/page.tsx (the '/' route every stale session and every fresh " +
      "sign-in resolves through) or in (account)/layout.tsx's descendants falls through to Next's bare " +
      "fallback with no app chrome — a blank white page on a production build.",
  );
});

test("app/error.tsx is a real client error boundary with a recovery action, not a hollow stub", () => {
  const source = readFileSync(ROOT_ERROR_PATH, "utf8");
  assert.match(source, /'use client'/, "error.tsx must be a Client Component (Next.js requirement)");
  assert.match(
    source,
    /export default function/,
    "error.tsx must export a default component Next.js can mount as the boundary",
  );
  assert.match(
    source,
    /\{\s*error\s*,/,
    "error.tsx should accept Next's `error` prop — silently ignoring it drops the one diagnostic signal available",
  );
  assert.match(
    source,
    /ShellRecoveryActions/,
    "must offer the same Reload / Sign in again recovery used by (account)/layout.tsx's degraded-session " +
      "message — a caught crash with no way back is the same dead end as an uncaught one",
  );
  assert.doesNotMatch(
    source,
    /return\s+null\s*;/,
    "must render something visible — returning null on a caught error IS the blank page this guard exists to stop",
  );
});

test("app/global-error.tsx exists — the only boundary that can catch a crash in app/layout.tsx itself", () => {
  assert.ok(
    existsSync(GLOBAL_ERROR_PATH),
    "No app/global-error.tsx: error.tsx never catches its own segment's layout, so a throw in the ROOT " +
      "layout (app/layout.tsx) has no boundary at all without this file — Next's bare fallback, blank page.",
  );
});

test("app/global-error.tsx is self-contained and offers a real recovery path", () => {
  const source = readFileSync(GLOBAL_ERROR_PATH, "utf8");
  assert.match(source, /'use client'/, "global-error.tsx must be a Client Component (Next.js requirement)");
  assert.match(source, /export default function/, "global-error.tsx must export a default component");
  assert.match(
    source,
    /<html/,
    "global-error.tsx replaces the root layout, so it must render its own <html> — Next.js requirement",
  );
  assert.match(source, /<body/, "global-error.tsx must render its own <body> — Next.js requirement");
  assert.match(
    source,
    /href=["']\/login["']/,
    "must offer a real link back to /login — the whole point of this boundary is a person can still get out",
  );
  // Deliberately self-contained (see the file's own header comment): if the
  // thing that broke IS the app shell (root layout, its CSS imports), this
  // file cannot assume any of that survived. A future edit that reaches for
  // a shared component or a stylesheet import quietly reintroduces the same
  // fragility this file exists to avoid.
  assert.doesNotMatch(
    source,
    /from ['"]@\/(lib|app)\/(?!.*global-error)/,
    "global-error.tsx imports from app code again — re-read the file's own header comment on why it " +
      "deliberately renders with zero shared dependencies before adding one back",
  );
});
