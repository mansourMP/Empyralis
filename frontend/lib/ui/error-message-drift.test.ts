/**
 * MAN-327: `new Error(data?.error || data?.detail)` renders the literal
 * text "[object Object]" to a customer whenever the backend's `error`/
 * `detail` field is a structured object rather than a string — FastAPI's
 * own validation-error shape (`{"detail": [{"type":..., "loc":..., "msg":
 * ...}]}`) does exactly this. `getErrorMessage` (lib/ui/api-error.ts) is
 * the fix: it only ever returns a string, falling back to a human message
 * when `detail`/`error` isn't one.
 *
 * A prior pass wired ~29 call sites across the wizard/fleet surfaces into
 * getErrorMessage but never checked the rest of the frontend. This is the
 * structural half, the same idiom as authorized-fetch-drift.test.ts and
 * accent-restraint.test.ts: scan every .ts/.tsx file under lib/ and app/
 * for the unsafe shape and require every hit to be either safe (routed
 * through getErrorMessage, or already type-guarded with a `typeof x ===
 * "string"` check) or named in ALLOWLIST with a written reason.
 *
 * WHAT COUNTS AS A HIT: `new Error(` (or a `throw`/template built from) an
 * expression referencing `.error` or `.detail` off a variable that looks
 * like a parsed response body (`data`, `json`, `body`, `payload`, `res`,
 * `result`, `d`, `credData`, `profileData`, or similar `*Data`/`*Body`
 * suffixes), with NO `typeof ... === "string"` guard on the same line and
 * no call to `getErrorMessage(` on the same line. A behavioural test
 * cannot catch this — the bad code compiles, runs, and looks identical to
 * the fixed version until a route happens to return an object instead of
 * a string, which is exactly how the wizard/fleet instances went unnoticed
 * for as long as they did.
 *
 * Run: npx tsx lib/ui/error-message-drift.test.ts
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
// This file lives at lib/ui/ — the frontend root is two levels up.
const FRONTEND_ROOT = join(HERE, "..", "..");
const SCAN_DIRS = ["lib", "app"];
const EXCLUDE_DIR_NAMES = new Set(["node_modules", ".next"]);

/**
 * file path (relative to frontend/) -> reason it's allowed to build an
 * Error/message from an unguarded `.error`/`.detail` access. Every entry
 * here is a deliberate exception, not an oversight.
 */
const ALLOWLIST: Record<string, string> = {
  "app/api/auth/google/callback/route.ts":
    "a Next.js server route handler relaying Google's own OAuth token-exchange error, never our backend's response shape — Google's error/error_description fields are always strings per the OAuth spec, and this never reaches a customer-facing render (it redirects with a short code).",
};

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

type ScanHit = { file: string; line: number; text: string };

function walk(dir: string, out: string[]): string[] {
  for (const entry of readdirSync(dir)) {
    if (EXCLUDE_DIR_NAMES.has(entry)) continue;
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      walk(full, out);
    } else if (/\.(ts|tsx)$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

// Variable-name heuristic for "this looks like a parsed HTTP response
// body" — the same handful of names this codebase's own call sites use
// (data/json/body/payload/res/result/d, plus any identifier ending in
// Data/Body/Payload/Res/Result, e.g. credData, profileData).
const RESPONSE_VAR = "(?:data|json|body|payload|res|result|d|[A-Za-z_]*(?:Data|Body|Payload|Res|Result))";
// `<var>?.error` / `<var>.error` / `<var>?.detail` / `<var>.detail` —
// matched independently since either field alone is enough to trigger the
// bug (`data?.error || fallback` is just as unsafe as the classic `||`
// pair form).
const UNSAFE_ACCESS = new RegExp(`\\b${RESPONSE_VAR}\\??\\.(?:error|detail)\\b`);

function findUnsafeErrorHits(source: string): { line: number; text: string }[] {
  const hits: { line: number; text: string }[] = [];
  source.split("\n").forEach((line, idx) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
    if (!/\b(?:new Error\(|throw\s)/.test(line)) return;
    if (!UNSAFE_ACCESS.test(line)) return;
    // Safe if this exact line already routes through getErrorMessage(...).
    if (/getErrorMessage\(/.test(line)) return;
    // Safe if this exact line already type-guards the access
    // (`typeof x === "string" ? x : fallback`), the pattern several
    // existing call sites already use inline instead of the shared helper.
    if (/typeof\s+[A-Za-z0-9_.?]+\s*===\s*["']string["']/.test(line)) return;
    hits.push({ line: idx + 1, text: trimmed });
  });
  return hits;
}

function isServerOnly(source: string): boolean {
  return /^\s*import\s+['"]server-only['"]/m.test(source);
}

function main(): void {
  const files: string[] = [];
  for (const d of SCAN_DIRS) walk(join(FRONTEND_ROOT, d), files);
  assert(files.length > 100, `sanity: scanned a real number of files (got ${files.length})`);

  const violations: ScanHit[] = [];
  const seenAllowlistEntries = new Set<string>();

  for (const absPath of files) {
    const relPath = relative(FRONTEND_ROOT, absPath).split("\\").join("/");
    if (relPath.endsWith(".test.ts") || relPath.endsWith(".test.tsx")) continue;

    const source = readFileSync(absPath, "utf8");
    const hits = findUnsafeErrorHits(source);
    if (hits.length === 0) continue;

    if (relPath in ALLOWLIST) {
      seenAllowlistEntries.add(relPath);
      continue;
    }

    for (const hit of hits) {
      violations.push({ file: relPath, line: hit.line, text: hit.text });
    }
  }

  assert(
    violations.length === 0,
    violations.length === 0
      ? "no unguarded data?.error/data?.detail stringify hazard"
      : `${violations.length} unguarded error-message hazard(s) found outside the allowlist:\n` +
          violations.map((v) => `    ${v.file}:${v.line}: ${v.text}`).join("\n") +
          "\n  Route each through getErrorMessage(), guard with typeof ... === \"string\", or add a reasoned entry to ALLOWLIST in this test.",
  );

  // A stale allowlist entry (the file was deleted, or its hazard was fixed
  // some other way) would let the allowlist quietly stop meaning anything —
  // the same "dead entry" failure mode authorized-fetch-drift.test.ts
  // already guards against.
  for (const relPath of Object.keys(ALLOWLIST)) {
    assert(seenAllowlistEntries.has(relPath), `stale allowlist entry — no hazard found in ${relPath} anymore; remove it`);
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

main();
