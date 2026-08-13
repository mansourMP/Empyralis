import test from "node:test";
import assert from "node:assert/strict";

import { collectPassiveInventorySnapshot } from "../health/service-inventory";

/** Same "quiet not present" shape service-inventory.test.ts's own
 *  baseCommandExists/baseRunCommand use, so every probe unrelated to
 *  OpenClaw resolves without throwing and each test below can focus on the
 *  two new items alone. */
function quietCommandExists(extra: Record<string, string> = {}) {
  return (command: string): string | null => (command in extra ? extra[command] : null);
}
async function quietRunCommand(): Promise<{ exitCode: number | null; stdout: string; stderr: string; timedOut?: boolean }> {
  return { exitCode: 1, stdout: "", stderr: "not present" };
}

function byId(snapshot: Awaited<ReturnType<typeof collectPassiveInventorySnapshot>>) {
  return Object.fromEntries(snapshot.service_inventory.map((item) => [item.id, item]));
}

test("no openclawProfile configured: neither openclaw item is reported at all", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    deps: {
      platform: "linux",
      now: () => new Date("2026-08-13T00:00:00Z"),
      commandExists: quietCommandExists({ openclaw: "/usr/local/bin/openclaw" }),
      runCommand: async () => ({ exitCode: 0, stdout: "OpenClaw 2026.6.10 (aa69b12)", stderr: "" }),
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  const items = byId(snapshot);
  assert.equal(items.openclaw, undefined);
  assert.equal(items.openclaw_channel_plugins, undefined);
  // Never expressed as a "missing" service_statuses entry either — omitted
  // entirely is the honest representation of "not configured to probe",
  // distinct from "probed and found absent".
  assert.equal("openclaw" in snapshot.capability_readiness.service_statuses, false);
});

test("openclawProfile configured, binary not on PATH: both items report missing, not unknown", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    openclawProfile: "empyralis",
    deps: {
      platform: "linux",
      now: () => new Date("2026-08-13T00:00:00Z"),
      commandExists: quietCommandExists(),
      runCommand: quietRunCommand,
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  const items = byId(snapshot);
  assert.equal(items.openclaw.status, "missing");
  assert.equal(items.openclaw.detected, false);
  assert.equal(items.openclaw.metadata?.installed, false);
  // Zero channel plugins is a KNOWN fact here (there is no transport to carry
  // any), never "unknown" — the probe never even had to ask.
  assert.equal(items.openclaw_channel_plugins.status, "missing");
  assert.equal(items.openclaw_channel_plugins.metadata?.installed_count, 0);
});

test("openclawProfile configured, installed at the pinned version, channel plugins present: both items ready", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    openclawProfile: "empyralis",
    deps: {
      platform: "linux",
      now: () => new Date("2026-08-13T00:00:00Z"),
      commandExists: quietCommandExists({ openclaw: "/usr/local/bin/openclaw" }),
      runCommand: async (command, args) => {
        if (args.includes("--version")) {
          return { exitCode: 0, stdout: "OpenClaw 2026.6.10 (aa69b12)", stderr: "" };
        }
        if (args.includes("list")) {
          return {
            exitCode: 0,
            stdout: JSON.stringify({
              chat: {
                telegram: { installed: true },
                feishu: { installed: true },
                line: { installed: false },
              },
            }),
            stderr: "",
          };
        }
        return { exitCode: 1, stdout: "", stderr: "unexpected" };
      },
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  const items = byId(snapshot);
  assert.equal(items.openclaw.status, "ready");
  assert.equal(items.openclaw.metadata?.observed_version, "2026.6.10");
  assert.equal(items.openclaw.metadata?.version_match, true);
  assert.equal(items.openclaw_channel_plugins.status, "ready");
  assert.equal(items.openclaw_channel_plugins.metadata?.installed_count, 2);
  assert.equal(items.openclaw_channel_plugins.metadata?.total_count, 3);
  assert.deepEqual(items.openclaw_channel_plugins.metadata?.installed_channel_ids, ["feishu", "telegram"]);
  assert.equal(snapshot.capability_readiness.service_statuses.openclaw, "ready");
  assert.equal(snapshot.capability_readiness.service_statuses.openclaw_channel_plugins, "ready");
});

test("installed but the wrong version: degraded, never silently accepted as ready", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    openclawProfile: "empyralis",
    deps: {
      platform: "linux",
      now: () => new Date("2026-08-13T00:00:00Z"),
      commandExists: quietCommandExists({ openclaw: "/usr/local/bin/openclaw" }),
      runCommand: async (command, args) => {
        if (args.includes("--version")) {
          return { exitCode: 0, stdout: "OpenClaw 2026.7.1 (deadbeef)", stderr: "" };
        }
        return { exitCode: 0, stdout: JSON.stringify({ chat: {} }), stderr: "" };
      },
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  const items = byId(snapshot);
  assert.equal(items.openclaw.status, "degraded");
  assert.equal(items.openclaw.metadata?.observed_version, "2026.7.1");
  assert.equal(items.openclaw.metadata?.version_match, false);
});

test("installed, zero channel plugins installed: transport degraded (a fact), not unknown", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    openclawProfile: "empyralis",
    deps: {
      platform: "linux",
      now: () => new Date("2026-08-13T00:00:00Z"),
      commandExists: quietCommandExists({ openclaw: "/usr/local/bin/openclaw" }),
      runCommand: async (command, args) => {
        if (args.includes("--version")) {
          return { exitCode: 0, stdout: "OpenClaw 2026.6.10 (aa69b12)", stderr: "" };
        }
        return { exitCode: 0, stdout: JSON.stringify({ chat: { telegram: { installed: false } } }), stderr: "" };
      },
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  const items = byId(snapshot);
  assert.equal(items.openclaw.status, "ready");
  assert.equal(items.openclaw_channel_plugins.status, "degraded");
  assert.equal(items.openclaw_channel_plugins.metadata?.installed_count, 0);
  assert.equal(items.openclaw_channel_plugins.metadata?.total_count, 1);
});

test("version probe times out: unknown, never reported as missing", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    openclawProfile: "empyralis",
    deps: {
      platform: "linux",
      now: () => new Date("2026-08-13T00:00:00Z"),
      commandExists: quietCommandExists({ openclaw: "/usr/local/bin/openclaw" }),
      runCommand: async (command, args) => {
        if (args.includes("--version")) {
          return { exitCode: null, stdout: "", stderr: "timed out", timedOut: true };
        }
        return { exitCode: 1, stdout: "", stderr: "unexpected" };
      },
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  const items = byId(snapshot);
  assert.equal(items.openclaw.status, "unknown");
  assert.equal(items.openclaw.metadata?.probe_timed_out, true);
  // Could not confirm the transport is even absent, so plugin enumeration
  // is also honestly "unknown" here — never coerced to "missing", which
  // would claim a stronger fact than was actually observed.
  assert.equal(items.openclaw_channel_plugins.status, "unknown");
});

test("channels list probe times out while the transport itself is confirmed ready: unknown, not zero", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    openclawProfile: "empyralis",
    deps: {
      platform: "linux",
      now: () => new Date("2026-08-13T00:00:00Z"),
      commandExists: quietCommandExists({ openclaw: "/usr/local/bin/openclaw" }),
      runCommand: async (command, args) => {
        if (args.includes("--version")) {
          return { exitCode: 0, stdout: "OpenClaw 2026.6.10 (aa69b12)", stderr: "" };
        }
        if (args.includes("list")) {
          return { exitCode: null, stdout: "", stderr: "timed out", timedOut: true };
        }
        return { exitCode: 1, stdout: "", stderr: "unexpected" };
      },
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  const items = byId(snapshot);
  assert.equal(items.openclaw.status, "ready");
  assert.equal(items.openclaw_channel_plugins.status, "unknown");
  assert.equal(items.openclaw_channel_plugins.metadata?.installed_count, undefined);
});

test("not installed at all: --version output undefined maps to code openclaw_not_installed", async () => {
  const snapshot = await collectPassiveInventorySnapshot({
    openclawProfile: "empyralis",
    deps: {
      platform: "linux",
      now: () => new Date("2026-08-13T00:00:00Z"),
      commandExists: quietCommandExists(),
      runCommand: quietRunCommand,
      httpGetJson: async () => ({ ok: false, status: 0, error: "not reachable" }),
    },
  });
  const items = byId(snapshot);
  assert.equal(items.openclaw.metadata?.code, "openclaw_not_installed");
});
