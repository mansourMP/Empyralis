import type {
  GatewayChannelInboundPayload,
  GatewayChannelOutboundPayload,
  GatewayRequestEnvelope,
  GatewayScope,
  GatewayToolInvokePayload,
} from "../protocol/types";
import type { GatewayStateDb } from "../state/db";
import {
  buildImsgSendParams,
  ImsgRpcClient,
  mapImsgMessageToBridgeEvent,
  normalizeImsgFullDiskAccessError,
  probeImsgIMessageStaged,
  runImsgHomebrewInstall,
  type ImsgExecImpl,
  type ImsgMessagePayload,
  type ImsgSpawnImpl,
  type ImsgStagedProbeResult,
} from "../bridges/imsg-imessage-client";
import { buildLocalBridgeManifest, type LocalBridgeRuntimeConfig } from "./local-bridge-runtime";
import type {
  PersonalChannelCapabilityManifest,
  PersonalChannelGatewayPublisher,
  PersonalChannelHealthSnapshot,
  PersonalChannelRuntime,
} from "./personal-runtime";

// In-process iMessage-via-imsg runtime — the imsg-native counterpart to
// LocalBridgePersonalChannelRuntime's BlueBubbles-over-HTTP path. Unlike
// that class (which polls a separate Agent Computer bridge process over
// HTTP), this runtime owns an `imsg rpc` child process directly: no
// separate process for the user to run, no bridge URL to configure. This
// mirrors OpenClaw's own iMessage extension, which starts its imsg RPC
// client in-process from the channel monitor rather than shelling out to a
// standalone bridge (see extensions/imessage/src/monitor/monitor-provider.ts
// and client.ts in the OpenClaw tree).
//
// Provider/channel_key are deliberately left as "bluebubbles_local_bridge" /
// "imessage_personal" — unchanged from LocalBridgePersonalChannelRuntime's
// config — because server_modules/{channel_lane_contract_service,
// connection_catalog_service,personal_channels_service}.py all hardcode
// "bluebubbles_local_bridge" as the expected provider string for this
// channel's certification/connection-catalog/routing logic. Renaming it here
// without a coordinated backend change would silently break those checks.
// This is real naming debt (the transport is no longer BlueBubbles) — flagged
// for a follow-up, not fixed in this gateway-only change.

const WATCH_SUBSCRIBE_STARTUP_MAX_ATTEMPTS = 3;
const WATCH_SUBSCRIBE_RETRY_DELAY_MS = 1_000;
const SENT_MESSAGE_ID_CACHE_LIMIT = 500;
const IMSG_CURSOR_STATE_FILE = "imsg-imessage-cursor.json";
const DEFAULT_PROBE_TIMEOUT_MS = 15_000;
const DEFAULT_SEND_TIMEOUT_MS = 30_000;

// Capability ids the in-app setup panel dispatches via tool-invoke — see the
// class doc comment on requestedCapabilities()/handleCapabilityInvoke()
// below, and server_modules/personal_channels_service.py's
// recheck_imessage_personal_gateway()/install_imessage_imsg_gateway() on the
// backend side that calls them.
const RECHECK_CAPABILITY_ID = "channel.imessage.personal.recheck";
const INSTALL_CAPABILITY_ID = "channel.imessage.personal.install";

// The exact command from OpenClaw's own docs (docs/channels/imessage.md,
// "Install and verify imsg" step) — shown to the user as a copy-pasteable
// fallback regardless of whether the auto-install action (which runs this
// same command) succeeds.
const IMSG_HOMEBREW_INSTALL_COMMAND = "brew install steipete/tap/imsg";

interface ImsgCursorState {
  lastRowid?: number;
}

export interface ImsgIMessageRuntimeOptions {
  /** Optional durable state store. When provided, the since_rowid restart-
   *  recovery cursor survives a gateway process restart (not just a
   *  mid-process watch-loop reconnect). Omitting it (e.g. in unit tests)
   *  still gives in-memory-only recovery for the life of the process. */
  db?: GatewayStateDb;
  /** Injectable for tests — see ImsgSpawnImpl's doc comment. Defaults to
   *  node:child_process's real spawn via the client module. */
  spawnImpl?: ImsgSpawnImpl;
  /** Injectable for tests — the one-shot `imsg rpc --help` / `imsg status
   *  --json` runner used by the health probe. */
  execImpl?: ImsgExecImpl;
}

function readPositiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : fallback;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref?.();
  });
}

export class ImsgIMessagePersonalChannelRuntime implements PersonalChannelRuntime {
  private readonly manifest: PersonalChannelCapabilityManifest;
  private publisher?: PersonalChannelGatewayPublisher;
  private started = false;
  private stopping = false;
  private client: ImsgRpcClient | null = null;
  /** True only once watch.subscribe has actually succeeded on the current
   *  `client` -- gates handleChannelOutbound so a send never races ahead of
   *  a client that's still mid-handshake. `client` itself is tracked from
   *  the moment it's constructed (before subscribe even starts) purely so
   *  stop() can always reach and tear down whatever process is currently
   *  running, including one still awaiting its first response. */
  private clientReady = false;
  private watchLoopPromise: Promise<void> | null = null;
  private lastError?: string;
  private lastEventAt?: string;
  private lastDispatchedRowid: number | null = null;
  private readonly seenInboundEventIds: string[] = [];
  private readonly seenInboundEventSet = new Set<string>();
  /** Guids/ids of messages this runtime has itself sent — see
   *  mapImsgMessageToBridgeEvent's MapImsgMessageOptions.sentMessageIds for
   *  the is_reply_to_sage contract this feeds. */
  private readonly sentMessageIds = new Set<string>();

  constructor(
    private readonly config: LocalBridgeRuntimeConfig,
    private readonly options: ImsgIMessageRuntimeOptions = {},
  ) {
    this.manifest = buildLocalBridgeManifest(config);
  }

  // The imsg bridge itself has no phone/code/QR pairing step (see the class
  // doc comment) — but the in-app setup panel (frontend/lib/workspace/fleet/
  // IMessageSetupPanel.tsx) still needs two real actions on THIS gateway
  // process: re-running the health probe live (not the cached heartbeat
  // snapshot) and installing `imsg` via Homebrew without the user opening a
  // terminal. Both route through the existing generic tool-invoke RPC
  // (GatewayCapabilityRouter.handleToolInvoke ->
  // PersonalChannelRuntimeRegistry.runtimeForCapability -> here) — the same
  // mechanism WhatsAppPersonalRuntime/TelegramPersonalRuntime already use
  // for their own configure/disconnect capabilities, so no new gateway
  // protocol frame type was needed.
  requestedCapabilities(): string[] {
    return [RECHECK_CAPABILITY_ID, INSTALL_CAPABILITY_ID];
  }

  supportsCapability(capabilityId: string): boolean {
    const id = String(capabilityId || "").trim();
    return id === RECHECK_CAPABILITY_ID || id === INSTALL_CAPABILITY_ID;
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const capabilityId = String(frame.payload?.capability_id || "").trim();
    if (capabilityId === RECHECK_CAPABILITY_ID) {
      const probe = await this.runStagedProbe();
      return {
        channel_key: this.config.channelKey,
        provider: this.config.provider,
        probe,
      };
    }
    if (capabilityId === INSTALL_CAPABILITY_ID) {
      const install = await runImsgHomebrewInstall();
      return {
        channel_key: this.config.channelKey,
        provider: this.config.provider,
        install,
        manual_command: IMSG_HOMEBREW_INSTALL_COMMAND,
      };
    }
    throw new Error(
      `${this.config.label} is configured through the local imsg CLI — unsupported capability: ${capabilityId || "unknown"}.`,
    );
  }

  supportsChannel(channelKey: string): boolean {
    return String(channelKey || "").trim() === this.config.channelKey;
  }

  async handleGatewayConnected(_scope: GatewayScope): Promise<void> {
    await this.start();
  }

  async handleGatewayDisconnected(_reason: string): Promise<void> {
    await this.stop();
  }

  /** Observability/test hook: true only once watch.subscribe has actually
   *  succeeded on the currently running imsg process -- i.e. the point at
   *  which handleChannelOutbound will accept a send. Not part of the
   *  PersonalChannelRuntime interface; a request line having been written
   *  to stdin (or even a "connected"-looking manifest/health status) is not
   *  by itself proof the subscribe round-trip has completed. */
  isWatchReady(): boolean {
    return this.clientReady;
  }

  async start(): Promise<void> {
    if (this.started) {
      return;
    }
    this.started = true;
    this.stopping = false;
    this.lastDispatchedRowid = await this.loadCursor();
    this.watchLoopPromise = this.runWatchLoop().catch((error) => {
      this.lastError = error instanceof Error ? error.message : String(error);
    });
  }

  async stop(): Promise<void> {
    this.started = false;
    this.stopping = true;
    this.clientReady = false;
    const client = this.client;
    this.client = null;
    if (client) {
      // Tears down whatever imsg process is currently running AND rejects
      // any request it has in flight (see ImsgRpcClient.stop()'s failAll) --
      // this is what unblocks runWatchLoop even when stop() lands mid-
      // subscribe, instead of leaving it to hang until that request's own
      // timeout.
      await client.stop().catch(() => undefined);
    }
    if (this.watchLoopPromise) {
      await this.watchLoopPromise.catch(() => undefined);
      this.watchLoopPromise = null;
    }
  }

  setPublisher(publisher: PersonalChannelGatewayPublisher): void {
    this.publisher = publisher;
  }

  getManifest(): PersonalChannelCapabilityManifest {
    return this.manifest;
  }

  async getHealthSnapshot(): Promise<PersonalChannelHealthSnapshot> {
    const probe = await this.runStagedProbe();
    const probePayload = probe as unknown as Record<string, unknown>;
    const ok = probe.binary.state === "pass" && probe.rpc.state === "pass" && probe.fullDiskAccess.state === "pass";

    if (ok) {
      this.lastError = undefined;
      return {
        channelKey: this.config.channelKey,
        provider: this.config.provider,
        status: "connected",
        running: this.started,
        connected: true,
        reconnectAttempts: 0,
        lastEventAt: this.lastEventAt,
        issues: [],
        probe: probePayload,
      };
    }

    const failureError = probe.binary.error || probe.rpc.error || probe.fullDiskAccess.error || "imsg is not reachable";
    this.lastError = failureError;
    const status = probe.binary.state === "fail" ? "not_configured" : "unavailable";
    // Three distinct failure codes so the setup panel (and any future
    // alerting) can tell "go install imsg" apart from "go flip a System
    // Settings toggle" apart from "something else is wrong" — the coarse
    // two-way split this replaced (not_installed vs. unavailable) couldn't
    // distinguish the Full Disk Access case, which is the one with an exact,
    // actionable fix (see IMSG_FULL_DISK_ACCESS_ERROR above).
    let issue: string;
    if (probe.binary.state === "fail") {
      issue = `${this.config.channelKey}_imsg_not_installed`;
    } else if (probe.rpc.state === "fail") {
      issue = `${this.config.channelKey}_imsg_rpc_unsupported`;
    } else if (probe.fullDiskAccess.isFullDiskAccessError) {
      issue = `${this.config.channelKey}_full_disk_access_required`;
    } else {
      issue = `${this.config.channelKey}_imsg_unavailable`;
    }
    return {
      channelKey: this.config.channelKey,
      provider: this.config.provider,
      status,
      running: this.started,
      connected: false,
      reconnectAttempts: 0,
      lastError: failureError,
      issues: [issue],
      probe: probePayload,
    };
  }

  /** Shared by getHealthSnapshot() (folded into the heartbeat-pushed state)
   *  and the "recheck" capability invoke (a live, on-demand re-probe the
   *  setup panel's Re-check button triggers via tool-invoke — see
   *  handleCapabilityInvoke() above) so both paths report the identical
   *  per-stage shape. Never throws: a probe-machinery error itself (not an
   *  imsg failure, an actual bug in the probe call) is reported as a failed
   *  "binary" stage with the raw error, same as probeImsgIMessage's own
   *  historical .catch() fallback, so callers always get a renderable
   *  result instead of a rejected promise. */
  private async runStagedProbe(): Promise<ImsgStagedProbeResult> {
    const cliPath = this.resolveCliPath();
    try {
      return await probeImsgIMessageStaged(cliPath, {
        dbPath: this.resolveDbPath(),
        timeoutMs: this.resolveProbeTimeoutMs(),
        execImpl: this.options.execImpl,
        spawnImpl: this.options.spawnImpl,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const blocked = { state: "blocked" as const, error: "not checked — an earlier step failed first" };
      return {
        checkedAt: new Date().toISOString(),
        binary: { state: "fail", error: message },
        rpc: blocked,
        fullDiskAccess: { ...blocked, isFullDiskAccessError: false },
        privateApi: { ...blocked, checked: false },
      };
    }
  }

  async handleChannelOutbound(
    frame: GatewayRequestEnvelope<GatewayChannelOutboundPayload>,
  ): Promise<Record<string, unknown>> {
    const payload = frame.payload || ({} as GatewayChannelOutboundPayload);
    const remoteJid = String(payload.remote_jid || "").trim();
    const messageText = String(payload.text || "").trim();
    if (!remoteJid || !messageText) {
      throw new Error("remote_jid and text are required");
    }
    // Outbound send always goes through the SAME live watch.subscribe
    // client the runtime keeps running (never a separate short-lived
    // client) -- this is what lets a "send" RPC race safely against
    // in-flight inbound notifications on the identical stdio pipe, and
    // avoids spawning a second `imsg rpc` process for every outbound
    // message. If the watch client isn't up (imsg missing, FDA not
    // granted, still retrying), this fails fast and honestly instead of
    // silently spawning a parallel process whose failure mode has not been
    // verified against a live imsg binary.
    const client = this.client;
    if (!client || !this.clientReady) {
      throw new Error(
        `${this.config.label} imsg bridge is not connected` +
          (this.lastError ? `: ${this.lastError}` : " (imsg not started yet).") +
          " Verify `imsg` is installed and Full Disk Access is granted, then retry.",
      );
    }
    const replyTo = typeof payload.reply_to_external_message_id === "string"
      ? payload.reply_to_external_message_id
      : undefined;
    const params = buildImsgSendParams(remoteJid, messageText, { replyTo });
    const result = await client.request<Record<string, unknown>>("send", params, {
      timeoutMs: DEFAULT_SEND_TIMEOUT_MS,
    });
    const externalMessageId = this.extractSentMessageId(result);
    if (externalMessageId) {
      this.rememberSentMessageId(externalMessageId);
    }
    this.lastEventAt = new Date().toISOString();
    return {
      channel_key: this.config.channelKey,
      provider: this.config.provider,
      delivered: true,
      external_message_id: externalMessageId,
      status: "sent",
      bridge: "in_process_imsg",
    };
  }

  private resolveCliPath(): string {
    return process.env[`${this.config.envPrefix}_CLI_PATH`]?.trim() || "imsg";
  }

  private resolveDbPath(): string | undefined {
    return process.env[`${this.config.envPrefix}_DB_PATH`]?.trim() || undefined;
  }

  private resolveProbeTimeoutMs(): number {
    return readPositiveInt(process.env[`${this.config.envPrefix}_PROBE_TIMEOUT_MS`], DEFAULT_PROBE_TIMEOUT_MS);
  }

  /** Owns the full lifetime of the watch client: subscribe, tail
   *  notifications until the process/connection dies, then reconnect. The
   *  first WATCH_SUBSCRIBE_STARTUP_MAX_ATTEMPTS subscribe attempts retry
   *  quickly (mirrors OpenClaw's monitor-provider.ts startup retry,
   *  WATCH_SUBSCRIBE_MAX_ATTEMPTS/RETRY_DELAY_MS); once subscribed at least
   *  once, every subsequent disconnect (imsg crashed, Messages.app
   *  restarted, Mac slept) is treated as a live reconnect and retried
   *  indefinitely at the same interval for as long as start() is active —
   *  a deliberately simpler policy than OpenClaw's, which additionally
   *  persists/replays a bounded backlog window; this runtime instead
   *  restarts the subscribe with `since_rowid` set to the last dispatched
   *  rowid so imsg itself replays what was missed (see resolveSinceRowid). */
  private async runWatchLoop(): Promise<void> {
    let everSubscribed = false;
    let attempt = 0;
    while (!this.stopping) {
      attempt += 1;
      const cliPath = this.resolveCliPath();
      const client = new ImsgRpcClient({
        cliPath,
        dbPath: this.resolveDbPath(),
        spawnImpl: this.options.spawnImpl,
        onNotification: (notification) => {
          if (notification.method === "message") {
            this.handleInboundNotification(notification.params as ImsgMessagePayload | undefined);
          }
        },
        onStderrLine: (line) => {
          const fdaError = normalizeImsgFullDiskAccessError(line);
          if (fdaError) {
            this.lastError = fdaError;
          }
        },
      });
      // Tracked from construction (not just after a successful subscribe)
      // so stop() can always reach and tear down whatever process is
      // currently running -- including one still mid-handshake -- instead
      // of leaving that instance unreachable until its own request timeout.
      this.client = client;
      this.clientReady = false;
      if (this.stopping) {
        await client.stop().catch(() => undefined);
        return;
      }
      try {
        await client.start();
        const params: Record<string, unknown> = { attachments: false, include_reactions: true };
        const sinceRowid = this.resolveSinceRowid();
        if (sinceRowid !== null) {
          params.since_rowid = sinceRowid;
        }
        await client.request("watch.subscribe", params, { timeoutMs: this.resolveProbeTimeoutMs() });
        everSubscribed = true;
        attempt = 0;
        this.clientReady = true;
        this.lastError = undefined;
        await client.waitForClose();
      } catch (error) {
        this.lastError = this.lastError || (error instanceof Error ? error.message : String(error));
      } finally {
        this.clientReady = false;
        if (this.client === client) {
          this.client = null;
        }
        await client.stop().catch(() => undefined);
      }
      if (this.stopping) {
        return;
      }
      if (!everSubscribed && attempt >= WATCH_SUBSCRIBE_STARTUP_MAX_ATTEMPTS) {
        // Give up retrying rapidly after repeated startup failures (binary
        // missing, rpc unsupported, etc.) -- getHealthSnapshot's own probe
        // keeps reporting the real reason via its own independent check, so
        // this is not a silent failure, just a slower retry cadence so a
        // persistently broken imsg install doesn't spin a tight loop.
        await delay(WATCH_SUBSCRIBE_RETRY_DELAY_MS * WATCH_SUBSCRIBE_STARTUP_MAX_ATTEMPTS);
        continue;
      }
      await delay(WATCH_SUBSCRIBE_RETRY_DELAY_MS);
    }
  }

  /** since_rowid for watch.subscribe's restart-recovery replay window --
   *  verified param name against OpenClaw's monitor-provider.ts:1591-1598
   *  (`watch.subscribe` params: `attachments`, `include_reactions`,
   *  `since_rowid`). Prefers the persisted cursor (survives a gateway
   *  restart when options.db is wired) but always uses the more recent of
   *  {persisted cursor, in-memory last-dispatched rowid} so a mid-process
   *  reconnect never regresses behind what this run has already delivered. */
  private resolveSinceRowid(): number | null {
    return this.lastDispatchedRowid;
  }

  private handleInboundNotification(payload: ImsgMessagePayload | undefined): void {
    if (!payload) {
      return;
    }
    if (typeof payload.id === "number" && Number.isFinite(payload.id)) {
      this.lastDispatchedRowid = this.lastDispatchedRowid === null
        ? payload.id
        : Math.max(this.lastDispatchedRowid, payload.id);
      void this.saveCursor(this.lastDispatchedRowid);
    }
    const event = mapImsgMessageToBridgeEvent(payload, { sentMessageIds: this.sentMessageIds });
    if (!event) {
      return;
    }
    const eventKey = `${this.config.channelKey}:${event.remote_jid}:${event.external_message_id}`;
    if (!this.rememberInboundEvent(eventKey)) {
      return;
    }
    // Group gate: REMOVED as a gateway-side DECISION (see
    // local-bridge-runtime.ts's pollInboundEvents — the exact same removal,
    // commit 53e3abf40 "resolve agent identity for Signal/iMessage/WeChat-
    // personal"). That commit's message claimed "the backend is now the one
    // place that decides, for every channel," but this runtime — a second,
    // parallel iMessage transport (in-process `imsg` RPC, distinct from the
    // BlueBubbles-over-HTTP path LocalBridgePersonalChannelRuntime drives)
    // — still had its own copy of the same hard drop, left behind by that
    // commit. Confirmed safe to remove the same way: this runtime reports
    // the identical channel_key ("imessage_personal") and provider
    // ("bluebubbles_local_bridge" — see this file's own doc comment above
    // for why the provider string is unchanged even though the transport
    // isn't BlueBubbles) as the HTTP path, so the backend dispatches BOTH
    // transports to the exact same handler
    // (_LocalBridgePersonalChannelHandler, registered once per channel_key,
    // not per provider) and channel_lane_contract_service's provider check
    // passes identically for both. personal_channels_service.py's
    // _handle_local_bridge_gateway_channel_inbound now resolves a real
    // per-agent identity (_resolve_local_bridge_agent_id) and runs
    // _enforce_group_policy — the ONE shared resolver every personal-
    // gateway channel uses — before a group message ever reaches a model
    // turn, so removing this gateway-side copy does not open a hole; it
    // closes the one place the backend's group allowlist was still
    // structurally unreachable for an owner on this transport. This
    // runtime's job is now only to compute and always forward the raw
    // mention FACTS (is_group/is_mentioned/is_reply_to_sage) below — never
    // to withhold a message based on them. (is_mentioned is currently
    // always false here — see mapImsgMessageToBridgeEvent — pre-existing
    // and unrelated to this fix: only is_reply_to_sage can pass an
    // addressed iMessage group message on this transport today.)
    const inbound: GatewayChannelInboundPayload = {
      channel_key: this.config.channelKey,
      provider: this.config.provider,
      message: {
        external_message_id: event.external_message_id,
        remote_jid: event.remote_jid,
        sender_jid: event.sender_jid,
        push_name: event.push_name,
        text: event.text,
        received_at: event.received_at,
        from_me: event.from_me,
        is_group: event.is_group,
        is_mentioned: event.is_mentioned,
        is_reply_to_sage: event.is_reply_to_sage,
      },
    };
    this.lastEventAt = event.received_at;
    void this.publisher?.publishEvent("channel.inbound", inbound);
  }

  private rememberInboundEvent(eventKey: string): boolean {
    if (this.seenInboundEventSet.has(eventKey)) {
      return false;
    }
    this.seenInboundEventSet.add(eventKey);
    this.seenInboundEventIds.push(eventKey);
    while (this.seenInboundEventIds.length > 1000) {
      const oldest = this.seenInboundEventIds.shift();
      if (oldest) {
        this.seenInboundEventSet.delete(oldest);
      }
    }
    return true;
  }

  private rememberSentMessageId(id: string): void {
    this.sentMessageIds.add(id);
    if (this.sentMessageIds.size > SENT_MESSAGE_ID_CACHE_LIMIT) {
      this.sentMessageIds.clear();
    }
  }

  private extractSentMessageId(result: Record<string, unknown>): string | undefined {
    const guid = typeof result.guid === "string" ? result.guid.trim() : "";
    if (guid) {
      return guid;
    }
    if (typeof result.id === "number" || typeof result.id === "string") {
      return String(result.id);
    }
    return undefined;
  }

  private async loadCursor(): Promise<number | null> {
    if (!this.options.db) {
      return null;
    }
    try {
      const state = await this.options.db.readJson<ImsgCursorState>(IMSG_CURSOR_STATE_FILE, {});
      return typeof state.lastRowid === "number" && Number.isFinite(state.lastRowid) ? state.lastRowid : null;
    } catch {
      return null;
    }
  }

  private async saveCursor(rowid: number): Promise<void> {
    if (!this.options.db) {
      return;
    }
    try {
      await this.options.db.writeJson<ImsgCursorState>(IMSG_CURSOR_STATE_FILE, { lastRowid: rowid });
    } catch {
      // Best-effort -- a failed cursor write only widens the next restart's
      // replay window, it never drops a message.
    }
  }
}
