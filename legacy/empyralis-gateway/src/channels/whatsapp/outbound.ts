import { GatewayStateDb } from "../../state/db";
import { TypingKeepalive } from "../foundation/typing-keepalive";
import { OutboundStore, type OutboundRecord } from "../foundation/outbound-store";

export const WHATSAPP_TYPING_KEEPALIVE_MS = 3_000;
export const WHATSAPP_TYPING_MAX_TTL_MS = 60_000;

export type WhatsAppPresenceAction = "composing" | "paused";
export type WhatsAppPresenceSender = (action: WhatsAppPresenceAction) => Promise<void> | void;

export class WhatsAppTypingKeepalive {
  private readonly inner: TypingKeepalive;

  constructor(
    sender?: WhatsAppPresenceSender,
    keepaliveMs = WHATSAPP_TYPING_KEEPALIVE_MS,
    maxTtlMs = WHATSAPP_TYPING_MAX_TTL_MS,
  ) {
    this.inner = new TypingKeepalive(
      sender ? (action: string) => {
        const a = action as WhatsAppPresenceAction;
        return sender(a);
      } : undefined,
      { startAction: "composing", stopAction: "paused", keepaliveMs, maxTtlMs },
    );
  }

  async start(): Promise<void> { return this.inner.start(); }
  async stop(): Promise<void> { return this.inner.stop(); }
}

export type WhatsAppOutboundRecord = OutboundRecord & { clientMessageId?: string };

export class WhatsAppOutboundStore extends OutboundStore<{ clientMessageId?: string }> {
  constructor(db: GatewayStateDb) {
    super(db, "whatsapp-outbound.json");
  }

  protected override async onBeginSendExisting(
    existing: OutboundRecord & { clientMessageId?: string },
    payload: OutboundRecord & { clientMessageId?: string },
  ): Promise<(OutboundRecord & { clientMessageId?: string }) | undefined> {
    if (!existing.clientMessageId && payload.clientMessageId) {
      return { ...existing, clientMessageId: payload.clientMessageId };
    }
    return undefined;
  }
}
