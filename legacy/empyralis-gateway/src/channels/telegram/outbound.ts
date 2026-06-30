import { GatewayStateDb } from "../../state/db";
import { TypingKeepalive } from "../foundation/typing-keepalive";
import {
  OutboundStore,
  type OutboundRecord,
} from "../foundation/outbound-store";

export const TELEGRAM_TYPING_KEEPALIVE_MS = 3_000;
export const TELEGRAM_TYPING_MAX_TTL_MS = 60_000;

export type TelegramChatAction = "typing";
export type TelegramChatActionSender = (action: TelegramChatAction) => Promise<void> | void;

export class TelegramTypingKeepalive {
  private readonly inner: TypingKeepalive;

  constructor(
    sender?: TelegramChatActionSender,
    keepaliveMs = TELEGRAM_TYPING_KEEPALIVE_MS,
    maxTtlMs = TELEGRAM_TYPING_MAX_TTL_MS,
  ) {
    this.inner = new TypingKeepalive(
      sender ? (action: string) => {
        const a = action as TelegramChatAction;
        return sender(a);
      } : undefined,
      { startAction: "typing", keepaliveMs, maxTtlMs },
    );
  }

  async start(): Promise<void> { return this.inner.start(); }
  async stop(): Promise<void> { return this.inner.stop(); }
}

export type TelegramOutboundRecord = OutboundRecord;

export class TelegramOutboundStore extends OutboundStore {
  constructor(db: GatewayStateDb) {
    super(db, "telegram-outbound.json");
  }
}
