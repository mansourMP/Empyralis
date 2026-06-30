import type { WhatsAppSessionSnapshot } from "./session-store";

export interface WhatsAppLoginConfig {
  phoneNumber?: string;
  customPairingCode?: string;
}

function normalizePhoneNumber(value: string | undefined): string | undefined {
  const digits = String(value || "").replace(/\D+/g, "");
  return digits || undefined;
}

function maskPhoneNumber(phoneNumber: string | undefined): string | undefined {
  const digits = normalizePhoneNumber(phoneNumber);
  if (!digits) {
    return undefined;
  }
  if (digits.length <= 4) {
    return digits;
  }
  return `${"*".repeat(Math.max(digits.length - 4, 1))}${digits.slice(-4)}`;
}

export function loadWhatsAppLoginConfig(env: NodeJS.ProcessEnv = process.env): WhatsAppLoginConfig {
  return {
    phoneNumber: normalizePhoneNumber(env.EMPYRALIS_WHATSAPP_PHONE_NUMBER),
    customPairingCode: String(env.EMPYRALIS_WHATSAPP_PAIRING_CODE || "").trim() || undefined,
  };
}

export function buildWhatsAppPreflightState(
  config: WhatsAppLoginConfig,
): Partial<WhatsAppSessionSnapshot> | null {
  const explicitPhone = String(config.phoneNumber || "").trim();
  if (!explicitPhone) {
    return null;
  }
  const normalizedPhone = normalizePhoneNumber(explicitPhone);
  if (normalizedPhone) {
    return null;
  }
  return {
    status: "authorization_required",
    loginHint: "phone_number_invalid",
    retryable: false,
    pairingCode: undefined,
    pairingCodeGeneratedAt: undefined,
  };
}

export function buildWhatsAppPairingCodeState(
  config: WhatsAppLoginConfig,
  pairingCode: string,
): Partial<WhatsAppSessionSnapshot> {
  return {
    status: "pairing_code_required",
    qrCode: undefined,
    loginHint: maskPhoneNumber(config.phoneNumber) || "pairing_code_required",
    pairingCode: String(pairingCode || "").trim() || undefined,
    pairingCodeGeneratedAt: new Date().toISOString(),
    retryable: true,
    lastDisconnectReason: undefined,
    lastDisconnectCode: undefined,
  };
}
