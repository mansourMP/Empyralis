/**
 * Inbound message debounce / coalesce for personal channels.
 *
 * The founder's complaint: "if I type 5 messages fast, it replies 5 times;
 * OpenClaw waits and replies once." Each inbound message today is published
 * straight to the server, which runs one agent turn per message → one reply
 * per message. This module sits between "message admitted" and "publish to
 * server" and coalesces a rapid burst on ONE conversation into a SINGLE
 * published channel.inbound event, so the server runs one turn and sends one
 * reply.
 *
 * Behavior (per conversation, keyed by remote_jid):
 *   - A text message opens/extends a sliding window (default ~2s). Each new
 *     message within the window resets the timer and appends to the buffer.
 *     When the window finally elapses with no new message, the buffered
 *     messages are coalesced (texts joined, media concatenated) and published
 *     once.
 *   - A message that must NOT wait — one carrying media, or a control/command
 *     message (text starting with "/") — is appended and then flushed
 *     immediately, so a photo or a `/command` never sits in the window.
 *
 * The typing indicator is intentionally NOT managed here: the runtime starts
 * typing the instant a message is admitted (before handing it to the
 * debouncer), so the indicator is already live and persists across the whole
 * coalesce window — see each runtime's startTypingForChat().
 *
 * Channel-agnostic: it operates on the shared GatewayChannelInboundPayload
 * contract both the Telegram and WhatsApp runtimes already produce.
 */

import type { GatewayChannelInboundPayload } from "../../protocol/types";

/** Default sliding-window length: long enough to absorb a human's rapid
 *  multi-message burst, short enough not to feel laggy on a single message.
 *  Sits in the task's 1500-2500ms band. */
export const DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS = 2_000;

/** Minimal timer surface so tests can drive the window with a manual clock
 *  instead of real time. Defaults to a plain, ref'd setTimeout/clearTimeout —
 *  see defaultScheduler on why the flush timer must NOT be unref'd. */
export interface DebounceScheduler {
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
}

const defaultScheduler: DebounceScheduler = {
  // IMPORTANT: do NOT unref() this timer. Unlike the LLM/CLI *timeout* timers
  // elsewhere (which fire a "give up" side effect that must not hold the
  // process open), this timer fires the debounce FLUSH — the one and only
  // thing that publishes the user's coalesced message to the backend. An
  // unref'd timer is skipped by libuv whenever it is the only remaining handle
  // on the event loop, so at the quiet ~2s point right after a burst the flush
  // would silently never run and the message would never be published (the
  // exact production regression this module shipped with). The timer clears
  // itself the instant it fires, so keeping it ref'd only holds the loop for
  // the short debounce window.
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => {
    if (handle !== undefined && handle !== null) {
      clearTimeout(handle as ReturnType<typeof setTimeout>);
    }
  },
};

export interface InboundDebouncerOptions {
  /** Publishes one (coalesced) payload downstream — i.e. the runtime's
   *  publishInbound. */
  publish: (payload: GatewayChannelInboundPayload) => void | Promise<void>;
  windowMs?: number;
  scheduler?: DebounceScheduler;
  /** Overrides the "flush immediately" decision (media / command). */
  shouldBypass?: (payload: GatewayChannelInboundPayload) => boolean;
  /** Overrides how N buffered payloads become one. */
  coalesce?: (payloads: GatewayChannelInboundPayload[]) => GatewayChannelInboundPayload;
  /** Surfaced instead of an unhandled rejection when publish throws. */
  onError?: (error: unknown) => void;
}

interface PendingConversation {
  payloads: GatewayChannelInboundPayload[];
  timer: unknown;
}

/**
 * Default "flush immediately, don't wait out the window" predicate: media
 * attachments and control/command messages (text starting with "/").
 */
export function defaultShouldBypass(payload: GatewayChannelInboundPayload): boolean {
  const message = payload?.message;
  if (!message) {
    return false;
  }
  if (Array.isArray(message.media) && message.media.length > 0) {
    return true;
  }
  const text = String(message.text || "").trim();
  return text.startsWith("/");
}

/**
 * Merges a buffer of inbound payloads (all for the same conversation) into a
 * single payload. A single-element buffer is returned untouched, so the
 * common case — one message, window elapses, publish — is byte-identical to
 * the pre-debounce behavior. For a real burst: texts are joined with blank
 * lines, media arrays are concatenated, identity/timestamp fields come from
 * the most recent message, and the group signals are OR'd (a mention or a
 * reply-to-Sage anywhere in the burst still counts).
 */
export function coalesceInboundPayloads(
  payloads: GatewayChannelInboundPayload[],
): GatewayChannelInboundPayload {
  if (payloads.length === 1) {
    return payloads[0];
  }
  const messages = payloads.map((payload) => payload.message);
  const last = messages[messages.length - 1];
  const texts = messages
    .map((message) => String(message.text || ""))
    .filter((text) => text.trim().length > 0);
  const media = messages.flatMap((message) =>
    Array.isArray(message.media) ? message.media : [],
  );

  const mergedMessage: GatewayChannelInboundPayload["message"] = {
    ...last,
    text: texts.join("\n\n"),
  };
  if (media.length > 0) {
    mergedMessage.media = media;
  } else {
    delete mergedMessage.media;
  }
  if (messages.some((message) => message.is_group)) {
    mergedMessage.is_group = true;
  }
  if (messages.some((message) => message.is_mentioned)) {
    mergedMessage.is_mentioned = true;
  }
  if (messages.some((message) => message.is_reply_to_sage)) {
    mergedMessage.is_reply_to_sage = true;
  }

  return {
    channel_key: payloads[0].channel_key,
    provider: payloads[0].provider,
    message: mergedMessage,
  };
}

export class InboundDebouncer {
  private readonly publish: (payload: GatewayChannelInboundPayload) => void | Promise<void>;
  private readonly windowMs: number;
  private readonly scheduler: DebounceScheduler;
  private readonly shouldBypass: (payload: GatewayChannelInboundPayload) => boolean;
  private readonly coalesce: (payloads: GatewayChannelInboundPayload[]) => GatewayChannelInboundPayload;
  private readonly onError: (error: unknown) => void;
  private readonly pending = new Map<string, PendingConversation>();

  constructor(options: InboundDebouncerOptions) {
    this.publish = options.publish;
    this.windowMs = Math.max(0, options.windowMs ?? DEFAULT_INBOUND_DEBOUNCE_WINDOW_MS);
    this.scheduler = options.scheduler ?? defaultScheduler;
    this.shouldBypass = options.shouldBypass ?? defaultShouldBypass;
    this.coalesce = options.coalesce ?? coalesceInboundPayloads;
    // Default to LOUD, not silent: a failure to publish a coalesced inbound
    // message means the user's turn silently never runs — never swallow it.
    this.onError = options.onError ?? ((error) => {
      console.error("[inbound-debounce] failed to publish coalesced inbound message:", error);
    });
  }

  /** Number of conversations currently holding a buffered burst — for tests. */
  pendingCount(): number {
    return this.pending.size;
  }

  /**
   * Admit one mapped inbound payload. Buffers it against its conversation and
   * (re)arms the sliding window; a media/command message flushes the buffer
   * immediately instead of waiting.
   *
   * `opts.bypass` forces the immediate-flush path even when the payload itself
   * no longer looks like media — the runtime uses it to keep a photo whose
   * DOWNLOAD failed (and therefore degraded to a caption-only text payload)
   * out of the debounce window, since it was still a media moment.
   */
  admit(payload: GatewayChannelInboundPayload, opts?: { bypass?: boolean }): void {
    const key = String(payload?.message?.remote_jid || "").trim();
    if (!key) {
      // No conversation key to coalesce on — publish straight through rather
      // than silently dropping it.
      void this.invokePublish(payload);
      return;
    }
    const existing = this.pending.get(key);
    if (existing) {
      this.scheduler.clear(existing.timer);
      existing.payloads.push(payload);
    } else {
      this.pending.set(key, { payloads: [payload], timer: undefined });
    }

    if (opts?.bypass === true || this.shouldBypass(payload)) {
      this.flush(key);
      return;
    }
    const entry = this.pending.get(key);
    if (!entry) {
      return;
    }
    entry.timer = this.scheduler.set(() => this.flush(key), this.windowMs);
  }

  /** Force-flush one conversation's buffer now (used on bypass and by tests). */
  flush(key: string): void {
    const entry = this.pending.get(key);
    if (!entry) {
      return;
    }
    this.scheduler.clear(entry.timer);
    this.pending.delete(key);
    if (entry.payloads.length === 0) {
      return;
    }
    let coalesced: GatewayChannelInboundPayload;
    try {
      coalesced = this.coalesce(entry.payloads);
    } catch (error) {
      this.onError(error);
      return;
    }
    void this.invokePublish(coalesced);
  }

  /** Flush every buffered conversation now. */
  flushAll(): void {
    for (const key of Array.from(this.pending.keys())) {
      this.flush(key);
    }
  }

  /** Drop every buffered conversation WITHOUT publishing (shutdown/reset). */
  dispose(): void {
    for (const entry of this.pending.values()) {
      this.scheduler.clear(entry.timer);
    }
    this.pending.clear();
  }

  private async invokePublish(payload: GatewayChannelInboundPayload): Promise<void> {
    try {
      await this.publish(payload);
    } catch (error) {
      this.onError(error);
    }
  }
}
