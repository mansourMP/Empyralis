import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import path from "path";

/**
 * MAN-355 wiring assertion.
 *
 * The launch-path resolver is only worth anything if the code that WRITES a
 * supervisor unit actually calls it. A behavioural test cannot cover this:
 * `defaultAuditSupervisorInstall` is module-private and reads
 * `require.main.filename`, so passing the raw running path back in would
 * type-check, behave identically on a box that already runs from the release
 * layout, and silently re-pin a fixed checkout on every box that does not —
 * which is exactly the bug (a gateway writes its own un-updatable path back
 * into its own plist, forever).
 *
 * This is the same structural-guard idiom this codebase already uses for
 * seams a behavioural test structurally cannot reach.
 */

/** Locate the real `src` tree whether this runs from `dist/__tests__` (the
 *  `npm test` path) or from `src/__tests__`. Deliberately explicit: a sibling
 *  drift test in this directory resolves `path.resolve(__dirname, "..")` and
 *  then filters for `.ts` files, which — running from `dist/__tests__` —
 *  walks `dist/` and matches nothing, so it enforces nothing at all. */
function resolveSrcDir(): string {
  const candidates = [
    path.resolve(__dirname, "..", "..", "src"),
    path.resolve(__dirname, ".."),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, "health", "gateway-doctor.ts"))) {
      return candidate;
    }
  }
  throw new Error(
    `could not locate the gateway src tree from ${__dirname} — tried ${candidates.join(", ")}`,
  );
}

const SRC_DIR = resolveSrcDir();
const DOCTOR_PATH = path.join(SRC_DIR, "health", "gateway-doctor.ts");

test("CANARY: the doctor source is actually being read, so the assertions below cannot pass vacuously", () => {
  const source = fs.readFileSync(DOCTOR_PATH, "utf8");
  assert.ok(source.length > 1000, "gateway-doctor.ts source looks empty or truncated");
  assert.match(
    source,
    /function defaultAuditSupervisorInstall\(/,
    "the function this test reasons about no longer exists under that name — re-point this assertion rather than deleting it",
  );
});

test("the supervisor unit writer resolves its entry path through the release layout, not the running path", () => {
  const source = fs.readFileSync(DOCTOR_PATH, "utf8");

  assert.match(
    source,
    /import \{ resolveGatewayLaunchEntrypoint \} from "\.\.\/update\/gateway-launch-path"/,
    "gateway-doctor.ts must import the shared launch-path resolver",
  );

  const body = source.slice(source.indexOf("function defaultAuditSupervisorInstall("));
  const entryAssignment = body.match(/const entryPath = ([^;]+);/);
  assert.ok(entryAssignment, "defaultAuditSupervisorInstall no longer assigns entryPath");

  // The regression, stated precisely: assigning defaultEntryPath() here is
  // what pinned a fixed checkout into the unit forever.
  assert.doesNotMatch(
    entryAssignment[1],
    /^\s*defaultEntryPath\(\)/,
    "defaultAuditSupervisorInstall must not pin the running entry path directly (MAN-355) — route it through resolveGatewayLaunchEntrypoint",
  );
  assert.match(
    entryAssignment[1],
    /defaultSupervisorEntryPath\(/,
    "defaultAuditSupervisorInstall must resolve its entry path through the layout-preferring helper",
  );
});

test("the layout-preferring helper is the only thing that feeds the unit writer, and it takes the state dir", () => {
  const source = fs.readFileSync(DOCTOR_PATH, "utf8");
  const helper = source.match(
    /function defaultSupervisorEntryPath\([\s\S]*?\n\}/,
  );
  assert.ok(helper, "defaultSupervisorEntryPath is missing");
  assert.match(
    helper[0],
    /resolveGatewayLaunchEntrypoint\(\{[\s\S]*runningEntryPath:\s*defaultEntryPath\(\)[\s\S]*stateDir[\s\S]*\}\)/,
    "the helper must pass BOTH the running path (as the fallback) and the state dir (which resolves the layout)",
  );
});
