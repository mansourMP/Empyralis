import test from "node:test";
import assert from "node:assert/strict";

import {
  GatewayDoctorRuntime,
  buildDefaultGatewayDoctorChecks,
  runGatewayDoctor,
  type GatewayDoctorCheck,
  type GatewayDoctorContext,
} from "../health/gateway-doctor";
import type { PassiveInventorySnapshot } from "../health/service-inventory";
import type { PersonalChannelHealthSnapshot, PersonalChannelRuntime } from "../channels/personal-runtime";
import { GatewayCapabilityRouter } from "../supervisor/capability-router";
import { PersonalChannelRuntimeRegistry } from "../channels/personal-runtime";
import type { GatewayRequestEnvelope, GatewayToolInvokePayload, GatewayToolInterruptPayload } from "../protocol/types";

function makeEmptyInventory(overrides: Partial<PassiveInventorySnapshot> = {}): PassiveInventorySnapshot {
  return {
    service_inventory: [],
    native_runtime: {
      os: "linux",
      arch: "x64",
      release: "test",
      hostname: "test-box",
      desktop_session: "user_session",
      system_service_mode: false,
    },
    capability_readiness: {
      requested: [],
      ready: [],
      blocked: [],
      permission_states: {},
      passive_services: [],
      service_statuses: {},
      shell_full_access_locally_enabled: false,
    },
    ...overrides,
  };
}

function makeContext(overrides: Partial<GatewayDoctorContext> = {}): GatewayDoctorContext {
  return {
    getHealthState: () => "online",
    getRequestedCapabilities: () => [],
    personalChannelRuntimes: undefined,
    env: {},
    platform: "linux",
    collectPassiveInventory: (async () => makeEmptyInventory()) as GatewayDoctorContext["collectPassiveInventory"],
    invalidatePassiveInventoryCache: () => undefined,
    detectSupervisor: () => "none",
    auditSupervisorInstall: async () => ({ supported: false, definition: null, fileState: "not_applicable" }),
    ...overrides,
  };
}

function makeInvokeFrame(capabilityId: string, args: Record<string, unknown>): GatewayRequestEnvelope<GatewayToolInvokePayload> {
  return {
    kind: "request",
    id: "req-doctor-1",
    type: "tool.invoke",
    ts: new Date().toISOString(),
    payload: {
      capability_id: capabilityId,
      arguments: args,
      run_id: "run-doctor-1",
      trace_id: "trace-1",
      workspace_id: "ws-1",
    },
  };
}

// ---------------------------------------------------------------------------
// runGatewayDoctor: the generic detect -> (safe) repair -> re-validate engine

test("runGatewayDoctor reports a passing check as pass, never invokes repair", async () => {
  let repairCalls = 0;
  const check: GatewayDoctorCheck = {
    id: "always_pass",
    label: "Always fine",
    async detect() {
      return { status: "pass", detail: "All good." };
    },
    async repair() {
      repairCalls += 1;
      return {};
    },
  };
  const result = await runGatewayDoctor([check], makeContext(), { repair: true });
  assert.equal(result.results[0].status, "pass");
  assert.equal(result.results[0].repairable, true);
  assert.equal(result.results[0].repaired, undefined);
  assert.equal(repairCalls, 0, "repair must never run for an already-passing check");
});

test("runGatewayDoctor does not attempt repair unless repair:true was requested", async () => {
  let repairCalls = 0;
  const check: GatewayDoctorCheck = {
    id: "broken",
    label: "Broken thing",
    async detect() {
      return { status: "fail", detail: "It's broken." };
    },
    async repair() {
      repairCalls += 1;
      return {};
    },
  };
  const result = await runGatewayDoctor([check], makeContext(), { repair: false });
  assert.equal(result.results[0].status, "fail");
  assert.equal(repairCalls, 0);
  assert.equal(result.repair_requested, false);
});

test("runGatewayDoctor re-validates after repair and only marks repaired when detect() now passes", async () => {
  let fixed = false;
  const check: GatewayDoctorCheck = {
    id: "fixable",
    label: "Fixable thing",
    async detect() {
      return fixed
        ? { status: "pass", detail: "Now fine." }
        : { status: "fail", detail: "Not fine yet." };
    },
    async repair() {
      fixed = true;
      return { detail: "Applied the fix." };
    },
  };
  const result = await runGatewayDoctor([check], makeContext(), { repair: true });
  const r = result.results[0];
  assert.equal(r.status, "pass");
  assert.equal(r.repaired, true);
  assert.equal(r.repair_detail, "Applied the fix.");
  assert.equal(result.summary.repaired, 1);
});

test("runGatewayDoctor never trusts the repair step's own claim — a repair that doesn't actually fix it stays failed", async () => {
  const check: GatewayDoctorCheck = {
    id: "unfixable",
    label: "Unfixable thing",
    async detect() {
      return { status: "fail", detail: "Still broken." };
    },
    async repair() {
      // Claims success but the underlying condition never changes.
      return { detail: "Tried something." };
    },
  };
  const result = await runGatewayDoctor([check], makeContext(), { repair: true });
  const r = result.results[0];
  assert.equal(r.status, "fail");
  assert.equal(r.repaired, false);
  assert.equal(r.repair_detail, "Tried something.");
});

test("runGatewayDoctor survives a check whose detect() throws, without aborting later checks", async () => {
  const throwing: GatewayDoctorCheck = {
    id: "throws",
    label: "Throws",
    async detect() {
      throw new Error("boom");
    },
  };
  const fine: GatewayDoctorCheck = {
    id: "fine",
    label: "Fine",
    async detect() {
      return { status: "pass", detail: "ok" };
    },
  };
  const result = await runGatewayDoctor([throwing, fine], makeContext());
  assert.equal(result.results[0].status, "fail");
  assert.match(result.results[0].detail, /boom/);
  assert.equal(result.results[1].status, "pass");
});

test("runGatewayDoctor survives a repair() that throws — re-validates and reports the failure", async () => {
  const check: GatewayDoctorCheck = {
    id: "repair_throws",
    label: "Repair throws",
    async detect() {
      return { status: "fail", detail: "Broken." };
    },
    async repair() {
      throw new Error("repair exploded");
    },
  };
  const result = await runGatewayDoctor([check], makeContext(), { repair: true });
  const r = result.results[0];
  assert.equal(r.status, "fail");
  assert.equal(r.repaired, false);
  assert.match(String(r.repair_detail), /repair exploded/);
});

test("runGatewayDoctor's summary counts statuses and repairs", async () => {
  const checks: GatewayDoctorCheck[] = [
    { id: "a", label: "a", detect: async () => ({ status: "pass", detail: "" }) },
    { id: "b", label: "b", detect: async () => ({ status: "warn", detail: "" }) },
    { id: "c", label: "c", detect: async () => ({ status: "skip", detail: "" }) },
  ];
  const result = await runGatewayDoctor(checks, makeContext());
  assert.equal(result.summary.pass, 1);
  assert.equal(result.summary.warn, 1);
  assert.equal(result.summary.skip, 1);
});

// ---------------------------------------------------------------------------
// Default checks

test("default checks: cloud_connection reflects the real tracked health state, not a hardcoded value", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const cloudCheck = checks.find((c) => c.id === "cloud_connection")!;

  const online = await cloudCheck.detect(makeContext({ getHealthState: () => "online" }));
  assert.equal(online.status, "pass");

  const offline = await cloudCheck.detect(makeContext({ getHealthState: () => "offline" }));
  assert.equal(offline.status, "fail");

  const reconnecting = await cloudCheck.detect(makeContext({ getHealthState: () => "reconnecting" }));
  assert.equal(reconnecting.status, "warn");

  const degraded = await cloudCheck.detect(makeContext({ getHealthState: () => "degraded" }));
  assert.equal(degraded.status, "warn");
});

test("default checks: capability_readiness passes when nothing is blocked, warns with plain-language names otherwise", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "capability_readiness")!;

  const allReady = await check.detect(
    makeContext({
      getRequestedCapabilities: () => ["shell.execute"],
      collectPassiveInventory: (async () =>
        makeEmptyInventory({
          capability_readiness: {
            requested: ["shell.execute"],
            ready: ["shell.execute"],
            blocked: [],
            permission_states: {},
            passive_services: [],
            service_statuses: {},
            shell_full_access_locally_enabled: false,
          },
        })) as GatewayDoctorContext["collectPassiveInventory"],
    }),
  );
  assert.equal(allReady.status, "pass");

  const someBlocked = await check.detect(
    makeContext({
      getRequestedCapabilities: () => ["shell.execute"],
      collectPassiveInventory: (async () =>
        makeEmptyInventory({
          capability_readiness: {
            requested: ["shell.execute"],
            ready: [],
            blocked: ["shell.execute"],
            permission_states: {
              "shell.execute": {
                capability_id: "shell.execute",
                permission: "shell_sandbox",
                state: "denied",
                prompt_available: false,
                summary: "blocked",
              },
            },
            passive_services: [],
            service_statuses: {},
            shell_full_access_locally_enabled: false,
          },
        })) as GatewayDoctorContext["collectPassiveInventory"],
    }),
  );
  assert.equal(someBlocked.status, "warn");
  assert.match(someBlocked.detail, /sandboxed shell access/);
  assert.doesNotMatch(someBlocked.detail, /shell\.execute/, "must translate the raw capability id into plain language");
});

test("default checks: capability_readiness repair invalidates the cache and re-probes", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "capability_readiness")!;
  let invalidateCalls = 0;
  let probeCalls = 0;
  const ctx = makeContext({
    getRequestedCapabilities: () => ["llm.generate"],
    invalidatePassiveInventoryCache: () => {
      invalidateCalls += 1;
    },
    collectPassiveInventory: (async () => {
      probeCalls += 1;
      return makeEmptyInventory();
    }) as GatewayDoctorContext["collectPassiveInventory"],
  });
  await check.repair!(ctx);
  assert.equal(invalidateCalls, 1);
  assert.equal(probeCalls, 1);
});

test("default checks: cli_subscription passes if either CLI is ready, otherwise reports plain-language per-CLI status", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "cli_subscription")!;

  const ready = await check.detect(
    makeContext({
      collectPassiveInventory: (async () =>
        makeEmptyInventory({
          service_inventory: [
            {
              id: "claude_cli",
              label: "Claude Code",
              kind: "cli",
              status: "ready",
              detected: true,
              passive: true,
              execution_enabled: false,
              check: "x",
              summary: "ready",
              last_checked_at: new Date().toISOString(),
            },
          ],
        })) as GatewayDoctorContext["collectPassiveInventory"],
    }),
  );
  assert.equal(ready.status, "pass");

  const notReady = await check.detect(
    makeContext({
      collectPassiveInventory: (async () =>
        makeEmptyInventory({
          service_inventory: [
            {
              id: "claude_cli",
              label: "Claude Code",
              kind: "cli",
              status: "degraded",
              detected: true,
              passive: true,
              execution_enabled: false,
              check: "x",
              summary: "degraded",
              last_checked_at: new Date().toISOString(),
            },
            {
              id: "codex_cli",
              label: "Codex",
              kind: "cli",
              status: "missing",
              detected: false,
              passive: true,
              execution_enabled: false,
              check: "x",
              summary: "missing",
              last_checked_at: new Date().toISOString(),
            },
          ],
        })) as GatewayDoctorContext["collectPassiveInventory"],
    }),
  );
  assert.equal(notReady.status, "warn");
  assert.match(notReady.detail, /not signed in/);
  assert.match(notReady.detail, /isn't installed/);

  const untracked = await check.detect(makeContext());
  assert.equal(untracked.status, "skip");
});

test("default checks: personal_channel_imessage skips when not configured, passes when connected, fails with plain language otherwise", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "personal_channel_imessage")!;

  const notConfigured = await check.detect(makeContext());
  assert.equal(notConfigured.status, "skip");

  function fakeRuntime(snapshot: PersonalChannelHealthSnapshot | null): GatewayDoctorContext["personalChannelRuntimes"] {
    return {
      runtimeForChannel: (channelKey: string) => {
        if (channelKey !== "imessage_personal") return undefined;
        return {
          getHealthSnapshot: async () => snapshot as PersonalChannelHealthSnapshot,
        } as unknown as PersonalChannelRuntime;
      },
    };
  }

  const connected = await check.detect(
    makeContext({
      personalChannelRuntimes: fakeRuntime({
        channelKey: "imessage_personal",
        provider: "bluebubbles_local_bridge",
        status: "connected",
        running: true,
        connected: true,
        issues: [],
      }),
    }),
  );
  assert.equal(connected.status, "pass");

  const fdaRequired = await check.detect(
    makeContext({
      personalChannelRuntimes: fakeRuntime({
        channelKey: "imessage_personal",
        provider: "bluebubbles_local_bridge",
        status: "unavailable",
        running: true,
        connected: false,
        issues: ["imessage_personal_full_disk_access_required"],
      }),
    }),
  );
  assert.equal(fdaRequired.status, "fail");
  assert.match(fdaRequired.detail, /Full Disk Access/);
});

test("default checks: supervisor_presence passes under systemd/launchd, warns when unsupervised", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "supervisor_presence")!;

  const supervised = await check.detect(makeContext({ detectSupervisor: () => "systemd" }));
  assert.equal(supervised.status, "pass");

  const unsupervised = await check.detect(
    makeContext({
      detectSupervisor: () => "none",
      auditSupervisorInstall: async () => ({ supported: true, definition: null, fileState: "missing" }),
    }),
  );
  assert.equal(unsupervised.status, "warn");
  assert.match(unsupervised.detail, /no automatic restart/);
});

test("default checks: supervisor_presence distinguishes missing / installed-but-inactive / drifted / unsupported when not currently supervised", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "supervisor_presence")!;

  const missing = await check.detect(
    makeContext({
      detectSupervisor: () => "none",
      auditSupervisorInstall: async () => ({ supported: true, definition: null, fileState: "missing" }),
    }),
  );
  assert.equal(missing.status, "warn");
  assert.match(missing.detail, /no automatic restart/);

  const installedInactive = await check.detect(
    makeContext({
      detectSupervisor: () => "none",
      auditSupervisorInstall: async () => ({ supported: true, definition: null, fileState: "present_matching" }),
    }),
  );
  assert.equal(installedInactive.status, "warn");
  assert.match(installedInactive.detail, /take effect the next time/);

  const drifted = await check.detect(
    makeContext({
      detectSupervisor: () => "none",
      auditSupervisorInstall: async () => ({ supported: true, definition: null, fileState: "present_drifted" }),
    }),
  );
  assert.equal(drifted.status, "warn");
  assert.match(drifted.detail, /out of date/);

  const unsupported = await check.detect(
    makeContext({
      detectSupervisor: () => "none",
      auditSupervisorInstall: async () => ({ supported: false, definition: null, fileState: "not_applicable" }),
    }),
  );
  assert.equal(unsupported.status, "warn");
  assert.match(unsupported.detail, /isn't available on this computer's operating system/);
});

test("default checks: supervisor_presence repair skips entirely when already supervised", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "supervisor_presence")!;
  let auditCalls = 0;
  const result = await check.repair!(
    makeContext({
      detectSupervisor: () => "launchd",
      auditSupervisorInstall: async () => {
        auditCalls += 1;
        return { supported: true, definition: null, fileState: "present_matching" };
      },
    }),
  );
  assert.equal(auditCalls, 0, "must not even audit the on-disk unit when this process is already confirmed-supervised");
  assert.match(result.detail!, /already supervised/);
});

test("default checks: supervisor_presence repair installs a missing unit and reports the outcome", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "supervisor_presence")!;
  let repairRequested = false;
  const result = await check.repair!(
    makeContext({
      detectSupervisor: () => "none",
      auditSupervisorInstall: async (attemptRepair: boolean) => {
        repairRequested = attemptRepair;
        return {
          supported: true,
          definition: null,
          fileState: "missing",
          repair: {
            action: "wrote_new_unit",
            changed: true,
            permissionDenied: false,
            requiresManualReload: false,
            detail: "Installed the launch agent so this computer restarts itself automatically going forward.",
          },
        };
      },
    }),
  );
  assert.equal(repairRequested, true);
  assert.match(result.detail!, /Installed the launch agent/);
});

test("default checks: supervisor_presence repair surfaces a permission-denied outcome honestly", async () => {
  const checks = buildDefaultGatewayDoctorChecks();
  const check = checks.find((c) => c.id === "supervisor_presence")!;
  const result = await check.repair!(
    makeContext({
      detectSupervisor: () => "none",
      auditSupervisorInstall: async () => ({
        supported: true,
        definition: null,
        fileState: "missing",
        repair: {
          action: "permission_denied",
          changed: false,
          permissionDenied: true,
          requiresManualReload: false,
          detail: "Could not write the unit — this computer needs elevated permissions (for example, sudo) to install automatic restart here.",
        },
      }),
    }),
  );
  assert.match(result.detail!, /elevated permissions/);
});

// ---------------------------------------------------------------------------
// GatewayDoctorRuntime capability contract + router wiring

test("GatewayDoctorRuntime advertises gateway.doctor.run and returns a structured run result", async () => {
  const runtime = new GatewayDoctorRuntime({
    checkpoints: { currentHealthState: () => "online" },
    getRequestedCapabilities: () => [],
    checks: [
      { id: "a", label: "A", detect: async () => ({ status: "pass", detail: "ok" }) },
    ],
  });
  assert.deepEqual(runtime.requestedCapabilities(), ["gateway.doctor.run"]);
  assert.equal(runtime.supportsCapability("gateway.doctor.run"), true);
  assert.equal(runtime.supportsCapability("gateway.self_update"), false);

  const result = await runtime.handleCapabilityInvoke(makeInvokeFrame("gateway.doctor.run", {}));
  assert.equal(result.capability_id, "gateway.doctor.run");
  const results = result.results as unknown[];
  assert.equal(results.length, 1);
});

test("GatewayDoctorRuntime threads the repair argument through to runGatewayDoctor", async () => {
  let fixed = false;
  const runtime = new GatewayDoctorRuntime({
    checkpoints: { currentHealthState: () => "online" },
    getRequestedCapabilities: () => [],
    checks: [
      {
        id: "fixable",
        label: "Fixable",
        detect: async () => (fixed ? { status: "pass", detail: "fixed" } : { status: "fail", detail: "broken" }),
        repair: async () => {
          fixed = true;
          return {};
        },
      },
    ],
  });
  const withoutRepair = await runtime.handleCapabilityInvoke(makeInvokeFrame("gateway.doctor.run", {}));
  assert.equal((withoutRepair.results as any[])[0].status, "fail");

  fixed = false;
  const withRepair = await runtime.handleCapabilityInvoke(makeInvokeFrame("gateway.doctor.run", { repair: true }));
  assert.equal((withRepair.results as any[])[0].status, "pass");
  assert.equal((withRepair.results as any[])[0].repaired, true);
});

// ---------------------------------------------------------------------------
// GatewayDoctorRuntime.ensureSupervisorInstalled — MAN-295 / MAN-269: the
// install-time/afterConnected call index.ts makes so a fresh macOS pairing
// gets its LaunchAgent written automatically, without a human ever having to
// find the Diagnostics panel and click "Fix what's safe to fix". Reuses
// SUPERVISOR_PRESENCE_CHECK.detect()/.repair() verbatim (not re-implemented)
// — these tests pin that reuse and the three outcomes index.ts's afterConnected
// hook has to handle honestly: already-supervised, a clean install, and a
// permission-denied install.

test("ensureSupervisorInstalled skips repair entirely when this process is already supervised", async () => {
  let auditCalls = 0;
  const runtime = new GatewayDoctorRuntime({
    checkpoints: { currentHealthState: () => "online" },
    getRequestedCapabilities: () => [],
    detectSupervisor: () => "launchd",
    auditSupervisorInstall: async () => {
      auditCalls += 1;
      return { supported: true, definition: null, fileState: "present_matching" };
    },
  });

  const result = await runtime.ensureSupervisorInstalled();

  assert.equal(result.id, "supervisor_presence");
  assert.equal(result.status, "pass");
  assert.match(result.detail, /automatically restart itself/);
  assert.equal(result.repaired, undefined, "a passing check must never attempt (or claim) a repair");
  assert.equal(auditCalls, 0, "detect() already reports pass via detectSupervisor — no on-disk audit needed");
});

test("ensureSupervisorInstalled installs a missing LaunchAgent and re-validates against the new on-disk state", async () => {
  // A freshly-written LaunchAgent takes effect on the NEXT start (repair
  // deliberately never kickstarts/restarts the currently-running process —
  // see gateway-supervisor-install.ts's module doc comment), so THIS run's
  // re-validated status is "warn: takes effect next start", not "pass" —
  // repaired only ever means "the re-validated detect() came back pass",
  // and it honestly doesn't here. That's still a world away from the
  // pre-fix behavior (nothing ever ran repair() at all).
  let installedOnDisk = false;
  const runtime = new GatewayDoctorRuntime({
    checkpoints: { currentHealthState: () => "online" },
    getRequestedCapabilities: () => [],
    platform: "darwin",
    detectSupervisor: () => "none",
    auditSupervisorInstall: async (attemptRepair: boolean) => {
      if (!attemptRepair) {
        return {
          supported: true,
          definition: null,
          fileState: installedOnDisk ? "present_matching" : "missing",
        };
      }
      installedOnDisk = true;
      return {
        supported: true,
        definition: null,
        fileState: "missing",
        repair: {
          action: "wrote_new_unit",
          changed: true,
          permissionDenied: false,
          requiresManualReload: false,
          detail: "Installed the launch agent so this computer restarts itself automatically going forward.",
        },
      };
    },
  });

  const result = await runtime.ensureSupervisorInstalled();

  assert.equal(result.repair_detail, "Installed the launch agent so this computer restarts itself automatically going forward.");
  assert.equal(result.status, "warn");
  assert.notEqual(result.repaired, true);
  // Re-validated detail reflects the NEW on-disk state (installedOnDisk is
  // now true) rather than the pre-repair "missing" detail — proof the
  // re-validation pass actually re-read state instead of trusting the
  // repair step's own claim.
  assert.match(result.detail, /take effect the next time/);
});

test("ensureSupervisorInstalled surfaces a permission-denied install honestly instead of silently skipping it", async () => {
  const runtime = new GatewayDoctorRuntime({
    checkpoints: { currentHealthState: () => "online" },
    getRequestedCapabilities: () => [],
    platform: "darwin",
    detectSupervisor: () => "none",
    auditSupervisorInstall: async () => ({
      supported: true,
      definition: null,
      fileState: "missing",
      repair: {
        action: "permission_denied",
        changed: false,
        permissionDenied: true,
        requiresManualReload: false,
        detail: "Could not write the unit — this computer needs elevated permissions (for example, sudo) to install automatic restart here.",
      },
    }),
  });

  const result = await runtime.ensureSupervisorInstalled();

  assert.equal(result.status, "warn", "a permission-denied install must stay warn, never silently report pass");
  assert.notEqual(result.repaired, true);
  assert.match(result.repair_detail!, /elevated permissions/);
});

test("router advertises gateway.doctor.run and dispatches it to the doctor executor", async () => {
  const doctorRuntime = new GatewayDoctorRuntime({
    checkpoints: { currentHealthState: () => "online" },
    getRequestedCapabilities: () => [],
    checks: [{ id: "a", label: "A", detect: async () => ({ status: "pass", detail: "ok" }) }],
  });
  const router = new GatewayCapabilityRouter(
    undefined,
    new PersonalChannelRuntimeRegistry(),
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    doctorRuntime,
  );
  assert.ok(router.supportedCapabilities().includes("gateway.doctor.run"));

  const invokeResult = await router.handleToolInvoke(makeInvokeFrame("gateway.doctor.run", {}));
  assert.equal(invokeResult.capability_id, "gateway.doctor.run");

  const interruptResult = await router.handleToolInterrupt({
    kind: "request",
    id: "req-doctor-2",
    type: "tool.interrupt",
    ts: new Date().toISOString(),
    payload: { run_id: "run-doctor-1", trace_id: "trace-1", workspace_id: "ws-1" },
  } as GatewayRequestEnvelope<GatewayToolInterruptPayload>);
  assert.equal(interruptResult.interrupted, false);
  assert.match(String(interruptResult.error), /not applicable/);
});

test("router without a doctor runtime rejects gateway.doctor.run with the standard unknown-executor error", async () => {
  const router = new GatewayCapabilityRouter(undefined, new PersonalChannelRuntimeRegistry());
  await assert.rejects(
    router.handleToolInvoke(makeInvokeFrame("gateway.doctor.run", {})),
    /No executor available for capability "gateway\.doctor\.run"/,
  );
});
