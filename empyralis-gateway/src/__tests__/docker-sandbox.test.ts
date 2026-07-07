import test from "node:test";
import assert from "node:assert/strict";

import { buildDockerRunArgs, DOCKER_WORKSPACE_PATH, hardenedRunFlags } from "../shell/docker-sandbox";

test("hardened run flags match the docker_execution_sandbox.py Hermes-derived posture", () => {
  const flags = hardenedRunFlags({ memoryMb: 512, cpus: 1.0 });
  assert.ok(flags.includes("--cap-drop"));
  assert.deepEqual(flags.slice(flags.indexOf("--cap-drop"), flags.indexOf("--cap-drop") + 2), ["--cap-drop", "ALL"]);
  for (const cap of ["DAC_OVERRIDE", "CHOWN", "FOWNER"]) {
    assert.ok(flags.includes(cap), `expected re-added capability ${cap}`);
  }
  assert.ok(flags.includes("no-new-privileges"));
  const pidsIdx = flags.indexOf("--pids-limit");
  assert.equal(flags[pidsIdx + 1], "256");
  assert.ok(flags.includes("/tmp:rw,nosuid,size=512m"));
  assert.ok(flags.includes("/var/tmp:rw,noexec,nosuid,size=256m"));
  assert.ok(flags.includes("/run:rw,noexec,nosuid,size=64m"));
  // /tmp deliberately omits noexec (unlike /var/tmp and /run) — matches the
  // Python original exactly, not uniform across all three mounts.
  assert.equal(flags.includes("/tmp:rw,noexec,nosuid,size=512m"), false);
});

test("network defaults to none and only opts into bridge when explicitly enabled", () => {
  const defaultFlags = hardenedRunFlags({});
  assert.deepEqual(defaultFlags.slice(1, 3), ["--network", "none"]);
  const enabledFlags = hardenedRunFlags({ networkEnabled: true });
  assert.deepEqual(enabledFlags.slice(1, 3), ["--network", "bridge"]);
});

test("memory and memory-swap are pinned equal (no swap escape) with a 32mb floor", () => {
  const flags = hardenedRunFlags({ memoryMb: 512 });
  const memIdx = flags.indexOf("--memory");
  assert.equal(flags[memIdx + 1], "512m");
  const swapIdx = flags.indexOf("--memory-swap");
  assert.equal(flags[swapIdx + 1], "512m");

  const tinyFlags = hardenedRunFlags({ memoryMb: 4 });
  assert.equal(tinyFlags[tinyFlags.indexOf("--memory") + 1], "32m");
  assert.equal(tinyFlags[tinyFlags.indexOf("--memory-swap") + 1], "32m");
});

test("cpus floor at 0.25 and format with two decimals", () => {
  const flags = hardenedRunFlags({ cpus: 0.01 });
  assert.equal(flags[flags.indexOf("--cpus") + 1], "0.25");
  const flags2 = hardenedRunFlags({ cpus: 2 });
  assert.equal(flags2[flags2.indexOf("--cpus") + 1], "2.00");
});

test("non-root --user is only added when both uid and gid are provided", () => {
  const withoutUser = hardenedRunFlags({});
  assert.equal(withoutUser.includes("--user"), false);
  // SETUID/SETGID privilege-drop caps are added only when running as root.
  assert.ok(withoutUser.includes("SETUID"));
  assert.ok(withoutUser.includes("SETGID"));

  const withUser = hardenedRunFlags({ runAsUid: 1000, runAsGid: 1000 });
  const userIdx = withUser.indexOf("--user");
  assert.equal(withUser[userIdx + 1], "1000:1000");
  // SETUID/SETGID are skipped when --user is set (no privilege drop needed).
  assert.equal(withUser.includes("SETUID"), false);
});

test("buildDockerRunArgs includes --rm, --read-only, --init, workspace mount and image", () => {
  const args = buildDockerRunArgs({
    image: "debian:bookworm-slim",
    workspaceHostPath: "/host/workspace/run-123",
    innerArgs: ["/bin/sh", "-lc", "echo hi"],
  });
  assert.equal(args[0], "run");
  assert.ok(args.includes("--rm"));
  assert.ok(args.includes("--read-only"));
  assert.ok(args.includes("--init"));
  const mountIdx = args.indexOf("-v");
  assert.equal(args[mountIdx + 1], `/host/workspace/run-123:${DOCKER_WORKSPACE_PATH}:rw`);
  const workdirIdx = args.indexOf("-w");
  assert.equal(args[workdirIdx + 1], DOCKER_WORKSPACE_PATH);
  // Image and inner args must be the trailing positional arguments.
  const imageIdx = args.indexOf("debian:bookworm-slim");
  assert.deepEqual(args.slice(imageIdx), ["debian:bookworm-slim", "/bin/sh", "-lc", "echo hi"]);
});

test("buildDockerRunArgs can opt out of --read-only when explicitly requested", () => {
  const args = buildDockerRunArgs({
    image: "debian:bookworm-slim",
    workspaceHostPath: "/host/workspace",
    innerArgs: [],
    readOnly: false,
  });
  assert.equal(args.includes("--read-only"), false);
});
