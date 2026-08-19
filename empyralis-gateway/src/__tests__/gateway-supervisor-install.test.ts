import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";

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

test("the GATEWAY's own unit names an absolute program, and its inputs cannot be otherwise", () => {
  // Companion to openclaw-supervisor-binary-path.test.ts, which exists because
  // the OpenClaw unit was rendered with the bare name "openclaw" and died with
  // launchd status 78 (EX_CONFIG) on every start, silently. The gateway's own
  // unit does NOT share that exposure, and this records why rather than
  // leaving the next reader to re-derive it:
  //
  //   execPath   process.execPath        — always absolute, per Node's docs
  //   entryPath  require.main?.filename  — module filenames are resolved
  //              || process.argv[1]      — Node resolves the entry to absolute
  //              || process.execPath     — absolute again
  //
  // So no refusal was added there; a guard would only ever fire on an input
  // Node cannot produce. The assertion below is on the REAL values this
  // process was launched with, so it would fail if that ever stopped holding.
  assert.ok(path.isAbsolute(process.execPath), process.execPath);
  const entryPath = require.main?.filename || process.argv[1] || process.execPath;
  assert.ok(path.isAbsolute(entryPath), entryPath);

  for (const platform of ["darwin", "linux"] as const) {
    const definition = resolveExpectedSupervisorUnit({
      platform,
      env: {},
      homeDir: "/Users/tester",
      execPath: process.execPath,
      entryPath,
      logDir: "/opt/empyralis/gateway/state/logs",
    });
    assert.ok(definition);
    const program =
      platform === "darwin"
        ? /<key>ProgramArguments<\/key>\s*<array>\s*<string>([^<]*)<\/string>/.exec(definition!.contents)?.[1]
        : /^ExecStart=(\S+)/m.exec(definition!.contents)?.[1];
    assert.ok(program, definition!.contents);
    assert.ok(path.isAbsolute(program!), `${platform}: ${program}`);
  }
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

// A launchd plist names its own log file, and launchd — not the program —
// opens it, before exec, without creating the parent directory. A missing
// parent is exit 78 (EX_CONFIG) and an empty universe: no log (the log is
// what failed), nothing in the gateway journal, just `launchctl list`
// showing 78 forever. That is how the Empyralis-managed OpenClaw transport
// silently never started on macOS on 2026-08-14. These two tests are the
// only thing standing between that and a repeat: the plist renders
// perfectly, the write succeeds, and the job still cannot start.
test("repairGatewaySupervisorUnit creates the log file's parent directory, not just the unit's", async () => {
  const definition: GatewaySupervisorUnitDefinition = {
    ...fakeDefinition("expected-contents"),
    logPath: "/var/state/logs/openclaw-empyralis.log",
  };
  const madeDirs: string[] = [];
  const result = await repairGatewaySupervisorUnit({
    definition,
    fileState: "missing",
    mkdir: async (dirPath) => {
      madeDirs.push(dirPath);
    },
    writeFile: async () => undefined,
  });
  assert.equal(result.action, "wrote_new_unit");
  assert.ok(
    madeDirs.includes("/var/state/logs"),
    `expected the log directory to be created, got ${JSON.stringify(madeDirs)}`,
  );
});

test("repairGatewaySupervisorUnit creates the log directory even when the unit file already matches (heals an installed-but-dead job)", async () => {
  const definition: GatewaySupervisorUnitDefinition = {
    ...fakeDefinition("expected-contents"),
    logPath: "/var/state/logs/openclaw-empyralis.log",
  };
  const madeDirs: string[] = [];
  let wrote = false;
  const result = await repairGatewaySupervisorUnit({
    definition,
    fileState: "present_matching",
    mkdir: async (dirPath) => {
      madeDirs.push(dirPath);
    },
    writeFile: async () => {
      wrote = true;
    },
  });
  assert.equal(result.action, "no_change");
  assert.equal(wrote, false, "a matching unit must still never be rewritten");
  assert.deepEqual(madeDirs, ["/var/state/logs"]);
});

test("repairGatewaySupervisorUnit skips log-directory creation when the unit redirects nowhere (systemd journals)", async () => {
  const definition = fakeDefinition("expected-contents");
  assert.equal(definition.logPath, undefined);
  const madeDirs: string[] = [];
  await repairGatewaySupervisorUnit({
    definition,
    fileState: "missing",
    mkdir: async (dirPath) => {
      madeDirs.push(dirPath);
    },
    writeFile: async () => undefined,
  });
  assert.deepEqual(madeDirs, [path.dirname(definition.unitPath)]);
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

// ---------------------------------------------------------------------------
// user-scope systemd (desktop app, MAN-356-adjacent): a normal logged-in
// Linux user has no path to /etc/systemd/system at all — resolveExpected
// SupervisorUnit must fall back to ~/.config/systemd/user when the caller's
// environment says so, and every registrar call must carry --user.

test("resolveExpectedSupervisorUnit stays system-scope on linux when EMPYRALIS_GATEWAY_SUPERVISOR_SCOPE is unset (no drift for existing boxes)", () => {
  const definition = resolveExpectedSupervisorUnit({
    platform: "linux",
    env: {},
    homeDir: "/home/tester",
    execPath: "/usr/bin/node",
    entryPath: "/opt/empyralis/agent-computer/current/gateway/dist/index.js",
    logDir: "/var/log/empyralis",
  });
  assert.ok(definition);
  assert.equal(definition!.unitPath, "/etc/systemd/system/empyralis-gateway.service");
  assert.equal(definition!.systemdScope, "system");
  assert.match(definition!.contents, /WantedBy=multi-user\.target/);
  assert.doesNotMatch(definition!.contents, /WantedBy=default\.target/);
});

test("resolveExpectedSupervisorUnit switches to a user unit under ~/.config/systemd/user when EMPYRALIS_GATEWAY_SUPERVISOR_SCOPE=user", () => {
  const definition = resolveExpectedSupervisorUnit({
    platform: "linux",
    env: { EMPYRALIS_GATEWAY_SUPERVISOR_SCOPE: "user" },
    homeDir: "/home/tester",
    execPath: "/usr/bin/node",
    entryPath: "/home/tester/.local/share/empyralis/gateway/dist/index.js",
    logDir: "/home/tester/.local/share/empyralis/logs",
  });
  assert.ok(definition);
  assert.equal(definition!.mode, "systemd");
  assert.equal(definition!.systemdScope, "user");
  assert.equal(
    definition!.unitPath,
    "/home/tester/.config/systemd/user/empyralis-gateway.service",
  );
  // No root, no User=/Group= — a --user manager already runs entirely as
  // the invoking user, and systemd rejects User= inside one.
  assert.doesNotMatch(definition!.contents, /^User=/m);
  assert.doesNotMatch(definition!.contents, /^Group=/m);
  assert.match(definition!.contents, /WantedBy=default\.target/);
  assert.doesNotMatch(definition!.contents, /WantedBy=multi-user\.target/);
});

test("resolveExpectedSupervisorUnit ignores EMPYRALIS_GATEWAY_SUPERVISOR_SCOPE on darwin (launchd has no scope concept)", () => {
  const definition = resolveExpectedSupervisorUnit({
    platform: "darwin",
    env: { EMPYRALIS_GATEWAY_SUPERVISOR_SCOPE: "user" },
    homeDir: "/Users/tester",
    execPath: "/usr/local/bin/node",
    entryPath: "/Users/tester/Library/Application Support/Empyralis/gateway/dist/index.js",
    logDir: "/Users/tester/Library/Logs/Empyralis",
  });
  assert.ok(definition);
  assert.equal(definition!.mode, "launchd");
  assert.equal(definition!.systemdScope, undefined);
});

test("createSystemdJobRegistrar passes --user before every subcommand for a user-scope definition", async () => {
  const calls: Array<{ command: string; args: string[] }> = [];
  const registrar = createSystemdJobRegistrar(async (command, args) => {
    calls.push({ command, args });
    return { stdout: "", stderr: "" };
  });
  const definition: GatewaySupervisorUnitDefinition = {
    mode: "systemd",
    unitPath: "/home/tester/.config/systemd/user/empyralis-gateway.service",
    name: "empyralis-gateway.service",
    systemdScope: "user",
    contents: "",
  };
  await registrar(definition);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], { command: "systemctl", args: ["--user", "daemon-reload"] });
  assert.deepEqual(calls[1], {
    command: "systemctl",
    args: ["--user", "enable", "empyralis-gateway.service"],
  });
});

test("createSystemdJobRegistrar omits --user for a system-scope (or scope-unset) definition — byte-identical to before", async () => {
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
  assert.deepEqual(calls[0], { command: "systemctl", args: ["daemon-reload"] });
  assert.deepEqual(calls[1], { command: "systemctl", args: ["enable", "empyralis-gateway.service"] });
});

test("auditAndRepairGatewaySupervisorInstall installs a user-scope unit end to end with no root required (mocked fs)", async () => {
  const written = new Map<string, string>();
  const mkdirCalls: string[] = [];
  const registerCalls: GatewaySupervisorUnitDefinition[] = [];

  const outcome = await auditAndRepairGatewaySupervisorInstall(
    {
      env: { EMPYRALIS_GATEWAY_SUPERVISOR_SCOPE: "user" },
      platform: "linux",
      homeDir: "/home/tester",
      execPath: "/usr/bin/node",
      entryPath: "/home/tester/.local/share/empyralis/gateway/dist/index.js",
      logDir: "/home/tester/.local/share/empyralis/logs",
      readFile: async () => {
        throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
      },
      writeFile: async (filePath, contents) => {
        written.set(filePath, contents);
      },
      mkdir: async (dirPath) => {
        mkdirCalls.push(dirPath);
      },
      registerJob: async (definition) => {
        registerCalls.push(definition);
      },
    },
    true,
  );

  assert.equal(outcome.supported, true);
  assert.equal(outcome.fileState, "missing");
  assert.equal(outcome.repair?.action, "wrote_new_unit");
  assert.equal(outcome.repair?.permissionDenied, false);
  assert.ok(written.has("/home/tester/.config/systemd/user/empyralis-gateway.service"));
  assert.ok(mkdirCalls.includes("/home/tester/.config/systemd/user"));
  assert.equal(registerCalls.length, 1);
  assert.equal(registerCalls[0].systemdScope, "user");
});
