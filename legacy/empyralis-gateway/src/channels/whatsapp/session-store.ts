import { promises as fs } from "fs";
import path from "path";

import { GatewayStateDb } from "../../state/db";

export const WHATSAPP_PERSONAL_CHANNEL_KEY = "whatsapp_personal";
export const WHATSAPP_PERSONAL_PROVIDER = "whatsapp_baileys";

export interface WhatsAppSessionSnapshot {
  channelKey: string;
  provider: string;
  status:
    | "idle"
    | "qr_required"
    | "pairing_code_required"
    | "authorization_required"
    | "connecting"
    | "connected"
    | "disconnected"
    | "logged_out";
  qrCode?: string;
  loginHint?: string;
  pairingCode?: string;
  pairingCodeGeneratedAt?: string;
  linkedJid?: string;
  linkedName?: string;
  connectedAt?: string;
  lastDisconnectReason?: string;
  lastDisconnectCode?: number;
  retryable?: boolean;
  updatedAt?: string;
}

const DEFAULT_SNAPSHOT: WhatsAppSessionSnapshot = {
  channelKey: WHATSAPP_PERSONAL_CHANNEL_KEY,
  provider: WHATSAPP_PERSONAL_PROVIDER,
  status: "idle",
};

export class WhatsAppSessionStore {
  constructor(private readonly db: GatewayStateDb) {}

  authStateDir(): string {
    return path.join(this.db.rootDirPath(), "whatsapp", "auth");
  }

  async ensureAuthStateDir(): Promise<string> {
    const target = this.authStateDir();
    await fs.mkdir(target, { recursive: true });
    return target;
  }

  async clearAuthStateDir(): Promise<void> {
    await fs.rm(this.authStateDir(), { recursive: true, force: true });
  }

  async load(): Promise<WhatsAppSessionSnapshot> {
    return this.db.readJson<WhatsAppSessionSnapshot>("whatsapp-session.json", DEFAULT_SNAPSHOT);
  }

  async save(snapshot: Partial<WhatsAppSessionSnapshot>): Promise<WhatsAppSessionSnapshot> {
    const current = await this.load();
    const next: WhatsAppSessionSnapshot = {
      ...current,
      ...snapshot,
      channelKey: WHATSAPP_PERSONAL_CHANNEL_KEY,
      provider: WHATSAPP_PERSONAL_PROVIDER,
      updatedAt: new Date().toISOString(),
    };
    return this.db.writeJson("whatsapp-session.json", next);
  }

  toGatewayStatePayload(snapshot: WhatsAppSessionSnapshot): Record<string, unknown> {
    return {
      personal_channels: {
        [WHATSAPP_PERSONAL_CHANNEL_KEY]: {
          channel_key: WHATSAPP_PERSONAL_CHANNEL_KEY,
          provider: WHATSAPP_PERSONAL_PROVIDER,
          status: snapshot.status,
          qr_code: snapshot.qrCode,
          login_hint: snapshot.loginHint,
          pairing_code: snapshot.pairingCode,
          pairing_code_generated_at: snapshot.pairingCodeGeneratedAt,
          linked_jid: snapshot.linkedJid,
          linked_name: snapshot.linkedName,
          connected_at: snapshot.connectedAt,
          retryable: snapshot.retryable,
          last_disconnect_reason: snapshot.lastDisconnectReason,
          last_disconnect_code: snapshot.lastDisconnectCode,
          updated_at: snapshot.updatedAt,
        },
      },
    };
  }
}
