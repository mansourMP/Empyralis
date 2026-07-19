import http from "node:http";
import { randomUUID } from "node:crypto";

const SIGNAL_CHANNEL_KEY = "signal_personal";
const SIGNAL_PROVIDER = "signal_local_bridge";

type BridgeEvent = {
  external_message_id: string;
  remote_jid: string;
  sender_jid?: string;
  push_name?: string;
  text: string;
  received_at: string;
  from_me?: boolean;
  is_self_chat?: boolean;
  is_group?: boolean;
  is_mentioned?: boolean;
  is_reply_to_sage?: boolean;
};

/** Caps how many of this bridge's own sent-message timestamps are kept for
 *  is_reply_to_sage matching — mirrors the WhatsApp/Telegram gateway
 *  runtime's sentMessageIds cap exactly (same rationale: bounded memory on
 *  a long-lived connection, 500 is far more than any realistic reply
 *  window needs). */
const SENT_MESSAGE_ID_CACHE_LIMIT = 500;

type JsonObject = Record<string, unknown>;

export interface SignalCliBridgeOptions {
  host?: string;
  port?: number;
  signalCliRpcUrl: string;
  account?: string;
  token?: string;
  connectEvents?: boolean;
}

export interface SignalCliBridge {
  url: string;
  close: () => Promise<void>;
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function normalizeSignalCliBaseUrl(value: string): string {
  const token = trimTrailingSlash(String(value || "").trim());
  if (!token) {
    throw new Error("EMPYRALIS_SIGNAL_CLI_RPC_URL is required for the Signal bridge.");
  }
  const parsed = new URL(token);
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("EMPYRALIS_SIGNAL_CLI_RPC_URL must be an HTTP(S) URL.");
  }
  return token;
}

function normalizeChannelKey(value: unknown): string {
  return String(value || "").trim() || SIGNAL_CHANNEL_KEY;
}

function parseJsonBody(request: http.IncomingMessage): Promise<JsonObject> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 256 * 1024) {
        reject(new Error("request_body_too_large"));
        request.destroy();
      }
    });
    request.on("end", () => {
      if (!body.trim()) {
        resolve({});
        return;
      }
      try {
        const parsed = JSON.parse(body);
        resolve(parsed && typeof parsed === "object" ? parsed as JsonObject : {});
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function sendJson(response: http.ServerResponse, statusCode: number, payload: JsonObject): void {
  response.writeHead(statusCode, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}

function authorizeRequest(request: http.IncomingMessage, token?: string): boolean {
  if (!token) {
    return true;
  }
  const header = String(request.headers.authorization || "").trim();
  return header === `Bearer ${token}`;
}

function buildSignalCliSendParams(account: string | undefined, remoteJid: string, text: string): JsonObject {
  const params: JsonObject = {
    message: text,
  };
  if (account) {
    params.account = account;
  }
  if (remoteJid.startsWith("group:")) {
    params.groupId = remoteJid.slice("group:".length);
  } else {
    params.recipient = [remoteJid];
  }
  return params;
}

/** Params for signal-cli's JSON-RPC "sendTyping" method — recipient/groupId
 *  + account exactly like buildSignalCliSendParams above, plus `stop: true`
 *  to clear an in-flight typing indicator. Verified against OpenClaw's
 *  compiled Signal extension (sendTypingSignal in its send-*.js bundle),
 *  which builds the identical {recipient|groupId, account?, stop?} shape
 *  before calling signalRpcRequest("sendTyping", params, ...) — not
 *  guessed. */
function buildSignalCliTypingParams(account: string | undefined, remoteJid: string, stop: boolean): JsonObject {
  const params: JsonObject = {};
  if (account) {
    params.account = account;
  }
  if (remoteJid.startsWith("group:")) {
    params.groupId = remoteJid.slice("group:".length);
  } else {
    params.recipient = [remoteJid];
  }
  if (stop) {
    params.stop = true;
  }
  return params;
}

async function callSignalCliRpc(
  signalCliBaseUrl: string,
  method: string,
  params: JsonObject,
): Promise<JsonObject> {
  const response = await fetch(`${signalCliBaseUrl}/api/v1/rpc`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      method,
      id: randomUUID(),
      params,
    }),
  });
  if (!response.ok) {
    throw new Error(`signal-cli JSON-RPC returned HTTP ${response.status}`);
  }
  const payload = await response.json() as JsonObject;
  if (payload.error && typeof payload.error === "object") {
    const error = payload.error as JsonObject;
    throw new Error(String(error.message || "signal-cli JSON-RPC error"));
  }
  return payload.result && typeof payload.result === "object" ? payload.result as JsonObject : {};
}

function eventText(value: unknown): string {
  return String(value || "").trim();
}

function eventTimestamp(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(numeric) && numeric > 0) {
    return new Date(numeric).toISOString();
  }
  const token = eventText(value);
  return token || new Date().toISOString();
}

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" ? value as JsonObject : {};
}

function signalEnvelope(notification: JsonObject): JsonObject {
  const params = asObject(notification.params);
  const wrappedResult = asObject(params.result);
  return asObject(wrappedResult.envelope || params.envelope);
}

/** signal-cli's own JsonGroupInfo (org.asamk.signal.json.JsonGroupInfo)
 *  serializes groupId as base64 — this is the same convention
 *  buildSignalCliSendParams above expects back via the "group:" prefix
 *  (remoteJid.slice("group:".length) is handed straight to signal-cli's
 *  groupId send param), so encoding it here is what makes an inbound group
 *  message's reply actually route back to the group instead of into a
 *  stray 1:1 chat with whichever member happened to send it. */
function signalGroupRemoteJid(groupInfo: JsonObject): string | undefined {
  const groupId = eventText(groupInfo.groupId);
  return groupId ? `group:${groupId}` : undefined;
}

/** True if any entry of a signal-cli dataMessage.mentions[] array
 *  (JsonMention: {name, number, uuid, start, length}) identifies this
 *  bridge's own configured account — i.e. this bridge's linked user was
 *  explicitly @-mentioned in the group message. */
function signalMentionsMatchAccount(mentions: unknown, account: string): boolean {
  if (!account || !Array.isArray(mentions)) {
    return false;
  }
  return mentions.some((entry) => {
    const mention = asObject(entry);
    return eventText(mention.number) === account || eventText(mention.uuid) === account;
  });
}

/** signal-cli's own JsonAttachment (org.asamk.signal.json.JsonAttachment)
 *  array on a dataMessage/sentMessage — each entry carries at least `id`
 *  and `contentType` (a MIME string); see AsamK/signal-cli's JSON-RPC
 *  output schema (verified against OpenClaw's compiled Signal extension,
 *  which reads the identical `attachment.contentType`/`attachment.id`
 *  fields). This bridge does not fetch attachment bytes — that's a real,
 *  separate gap (see docs/OpenClaw.md's Signal section) — but an
 *  attachment-only message (no caption) must still produce SOME text, or
 *  local-bridge-runtime.ts's mapInboundEvent (which requires a non-empty
 *  text field) silently drops the message entirely. Mirrors
 *  bluebubbles-bridge.ts's identical `<media:attachment> (N)` fallback for
 *  iMessage. */
function signalAttachmentPlaceholder(attachments: unknown): string {
  const list = Array.isArray(attachments) ? attachments : [];
  return list.length ? `<media:attachment> (${list.length})` : "";
}

export interface MapSignalCliReceiveOptions {
  /** This bridge's own signal-cli account (EMPYRALIS_SIGNAL_CLI_ACCOUNT,
   *  typically an E.164 phone number) — compared against
   *  dataMessage.mentions[].number to resolve is_mentioned, and against a
   *  fromMe message's own remoteJid to resolve is_self_chat (a "Note to
   *  Self" send has destination === this same account). Both stay false
   *  when this isn't configured, since there is then no reliable identity
   *  to match against — safe default, the group gate still applies via
   *  is_group. */
  account?: string;
  /** External message ids (signal-cli timestamps, stringified) this bridge
   *  has itself sent successfully — see SENT_MESSAGE_ID_CACHE_LIMIT. A
   *  Signal "quote" identifies the original message purely by
   *  (author, timestamp); there's no separate opaque message id, so
   *  dataMessage.quote.id matching one of these timestamps is exactly
   *  "someone replied to a message Sage sent" — the same is_reply_to_sage
   *  contract WhatsApp/Telegram resolve via their own sentMessageIds sets. */
  sentMessageIds?: Set<string>;
}

export function mapSignalCliReceiveNotification(
  notification: JsonObject,
  options: MapSignalCliReceiveOptions = {},
): BridgeEvent | null {
  if (eventText(notification.method) !== "receive") {
    return null;
  }
  const envelope = signalEnvelope(notification);
  const dataMessage = asObject(envelope.dataMessage);
  const syncMessage = asObject(envelope.syncMessage);
  const sentMessage = asObject(syncMessage.sentMessage);
  const incomingText = eventText(dataMessage.message);
  const syncText = eventText(sentMessage.message);
  // Attachment-only (no caption) messages have empty incomingText/syncText
  // but a non-empty attachments array — without this, such a message would
  // fail the `if (!text) return null` check below and vanish silently. See
  // signalAttachmentPlaceholder's doc comment for what this fallback is
  // (and isn't).
  const incomingAttachments = Array.isArray(dataMessage.attachments) ? dataMessage.attachments : [];
  const syncAttachments = Array.isArray(sentMessage.attachments) ? sentMessage.attachments : [];
  const hasIncoming = Boolean(incomingText) || incomingAttachments.length > 0;
  const hasSync = Boolean(syncText) || syncAttachments.length > 0;
  const text = incomingText || syncText || signalAttachmentPlaceholder(hasIncoming ? incomingAttachments : syncAttachments);
  if (!text) {
    return null;
  }
  const fromMe = !hasIncoming && hasSync;
  // A group message's dataMessage/sentMessage carries groupInfo.groupId
  // instead of (only) an individual source/destination — checked first so
  // remoteJid below prefers the group's own address over the individual
  // sender's, for both is_group's meaning and correct reply routing.
  const groupInfo = asObject(fromMe ? sentMessage.groupInfo : dataMessage.groupInfo);
  const isGroup = Boolean(eventText(groupInfo.groupId));
  const source = eventText(envelope.sourceNumber || envelope.source || envelope.sourceUuid);
  const destination = eventText(sentMessage.destinationNumber || sentMessage.destination || sentMessage.destinationUuid);
  const remoteJid = (isGroup ? signalGroupRemoteJid(groupInfo) : undefined) || (fromMe ? destination : source);
  if (!remoteJid) {
    return null;
  }
  const timestamp = envelope.timestamp || dataMessage.timestamp || sentMessage.timestamp;
  const externalMessageId = eventText(timestamp) || randomUUID();
  // Mention/reply detection only makes sense for a genuine incoming group
  // message — a self-sent echo (fromMe, via syncMessage) can't mention or
  // reply to "Sage" in any meaningful sense.
  const isMentioned = isGroup && !fromMe
    ? signalMentionsMatchAccount(dataMessage.mentions, eventText(options.account))
    : false;
  const quoteId = eventText(asObject(dataMessage.quote).id);
  const isReplyToSage = isGroup && !fromMe && quoteId
    ? Boolean(options.sentMessageIds?.has(quoteId))
    : false;
  // "Note to Self": a fromMe sync whose destination IS this bridge's own
  // configured account — the Signal analog of WhatsApp's/Telegram's
  // is_self_chat command channel (see whatsapp/message-mapper.ts's
  // identical `remoteJid === ownedJid` contract). Structurally just an
  // ordinary 1:1 send that happens to target yourself; groups are excluded
  // since a group remoteJid ("group:...") can never equal a bare account id.
  const isSelfChat = fromMe && !isGroup && Boolean(options.account) && remoteJid === eventText(options.account);
  // LOOP GUARD: self-chat is deliberately let through the from_me gate
  // downstream (personal_channels_service.py's
  // _handle_local_bridge_gateway_channel_inbound) as an owner command
  // channel — but signal-cli syncs EVERY send this bridge itself makes
  // (including Sage's own reply INTO self-chat) back through this exact
  // "receive" notification path via syncMessage.sentMessage, the same
  // mechanism fromMe/hasSync above already rely on. Without this guard,
  // Sage's own self-chat reply would echo back as a fresh
  // is_self_chat=true "command," re-triggering another agent turn,
  // forever — the exact bug class WhatsApp's/Telegram's own self-chat
  // loop guards exist for (see whatsapp/runtime.ts's isOwnSelfChatEcho and
  // whatsapp-self-chat.test.ts). sentMessageIds already records every
  // timestamp this bridge's own /messages handler successfully sent (see
  // MapSignalCliReceiveOptions doc comment above); a Signal sync echo's
  // envelope timestamp equals the ORIGINAL send's timestamp (the same
  // identity relationship is_reply_to_sage's quote-matching above already
  // depends on), so membership here reliably means "this is our own echo,
  // not a new owner message." Unlike WhatsApp/Telegram (live socket
  // events), this bridge is polled over HTTP by local-bridge-runtime.ts
  // (5s default interval) rather than delivered instantly, which gives
  // sentMessageIds.add() (synchronous, right after the send RPC resolves)
  // ample time to land before the next poll — so no WhatsApp-style
  // race-window/text-match fallback layer is needed here.
  const isSelfChatEcho = isSelfChat && Boolean(options.sentMessageIds?.has(externalMessageId));
  if (isSelfChatEcho) {
    return null;
  }
  return {
    external_message_id: externalMessageId,
    remote_jid: remoteJid,
    sender_jid: source || undefined,
    push_name: eventText(envelope.sourceName) || undefined,
    text,
    received_at: eventTimestamp(timestamp),
    from_me: fromMe,
    is_self_chat: isSelfChat,
    is_group: isGroup,
    is_mentioned: isMentioned,
    is_reply_to_sage: isReplyToSage,
  };
}

function parseSseChunk(chunk: string): JsonObject[] {
  const payloads: JsonObject[] = [];
  for (const block of chunk.split(/\n\n+/)) {
    const dataLines = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice("data:".length).trim())
      .filter(Boolean);
    if (!dataLines.length) {
      continue;
    }
    try {
      const parsed = JSON.parse(dataLines.join("\n"));
      if (parsed && typeof parsed === "object") {
        payloads.push(parsed as JsonObject);
      }
    } catch {
      // Ignore malformed third-party SSE records; health will surface reconnect state.
    }
  }
  return payloads;
}

async function connectSignalCliEvents(
  signalCliBaseUrl: string,
  enqueue: (event: BridgeEvent) => void,
  controller: AbortController,
  mapOptions: MapSignalCliReceiveOptions,
): Promise<void> {
  const response = await fetch(`${signalCliBaseUrl}/api/v1/events`, {
    method: "GET",
    headers: { accept: "text/event-stream" },
    signal: controller.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`signal-cli events returned HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!controller.signal.aborted) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const lastBoundary = buffer.lastIndexOf("\n\n");
    if (lastBoundary < 0) {
      continue;
    }
    const complete = buffer.slice(0, lastBoundary + 2);
    buffer = buffer.slice(lastBoundary + 2);
    for (const notification of parseSseChunk(complete)) {
      const event = mapSignalCliReceiveNotification(notification, mapOptions);
      if (event) {
        enqueue(event);
      }
    }
  }
}

export async function startSignalCliBridge(options: SignalCliBridgeOptions): Promise<SignalCliBridge> {
  const host = options.host || "127.0.0.1";
  const signalCliBaseUrl = normalizeSignalCliBaseUrl(options.signalCliRpcUrl);
  const account = String(options.account || "").trim() || undefined;
  const token = String(options.token || "").trim() || undefined;
  const eventsByChannel = new Map<string, BridgeEvent[]>();
  const eventController = new AbortController();
  // Sent-message timestamps, for is_reply_to_sage matching against an
  // inbound dataMessage.quote.id — see MapSignalCliReceiveOptions and the
  // /messages POST handler below (where this is populated).
  const sentMessageIds = new Set<string>();

  const enqueue = (event: BridgeEvent): void => {
    const items = eventsByChannel.get(SIGNAL_CHANNEL_KEY) || [];
    items.push(event);
    eventsByChannel.set(SIGNAL_CHANNEL_KEY, items);
  };

  if (options.connectEvents !== false) {
    void connectSignalCliEvents(signalCliBaseUrl, enqueue, eventController, { account, sentMessageIds }).catch(() => undefined);
  }

  const server = http.createServer(async (request, response) => {
    try {
      if (!authorizeRequest(request, token)) {
        sendJson(response, 401, { error: "unauthorized" });
        return;
      }
      const url = new URL(request.url || "/", `http://${host}`);
      if (request.method === "GET" && url.pathname === "/health") {
        try {
          const check = await fetch(`${signalCliBaseUrl}/api/v1/check`);
          sendJson(response, check.ok ? 200 : 503, {
            status: check.ok ? "connected" : "unavailable",
            connected: check.ok,
            provider: SIGNAL_PROVIDER,
            channel_keys: [SIGNAL_CHANNEL_KEY],
            account_configured: Boolean(account),
            issues: check.ok ? [] : ["signal_cli_check_failed"],
          });
        } catch (error) {
          sendJson(response, 503, {
            status: "unavailable",
            connected: false,
            provider: SIGNAL_PROVIDER,
            channel_keys: [SIGNAL_CHANNEL_KEY],
            account_configured: Boolean(account),
            last_error: error instanceof Error ? error.message : String(error),
            issues: ["signal_cli_unavailable"],
          });
        }
        return;
      }
      if (request.method === "POST" && url.pathname === "/messages") {
        const body = await parseJsonBody(request);
        const channelKey = normalizeChannelKey(body.channel_key);
        const remoteJid = eventText(body.remote_jid);
        const text = eventText(body.text);
        if (channelKey !== SIGNAL_CHANNEL_KEY) {
          sendJson(response, 400, { error: "unsupported_channel" });
          return;
        }
        if (!remoteJid || !text) {
          sendJson(response, 400, { error: "remote_jid_and_text_required" });
          return;
        }
        const result = await callSignalCliRpc(
          signalCliBaseUrl,
          "send",
          buildSignalCliSendParams(account, remoteJid, text),
        );
        const timestamp = result.timestamp || result.timestamps;
        const externalMessageId = eventText(timestamp) || `signal-${Date.now()}`;
        // Track for is_reply_to_sage: a Signal quote identifies the
        // original message by timestamp, and this send's timestamp IS that
        // identifier for whatever we just sent — see
        // MapSignalCliReceiveOptions.sentMessageIds.
        sentMessageIds.add(externalMessageId);
        if (sentMessageIds.size > SENT_MESSAGE_ID_CACHE_LIMIT) {
          sentMessageIds.clear();
        }
        sendJson(response, 200, {
          delivered: true,
          status: "sent",
          channel_key: SIGNAL_CHANNEL_KEY,
          provider: SIGNAL_PROVIDER,
          external_message_id: externalMessageId,
        });
        return;
      }
      if (request.method === "GET" && url.pathname === "/events") {
        const channelKey = normalizeChannelKey(url.searchParams.get("channel_key"));
        if (channelKey !== SIGNAL_CHANNEL_KEY) {
          sendJson(response, 200, { items: [] });
          return;
        }
        const items = eventsByChannel.get(SIGNAL_CHANNEL_KEY) || [];
        eventsByChannel.set(SIGNAL_CHANNEL_KEY, []);
        sendJson(response, 200, { items });
        return;
      }
      if (request.method === "POST" && url.pathname === "/typing") {
        const body = await parseJsonBody(request);
        const channelKey = normalizeChannelKey(body.channel_key);
        const remoteJid = eventText(body.remote_jid);
        const action = eventText(body.action) === "stop" ? "stop" : "start";
        if (channelKey !== SIGNAL_CHANNEL_KEY) {
          sendJson(response, 400, { error: "unsupported_channel" });
          return;
        }
        if (!remoteJid) {
          sendJson(response, 400, { error: "remote_jid_required" });
          return;
        }
        try {
          await callSignalCliRpc(
            signalCliBaseUrl,
            "sendTyping",
            buildSignalCliTypingParams(account, remoteJid, action === "stop"),
          );
          sendJson(response, 200, { ok: true, channel_key: SIGNAL_CHANNEL_KEY, action });
        } catch (error) {
          // Typing indicators are cosmetic. A signal-cli build without
          // sendTyping support, or a transient RPC hiccup, must never look
          // like a message-delivery failure to the caller (which already
          // treats this endpoint as best-effort — see
          // local-bridge-runtime.ts's sendTypingAction) — a soft 200 keeps
          // it that way instead of surfacing as a 5xx.
          sendJson(response, 200, {
            ok: false,
            channel_key: SIGNAL_CHANNEL_KEY,
            action,
            error: error instanceof Error ? error.message : String(error),
          });
        }
        return;
      }
      sendJson(response, 404, { error: "not_found" });
    } catch (error) {
      sendJson(response, 500, { error: error instanceof Error ? error.message : String(error) });
    }
  });

  await new Promise<void>((resolve) => {
    server.listen(options.port || 0, host, resolve);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : options.port || 0;
  return {
    url: `http://${host}:${port}`,
    close: async () => {
      eventController.abort();
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}

async function main(): Promise<void> {
  const port = Number(process.env.EMPYRALIS_SIGNAL_BRIDGE_PORT || "8901");
  const bridge = await startSignalCliBridge({
    port,
    signalCliRpcUrl: String(process.env.EMPYRALIS_SIGNAL_CLI_RPC_URL || "").trim(),
    account: String(process.env.EMPYRALIS_SIGNAL_CLI_ACCOUNT || "").trim() || undefined,
    token: String(process.env.EMPYRALIS_SIGNAL_BRIDGE_TOKEN || "").trim() || undefined,
  });
  console.log(`Signal Agent Computer bridge listening on ${bridge.url}`);
  console.log(`export EMPYRALIS_SIGNAL_BRIDGE_URL=${bridge.url}`);
  if (process.env.EMPYRALIS_SIGNAL_BRIDGE_TOKEN) {
    console.log("EMPYRALIS_SIGNAL_BRIDGE_TOKEN is configured; set the same value in the gateway environment.");
  }
}

if (require.main === module) {
  void main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
