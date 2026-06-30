import { CONFIG } from "../config.js";
import { mapTelegramInboundMessage } from "./message-mapper.js";

// Dynamic import for ESM GramJS — same pattern as Gateway runtime.ts
const dynamicImport = new Function("specifier", "return import(specifier)");

/**
 * Create a connected GramJS TelegramClient for an existing session.
 *
 * Extracted from empyralis-gateway/src/channels/telegram/runtime.ts:493-538,
 * with the same connection/retry approach:
 *   - StringSession (in-memory, no localStorage dependency)
 *   - TelegramClient with connectionRetries
 *   - NewMessage event handler emitting normalized inbound events
 */
export async function createTelegramClient({ sessionString, onInboundMessage, logger }) {
  if (!sessionString) {
    throw new Error("sessionString is required");
  }
  const apiId = CONFIG.gramjsApiId;
  const apiHash = CONFIG.gramjsApiHash;
  if (!apiId || !apiHash) {
    throw new Error("GRAMJS_API_ID and GRAMJS_API_HASH are required");
  }

  const [telegram, sessions, eventsMod] = await Promise.all([
    dynamicImport("telegram"),
    dynamicImport("telegram/sessions/index.js"),
    dynamicImport("telegram/events/index.js"),
  ]);

  const { TelegramClient } = telegram;
  const { StringSession } = sessions;

  if (!TelegramClient || !StringSession) {
    throw new Error("telegram_package_missing: TelegramClient or StringSession not found");
  }

  const stringSession = new StringSession(sessionString);

  const client = new TelegramClient(
    stringSession,
    apiId,
    apiHash,
    {
      connectionRetries: CONFIG.gramjsConnectionRetries,
    },
  );

  // Linked username — captured by closure below, set after getMe()
  let _linkedUsername = "";

  // Add NewMessage event handler BEFORE connecting
  client.addEventHandler((event) => {
    try {
      const message = event?.message;
      if (!message) return;

      // Group detection: GramJS message.isGroup or chat className check
      const isGroup = Boolean(
        message.isGroup ||
        (message.chat?.className === "Chat" && !message.chat?.isPrivate) ||
        message.chat?.className === "Channel"
      );

      // Extract entities for mention detection
      const entities = message.entities || message.raw?.entities || [];

      // Reply-to tracking: capture the replied-to message ID
      const replyToMsgId = message.replyTo?.replyToMsgId || message.replyToMsgId || null;

      const raw = {
        externalMessageId: String(message.id || ""),
        remoteJid: String(message.chatId || message.peerId || ""),
        senderJid: String(message.senderId || message.sender?.userId || ""),
        pushName: String(message.sender?.firstName || message.sender?.username || ""),
        text: String(message.text || message.message || ""),
        receivedAt: new Date().toISOString(),
        fromMe: Boolean(message.out || message.outgoing),
        isSelfChat: String(message.chatId || message.peerId || "") === String(message.senderId || message.sender?.userId || ""),
        isGroup,
        entities,
        replyToMsgId,
      };

      const normalized = mapTelegramInboundMessage(raw);
      if (normalized && typeof onInboundMessage === "function") {
        onInboundMessage(normalized);
      }
    } catch (_err) {
      // Don't let event handler errors crash the client
      logger?.warn?.({ err: _err }, "telegram inbound event handler error");
    }
  }, new eventsMod.NewMessage({}));

  // Connect
  await client.connect();

  // Extract linked account info
  const me = await client.getMe();
  const linkedUsername = String(me?.username || me?.firstName || "").trim() || undefined;
  const linkedUserId = String(me?.id || "").trim() || undefined;
  const linkedPhone = String(me?.phone || "").trim() || undefined;
  // Update closure-captured _linkedUsername for mention detection in event handler
  _linkedUsername = linkedUsername || "";

  logger?.info?.({ linkedUsername, linkedUserId }, "telegram client connected");

  return {
    client,
    stringSession,
    linkedUsername,
    linkedUserId,
    linkedPhone,
    sessionString: () => String(client.session?.save?.() || stringSession.save() || "").trim(),
  };
}

/**
 * Reconnect a client after disconnect. Returns a fresh client with the same session.
 */
export async function reconnectTelegramClient({ previousSessionString, onInboundMessage, logger }) {
  const sessionString = (typeof previousSessionString === "function")
    ? previousSessionString()
    : previousSessionString;

  logger?.info?.("telegram client reconnecting with existing session");
  return createTelegramClient({ sessionString, onInboundMessage, logger });
}


// ── Sign-in flow ────────────────────────────────────────────────

/** Store for in-progress sign-ins (phone number → {client, phoneCodeHash, expiresAt}) */
const _pendingSignIns = new Map();

/**
 * Step 1: Request a verification code for a phone number.
 * Returns { phoneCodeHash, tempSessionId, client }.
 * The client is kept connected so the code can be verified.
 */
export async function requestSignInCode({ phoneNumber, logger }) {
  const apiId = CONFIG.gramjsApiId;
  const apiHash = CONFIG.gramjsApiHash;
  if (!apiId || !apiHash) {
    throw new Error("GRAMJS_API_ID and GRAMJS_API_HASH are required");
  }
  if (!phoneNumber) {
    throw new Error("phoneNumber is required (international format, e.g. +1234567890)");
  }

  const [telegram, sessions, eventsMod] = await Promise.all([
    dynamicImport("telegram"),
    dynamicImport("telegram/sessions/index.js"),
    dynamicImport("telegram/events/index.js"),
  ]);

  const { TelegramClient } = telegram;
  const { StringSession } = sessions;

  if (!TelegramClient || !StringSession) {
    throw new Error("telegram_package_missing");
  }

  // Start with an empty session
  const client = new TelegramClient(
    new StringSession(""),
    apiId,
    apiHash,
    { connectionRetries: 3},
  );

  await client.connect();

  try {
    const result = await client.sendCode({ apiId, apiHash }, phoneNumber);
    const phoneCodeHash = String(result.phoneCodeHash || "").trim();
    if (!phoneCodeHash) {
      throw new Error("telegram_phone_code_hash_missing");
    }

    const tempSessionId = `pending-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 6)}`;

    _pendingSignIns.set(tempSessionId, {
      client,
      phoneCodeHash,
      phoneNumber,
      expiresAt: Date.now() + 5 * 60 * 1000, // 5 min expiry
    });

    logger?.info?.({ tempSessionId, phoneNumber: phoneNumber.slice(0, 6) + "..." }, "verification code requested");

    return { phoneCodeHash, tempSessionId };
  } catch (err) {
    // Clean up on failure
    try { await client.disconnect(); } catch (_) {}
    throw err;
  }
}

/**
 * Step 2: Verify the code and complete sign-in.
 * Returns a connected client + session info.
 */
export async function verifySignInCode({ tempSessionId, phoneCodeHash, phoneNumber, code, password, onInboundMessage, logger }) {
  const pending = _pendingSignIns.get(tempSessionId);
  if (!pending) {
    throw new Error("sign_in_session_expired_or_not_found");
  }
  if (Date.now() > pending.expiresAt) {
    _pendingSignIns.delete(tempSessionId);
    try { await pending.client.disconnect(); } catch (_) {}
    throw new Error("sign_in_session_expired");
  }

  const { client } = pending;

  try {
    const [telegram, eventsMod2] = await Promise.all([dynamicImport("telegram"), dynamicImport("telegram/events/index.js")]);
    const Api = telegram.Api;

    // Try to sign in with the code
    let _signInRes;
    let _passwordResult;
    try {
      _signInRes = await client.invoke(
        new Api.auth.SignIn({
          phoneNumber,
          phoneCodeHash,
          phoneCode: code,
        })
      );
    } catch (signInErr) {
      const msg = String(signInErr?.message || "").toLowerCase();
      // Check if 2FA password is required
      if (msg.includes("password") || msg.includes("2fa") || msg.includes("two-factor") || msg.includes("SESSION_PASSWORD_NEEDED")) {
        if (!password) {
          throw new Error("two_factor_password_required: Telegram requires your 2FA password. Retry with { password: 'your_2fa_password' }.");
        }
        // Try with password using proper SRP
        try {
          const passwordInfo = await client.invoke(new Api.account.GetPassword());
          let passwordCheck;
          if (passwordInfo.currentAlgo) {
            // SRP-based password check
            const { computeCheck } = await dynamicImport("telegram/Password.js");
            passwordCheck = await computeCheck(passwordInfo, password);
          } else {
            passwordCheck = { password };
          }
          _passwordResult = await client.invoke(
            new Api.auth.CheckPassword({ password: passwordCheck })
          );
          _signInRes = _passwordResult;
        } catch (pwdErr) {
          throw new Error(`two_factor_password_failed: ${pwdErr?.message || "invalid password"}`);
        }
        _signInRes = _passwordResult;
      } else {
        // Check for flood wait
        if (msg.includes("flood") || msg.includes("too_many")) {
          _pendingSignIns.delete(tempSessionId);
          try { await client.disconnect(); } catch (_) {}
          throw new Error(`telegram_flood_wait: ${signInErr?.message}`);
        }
        throw signInErr;
      }
    }

    // Sign-in successful — now set up the connected session
    const me = await client.getMe();
    const linkedUsername = String(me?.username || me?.firstName || "").trim() || undefined;
    const linkedUserId = String(me?.id || "").trim() || undefined;
    const linkedPhone = String(me?.phone || phoneNumber || "").trim() || undefined;
    const sessionString = String(client.session?.save?.() || "").trim();

    // Clean up pending sign-in
    _pendingSignIns.delete(tempSessionId);

    // If the caller wants inbound messages, add the event handler
    if (typeof onInboundMessage === "function") {
      // Inline message mapping to avoid ESM/CJS issues
      const mapMsg = (rawMessage) => {
        const externalMessageId = String(rawMessage.externalMessageId || "").trim();
        const remoteJid = String(rawMessage.remoteJid || "").trim();
        const text = String(rawMessage.text || "").trim();
        if (!externalMessageId || !remoteJid || !text) return null;
        return {
          channel_key: "telegram_personal",
          provider: "telegram_gramjs",
          message: {
            external_message_id: externalMessageId,
            remote_jid: remoteJid,
            sender_jid: String(rawMessage.senderJid || "").trim() || undefined,
            push_name: String(rawMessage.pushName || "").trim() || undefined,
            text,
            received_at: String(rawMessage.receivedAt || "").trim() || new Date().toISOString(),
            from_me: Boolean(rawMessage.fromMe),
            is_self_chat: Boolean(rawMessage.isSelfChat),
            is_group: Boolean(rawMessage.isGroup),
            entities: rawMessage.entities || [],
            reply_to_msg_id: rawMessage.replyToMsgId || null,
          },
        };
      };
      client.addEventHandler((event) => {
        try {
          const message = event?.message;
          if (!message) return;
          const raw = {
            externalMessageId: String(message.id || ""),
            remoteJid: String(message.chatId || message.peerId || ""),
            senderJid: String(message.senderId || message.sender?.userId || ""),
            pushName: String(message.sender?.firstName || message.sender?.username || ""),
            text: String(message.text || message.message || ""),
            receivedAt: new Date().toISOString(),
            fromMe: Boolean(message.out || message.outgoing),
            isSelfChat: String(message.chatId || message.peerId || "") === String(message.senderId || message.sender?.userId || ""),
            isGroup: Boolean(
              message.isGroup ||
              (message.chat?.className === "Chat" && !message.chat?.isPrivate) ||
              message.chat?.className === "Channel"
            ),
            entities: message.entities || message.raw?.entities || [],
            replyToMsgId: message.replyTo?.replyToMsgId || message.replyToMsgId || null,
          };
          const normalized = mapMsg(raw);
          if (normalized) onInboundMessage(normalized);
        } catch (_err) {
          logger?.warn?.({ err: _err }, "telegram inbound event handler error");
        }
      }, new eventsMod2.NewMessage({}));
    }

    logger?.info?.({ linkedUsername, linkedUserId }, "sign-in complete");

    return {
      client,
      sessionString,
      linkedUsername,
      linkedUserId,
      linkedPhone,
    };
  } catch (err) {
    // Clean up on failure
    _pendingSignIns.delete(tempSessionId);
    try { await client.disconnect(); } catch (_) {}
    throw err;
  }
}

