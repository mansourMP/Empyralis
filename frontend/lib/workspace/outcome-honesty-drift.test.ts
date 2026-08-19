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
 * THE FIX, not just the catch: `frontend/lib/workspace/mutation-outcome.ts`
 * holds `runMutationWithBestEffortRefresh` (shape 1's majority case — one
 * mutation, one best-effort follow-up refresh) and `MutateNetworkError`
 * (written independently twice before that file existed — members-data.ts's
 * acceptWorkspaceInvite and cloud-vps-setup-panel.tsx's createServer() —
 * which is the actual argument for a shared primitive over a third
 * hand-rolled copy). Both are named directly in this file's own failure
 * message below, because a guard that only catches a mistake is worth
 * less than one that also points at the fix.
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
  "app/(account)/w/[workspaceId]/projects/[projectId]/page.tsx":
    "handleAssign's `assignFleetTask` / `assignFleetTaskToUser` are mutually EXCLUSIVE if/else branches (agent vs human assignee) — exactly one ever runs per call, never both in sequence — so whichever one throws IS the accurate cause and 'Could not assign this task.' is not a collapse of two different facts. This scanner's 'awaits 2+ distinct calls' heuristic can't see that they're alternatives rather than sequential steps.",
  "app/(account)/w/[workspaceId]/projects/[projectId]/tasks/[taskId]/page.tsx":
    "Same shape and same reasoning as the projects/[projectId]/page.tsx entry above — handleAssign's assignFleetTask/assignFleetTaskToUser are mutually exclusive branches, not sequential steps.",
  "app/(account)/w/[workspaceId]/my-work/page.tsx":
    "Third instance of the same shape, same reasoning — My work reassigns a task through the identical mutually exclusive assignFleetTask/assignFleetTaskToUser if/else, copied deliberately from the project board's own handleAssign rather than invented (including the part that matters here: the refresh() is OUTSIDE the try, so a failed GET can neither overwrite a real wake-status notice nor report 'could not assign' over an assignment that already committed).",
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

/** Blanks out the BODY of every nested arrow-function/function-expression
 *  block (`=> { ... }`, `function (...) { ... }`, `async function (...) {
 *  ... }`) inside a block body — keeping the function's own signature and
 *  newlines intact, only hollowing out what runs inside it.
 *
 *  Awaits inside a callback passed as an ARGUMENT to another call are not
 *  sequential steps of the OUTER try body the way two directly-sequential
 *  `await` statements are — the callback's own execution is scheduled by
 *  whatever it was handed to, not by "the next line of this try block".
 *  Without this, adopting runMutationWithBestEffortRefresh(mutate, refresh)
 *  itself trips this scanner: `await runMutationWithBestEffortRefresh(async
 *  () => { await createWorkspaceInvite(...); ... }, refresh)` reads, to a
 *  naive text scan, as TWO sequential awaited calls at the try body's own
 *  level (runMutationWithBestEffortRefresh, then createWorkspaceInvite) —
 *  which would make adopting the sweep's own recommended fix the thing
 *  that fails its own guard. Measured directly: this was found by running
 *  the scanner against this session's OWN adoption commits.
 *
 *  Same brace-matching approach as stripNestedTryCatchBlocks, and the same
 *  "not every case" caveat: an arrow function with an EXPRESSION body
 *  (`() => someExpr`, no braces) has nothing to blank here, but such a body
 *  cannot itself contain a sequential `await` statement in the shape this
 *  check cares about either — only a block body can. */
function stripNestedFunctionExpressionBodies(body: string): string {
  let out = body;
  for (let pass = 0; pass < 5; pass++) {
    const opens: number[] = [];
    const re = /(?:=>\s*\{|\bfunction\b[^{(]*\([^)]*\)\s*\{)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(out))) {
      opens.push(m.index + m[0].length - 1); // index of the opening `{`
    }
    if (opens.length === 0) break;

    let next = "";
    let cursor = 0;
    for (const braceStart of opens) {
      if (braceStart < cursor) continue; // inside a span already blanked this pass
      let depth = 1;
      let j = braceStart + 1;
      while (j < out.length && depth > 0) {
        if (out[j] === "{") depth++;
        else if (out[j] === "}") depth--;
        j++;
      }
      if (depth !== 0) continue; // unbalanced — skip rather than misreport
      next += out.slice(cursor, braceStart + 1);
      next += out.slice(braceStart + 1, j - 1).replace(/[^\n]/g, " ");
      cursor = j - 1;
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

/** The scan logic proper, factored out of scanFile so the exact same code
 *  path can be driven against an in-memory fixture string (see
 *  runFixtureChecks below) as well as a real file on disk — the same
 *  "the detector must be provable in memory" discipline the Python sibling
 *  (test_exception_and_task_lint.py's TestFixtureDetection) already uses. */
function scanSource(raw: string, relPath: string): ScanHit[] {
  const source = stripCommentsAndStrings(raw);
  const hits: ScanHit[] = [];

  for (const block of findTryCatchBlocks(source)) {
    const strippedTryBody = stripNestedFunctionExpressionBodies(stripNestedTryCatchBlocks(block.tryBody));
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

function scanFile(absPath: string, relPath: string): ScanHit[] {
  return scanSource(readFileSync(absPath, "utf8"), relPath);
}

/** In-memory proofs of the detector itself — the frontend counterpart to
 *  test_exception_and_task_lint.py's TestFixtureDetection class. Run before
 *  the real file scan so a detector regression fails loudly here rather
 *  than as a confusing false positive/negative buried in a 20-file scan. */
function runFixtureChecks(): void {
  const flagged = (source: string) =>
    scanSource(source, "fixture.ts").some((h) => h.kind === "collapsed-catch");

  assert(
    flagged(
      "async function f() {\n" +
        "  try {\n" +
        "    await createThing();\n" +
        "    await notifyThingCreated();\n" +
        "  } catch (e) {\n" +
        "    setError(e instanceof Error ? e.message : 'failed');\n" +
        "  }\n" +
        "}\n",
    ),
    "fixture: flags two distinct mutation-shaped awaits collapsed into one catch",
  );

  assert(
    !flagged(
      "async function f() {\n" +
        "  try {\n" +
        "    await runMutationWithBestEffortRefresh(async () => {\n" +
        "      const created = await createWorkspaceInvite(workspaceId, email, role);\n" +
        "      setFreshLink(created.token);\n" +
        "    }, refreshInvites);\n" +
        "  } catch (e) {\n" +
        "    setInviteError(e instanceof Error ? e.message : 'Could not create this invite.');\n" +
        "  }\n" +
        "}\n",
    ),
    "fixture: adopting runMutationWithBestEffortRefresh must not itself trip the guard " +
      "(the callback's own await must not read as a second sequential step of the outer try)",
  );

  assert(
    flagged(
      "async function f() {\n" +
        "  try {\n" +
        "    await createThing();\n" +
        "    somePromise.then(async function () {\n" +
        "      await someUnrelatedCallbackWork();\n" +
        "    });\n" +
        "    await notifyThingCreated();\n" +
        "  } catch (e) {\n" +
        "    setError(e instanceof Error ? e.message : 'failed');\n" +
        "  }\n" +
        "}\n",
    ),
    "fixture: a real second sequential mutation OUTSIDE any callback still flags, " +
      "even when an unrelated callback sits between them",
  );
}

function main(): void {
  runFixtureChecks();

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
          "\n  FIRST thing to reach for: if this is one mutation followed by a plain refresh whose own failure should never read as the mutation failing, wrap it in runMutationWithBestEffortRefresh (frontend/lib/workspace/mutation-outcome.ts) — most sites are exactly this shape, and it is a one-line change around the existing try/catch (see ChannelPairingSection.tsx / MembersSection.tsx / ProjectMemberAdd.tsx / McpServersSection.tsx / OpenClawChannelsPanel.tsx / NewWorkspacePageClient.tsx for real adoptions). If the mutation's own outcome can be genuinely UNKNOWN (a fetch() that never produced a response), reach for the shared MutateNetworkError class in that same file rather than re-deriving it — never adopt either mechanically without checking the fit: a follow-up failure that must be SURFACED (not silently logged) with distinguishing wording, two or more independent follow-ups, or a sequence of 2+ real mutations each needing their own message are all shapes the helper does not fit — split the try/catch by hand instead, or verify the real result before reporting failure (frontend/app/join/[token]/page.tsx). Whatever you do, add a reasoned entry to ALLOWLIST in this test only as a last resort.",
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
  // three (the assignFleetTask/assignFleetTaskToUser mutually-exclusive-
  // branch entries — projects/[projectId], tasks/[taskId], my-work) are
  // REASONED FALSE POSITIVES of this scanner's own heuristic — if/else-branch
  // exclusivity it structurally cannot see — not bugs expected to be fixed
  // away, so there is no "stale" state for them to reach. (A fourth entry of
  // this second kind, FleetCreateAgentWizard.tsx's cross-function-reasoning
  // false positive, was removed 2026-08-19 when that whole file was deleted
  // — the zero-decision agent create that replaced it, agent-quick-create.ts,
  // has no multi-step try/catch of this shape at all.) Prefer fixing entries
  // off this list to adding new ones; when a real fix lands (signup) or the
  // code changes shape enough that the reasoning no longer applies (the
  // other three), remove the entry and let this test's own green run be the
  // proof.

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

main();
