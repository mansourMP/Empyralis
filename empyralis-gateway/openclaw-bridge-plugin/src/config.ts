/**
 * Env-driven config, matching empyralis-gateway/src/config.ts's own
 * `EMPYRALIS_*` env-var convention rather than inventing a second one.
 *
 * AUTH DECISION (requirement #4): this plugin never holds an Empyralis
 * *cloud* credential. It POSTs to a local endpoint owned by the
 * already-running empyralis-gateway process on the same machine — the
 * process that already completed device pairing and already holds the
 * long-lived `gatewayToken` / short-lived `sessionToken` pair
 * (empyralis-gateway/src/pairing/token-store.ts,
 * empyralis-gateway/src/cloud/ws-client.ts) and already has a durable,
 * retrying, journaled path to Empyralis's cloud
 * (`GatewayOutbox`/`GatewayJournal`, same file). That local hop is
 * authenticated with a separate, low-privilege, loopback-only shared
 * secret (`EMPYRALIS_BRIDGE_TOKEN`) — distinct from and much narrower than
 * the cloud gateway token, so a compromise of this OpenClaw plugin process
 * (third-party code, not audited by us) can never leak or use a cloud
 * credential. This *is* "reuse rather than invent": rather than this
 * plugin re-implementing pairing, session negotiation, WS reconnect, and
 * outbox durability a second time in a second process, it hands the event
 * to the process that already does all of that.
 *
 * The local intake endpoint itself (empyralis-gateway listening on
 * `EMPYRALIS_BRIDGE_ENDPOINT_URL` and forwarding into its existing
 * `publisher.publishEvent("channel.inbound", ...)` path) is step 2/4
 * work — CHANNEL-ADOPTION-PLAN.md's "INBOUND WIRING" / "PROVISIONING" —
 * and does not exist yet. Today this plugin talks to whatever
 * `EMPYRALIS_BRIDGE_ENDPOINT_URL` points at (a mock listener in dev/test).
 * Never log `EMPYRALIS_BRIDGE_TOKEN`'s value, anywhere, for any reason.
 */

export interface BridgeConfig {
  endpointUrl: string;
  token: string | undefined;
  timeoutMs: number;
  queueFilePath: string;
  queueMaxItems: number;
  queueMaxAttempts: number;
  queueMinBackoffMs: number;
  queueMaxBackoffMs: number;
  queueFlushIntervalMs: number;
}

function readInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function loadBridgeConfig(
  env: NodeJS.ProcessEnv,
  defaultQueueDir: string,
): BridgeConfig {
  const endpointUrl = String(env.EMPYRALIS_BRIDGE_ENDPOINT_URL ?? "http://127.0.0.1:8790/openclaw/inbound").trim();
  const token = env.EMPYRALIS_BRIDGE_TOKEN?.trim() || undefined;
  return {
    endpointUrl,
    token,
    timeoutMs: readInt(env.EMPYRALIS_BRIDGE_TIMEOUT_MS, 5_000),
    queueFilePath: String(env.EMPYRALIS_BRIDGE_QUEUE_FILE ?? `${defaultQueueDir}/empyralis-bridge-queue.json`).trim(),
    queueMaxItems: readInt(env.EMPYRALIS_BRIDGE_QUEUE_MAX, 500),
    queueMaxAttempts: readInt(env.EMPYRALIS_BRIDGE_QUEUE_MAX_ATTEMPTS, 8),
    queueMinBackoffMs: readInt(env.EMPYRALIS_BRIDGE_QUEUE_MIN_BACKOFF_MS, 2_000),
    queueMaxBackoffMs: readInt(env.EMPYRALIS_BRIDGE_QUEUE_MAX_BACKOFF_MS, 5 * 60_000),
    queueFlushIntervalMs: readInt(env.EMPYRALIS_BRIDGE_QUEUE_FLUSH_INTERVAL_MS, 3_000),
  };
}
