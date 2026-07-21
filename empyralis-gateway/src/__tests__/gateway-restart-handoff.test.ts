import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "events";
import { promises as fs } from "fs";
import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";

import { detectGatewaySupervisor, spawnGatewayRestartHandoff } from "../update/gateway-restart-handoff";
import { resolveGatewayReleaseLayout } from "../update/gateway-release-layout";

async function withTmpDir<T>(prefix: string, fn: (dir: string) => Promise<T>): Promise<T> {
  const dir = await mkdtemp(path.join(tmpdir(), prefix));
  try {
    return await fn(dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

test("detectGatewaySupervisor recognizes systemd on linux via env hints systemd itself sets", () => {
  assert.equal(detectGatewaySupervisor({ INVOCATION_ID: "abc" }, "linux"), "systemd");
  assert.equal(detectGatewaySupervisor({ JOURNAL_STREAM: "8:123" }, "linux"), "systemd");
  assert.equal(detectGatewaySupervisor({}, "linux"), "none");
});

test("detectGatewaySupervisor never reports systemd on darwin even with the same env hints (platform-scoped)", () => {
  assert.equal(detectGatewaySupervisor({ INVOCATION_ID: "abc" }, "darwin"), "none");
});

test("detectGatewaySupervisor recognizes launchd on darwin only via its own hint, and reports none by default (no installer wires a launchd job today)", () => {
  assert.equal(detectGatewaySupervisor({}, "darwin"), "none");
  assert.equal(detectGatewaySupervisor({ XPC_SERVICE_NAME: "com.empyralis.gateway" }, "darwin"), "launchd");
});

/** Fake child_process.spawn: never launches a real process. Returns an
 *  EventEmitter-based stand-in with a pid, exactly the shape
 *  spawnGatewayRestartHandoff needs (`.pid`, `.once('error', ...)`, `.unref()`). */
function fakeSpawn(pid: number | undefined) {
  const calls: Array<{ command: string; args: string[] }> = [];
  const spawnImpl = ((command: string, args: string[]) => {
    calls.push({ command, args });
    const child = new EventEmitter() as EventEmitter & { pid?: number; unref: () => void };
    child.pid = pid;
    child.unref = () => undefined;
    return child;
  }) as unknown as typeof import("child_process").spawn;
  return { spawnImpl, calls };
}

test("spawnGatewayRestartHandoff writes the script + params files and spawns it detached, returning the confirmed pid", async () => {
  await withTmpDir("empyralis-gw-handoff-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    const { spawnImpl, calls } = fakeSpawn(4242);

    const result = await spawnGatewayRestartHandoff({
      layout,
      parentPid: 111,
      newEntry: "/releases/0.2.0/gateway/dist/index.js",
      previousEntry: "/releases/0.1.0/gateway/dist/index.js",
      previousCurrentTarget: "/releases/0.1.0",
      logPath: path.join(root, "handoff.log"),
      spawnImpl,
    });

    assert.equal(result.pid, 4242);
    assert.equal(calls.length, 1, "exactly one detached process must be spawned");

    const scriptContents = await fs.readFile(result.scriptPath, "utf8");
    assert.match(scriptContents, /parentPid/);
    const paramsContents = JSON.parse(await fs.readFile(result.paramsPath, "utf8"));
    assert.equal(paramsContents.parentPid, 111);
    assert.equal(paramsContents.newEntry, "/releases/0.2.0/gateway/dist/index.js");
    assert.equal(paramsContents.previousEntry, "/releases/0.1.0/gateway/dist/index.js");
    assert.equal(paramsContents.previousCurrentTarget, "/releases/0.1.0");
    assert.equal(paramsContents.currentSymlinkPath, layout.currentSymlinkPath);
    assert.equal(paramsContents.logPath, path.join(root, "handoff.log"));

    // The spawned command is the params-driven script itself, not the real
    // gateway entrypoint — the handoff script is a separate standalone
    // process precisely so it survives this (the parent) process exiting.
    assert.equal(calls[0].args[0], result.scriptPath);
    assert.equal(calls[0].args[1], result.paramsPath);
  });
});

test("spawnGatewayRestartHandoff surfaces a spawn without a pid (caller decides this counts as failure)", async () => {
  await withTmpDir("empyralis-gw-handoff-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    const { spawnImpl } = fakeSpawn(undefined);

    const result = await spawnGatewayRestartHandoff({
      layout,
      parentPid: 111,
      newEntry: "/releases/0.2.0/gateway/dist/index.js",
      previousEntry: null,
      previousCurrentTarget: null,
      logPath: path.join(root, "handoff.log"),
      spawnImpl,
    });
    assert.equal(result.pid, undefined);
  });
});

test("spawnGatewayRestartHandoff propagates a synchronous spawn throw to the caller instead of swallowing it", async () => {
  await withTmpDir("empyralis-gw-handoff-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    const throwingSpawn = (() => {
      throw new Error("ENOENT: no such node binary");
    }) as unknown as typeof import("child_process").spawn;

    await assert.rejects(
      spawnGatewayRestartHandoff({
        layout,
        parentPid: 111,
        newEntry: "/releases/0.2.0/gateway/dist/index.js",
        previousEntry: null,
        previousCurrentTarget: null,
        logPath: path.join(root, "handoff.log"),
        spawnImpl: throwingSpawn,
      }),
      /ENOENT/,
    );
  });
});
