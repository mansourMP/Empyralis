import test from "node:test";
import assert from "node:assert/strict";
import { promises as fsp } from "fs";
import os from "os";
import path from "path";

import {
  ensureGatewayLaunchRepair,
  renderGatewayLaunchScript,
  shellSingleQuote,
} from "../update/gateway-launch-repair";

/**
 * The launcher this writes is the thing an operator is about to put in an
 * `ExecStart=` on a machine nobody can SSH into. So these tests RUN it — for
 * real, through /bin/sh — rather than asserting on its text. A script that
 * reads correctly and execs the wrong thing is exactly the failure that
 * cannot be recovered from.
 */

async function withTempDir<T>(fn: (dir: string) => Promise<T>): Promise<T> {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), "gw-launch-repair-"));
  try {
    return await fn(dir);
  } finally {
    await fsp.rm(dir, { recursive: true, force: true });
  }
}

test("with NO staged release, the launcher starts exactly the entrypoint running today", async (t) => {
  if (process.platform === "win32") {
    t.skip("POSIX launcher");
    return;
  }
  await withTempDir(async (dir) => {
    const stateDir = path.join(dir, "state");
    const runningEntry = path.join(dir, "opt", "empyralis-gateway", "dist", "index.js");
    await fsp.mkdir(path.dirname(runningEntry), { recursive: true });
    await fsp.writeFile(runningEntry, "// the build running right now\n");

    const plan = await ensureGatewayLaunchRepair({ stateDir, runningEntryPath: runningEntry, env: {} });
    assert.ok(plan);
    assert.equal(plan.verified, true, plan.failureReason ?? "");
    assert.equal(plan.failureReason, null);
    assert.equal(plan.execStartLine, `ExecStart=${plan.launcherPath}`);

    // The probe answers with what it WOULD exec — and with an empty layout
    // that must be today's path, or the operator's edit takes the box down.
    const { execFileWithTimeout } = await import("../shell/exec-file-with-timeout");
    const probe = await execFileWithTimeout(plan.launcherPath, [], 5_000, {
      env: { ...process.env, EMPYRALIS_GATEWAY_LAUNCH_PROBE: "1" },
    });
    assert.equal(probe.exitCode, 0);
    assert.equal(probe.stdout.trim(), runningEntry);
  });
});

test("once a release IS staged, the same launcher switches to it with no further edit", async (t) => {
  if (process.platform === "win32") {
    t.skip("POSIX launcher");
    return;
  }
  await withTempDir(async (dir) => {
    const stateDir = path.join(dir, "state");
    const runningEntry = path.join(dir, "opt", "empyralis-gateway", "dist", "index.js");
    await fsp.mkdir(path.dirname(runningEntry), { recursive: true });
    await fsp.writeFile(runningEntry, "// old\n");

    const plan = await ensureGatewayLaunchRepair({ stateDir, runningEntryPath: runningEntry, env: {} });
    assert.ok(plan?.verified);

    // Exactly where a self-update stages and swaps: <dirname(stateDir)>/
    // gateway-releases/current -> releases/<v>.
    const layoutRoot = path.join(dir, "gateway-releases");
    const releaseDir = path.join(layoutRoot, "releases", "0.2.0");
    const releaseEntry = path.join(releaseDir, "gateway", "dist", "index.js");
    await fsp.mkdir(path.dirname(releaseEntry), { recursive: true });
    await fsp.writeFile(releaseEntry, "// new\n");
    await fsp.mkdir(layoutRoot, { recursive: true });
    await fsp.symlink(releaseDir, path.join(layoutRoot, "current"));

    const { execFileWithTimeout } = await import("../shell/exec-file-with-timeout");
    const probe = await execFileWithTimeout(plan.launcherPath, [], 5_000, {
      env: { ...process.env, EMPYRALIS_GATEWAY_LAUNCH_PROBE: "1" },
    });
    assert.equal(probe.exitCode, 0);
    assert.equal(
      probe.stdout.trim(),
      path.join(layoutRoot, "current", "gateway", "dist", "index.js"),
      "a swapped `current` must be picked up with no second operator action, ever",
    );
  });
});

test("a launcher that cannot be run is reported UNVERIFIED, never recommended", async () => {
  await withTempDir(async (dir) => {
    const stateDir = path.join(dir, "state");
    const plan = await ensureGatewayLaunchRepair({
      stateDir,
      runningEntryPath: path.join(dir, "index.js"),
      env: {},
      probe: async () => {
        throw new Error("noexec");
      },
    });
    assert.ok(plan);
    assert.equal(plan.verified, false);
    assert.match(String(plan.failureReason), /noexec/);
  });
});

test("a probe that answers nothing is unverified too — silence is not success", async () => {
  await withTempDir(async (dir) => {
    const plan = await ensureGatewayLaunchRepair({
      stateDir: path.join(dir, "state"),
      runningEntryPath: path.join(dir, "index.js"),
      env: {},
      probe: async () => "",
    });
    assert.ok(plan);
    assert.equal(plan.verified, false);
    assert.ok(plan.failureReason);
  });
});

test("a write failure is reported, never thrown — a gateway that cannot prepare its repair still runs", async () => {
  const plan = await ensureGatewayLaunchRepair({
    stateDir: "/state",
    runningEntryPath: "/index.js",
    env: {},
    mkdir: async () => {
      const error = new Error("EACCES: permission denied") as NodeJS.ErrnoException;
      error.code = "EACCES";
      throw error;
    },
    probe: async () => {
      throw new Error("must never be reached");
    },
  });
  assert.ok(plan);
  assert.equal(plan.verified, false);
  assert.match(String(plan.failureReason), /permission denied/);
});

test("nothing is written at all without both a state dir and a running entrypoint", async () => {
  assert.equal(await ensureGatewayLaunchRepair({ stateDir: "", runningEntryPath: "/a.js" }), null);
  assert.equal(await ensureGatewayLaunchRepair({ stateDir: "/s", runningEntryPath: "" }), null);
});

test("a path holding a quote or a space cannot break out of the generated script", async (t) => {
  if (process.platform === "win32") {
    t.skip("POSIX launcher");
    return;
  }
  assert.equal(shellSingleQuote("/a/b"), "'/a/b'");
  assert.equal(shellSingleQuote("/a'b"), `'/a'\\''b'`);

  await withTempDir(async (dir) => {
    const nasty = path.join(dir, "we ird's dir");
    const runningEntry = path.join(nasty, "index.js");
    await fsp.mkdir(nasty, { recursive: true });
    await fsp.writeFile(runningEntry, "// running\n");
    const plan = await ensureGatewayLaunchRepair({
      stateDir: path.join(dir, "state"),
      runningEntryPath: runningEntry,
      env: {},
    });
    assert.ok(plan);
    assert.equal(plan.verified, true, plan.failureReason ?? "");

    const { execFileWithTimeout } = await import("../shell/exec-file-with-timeout");
    const probe = await execFileWithTimeout(plan.launcherPath, [], 5_000, {
      env: { ...process.env, EMPYRALIS_GATEWAY_LAUNCH_PROBE: "1" },
    });
    assert.equal(probe.stdout.trim(), runningEntry);
  });
});

test("the script is /bin/sh, so it does not depend on bash existing on a minimal image", () => {
  const script = renderGatewayLaunchScript({
    layoutEntrypoint: "/layout/current/gateway/dist/index.js",
    fallbackEntrypoint: "/opt/app/dist/index.js",
    nodePath: "/usr/bin/node",
  });
  assert.match(script, /^#!\/bin\/sh\n/);
});
