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
} from "./channels/local-bridge-runtime";
import { GatewayBrowserWorker } from "./browser/worker";
import { GatewayBrowserRuntime } from "./browser/runtime";
import { GatewayShellRuntime } from "./shell/runtime";
import { GatewayLLMRuntime } from "./llm/runtime";
import { GatewayCliSetupRuntime } from "./llm/cli-setup-runtime";
import { collectPassiveInventorySnapshot } from "./health/service-inventory";
import { setCliSetupLocallyEnabled } from "./runtime/desktop-permissions";

const GATEWAY_VERSION = "0.1.0";

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
          ...LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.map((config) => new LocalBridgePersonalChannelRuntime(config)),
        ]
      : [],
  );
  // Docker readiness feeds the shell_sandbox permission (runtime/desktop-
  // permissions.ts), which gates what capabilityRouter.supportedCapabilities()
  // below advertises. requestedCapabilities is computed exactly once at
  // startup and never recomputed for this process's lifetime (heartbeats
  // only re-evaluate ready/blocked within that fixed list) — so this probe
  // MUST be awaited here, before that one-time computation, or a Docker
  // daemon that's genuinely available could still be wrongly excluded for
  // the whole life of this process.
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
  const capabilityRouter = new GatewayCapabilityRouter(
    browserRuntime,
    personalChannelRuntimes,
    undefined,
    shellRuntime,
    llmRuntime,
    cliSetupRuntime,
  );
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
  const installSignalHandler = (signal: NodeJS.Signals) => {
    process.once(signal, () => {
      if (shuttingDown) {
        return;
      }
      shuttingDown = true;
      void cleanup(signal).finally(() => {
        process.exit(0);
      });
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
  void main().catch((error: unknown) => {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    console.error(message);
    process.exitCode = 1;
  });
}
