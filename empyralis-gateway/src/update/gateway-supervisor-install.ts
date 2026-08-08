import { promises as fsp } from "fs";
import { execFile } from "child_process";
import os from "os";
import path from "path";
import { promisify } from "util";

/**
 * Self-repairing supervision (reliability audit §2, docs/design/reliability-
 * audit-1-gateway-health.md): the systemd unit scripts/install-agent-
 * computer.sh writes at install time is never audited or repaired
 * afterward, and no installer in this repo wires up macOS launchd at all —
 * detectGatewaySupervisor() (./gateway-restart-handoff.ts) can only tell
 * whether THIS live process happens to be running under a supervisor right
 * now (via the env hints systemd/launchd themselves set on the processes
 * they directly exec). It cannot tell whether a unit/plist file exists on
 * disk at all, whether it matches what we'd want, or fix either. This
 * module is the missing "detect the unit file itself, then conservatively
 * install/repair it" half, wired into the gateway.doctor.run capability's
 * "supervisor_presence" check (health/gateway-doctor.ts).
 *
 * Conceptually mirrors OpenClaw's doctor-gateway-services.ts (/Users/mansur/
 * openclaw/src/commands/doctor-gateway-services.ts — read-only reference,
 * nothing imported or copied): audit the installed definition against an
 * expected one, repair on drift or absence.
 *
 * Safety posture — deliberately more conservative than an interactive CLI
 * doctor, since this can run unattended from inside a live gateway process
 * with nobody watching:
 *
 *  - This module NEVER unloads, restarts, or kills anything already running
 *    under the supervisor. It never calls `launchctl bootout`/`kickstart`
 *    or `systemctl restart`/`stop` on an existing job — those would kill
 *    whatever process is currently registered under that label/unit, which
 *    could be this very gateway (or another healthy one). If the exact
 *    process calling this already reports itself supervised (detect
 *    GatewaySupervisor() returns non-"none"), callers should skip this
 *    module entirely — see gateway-doctor.ts's SUPERVISOR_PRESENCE_CHECK.
 *  - A MISSING unit is written to disk and, best-effort, registered with
 *    the OS supervisor (`launchctl bootstrap`+`enable` on macOS,
 *    `systemctl daemon-reload`+`enable` on Linux — deliberately no
 *    `--now`/`kickstart -k`: registering does not immediately replace a
 *    running process; a fresh RunAtLoad launch attempt is safe because the
 *    gateway's own single-instance PID lock (index.ts's
 *    acquireGatewayProcessLock) makes a second concurrent instance exit
 *    harmlessly, and the supervised copy only genuinely takes over once
 *    this process eventually exits — which is the intended end state).
 *  - A DRIFTED unit (file exists but its content differs from what this
 *    process would write) is REWRITTEN ON DISK ONLY. The caller is told
 *    `requiresManualReload: true` with the exact command a human would run
 *    to actually reload it — never attempted automatically here.
 *  - Any filesystem/exec failure (most commonly EACCES/EPERM writing to
 *    /etc/systemd/system without root) is caught and reported as
 *    `permissionDenied: true` with a plain-language explanation. This
 *    module never throws out of its repair path, never attempts sudo or
 *    any other privilege escalation, and never leaves the gateway process
 *    in a worse state than it found it — an install/repair that can't
 *    proceed is just honestly reported, never bricked or retried blindly.
 */

const execFileAsync = promisify(execFile);

/** Bounds how long `launchctl bootstrap`/`enable` or `systemctl daemon-
 *  reload`/`enable` may take (passed as execFile's own `timeout` option,
 *  mirroring health/service-inventory.ts's defaultRunCommand) AND, as a
 *  second independent backstop, how long repairGatewaySupervisorUnit()
 *  waits on ANY registerJob callback (including test/caller-injected ones
 *  that don't go through execFileAsync at all) before giving up on it —
 *  see REGISTER_JOB_TIMEOUT_MS below. Without either of these, a wedged
 *  launchctl/systemctl (stuck dbus, hung disk I/O) hangs the whole
 *  gateway.doctor.run(repair:true) capability invocation forever, since
 *  its only guard (health/gateway-doctor.ts) is a try/catch that can't
 *  help a promise that never settles. */
const OS_REGISTRATION_EXEC_TIMEOUT_MS = 15_000;

/** Slightly above OS_REGISTRATION_EXEC_TIMEOUT_MS so the exec-level timeout
 *  (when the registerJob callback IS execFileAsync-based, i.e. the real
 *  production registrars below) gets a chance to fire and produce its own
 *  clear error first; this is the outer backstop for any registerJob,
 *  including ones that don't call execFile at all. */
const REGISTER_JOB_TIMEOUT_MS = OS_REGISTRATION_EXEC_TIMEOUT_MS + 5_000;

/** Races `promise` against a timer; rejects with a clear, recognizable
 *  error if the timer wins. Never leaves a dangling handle either way —
 *  the timer is always cleared, and it's unref'd so it can't itself keep
 *  the process alive while waiting. */
function withTimeout<T>(promise: Promise<T>, timeoutMs: number, label: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`${label} timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    timer.unref?.();
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

export type GatewaySupervisorMode = "launchd" | "systemd";

export interface GatewaySupervisorUnitDefinition {
  mode: GatewaySupervisorMode;
  /** Absolute path of the unit/plist file this platform expects. */
  unitPath: string;
  /** Fully-rendered file contents this module would write. Drift detection
   *  is a plain string-equality check against this — safe because this
   *  module fully controls rendering (deterministic output for the same
   *  inputs), so no XML/ini parser is needed to answer "does the installed
   *  file match what we'd write today". */
  contents: string;
  /** Human-readable unit/label name, for logging and launchctl/systemctl
   *  target arguments. */
  name: string;
}

export interface ResolveExpectedSupervisorUnitOptions {
  platform: NodeJS.Platform;
  env: NodeJS.ProcessEnv;
  homeDir: string;
  execPath: string;
  /** Absolute path to the dist/index.js this running process was actually
   *  launched from — resolved by the caller, never guessed here. */
  entryPath: string;
  /** Directory launchd-adjacent stdout/stderr logs should land in. */
  logDir: string;
}

const DEFAULT_LAUNCHD_LABEL = "ai.empyralis.agent-computer";
const DEFAULT_SYSTEMD_UNIT_NAME = "empyralis-gateway.service";

function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * What a supervised program looks like, independent of WHICH program it is.
 *
 * Generalized 2026-08-08 for CHANNEL-ADOPTION-PLAN.md step 4: the co-located
 * OpenClaw gateway needs exactly this supervision (start on login, restart on
 * crash, audited-and-repaired unit file) and there must not be a second
 * implementation of it. The renderers, the drift audit
 * (auditGatewaySupervisorUnit), the repair (repairGatewaySupervisorUnit) and
 * both OS registrars below are shared verbatim; the only thing OpenClaw
 * supplies is a different definition — see openclaw/provisioning/
 * openclaw-supervisor-unit.ts.
 *
 * `environment` is new and exists for OpenClaw specifically: launchd/systemd
 * do NOT inherit a login shell's environment, so the child's env is whatever
 * the unit says it is. That is the strongest possible place to guarantee no
 * model-provider credential reaches OpenClaw — there is no value present to
 * drift. Omitting it renders byte-identically to the pre-generalization
 * output, so the Empyralis gateway's own unit is unchanged.
 */
export interface SupervisedProgramDefinition {
  /** launchd Label / systemd unit file name. */
  label: string;
  /** argv, already resolved to absolute paths. */
  programArguments: string[];
  workingDirectory: string;
  /** launchd only (systemd journals on its own). */
  logPath: string;
  /** Exact environment for the child. Absent = inherit whatever the
   *  supervisor provides. */
  environment?: Record<string, string>;
  /** systemd `Description=`. */
  description?: string;
}

export function renderLaunchAgentPlist(opts: SupervisedProgramDefinition): string {
  const cwdXml = xmlEscape(opts.workingDirectory);
  const logXml = xmlEscape(opts.logPath);
  const labelXml = xmlEscape(opts.label);
  const argsXml = opts.programArguments
    .map((arg) => `    <string>${xmlEscape(arg)}</string>`)
    .join("\n");
  const envKeys = Object.keys(opts.environment ?? {}).sort();
  const envXml =
    envKeys.length > 0
      ? `  <key>EnvironmentVariables</key>\n  <dict>\n${envKeys
          .map(
            (key) =>
              `    <key>${xmlEscape(key)}</key>\n    <string>${xmlEscape(String(opts.environment?.[key] ?? ""))}</string>`,
          )
          .join("\n")}\n  </dict>\n`
      : "";
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${labelXml}</string>
  <key>ProgramArguments</key>
  <array>
${argsXml}
  </array>
  <key>WorkingDirectory</key>
  <string>${cwdXml}</string>
${envXml}  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${logXml}</string>
  <key>StandardErrorPath</key>
  <string>${logXml}</string>
</dict>
</plist>
`;
}

export function renderSystemdUnit(opts: SupervisedProgramDefinition): string {
  const envLines = Object.keys(opts.environment ?? {})
    .sort()
    .map((key) => `Environment=${key}=${String(opts.environment?.[key] ?? "")}`)
    .join("\n");
  return `[Unit]
Description=${opts.description ?? "Empyralis Agent Computer Gateway"}
Documentation=https://empyralis.ai
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${opts.workingDirectory}
ExecStart=${opts.programArguments.join(" ")}
${envLines ? `${envLines}\n` : ""}Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
`;
}

/** Computes what this platform's supervisor unit SHOULD contain for this
 *  running gateway. Returns null on a platform neither launchd nor systemd
 *  applies to (e.g. win32) — callers must treat that as "unsupported", not
 *  as a failure. */
export function resolveExpectedSupervisorUnit(
  opts: ResolveExpectedSupervisorUnitOptions,
): GatewaySupervisorUnitDefinition | null {
  const workingDirectory = path.dirname(opts.entryPath);
  if (opts.platform === "darwin") {
    const label = String(opts.env.EMPYRALIS_LAUNCHD_LABEL || "").trim() || DEFAULT_LAUNCHD_LABEL;
    const unitPath = path.join(opts.homeDir, "Library", "LaunchAgents", `${label}.plist`);
    const logPath = path.join(opts.logDir, "gateway-launchd.log");
    return {
      mode: "launchd",
      unitPath,
      name: label,
      contents: renderLaunchAgentPlist({
        label,
        programArguments: [opts.execPath, opts.entryPath],
        workingDirectory,
        logPath,
      }),
    };
  }
  if (opts.platform === "linux") {
    const unitName = String(opts.env.EMPYRALIS_SYSTEMD_UNIT || "").trim() || DEFAULT_SYSTEMD_UNIT_NAME;
    const unitPath = path.join("/etc/systemd/system", unitName);
    return {
      mode: "systemd",
      unitPath,
      name: unitName,
      contents: renderSystemdUnit({
        label: unitName,
        programArguments: [opts.execPath, opts.entryPath],
        workingDirectory,
        logPath: path.join(opts.logDir, "gateway.log"),
      }),
    };
  }
  return null;
}

export type GatewaySupervisorFileState = "missing" | "present_matching" | "present_drifted";

export interface GatewaySupervisorAuditOptions {
  definition: GatewaySupervisorUnitDefinition;
  readFile: (filePath: string) => Promise<string>;
}

/** Reads the unit/plist file on disk (if any) and compares it verbatim
 *  against what this process would write today. Any read failure other
 *  than "file doesn't exist" (e.g. EACCES on an existing-but-unreadable
 *  file) is treated the same as "missing" for audit purposes — repair(),
 *  if invoked, will surface the real underlying reason when it attempts the
 *  write. */
export async function auditGatewaySupervisorUnit(
  opts: GatewaySupervisorAuditOptions,
): Promise<GatewaySupervisorFileState> {
  let existing: string;
  try {
    existing = await opts.readFile(opts.definition.unitPath);
  } catch {
    return "missing";
  }
  return existing === opts.definition.contents ? "present_matching" : "present_drifted";
}

export interface GatewaySupervisorRepairOptions {
  definition: GatewaySupervisorUnitDefinition;
  fileState: GatewaySupervisorFileState;
  mkdir: (dirPath: string) => Promise<void>;
  writeFile: (filePath: string, contents: string) => Promise<void>;
  /** Only invoked for a fresh ("missing" -> written) install — never for a
   *  drifted rewrite. See module doc comment for why. Absence of this
   *  callback (or a throw from it) still counts the on-disk write as a
   *  success; only the OS-level registration step is best-effort. */
  registerJob?: (definition: GatewaySupervisorUnitDefinition) => Promise<void>;
}

export interface GatewaySupervisorRepairResult {
  action: "no_change" | "wrote_new_unit" | "rewrote_drifted_unit_file_only" | "permission_denied";
  changed: boolean;
  permissionDenied: boolean;
  requiresManualReload: boolean;
  detail: string;
}

export async function repairGatewaySupervisorUnit(
  opts: GatewaySupervisorRepairOptions,
): Promise<GatewaySupervisorRepairResult> {
  if (opts.fileState === "present_matching") {
    return {
      action: "no_change",
      changed: false,
      permissionDenied: false,
      requiresManualReload: false,
      detail: `${opts.definition.name} is already installed and matches the expected definition.`,
    };
  }

  try {
    await opts.mkdir(path.dirname(opts.definition.unitPath));
    await opts.writeFile(opts.definition.unitPath, opts.definition.contents);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException | undefined)?.code;
    const message = error instanceof Error ? error.message : String(error);
    const permissionDenied = code === "EACCES" || code === "EPERM";
    return {
      action: "permission_denied",
      changed: false,
      permissionDenied,
      requiresManualReload: false,
      detail: permissionDenied
        ? `Could not write ${opts.definition.unitPath} — this computer needs elevated permissions (for example, sudo) to install automatic restart here.`
        : `Could not write ${opts.definition.unitPath}: ${message}`,
    };
  }

  if (opts.fileState === "missing") {
    if (opts.registerJob) {
      try {
        // Bounded regardless of what registerJob actually does internally
        // -- the production registrars (createLaunchdJobRegistrar/
        // createSystemdJobRegistrar) already pass their own execFile
        // `timeout`, but this outer bound is what actually protects a
        // wedged/never-settling registerJob (a hung launchctl/systemctl
        // process execFile's own timeout somehow doesn't catch, or any
        // other registerJob implementation) from hanging this repair --
        // and therefore gateway.doctor.run(repair:true) -- forever. See
        // gateway-supervisor-install-repair-hang.test.ts.
        await withTimeout(
          opts.registerJob(opts.definition),
          REGISTER_JOB_TIMEOUT_MS,
          `Registering ${opts.definition.unitPath} with the OS supervisor`,
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          action: "wrote_new_unit",
          changed: true,
          permissionDenied: false,
          requiresManualReload: true,
          detail: `Installed ${opts.definition.unitPath} but could not register it with the OS automatically (${message}). It will take effect after a manual reload or the next restart.`,
        };
      }
      return {
        action: "wrote_new_unit",
        changed: true,
        permissionDenied: false,
        requiresManualReload: false,
        detail: `Installed ${opts.definition.unitPath} so this computer restarts itself automatically going forward.`,
      };
    }
    return {
      action: "wrote_new_unit",
      changed: true,
      permissionDenied: false,
      requiresManualReload: true,
      detail: `Installed ${opts.definition.unitPath} so this computer restarts itself automatically going forward. It will take effect after a manual reload or the next restart.`,
    };
  }

  // present_drifted: file rewritten, but a currently-loaded job (if any) is
  // deliberately left running untouched — see module doc comment.
  const reloadHint = opts.definition.mode === "launchd"
    ? "launchctl bootout, then launchctl bootstrap"
    : "systemctl daemon-reload && systemctl restart";
  return {
    action: "rewrote_drifted_unit_file_only",
    changed: true,
    permissionDenied: false,
    requiresManualReload: true,
    detail: `Updated ${opts.definition.unitPath} to the expected definition. If this computer is already running under it, reload it manually (${reloadHint}) to pick up the change.`,
  };
}

/** Best-effort macOS registrar: `launchctl bootstrap` + `enable`. No
 *  `kickstart` — see module doc comment for why this module never forces a
 *  restart of an existing job. Injectable `exec` for tests; production
 *  callers get the real `child_process.execFile` via the default. */
export function createLaunchdJobRegistrar(
  uid: number,
  exec: (command: string, args: string[]) => Promise<unknown> = (command, args) =>
    execFileAsync(command, args, { timeout: OS_REGISTRATION_EXEC_TIMEOUT_MS }),
): (definition: GatewaySupervisorUnitDefinition) => Promise<void> {
  return async (definition) => {
    if (definition.mode !== "launchd") {
      return;
    }
    const target = `gui/${uid}`;
    await exec("launchctl", ["bootstrap", target, definition.unitPath]);
    await exec("launchctl", ["enable", `${target}/${definition.name}`]);
  };
}

/** Best-effort Linux registrar: `systemctl daemon-reload` + `enable` (never
 *  `--now`/`start` — same reasoning as the launchd registrar above). */
export function createSystemdJobRegistrar(
  exec: (command: string, args: string[]) => Promise<unknown> = (command, args) =>
    execFileAsync(command, args, { timeout: OS_REGISTRATION_EXEC_TIMEOUT_MS }),
): (definition: GatewaySupervisorUnitDefinition) => Promise<void> {
  return async (definition) => {
    if (definition.mode !== "systemd") {
      return;
    }
    await exec("systemctl", ["daemon-reload"]);
    await exec("systemctl", ["enable", definition.name]);
  };
}

export interface RunGatewaySupervisorInstallOptions {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  homeDir?: string;
  execPath?: string;
  entryPath: string;
  logDir: string;
  readFile?: (filePath: string) => Promise<string>;
  writeFile?: (filePath: string, contents: string) => Promise<void>;
  mkdir?: (dirPath: string) => Promise<void>;
  registerJob?: (definition: GatewaySupervisorUnitDefinition) => Promise<void>;
}

export interface GatewaySupervisorInstallOutcome {
  supported: boolean;
  definition: GatewaySupervisorUnitDefinition | null;
  fileState: GatewaySupervisorFileState | "not_applicable";
  repair?: GatewaySupervisorRepairResult;
}

/** The single entry point gateway-doctor.ts's supervisor_presence check
 *  calls: audits the unit/plist on disk against the expected definition,
 *  and — only when attemptRepair is true — installs a missing one or
 *  rewrites (file-only) a drifted one. Never throws; an unsupported
 *  platform or an inaccessible unit path is reported in the returned
 *  outcome, not as a rejected promise. */
export async function auditAndRepairGatewaySupervisorInstall(
  opts: RunGatewaySupervisorInstallOptions,
  attemptRepair: boolean,
): Promise<GatewaySupervisorInstallOutcome> {
  const platform = opts.platform ?? process.platform;
  const env = opts.env ?? process.env;
  const homeDir = opts.homeDir ?? os.homedir();
  const execPath = opts.execPath ?? process.execPath;
  const readFile = opts.readFile ?? ((filePath: string) => fsp.readFile(filePath, "utf8"));
  const writeFile =
    opts.writeFile ??
    (async (filePath: string, contents: string) => {
      await fsp.writeFile(filePath, contents, { mode: 0o644 });
    });
  const mkdir = opts.mkdir ?? (async (dirPath: string) => { await fsp.mkdir(dirPath, { recursive: true }); });

  const definition = resolveExpectedSupervisorUnit({
    platform,
    env,
    homeDir,
    execPath,
    entryPath: opts.entryPath,
    logDir: opts.logDir,
  });
  if (!definition) {
    return { supported: false, definition: null, fileState: "not_applicable" };
  }

  const fileState = await auditGatewaySupervisorUnit({ definition, readFile });
  if (!attemptRepair) {
    return { supported: true, definition, fileState };
  }

  const repair = await repairGatewaySupervisorUnit({
    definition,
    fileState,
    mkdir,
    writeFile,
    registerJob: opts.registerJob,
  });
  return { supported: true, definition, fileState, repair };
}
