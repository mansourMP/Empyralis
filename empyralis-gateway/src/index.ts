import { promises as fs } from "fs";
import path from "path";

import { loadGatewayConfig, openClawGatewayPortFromUrl } from "./config";
import type { GatewayConfig } from "./config";
import { GatewayWsClient } from "./cloud/ws-client";
import { resolveDeviceIdentity } from "./pairing/device-identity";
import type { GatewayDeviceIdentity } from "./pairing/device-identity";
import { GatewayTokenStore } from "./pairing/token-store";
import type { GatewayTokenState } from "./pairing/token-store";
import { GatewayStateDb } from "./state/db";
import { GatewayJournal } from "./state/journal";
import { GatewayOutbox } from "./state/outbox";
import { GatewayCheckpoints } from "./state/checkpoints";
import { buildRuntimeMetadata } from "./runtime/runtime-metadata";
import type { GatewayRuntimeMetadata } from "./runtime/runtime-metadata";
// ARCHIVED (Phase U1): GatewaySupervisorClient removed.
// The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.
import { GatewayCapabilityRouter } from "./supervisor/capability-router";
import { GatewayRegistrationError } from "./cloud/registration-failure";
import { PersonalChannelRuntimeRegistry } from "./channels/personal-runtime";
import { GatewayBrowserWorker } from "./browser/worker";
import { GatewayBrowserRuntime } from "./browser/runtime";
import { GatewayShellRuntime } from "./shell/runtime";
import { GatewayLLMRuntime } from "./llm/runtime";
import { OpenClawInboundListener, OPENCLAW_INBOUND_PATH } from "./openclaw/inbound-listener";
import { setOpenClawTransportEnabled } from "./openclaw/capabilities";
import { OpenClawGatewayClient } from "./openclaw/openclaw-gateway-client";
import { buildOpenClawPersonalChannelRuntimes } from "./openclaw/outbound-runtime";
import { OpenClawChannelSetupRuntime } from "./openclaw/provisioning/openclaw-channel-setup";
import { resolveOpenClawLocalSecrets } from "./openclaw/openclaw-local-secrets";
import { buildOpenClawProvisioningRuntime } from "./openclaw/provisioning/openclaw-provisioning-runtime";
import { GatewayCliSetupRuntime } from "./llm/cli-setup-runtime";
import { GatewaySelfUpdateRuntime } from "./update/gateway-self-update-runtime";
import { GatewayRestartRuntime } from "./update/gateway-restart-runtime";
import { readAndClearPendingGatewayRestartMarker } from "./update/gateway-restart-pending";
import { resolveGatewayLaunchEntrypoint } from "./update/gateway-launch-path";
import {
  auditAndRepairGatewaySupervisorInstall,
  createLaunchdJobRegistrar,
  createSystemdJobRegistrar,
} from "./update/gateway-supervisor-install";
import {
  computeGatewayBuildFingerprint,
  isGatewayBuildFingerprint,
  resolveRunningGatewayDistDir,
} from "./update/gateway-build-fingerprint";
import { classifyGatewayLaunchUpdatability } from "./update/gateway-launch-updatability";
import { ensureGatewayLaunchRepair } from "./update/gateway-launch-repair";
import { GatewayDoctorRuntime, type GatewayDoctorCheckResult, type GatewayDoctorRunResult } from "./health/gateway-doctor";
import { collectPassiveInventorySnapshot } from "./health/service-inventory";
import { setCliSetupLocallyEnabled, setShellFullAccessLocallyEnabled } from "./runtime/desktop-permissions";

const GATEWAY_VERSION = "0.1.0";

/**
 * A small, explicit denylist of error signatures that mean the process's
 * own memory/call-stack state may be corrupted — continuing to run risks
 * doing more damage (e.g. writing bad state to disk) than a clean restart
 * would. Everything else that reaches the crash guards below is treated as
 * recoverable and logged-not-exited; see installProcessCrashGuards().
 */
const FATAL_PROCESS_ERROR_PATTERNS: RegExp[] = [
  /maximum call stack size exceeded/i,
  /heap out of memory/i,
  /allocation failed/i,
];

export function isFatalProcessError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return FATAL_PROCESS_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}

/**
 * Last-resort, process-wide safety net. Today there is NO
 * `process.on("uncaughtException"/"unhandledRejection")` handler anywhere
 * in this codebase, so any error that slips past a local mitigation — the
 * two documented, individually-patched examples are the still-connecting
 * socket close race (cloud/ws-client.ts openSocket(), ~554-563) and the
 * null-socket .send() race (cloud/ws-client.ts dispatchRequestFrame(),
 * ~700-711) — crashes the whole gateway process with no supervisor on most
 * boxes to bring it back (see docs/design/reliability-audit-1-gateway-
 * health.md §2: non-systemd hosts get an explicit "no restart supervision"
 * warning, and even systemd is only wired up by the install script, never
 * verified afterward).
 *
 * Node's own guidance for `uncaughtException` is "always exit" because the
 * process may be in an inconsistent state — but for this process, the
 * dominant real-world cause of an uncaught error/rejection is a transient
 * WebSocket or network fault (exactly the two examples above), which is
 * NOT a corrupted-process condition: `GatewayWsClient.run()`'s reconnect
 * loop (cloud/ws-client.ts) and `HeartbeatLoop` (cloud/heartbeat.ts) are
 * fully intact and able to keep going. Exiting on every one of those would
 * just reintroduce the crash this file exists to stop. So: log with
 * context and keep the process alive by default, and only exit for the
 * narrow, explicit set of signatures in FATAL_PROCESS_ERROR_PATTERNS above
 * (stack/heap corruption) where continuing is genuinely unsafe — those
 * exit cleanly so the systemd `Restart=always` unit (scripts/install-
 * agent-computer.sh write_systemd_units()) can bring up a fresh process.
 *
 * unhandledRejection is never treated as fatal: a rejected promise with no
 * listener cannot corrupt call-stack/heap state the way a thrown exception
 * potentially can, so there is no scenario here where exiting is safer
 * than logging and continuing.
 */
/**
 * The actual decision logic behind installProcessCrashGuards()'s two
 * listeners, factored out so it's unit-testable without ever touching the
 * real process-wide "uncaughtException"/"unhandledRejection" events —
 * node:test installs its own listeners for exactly those events to detect
 * a test crashing the process, and manually emitting them from a test
 * collides with that (the runner's own listener runs first and re-throws
 * before ours would even see it). Calling this function directly sidesteps
 * that entirely.
 */
export function handleProcessCrashCondition(
  kind: "uncaughtException" | "unhandledRejection",
  error: unknown,
): void {
  const detail = error instanceof Error ? error.stack || error.message : String(error);
  // eslint-disable-next-line no-console -- this is the last-resort log
  // path; there is no guarantee the journal/db are usable at this point.
  console.error(
    `[gateway] ${kind} at ${new Date().toISOString()} (pid ${process.pid}, uptime ${Math.round(process.uptime())}s): ${detail}`,
  );
  if (kind === "uncaughtException" && isFatalProcessError(error)) {
    console.error(
      "[gateway] uncaughtException classified as fatal (stack/memory corruption signature) — exiting for supervisor restart instead of continuing in a possibly-corrupted state.",
    );
    process.exitCode = 1;
    process.exit(1);
    return;
  }
  // Non-fatal (or any unhandledRejection, which is never fatal): fall
  // through and keep running. The reconnect loop and heartbeat timers are
  // unaffected by an exception caught here.
}

export function installProcessCrashGuards(): void {
  process.on("uncaughtException", (error) => {
    handleProcessCrashCondition("uncaughtException", error);
  });
  process.on("unhandledRejection", (reason) => {
    handleProcessCrashCondition("unhandledRejection", reason);
  });
}

async function acquireGatewayProcessLock(stateDir: string): Promise<() => Promise<void>> {
  const lockPath = path.join(stateDir, "gateway.lock");
  await fs.mkdir(stateDir, { recursive: true });
  const payload = JSON.stringify(
    {
      pid: process.pid,
      startedAt: new Date().toISOString(),
    },
    null,
    2,
  );

  try {
    const handle = await fs.open(lockPath, "wx");
    await handle.writeFile(payload, "utf8");
    await handle.close();
  } catch (error) {
    const code = (error as NodeJS.ErrnoException | undefined)?.code;
    if (code !== "EEXIST") {
      throw error;
    }
    const existing = await readExistingLock(lockPath);
    const existingPid = Number(existing?.pid || 0);
    if (existingPid > 0 && processAlive(existingPid)) {
      throw new Error(`Empyralis gateway is already running for ${stateDir} (pid ${existingPid}).`);
    }
    await fs.rm(lockPath, { force: true });
    const handle = await fs.open(lockPath, "wx");
    await handle.writeFile(payload, "utf8");
    await handle.close();
  }

  let released = false;
  return async () => {
    if (released) {
      return;
    }
    released = true;
    await fs.rm(lockPath, { force: true });
  };
}

async function readExistingLock(lockPath: string): Promise<{ pid?: number } | null> {
  try {
    const raw = await fs.readFile(lockPath, "utf8");
    const payload = JSON.parse(raw) as { pid?: number };
    return typeof payload === "object" && payload ? payload : null;
  } catch {
    return null;
  }
}

function processAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * The installer writes EMPYRALIS_PAIRING_TOKEN into a systemd EnvironmentFile that
 * survives every restart, and never clears it after a successful pairing (it can't —
 * the gateway service only has read access to that config path). Without this check,
 * every crash/reboot/redeploy re-attempts pairing with the now-consumed token, throws,
 * and crash-loops forever instead of reconnecting with the credentials it already has.
 */
export function shouldAttemptPairing(
  pairingToken: string | undefined,
  storedGatewayToken: string | undefined,
): boolean {
  return Boolean(pairingToken) && !storedGatewayToken;
}

/**
 * sysexits.h EX_CONFIG: "something is wrong with the configuration files
 * and the situation is not recoverable by trying again." A consumed,
 * revoked, or expired pairing token is exactly that — the pairing token is
 * effectively this box's boot-time configuration, and no amount of
 * restarting fixes it.
 *
 * Chosen specifically because scripts/install-agent-computer.sh's systemd
 * unit lists this exact value in RestartPreventExitStatus, so exiting with
 * it is what actually stops the restart loop, not merely slows it down.
 * Every OTHER startup failure in this file (network errors, transient 5xx
 * registration responses, and any genuinely unexpected crash) still exits
 * with the generic exitCode=1 below, which systemd keeps retrying exactly
 * as before — this code is deliberately narrow to the one class of failure
 * that can never succeed by retrying.
 */
export const EXIT_PERMANENT_REGISTRATION_FAILURE = 78;

/**
 * Thrown instead of a plain Error when startup fails in a way retrying can
 * never fix, so the require.main catch below can exit with a code systemd
 * is configured to NOT restart on, instead of the generic exitCode=1 every
 * transient startup failure still uses.
 */
export class GatewayPermanentStartupFailure extends Error {
  readonly exitCode: number;
  constructor(message: string, exitCode: number) {
    super(message);
    this.name = "GatewayPermanentStartupFailure";
    this.exitCode = exitCode;
  }
}

/**
 * Best-effort phone-home for a registration failure this box will never
 * recover from by retrying. Reuses the SAME beacon endpoint install-agent-
 * computer.sh's own report_beacon() already POSTs to
 * (POST /gateway/provisioning-events, MAN-121) — it is keyed by pairing
 * token, unauthenticated, and matches independently of whether that token
 * is still "pending" in gateway_pairing_intents, so it works precisely in
 * this scenario where the token has already been consumed/expired/revoked.
 *
 * For a VPS this product provisioned, a `terminal: true` beacon on this
 * phase flips vps_provisioning_service._resolved_record_status() to
 * "failed" on its very next poll (VPS_CONNECT_POLL_INTERVAL_SECONDS = 15s)
 * instead of waiting out the full VPS_CONNECT_TIMEOUT_SECONDS (20 minutes) —
 * turning "Timed out after 20 minutes waiting for the agent computer to
 * connect" (no reason given) into the real reason, within ~15-30s. For a
 * box paired through the manual "connect your own computer" flow (no VPS
 * record exists), the same beacon call is silently discarded server-side —
 * see record_vps_install_event()'s own "Returns None when the token matches
 * no record" contract — so this call is always safe to make.
 *
 * Best-effort like report_beacon() itself: an unreachable control plane
 * must never block this box from finishing its own local failure
 * bookkeeping or exiting cleanly.
 */
async function reportPermanentRegistrationFailureBeacon(
  config: Pick<GatewayConfig, "apiBaseUrl" | "pairingToken">,
  error: GatewayRegistrationError,
): Promise<void> {
  const pairingToken = config.pairingToken;
  if (!pairingToken || !config.apiBaseUrl) {
    return;
  }
  const message = `Gateway registration was permanently refused (${error.code}${
    error.status ? `, HTTP ${error.status}` : ""
  }): ${error.detail || error.message}`.slice(0, 2000);
  const controller = new AbortController();
  const timeoutHandle = setTimeout(() => controller.abort(), 15_000);
  try {
    await fetch(`${config.apiBaseUrl}/gateway/provisioning-events`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        pairing_token: pairingToken,
        phase: "gateway_registration",
        message,
        terminal: true,
        kind: "problem",
      }),
      signal: controller.signal,
    });
  } catch {
    // Best-effort — see doc comment above.
  } finally {
    clearTimeout(timeoutHandle);
  }
}

/**
 * Writes the local, human-discoverable half of a permanent registration
 * failure: a structured state file under stateDir (readable by a human who
 * SSHes into the box, or by a future local diagnostic that wants it) and a
 * journal entry, then best-effort phones the failure home. Never throws —
 * every step here is best-effort so a disk or network hiccup while
 * RECORDING the failure can never prevent the process from actually
 * stopping (see attemptGatewayPairing below).
 */
async function recordPermanentRegistrationFailure(
  db: Pick<GatewayStateDb, "writeJson" | "filePath">,
  journal: Pick<GatewayJournal, "append">,
  config: Pick<GatewayConfig, "apiBaseUrl" | "pairingToken">,
  identity: Pick<GatewayDeviceIdentity, "gatewayId" | "deviceId">,
  error: GatewayRegistrationError,
): Promise<void> {
  const record = {
    failedAt: new Date().toISOString(),
    code: error.code,
    httpStatus: error.status ?? null,
    detail: error.detail || error.message,
    gatewayId: identity.gatewayId,
    deviceId: identity.deviceId,
  };
  console.error(
    `[gateway] pairing registration failed permanently (${error.code}${
      error.status ? `, http ${error.status}` : ""
    }): ${record.detail} — this box will NOT keep retrying with the same pairing token. ` +
      `See ${db.filePath("registration_failure.json")}. Re-pair this device with a fresh pairing token to bring it online.`,
  );
  await db.writeJson("registration_failure.json", record).catch(() => undefined);
  await journal.append("system", "gateway.registration.permanent_failure", record).catch(() => undefined);
  await reportPermanentRegistrationFailureBeacon(config, error);
}

/**
 * The pairing decision main() used to inline directly. Pulled out, same
 * spirit as shouldAttemptPairing() above, so the permanent-vs-retryable
 * branch can be exercised in a test without running the rest of main()'s
 * startup sequence.
 *
 * Resolves normally on success OR on any of the pre-existing
 * skip/adopt-token branches (unchanged behavior). Throws
 * GatewayPermanentStartupFailure when registerFromPairing fails in a way
 * retrying can never fix (after recording it — see
 * recordPermanentRegistrationFailure above); rethrows the original error
 * unchanged for anything retryable (network errors, 5xx, 429) or
 * unclassified, so the existing exitCode=1 + systemd-restart path is
 * exactly what it was before this change.
 */
export async function attemptGatewayPairing(params: {
  client: Pick<GatewayWsClient, "registerFromPairing">;
  db: Pick<GatewayStateDb, "writeJson" | "filePath">;
  journal: Pick<GatewayJournal, "append">;
  config: Pick<GatewayConfig, "apiBaseUrl" | "pairingToken" | "gatewayToken">;
  identity: Pick<GatewayDeviceIdentity, "gatewayId" | "deviceId">;
  runtimeMetadata: GatewayRuntimeMetadata;
  existingTokens: GatewayTokenState;
  tokenStore: Pick<GatewayTokenStore, "save">;
}): Promise<void> {
  const { client, db, journal, config, identity, runtimeMetadata, existingTokens, tokenStore } = params;
  if (shouldAttemptPairing(config.pairingToken, existingTokens.gatewayToken)) {
    try {
      await client.registerFromPairing(config.pairingToken as string, identity as GatewayDeviceIdentity, runtimeMetadata);
    } catch (error) {
      if (error instanceof GatewayRegistrationError && !error.retryable) {
        await recordPermanentRegistrationFailure(db, journal, config, identity, error);
        throw new GatewayPermanentStartupFailure(
          `Gateway pairing failed permanently (${error.code}): ${error.detail || error.message}`,
          EXIT_PERMANENT_REGISTRATION_FAILURE,
        );
      }
      throw error;
    }
  } else if (!existingTokens.gatewayToken && config.gatewayToken) {
    await tokenStore.save({ gatewayToken: config.gatewayToken });
  } else if (config.pairingToken && existingTokens.gatewayToken) {
    await journal.append("system", "gateway.pairing.skipped_already_registered", {
      gatewayId: identity.gatewayId,
      deviceId: identity.deviceId,
    });
  }
}

/** Desktop-app-only install path: audits and (best-effort) repairs THIS
 *  machine's supervisor unit, then exits — never boots the rest of the
 *  gateway (no process lock, no cloud connection, no channel runtimes).
 *  Mirrors gateway-doctor.ts's SUPERVISOR_PRESENCE_CHECK wiring exactly
 *  (same resolveGatewayLaunchEntrypoint call, same registrars) rather than
 *  reusing its private helpers directly, so this stays a small, obviously
 *  correct addition instead of widening that module's exported surface.
 *
 *  Exists because the desktop app (src-tauri) needs supervisor installation
 *  to happen SYNCHRONOUSLY, right after it spawns the gateway for the first
 *  time — waiting for the `gateway.doctor.run` capability to be invoked
 *  remotely by the cloud is not a fit for "installing the app IS pairing
 *  the machine." Symmetric with the existing EMPYRALIS_GATEWAY_LAUNCH_PROBE
 *  flag (gateway-launch-repair.ts) in shape: one env var, one early exit,
 *  never wired into the ordinary boot path.
 *
 *  Prints one JSON line to stdout and exits 0 on success OR on a handled,
 *  reported failure (permission denied, unsupported platform) — exit 0
 *  because "the audit ran and reported honestly" is success from the
 *  caller's perspective; the caller reads the JSON to know whether
 *  supervision actually landed. Only an unexpected exception exits 1. */
const GATEWAY_INSTALL_SUPERVISOR_ENV = "EMPYRALIS_GATEWAY_INSTALL_SUPERVISOR";

async function runInstallSupervisorAndExit(config: GatewayConfig): Promise<never> {
  const env = process.env;
  const platform = process.platform;
  const runningEntryPath = require.main?.filename || process.argv[1] || process.execPath;
  const entryPath = resolveGatewayLaunchEntrypoint({
    runningEntryPath,
    stateDir: config.stateDir,
    env,
  });
  const logDir = path.join(config.stateDir, "logs");
  const registerJob =
    platform === "darwin"
      ? createLaunchdJobRegistrar(typeof process.getuid === "function" ? process.getuid() : 0)
      : platform === "linux"
        ? createSystemdJobRegistrar()
        : undefined;

  try {
    const outcome = await auditAndRepairGatewaySupervisorInstall(
      { env, platform, entryPath, logDir, registerJob },
      true,
    );
    console.log(JSON.stringify({ ok: true, outcome }));
    process.exit(0);
  } catch (error) {
    console.log(
      JSON.stringify({
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      }),
    );
    process.exit(0);
  }
}

async function main(): Promise<void> {
  const config = loadGatewayConfig();
  if (String(process.env[GATEWAY_INSTALL_SUPERVISOR_ENV] || "").trim() === "1") {
    await runInstallSupervisorAndExit(config);
    return;
  }
  // Single-use hand-off from whichever process (gateway.self_update or
  // gateway.restart) triggered THIS boot, if any — see update/gateway-
  // restart-pending.ts's module doc comment for why the post-restart health
  // check has to happen here, in the new process, rather than in the old
  // one's capability-invoke response. Read (and deleted) unconditionally,
  // before anything else touches stateDir, so a normal boot with no pending
  // marker costs nothing extra below.
  const pendingRestart = await readAndClearPendingGatewayRestartMarker(config.stateDir);
  const releaseLock = await acquireGatewayProcessLock(config.stateDir);
  const db = new GatewayStateDb(config.stateDir);
  const journal = new GatewayJournal(db);
  const outbox = new GatewayOutbox(db);
  const checkpoints = new GatewayCheckpoints(db);
  const tokenStore = new GatewayTokenStore(db);
  // The two OpenClaw loopback secrets. Read from the environment when an
  // operator set them; otherwise minted here and persisted under stateDir
  // (./openclaw/openclaw-local-secrets.ts).
  //
  // Everything OpenClaw on this box used to hang off
  // `config.openclawBridgeToken && config.openclawGatewayToken`, and NO
  // INSTALLER IN THIS REPO EVER SET EITHER — so the inbound listener, the
  // outbound client, the channel runtimes and `openclaw.provision` were all
  // silently un-constructed on every box this product has ever provisioned.
  // Both values are ours on both ends and never leave the machine, so there
  // was never anything for a human to supply; resolving them here is what
  // makes "every hardware path comes up channel-ready" true without a
  // customer typing anything.
  const openclawSecrets = await resolveOpenClawLocalSecrets({
    stateDir: config.stateDir,
    envBridgeToken: config.openclawBridgeToken,
    envGatewayToken: config.openclawGatewayToken,
  });
  const openclawBridgeToken = openclawSecrets.bridgeToken;
  const openclawGatewayToken = openclawSecrets.gatewayToken;
  // ARCHIVED (Phase U1): supervisorClient instantiation removed.
  const browserWorker = new GatewayBrowserWorker(config);
  const browserRuntime = new GatewayBrowserRuntime(db, browserWorker);
  const shellRuntime = new GatewayShellRuntime({
    stateDir: config.stateDir,
    fullAccessLocallyEnabled: config.shellFullAccessLocallyEnabled,
    dockerImage: config.shellSandboxDockerImage,
  });
  // Outbound leg for OpenClaw-transported channels (CHANNEL-ADOPTION-PLAN.md
  // step 3). The session is opened only when OpenClaw's own gateway token is
  // configured; the runtimes are registered whenever this box is an OpenClaw
  // transport box at all (same condition as the capability advertisement
  // below, so what we say we can carry and what we can actually route are
  // never two different sets). Without a token the runtimes still answer
  // channel.outbound — with a named "not configured" failure, which is far
  // more diagnosable than GatewayWsClient's generic "Unsupported personal
  // channel key".
  const openclawGatewayClient = openclawGatewayToken
    ? new OpenClawGatewayClient({
        url: config.openclawGatewayUrl,
        token: openclawGatewayToken,
        record: (messageType, payload) => journal.append("outbound", messageType, payload),
        logger: {
          info: (message: string) => console.log(`[gateway] ${message}`),
          error: (message: string) => console.error(`[gateway] ${message}`),
        },
      })
    : null;
  // 2026-08-14 full OpenClaw channel cutover: every first-party personal-
  // gateway runtime (WhatsApp/Baileys, Telegram/gramjs, and the Signal/
  // iMessage/WeChat local-bridge family) is deleted in this change —
  // OPENCLAW_CUT_OVER_CHANNEL_IDS now covers all five, so
  // activeFirstPartyPersonalChannels()/transport-ownership.ts had nothing
  // left to filter and were removed with them (see git history to recover
  // either if a NEW first-party personal-gateway channel is ever built).
  // Every personal-gateway channel this box can run now comes from the
  // OpenClaw transport below. discord_personal stays first-party but is not
  // a gateway-constructed runtime at all (bot-token/cloud_connector — see
  // channel_lane_contract_service.py's PERSONAL_CHANNEL_SPECS), so it has no
  // entry here either.
  const personalChannelRuntimes = new PersonalChannelRuntimeRegistry([
    ...(openclawBridgeToken
      ? buildOpenClawPersonalChannelRuntimes(openclawGatewayClient, (messageType, payload) =>
          journal.append("outbound", messageType, payload),
        )
      : []),
  ]);
  // Docker readiness feeds the shell_sandbox permission (runtime/desktop-
  // permissions.ts), which gates what capabilityRouter.supportedCapabilities()
  // below advertises. This probe MUST still be awaited here, before that
  // FIRST computation just below, so the very first advertised set (sent on
  // gateway.connect/registerFromPairing) isn't wrongly missing a Docker
  // daemon that was already available at boot.
  //
  // After startup, requestedCapabilities is no longer frozen for the rest of
  // the process's life: GatewayWsClient.syncRequestedCapabilities() (cloud/
  // ws-client.ts) re-calls capabilityRouter.supportedCapabilities() on every
  // heartbeat tick and, if the advertised SET changed (not just ready/
  // blocked status within it — e.g. Docker/Ollama/a CLI became available
  // after this process already started), mutates runtimeMetadata below IN
  // PLACE so the next heartbeat re-advertises it without a restart. See that
  // method's doc comment for the full mechanism.
  await collectPassiveInventorySnapshot({
    openclawProfile: config.openclawProfile,
    openclawBinaryPath: config.openclawBinaryPath,
  });
  // BYO-brain Phase 2: on-box LLM runtime. Its llm.generate capability is only
  // advertised when the llm_runtime permission reads granted — i.e. when the
  // Ollama probe in collectPassiveInventorySnapshot() above confirmed a local
  // endpoint is reachable (mirrors how shell_sandbox is gated on Docker).
  const llmRuntime = new GatewayLLMRuntime();
  // BYO-brain onboarding (Build F): cli_setup is a static local policy
  // choice, not a probed environment fact (contrast the Docker/Ollama probe
  // just above) — set once, here, before the one-time
  // supportedCapabilities() computation below.
  setCliSetupLocallyEnabled(config.cliSetupLocallyEnabled);
  // Same shape again (CHANNEL-ADOPTION-PLAN.md step 2): whether this box
  // transports channels through a co-located OpenClaw gateway is a static
  // local configuration fact — the presence of the shared bridge secret —
  // and must be known before the one-time supportedCapabilities()
  // computation below, because the cloud rejects a channel.inbound for any
  // channel this gateway never advertised.
  setOpenClawTransportEnabled(Boolean(openclawBridgeToken));
  // Same "static local policy choice, set once before the first
  // supportedCapabilities() computation" shape as cliSetupLocallyEnabled
  // just above — see desktop-permissions.ts's shellFullAccessLocallyEnabled
  // doc comment for why this now also unlocks shell_sandbox advertisement,
  // not just execution mode.
  setShellFullAccessLocallyEnabled(config.shellFullAccessLocallyEnabled);
  const cliSetupRuntime = new GatewayCliSetupRuntime();
  // `triggerShutdown` is reassigned below, once `cleanup`/`identity`/`journal`
  // exist, to the real SIGINT/SIGTERM shutdown path — self-update needs to
  // reuse that exact path (see GatewaySelfUpdateRuntimeOptions.requestShutdown's
  // doc comment), but is constructed here, earlier in startup, before those
  // exist. The indirection is just a mutable function reference so
  // selfUpdateRuntime can be built now and still call the real thing later;
  // self-update can't plausibly be dispatched before startup finishes
  // (nothing is connected to the cloud WS yet), so the throwing default
  // below is a safety net, not an expected path.
  let triggerShutdown: (reason: string) => void = () => {
    throw new Error("Gateway shutdown was requested before startup finished.");
  };
  const selfUpdateRuntime = new GatewaySelfUpdateRuntime({
    currentVersion: GATEWAY_VERSION,
    stateDir: config.stateDir,
    requestShutdown: () => triggerShutdown("gateway.self_update"),
  });
  // gateway.restart (gap-hardware-gateway.md Part 1 item 2): same
  // triggerShutdown indirection as self-update just above — both funnel
  // into the one real SIGINT/SIGTERM shutdown path, never a second exit
  // code path.
  const restartRuntime = new GatewayRestartRuntime({
    currentVersion: GATEWAY_VERSION,
    stateDir: config.stateDir,
    requestShutdown: () => triggerShutdown("gateway.restart"),
  });
  // Same indirection as triggerShutdown just above: the doctor's capability-
  // readiness check needs the fixed capability list capabilityRouter.
  // supportedCapabilities() will produce, but doctorRuntime has to exist
  // BEFORE capabilityRouter (it's one of the constructor args) — so it reads
  // through a mutable getter reference, reassigned once capabilityRouter
  // exists a few lines down, rather than capturing a value that doesn't
  // exist yet.
  let getDoctorRequestedCapabilities: () => string[] = () => [];
  const doctorRuntime = new GatewayDoctorRuntime({
    checkpoints,
    getRequestedCapabilities: () => getDoctorRequestedCapabilities(),
    personalChannelRuntimes,
    stateDir: config.stateDir,
  });
  // OpenClaw provisioning (CHANNEL-ADOPTION-PLAN.md step 4), through the
  // shared builder, which the root installer also uses at install time — the
  // port, the bridge endpoint and the plugin path are derived there, once, so
  // the two callers cannot provision a box against a different port than it
  // runs on.
  //
  // No longer conditional. Both secrets now always resolve (env, else the
  // box's own persisted pair), so every box that runs this gateway is an
  // OpenClaw transport box. That is the point: "there is only one thing which
  // is channels", and a hardware path where channels silently do not exist is
  // not a smaller product, it is a broken one.
  const openclawProvisioningRuntime = buildOpenClawProvisioningRuntime({
    config,
    secrets: { bridgeToken: openclawBridgeToken, gatewayToken: openclawGatewayToken },
    entryPath: require.main?.filename || process.argv[1] || process.execPath,
    record: (messageType, payload) => journal.append("system", messageType, payload),
  });
  // The credential half of channel setup. Kept a separate runtime from
  // provisioning above because the two answer to different authorities —
  // Empyralis owns and regenerates the policy, the OWNER owns the credential
  // and nothing may regenerate it. Unconditional for the same reason
  // provisioning now is: both secrets always resolve, so every box that runs
  // this gateway has a live OpenClaw instance to hold a credential for.
  const openclawChannelSetupRuntime = new OpenClawChannelSetupRuntime({
    profile: config.openclawProfile,
    binaryPath: config.openclawBinaryPath,
    record: (messageType, payload) => journal.append("system", messageType, payload),
    // Linking a channel (QR and friends) goes over OpenClaw's own loopback
    // HTTP, to the route the Empyralis bridge plugin registers inside their
    // process — see openclaw-bridge-plugin/src/channel-login.ts for why that
    // is their required shape and what it does and does not change. The origin
    // is derived from the WS url already configured for this box so there is
    // one address for OpenClaw here, never two that can disagree.
    openclawHttpUrl: config.openclawGatewayUrl.replace(/^ws/, "http"),
    openclawGatewayToken: openclawGatewayToken ?? undefined,
  });
  const capabilityRouter = new GatewayCapabilityRouter(
    browserRuntime,
    personalChannelRuntimes,
    undefined,
    shellRuntime,
    llmRuntime,
    cliSetupRuntime,
    selfUpdateRuntime,
    doctorRuntime,
    restartRuntime,
    openclawProvisioningRuntime ?? undefined,
    openclawChannelSetupRuntime ?? undefined,
  );
  getDoctorRequestedCapabilities = () => capabilityRouter.supportedCapabilities();
  const identity = await resolveDeviceIdentity(db, {
    gatewayId: config.gatewayId,
    deviceId: config.deviceId,
  });
  // Computed once at startup, from the directory this very module was loaded
  // out of — not from config, which can name an install root this process
  // never executed a byte from (the exact production failure mode
  // gateway-self-update-runtime.ts's BOOTSTRAP NOTE describes).
  //
  // MEASURED, not estimated: 31ms over 144 files / 1.96MB on a real build,
  // once per boot. Cheap enough to be unconditional, which matters — a
  // staleness check that is skipped under load is a staleness check that is
  // absent exactly when a box is in trouble.
  //
  // Never fatal: a box that cannot fingerprint itself still connects and
  // still serves every capability. It just reports an unknown build, and the
  // backend degrades to refusing an update it could not verify rather than
  // firing one blind.
  const buildFingerprintOutcome = await computeGatewayBuildFingerprint(
    resolveRunningGatewayDistDir(),
  );
  const buildFingerprint = isGatewayBuildFingerprint(buildFingerprintOutcome)
    ? buildFingerprintOutcome.fingerprint
    : null;
  if (!buildFingerprint) {
    await journal.append("system", "gateway.build_fingerprint.unavailable", {
      reason: (buildFingerprintOutcome as { reason: string }).reason,
    });
  }
  // CAN AN UPDATE ON THIS BOX EVER TAKE EFFECT — a property of the supervisor
  // unit that starts the NEXT process, which this one cannot rewrite (see
  // update/gateway-launch-updatability.ts for the three measured barriers).
  // Read-only, never fatal, and it fails toward "unknown" so a box we cannot
  // classify keeps exactly the update behaviour it has today.
  const runningEntryPath = require.main?.filename || process.argv[1] || process.execPath;
  const launchUpdatability = await classifyGatewayLaunchUpdatability({
    stateDir: config.stateDir,
  });
  // Only a box that is actually stuck gets a launcher written for it. The
  // gateway does the whole repair EXCEPT the one line it is structurally
  // forbidden from writing, and proves the launcher runs before anything
  // recommends pointing a unit at it — an unproven launcher in an ExecStart=
  // is how a stale box becomes a dead one.
  let launchRepair = null;
  if (launchUpdatability.status === "not_updatable") {
    launchRepair = await ensureGatewayLaunchRepair({
      stateDir: config.stateDir,
      runningEntryPath,
    });
    await journal.append("system", "gateway.launch_path.not_updatable", {
      unitPath: launchUpdatability.unitPath,
      launchCommand: launchUpdatability.launchCommand,
      blockers: launchUpdatability.blockers.map((blocker) => blocker.code),
      repairLauncherPath: launchRepair?.launcherPath ?? null,
      repairVerified: launchRepair?.verified ?? false,
      repairFailureReason: launchRepair?.failureReason ?? null,
    });
  }
  const runtimeMetadata = buildRuntimeMetadata(
    GATEWAY_VERSION,
    capabilityRouter.supportedCapabilities(),
    buildFingerprint,
    { ...launchUpdatability, repair: launchRepair },
  );
  const client = new GatewayWsClient(
    config,
    db,
    journal,
    outbox,
    checkpoints,
    tokenStore,
    capabilityRouter,
    personalChannelRuntimes,
  );
  personalChannelRuntimes.setPublisher(client);
  // Same circular-dependency shape as the line above: cliSetupRuntime is
  // constructed before client exists (the router needs it first), so the
  // event-push side of it is wired here, after the fact.
  cliSetupRuntime.setEventPublisher((payload) => client.publishEvent("cli.login.output", payload));
  // Phase 2 (streaming): same reason — llmRuntime is constructed before
  // client exists.
  llmRuntime.setEventPublisher((payload) => client.publishEvent("tool.invoke.chunk", payload));

  // OpenClaw bridge intake (CHANNEL-ADOPTION-PLAN.md step 2). Constructed
  // only when the shared secret is configured — there is no unauthenticated
  // mode, so an unset EMPYRALIS_BRIDGE_TOKEN means the listener simply does
  // not exist, and the OpenClaw plugin's POSTs pile up in its own durable
  // queue instead of being accepted by an open port.
  const openclawInboundListener = openclawBridgeToken
    ? new OpenClawInboundListener({
        port: config.openclawBridgePort,
        token: openclawBridgeToken,
        publisher: client,
        record: (messageType, payload) => journal.append("inbound", messageType, payload),
        logger: {
          info: (message: string) => console.log(`[gateway] ${message}`),
          error: (message: string) => console.error(`[gateway] ${message}`),
        },
      })
    : null;

  const cleanup = async (reason: string) => {
    await openclawInboundListener?.stop().catch(() => undefined);
    // One shared OpenClaw session for all five channel runtimes, so it is
    // stopped here once rather than from any single runtime's stop().
    await openclawGatewayClient?.stop().catch(() => undefined);
    await journal.append("system", "gateway.process.stop", {
      gatewayId: identity.gatewayId,
      deviceId: identity.deviceId,
      reason,
    });
    await releaseLock();
  };
  let shuttingDown = false;
  // Now that cleanup/identity/journal exist, replace the throwing stub
  // passed into GatewaySelfUpdateRuntime above with the real shutdown path —
  // the SAME journal-flush + lock-release + process.exit(0) sequence SIGINT/
  // SIGTERM use below, not a second one. A self-update-triggered shutdown is
  // otherwise indistinguishable from an operator-triggered one: the atomic
  // symlink swap + restart handoff already happened by the time this fires
  // (see gateway-self-update-runtime.ts), so this is just "shut down
  // cleanly," exactly like a signal.
  triggerShutdown = (reason: string) => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;
    void cleanup(reason).finally(() => {
      process.exit(0);
    });
  };
  const installSignalHandler = (signal: NodeJS.Signals) => {
    process.once(signal, () => {
      triggerShutdown(signal);
    });
  };
  installSignalHandler("SIGINT");
  installSignalHandler("SIGTERM");

  // Post-restart health gate (gap-hardware-gateway.md Part 1 item 1): runs
  // ONLY when this boot was preceded by a gateway.self_update or
  // gateway.restart invoke (pendingRestart non-null — a plain reboot costs
  // nothing here). Fires from afterConnected below, i.e. right after
  // checkpoints.currentHealthState() has already been set to "online"
  // (GatewayWsClient.connect()'s markRecovered() call, awaited before
  // afterConnected runs) — so the doctor's cloud_connection check reads the
  // real, live state instead of the "offline" default a check run any
  // earlier would see. Never throws: doctorRuntime.runHealthCheck() itself
  // never throws (every check is wrapped, see gateway-doctor.ts's
  // safeDetect()), and the caller below wraps this whole function in its
  // own catch — a failure here must never affect startup, personal-channel
  // start, or the heartbeat/reconnect loop.
  const reportPostRestartHealthCheck = async (
    marker: NonNullable<typeof pendingRestart>,
  ): Promise<void> => {
    const run: GatewayDoctorRunResult = await doctorRuntime.runHealthCheck();
    const healthCheck: "pass" | "fail" = run.results.some((result) => result.status === "fail")
      ? "fail"
      : "pass";
    const report = {
      trigger: marker.trigger,
      previous_version: marker.previousVersion,
      target_version: marker.targetVersion,
      restart_mode: marker.restartMode,
      triggered_at: marker.triggeredAt,
      checked_at: run.checked_at,
      health_check: healthCheck,
      summary: run.summary,
    };
    await journal.append("system", "gateway.restart.health_check", report);
    // gateway.state.update merges its whole payload into this registration's
    // stored metadata (server_modules/gateway_protocol_service.py's
    // "gateway.state.update" handler), which the Hardware page already
    // reads wholesale via gateway_registration_public_payload()'s `metadata`
    // field — no new backend route needed for this to reach the UI.
    await client.publishStateUpdate({ gateway_restart_health_check: report });
  };

  // MAN-295 / MAN-269: "something must be running in the background always"
  // — a Mac gateway had NO automatic-restart-on-crash path at all until a
  // human explicitly ran gateway.doctor.run with repair:true (the ONLY
  // pre-existing caller of update/gateway-supervisor-install.ts's
  // LaunchAgent writer). This makes that happen as part of normal pairing/
  // install instead: fired once from afterConnected below, same hook
  // personal-channel startup and the post-restart health check already use,
  // so it runs on every real boot (including the very first one right after
  // `curl | sh` on a fresh Mac) — not gated behind the owner discovering the
  // Hardware page's Diagnostics panel. Reuses GatewayDoctorRuntime.
  // ensureSupervisorInstalled(), which itself reuses SUPERVISOR_PRESENCE_
  // CHECK.detect()/.repair() verbatim — no second LaunchAgent-writing
  // implementation. Publishes the outcome via the same gateway.state.update
  // -> registration.metadata path reportPostRestartHealthCheck uses above,
  // so a permission-denied repair (the one case this can't silently
  // succeed — e.g. an unwritable home directory) is a VISIBLE state on the
  // Hardware page's Diagnostics panel instead of a line only findable in
  // this process's own local log file.
  const ensureSupervisorInstalledOnce = async (): Promise<void> => {
    const result: GatewayDoctorCheckResult = await doctorRuntime.ensureSupervisorInstalled();
    await journal.append("system", "gateway.supervisor_install.checked", {
      status: result.status,
      detail: result.detail,
      repaired: Boolean(result.repaired),
      repair_detail: result.repair_detail ?? null,
    });
    await client.publishStateUpdate({ gateway_supervisor_install: result });
  };

  try {
    const existingTokens: GatewayTokenState = await tokenStore.load();
    await attemptGatewayPairing({
      client,
      db,
      journal,
      config,
      identity,
      runtimeMetadata,
      existingTokens,
      tokenStore,
    });

    await journal.append("system", "gateway.process.start", {
      gatewayId: identity.gatewayId,
      deviceId: identity.deviceId,
      stateDir: config.stateDir,
      apiBaseUrl: config.apiBaseUrl,
    });
    await client.run(identity, runtimeMetadata, {
      afterConnected: async () => {
        void personalChannelRuntimes.startAll().catch((error: unknown) => {
          const message = error instanceof Error ? error.message : String(error);
          void journal.append("system", "gateway.personal_channels.start_failed", {
            error: message,
          });
        });
        // Started only after the cloud scope is active: publishEvent throws
        // without one, and an intake that can only 503 is worse than an
        // intake that isn't listening yet (the plugin's queue holds the
        // events either way, but a refused connection is unambiguous).
        if (openclawInboundListener) {
          void openclawInboundListener.start().catch((error: unknown) => {
            const message = error instanceof Error ? error.message : String(error);
            void journal.append("system", "gateway.openclaw_inbound.start_failed", {
              error: message,
              port: config.openclawBridgePort,
            });
          });
        }
        // Boot-time OpenClaw provisioning (CHANNEL-ADOPTION-PLAN.md step 4).
        //
        // TWO JOBS, one call. On a box with a stored policy it re-asserts it
        // against whatever the local OpenClaw config actually says now —
        // their in-chat `/activation` command and the box operator's editor
        // can both have changed it since, with no cloud round trip and
        // nothing logged anywhere. On a box that has NEVER been provisioned
        // it installs the pinned OpenClaw, locks it down, audits it and
        // supervises it with an empty channel policy
        // (ensureProvisionedAtBoot's doc comment has the full argument).
        //
        // That second half is what makes the product's promise true: press
        // one button, authorise the cloud provider, come back to a machine
        // that is already channel-ready. It used to require a human calling
        // the provision route, which is precisely the step a customer must
        // never know exists.
        //
        // Fire-and-forget, like every other boot task here: a box that cannot
        // reach the npm registry still comes up as a fully working gateway
        // with every other capability. Channels degrade; nothing else does.
        if (openclawProvisioningRuntime) {
          void openclawProvisioningRuntime
            .ensureProvisionedAtBoot()
            .then(async ({ result, mode }) => {
              await journal.append("system", "gateway.openclaw_provision.boot_reconcile", {
                mode,
                status: result.status,
                refusal_code: result.refusal?.code ?? null,
                runtime_install: result.runtimeInstall?.action ?? null,
                config_changed: result.configChanged,
                drifted_paths: result.driftedPaths,
              });
              // Same gateway.state.update -> registration.metadata path the
              // restart health check and supervisor install already use, so a
              // refused instance is a VISIBLE state rather than a line in a
              // log file on someone else's machine. This is the ONLY way a
              // customer learns that their box could not install the channel
              // transport — they are never shown a shell, and the box's own
              // logs are not somewhere they can read.
              await client.publishStateUpdate({
                openclaw_provisioning: {
                  mode,
                  status: result.status,
                  profile: result.profile,
                  refusal: result.refusal ?? null,
                  runtime_install: result.runtimeInstall
                    ? {
                        action: result.runtimeInstall.action,
                        observed_version: result.runtimeInstall.observedVersion ?? null,
                        expected_version: result.runtimeInstall.expectedVersion,
                      }
                    : null,
                  supervisor: result.supervisor
                    ? {
                        supported: result.supervisor.supported,
                        action: result.supervisor.repair?.action ?? null,
                        detail: result.supervisor.repair?.detail ?? null,
                      }
                    : null,
                  config_fingerprint: result.configFingerprint ?? null,
                  drifted_paths: result.driftedPaths,
                  disabled_channels: result.disabledChannels,
                },
              });
            })
            .catch((error: unknown) => {
              const message = error instanceof Error ? error.message : String(error);
              void journal.append("system", "gateway.openclaw_provision.boot_reconcile_failed", {
                error: message,
              });
            });
        }
        void ensureSupervisorInstalledOnce().catch((error: unknown) => {
          const message = error instanceof Error ? error.message : String(error);
          void journal.append("system", "gateway.supervisor_install.check_failed", {
            error: message,
          });
        });
        if (pendingRestart) {
          void reportPostRestartHealthCheck(pendingRestart).catch((error: unknown) => {
            const message = error instanceof Error ? error.message : String(error);
            void journal.append("system", "gateway.restart.health_check_failed", {
              error: message,
            });
          });
        }
      },
    });
  } finally {
    await releaseLock();
  }
}

if (require.main === module) {
  installProcessCrashGuards();
  void main().catch((error: unknown) => {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    console.error(message);
    // GatewayPermanentStartupFailure carries its own exit code (see the doc
    // comment on EXIT_PERMANENT_REGISTRATION_FAILURE above) so systemd's
    // RestartPreventExitStatus can tell "will never succeed, stop" apart
    // from every other startup failure here, which keeps the pre-existing
    // exitCode=1 -> Restart=always retry behavior unchanged.
    process.exitCode = error instanceof GatewayPermanentStartupFailure ? error.exitCode : 1;
  });
}
