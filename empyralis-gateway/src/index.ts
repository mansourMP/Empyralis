import { promises as fs } from "fs";
import path from "path";

import { loadGatewayConfig } from "./config";
import { GatewayWsClient } from "./cloud/ws-client";
import { resolveDeviceIdentity } from "./pairing/device-identity";
import { GatewayTokenStore } from "./pairing/token-store";
import type { GatewayTokenState } from "./pairing/token-store";
import { GatewayStateDb } from "./state/db";
import { GatewayJournal } from "./state/journal";
import { GatewayOutbox } from "./state/outbox";
import { GatewayCheckpoints } from "./state/checkpoints";
import { buildRuntimeMetadata } from "./runtime/runtime-metadata";
// ARCHIVED (Phase U1): GatewaySupervisorClient removed.
// The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.
import { GatewayCapabilityRouter } from "./supervisor/capability-router";
import { WhatsAppPersonalRuntime } from "./channels/whatsapp/runtime";
import { TelegramPersonalRuntime } from "./channels/telegram/runtime";
import { PersonalChannelRuntimeRegistry } from "./channels/personal-runtime";
import {
  LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS,
  LocalBridgePersonalChannelRuntime,
  type LocalBridgeRuntimeConfig,
} from "./channels/local-bridge-runtime";
import { ImsgIMessagePersonalChannelRuntime } from "./channels/imsg-imessage-runtime";
import type { PersonalChannelRuntime } from "./channels/personal-runtime";
import { GatewayBrowserWorker } from "./browser/worker";
import { GatewayBrowserRuntime } from "./browser/runtime";
import { GatewayShellRuntime } from "./shell/runtime";
import { GatewayLLMRuntime } from "./llm/runtime";
import { GatewayCliSetupRuntime } from "./llm/cli-setup-runtime";
import { GatewaySelfUpdateRuntime } from "./update/gateway-self-update-runtime";
import { GatewayDoctorRuntime } from "./health/gateway-doctor";
import { collectPassiveInventorySnapshot } from "./health/service-inventory";
import { setCliSetupLocallyEnabled } from "./runtime/desktop-permissions";

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

/** Selects the iMessage transport for the "imessage_personal" local-bridge
 *  config: the in-process imsg RPC runtime by default (no separate process
 *  to run — see channels/imsg-imessage-runtime.ts), or the legacy
 *  BlueBubbles-over-HTTP runtime when a deployment already has
 *  EMPYRALIS_IMESSAGE_BRIDGE_URL configured, so an existing BlueBubbles
 *  Agent Computer bridge setup keeps working unchanged after this upgrade.
 *  Every other local-bridge channel (Signal, WeChat) is untouched. */
function buildLocalBridgeChannelRuntime(
  bridgeConfig: LocalBridgeRuntimeConfig,
  db: GatewayStateDb,
): PersonalChannelRuntime {
  if (bridgeConfig.channelKey === "imessage_personal") {
    const legacyBlueBubblesUrl = String(process.env.EMPYRALIS_IMESSAGE_BRIDGE_URL || "").trim();
    if (legacyBlueBubblesUrl) {
      return new LocalBridgePersonalChannelRuntime(bridgeConfig);
    }
    return new ImsgIMessagePersonalChannelRuntime(bridgeConfig, { db });
  }
  return new LocalBridgePersonalChannelRuntime(bridgeConfig);
}

async function main(): Promise<void> {
  const config = loadGatewayConfig();
  const releaseLock = await acquireGatewayProcessLock(config.stateDir);
  const db = new GatewayStateDb(config.stateDir);
  const journal = new GatewayJournal(db);
  const outbox = new GatewayOutbox(db);
  const checkpoints = new GatewayCheckpoints(db);
  const tokenStore = new GatewayTokenStore(db);
  // ARCHIVED (Phase U1): supervisorClient instantiation removed.
  const browserWorker = new GatewayBrowserWorker(config);
  const browserRuntime = new GatewayBrowserRuntime(db, browserWorker);
  const shellRuntime = new GatewayShellRuntime({
    stateDir: config.stateDir,
    fullAccessLocallyEnabled: config.shellFullAccessLocallyEnabled,
    dockerImage: config.shellSandboxDockerImage,
  });
  const personalChannelRuntimes = new PersonalChannelRuntimeRegistry(
    config.personalChannelsEnabled
      ? [
          new WhatsAppPersonalRuntime(db),
          new TelegramPersonalRuntime(db),
          ...LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.map((bridgeConfig) => buildLocalBridgeChannelRuntime(bridgeConfig, db)),
        ]
      : [],
  );
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
  await collectPassiveInventorySnapshot({});
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
  const capabilityRouter = new GatewayCapabilityRouter(
    browserRuntime,
    personalChannelRuntimes,
    undefined,
    shellRuntime,
    llmRuntime,
    cliSetupRuntime,
    selfUpdateRuntime,
    doctorRuntime,
  );
  getDoctorRequestedCapabilities = () => capabilityRouter.supportedCapabilities();
  const identity = await resolveDeviceIdentity(db, {
    gatewayId: config.gatewayId,
    deviceId: config.deviceId,
  });
  const runtimeMetadata = buildRuntimeMetadata(
    GATEWAY_VERSION,
    capabilityRouter.supportedCapabilities(),
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

  const cleanup = async (reason: string) => {
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

  try {
    const existingTokens: GatewayTokenState = await tokenStore.load();
    if (shouldAttemptPairing(config.pairingToken, existingTokens.gatewayToken)) {
      await client.registerFromPairing(config.pairingToken as string, identity, runtimeMetadata);
    } else if (!existingTokens.gatewayToken && config.gatewayToken) {
      await tokenStore.save({ gatewayToken: config.gatewayToken });
    } else if (config.pairingToken && existingTokens.gatewayToken) {
      await journal.append("system", "gateway.pairing.skipped_already_registered", {
        gatewayId: identity.gatewayId,
        deviceId: identity.deviceId,
      });
    }

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
    process.exitCode = 1;
  });
}
