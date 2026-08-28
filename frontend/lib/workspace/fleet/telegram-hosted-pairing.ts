/**
 * The hosted-Telegram pairing state machine — the "no BotFather, no token"
 * door on the Telegram card.
 *
 * WHY THIS MODULE EXISTS AT ALL
 * -----------------------------
 * The backend for this shipped complete and was reachable from NOTHING.
 * `POST /sage/telegram-hosted/pair/start`, `GET .../pair/status` and
 * `DELETE .../pair` have existed for months; the only frontend call to any of
 * them was SystemHealthButton.tsx using `pair/status` as a liveness probe.
 * The component that used to drive it (TelegramPairPanel.tsx, on the old fleet
 * home) was deleted, and its door was never added to the Channels surface — so
 * the ONLY way to connect Telegram was to leave the product, create a bot in
 * BotFather and come back with a token. CLAUDE.md's most-documented defect
 * class ("built, tested, and never wired"), on the founder's own #1 launch
 * channel.
 *
 * Pure + tested for the same reason channel-doors.ts and agent-count-shape.ts
 * are: telegram-hosted-pairing.test.ts runs under plain `tsx`, outside
 * Next.js, so this file imports no React and no stylesheet.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT THIS DOOR ACTUALLY CONNECTS — READ BEFORE CHANGING THE COPY
 * ═══════════════════════════════════════════════════════════════════════════
 * The hosted bot is WORKSPACE-scoped and answers as SAGE, not as the agent
 * whose Channels tab you are looking at. Verified in the backend, not assumed:
 *
 *   routes_sage_telegram_hosted.telegram_webhook
 *     workspace_id = hosted.get_workspace_for_chat(chat_id)   ← chat → WORKSPACE
 *     dispatch_sage_reply_safe(workspace_id=..., thread_id="sage-main")
 *                              ^^^ no `specialist_context` argument is passed,
 *     and agent_reply_dispatcher's own docstring says:
 *       "specialist_context: When set, the turn runs as this specialist agent
 *        (persona/model/memory) INSTEAD OF SAGE"
 *
 *   hosted_bot_provisioning_service.py, module docstring, verbatim:
 *       "There is no platform-owned bot pool for specialist agents — the ONE
 *        hosted bot the platform owns is reserved for Sage itself"
 *
 * So the two Telegram doors differ in WHO ANSWERS, and that is exactly the
 * kind of consequence a picker exists to state up front:
 *
 *   Chatbot (BYO)   your own bot token  ─▶ route_agent_inbound ─▶ THIS agent
 *   Empyralis bot   the shared bot      ─▶ dispatch_sage_reply ─▶ SAGE
 *
 * The door's own body says this in plain words. Do NOT reword it into "connect
 * this agent to Telegram" — that would be false, and it is false in a way no
 * test can catch, because the pairing succeeds and a reply does arrive.
 *
 * Making the hosted bot answer AS the agent it was paired from is a real
 * backend change (the pending-code map and the persisted pair record are both
 * workspace-keyed, and pairing codes are an auth boundary). It is not done
 * here, and this comment is the record that it is unfinished rather than
 * impossible — see CLAUDE.md, "A BUG YOU DOCUMENT BECOMES A CONSTRAINT THE
 * NEXT READER INHERITS": route around a defect, never pin it as intended.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE STATES ARE FACTS, AND NO TWO OF THEM SHARE A MESSAGE
 * ═══════════════════════════════════════════════════════════════════════════
 *   checking        we have not found out yet          ← never rendered as "no"
 *   unsupported     this deployment has no hosted bot  ← is_configured() false
 *   unreachable     configured, but Telegram would not answer us just now
 *                   (the 401 circuit breaker, or getMe failing so we cannot
 *                    even name the bot the customer is meant to open)
 *   status_unknown  the status call itself failed      ← NOT the same as "no"
 *   idle            ready, nothing started
 *   waiting         a code/link is live, nobody has tapped it yet
 *   expired         the only way in was a numeric code, and it has expired
 *   connected       a Telegram chat is paired to this workspace
 *
 * `connected` additionally carries `botUnreachable`, because "paired" and
 * "paired but the shared bot's token is dead so replies stopped" are two
 * different facts about a working-looking connection.
 */

/** `GET /sage/telegram-hosted/pair/status` — routes_sage_telegram_hosted.py:97.
 *  Every field optional: a malformed/partial body must degrade to "we don't
 *  know", never to a confident "not connected". */
export type HostedPairingStatusResponse = {
  configured?: boolean | null;
  has_pending_code?: boolean | null;
  paired?: boolean | null;
  suspended?: boolean | null;
  needs_reauth?: boolean | null;
};

/** `POST /sage/telegram-hosted/pair/start` — routes_sage_telegram_hosted.py:58. */
export type HostedPairingStartResponse = {
  pairing_code?: string | null;
  deep_link?: string | null;
  bot_username?: string | null;
  status?: string | null;
  /** How long a NUMERIC pairing code stays valid, from the backend's own
   *  `_PAIRING_CODE_TTL_SECONDS`. Read from the response rather than
   *  hardcoded here: the value is an env var
   *  (EMPYRALIS_TELEGRAM_PAIRING_CODE_TTL_SECONDS) that a deployment can
   *  change, and a transcribed copy of a default would state a countdown this
   *  product cannot honour. Absent (an older backend) = no countdown shown,
   *  never an invented one. */
  expires_in_seconds?: number | null;
};

export type HostedPairingState =
  | { kind: "checking" }
  | { kind: "unsupported" }
  | { kind: "unreachable" }
  | { kind: "status_unknown"; detail: string }
  | { kind: "idle" }
  | {
      kind: "waiting";
      /** Tap-to-pair. Null when the bot's username could not be resolved, or
       *  when a numeric code was already outstanding (the backend cannot
       *  rebuild a link from a numeric code — see pair/start's own
       *  `is_deep_link` branch). */
      deepLink: string | null;
      /** The 6-digit code, ONLY when it is genuinely typeable — see
       *  `hostedPairingCodeIsTypeable`. */
      code: string | null;
      botUsername: string | null;
      /** Epoch ms, or null when nothing here expires (deep-link tokens do not). */
      codeExpiresAt: number | null;
      codeExpired: boolean;
    }
  | { kind: "expired" }
  | { kind: "connected"; botUnreachable: boolean };

/**
 * Should the numeric-code box be shown, and is the value in `pairing_code`
 * actually a code a person can type?
 *
 * THE HISTORICAL BUG THIS ENCODES, structurally rather than by a length
 * constant: `pair/start` returns EITHER a fresh 6-digit code plus a deep link
 * built from a SEPARATE 192-bit token, OR — when a pairing session already
 * exists — the long deep-link TOKEN reused in the `pairing_code` field. The
 * deleted panel rendered that token into the "type this code" box, so the
 * customer was shown 32 characters of base64 to type into Telegram.
 *
 * The old fix compared `pairing_code.length` against a hardcoded 6, i.e. a
 * transcription of the backend's PAIRING_CODE_LENGTH that goes stale silently
 * if that constant ever moves. This derives the same answer from the response
 * itself: when `pairing_code` IS the deep link's own token, the link ends with
 * it. Nothing here knows how long a code is.
 */
export function hostedPairingCodeIsTypeable(start: HostedPairingStartResponse | null): boolean {
  const code = String(start?.pairing_code ?? "").trim();
  if (!code) return false;
  const link = String(start?.deep_link ?? "").trim();
  // The link carries the token as `?start=<token>`; if that token is the very
  // value we were handed as `pairing_code`, the value is a token, not a code.
  if (link && link.endsWith(`=${code}`)) return false;
  return true;
}

/** When the numeric code stops being accepted, in epoch ms — or null when
 *  there is nothing to count down (no typeable code, or a backend that does
 *  not report its own TTL). */
export function hostedPairingCodeExpiry(
  start: HostedPairingStartResponse | null,
  startedAt: number | null,
): number | null {
  if (!hostedPairingCodeIsTypeable(start)) return null;
  if (startedAt === null) return null;
  const ttl = Number(start?.expires_in_seconds ?? 0);
  if (!Number.isFinite(ttl) || ttl <= 0) return null;
  return startedAt + ttl * 1000;
}

export type HostedPairingInput = {
  /** null = the status call has not come back yet. */
  status: HostedPairingStatusResponse | null;
  /** Non-null = the status call FAILED. Distinct from a status that says
   *  "not connected" — collapsing the two is the outcome-honesty law's own
   *  "empty vs. could not load" case. */
  statusError: string | null;
  /** null = nobody has pressed Connect in this session. */
  start: HostedPairingStartResponse | null;
  /** Epoch ms when `start` came back. */
  startedAt: number | null;
  now: number;
};

/**
 * The whole rule, in one place.
 *
 * Precedence is deliberate and each step is a different question:
 *   paired?          a working connection outranks every setup state
 *   do we know?      an unread/failed status is never rendered as "no"
 *   supported here?  a deployment with no hosted bot has no door to offer
 *   reachable?       configured but Telegram will not answer us
 *   started?         only then is there a code/link to be waiting on
 */
export function planHostedPairing(input: HostedPairingInput): HostedPairingState {
  const { status, statusError, start, startedAt, now } = input;

  if (status === null) {
    // A failed status call and a status call still in flight are different
    // facts and get different screens.
    return statusError ? { kind: "status_unknown", detail: statusError } : { kind: "checking" };
  }

  const suspended = Boolean(status.suspended || status.needs_reauth);

  if (status.paired === true) {
    return { kind: "connected", botUnreachable: suspended };
  }

  if (status.configured !== true) {
    // is_configured() is a bare "is a bot token set on this deployment".
    // Explicit false only — an absent field means we were not told, and
    // that belongs in `checking`/`status_unknown`, not in a flat "no".
    return status.configured === false ? { kind: "unsupported" } : { kind: "status_unknown", detail: "" };
  }

  if (suspended) return { kind: "unreachable" };

  if (start === null) return { kind: "idle" };

  const deepLink = String(start.deep_link ?? "").trim() || null;
  const botUsername = String(start.bot_username ?? "").trim() || null;
  const typeable = hostedPairingCodeIsTypeable(start);
  const code = typeable ? String(start.pairing_code ?? "").trim() : null;

  // A code with no bot to send it to is not a way in. This is the state a
  // deployment lands in when the token is set but getMe fails (a revoked or
  // placeholder token): pair/start succeeds, and hands back a code addressed
  // to a bot nobody can name.
  if (!deepLink && !(code && botUsername)) return { kind: "unreachable" };

  const codeExpiresAt = hostedPairingCodeExpiry(start, startedAt);
  const codeExpired = codeExpiresAt !== null && now >= codeExpiresAt;

  // Deep-link tokens carry no TTL in the backend (only _PENDING_PAIRING_CODES
  // is time-tracked), so an expired numeric code does NOT end the pairing when
  // a link is still live — the code box goes away and the link stays. Only a
  // code-only pairing can actually run out.
  if (codeExpired && !deepLink) return { kind: "expired" };

  return {
    kind: "waiting",
    deepLink,
    code: codeExpired ? null : code,
    botUsername,
    codeExpiresAt,
    codeExpired,
  };
}

/**
 * Whether the hosted door can be walked through on this deployment at all.
 *
 * Unknown is NOT unavailable: a door hidden because we have not finished
 * asking is a worse lie than a door that opens onto an honest "checking".
 * Only an explicit `configured: false` takes the door away.
 */
export function hostedDoorSupported(status: HostedPairingStatusResponse | null): boolean | null {
  if (status === null) return null;
  if (status.configured === true) return true;
  if (status.configured === false) return false;
  return null;
}

/**
 * Which Telegram door depends on this INSTALLATION, and which does not.
 *
 * Lives here rather than inline in ChannelsTab so it is testable: the
 * per-door-ness is the whole point (the hosted bot's absence must not make the
 * BYO door look broken), and a rule that only exists inside a .tsx cannot be
 * asserted by the `tsx` unit runner.
 *
 * `undefined` — the return for every door but the hosted one — means "this
 * door has no deployment dependency", which isChannelDoorAvailable treats as
 * supported. That is what keeps every other channel byte-for-byte unchanged.
 */
export function telegramDoorDeploymentSupported(
  doorKey: string,
  status: HostedPairingStatusResponse | null,
): boolean | null | undefined {
  return doorKey === "hosted_bot" ? hostedDoorSupported(status) : undefined;
}

/** The line the panel shows for each state. Lives here, beside the state it
 *  describes, so a state added without copy is a type error rather than a
 *  blank pane. Kept factual: a professional tool labels, it does not lecture. */
export function hostedPairingHeadline(state: HostedPairingState): string {
  switch (state.kind) {
    case "checking":
      return "Checking…";
    case "unsupported":
      return "Not available on this deployment";
    case "unreachable":
      return "Telegram didn't answer";
    case "status_unknown":
      return "Couldn't check this";
    case "idle":
      return "Connect with the Empyralis bot";
    case "waiting":
      return "Waiting for you in Telegram";
    case "expired":
      return "That code expired";
    case "connected":
      return state.botUnreachable ? "Connected — but replies have stopped" : "Connected";
  }
}
