/**
 * Pure mapping: OpenClaw's `message_received` hook event -> the payload we
 * forward to Empyralis. No OpenClaw imports here on purpose — this file (and
 * its test) must typecheck and run without the plugin ever being loaded by a
 * real gateway.
 *
 * Field-shape source of truth: `dist/message-hook-mappers-*.js`'s
 * `toPluginMessageReceivedEvent` and `dist/plugin-sdk/hook-types-*.d.ts`'s
 * `PluginHookMessageReceivedEvent`, both audited 2026-08-08 against
 * openclaw@2026.6.10.
 */

export interface InboundMessageReceivedEvent {
  from: string;
  content: string;
  timestamp?: number;
  threadId?: string | number;
  messageId?: string;
  senderId?: string;
  replyToId?: string;
  replyToIdFull?: string;
  replyToBody?: string;
  replyToSender?: string;
  replyToIsQuote?: boolean;
  sessionKey?: string;
  runId?: string;
  metadata?: Record<string, unknown>;
}

export interface InboundMessageContext {
  channelId?: string;
  accountId?: string;
  conversationId?: string;
  sessionKey?: string;
}

/**
 * `message_received`'s real, shipped shape has no `isGroup` field — unlike
 * `inbound_claim`'s event, which does (see types/openclaw-plugin-sdk.d.ts's
 * header comment and scratchpad/openclaw-issue-draft.md's 2026-08-08
 * addendum). `metadata.channelName` is the closest public proxy: OpenClaw's
 * own internal `isGroup` is `Boolean(ctx.GroupSubject || ctx.GroupChannel)`,
 * and `channelName` is `ctx.GroupChannel` forwarded into metadata. It is not
 * guaranteed present for every one of the 23 channels' group shapes, so this
 * returns `true` when there's positive evidence and `undefined` — not
 * `false` — otherwise. Callers (Empyralis's own three-gate machinery) must
 * treat `undefined` as "unknown, gate conservatively," never as "not a
 * group."
 */
export function deriveIsGroupBestEffort(event: InboundMessageReceivedEvent): boolean | undefined {
  const metadata = event.metadata ?? {};
  const channelName = metadata["channelName"];
  const guildId = metadata["guildId"];
  if (typeof channelName === "string" && channelName.trim().length > 0) return true;
  if (typeof guildId === "string" && guildId.trim().length > 0) return true;
  if (typeof guildId === "number" && Number.isFinite(guildId)) return true;
  return undefined;
}

export interface EmpyralisInboundPayload {
  /** Stable schema tag so Empyralis's endpoint can version this shape independently of OpenClaw's own hook contract churn. */
  schema: "empyralis.openclaw_bridge.inbound.v1";
  channel: string | undefined;
  accountId: string | undefined;
  conversationId: string | undefined;
  senderId: string | undefined;
  messageId: string | undefined;
  content: string;
  timestamp: number | undefined;
  threadId: string | number | undefined;
  isGroup: boolean | undefined;
  /** Always `undefined` today — see deriveIsGroupBestEffort's doc comment. Present so Empyralis's schema doesn't silently drop the field if a future OpenClaw release starts sending it. */
  wasMentioned: boolean | undefined;
  isReply: boolean;
  replyToId: string | undefined;
  replyToSender: string | undefined;
  media: {
    path: string | undefined;
    url: string | undefined;
    type: string | undefined;
    paths: string[] | undefined;
    urls: string[] | undefined;
    types: string[] | undefined;
  };
  /** Raw metadata forwarded as-is so Empyralis's gate logic can use richer signals than this mapper knows how to name. */
  rawMetadata: Record<string, unknown> | undefined;
  /** OpenClaw's own session correlation, forwarded so a later outbound send RPC can be tied back to the conversation that triggered it. */
  openclawSessionKey: string | undefined;
  openclawRunId: string | undefined;
  receivedAt: string;
}

function readStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const strings = value.filter((item): item is string => typeof item === "string" && item.length > 0);
  return strings.length > 0 ? strings : undefined;
}

export function mapInboundEvent(
  event: InboundMessageReceivedEvent,
  ctx: InboundMessageContext,
  now: () => Date = () => new Date(),
): EmpyralisInboundPayload {
  const metadata = event.metadata ?? {};
  return {
    schema: "empyralis.openclaw_bridge.inbound.v1",
    channel: ctx.channelId,
    accountId: ctx.accountId,
    conversationId: ctx.conversationId,
    senderId: event.senderId,
    messageId: event.messageId,
    content: event.content ?? "",
    timestamp: event.timestamp,
    threadId: event.threadId,
    isGroup: deriveIsGroupBestEffort(event),
    wasMentioned: undefined,
    isReply: Boolean(event.replyToId || event.replyToIdFull),
    replyToId: event.replyToId ?? event.replyToIdFull,
    replyToSender: event.replyToSender,
    media: {
      path: typeof metadata["mediaPath"] === "string" ? (metadata["mediaPath"] as string) : undefined,
      url: typeof metadata["mediaUrl"] === "string" ? (metadata["mediaUrl"] as string) : undefined,
      type: typeof metadata["mediaType"] === "string" ? (metadata["mediaType"] as string) : undefined,
      paths: readStringArray(metadata["mediaPaths"]),
      urls: readStringArray(metadata["mediaUrls"]),
      types: readStringArray(metadata["mediaTypes"]),
    },
    rawMetadata: event.metadata,
    openclawSessionKey: event.sessionKey ?? ctx.sessionKey,
    openclawRunId: event.runId,
    receivedAt: now().toISOString(),
  };
}
