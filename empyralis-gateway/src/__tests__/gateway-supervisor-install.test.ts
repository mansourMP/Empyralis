import test from "node:test";
import assert from "node:assert/strict";

import {
  resolveExpectedSupervisorUnit,
  auditGatewaySupervisorUnit,
  repairGatewaySupervisorUnit,
  auditAndRepairGatewaySupervisorInstall,
  createLaunchdJobRegistrar,
  createSystemdJobRegistrar,
  type GatewaySupervisorUnitDefinition,
} from "../update/gateway-supervisor-install";

// ---------------------------------------------------------------------------
// resolveExpectedSupervisorUnit: platform-specific unit shape

test("resolveExpectedSupervisorUnit produces a launchd plist under ~/Library/LaunchAgents on darwin", () => {
  const definition = resolveExpectedSupervisorUnit({
    platform: "darwin",
    env: {},
    homeDir: "/Users/tester",
    execPath: "/usr/local/bin/node",
    entryPath: "/opt/empyralis/gateway/current/gateway/dist/index.js",
    logDir: "/opt/empyralis/gateway/state/logs",
  });
  assert.ok(definition);
  assert.equal(definition!.mode, "launchd");
  assert.equal(definition!.unitPath, "/Users/tester/Library/LaunchAgents/ai.empyralis.agent-computer.plist");
  assert.equal(definition!.name, "ai.empyralis.agent-computer");
  assert.match(definition!.contents, /<key>Label<\/key>/);
  assert.match(definition!.contents, /<string>ai\.empyralis\.agent-computer<\/string>/);
  assert.match(definition!.contents, /<string>\/usr\/local\/bin\/node<\/string>/);
  assert.match(definition!.contents, /<string>\/opt\/empyralis\/gateway\/current\/gateway\/dist\/index\.js<\/string>/);
  assert.match(definition!.contents, /<key>RunAtLoad<\/key>\s*<true\/>/);
  assert.match(definition!.contents, /<key>KeepAlive<\/key>\s*<true\/>/);
});

test("resolveExpectedSupervisorUnit honors EMPYRALIS_LAUNCHD_LABEL override on darwin", () => {
  const definition = resolveExpectedSupervisorUnit({
    platform: "darwin",
    env: { EMPYRALIS_LAUNCHD_LABEL: "com.example.custom-gateway" },
    homeDir: "/Users/tester",
    execPath: "/usr/local/bin/node",
    entryPath: "/opt/empyralis/gateway/current/gateway/dist/index.js",
    logDir: "/opt/empyralis/gateway/state/logs",
  });
  assert.equal(definition!.name, "com.example.custom-gateway");
  assert.equal(definition!.unitPath, "/Users/tester/Library/LaunchAgents/com.example.custom-gateway.plist");
});

test("resolveExpectedSupervisorUnit produces a systemd unit under /etc/systemd/system on linux", () => {
  const definition = resolveExpectedSupervisorUnit({
    platform: "linux",
    env: {},
    homeDir: "/home/tester",
    execPath: "/usr/bin/node",
    entryPath: "/opt/empyralis/agent-computer/current/gateway/dist/index.js",
    logDir: "/var/log/empyralis",
  });
  assert.ok(definition);
  assert.equal(definition!.mode, "systemd");
  assert.equal(definition!.unitPath, "/etc/systemd/system/empyralis-gateway.service");
  assert.match(definition!.contents, /Restart=always/);
  assert.match(definition!.contents, /RestartSec=5/);
  assert.match(definition!.contents, /ExecStart=\/usr\/bin\/node \/opt\/empyralis\/agent-computer\/current\/gateway\/dist\/index\.js/);
});

test("resolveExpectedSupervisorUnit returns null on an unsupported platform (e.g. win32)", () => {
  const definition = resolveExpectedSupervisorUnit({
    platform: "win32",
    env: {},
    homeDir: "C:\\Users\\tester",
    execPath: "node.exe",
    entryPath: "C:\\gateway\\dist\\index.js",
    logDir: "C:\\gateway\\logs",
  });
  assert.equal(definition, null);
});

test("resolveExpectedSupervisorUnit rendering is deterministic for the same inputs", () => {
  const opts = {
    platform: "darwin" as const,
    env: {},
    homeDir: "/Users/tester",
    execPath: "/usr/local/bin/node",
    entryPath: "/opt/empyralis/gateway/current/gateway/dist/index.js",
    logDir: "/opt/empyralis/gateway/state/logs",
  };
  const a = resolveExpectedSupervisorUnit(opts);
  const b = resolveExpectedSupervisorUnit(opts);
  assert.equal(a!.contents, b!.contents);
});

// ---------------------------------------------------------------------------
// auditGatewaySupervisorUnit: missing / matching / drifted

function fakeDefinition(contents: string): GatewaySupervisorUnitDefinition {
  return {
    mode: "launchd",
    unitPath: "/Users/tester/Library/LaunchAgents/ai.empyralis.agent-computer.plist",
    name: "ai.empyralis.agent-computer",
    contents,
  };
}

test("auditGatewaySupervisorUnit reports 'missing' when the file doesn't exist (ENOENT)", async () => {
  const definition = fakeDefinition("expected-contents");
  const state = await auditGatewaySupervisorUnit({
    definition,
    readFile: async () => {
      const error = new Error("no such file") as NodeJS.ErrnoException;
      error.code = "ENOENT";
      throw error;
    },
  });
  assert.equal(state, "missing");
});

test("auditGatewaySupervisorUnit reports 'missing' for any other read failure too (e.g. EACCES)", async () => {
  const definition = fakeDefinition("expected-contents");
  const state = await auditGatewaySupervisorUnit({
    definition,
    readFile: async () => {
      const error = new Error("permission denied") as NodeJS.ErrnoException;
      error.code = "EACCES";
      throw error;
    },
  });
  assert.equal(state, "missing");
});

test("auditGatewaySupervisorUnit reports 'present_matching' when file content is byte-identical", async () => {
  const definition = fakeDefinition("expected-contents");
  const state = await auditGatewaySupervisorUnit({
    definition,
    readFile: async () => "expected-contents",
  });
  assert.equal(state, "present_matching");
});

test("auditGatewaySupervisorUnit reports 'present_drifted' when file content differs", async () => {
  const definition = fakeDefinition("expected-contents");
  const state = await auditGatewaySupervisorUnit({
    definition,
    readFile: async () => "stale-old-contents",
  });
  assert.equal(state, "present_drifted");
});

// ---------------------------------------------------------------------------
// repairGatewaySupervisorUnit: the conservative install/repair state machine

test("repairGatewaySupervisorUnit is a no-op when already matching", async () => {
  const definition = fakeDefinition("expected-contents");
  let wrote = false;
  const result = await repairGatewaySupervisorUnit({
    definition,
    fileState: "present_matching",
    mkdir: async () => undefined,
    writeFile: async () => {
      wrote = true;
    },
  });
  assert.equal(result.action, "no_change");
  assert.equal(result.changed, false);
  assert.equal(wrote, false);
});

test("repairGatewaySupervisorUnit writes a missing unit and registers it when a registrar is provided", async () => {
  const definition = fakeDefinition("expected-contents");
  const written: Record<string, string> = {};
  let registeredWith: GatewaySupervisorUnitDefinition | null = null;
  const result = await repairGatewaySupervisorUnit({
    definition,
    fileState: "missing",
    mkdir: async () => undefined,
    writeFile: async (filePath, contents) => {
      written[filePath] = contents;
    },
    registerJob: async (def) => {
      registeredWith = def;
    },
  });
  assert.equal(result.action, "wrote_new_unit");
  assert.equal(result.changed, true);
  assert.equal(result.requiresManualReload, false);
  assert.equal(written[definition.unitPath], "expected-contents");
  assert.equal(registeredWith, definition);
});

test("repairGatewaySupervisorUnit writes a missing unit but marks manual reload required when there is no registrar", async () => {
  const definition = fakeDefinition("expected-contents");
  const result = await repairGatewaySupervisorUnit({
    definition,
    fileState: "missing",
    mkdir: async () => undefined,
    writeFile: async () => undefined,
  });
  assert.equal(result.action, "wrote_new_unit");
  assert.equal(result.changed, true);
  assert.equal(result.requiresManualReload, true);
});

test("repairGatewaySupervisorUnit still reports the write as successful when registration itself throws", async () => {
  const definition = fakeDefinition("expected-contents");
  const result = await repairGatewaySupervisorUnit({
    definition,
    fileState: "missing",
    mkdir: async () => undefined,
    writeFile: async () => undefined,
    registerJob: async () => {
      throw new Error("launchctl not found");
    },
  });
  assert.equal(result.action, "wrote_new_unit");
  assert.equal(result.changed, true);
  assert.equal(result.requiresManualReload, true);
  assert.match(result.detail, /launchctl not found/);
});

test("repairGatewaySupervisorUnit rewrites a drifted unit's FILE ONLY and never invokes a registrar", async () => {
  const definition = fakeDefinition("expected-contents");
  let registrarCalled = false;
  const written: Record<string, string> = {};
  const result = await repairGatewaySupervisorUnit({
    definition,
    fileState: "present_drifted",
    mkdir: async () => undefined,
    writeFile: async (filePath, contents) => {
      written[filePath] = contents;
    },
    registerJob: async () => {
      registrarCalled = true;
    },
  });
  assert.equal(result.action, "rewrote_drifted_unit_file_only");
  assert.equal(result.changed, true);
  assert.equal(result.requiresManualReload, true);
  assert.equal(registrarCalled, false, "a drifted (possibly already-loaded) unit must never trigger an automatic reload/registration");
  assert.equal(written[definition.unitPath], "expected-contents");
});

test("repairGatewaySupervisorUnit reports permissionDenied on EACCES/EPERM and never throws", async () => {
  const definition = fakeDefinition("expected-contents");
  for (const code of ["EACCES", "EPERM"] as const) {
    const result = await repairGatewaySupervisorUnit({
      definition,
      fileState: "missing",
      mkdir: async () => undefined,
      writeFile: async () => {
        const error = new Error("denied") as NodeJS.ErrnoException;
        error.code = code;
        throw error;
      },
    });
    assert.equal(result.action, "permission_denied");
    assert.equal(result.permissionDenied, true);
    assert.equal(result.changed, false);
    assert.match(result.detail, /elevated permissions/);
  }
});

test("repairGatewaySupervisorUnit surfaces a non-permission write failure honestly, without throwing", async () => {
  const definition = fakeDefinition("expected-contents");
  const result = await repairGatewaySupervisorUnit({
    definition,
    fileState: "missing",
    mkdir: async () => undefined,
    writeFile: async () => {
      throw new Error("disk full");
    },
  });
  assert.equal(result.action, "permission_denied");
  assert.equal(result.permissionDenied, false);
  assert.match(result.detail, /disk full/);
});

// ---------------------------------------------------------------------------
// auditAndRepairGatewaySupervisorInstall: the orchestrator

test("auditAndRepairGatewaySupervisorInstall reports unsupported on a platform with no known supervisor", async () => {
  const outcome = await auditAndRepairGatewaySupervisorInstall(
    {
      platform: "win32",
      entryPath: "C:\\gateway\\dist\\index.js",
      logDir: "C:\\gateway\\logs",
    },
    false,
  );
  assert.equal(outcome.supported, false);
  assert.equal(outcome.definition, null);
  assert.equal(outcome.fileState, "not_applicable");
});

test("auditAndRepairGatewaySupervisorInstall audits without writing when attemptRepair is false", async () => {
  let readCalls = 0;
  let writeCalls = 0;
  const outcome = await auditAndRepairGatewaySupervisorInstall(
    {
      platform: "darwin",
      homeDir: "/Users/tester",
      execPath: "/usr/local/bin/node",
      entryPath: "/opt/empyralis/gateway/current/gateway/dist/index.js",
      logDir: "/opt/empyralis/gateway/state/logs",
      readFile: async () => {
        readCalls += 1;
        const error = new Error("no such file") as NodeJS.ErrnoException;
        error.code = "ENOENT";
        throw error;
      },
      writeFile: async () => {
        writeCalls += 1;
      },
    },
    false,
  );
  assert.equal(outcome.supported, true);
  assert.equal(outcome.fileState, "missing");
  assert.equal(outcome.repair, undefined);
  assert.equal(readCalls, 1);
  assert.equal(writeCalls, 0);
});

test("auditAndRepairGatewaySupervisorInstall installs a missing unit end to end when attemptRepair is true", async () => {
  const written: Record<string, string> = {};
  const registered: GatewaySupervisorUnitDefinition[] = [];
  const outcome = await auditAndRepairGatewaySupervisorInstall(
    {
      platform: "darwin",
      homeDir: "/Users/tester",
      execPath: "/usr/local/bin/node",
      entryPath: "/opt/empyralis/gateway/current/gateway/dist/index.js",
      logDir: "/opt/empyralis/gateway/state/logs",
      readFile: async () => {
        const error = new Error("no such file") as NodeJS.ErrnoException;
        error.code = "ENOENT";
        throw error;
      },
      writeFile: async (filePath, contents) => {
        written[filePath] = contents;
      },
      mkdir: async () => undefined,
      registerJob: async (def) => {
        registered.push(def);
      },
    },
    true,
  );
  assert.equal(outcome.supported, true);
  assert.equal(outcome.fileState, "missing");
  assert.ok(outcome.repair);
  assert.equal(outcome.repair!.action, "wrote_new_unit");
  assert.equal(outcome.repair!.changed, true);
  assert.equal(Object.keys(written).length, 1);
  assert.equal(registered.length, 1);
});

// ---------------------------------------------------------------------------
// Registrars: correct commands, no forced restart of a live job

test("createLaunchdJobRegistrar runs bootstrap + enable, never kickstart", async () => {
  const calls: Array<{ command: string; args: string[] }> = [];
  const registrar = createLaunchdJobRegistrar(501, async (command, args) => {
    calls.push({ command, args });
    return { stdout: "", stderr: "" };
  });
  const definition = fakeDefinition("plist-contents");
  await registrar(definition);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], { command: "launchctl", args: ["bootstrap", "gui/501", definition.unitPath] });
  assert.deepEqual(calls[1], { command: "launchctl", args: ["enable", "gui/501/ai.empyralis.agent-computer"] });
  assert.ok(!calls.some((call) => call.args.includes("kickstart")), "must never kickstart (would kill an already-running job)");
});

test("createLaunchdJobRegistrar is a no-op for a systemd definition", async () => {
  const calls: Array<{ command: string; args: string[] }> = [];
  const registrar = createLaunchdJobRegistrar(501, async (command, args) => {
    calls.push({ command, args });
    return { stdout: "", stderr: "" };
  });
  await registrar({ mode: "systemd", unitPath: "/etc/systemd/system/x.service", name: "x.service", contents: "" });
  assert.equal(calls.length, 0);
});

test("createSystemdJobRegistrar runs daemon-reload + enable, never start/restart", async () => {
  const calls: Array<{ command: string; args: string[] }> = [];
  const registrar = createSystemdJobRegistrar(async (command, args) => {
    calls.push({ command, args });
    return { stdout: "", stderr: "" };
  });
  const definition: GatewaySupervisorUnitDefinition = {
    mode: "systemd",
    unitPath: "/etc/systemd/system/empyralis-gateway.service",
    name: "empyralis-gateway.service",
    contents: "",
  };
  await registrar(definition);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], { command: "systemctl", args: ["daemon-reload"] });
  assert.deepEqual(calls[1], { command: "systemctl", args: ["enable", "empyralis-gateway.service"] });
  assert.ok(!calls.some((call) => call.args.includes("start") || call.args.includes("restart")), "must never start/restart (would touch an already-running unit)");
});
