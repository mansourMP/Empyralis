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
  const personalChannelRuntimes = new PersonalChannelRuntimeRegistry(
    config.personalChannelsEnabled
      ? [
          new WhatsAppPersonalRuntime(db),
          new TelegramPersonalRuntime(db),
          ...LOCAL_BRIDGE_PERSONAL_CHANNEL_CONFIGS.map((config) => new LocalBridgePersonalChannelRuntime(config)),
        ]
      : [],
  );
  const capabilityRouter = new GatewayCapabilityRouter(
    browserRuntime,
    personalChannelRuntimes,
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
