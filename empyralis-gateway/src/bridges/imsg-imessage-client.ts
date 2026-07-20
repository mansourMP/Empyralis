import { execFile } from "node:child_process";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { StringDecoder } from "node:string_decoder";
import { promisify } from "node:util";

// iMessage via the third-party `imsg` CLI (github.com/steipete/imsg), the
// same integration OpenClaw ships today (BlueBubbles support was removed
// there). The gateway spawns `imsg rpc --json` as a long-lived child process
// and talks newline-delimited JSON-RPC 2.0 over its stdin/stdout — no HTTP
// server, no port, no webhook, no password. Basic mode (outbound send,
// inbound watch, chat list) needs only a signed-in macOS Messages account
// and Full Disk Access to this process; no SIP change required.
//
// Framing/behavior verified against OpenClaw's
// extensions/imessage/src/client.ts (JSON-RPC-over-stdio client),
// probe.ts (layered health probe), and monitor/monitor-provider.ts (the
// watch.subscribe / "message" notification contract) — not guessed. See the
// method-by-method citations on each function below.

const IMESSAGE_CHANNEL_KEY = "imessage_personal";
const IMESSAGE_PROVIDER = "bluebubbles_local_bridge";

export const IMSG_DEFAULT_PROBE_TIMEOUT_MS = 15_000;

/** Same wording OpenClaw's client.ts surfaces for this exact failure mode
 *  (extensions/imessage/src/client.ts:43-44, PUBLIC_IMESSAGE_FULL_DISK_ACCESS_ERROR)
 *  — imsg's own stderr line names "Full Disk Access" + "chat.db" when the
 *  process running it cannot read ~/Library/Messages/chat.db. */
export const IMSG_FULL_DISK_ACCESS_ERROR =
  "imsg cannot access ~/Library/Messages/chat.db. Grant Full Disk Access to the Gateway process (System Settings > Privacy & Security > Full Disk Access) and restart the gateway.";

function normalizeLowercase(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

/** Mirrors OpenClaw's normalizeIMessageFullDiskAccessError (client.ts:54-60):
 *  any stderr line mentioning both "full disk access" and "chat.db" is
 *  recognized as this specific failure, regardless of imsg's exact wording. */
export function normalizeImsgFullDiskAccessError(message: string): string | undefined {
  const normalized = normalizeLowercase(message);
  if (!normalized.includes("full disk access") || !normalized.includes("chat.db")) {
    return undefined;
  }
  return IMSG_FULL_DISK_ACCESS_ERROR;
}

type JsonObject = Record<string, unknown>;

export interface ImsgRpcError {
  code?: number;
  message?: string;
  data?: unknown;
}

export interface ImsgRpcResponse {
  jsonrpc?: string;
  id?: string | number | null;
  result?: unknown;
  error?: ImsgRpcError;
  method?: string;
  params?: unknown;
}

export interface ImsgRpcNotification {
  method: string;
  params?: unknown;
}

/** Structural subset of node:child_process's ChildProcess this client
 *  actually needs — same "inject a fake, no mocking library needed" shape
 *  used by llm/cli-runner.ts's CliChildProcessLike, extended with a
 *  writable stdin because (unlike a one-shot CLI run) imsg rpc is a
 *  long-lived process the gateway writes JSON-RPC requests INTO. */
export interface ImsgChildProcessLike {
  stdin: {
    write(chunk: string, callback?: (err?: Error | null) => void): unknown;
    end(): unknown;
    on(event: "error", listener: (err: NodeJS.ErrnoException) => void): unknown;
  } | null;
  stdout: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown } | null;
  stderr: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown } | null;
  on(event: "error", listener: (err: NodeJS.ErrnoException) => void): unknown;
  on(event: "close", listener: (code: number | null, signal: NodeJS.Signals | null) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
  killed?: boolean;
}

export type ImsgSpawnImpl = (command: string, args: string[]) => ImsgChildProcessLike;

function defaultImsgSpawn(command: string, args: string[]): ImsgChildProcessLike {
  return spawn(command, args, { stdio: ["pipe", "pipe", "pipe"] }) as unknown as ImsgChildProcessLike;
}

export interface ImsgRpcClientOptions {
  cliPath?: string;
  dbPath?: string;
  spawnImpl?: ImsgSpawnImpl;
  onNotification?: (notification: ImsgRpcNotification) => void;
  onStderrLine?: (line: string) => void;
}

interface PendingImsgRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer?: NodeJS.Timeout;
}

/** JSON-RPC-over-stdio client for `imsg rpc --json`. Mirrors OpenClaw's
 *  IMessageRpcClient (extensions/imessage/src/client.ts:62-331): newline-
 *  delimited request/response framing, notifications distinguished by
 *  having a `method` but no `id` (client.ts:299-304), and the same
 *  Full-Disk-Access diagnostic capture off stderr (client.ts:307-309,
 *  buildCloseError at 311-320) so a process that dies from a permissions
 *  problem reports THAT, not a generic "imsg rpc closed". */
export class ImsgRpcClient {
  private readonly cliPath: string;
  private readonly dbPath?: string;
  private readonly spawnImpl: ImsgSpawnImpl;
  private readonly onNotification?: (notification: ImsgRpcNotification) => void;
  private readonly onStderrLine?: (line: string) => void;
  private readonly pending = new Map<string, PendingImsgRequest>();
  private child: ImsgChildProcessLike | null = null;
  private stdoutBuffer = "";
  private readonly stdoutDecoder = new StringDecoder("utf8");
  private nextId = 1;
  private publicProcessError: string | null = null;
  private closedResolve: (() => void) | null = null;
  private readonly closed: Promise<void>;

  constructor(opts: ImsgRpcClientOptions = {}) {
    this.cliPath = opts.cliPath?.trim() || "imsg";
    this.dbPath = opts.dbPath?.trim() || undefined;
    this.spawnImpl = opts.spawnImpl || defaultImsgSpawn;
    this.onNotification = opts.onNotification;
    this.onStderrLine = opts.onStderrLine;
    this.closed = new Promise((resolve) => {
      this.closedResolve = resolve;
    });
  }

  /** `imsg rpc --json [--db <path>]` — verified against OpenClaw's
   *  client.ts:93-99 (args build) and monitor-provider.ts's watch client,
   *  which spawns the identical cliPath with these args. */
  async start(): Promise<void> {
    if (this.child) {
      return;
    }
    const args = ["rpc", "--json"];
    if (this.dbPath) {
      args.push("--db", this.dbPath);
    }
    const child = this.spawnImpl(this.cliPath, args);
    this.child = child;

    child.stdout?.on("data", (chunk) => {
      if (this.child !== child) {
        return;
      }
      this.handleStdoutChunk(chunk);
    });

    child.stderr?.on("data", (chunk) => {
      const lines = String(chunk).split(/\r?\n/);
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) {
          continue;
        }
        this.recordProcessDiagnostic(trimmed);
        this.onStderrLine?.(trimmed);
      }
    });

    child.on("error", (err) => {
      // Null out this.child (e.g. ENOENT — binary not found) so a request()
      // called after this point fails fast with "imsg rpc not running"
      // instead of writing into a stdin stream attached to a process that
      // never actually started, which could otherwise hang until the
      // request's own timeout.
      if (this.child === child) {
        this.child = null;
      }
      this.failAll(err instanceof Error ? err : new Error(String(err)));
      this.closedResolve?.();
    });

    // Without this listener, an async EPIPE from a dead child crashes the
    // whole gateway process via uncaughtException — mirrors OpenClaw
    // client.ts:126-130's identical fix (#75438 there).
    child.stdin?.on("error", (err) => {
      this.failAll(err instanceof Error ? err : new Error(String(err)));
    });

    child.on("close", (code, signal) => {
      if (this.child === child) {
        this.flushStdoutBuffer();
        this.child = null;
      }
      this.failAll(this.buildCloseError(code, signal));
      this.closedResolve?.();
    });
  }

  async stop(): Promise<void> {
    if (!this.child) {
      return;
    }
    this.stdoutBuffer = "";
    this.stdoutDecoder.end();
    this.child.stdin?.end();
    const child = this.child;
    this.child = null;
    // Reject any request still in flight (e.g. a watch.subscribe the caller
    // is awaiting) immediately, rather than leaving it to resolve only via
    // the child's own close/error event (which a fake/dead process may
    // never emit) or its own per-request timeout — an explicit stop() means
    // "give up on this now."
    this.failAll(new Error("imsg rpc stopped"));

    await Promise.race([
      this.closed,
      new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          if (!child.killed) {
            child.kill("SIGTERM");
          }
          resolve();
        }, 500);
        timer.unref?.();
      }),
    ]);
    // Unblock anyone else awaiting waitForClose() (e.g. the caller's own
    // watch loop) even if the underlying process never actually emits a
    // 'close' event within (or after) the timeout above — an explicit
    // stop() is this client's own declaration that it is done, independent
    // of whether the OS process has technically exited yet.
    this.closedResolve?.();
  }

  async waitForClose(): Promise<void> {
    await this.closed;
  }

  /** Writes one `{jsonrpc:"2.0", id, method, params}\n` request line and
   *  resolves with `result` (or rejects with `error`) from the matching
   *  response line — same shape as OpenClaw's client.ts:168-218 `request`. */
  async request<T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    opts?: { timeoutMs?: number },
  ): Promise<T> {
    if (!this.child || !this.child.stdin) {
      throw new Error("imsg rpc not running");
    }
    const id = this.nextId++;
    const payload = { jsonrpc: "2.0", id, method, params: params ?? {} };
    const line = `${JSON.stringify(payload)}\n`;
    const timeoutMs = opts?.timeoutMs ?? IMSG_DEFAULT_PROBE_TIMEOUT_MS;

    const response = new Promise<T>((resolve, reject) => {
      const key = String(id);
      const timer =
        timeoutMs > 0
          ? setTimeout(() => {
              this.pending.delete(key);
              reject(new Error(`imsg rpc timeout (${method})`));
            }, timeoutMs)
          : undefined;
      this.pending.set(key, {
        resolve: (value) => resolve(value as T),
        reject,
        timer,
      });
    });

    this.child.stdin.write(line, (err) => {
      if (err) {
        const key = String(id);
        const pending = this.pending.get(key);
        if (pending) {
          if (pending.timer) {
            clearTimeout(pending.timer);
          }
          this.pending.delete(key);
          pending.reject(err instanceof Error ? err : new Error(String(err)));
        }
      }
    });
    return await response;
  }

  private handleStdoutChunk(chunk: Buffer | string): void {
    const text = typeof chunk === "string" ? chunk : this.stdoutDecoder.write(chunk);
    this.stdoutBuffer += text;
    let newlineIndex = this.stdoutBuffer.indexOf("\n");
    while (newlineIndex !== -1) {
      const line = this.stdoutBuffer.slice(0, newlineIndex);
      this.stdoutBuffer = this.stdoutBuffer.slice(newlineIndex + 1);
      this.handleStdoutLine(line);
      newlineIndex = this.stdoutBuffer.indexOf("\n");
    }
  }

  private flushStdoutBuffer(): void {
    const tail = this.stdoutDecoder.end();
    if (tail) {
      this.stdoutBuffer += tail;
    }
    if (!this.stdoutBuffer) {
      return;
    }
    const line = this.stdoutBuffer;
    this.stdoutBuffer = "";
    this.handleStdoutLine(line);
  }

  private handleStdoutLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) {
      return;
    }
    let parsed: ImsgRpcResponse;
    try {
      parsed = JSON.parse(trimmed) as ImsgRpcResponse;
    } catch {
      // Non-JSON stdout noise (imsg banner lines, etc.) — ignore.
      return;
    }

    if (parsed.id !== undefined && parsed.id !== null) {
      const key = String(parsed.id);
      const pending = this.pending.get(key);
      if (!pending) {
        return;
      }
      if (pending.timer) {
        clearTimeout(pending.timer);
      }
      this.pending.delete(key);
      if (parsed.error) {
        const baseMessage = parsed.error.message ?? "imsg rpc error";
        const details = parsed.error.data;
        const suffixes: string[] = [];
        if (typeof parsed.error.code === "number") {
          suffixes.push(`code=${parsed.error.code}`);
        }
        if (details !== undefined) {
          const detailText = typeof details === "string" ? details : JSON.stringify(details);
          if (detailText) {
            suffixes.push(detailText);
          }
        }
        pending.reject(new Error(suffixes.length ? `${baseMessage}: ${suffixes.join(" ")}` : baseMessage));
        return;
      }
      pending.resolve(parsed.result);
      return;
    }

    if (parsed.method) {
      this.onNotification?.({ method: parsed.method, params: parsed.params });
    }
  }

  private recordProcessDiagnostic(line: string): void {
    this.publicProcessError ??= normalizeImsgFullDiskAccessError(line) ?? null;
  }

  private buildCloseError(code: number | null, signal: NodeJS.Signals | null): Error {
    if (this.publicProcessError) {
      return new Error(this.publicProcessError);
    }
    if (code !== 0 && code !== null) {
      const reason = signal ? `signal ${signal}` : `code ${code}`;
      return new Error(`imsg rpc exited (${reason})`);
    }
    return new Error("imsg rpc closed");
  }

  private failAll(err: Error): void {
    for (const [key, pending] of this.pending.entries()) {
      if (pending.timer) {
        clearTimeout(pending.timer);
      }
      pending.reject(err);
      this.pending.delete(key);
    }
  }
}

// ---------------------------------------------------------------------
// Target parsing / send params — mirrors OpenClaw's targets.ts + chat.ts's
// buildChatTargetParams (chat.ts:20-46): a chat can be addressed by
// chat_id / chat_guid / chat_identifier, or (for a fresh DM) a raw handle.
// ---------------------------------------------------------------------

export type ImsgTarget =
  | { kind: "chat_id"; chatId: number }
  | { kind: "chat_guid"; chatGuid: string }
  | { kind: "chat_identifier"; chatIdentifier: string }
  | { kind: "handle"; to: string };

/** Parses a remote_jid built by mapImsgMessageToBridgeEvent back into the
 *  imsg send-target shape. Prefixes ("chat_guid:", "chat_identifier:",
 *  "chat_id:") are this bridge's own encoding, chosen so a reply always
 *  routes back to the exact same chat instead of imsg re-resolving (and
 *  possibly creating a new) chat from a bare handle. */
export function parseImsgTarget(remoteJid: string): ImsgTarget {
  const value = String(remoteJid || "").trim();
  if (value.startsWith("chat_guid:")) {
    return { kind: "chat_guid", chatGuid: value.slice("chat_guid:".length) };
  }
  if (value.startsWith("chat_identifier:")) {
    return { kind: "chat_identifier", chatIdentifier: value.slice("chat_identifier:".length) };
  }
  if (value.startsWith("chat_id:")) {
    const chatId = Number(value.slice("chat_id:".length));
    if (Number.isFinite(chatId)) {
      return { kind: "chat_id", chatId };
    }
  }
  return { kind: "handle", to: value };
}

/** Builds the `send` RPC's params object. Field names/shape verified
 *  against OpenClaw's send.ts:1003-1026 (`text`, `service`, `region`,
 *  `transport`, plus exactly one of chat_id/chat_guid/chat_identifier/to). */
export function buildImsgSendParams(remoteJid: string, text: string, opts: { replyTo?: string } = {}): JsonObject {
  const target = parseImsgTarget(remoteJid);
  const params: JsonObject = {
    text,
    service: "auto",
    region: "US",
    transport: "auto",
  };
  if (target.kind === "chat_id") {
    params.chat_id = target.chatId;
  } else if (target.kind === "chat_guid") {
    params.chat_guid = target.chatGuid;
  } else if (target.kind === "chat_identifier") {
    params.chat_identifier = target.chatIdentifier;
  } else {
    params.to = target.to;
  }
  if (opts.replyTo) {
    params.reply_to = opts.replyTo;
  }
  return params;
}

// ---------------------------------------------------------------------
// Inbound mapping — same BridgeEvent shape bluebubbles-bridge.ts and
// signal-cli-bridge.ts emit, so local-bridge-runtime.ts's downstream
// contract (and everything server-side that consumes channel.inbound)
// needs no changes. Field source: OpenClaw's monitor/types.ts IMessagePayload
// (id, guid, chat_id, chat_guid, chat_identifier, sender, is_from_me, text,
// attachments, is_group, reply_to_id, is_reaction — verified, not guessed).
// ---------------------------------------------------------------------

export interface BridgeEvent {
  external_message_id: string;
  remote_jid: string;
  sender_jid?: string;
  push_name?: string;
  text: string;
  received_at: string;
  from_me?: boolean;
  is_group?: boolean;
  is_mentioned?: boolean;
  is_reply_to_sage?: boolean;
}

export interface ImsgAttachment {
  original_path?: string | null;
  mime_type?: string | null;
  missing?: boolean | null;
  transfer_name?: string | null;
  uti?: string | null;
}

export interface ImsgMessagePayload {
  id?: number | null;
  guid?: string | null;
  chat_id?: number | null;
  chat_guid?: string | null;
  chat_identifier?: string | null;
  sender?: string | null;
  destination_caller_id?: string | null;
  is_from_me?: boolean | null;
  text?: string | null;
  reply_to_id?: number | string | null;
  created_at?: string | null;
  is_reaction?: boolean | null;
  is_tapback?: boolean | null;
  associated_message_guid?: string | null;
  attachments?: ImsgAttachment[] | null;
  is_group?: boolean | null;
}

function text(value: unknown): string {
  return String(value || "").trim();
}

/** Same `<media:attachment> (N)` fallback bluebubbles-bridge.ts:310 and
 *  signal-cli-bridge.ts's signalAttachmentPlaceholder use for an
 *  attachment-only (no caption) message — required so local-bridge-
 *  runtime.ts's mapInboundEvent (which drops anything with empty text)
 *  doesn't silently swallow the message. */
function imsgAttachmentPlaceholder(attachments: ImsgAttachment[] | null | undefined): string {
  const usable = (attachments || []).filter((entry) => !entry?.missing);
  return usable.length ? `<media:attachment> (${usable.length})` : "";
}

/** Encodes the chat target into remote_jid so a reply's buildImsgSendParams
 *  routes back to the exact same chat — see parseImsgTarget's doc comment. */
function imsgRemoteJid(message: ImsgMessagePayload): string {
  const chatGuid = text(message.chat_guid);
  if (chatGuid) {
    return `chat_guid:${chatGuid}`;
  }
  const chatIdentifier = text(message.chat_identifier);
  if (chatIdentifier) {
    return `chat_identifier:${chatIdentifier}`;
  }
  if (typeof message.chat_id === "number" && Number.isFinite(message.chat_id)) {
    return `chat_id:${message.chat_id}`;
  }
  return text(message.sender);
}

export interface MapImsgMessageOptions {
  /** External message keys (imsg guid and/or stringified rowid) this bridge
   *  has itself sent successfully — compared against an inbound message's
   *  reply_to_id to resolve is_reply_to_sage. Same contract as
   *  bluebubbles-bridge.ts's MapBlueBubblesWebhookOptions.sentMessageIds. */
  sentMessageIds?: Set<string>;
}

/** Maps one imsg `"message"` notification payload (OpenClaw
 *  monitor/monitor-provider.ts:1545 `msg.method === "message"`,
 *  params = IMessagePayload) into the shared BridgeEvent shape. Returns
 *  null for anything with no deliverable content (e.g. a bare tapback/
 *  reaction row, which imsg reports separately via is_reaction/is_tapback
 *  and which carries no plain text) — same "empty text => drop" contract
 *  bluebubbles-bridge.ts and signal-cli-bridge.ts already use. */
export function mapImsgMessageToBridgeEvent(
  message: ImsgMessagePayload,
  options: MapImsgMessageOptions = {},
): BridgeEvent | null {
  if (message.is_reaction === true || message.is_tapback === true) {
    return null;
  }
  const body = text(message.text);
  const messageText = body || imsgAttachmentPlaceholder(message.attachments);
  if (!messageText) {
    return null;
  }
  const remoteJid = imsgRemoteJid(message);
  if (!remoteJid) {
    return null;
  }
  const fromMe = message.is_from_me === true;
  const isGroup = message.is_group === true;
  const replyToKey = text(message.reply_to_id) || text(message.associated_message_guid);
  const isReplyToSage = isGroup && !fromMe && replyToKey
    ? Boolean(options.sentMessageIds?.has(replyToKey))
    : false;
  return {
    external_message_id: text(message.guid) || (message.id != null ? String(message.id) : randomUUID()),
    remote_jid: remoteJid,
    sender_jid: text(message.sender) || undefined,
    text: messageText,
    received_at: text(message.created_at) || new Date().toISOString(),
    from_me: fromMe,
    is_group: isGroup,
    // imsg's inbound message payload carries no structured "you were
    // @-mentioned" field (verified against OpenClaw's IMessagePayload type,
    // monitor/types.ts:37-63 — no mentions array) — same documented
    // always-false as bluebubbles-bridge.ts's identical field (see its
    // isBlueBubblesGroupChat block comment for the same rationale).
    is_mentioned: false,
    is_reply_to_sage: isReplyToSage,
  };
}

// ---------------------------------------------------------------------
// Layered health probe — mirrors OpenClaw's probe.ts staging: binary
// present -> `rpc` subcommand supported -> RPC actually responsive
// (chats.list succeeds). OpenClaw additionally probes `status --json` for
// private-API (advanced actions) availability (probe.ts:222-283); this
// gateway only needs basic-mode health (send/receive), so that stage is
// surfaced as informational, not gating.
// ---------------------------------------------------------------------

const execFileAsync = promisify(execFile);

export type ImsgExecImpl = (
  command: string,
  args: string[],
  opts: { timeoutMs: number },
) => Promise<{ code: number; stdout: string; stderr: string }>;

async function defaultImsgExec(
  command: string,
  args: string[],
  opts: { timeoutMs: number },
): Promise<{ code: number; stdout: string; stderr: string }> {
  try {
    const { stdout, stderr } = await execFileAsync(command, args, { timeout: opts.timeoutMs });
    return { code: 0, stdout: String(stdout), stderr: String(stderr) };
  } catch (error) {
    const err = error as NodeJS.ErrnoException & { code?: number | string; stdout?: string; stderr?: string };
    if (err.code === "ENOENT") {
      throw err;
    }
    return {
      code: typeof err.code === "number" ? err.code : 1,
      stdout: String(err.stdout || ""),
      stderr: String(err.stderr || err.message || ""),
    };
  }
}

export type ImsgProbeStage = "binary" | "rpc" | "chats";

export interface ImsgProbeResult {
  ok: boolean;
  stage?: ImsgProbeStage;
  error?: string;
  /** Best-effort — whether `imsg status --json` reports the private API
   *  (advanced actions: replies, tapbacks, launch-required features) is
   *  available. Never gates `ok`: basic mode works without it. */
  privateApiAvailable?: boolean;
}

export interface ImsgProbeOptions {
  dbPath?: string;
  timeoutMs?: number;
  execImpl?: ImsgExecImpl;
  spawnImpl?: ImsgSpawnImpl;
}

function parseImsgStatusPayload(stdout: string): JsonObject | null {
  const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (const line of lines.reverse()) {
    try {
      const parsed = JSON.parse(line);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as JsonObject;
      }
    } catch {
      // Keep scanning earlier JSONL records.
    }
  }
  return null;
}

/** Stage 1+2: binary present and the `rpc` subcommand is supported.
 *  Mirrors OpenClaw's detectBinary + probeRpcSupport (probe.ts:113-143). */
async function probeImsgBinaryAndRpc(
  cliPath: string,
  exec: ImsgExecImpl,
  timeoutMs: number,
): Promise<ImsgProbeResult | null> {
  try {
    const result = await exec(cliPath, ["rpc", "--help"], { timeoutMs });
    if (result.code !== 0) {
      return {
        ok: false,
        stage: "rpc",
        error: (result.stderr || result.stdout || `imsg rpc --help failed (code ${result.code})`).trim(),
      };
    }
    return null;
  } catch (error) {
    const err = error as NodeJS.ErrnoException;
    if (err.code === "ENOENT") {
      return { ok: false, stage: "binary", error: `imsg not found (${cliPath})` };
    }
    return { ok: false, stage: "binary", error: String(err.message || err) };
  }
}

/** Stage 3 (informational only): `imsg status --json` private-API flag.
 *  Field names verified against OpenClaw's probe.ts:240-241
 *  (`advanced_features`, `v2_ready`). */
async function probeImsgPrivateApi(
  cliPath: string,
  exec: ImsgExecImpl,
  timeoutMs: number,
): Promise<boolean | undefined> {
  try {
    const result = await exec(cliPath, ["status", "--json"], { timeoutMs });
    const payload = parseImsgStatusPayload(result.stdout);
    if (!payload) {
      return undefined;
    }
    return payload.advanced_features === true && payload.v2_ready === true;
  } catch {
    return undefined;
  }
}

/** Stage 4: an actual RPC round-trip (`chats.list`) — the only stage that
 *  proves imsg can read chat.db right now (Full Disk Access, signed-in
 *  Messages account). Mirrors OpenClaw's probeIMessage tail
 *  (probe.ts:324-336). */
async function probeImsgChatsList(
  cliPath: string,
  dbPath: string | undefined,
  spawnImpl: ImsgSpawnImpl | undefined,
  timeoutMs: number,
): Promise<ImsgProbeResult> {
  const client = new ImsgRpcClient({ cliPath, dbPath, spawnImpl });
  try {
    await client.start();
    await client.request("chats.list", { limit: 1 }, { timeoutMs });
    return { ok: true };
  } catch (error) {
    return { ok: false, stage: "chats", error: error instanceof Error ? error.message : String(error) };
  } finally {
    await client.stop();
  }
}

export async function probeImsgIMessage(
  cliPath: string,
  opts: ImsgProbeOptions = {},
): Promise<ImsgProbeResult> {
  const exec = opts.execImpl || defaultImsgExec;
  const timeoutMs = opts.timeoutMs ?? IMSG_DEFAULT_PROBE_TIMEOUT_MS;

  const binaryOrRpcFailure = await probeImsgBinaryAndRpc(cliPath, exec, timeoutMs);
  if (binaryOrRpcFailure) {
    return binaryOrRpcFailure;
  }

  const privateApiAvailable = await probeImsgPrivateApi(cliPath, exec, timeoutMs);
  const chatsResult = await probeImsgChatsList(cliPath, opts.dbPath, opts.spawnImpl, timeoutMs);
  return { ...chatsResult, privateApiAvailable };
}

// ---------------------------------------------------------------------
// Staged probe — the same underlying checks as probeImsgIMessage above,
// but reported per-stage instead of short-circuited to the first failure.
// Built for the in-app iMessage setup panel (frontend/lib/workspace/fleet/
// IMessageSetupPanel.tsx): that UI needs to show "binary present? / rpc
// responsive? / Full Disk Access ok? / private API (optional)" as four
// independent rows, each with its own honest pass/fail/blocked state and
// inline fix, not just one aggregate ok/error pair. "blocked" means an
// earlier stage failed so this one was never attempted — never fabricated
// as pass or fail.
// ---------------------------------------------------------------------

export type ImsgStageState = "pass" | "fail" | "blocked";

export interface ImsgStageDetail {
  state: ImsgStageState;
  error?: string;
}

export interface ImsgStagedProbeResult {
  /** ISO timestamp of when this probe ran — lets the UI show "last checked
   *  at" and distinguish a fresh live re-check from cached heartbeat state. */
  checkedAt: string;
  /** `imsg` binary found (ENOENT vs. found is the only signal available). */
  binary: ImsgStageDetail;
  /** `imsg rpc --help` exits 0 — the gateway can spawn imsg and talk to it. */
  rpc: ImsgStageDetail;
  /** `chats.list` round-trips over the RPC connection — the one stage that
   *  actually proves imsg can read ~/Library/Messages/chat.db right now
   *  (Full Disk Access) with a signed-in Messages account able to serve
   *  chat history. imsg reports Full Disk Access failures in a recognizable
   *  way (see normalizeImsgFullDiskAccessError); anything else that fails
   *  this stage (not signed in, a crashed process, an RPC timeout) is
   *  surfaced as its own raw error rather than mislabeled as an FDA issue. */
  fullDiskAccess: ImsgStageDetail & { isFullDiskAccessError: boolean };
  /** Optional/non-gating: whether `imsg status --json` reports the private
   *  API (SIP-off + helper-dylib injected) is available. `checked: false`
   *  means the probe could not determine this (status --json failed, timed
   *  out, or returned no parseable JSONL) — NOT that private API is
   *  unavailable; the UI must not conflate "unknown" with "fail" here. */
  privateApi: ImsgStageDetail & { checked: boolean };
}

const BLOCKED_STAGE_ERROR = "not checked — an earlier step failed first";

export async function probeImsgIMessageStaged(
  cliPath: string,
  opts: ImsgProbeOptions = {},
): Promise<ImsgStagedProbeResult> {
  const exec = opts.execImpl || defaultImsgExec;
  const timeoutMs = opts.timeoutMs ?? IMSG_DEFAULT_PROBE_TIMEOUT_MS;
  const checkedAt = new Date().toISOString();

  const binaryOrRpcFailure = await probeImsgBinaryAndRpc(cliPath, exec, timeoutMs);
  if (binaryOrRpcFailure) {
    const blockedFda: ImsgStagedProbeResult["fullDiskAccess"] = {
      state: "blocked",
      error: BLOCKED_STAGE_ERROR,
      isFullDiskAccessError: false,
    };
    const blockedPrivateApi: ImsgStagedProbeResult["privateApi"] = {
      state: "blocked",
      error: BLOCKED_STAGE_ERROR,
      checked: false,
    };
    if (binaryOrRpcFailure.stage === "binary") {
      return {
        checkedAt,
        binary: { state: "fail", error: binaryOrRpcFailure.error },
        rpc: { state: "blocked", error: BLOCKED_STAGE_ERROR },
        fullDiskAccess: blockedFda,
        privateApi: blockedPrivateApi,
      };
    }
    // binaryOrRpcFailure.stage is "rpc" here (probeImsgBinaryAndRpc only ever
    // returns "binary" or "rpc" — see its own return sites above).
    return {
      checkedAt,
      binary: { state: "pass" },
      rpc: { state: "fail", error: binaryOrRpcFailure.error },
      fullDiskAccess: blockedFda,
      privateApi: blockedPrivateApi,
    };
  }

  // Both remaining checks are independent RPC/exec round-trips once binary+
  // rpc are confirmed working — mirrors probeImsgIMessage's own ordering
  // (private API status, then chats.list), just captured instead of
  // discarded on the way to a single aggregate result.
  const privateApiAvailable = await probeImsgPrivateApi(cliPath, exec, timeoutMs);
  const chatsResult = await probeImsgChatsList(cliPath, opts.dbPath, opts.spawnImpl, timeoutMs);

  const fullDiskAccess: ImsgStagedProbeResult["fullDiskAccess"] = chatsResult.ok
    ? { state: "pass", isFullDiskAccessError: false }
    : {
        state: "fail",
        error: chatsResult.error || "imsg chats.list failed",
        isFullDiskAccessError: !!normalizeImsgFullDiskAccessError(chatsResult.error || ""),
      };

  const privateApi: ImsgStagedProbeResult["privateApi"] =
    privateApiAvailable === undefined
      ? {
          state: "blocked",
          checked: false,
          error: "imsg status --json did not report a usable private-API status",
        }
      : { state: privateApiAvailable ? "pass" : "fail", checked: true };

  return {
    checkedAt,
    binary: { state: "pass" },
    rpc: { state: "pass" },
    fullDiskAccess,
    privateApi,
  };
}

// ---------------------------------------------------------------------
// Homebrew auto-install — runs the exact command OpenClaw's own docs give
// for installing imsg (docs/channels/imessage.md, "Install and verify
// imsg" step): `brew install steipete/tap/imsg`. This IS the auto-install
// path (see IMessageSetupPanel.tsx / imsg-imessage-runtime.ts's "install"
// capability): the gateway already runs as the signed-in Mac user and
// already spawns `imsg` subprocesses directly for the RPC bridge, so
// running one more well-known Homebrew formula install under that same
// already-trusted local process does not cross into a new trust boundary.
// It is still gated behind an explicit user click in the setup panel
// (never run automatically/on a timer), and the manual command is ALWAYS
// also shown in the UI so a Mac without Homebrew (or a user who'd rather
// run it themselves) has a copy-pasteable fallback regardless of whether
// this succeeds.
// ---------------------------------------------------------------------

export interface ImsgHomebrewInstallResult {
  ok: boolean;
  /** False specifically means `brew` itself was not found (ENOENT) — the
   *  UI should point at the manual command rather than imply a broken
   *  install; true+ok:false means brew ran but the install itself failed. */
  brewFound: boolean;
  stdout: string;
  stderr: string;
  error?: string;
}

// Homebrew installs of a small formula are normally well under a minute,
// but a cold tap clone or a source build (no bottle for this macOS/arch
// combination) can take several minutes — generous but bounded so a truly
// stuck `brew` doesn't hang the setup panel's install button forever.
const HOMEBREW_INSTALL_TIMEOUT_MS = 5 * 60 * 1000;
const INSTALL_OUTPUT_TAIL_CHARS = 4000;

function tailOutput(text: string, maxChars: number): string {
  const value = String(text || "");
  return value.length > maxChars ? `…${value.slice(value.length - maxChars)}` : value;
}

export async function runImsgHomebrewInstall(
  execImpl: ImsgExecImpl = defaultImsgExec,
): Promise<ImsgHomebrewInstallResult> {
  try {
    const result = await execImpl("brew", ["install", "steipete/tap/imsg"], {
      timeoutMs: HOMEBREW_INSTALL_TIMEOUT_MS,
    });
    return {
      ok: result.code === 0,
      brewFound: true,
      stdout: tailOutput(result.stdout, INSTALL_OUTPUT_TAIL_CHARS),
      stderr: tailOutput(result.stderr, INSTALL_OUTPUT_TAIL_CHARS),
      ...(result.code === 0 ? {} : { error: `brew install exited with code ${result.code}` }),
    };
  } catch (error) {
    const err = error as NodeJS.ErrnoException;
    if (err.code === "ENOENT") {
      return {
        ok: false,
        brewFound: false,
        stdout: "",
        stderr: "",
        error: "Homebrew (`brew`) was not found on this Mac.",
      };
    }
    return { ok: false, brewFound: true, stdout: "", stderr: "", error: String(err.message || err) };
  }
}

export { IMESSAGE_CHANNEL_KEY, IMESSAGE_PROVIDER };
