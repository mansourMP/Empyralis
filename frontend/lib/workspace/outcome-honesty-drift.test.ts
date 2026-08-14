/**
 * Structural guard for the "outcome honesty" law (CLAUDE.md, "Product
 * laws"): after an action, the product must tell the person what actually
 * happened. "The action failed" and "the action may have succeeded but I
 * lost track of it" are different facts and must never share one message.
 *
 * Three real instances motivated this file. Signup's handleSubmit
 * (frontend/app/signup/page.tsx) wraps `await signup(...)` AND a separate
 * `await awaitBrowserAuthReady(...)` in ONE try/catch, so a failure in the
 * second step — after the account was already created — renders "Signup
 * failed" on a request that succeeded. The workspace-invite accept flow
 * (frontend/lib/workspace/fleet/members-data.ts's old acceptWorkspaceInvite)
 * had the same shape one level down: a lost network response after the
 * server had already committed the accept came back as a flat "Could not
 * accept this invite" string, indistinguishable from a genuine rejection —
 * fixed in this same change by giving `AcceptInviteResult` a real
 * `ambiguous` discriminator and having the caller verify the true outcome
 * before ever showing failure (see frontend/app/join/[token]/page.tsx). A
 * channel group list previously rendered "no groups configured" on any read
 * failure, indistinguishable from genuine emptiness.
 *
 * A behavioural test cannot catch the NEXT instance of this shape — new
 * code behaves perfectly until the failure path actually fires, exactly
 * like every other drift test in this file's family (authorized-fetch-
 * drift.test.ts, safe-render-url.test.ts's SEAMS list,
 * test_unguarded_reply_paths.py, the exec-file-timeout ban). This is the
 * source-scan half: it looks for the literal shape of the bug rather than
 * exercising it.
 *
 * WHAT IT CATCHES — two independent shapes, both proven live (not
 * hypothetical) before this file existed:
 *
 * 1. A `try { ... } catch` (or `.catch(`) block whose try body `await`s two
 *    or more DIFFERENT async calls — where at least one callee name looks
 *    like a MUTATION (create/accept/join/save/submit/... — see
 *    MUTATION_NAME_PATTERN) — and whose catch body performs at most one
 *    observable state change (one `set<X>(...)` call, or none), with
 *    nothing distinguishing which of the two awaited steps actually failed.
 *    That is precisely the signup/invite-accept shape: a mutation that
 *    already happened, followed by a step that can independently fail,
 *    collapsed into one generic failure message.
 *
 * 2. A `try { ... } catch` block that performs the SAME navigation call
 *    (`window.location.replace/assign/href`, `router.push/replace`) in BOTH
 *    the try body (after at least one await) AND the catch body — i.e. code
 *    that proceeds identically whether the awaited step succeeded or
 *    failed. Found live 2026-08-14 in frontend/app/login/page.tsx:
 *    handleSubmit awaited a post-login session-readiness poll and called
 *    `window.location.replace(loginRedirectTarget)` on BOTH the success
 *    path and the catch — so a poll that failed after a genuinely
 *    successful login still navigated to a page that needs a working
 *    session, which then bounced the person straight back to a blank
 *    /login with nothing on screen ever saying why. This is the sharpest
 *    form of the law: not a wrong message, but literal silence, because the
 *    "handle the failure" branch and the "handle success" branch are the
 *    same line of code. Signup's OWN analogous poll (MAN-343, already
 *    fixed) legitimately proceeds on both paths — its destination
 *    (/verify-email) works without a live session — which is exactly why
 *    this is its own separate, narrower check rather than a blanket ban:
 *    the shape is only wrong when the destination NEEDS the thing the
 *    failed step was trying to confirm. A human reviews every hit, same as
 *    check 1 — see ALLOWLIST.
 *
 * WHAT IT DELIBERATELY CANNOT CATCH (do not extend this file to chase these
 * — they need a different tool, not a bigger regex):
 *   - An "empty state rendered on fetch error" bug that ISN'T inside a
 *     try/catch with 2+ awaits (e.g. a single try/catch that sets a list
 *     state to `[]` on failure with no separate error flag). That shape has
 *     a much higher false-positive rate against ordinary "nothing loaded
 *     yet" UI and needs a targeted, hand-reviewed sweep instead of a
 *     generic scan — see the sweep report this change shipped alongside.
 *   - Any bug expressed only in prose/copy (a message that LIES about
 *     what happened while the code path is structurally fine) — a source
 *     scan cannot read English.
 *   - Backend (Python) instances of the same shape — server_modules has its
 *     own structural-test idiom (AST-based, test_exception_and_task_lint.py
 *     etc.); a frontend text scanner is the wrong tool for that surface.
 *   - A try/catch where both awaited calls are read-only GETs feeding one
 *     display — deliberately not flagged (MUTATION_NAME_PATTERN requires at
 *     least one callee to look like a real side effect), because collapsing
 *     two failed reads into one message loses nothing that already
 *     happened.
 *
 * Run: npx tsx lib/workspace/outcome-honesty-drift.test.ts
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
// This file lives at lib/workspace/ — the frontend root is two levels up.
const FRONTEND_ROOT = join(HERE, "..", "..");
const SCAN_DIRS = ["lib", "app"];
const EXCLUDE_DIR_NAMES = new Set(["node_modules", ".next"]);

/**
 * A callee name is treated as "this step is a real mutation, not just a
 * read" if it contains one of these words (case-insensitive, word-ish
 * boundary via the surrounding regex). Deliberately broad on purpose: a
 * false positive here just means a real try/catch needs a documented
 * ALLOWLIST entry (cheap); a false negative means a genuine instance of
 * this bug ships silently (expensive). Extend this list rather than
 * fighting it if a real violation's callee name doesn't match.
 */
const MUTATION_NAME_PATTERN =
  /create|accept|join|decline|delete|remove|add|update|submit|save|signup|sign_up|register|provision|purchase|credit|invite|revoke|install|cancel|attach|assign|patch|write|send|configure|rotate|reset|grant|approve|deploy|commit|apply|toggle|enable|disable|move|archive|publish|pair|mutate|post|put/i;

/**
 * file path (relative to frontend/) -> reason it's allowed to have a
 * try/catch spanning 2+ distinct mutation-shaped awaits with a single
 * collapsed failure message. Every entry here is a DELIBERATE, reviewed
 * exception, not an oversight — the same allowlist-with-a-verdict idiom
 * authorized-fetch-drift.test.ts and safe-render-url.test.ts already use.
 */
const ALLOWLIST: Record<string, string> = {
  "app/signup/page.tsx":
    "MAN (fix/signup-false-failure-report, a separate in-flight branch): handleSubmit's `await signup(...)` + `await awaitBrowserAuthReady(...)` share one catch and report 'Signup failed.' even when the account was created. Known, tracked, being fixed on its own branch per the founder's explicit instruction not to touch this file from this change — do not remove this entry without confirming that branch merged AND the shape actually changed (this test will tell you: it goes stale-red if the violation disappears while still listed, see the stale-entry check below... except this scanner does not enforce staleness on purpose, see its own comment below).",
  "lib/workspace/fleet/FleetCreateAgentWizard.tsx":
    "Two real hits, both reviewed and judged lower-severity than the ones fixed in this same change: (1) submitPlacement's tail `await patchAgent(...); await hydrateCreatedAgent(...)` — hydrateCreatedAgent's OWN body is a self-contained try/catch that never rethrows (comment: 'Best-effort — ChannelsTab tolerates a null agent'), so the outer catch here can only ever fire from patchAgent, and 'Could not save placement.' stays accurate; this scanner can't see across a function boundary to know that. (2) submitBrain's BYOK branch — `POST /credentials/vault` then `POST /providers/profiles` then patchAgent: if the credential is created but the profile create fails, the credential is orphaned in the vault. Real but low blast radius (no duplicate REAL resource, no billing, no data loss — just DB clutter a retry adds one more of) compared to the agent-duplication and billed-droplet-duplication bugs this change fixes; flagged in the sweep report as a follow-up rather than fixed here.",
  "app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx":
    "handleAssign's `assignFleetTask` / `assignFleetTaskToUser` are mutually EXCLUSIVE if/else branches (agent vs human assignee) — exactly one ever runs per call, never both in sequence — so whichever one throws IS the accurate cause and 'Could not assign this task.' is not a collapse of two different facts. This scanner's 'awaits 2+ distinct calls' heuristic can't see that they're alternatives rather than sequential steps.",
  "app/(account)/w/[workspaceId]/projects/[projectId]/tasks/[taskId]/page.tsx":
    "Same shape and same reasoning as the projects/[projectId]/page.tsx entry above — handleAssign's assignFleetTask/assignFleetTaskToUser are mutually exclusive branches, not sequential steps.",
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

/** Strips // and /* comments and string/template literal CONTENTS (keeping
 *  delimiters) so brace-counting and regex matching below don't trip on
 *  braces or the word "await" appearing inside a comment or a string. Not a
 *  real lexer — good enough for this codebase's style, same trade-off
 *  authorized-fetch-drift.test.ts's line-based scan already makes. */
function stripCommentsAndStrings(source: string): string {
  let out = "";
  let i = 0;
  const n = source.length;
  while (i < n) {
    const two = source.slice(i, i + 2);
    if (two === "//") {
      while (i < n && source[i] !== "\n") i++;
      continue;
    }
    if (two === "/*") {
      const end = source.indexOf("*/", i + 2);
      i = end === -1 ? n : end + 2;
      continue;
    }
    const c = source[i];
    if (c === '"' || c === "'" || c === "`") {
      out += c;
      i++;
      while (i < n && source[i] !== c) {
        if (source[i] === "\\") i++;
        i++;
      }
      if (i < n) {
        out += c;
        i++;
      }
      continue;
    }
    out += c;
    i++;
  }
  return out;
}

type TryCatchBlock = { tryBody: string; catchBody: string; startIndex: number; endIndex: number };

/** Finds every `try { ... } catch (...) { ... }` / `try { ... } catch { ... }`
 *  block in a (comment/string-stripped) source string, via brace-depth
 *  matching — this repo's frontend has no TypeScript-AST-based test today
 *  (verified: `grep -rl "from \"typescript\"" lib` is empty), so this
 *  follows the established plain-text-scan convention rather than adding a
 *  new parsing dependency for one check. */
function findTryCatchBlocks(source: string): TryCatchBlock[] {
  const blocks: TryCatchBlock[] = [];
  const tryRe = /\btry\s*\{/g;
  let m: RegExpExecArray | null;
  while ((m = tryRe.exec(source))) {
    const tryBraceStart = m.index + m[0].length - 1;
    const tryBodyStart = tryBraceStart + 1;
    let depth = 1;
    let j = tryBodyStart;
    while (j < source.length && depth > 0) {
      if (source[j] === "{") depth++;
      else if (source[j] === "}") depth--;
      j++;
    }
    if (depth !== 0) continue; // unbalanced — skip rather than misreport
    const tryBodyEnd = j - 1;
    const tryBody = source.slice(tryBodyStart, tryBodyEnd);

    // After the try block's closing brace, expect (whitespace) catch
    // (optional (ident)) {
    const catchMatch = /^\s*catch(\s*\([^)]*\))?\s*\{/.exec(source.slice(j));
    if (!catchMatch) continue; // try/finally with no catch — not this shape
    const catchBraceStart = j + catchMatch[0].length - 1;
    const catchBodyStart = catchBraceStart + 1;
    depth = 1;
    let k = catchBodyStart;
    while (k < source.length && depth > 0) {
      if (source[k] === "{") depth++;
      else if (source[k] === "}") depth--;
      k++;
    }
    if (depth !== 0) continue;
    const catchBody = source.slice(catchBodyStart, k - 1);

    blocks.push({ tryBody, catchBody, startIndex: m.index, endIndex: k });
  }
  return blocks;
}

/** Blanks out every NESTED `try { ... } catch ... { ... }` span inside a
 *  block body (keeping newlines, so nothing downstream misreads line
 *  numbers) before counting awaits. A nested try/catch that swallows its
 *  own error already stops it from ever reaching the OUTER catch — that is
 *  exactly the correct, exemplary shape TaskComposer.tsx's `create()` uses
 *  (createFleetTask in the outer try; patchFleetTask/attachFleetTaskLabel/
 *  assignFleetTask* each individually try/catch'd inline, collecting a
 *  `problems` list and reporting "Task created, but X did not land" rather
 *  than a flat failure) — without this, the scan would flag that function
 *  as a violation when it is actually the reference pattern this whole
 *  check exists to require elsewhere. Only strips try/catch (not bare
 *  try/finally, which has no catch of its own and genuinely does let an
 *  inner await's rejection reach the outer catch unmodified — those stay
 *  counted). */
function stripNestedTryCatchBlocks(body: string): string {
  let out = body;
  // Repeat: stripping the innermost nested blocks first can reveal further
  // nesting the single pass's regex/brace-walk already skipped over inside
  // what it treated as one blanked span. A handful of passes is plenty for
  // any real nesting depth this codebase uses; a fixed cap avoids ever
  // looping on unexpected input.
  for (let pass = 0; pass < 5; pass++) {
    const nested = findTryCatchBlocks(out);
    if (nested.length === 0) break;
    // Blank from `try` through the catch block's closing `}` inclusive.
    let next = "";
    let cursor = 0;
    for (const block of nested) {
      if (block.startIndex < cursor) continue; // overlapping match — already covered
      next += out.slice(cursor, block.startIndex);
      next += out.slice(block.startIndex, block.endIndex).replace(/[^\n]/g, " ");
      cursor = block.endIndex;
    }
    next += out.slice(cursor);
    out = next;
  }
  return out;
}

/** Distinct top-level-ish `await <callee>(` names inside a block body —
 *  `<callee>` is the identifier or property-access chain immediately before
 *  the `(`, e.g. `await signup(...)` -> "signup",
 *  `await api.workspaces.create(...)` -> "api.workspaces.create". */
function awaitedCalleeNames(body: string): string[] {
  const re = /\bawait\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(/g;
  const names = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = re.exec(body))) {
    names.add(m[1]);
  }
  return [...names];
}

/** Distinct `set<Ident>(` state-setter invocations in a block body — the
 *  useState convention this whole codebase uses (setStatus, setError, ...).
 *  Counts DISTINCT setter names, not call count, so `setError(a)` followed
 *  later by `setError(b)` in the same catch still reads as "one signal",
 *  while `setError(a); setAmbiguous(b);` reads as "two signals" (a real
 *  distinguishing catch, like this change's own join/[token]/page.tsx). */
function distinctStateSetterNames(body: string): string[] {
  const re = /\bset[A-Z]\w*\s*\(/g;
  const names = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = re.exec(body))) {
    names.add(m[0]);
  }
  return [...names];
}

/** A "proceed regardless" navigation — the exact call shape login/page.tsx's
 *  handleSubmit had in both its try body and its catch body. Deliberately
 *  narrow to actual navigation (never just any duplicated call), because a
 *  navigation duplicated across success/failure is what turns a failure
 *  into literal silence: the person's screen changes (or doesn't, if the
 *  destination bounces them right back) with no error ever rendered. */
const NAVIGATION_CALL_PATTERN = /\b(?:window\.location\.(?:replace|assign|href)|router\.(?:push|replace))\s*[(=]/;

function hasNavigationCall(body: string): boolean {
  return NAVIGATION_CALL_PATTERN.test(body);
}

type ScanHit =
  | { kind: "collapsed-catch"; file: string; line: number; calleeNames: string[] }
  | { kind: "blind-navigation"; file: string; line: number };

function scanFile(absPath: string, relPath: string): ScanHit[] {
  const raw = readFileSync(absPath, "utf8");
  const source = stripCommentsAndStrings(raw);
  const hits: ScanHit[] = [];

  for (const block of findTryCatchBlocks(source)) {
    const strippedTryBody = stripNestedTryCatchBlocks(block.tryBody);
    // Line number from the STRIPPED source, not `raw`: stripCommentsAndStrings
    // deletes comment/string-body characters (shrinking absolute offsets)
    // but never deletes a newline, so a newline COUNT up to a given index is
    // preserved between the two — slicing `raw` with an index computed
    // against the shorter `source` silently undercounted the line number
    // whenever a file had comments before the match (caught by this scan
    // itself reporting task-labels.tsx:124/144 for a try/catch that is
    // actually ~150 lines further down — every comment before it shifted
    // the reported line number backward by roughly its own line count).
    const line = source.slice(0, block.startIndex).split("\n").length;

    const callees = awaitedCalleeNames(strippedTryBody);
    if (callees.length >= 2) {
      const hasMutation = callees.some((name) => MUTATION_NAME_PATTERN.test(name));
      const setters = distinctStateSetterNames(block.catchBody);
      if (hasMutation && setters.length <= 1) {
        hits.push({ kind: "collapsed-catch", file: relPath, line, calleeNames: callees });
      }
    }

    // Only flag when the catch navigates AND says nothing else first (zero
    // `set<X>(` calls in its body) — a catch that shows a real message
    // before moving on (frontend/app/auth/complete/page.tsx's own
    // finalizeAuth: setError(...), THEN a delayed redirect carrying the
    // error forward as a query param) has already told the person
    // something true and is not this bug, even though it also navigates.
    if (
      /\bawait\b/.test(strippedTryBody) &&
      hasNavigationCall(strippedTryBody) &&
      hasNavigationCall(block.catchBody) &&
      distinctStateSetterNames(block.catchBody).length === 0
    ) {
      hits.push({ kind: "blind-navigation", file: relPath, line });
    }
  }
  return hits;
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

    const hits = scanFile(absPath, relPath);
    if (hits.length === 0) continue;

    if (relPath in ALLOWLIST) {
      seenAllowlistEntries.add(relPath);
      continue;
    }

    violations.push(...hits);
  }

  const collapsedCatchHits = violations.filter((v) => v.kind === "collapsed-catch");
  const blindNavigationHits = violations.filter((v) => v.kind === "blind-navigation");

  assert(
    collapsedCatchHits.length === 0,
    collapsedCatchHits.length === 0
      ? "no try/catch collapses a mutation + a follow-up step into one undifferentiated failure"
      : `${collapsedCatchHits.length} try/catch block(s) await 2+ distinct calls (at least one mutation-shaped) but the catch cannot tell which failed:\n` +
          collapsedCatchHits
            .map((v) => `    ${v.file}:${v.line}: awaits [${v.calleeNames.join(", ")}]`)
            .join("\n") +
          "\n  Either give the catch a way to distinguish outcomes (a second state signal, or verify the real result before reporting failure — see frontend/app/join/[token]/page.tsx for the pattern this change added), split the try/catch per step, or add a reasoned entry to ALLOWLIST in this test.",
  );

  assert(
    blindNavigationHits.length === 0,
    blindNavigationHits.length === 0
      ? "no try/catch navigates identically whether the awaited step succeeded or failed"
      : `${blindNavigationHits.length} try/catch block(s) call the same navigation on both the success path and the catch:\n` +
          blindNavigationHits.map((v) => `    ${v.file}:${v.line}`).join("\n") +
          "\n  A step worth awaiting is worth telling the person about if it fails — see frontend/app/login/page.tsx's handleSubmit for the fix (stay on the page and say so, rather than navigating into a destination that needs the very thing that failed to be confirmed), or add a reasoned entry to ALLOWLIST if the destination genuinely does not need what failed (signup/page.tsx's analogous poll -> /verify-email is the reference case for when proceeding regardless is correct).",
  );

  // Unlike authorized-fetch-drift.test.ts, this allowlist is NOT checked for
  // staleness. Its entries are two different KINDS, and neither wants an
  // auto-prune: signup/page.tsx is a KNOWN, ALREADY-DIAGNOSED bug on someone
  // else's in-flight branch — auto-removing it the moment the shape changes
  // would silently stop covering that file the next time someone edits it,
  // before the other branch's fix has actually landed on this one. The other
  // three (FleetCreateAgentWizard.tsx, and the two assignFleetTask/
  // assignFleetTaskToUser mutually-exclusive-branch entries) are REASONED
  // FALSE POSITIVES of this scanner's own heuristic — cross-function
  // reasoning and if/else-branch exclusivity it structurally cannot see —
  // not bugs expected to be fixed away, so there is no "stale" state for
  // them to reach. Prefer fixing entries off this list to adding new ones;
  // when a real fix lands (signup) or the code changes shape enough that the
  // reasoning no longer applies (the other three), remove the entry and let
  // this test's own green run be the proof.

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

main();
