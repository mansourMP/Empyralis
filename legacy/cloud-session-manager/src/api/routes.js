import { Router } from "express";

/**
 * Create API routes. Accepts the session pool as a dependency.
 * @param {import("../session-pool.js").SessionPool} pool
 * @param {object} logger
 */
export function createRoutes(pool, logger) {
  const router = Router();

  // Health check — includes connected/disconnected session counts for backend health verification
  router.get("/health", async (_req, res) => {
    try {
      const { CloudSessionStore } = await import("../session-store.js");
      const allSessions = await CloudSessionStore.listAllSessionIds();
      const connected = allSessions.filter(s => s.meta?.status === "connected").length;
      const disconnected = allSessions.filter(s => s.meta?.status === "disconnected").length;
      res.json({
        ok: true,
        version: "0.1.0",
        liveSessionCount: pool.sessionCount,
        sessions: {
          connected,
          disconnected,
          total: allSessions.length,
        },
        uptimeMs: process.uptime() * 1000,
      });
    } catch (_err) {
      // Fallback if Redis is unreachable
      res.json({
        ok: true,
        version: "0.1.0",
        liveSessionCount: pool.sessionCount,
        sessions: { connected: pool.sessionCount, disconnected: 0, total: pool.sessionCount },
        uptimeMs: process.uptime() * 1000,
      });
    }
  });

  // Provision a new session from a sessionString
  router.post("/sessions", async (req, res) => {
    try {
      const { sessionString, workspace_id } = req.body || {};
      if (!sessionString || !String(sessionString).trim()) {
        return res.status(400).json({ error: "sessionString is required" });
      }
      const opts = {};
      if (workspace_id) opts.workspaceId = String(workspace_id).trim();
      const sessionId = await pool.createSession(String(sessionString).trim(), opts);
      const session = pool.getSession(sessionId);
      res.status(201).json(session);
    } catch (err) {
      const message = err?.message || "unknown_error";
      const status = message.includes("max_sessions") ? 429
        : message.includes("required") ? 400
        : 500;
      logger?.error?.({ err }, "POST /sessions failed");
      res.status(status).json({ error: message });
    }
  });

  // Get session status
  router.get("/sessions/:sessionId/status", (req, res) => {
    const session = pool.getSession(req.params.sessionId);
    if (!session) {
      return res.status(404).json({ error: "session_not_found" });
    }
    res.json(session);
  });

  // List all sessions (diagnostics)
  router.get("/sessions", (_req, res) => {
    const ids = pool.listSessionIds();
    const sessions = ids.map((id) => pool.getSession(id)).filter(Boolean);
    res.json({ count: sessions.length, sessions });
  });

  // Delete a session
  router.delete("/sessions/:sessionId", async (req, res) => {
    const destroyed = await pool.destroySession(req.params.sessionId);
    if (!destroyed) {
      return res.status(404).json({ error: "session_not_found" });
    }
    res.json({ ok: true, sessionId: req.params.sessionId });
  });

  // Stage 3: Outbox status
  router.get("/sessions/:sessionId/outbox", async (req, res) => {
    try {
      const { CloudSessionOutbox } = await import("../outbox/outbox.js");
      const outbox = new CloudSessionOutbox(req.params.sessionId);
      const summary = await outbox.summarize();
      await outbox.disconnect();
      res.json(summary);
    } catch (err) {
      logger?.error?.({ err }, "outbox status failed");
      res.status(500).json({ error: err?.message || "outbox_error" });
    }
  });

  // Stage 3: Trigger replay drain
  router.post("/sessions/:sessionId/outbox/drain", async (req, res) => {
    try {
      const session = pool._getInternal?.(req.params.sessionId);
      // Fallback: create replay loop directly
      const { CloudSessionOutbox } = await import("../outbox/outbox.js");
      const { ReplayLoop } = await import("../outbox/replay-loop.js");
      const outbox = new CloudSessionOutbox(req.params.sessionId);
      const loop = new ReplayLoop({
        sessionId: req.params.sessionId,
        outbox,
        send: async (item) => {
          // Use pool's internal send
          return pool._sendViaGramJS?.(req.params.sessionId, item.payload) || { ok: false, error: "pool_send_unavailable" };
        },
        isConnected: () => {
          const s = pool.getSession(req.params.sessionId);
          return s?.status === "connected";
        },
        logger,
      });
      const result = await loop.drain();
      await outbox.disconnect();
      res.json({ ok: true, ...result });
    } catch (err) {
      logger?.error?.({ err }, "outbox drain failed");
      res.status(500).json({ error: err?.message || "drain_error" });
    }
  });

  // Stage 2: Receive outbound reply from backend, deliver via GramJS
  router.post("/sessions/:sessionId/inbound", async (req, res) => {
    try {
      const { text, remote_jid, reply_to_message_id } = req.body || {};
      if (!text || !String(text).trim()) {
        return res.status(400).json({ error: "text is required" });
      }

      const result = await pool.sendMessage(req.params.sessionId, {
        text: String(text).trim(),
        remoteJid: String(remote_jid || "me").trim() || "me",
      });

      if (!result.ok) {
        const status = result.error === "session_not_found" ? 404 : 502;
        return res.status(status).json({ error: result.error });
      }

      logger?.info?.({ sessionId: req.params.sessionId, messageId: result.messageId }, "outbound reply delivered");
      res.json({ ok: true, message_id: result.messageId });
    } catch (err) {
      logger?.error?.({ err }, "POST /sessions/:id/inbound failed");
      res.status(500).json({ error: err?.message || "delivery_failed" });
    }
  });

  // ── Sign-in flow ──────────────────────────────────────────────
  // Step 1: Request a verification code
  // POST /sessions/sign-in/request-code  { phoneNumber }
  // Returns { phoneCodeHash, tempSessionId }
  router.post("/sessions/sign-in/request-code", async (req, res) => {
    try {
      const { phoneNumber } = req.body || {};
      if (!phoneNumber || !String(phoneNumber).trim()) {
        return res.status(400).json({ error: "phoneNumber is required (international format, e.g. +1234567890)" });
      }

      const { phoneCodeHash, tempSessionId } = await pool.requestSignInCode(String(phoneNumber).trim());
      res.json({
        ok: true,
        tempSessionId,
        phoneCodeHash: phoneCodeHash.slice(0, 8) + "...",
        _phoneCodeHash: phoneCodeHash, // full hash for the verify step
        nextStep: "Enter the code Telegram sent you via SMS/Telegram app, then POST /sessions/sign-in/verify-code",
      });
    } catch (err) {
      const message = err?.message || "unknown_error";
      logger?.error?.({ err }, "sign-in request-code failed");
      res.status(message.includes("required") ? 400 : 500).json({ error: message });
    }
  });

  // Step 2: Verify the code and create the session
  // POST /sessions/sign-in/verify-code  { tempSessionId, phoneCodeHash, phoneNumber, code, password? }
  // Returns { sessionId, sessionString, linkedUsername }
  router.post("/sessions/sign-in/verify-code", async (req, res) => {
    try {
      const { tempSessionId, phoneCodeHash, phoneNumber, code, password } = req.body || {};
      if (!tempSessionId) return res.status(400).json({ error: "tempSessionId is required" });
      if (!phoneCodeHash) return res.status(400).json({ error: "phoneCodeHash is required (full value from step 1)" });
      if (!phoneNumber) return res.status(400).json({ error: "phoneNumber is required" });
      if (!code) return res.status(400).json({ error: "code is required (the verification code Telegram sent you)" });

      const result = await pool.verifySignInCode({
        tempSessionId: String(tempSessionId).trim(),
        phoneCodeHash: String(phoneCodeHash).trim(),
        phoneNumber: String(phoneNumber).trim(),
        code: String(code).trim(),
        password: password ? String(password).trim() : undefined,
      });

      res.json({
        ok: true,
        sessionId: result.sessionId,
        linkedUsername: result.linkedUsername,
        linkedPhone: result.linkedPhone ? `***${result.linkedPhone.slice(-4)}` : undefined,
        sessionString: result.sessionString,
        _sessionString: result.sessionString,
        note: "Save this sessionString — it can be used to reconnect without re-verification. POST /sessions with it next time.",
      });
    } catch (err) {
      const message = err?.message || "unknown_error";
      logger?.error?.({ err }, "sign-in verify-code failed");
      const status = message.includes("invalid") || message.includes("expired") ? 400
        : message.includes("password") ? 401
        : 500;
      res.status(status).json({ error: message });
    }
  });

  return router;
}
