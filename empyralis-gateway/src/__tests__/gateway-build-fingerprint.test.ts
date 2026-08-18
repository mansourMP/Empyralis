import test from "node:test";
import assert from "node:assert/strict";
import { promises as fs } from "fs";
import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";

import {
  computeGatewayBuildFingerprint,
  isGatewayBuildFingerprint,
  resolveRunningGatewayDistDir,
} from "../update/gateway-build-fingerprint";

/**
 * The properties that make this usable as a staleness signal, each pinned
 * separately. The one that matters most is AGREEMENT: two boxes that built
 * the same commit at different moments must produce the same fingerprint, or
 * the whole comparison is noise and every box looks permanently drifted.
 */

async function withTmpDir<T>(prefix: string, fn: (dir: string) => Promise<T>): Promise<T> {
  const dir = await mkdtemp(path.join(tmpdir(), prefix));
  try {
    return await fn(dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

async function writeBuild(dir: string, files: Record<string, string>): Promise<void> {
  for (const [relative, contents] of Object.entries(files)) {
    const absolute = path.join(dir, relative);
    await fs.mkdir(path.dirname(absolute), { recursive: true });
    await fs.writeFile(absolute, contents, "utf8");
  }
}

function fingerprintOf(outcome: Awaited<ReturnType<typeof computeGatewayBuildFingerprint>>): string {
  assert.ok(isGatewayBuildFingerprint(outcome), "expected a computed fingerprint");
  return outcome.fingerprint;
}

test("identical content produces an identical fingerprint in two separate trees", async () => {
  // The property the VERSION string has and that made it useless is the
  // opposite of this: 0.1.0 matches everywhere including across genuinely
  // different builds. Here, agreement must mean the bytes agree.
  const build = {
    "index.js": "console.log('a');",
    "openclaw/provisioning/plan.js": "module.exports = {};",
  };
  await withTmpDir("fp-a-", async (left) => {
    await withTmpDir("fp-b-", async (right) => {
      await writeBuild(left, build);
      // Written later, so mtimes differ — a timestamp in the digest would
      // make every box unique and the comparison worthless.
      await new Promise((resolve) => setTimeout(resolve, 15));
      await writeBuild(right, build);
      assert.equal(
        fingerprintOf(await computeGatewayBuildFingerprint(left)),
        fingerprintOf(await computeGatewayBuildFingerprint(right)),
      );
    });
  });
});

test("a one-character change to one file changes the fingerprint", async () => {
  await withTmpDir("fp-content-", async (dir) => {
    await writeBuild(dir, { "index.js": "console.log('a');" });
    const before = fingerprintOf(await computeGatewayBuildFingerprint(dir));
    await writeBuild(dir, { "index.js": "console.log('b');" });
    assert.notEqual(before, fingerprintOf(await computeGatewayBuildFingerprint(dir)));
  });
});

test("a MISSING feature directory changes the fingerprint — the 2026-08-18 incident", async () => {
  // Production ran a dist/ with no dist/openclaw at all: it advertised zero
  // OpenClaw channels while the source had carried 24 for weeks, and both
  // builds called themselves 0.1.0. This is the case the version string
  // structurally could not see.
  await withTmpDir("fp-partial-", async (dir) => {
    await writeBuild(dir, {
      "index.js": "console.log('a');",
      "openclaw/provisioning/plan.js": "module.exports = {};",
    });
    const complete = await computeGatewayBuildFingerprint(dir);
    await rm(path.join(dir, "openclaw"), { recursive: true, force: true });
    const partial = await computeGatewayBuildFingerprint(dir);
    assert.notEqual(fingerprintOf(complete), fingerprintOf(partial));
    assert.ok(isGatewayBuildFingerprint(complete) && isGatewayBuildFingerprint(partial));
    assert.equal(complete.fileCount, 2);
    // fileCount is what tells a partial build from a merely different one.
    assert.equal(partial.fileCount, 1);
  });
});

test("moving a file without editing it changes the fingerprint", async () => {
  // Imports resolve by path, so a relocated file is a different build even
  // though every byte of content is identical.
  await withTmpDir("fp-path-", async (dir) => {
    await writeBuild(dir, { "a/mod.js": "module.exports = 1;" });
    const before = fingerprintOf(await computeGatewayBuildFingerprint(dir));
    await rm(path.join(dir, "a"), { recursive: true, force: true });
    await writeBuild(dir, { "b/mod.js": "module.exports = 1;" });
    assert.notEqual(before, fingerprintOf(await computeGatewayBuildFingerprint(dir)));
  });
});

test("node_modules is excluded, so a dependency tree cannot swamp the signal", async () => {
  await withTmpDir("fp-nm-", async (dir) => {
    await writeBuild(dir, { "index.js": "console.log('a');" });
    const before = await computeGatewayBuildFingerprint(dir);
    await writeBuild(dir, { "node_modules/left-pad/index.js": "module.exports = 1;" });
    const after = await computeGatewayBuildFingerprint(dir);
    assert.equal(fingerprintOf(before), fingerprintOf(after));
    assert.ok(isGatewayBuildFingerprint(after));
    assert.equal(after.fileCount, 1);
  });
});

test("a missing or empty dist reports a reason instead of throwing", async () => {
  // A box that cannot fingerprint itself must still boot and serve every
  // capability — losing the signal is a degradation, never a reason to take
  // a working gateway off the air.
  const absent = await computeGatewayBuildFingerprint(
    path.join(tmpdir(), "definitely-not-a-real-dist-dir-empyralis"),
  );
  assert.equal(isGatewayBuildFingerprint(absent), false);
  assert.ok(!isGatewayBuildFingerprint(absent) && absent.reason.length > 0);

  await withTmpDir("fp-empty-", async (dir) => {
    const empty = await computeGatewayBuildFingerprint(dir);
    assert.equal(isGatewayBuildFingerprint(empty), false);
    assert.ok(!isGatewayBuildFingerprint(empty) && empty.reason.includes("no compiled files"));
  });
});

test("the running dist dir is derived from this module's own location", async () => {
  // Deliberately not from config: a configured path can name an install root
  // this process never loaded a byte from, which is the exact production
  // failure mode (a launcher pointed at one root while a newer build sat
  // unused in another).
  const resolved = resolveRunningGatewayDistDir("/opt/empyralis/current/gateway/dist/update");
  assert.equal(resolved, "/opt/empyralis/current/gateway/dist");
});
