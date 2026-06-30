import Redis from "ioredis";
import { CONFIG } from "./config.js";
import { encrypt, decrypt } from "./kms.js";

/**
 * CloudSessionStore — encrypted session string storage in Redis.
 *
 * Key pattern: session:{sessionId}:auth
 * Value: KMS-encrypted session string (base64 blob, never plaintext in Redis)
 *
 * Every read/write/delete is audit-logged to a separate audit key:
 *   session:{sessionId}:audit  (Redis list, append-only)
 */

export class CloudSessionStore {
  /** @param {string} sessionId */
  constructor(sessionId) {
    this.sessionId = sessionId;
    this.authKey = `session:${sessionId}:auth`;
    this.auditKey = `session:${sessionId}:audit`;
    this.redis = new Redis(CONFIG.sessionRedisUrl, {
      lazyConnect: true,
      maxRetriesPerRequest: 2,
      retryStrategy: (times) => Math.min(times * 200, 2000),
    });
  }

  async connect() {
    if (this.redis.status !== "ready" && this.redis.status !== "connecting") {
      await this.redis.connect();
    }
  }

  async disconnect() {
    if (this.redis.status !== "end") {
      await this.redis.quit().catch(() => {});
    }
  }

  /**
   * Save an encrypted session string to Redis.
   * Plaintext NEVER touches Redis — encrypted before write.
   */
  async saveSessionString(plaintext) {
    await this.connect();
    const ciphertext = await encrypt(plaintext);
    await this.redis.set(this.authKey, ciphertext);
    await this.redis.expire(this.authKey, 86400 * 30); // 30-day TTL
    await this._audit("save", { ciphertextLength: ciphertext.length });
  }

  /**
   * Load and decrypt a session string from Redis.
   * Returns plaintext (decrypted on read).
   */
  async loadSessionString() {
    await this.connect();
    const ciphertext = await this.redis.get(this.authKey);
    if (!ciphertext) return null;
    const plaintext = await decrypt(ciphertext);
    await this._audit("load", { ciphertextLength: String(ciphertext).length });
    return plaintext;
  }

  /**
   * Delete the encrypted session string from Redis.
   */
  async deleteSessionString() {
    await this.connect();
    const existed = await this.redis.del(this.authKey);
    await this._audit("delete", { existed: existed > 0 });
    return existed > 0;
  }

  /**
   * Check if a session string exists in Redis.
   */
  async hasSessionString() {
    await this.connect();
    return (await this.redis.exists(this.authKey)) > 0;
  }


  // ── Metadata ──────────────────────────────────────────────────

  /**
   * Save session metadata (workspaceId, linkedUsername, status, etc.)
   * Key: session:{sessionId}:meta
   */
  async saveMetadata(meta = {}) {
    await this.connect();
    const data = JSON.stringify({
      workspaceId: String(meta.workspaceId || "default").trim(),
      linkedUsername: String(meta.linkedUsername || "").trim(),
      linkedUserId: String(meta.linkedUserId || "").trim(),
      status: String(meta.status || "unknown").trim(),
      createdAt: meta.createdAt || new Date().toISOString(),
      lastConnectedAt: meta.lastConnectedAt || new Date().toISOString(),
    });
    const metaKey = `session:${this.sessionId}:meta`;
    await this.redis.set(metaKey, data);
    await this.redis.expire(metaKey, 86400 * 30); // 30-day TTL
  }

  /**
   * Load session metadata.
   */
  async loadMetadata() {
    await this.connect();
    const metaKey = `session:${this.sessionId}:meta`;
    const raw = await this.redis.get(metaKey);
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (_) { return null; }
  }

  /**
   * Mark a session as disconnected.
   */
  async markDisconnected() {
    const meta = (await this.loadMetadata()) || {};
    meta.status = "disconnected";
    meta.lastDisconnectedAt = new Date().toISOString();
    await this.saveMetadata(meta);
  }

  /**
   * Scan Redis for all known session IDs.
   * Scans for session:*:auth keys first (authoritative), then enriches with
   * metadata from session:*:meta keys. Sessions with auth data but no metadata
   * (created before auto-reconnect was implemented) are still returned with
   * meta=null — the restore sweep will attempt to reconnect them anyway.
   *
   * Returns array of { sessionId, meta } for each stored session.
   */
  static async listAllSessionIds() {
    const redis = new Redis(CONFIG.sessionRedisUrl, {
      lazyConnect: true,
      maxRetriesPerRequest: 2,
      retryStrategy: (times) => Math.min(times * 200, 2000),
    });
    try {
      await redis.connect();

      // Scan for session:*:auth keys (the authoritative store)
      const sessionIds = new Set();
      let cursor = "0";
      do {
        const [nextCursor, keys] = await redis.scan(cursor, "MATCH", "session:*:auth", "COUNT", 100);
        cursor = nextCursor;
        for (const key of keys) {
          const parts = key.split(":");
          if (parts.length >= 3 && parts[0] === "session" && parts[2] === "auth") {
            sessionIds.add(parts[1]);
          }
        }
      } while (cursor !== "0");

      // For each session, load metadata if available
      const sessions = [];
      for (const sessionId of sessionIds) {
        const metaKey = `session:${sessionId}:meta`;
        const raw = await redis.get(metaKey);
        let meta = null;
        if (raw) {
          try { meta = JSON.parse(raw); } catch (_) {}
        }
        sessions.push({ sessionId, meta });
      }

      return sessions;
    } finally {
      await redis.quit().catch(() => {});
    }
  }

  // ── Audit ────────────────────────────────────────────────────

  async _audit(operation, metadata = {}) {
    const entry = JSON.stringify({
      ts: new Date().toISOString(),
      operation,
      sessionId: this.sessionId,
      ...metadata,
    });
    // Append to audit list, trim to last 1000 entries
    await this.redis.lpush(this.auditKey, entry);
    await this.redis.ltrim(this.auditKey, 0, 999);
    await this.redis.expire(this.auditKey, 86400 * 90); // 90-day TTL
  }

  /**
   * Read the audit log for this session.
   */
  async getAuditLog(limit = 50) {
    await this.connect();
    const entries = await this.redis.lrange(this.auditKey, 0, limit - 1);
    return entries.map(e => JSON.parse(e));
  }
}
