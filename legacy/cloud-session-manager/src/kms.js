import crypto from "node:crypto";
import { CONFIG } from "./config.js";

/**
 * KMS abstraction for session encryption at rest.
 *
 * Primary: AWS KMS (via @aws-sdk/client-kms) — production path.
 * Fallback: AES-256-GCM with key derived from BACKEND_HMAC_SECRET (dev only).
 *
 * The fallback is EXPLICITLY flagged for production replacement.
 * It does NOT silently degrade — it logs a loud warning on first use.
 */

const AES_ALGORITHM = "aes-256-gcm";
const IV_LENGTH = 12;
const AUTH_TAG_LENGTH = 16;
const KMS_KEY_ID = CONFIG.kmsKeyArn || "";

let _fallbackWarned = false;
let _awsKmsClient = null;
let _awsKmsAvailable = null;

async function _initAwsKms() {
  if (_awsKmsAvailable !== null) return _awsKmsAvailable;
  try {
    const mod = await import("@aws-sdk/client-kms");
    _awsKmsClient = new mod.KMSClient({ region: CONFIG.kmsRegion || "us-east-1" });
    _awsKmsAvailable = true;
  } catch {
    _awsKmsAvailable = false;
  }
  return _awsKmsAvailable;
}

function _warnFallback() {
  if (!_fallbackWarned) {
    console.warn("[kms] WARNING: AWS KMS not configured — using AES-256-GCM dev fallback.");
    console.warn("[kms] TODO: provision AWS KMS key and set KMS_KEY_ARN before production use.");
    _fallbackWarned = true;
  }
}

function _deriveFallbackKey() {
  // Derive a 256-bit key from the HMAC secret using HKDF
  const secret = CONFIG.backendHmacSecret || CONFIG.apiSecret || "dev-secret-change-me";
  return crypto.createHash("sha256").update("kms-fallback:" + secret).digest();
}

/**
 * Encrypt plaintext. Returns a base64-encoded ciphertext blob.
 * Format: "kms:aws:<base64>" or "kms:aes256gcm:<iv>:<authTag>:<ciphertext>"
 */
export async function encrypt(plaintext) {
  if (!plaintext) throw new Error("plaintext is required");

  if (KMS_KEY_ID && await _initAwsKms()) {
    // AWS KMS path
    const { EncryptCommand } = await import("@aws-sdk/client-kms");
    const result = await _awsKmsClient.send(
      new EncryptCommand({ KeyId: KMS_KEY_ID, Plaintext: Buffer.from(plaintext, "utf-8") })
    );
    return "kms:aws:" + Buffer.from(result.CiphertextBlob).toString("base64");
  }

  // Dev fallback: AES-256-GCM
  _warnFallback();
  const key = _deriveFallbackKey();
  const iv = crypto.randomBytes(IV_LENGTH);
  const cipher = crypto.createCipheriv(AES_ALGORITHM, key, iv, { authTagLength: AUTH_TAG_LENGTH });
  const encrypted = Buffer.concat([cipher.update(plaintext, "utf-8"), cipher.final()]);
  const authTag = cipher.getAuthTag();

  const blob = Buffer.concat([iv, authTag, encrypted]);
  return "kms:aes256gcm:" + blob.toString("base64");
}

/**
 * Decrypt ciphertext produced by encrypt().
 */
export async function decrypt(ciphertext) {
  if (!ciphertext) throw new Error("ciphertext is required");

  const parts = String(ciphertext).split(":");
  const scheme = parts[0] + ":" + parts[1];
  const payload = parts.slice(2).join(":");

  if (scheme === "kms:aws") {
    if (!await _initAwsKms()) {
      throw new Error("AWS KMS not available for decryption — was this encrypted with KMS?");
    }
    const { DecryptCommand } = await import("@aws-sdk/client-kms");
    const result = await _awsKmsClient.send(
      new DecryptCommand({ CiphertextBlob: Buffer.from(payload, "base64") })
    );
    return Buffer.from(result.Plaintext).toString("utf-8");
  }

  if (scheme === "kms:aes256gcm") {
    _warnFallback();
    const key = _deriveFallbackKey();
    const blob = Buffer.from(payload, "base64");

    const iv = blob.subarray(0, IV_LENGTH);
    const authTag = blob.subarray(IV_LENGTH, IV_LENGTH + AUTH_TAG_LENGTH);
    const encrypted = blob.subarray(IV_LENGTH + AUTH_TAG_LENGTH);

    const decipher = crypto.createDecipheriv(AES_ALGORITHM, key, iv, { authTagLength: AUTH_TAG_LENGTH });
    decipher.setAuthTag(authTag);
    const decrypted = Buffer.concat([decipher.update(encrypted), decipher.final()]);
    return decrypted.toString("utf-8");
  }

  throw new Error(`Unknown KMS scheme: ${scheme}`);
}
