import { CONFIG } from "./config.js";
import { InboundHandler } from "./telegram/inbound-handler.js";
import { createTelegramClient, reconnectTelegramClient } from "./telegram/client-factory.js";
import { CloudSessionOutbox } from "./outbox/outbox.js";
import { PerSessionRateLimiter } from "./rate-limiter.js";
import { CloudSessionStore } from "./session-store.js";
import { ReplayLoop } from "./outbox/replay-loop.js";

/**
 * SessionPool manages a pool of GramJS clients keyed by sessionId.
 *
 * Responsibilities:
 *   - createSession(sessionString) → starts GramJS client, returns sessionId
 *   - destroySession(sessionId) → disconnects and removes
 *   - getSession(sessionId) → returns session state
 *   - sendMessage(sessionId, text, remoteJid) → sends outbound message via GramJS
 *   - Auto-cleanup stale sessions (idle > SESSION_TTL_MS)
 *   - Max-sessions cap (MAX_SESSIONS)
 *
 * Stage 2: InboundHandler forwards messages to backend.
 */
export class SessionPool {
  constructor({ logger }) {
    this.logger = logger;
    /** @type {Map<string, {client: object, sessionString: string, linkedUsername?: string, linkedUserId?: string, linkedPhone?: string, status: string, createdAt: number, lastActivityAt: number, inboundMessages: Array<object>, inboundHandler: object, outbox: object, replayLoop: object, rateLimiter: object, store: object}>} */
    this.sessions = new Map();

    // Auto-cleanup every 60s
    // Explicitly ref'd to keep Node 26 event loop alive.
    // Node 26 has a known edge case where the event loop exits if it
    // doesn't detect active handles beyond the HTTP server.
    this._cleanupInterval = setInterval(() => this._cleanupStale(), 60_000);
    // Prevent Node from unref-ing this timer internally
    if (typeof this._cleanupInterval.ref === 'function') {
        this._cleanupInterval.ref();
    }
  }

  /**
   * Create and connect a new GramJS session.
   * @param {string} sessionString
   * @param {object} [opts]
   * @param {string} [opts.workspaceId] - workspace for this session
   * @returns {Promise<string>} sessionId
   */
  async createSession(sessionString, opts = {}) {
    if (this.sessions.size >= CONFIG.maxSessions) {
      throw new Error(`max_sessions_reached: ${this.sessions.size}/${CONFIG.maxSessions}`);
    }

    const sessionId = `tg-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}`;

    // Track inbound messages for this session
    const inboundMessages = [];

    // Create inbound handler BEFORE connect so it catches all messages
    const inboundHandler = new InboundHandler({
      sessionId,
      workspaceId: opts.workspaceId || "default",
      linkedUserId: "",  // updated after connect
      logger: this.logger,
    });

    const connected = await createTelegramClient({
      sessionString,
      onInboundMessage: (msg) => {
        inboundMessages.push({ receivedAt: new Date().toISOString(), ...msg });
        if (inboundMessages.length > 50) {
          inboundMessages.shift();
        }
        // Forward to backend via InboundHandler (Stage 2)
        if (inboundHandler) {
          inboundHandler.handleMessage(msg).catch((err) => {
            this.logger?.warn?.({ err }, "inbound handler error (non-fatal)");
          });
        }
      },
      logger: this.logger,
    });

    // Update linkedUserId and linkedUsername now that we have them from Telegram
    inboundHandler.linkedUserId = connected.linkedUserId;
    inboundHandler.linkedUsername = connected.linkedUsername || "";

    const now = Date.now();
    // Create outbox for this session
    const outbox = new CloudSessionOutbox(sessionId);
    const rateLimiter = new PerSessionRateLimiter(sessionId);
    const store = new CloudSessionStore(sessionId);
    const replayLoop = new ReplayLoop({
      sessionId,
      outbox,
      send: async (item) => {
        return this._sendViaGramJS(sessionId, item.payload);
      },
      isConnected: () => {
        const s = this.sessions.get(sessionId);
        return s?.status === "connected";
      },
      logger: this.logger,
    });

    this.sessions.set(sessionId, {
      client: connected.client,
      sessionString: connected.sessionString(),
      linkedUsername: connected.linkedUsername,
      linkedUserId: connected.linkedUserId,
      linkedPhone: connected.linkedPhone,
      workspaceId: opts.workspaceId || "default",
      status: "connected",
      createdAt: now,
      lastActivityAt: now,
      inboundMessages,
      inboundHandler,
      outbox,
      replayLoop,
      rateLimiter,
      store,
    });

    // Save encrypted session string (Stage 5)
    store.saveSessionString(connected.sessionString()).catch((err) => {
      this.logger?.warn?.({ err }, "failed to save session string to store (non-fatal)");
    });

    // Save session metadata for auto-reconnect on startup
    store.saveMetadata({
      workspaceId: opts.workspaceId || "default",
      linkedUsername: connected.linkedUsername || "",
      linkedUserId: connected.linkedUserId || "",
      status: "connected",
      createdAt: new Date(now).toISOString(),
      lastConnectedAt: new Date().toISOString(),
    }).catch((err) => {
      this.logger?.warn?.({ err }, "failed to save session metadata to store (non-fatal)");
    });

    // Drain replay on connect (async, non-blocking)
    replayLoop.drain().catch((err) => {
      this.logger?.warn?.({ err }, "initial replay drain failed (non-fatal)");
    });

    this.logger?.info?.({ sessionId, username: connected.linkedUsername }, "session created");
    return sessionId;
  }

  /**
   * Destroy a session — disconnect GramJS and remove from pool.
   * @param {string} sessionId
   */
  async destroySession(sessionId) {
    const session = this.sessions.get(sessionId);
    if (!session) {
      return false;
    }
    try {
      await session.client.disconnect?.();
    } catch (_err) {
      this.logger?.warn?.({ sessionId, err: _err }, "error disconnecting client");
    }
    try {
      await session.outbox?.disconnect?.();
    } catch (_err) {
      // non-fatal
    }
    try {
      await session.rateLimiter?.disconnect?.();
    } catch (_err) {
      // non-fatal
    }
    try {
      await session.store?.deleteSessionString?.();
    } catch (_err) {
      // non-fatal
    }
    try {
      await session.store?.disconnect?.();
    } catch (_err) {
      // non-fatal
    }
    this.sessions.delete(sessionId);
    this.logger?.info?.({ sessionId }, "session destroyed");
    return true;
  }

  /**
   * Get session state.
   * @param {string} sessionId
   * @returns {object|null}
   */
  getSession(sessionId) {
    const session = this.sessions.get(sessionId);
    if (!session) return null;

    session.lastActivityAt = Date.now();

    return {
      sessionId,
      status: session.status,
      linkedUsername: session.linkedUsername,
      linkedUserId: session.linkedUserId,
      workspaceId: session.workspaceId || "default",
      linkedPhone: session.linkedPhone ? `***${session.linkedPhone.slice(-4)}` : undefined,
      createdAt: new Date(session.createdAt).toISOString(),
      uptimeMs: Date.now() - session.createdAt,
      inboundMessageCount: session.inboundMessages.length,
    };
  }

  /**
   * Send an outbound message through the GramJS session (Stage 2).
   * @param {string} sessionId
   * @param {object} opts
   * @param {string} opts.text
   * @param {string} opts.remoteJid - the chat/peer to send to
   * @returns {Promise<{ok: boolean, messageId?: string, error?: string}>}
   */
  /**
   * Send an outbound message through the GramJS session (Stage 2+3).
   * Uses outbox: enqueue → send → ack on success, markForReplay on failure.
   */
  async sendMessage(sessionId, { text, remoteJid }) {
    const session = this.sessions.get(sessionId);
    if (!session) {
      return { ok: false, error: "session_not_found" };
    }
    if (!text || !String(text).trim()) {
      return { ok: false, error: "text is required" };
    }

    const payload = { text: String(text).trim(), remoteJid: remoteJid || "me" };
    const requestId = `msg-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 6)}`;

    // Stage 4: Check rate limiter before sending
    const rateResult = await session.rateLimiter.consume(1);
    if (!rateResult.allowed) {
      // Rate limited — enqueue with scheduledAt for delayed replay
      const scheduledAt = new Date(Date.now() + rateResult.retryAfterMs).toISOString();
      await session.outbox.enqueue(requestId, "send_message", payload, { scheduledAt });
      this.logger?.warn?.(
        { sessionId, retryAfterMs: rateResult.retryAfterMs, scheduledAt },
        "rate limited — outbound message queued for scheduled replay"
      );
      return { ok: false, error: "rate_limited", retryAfterMs: rateResult.retryAfterMs, queued: true, scheduledAt };
    }

    // Enqueue in outbox
    await session.outbox.enqueue(requestId, "send_message", payload);

    // Attempt delivery
    const result = await this._sendViaGramJS(sessionId, payload);
    if (result.ok) {
      await session.outbox.acknowledge(requestId);
      this.logger?.info?.({ sessionId, messageId: result.messageId, tokensLeft: rateResult.tokensLeft }, "outbound message sent + acked");
      session.inboundHandler?.addSentMessageId(result.messageId);
      return { ok: true, messageId: result.messageId, tokensLeft: rateResult.tokensLeft };
    }

    // Delivery failed — outbox will retry on next drain
    await session.outbox.markForReplay(requestId, result.error || "send_failed");
    this.logger?.warn?.({ sessionId, error: result.error }, "outbound message queued for replay");
    return { ok: false, error: result.error, queued: true };
  }

  /**
   * Internal: send a message via GramJS (no outbox logic).
   * Used by both sendMessage() and ReplayLoop.
   */
  async _sendViaGramJS(sessionId, { text, remoteJid }) {
    const session = this.sessions.get(sessionId);
    if (!session || session.status !== "connected") {
      return { ok: false, error: "session_not_connected" };
    }

    // Stage 4: Check rate limiter (gates both sendMessage and ReplayLoop)
    if (session.rateLimiter) {
      const rateResult = await session.rateLimiter.consume(1);
      if (!rateResult.allowed) {
        return { ok: false, error: "rate_limited", retryAfterMs: rateResult.retryAfterMs };
      }
    }

    try {
      const client = session.client;
      let peer;
      try {
        peer = await client.getInputEntity(remoteJid || "me");
      } catch (_) {
        peer = "me";
      }
      const result = await client.sendMessage(peer, { message: String(text).trim() });
      session.lastActivityAt = Date.now();
      return { ok: true, messageId: String(result?.id || "") };
    } catch (err) {
      return { ok: false, error: err?.message || "send_failed" };
    }
  }

  /**
   * List all session IDs (for diagnostics).
   */
  listSessionIds() {
    return Array.from(this.sessions.keys());
  }

  /**
   * Get the number of active sessions.
   */
  get sessionCount() {
    return this.sessions.size;
  }

  /**
   * Gracefully disconnect all sessions.
   */
  async shutdown() {
    clearInterval(this._cleanupInterval);
    const ids = this.listSessionIds();
    this.logger?.info?.({ count: ids.length }, "shutting down all sessions");
    await Promise.allSettled(ids.map((id) => this.destroySession(id)));
  }


  /**
   * Step 1 of sign-in: request a verification code.
   * Returns { phoneCodeHash, tempSessionId }.
   */
  async requestSignInCode(phoneNumber) {
    const { requestSignInCode } = await import("./telegram/client-factory.js");
    return requestSignInCode({ phoneNumber, logger: this.logger });
  }

  /**
   * Step 2 of sign-in: verify the code and create a connected session.
   * Returns { sessionId, sessionString, linkedUsername, linkedPhone }.
   */
  async verifySignInCode({ tempSessionId, phoneCodeHash, phoneNumber, code, password }) {
    if (this.sessions.size >= CONFIG.maxSessions) {
      throw new Error(`max_sessions_reached: ${this.sessions.size}/${CONFIG.maxSessions}`);
    }

    const { verifySignInCode } = await import("./telegram/client-factory.js");
    const sessionId = `tg-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}`;
    const inboundMessages = [];

    // Create inbound handler BEFORE sign-in completes so it catches all messages
    const inboundHandler = new InboundHandler({
      sessionId,
      workspaceId: "default",
      linkedUserId: "",  // updated after sign-in
      linkedUsername: "",  // updated after sign-in
      logger: this.logger,
    });

    const connected = await verifySignInCode({
      tempSessionId,
      phoneCodeHash,
      phoneNumber,
      code,
      password,
      onInboundMessage: (msg) => {
        inboundMessages.push({ receivedAt: new Date().toISOString(), ...msg });
        if (inboundMessages.length > 50) inboundMessages.shift();
        if (inboundHandler) {
          inboundHandler.handleMessage(msg).catch((err) => {
            this.logger?.warn?.({ err }, "inbound handler error (non-fatal)");
          });
        }
      },
      logger: this.logger,
    });

    // Update linkedUserId and linkedUsername now that we have them from Telegram
    inboundHandler.linkedUserId = connected.linkedUserId;
    inboundHandler.linkedUsername = connected.linkedUsername || "";

    const now = Date.now();
    const outbox = new CloudSessionOutbox(sessionId);
    const rateLimiter = new PerSessionRateLimiter(sessionId);
    const store = new CloudSessionStore(sessionId);
    const replayLoop = new ReplayLoop({
      sessionId,
      outbox,
      send: async (item) => {
        return this._sendViaGramJS(sessionId, item.payload);
      },
      isConnected: () => {
        const s = this.sessions.get(sessionId);
        return s?.status === "connected";
      },
      logger: this.logger,
    });

    this.sessions.set(sessionId, {
      client: connected.client,
      sessionString: connected.sessionString,
      linkedUsername: connected.linkedUsername,
      linkedUserId: connected.linkedUserId,
      linkedPhone: connected.linkedPhone,
      workspaceId: opts.workspaceId || "default",
      status: "connected",
      createdAt: now,
      lastActivityAt: now,
      inboundMessages,
      inboundHandler,
      outbox,
      replayLoop,
      rateLimiter,
      store,
    });

    store.saveSessionString(connected.sessionString).catch((err) => {
      this.logger?.warn?.({ err }, "failed to save session string to store (non-fatal)");
    });

    // Save session metadata for auto-reconnect on startup
    store.saveMetadata({
      workspaceId: "default",
      linkedUsername: connected.linkedUsername || "",
      linkedUserId: connected.linkedUserId || "",
      status: "connected",
      createdAt: new Date(now).toISOString(),
      lastConnectedAt: new Date().toISOString(),
    }).catch((err) => {
      this.logger?.warn?.({ err }, "failed to save session metadata to store (non-fatal)");
    });

    replayLoop.drain().catch((err) => {
      this.logger?.warn?.({ err }, "initial replay drain failed (non-fatal)");
    });

    this.logger?.info?.({ sessionId, username: connected.linkedUsername }, "session created via sign-in");

    return {
      sessionId,
      sessionString: connected.sessionString,
      linkedUsername: connected.linkedUsername,
      linkedPhone: connected.linkedPhone,
    };
  }


  /**
   * Restore all previously stored sessions on startup.
   * Scans Redis for stored session strings, attempts to reconnect each.
   * Runs BEFORE the HTTP server starts accepting requests.
   *
   * @returns {Promise<{restored: number, failed: number, failures: Array<{sessionId, workspaceId, error}>}>}
   */
  async restoreSessions() {
    const summary = { restored: 0, failed: 0, failures: [] };
    let storedSessions = [];
    try {
      storedSessions = await CloudSessionStore.listAllSessionIds();
    } catch (err) {
      this.logger?.warn?.({ err }, "failed to scan Redis for stored sessions");
      return summary;
    }

    if (storedSessions.length === 0) {
      this.logger?.info?.("no stored sessions to restore");
      return summary;
    }

    this.logger?.info?.({ count: storedSessions.length }, "restoring stored sessions");

    for (const { sessionId, meta } of storedSessions) {
      const workspaceId = meta?.workspaceId || "default";
      const storedStatus = meta?.status || "unknown";

      // Skip sessions that were already marked disconnected (user explicitly removed)
      if (storedStatus === "disconnected") {
        this.logger?.info?.({ sessionId, workspaceId }, "skipping disconnected session");
        continue;
      }

      const store = new CloudSessionStore(sessionId);
      let sessionString;
      try {
        sessionString = await store.loadSessionString();
      } catch (err) {
        this.logger?.warn?.({ sessionId, workspaceId, err }, "failed to load stored session string");
        summary.failed++;
        summary.failures.push({ sessionId, workspaceId, error: "load_failed: " + (err?.message || "unknown") });
        continue;
      }

      if (!sessionString) {
        this.logger?.warn?.({ sessionId, workspaceId }, "stored session has no session string — skipping");
        continue;
      }

      // Attempt reconnect with exponential backoff (3 retries: 1s, 2s, 4s)
      let connected = null;
      let lastError = null;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const delay = 1000 * Math.pow(2, attempt);
          if (attempt > 0) {
            this.logger?.info?.({ sessionId, attempt: attempt + 1, delayMs: delay }, "retrying session reconnect");
            await new Promise(resolve => setTimeout(resolve, delay));
          }

          // Check max sessions
          if (this.sessions.size >= CONFIG.maxSessions) {
            throw new Error("max_sessions_reached");
          }

          // Create inbound handler BEFORE connect
          const inboundHandler = new InboundHandler({
            sessionId,
            workspaceId,
            linkedUserId: meta?.linkedUserId || "",
            linkedUsername: meta?.linkedUsername || "",
            logger: this.logger,
          });

          const inboundMessages = [];

          const result = await createTelegramClient({
            sessionString,
            onInboundMessage: (msg) => {
              inboundMessages.push({ receivedAt: new Date().toISOString(), ...msg });
              if (inboundMessages.length > 50) inboundMessages.shift();
              if (inboundHandler) {
                inboundHandler.handleMessage(msg).catch((err) => {
                  this.logger?.warn?.({ err }, "inbound handler error (non-fatal)");
                });
              }
            },
            logger: this.logger,
          });

          // Update handler with actual linked info
          inboundHandler.linkedUserId = result.linkedUserId;
          inboundHandler.linkedUsername = result.linkedUsername || "";

          connected = result;
          break; // success
        } catch (err) {
          lastError = err;
          this.logger?.warn?.({ sessionId, attempt: attempt + 1, err }, "session reconnect attempt failed");
        }
      }

      if (!connected) {
        // All retries exhausted — mark as disconnected
        this.logger?.error?.({ sessionId, workspaceId, err: lastError }, "session reconnect failed after 3 retries");
        await store.markDisconnected().catch(() => {});
        summary.failed++;
        summary.failures.push({
          sessionId,
          workspaceId,
          error: "reconnect_failed: " + (lastError?.message || "unknown"),
        });
        continue;
      }

      // Success — add to pool
      const now = Date.now();
      const outbox = new CloudSessionOutbox(sessionId);
      const rateLimiter = new PerSessionRateLimiter(sessionId);
      const replayLoop = new ReplayLoop({
        sessionId,
        outbox,
        send: async (item) => {
          return this._sendViaGramJS(sessionId, item.payload);
        },
        isConnected: () => {
          const s = this.sessions.get(sessionId);
          return s?.status === "connected";
        },
        logger: this.logger,
      });

      this.sessions.set(sessionId, {
        client: connected.client,
        sessionString: connected.sessionString(),
        linkedUsername: connected.linkedUsername,
        linkedUserId: connected.linkedUserId,
        linkedPhone: connected.linkedPhone,
        workspaceId,
        status: "connected",
        createdAt: meta?.createdAt ? new Date(meta.createdAt).getTime() : now,
        lastActivityAt: now,
        inboundMessages: [],
        inboundHandler: null, // handler is created above but we recreate on messages
        outbox,
        replayLoop,
        rateLimiter,
        store,
      });

      // Wire inbound handler from the connect step
      const session = this.sessions.get(sessionId);
      if (session) {
        // Recreate inbound handler with the restored session
        const restoredHandler = new InboundHandler({
          sessionId,
          workspaceId,
          linkedUserId: connected.linkedUserId || "",
          linkedUsername: connected.linkedUsername || "",
          logger: this.logger,
        });
        session.inboundHandler = restoredHandler;
      }

      // Update metadata status
      store.saveMetadata({
        workspaceId,
        linkedUsername: connected.linkedUsername || "",
        linkedUserId: connected.linkedUserId || "",
        status: "connected",
        createdAt: meta?.createdAt || new Date(now).toISOString(),
        lastConnectedAt: new Date().toISOString(),
      }).catch(() => {});

      // Save refreshed session string (may have changed after reconnect)
      store.saveSessionString(connected.sessionString()).catch(() => {});

      // Drain replay queue
      replayLoop.drain().catch((err) => {
        this.logger?.warn?.({ err, sessionId }, "restored session replay drain failed (non-fatal)");
      });

      summary.restored++;
      this.logger?.info?.({
        sessionId,
        workspaceId,
        username: connected.linkedUsername,
      }, "session restored");
    }

    this.logger?.info?.(summary, "CSM startup: session restore sweep complete");
    return summary;
  }

  /**
   * Clean up sessions idle longer than SESSION_TTL_MS.
   */
  async _cleanupStale() {
    const cutoff = Date.now() - CONFIG.sessionTtlMs;
    const stale = [];
    for (const [id, session] of this.sessions) {
      if (session.lastActivityAt < cutoff) {
        stale.push(id);
      }
    }
    for (const id of stale) {
      this.logger?.info?.({ sessionId: id }, "cleaning up stale session");
      await this.destroySession(id);
    }
  }
}
