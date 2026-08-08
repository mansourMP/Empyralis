/**
 * Hand-written ambient types for the slice of OpenClaw's public plugin
 * contract this plugin uses.
 *
 * Why hand-written instead of `import` from the real `openclaw` package:
 * this package intentionally carries zero runtime dependencies (see
 * package.json's description) so it stays trivially installable next to a
 * customer's OpenClaw install without version-locking to a specific
 * `openclaw` release. At OpenClaw's own plugin-load time, `openclaw/plugin-sdk/*`
 * resolves against the *host* gateway process, not this package's
 * node_modules — confirmed live 2026-08-08 against openclaw@2026.6.10 (see
 * scratchpad/openclaw-poc/gateway.log: the PoC plugin loaded and ran with no
 * node_modules of its own).
 *
 * Field shapes below are transcribed verbatim from the installed package's
 * own type declarations, audited 2026-08-08:
 *   /opt/homebrew/lib/node_modules/openclaw/dist/plugin-sdk/hook-types-*.d.ts
 * If OpenClaw changes these shapes in a later release, this file will not
 * catch the drift at compile time — `npm run typecheck` here only proves
 * internal consistency, not that these types still match a live gateway.
 * Re-audit against the installed openclaw version before bumping any pin.
 */
declare module "openclaw/plugin-sdk/plugin-entry" {
  export interface PluginHookMessageReceivedEvent {
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
    trace?: unknown;
    traceId?: string;
    spanId?: string;
    parentSpanId?: string;
    /**
     * NOT `isGroup`, NOT `wasMentioned` — confirmed absent from this event's
     * real shape even though `inbound_claim`'s event has both. See
     * scratchpad/openclaw-issue-draft.md's 2026-08-08 addendum. `metadata`
     * is the only place any group/channel-shape signal can come from here.
     */
    metadata?: Record<string, unknown>;
  }

  export interface PluginHookMessageContext {
    channelId?: string;
    accountId?: string;
    conversationId?: string;
    sessionKey?: string;
    runId?: string;
    messageId?: string;
    senderId?: string;
    replyToId?: string;
    replyToIdFull?: string;
    replyToBody?: string;
    replyToSender?: string;
    replyToIsQuote?: boolean;
    traceId?: string;
    spanId?: string;
    parentSpanId?: string;
    callDepth?: number;
  }

  export interface PluginHookMessageSendingEvent {
    to: string;
    content: string;
    replyToId?: string | number;
    threadId?: string | number;
    metadata?: Record<string, unknown>;
  }

  export interface PluginHookMessageSendingResult {
    content?: string;
    cancel?: boolean;
    cancelReason?: string;
    metadata?: Record<string, unknown>;
  }

  export interface PluginHookMessageSentEvent {
    to: string;
    content: string;
    success: boolean;
    messageId?: string;
    sessionKey?: string;
    runId?: string;
    error?: string;
  }

  export interface PluginHookGatewayStartEvent {
    [key: string]: unknown;
  }

  export interface PluginHookGatewayContext {
    config?: Record<string, unknown>;
    workspaceDir?: string;
    getCron?: () => unknown;
  }

  export interface PluginHookGatewayStopEvent {
    [key: string]: unknown;
  }

  export interface PluginHookApi {
    on(
      name: "gateway_start",
      handler: (event: PluginHookGatewayStartEvent, ctx: PluginHookGatewayContext) => Promise<void> | void,
      opts?: { priority?: number; timeoutMs?: number },
    ): void;
    on(
      name: "gateway_stop",
      handler: (event: PluginHookGatewayStopEvent, ctx: PluginHookGatewayContext) => Promise<void> | void,
      opts?: { priority?: number; timeoutMs?: number },
    ): void;
    on(
      name: "message_received",
      handler: (
        event: PluginHookMessageReceivedEvent,
        ctx: PluginHookMessageContext,
      ) => Promise<void> | void,
      opts?: { priority?: number; timeoutMs?: number },
    ): void;
    on(
      name: "message_sending",
      handler: (
        event: PluginHookMessageSendingEvent,
        ctx: PluginHookMessageContext,
      ) => Promise<PluginHookMessageSendingResult | void> | PluginHookMessageSendingResult | void,
      opts?: { priority?: number; timeoutMs?: number },
    ): void;
    on(
      name: "message_sent",
      handler: (
        event: PluginHookMessageSentEvent,
        ctx: PluginHookMessageContext,
      ) => Promise<void> | void,
      opts?: { priority?: number; timeoutMs?: number },
    ): void;
  }

  export interface PluginEntryDefinition {
    id: string;
    name?: string;
    description?: string;
    register(api: PluginHookApi): void;
  }

  export function definePluginEntry(def: PluginEntryDefinition): PluginEntryDefinition;
}
