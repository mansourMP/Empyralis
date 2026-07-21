import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "events";
import { promises as fs } from "fs";
import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";

import { GatewaySelfUpdateRuntime } from "../update/gateway-self-update-runtime";
import { resolveGatewayReleaseLayout } from "../update/gateway-release-layout";
import type { ExecLike, GatewayFetchLike } from "../update/gateway-artifact-installer";
import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";

async function withTmpDir<T>(prefix: string, fn: (dir: string) => Promise<T>): Promise<T> {
  const dir = await mkdtemp(path.join(tmpdir(), prefix));
  try {
    return await fn(dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

function streamFromBuffer(buf: Buffer): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array(buf));
      controller.close();
    },
  });
}

/** Every test's fetch: returns a body big enough to pass the size floor, and
 *  no `.sha256` sidecar (matches the honest today-there-is-none reality this
 *  runtime is built against — see its module doc comment). */
const fakeFetch: GatewayFetchLike = async () => ({
  ok: true,
  status: 200,
  statusText: "OK",
  body: streamFromBuffer(Buffer.from("x".repeat(4096))),
});

/** Fake `tar`: materializes a real dist/index.js so installGatewayArtifact's
 *  own validation passes, without ever shelling out to a real tar process. */
const fakeTar: ExecLike = async (_command, args) => {
  const targetDir = args[args.indexOf("-C") + 1];
  await fs.mkdir(path.join(targetDir, "dist"), { recursive: true });
  await fs.writeFile(path.join(targetDir, "dist", "index.js"), "// fake build\n");
  return { code: 0, stderr: "" };
};

function fakeHandoffSpawn(pid: number | undefined) {
  const calls: unknown[] = [];
  const spawnImpl = ((command: string, args: string[]) => {
    calls.push({ command, args });
    const child = new EventEmitter() as EventEmitter & { pid?: number; unref: () => void };
    child.pid = pid;
    child.unref = () => undefined;
    return child;
  }) as unknown as Parameters<typeof import("../update/gateway-restart-handoff").spawnGatewayRestartHandoff>[0]["spawnImpl"];
  return { spawnImpl, calls };
}

function makeFrame(args: Record<string, unknown>): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id: "req-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: "gateway.self_update",
      arguments: args,
      run_id: "run-1",
      trace_id: "trace-1",
      workspace_id: "ws-1",
    },
  };
}

test("no-op-safe: target_version === currentVersion never downloads, swaps, or shuts down", async () => {
  await withTmpDir("empyralis-gw-selfupdate-", async (root) => {
    const stateDir = path.join(root, "gateway");
    await fs.mkdir(stateDir, { recursive: true });
    let shutdownCalls = 0;
    let fetchCalls = 0;
    const runtime = new GatewaySelfUpdateRuntime({
      currentVersion: "0.1.0",
      stateDir,
      requestShutdown: () => {
        shutdownCalls += 1;
      },
      preShutdownDelayMs: 0,
      fetchImpl: (async (...fetchArgs: Parameters<GatewayFetchLike>) => {
        fetchCalls += 1;
        return fakeFetch(...fetchArgs);
      }) as GatewayFetchLike,
    });

    const result = await runtime.handleCapabilityInvoke(
      makeFrame({ target_version: "0.1.0", artifact_url: "https://example.invalid/gw.tar.gz" }),
    );
    assert.deepEqual(result, {
      updated: false,
      reason: "already_current",
      current_version: "0.1.0",
      target_version: "0.1.0",
    });
    assert.equal(fetchCalls, 0);
    assert.equal(shutdownCalls, 0);
    const layout = resolveGatewayReleaseLayout({ stateDir, env: {} });
    await assert.rejects(fs.access(layout.currentSymlinkPath), "nothing should be staged for a no-op update");
  });
});

test("rejects missing target_version / artifact_url before touching the network", async () => {
  await withTmpDir("empyralis-gw-selfupdate-", async (root) => {
    const stateDir = path.join(root, "gateway");
    const runtime = new GatewaySelfUpdateRuntime({
      currentVersion: "0.1.0",
      stateDir,
      requestShutdown: () => assert.fail("must not shut down on a validation failure"),
      preShutdownDelayMs: 0,
    });
    await assert.rejects(runtime.handleCapabilityInvoke(makeFrame({ artifact_url: "https://x/y.tar.gz" })), /target_version is required/);
    await assert.rejects(runtime.handleCapabilityInvoke(makeFrame({ target_version: "0.2.0" })), /artifact_url is required/);
  });
});

test("supervised (systemd) path: swaps the symlink, requests shutdown, never spawns a restart handoff", async () => {
  await withTmpDir("empyralis-gw-selfupdate-", async (root) => {
    const stateDir = path.join(root, "gateway");
    await fs.mkdir(stateDir, { recursive: true });
    let shutdownCalls = 0;
    const { spawnImpl, calls: handoffCalls } = fakeHandoffSpawn(999);
    const runtime = new GatewaySelfUpdateRuntime({
      currentVersion: "0.1.0",
      stateDir,
      requestShutdown: () => {
        shutdownCalls += 1;
      },
      preShutdownDelayMs: 0,
      fetchImpl: fakeFetch,
      tarExec: fakeTar,
      spawnImpl,
      detectSupervisor: () => "systemd",
    });

    const result = await runtime.handleCapabilityInvoke(
      makeFrame({ target_version: "0.2.0", artifact_url: "https://example.invalid/gw.tar.gz" }),
    );
    assert.equal(result.updated, true);
    assert.equal(result.restart_mode, "supervised");
    assert.equal(handoffCalls.length, 0, "a supervised restart must never spawn a detached handoff");
    assert.equal(shutdownCalls, 1);

    const layout = resolveGatewayReleaseLayout({ stateDir, env: {} });
    assert.equal(await fs.readlink(layout.currentSymlinkPath), path.join(layout.releasesDir, "0.2.0"));
  });
});

test("unsupervised path: spawns the restart handoff, requests shutdown only after a confirmed pid", async () => {
  await withTmpDir("empyralis-gw-selfupdate-", async (root) => {
    const stateDir = path.join(root, "gateway");
    await fs.mkdir(stateDir, { recursive: true });
    let shutdownCalls = 0;
    const { spawnImpl, calls: handoffCalls } = fakeHandoffSpawn(4321);
    const runtime = new GatewaySelfUpdateRuntime({
      currentVersion: "0.1.0",
      stateDir,
      requestShutdown: () => {
        shutdownCalls += 1;
      },
      preShutdownDelayMs: 0,
      fetchImpl: fakeFetch,
      tarExec: fakeTar,
      spawnImpl,
      detectSupervisor: () => "none",
    });

    const result = await runtime.handleCapabilityInvoke(
      makeFrame({ target_version: "0.2.0", artifact_url: "https://example.invalid/gw.tar.gz" }),
    );
    assert.equal(result.updated, true);
    assert.equal(result.restart_mode, "handoff");
    assert.equal(result.handoff_pid, 4321);
    assert.equal(handoffCalls.length, 1);
    assert.equal(shutdownCalls, 1);

    const layout = resolveGatewayReleaseLayout({ stateDir, env: {} });
    assert.equal(await fs.readlink(layout.currentSymlinkPath), path.join(layout.releasesDir, "0.2.0"));
  });
});

test("unsupervised path: a handoff spawn that never reports a pid rolls the symlink back and fails the invoke without ever shutting down", async () => {
  await withTmpDir("empyralis-gw-selfupdate-", async (root) => {
    const stateDir = path.join(root, "gateway");
    await fs.mkdir(stateDir, { recursive: true });
    let shutdownCalls = 0;
    const { spawnImpl: firstSpawn } = fakeHandoffSpawn(111);
    const runtime1 = new GatewaySelfUpdateRuntime({
      currentVersion: "0.1.0",
      stateDir,
      requestShutdown: () => undefined,
      preShutdownDelayMs: 0,
      fetchImpl: fakeFetch,
      tarExec: fakeTar,
      spawnImpl: firstSpawn,
      detectSupervisor: () => "systemd", // establish a known-good "0.2.0" current via the simpler supervised path
    });
    await runtime1.handleCapabilityInvoke(makeFrame({ target_version: "0.2.0", artifact_url: "https://example.invalid/gw.tar.gz" }));
    const layout = resolveGatewayReleaseLayout({ stateDir, env: {} });
    const goodTarget = await fs.readlink(layout.currentSymlinkPath);
    assert.equal(goodTarget, path.join(layout.releasesDir, "0.2.0"));

    const { spawnImpl: pidlessSpawn } = fakeHandoffSpawn(undefined);
    const runtime2 = new GatewaySelfUpdateRuntime({
      currentVersion: "0.2.0",
      stateDir,
      requestShutdown: () => {
        shutdownCalls += 1;
      },
      preShutdownDelayMs: 0,
      fetchImpl: fakeFetch,
      tarExec: fakeTar,
      spawnImpl: pidlessSpawn,
      detectSupervisor: () => "none",
    });

    await assert.rejects(
      runtime2.handleCapabilityInvoke(makeFrame({ target_version: "0.3.0", artifact_url: "https://example.invalid/gw2.tar.gz" })),
      /restart handoff process did not report a pid/,
    );
    assert.equal(shutdownCalls, 0, "a failed handoff must never trigger shutdown — the old build keeps running");
    assert.equal(await fs.readlink(layout.currentSymlinkPath), goodTarget, "the symlink must be rolled back to the last known-good release");
  });
});

test("unsupervised path: a throwing spawn rolls the symlink back and fails the invoke without ever shutting down", async () => {
  await withTmpDir("empyralis-gw-selfupdate-", async (root) => {
    const stateDir = path.join(root, "gateway");
    await fs.mkdir(stateDir, { recursive: true });
    const layout = resolveGatewayReleaseLayout({ stateDir, env: {} });

    let shutdownCalls = 0;
    const throwingSpawn = (() => {
      throw new Error("ENOENT: node binary not found");
    }) as unknown as Parameters<typeof import("../update/gateway-restart-handoff").spawnGatewayRestartHandoff>[0]["spawnImpl"];
    const runtime = new GatewaySelfUpdateRuntime({
      currentVersion: "0.1.0",
      stateDir,
      requestShutdown: () => {
        shutdownCalls += 1;
      },
      preShutdownDelayMs: 0,
      fetchImpl: fakeFetch,
      tarExec: fakeTar,
      spawnImpl: throwingSpawn,
      detectSupervisor: () => "none",
    });

    await assert.rejects(
      runtime.handleCapabilityInvoke(makeFrame({ target_version: "0.2.0", artifact_url: "https://example.invalid/gw.tar.gz" })),
      /restart handoff failed to start/,
    );
    assert.equal(shutdownCalls, 0, "the currently-running (old-build) process must never shut itself down over a failed handoff spawn");
    // First-ever install: there is no previous release to roll back TO, so
    // rollbackCurrentGatewaySymlink is correctly a no-op here — `current`
    // is left pointing at the newly-staged (but restart-unconfirmed)
    // release. That's safe: nothing has restarted, this process (still
    // running the old build in memory, per the assertion above) was never
    // touched, and `current` is purely a pointer for whatever the NEXT
    // restart resolves through.
    assert.equal(await fs.readlink(layout.currentSymlinkPath), path.join(layout.releasesDir, "0.2.0"));
  });
});

test("a download failure never touches the symlink and never shuts down", async () => {
  await withTmpDir("empyralis-gw-selfupdate-", async (root) => {
    const stateDir = path.join(root, "gateway");
    await fs.mkdir(stateDir, { recursive: true });
    const layout = resolveGatewayReleaseLayout({ stateDir, env: {} });
    let shutdownCalls = 0;
    const failingFetch: GatewayFetchLike = async () => ({ ok: false, status: 500, statusText: "Server Error", body: null });
    const runtime = new GatewaySelfUpdateRuntime({
      currentVersion: "0.1.0",
      stateDir,
      requestShutdown: () => {
        shutdownCalls += 1;
      },
      preShutdownDelayMs: 0,
      fetchImpl: failingFetch,
      tarExec: fakeTar,
      detectSupervisor: () => "none",
    });

    await assert.rejects(
      runtime.handleCapabilityInvoke(makeFrame({ target_version: "0.2.0", artifact_url: "https://example.invalid/gw.tar.gz" })),
      /HTTP 500/,
    );
    assert.equal(shutdownCalls, 0);
    await assert.rejects(fs.access(layout.currentSymlinkPath));
  });
});
