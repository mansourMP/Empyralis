import { GatewayStateDb } from "../state/db";

export interface TelegramPersonalConfigSnapshot {
  apiId?: number;
  apiHash?: string;
  phoneNumber?: string;
  loginCode?: string;
  password?: string;
  updatedAt?: string;
}

export interface WhatsAppPersonalConfigSnapshot {
  phoneNumber?: string;
  customPairingCode?: string;
  updatedAt?: string;
}

interface PersonalChannelConfigSnapshot {
  telegram?: TelegramPersonalConfigSnapshot;
  whatsapp?: WhatsAppPersonalConfigSnapshot;
}

export interface TelegramPersonalConfigPatch {
  apiId?: number | null;
  apiHash?: string | null;
  phoneNumber?: string | null;
  loginCode?: string | null;
  password?: string | null;
}

export interface WhatsAppPersonalConfigPatch {
  phoneNumber?: string | null;
  customPairingCode?: string | null;
}

function cleanText(value: unknown): string | null | undefined {
  if (value === undefined) {
    return undefined;
  }
  const token = String(value ?? "").trim();
  return token || null;
}

function cleanPhone(value: unknown): string | null | undefined {
  if (value === undefined) {
    return undefined;
  }
  const digits = String(value ?? "").replace(/\D+/g, "");
  return digits || null;
}

function cleanPositiveInt(value: unknown): number | null | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (value === null || value === "") {
    return null;
  }
  const parsed = Number.parseInt(String(value).trim(), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error("api_id must be a positive integer.");
  }
  return parsed;
}

function applyPatch<T extends object>(
  current: T | undefined,
  patch: Record<string, unknown>,
): T | undefined {
  const next: Record<string, unknown> = {
    ...(current ?? {}),
  };
  let changed = false;
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) {
      continue;
    }
    changed = true;
    if (value === null) {
      delete next[key];
      continue;
    }
    next[key] = value;
  }
  if (changed) {
    next.updatedAt = new Date().toISOString();
  }
  return Object.keys(next).length > 0 ? (next as T) : undefined;
}

export class PersonalChannelConfigStore {
  constructor(private readonly db: GatewayStateDb) {}

  private async loadSnapshot(): Promise<PersonalChannelConfigSnapshot> {
    return this.db.readJson<PersonalChannelConfigSnapshot>("personal-channel-config.json", {});
  }

  private async saveSnapshot(snapshot: PersonalChannelConfigSnapshot): Promise<void> {
    await this.db.writeJson("personal-channel-config.json", snapshot);
  }

  async loadTelegramConfig(): Promise<TelegramPersonalConfigSnapshot> {
    const snapshot = await this.loadSnapshot();
    return snapshot.telegram ?? {};
  }

  async loadWhatsAppConfig(): Promise<WhatsAppPersonalConfigSnapshot> {
    const snapshot = await this.loadSnapshot();
    return snapshot.whatsapp ?? {};
  }

  async patchTelegramConfig(patch: TelegramPersonalConfigPatch): Promise<TelegramPersonalConfigSnapshot> {
    const snapshot = await this.loadSnapshot();
    snapshot.telegram = applyPatch<TelegramPersonalConfigSnapshot>(snapshot.telegram, {
      apiId: cleanPositiveInt(patch.apiId),
      apiHash: cleanText(patch.apiHash),
      phoneNumber: cleanText(patch.phoneNumber),
      loginCode: cleanText(patch.loginCode),
      password: cleanText(patch.password),
    });
    await this.saveSnapshot(snapshot);
    return snapshot.telegram ?? {};
  }

  async clearTelegramSecrets(): Promise<TelegramPersonalConfigSnapshot> {
    return this.patchTelegramConfig({
      loginCode: null,
      password: null,
    });
  }

  async patchWhatsAppConfig(patch: WhatsAppPersonalConfigPatch): Promise<WhatsAppPersonalConfigSnapshot> {
    const snapshot = await this.loadSnapshot();
    snapshot.whatsapp = applyPatch<WhatsAppPersonalConfigSnapshot>(snapshot.whatsapp, {
      phoneNumber: cleanPhone(patch.phoneNumber),
      customPairingCode: cleanText(patch.customPairingCode),
    });
    await this.saveSnapshot(snapshot);
    return snapshot.whatsapp ?? {};
  }
}
