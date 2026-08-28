/**
 * Pure mapping + outcome classification for the OpenClaw outbound leg
 * (the OpenClaw channel adoption step 3). The mirror image of
 * ./inbound-payload.ts: no socket, no `ws`, no `node:http` here, so every
 * rule below is testable without a live gateway.
 *
 * THE SHAPE IS FORCED, AND IT IS NOT SYMMETRIC WITH INBOUND
 * -------------------------------------------------------
 * Inbound arrives over plain loopback HTTP because the bridge plugin is the
 * one making the call. Outbound cannot: OpenClaw's `admin-http-rpc`
 * allowlist has no send/message method at all, so a stateless HTTP caller
 * cannot deliver. `message.action` is a Gateway **WebSocket** RPC
 * (`dist/send-*.js`'s `sendHandlers["message.action"]`, descriptor scope
 * `operator.write` in `dist/core-descriptors-*.js`), which means delivery
 * needs a live authenticated WS session from this same machine. That is
 * what ./openclaw-gateway-client.ts holds; this file only decides what to
 * put on the wire and what came back.
 *
 * NEVER STRING-MATCH THE FAILURE (CLAUDE.md, "stale string matching")
 * ------------------------------------------------------------------
 * OpenClaw's response frame carries a structured `error.code` from a small
 * closed set (`dist/schema-*.js`'s `ErrorCodes`: INVALID_REQUEST,
 * UNAVAILABLE, AGENT_TIMEOUT, NOT_LINKED, NOT_PAIRED, APPROVAL_NOT_FOUND)
 * plus optional `retryable` / `retryAfterMs`. classifyOpenClawSendResponse
 * below reads ONLY those fields. The human sentence ("unsupported channel:
 * telegram") is carried through for the journal and never matched on — the
 * exact discipline `extensions/telegram/src/sendchataction-401-backoff.ts`
 * cites in their own codebase (read `error_code`, never the prose).
 */

import type {
  GatewayChannelOutboundMediaItem,
  GatewayChannelOutboundPayload,
} from "../protocol/types";
import { OPENCLAW_CHANNEL_KEY_PREFIX, OPENCLAW_PROVIDER } from "./inbound-payload";

/** OpenClaw's own RPC method name. Verified live against
 *  openclaw@2026.6.10 — see the module doc. */
export const OPENCLAW_MESSAGE_ACTION_METHOD = "message.action";
export const OPENCLAW_SEND_ACTION = "send";

/** Least privilege. `message.action`'s descriptor scope is `operator.write`;
 *  `operator.admin` is deliberately NOT requested because it is the only
 *  scope under which OpenClaw honours a client-asserted `senderIsOwner`
 *  (`dist/send-*.js`: `gatewayClientScopes.includes("operator.admin") ?
 *  request.senderIsOwner === true : false`). That client-trusted-owner flag
 *  is literally one of the CVEs in OpenClaw's record; a transport that can
 *  never claim it cannot ever be the thing that reintroduces it. */
export const OPENCLAW_REQUIRED_SCOPES: readonly string[] = ["operator.write"];

export interface OpenClawMessageActionParams {
  channel: string;
  action: string;
  params: Record<string, unknown>;
  idempotencyKey: string;
}

export type OpenClawOutboundMapResult =
  | { ok: true; request: OpenClawMessageActionParams; droppedMedia: GatewayChannelOutboundMediaItem[] }
  | { ok: false; error: string; detail?: string };

/** `openclaw_feishu` -> `feishu`. Returns undefined for any channel_key this
 *  lane does not own, so a mis-routed first-party key (`telegram_personal`)
 *  can never be reshaped into an OpenClaw channel id. */
export function openClawChannelIdFromChannelKey(channelKey: string): string | undefined {
  const normalized = String(channelKey || "").trim().toLowerCase();
  if (!normalized.startsWith(OPENCLAW_CHANNEL_KEY_PREFIX)) return undefined;
  const channelId = normalized.slice(OPENCLAW_CHANNEL_KEY_PREFIX.length);
  return /^[a-z0-9][a-z0-9_-]{0,63}$/.test(channelId) ? channelId : undefined;
}

function readString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

/**
 * Maps one `channel.outbound` request frame payload into `message.action`
 * params. Returns a stable machine-matchable reason code on refusal rather
 * than throwing, exactly like mapOpenClawInboundBody does on the way in.
 */
export function mapOpenClawOutboundPayload(
  payload: GatewayChannelOutboundPayload | undefined,
): OpenClawOutboundMapResult {
  if (!payload || typeof payload !== "object") {
    return { ok: false, error: "openclaw_outbound_payload_missing" };
  }

  const channelId = openClawChannelIdFromChannelKey(payload.channel_key);
  if (!channelId) {
    return {
      ok: false,
      error: "openclaw_outbound_channel_key_not_openclaw",
      detail: String(payload.channel_key || ""),
    };
  }

  const provider = String(payload.provider || "").trim();
  if (provider && provider !== OPENCLAW_PROVIDER) {
    // The cloud always sends provider "openclaw" for this lane
    // (channel_lane_contract_service.OPENCLAW_TRANSPORT_PROVIDER). A
    // different value means the routing table drifted; refuse loudly rather
    // than deliver on a lane whose contract we can no longer vouch for.
    return { ok: false, error: "openclaw_outbound_provider_mismatch", detail: provider };
  }

  // Draft streaming is not part of this lane. `_deliver_local_bridge_personal_
  // reply` never sets `operation`, so seeing one means someone enabled draft
  // streaming for an OpenClaw channel without implementing it here — and a
  // draft delta delivered as a standalone chat message would spam the user
  // with half sentences. Refuse; do not "best effort" it.
  const operation = readString(payload.operation);
  if (operation && operation !== "send_final") {
    return { ok: false, error: "openclaw_outbound_draft_operation_unsupported", detail: operation };
  }

  const target = readString(payload.remote_jid);
  if (!target) return { ok: false, error: "openclaw_outbound_target_missing" };

  const idempotencyKey = readString(payload.idempotency_key);
  if (!idempotencyKey) {
    // OpenClaw dedupes in-flight and completed `message.action` calls by
    // idempotencyKey (`resolveGatewayInflightRequest`), which is the ONLY
    // reason the retry in openclaw-gateway-client.ts is safe. Without one,
    // a retry becomes a duplicate message. Required, not defaulted.
    return { ok: false, error: "openclaw_outbound_idempotency_key_missing" };
  }

  const text = typeof payload.text === "string" ? payload.text : "";
  const droppedMedia = Array.isArray(payload.media) ? payload.media.filter((item) => item && typeof item === "object") : [];
  if (text.trim().length === 0) {
    // Nothing deliverable. A media-only reply cannot be salvaged on this
    // lane (see the media note below), so this is an error, never a silent
    // no-op that the cloud would record as "delivered".
    return {
      ok: false,
      error: droppedMedia.length > 0 ? "openclaw_outbound_media_only_unsupported" : "openclaw_outbound_text_missing",
    };
  }

  // `to`, NOT `target`. Read out of their shipped bundle rather than
  // inferred: dist/message-action-runner-jQYM2Orj.js resolves the
  // destination as
  //
  //   readStringParam(actionParams, "to") ?? readStringParam(actionParams, "channelId")
  //
  // and never consults `target` for the `send` action at all. `target` IS a
  // real param name elsewhere in their surface, which is exactly why this
  // was wrong in a way that type-checked, passed every unit test, and only
  // failed against the live binary.
  //
  // What it cost, observed 2026-08-15 on a real Telegram DM: every reply
  // Empyralis authored was refused three times and then dropped —
  //
  //   openclaw.outbound.attempt_failed  code=UNAVAILABLE
  //                                     detail="ToolInputError: to required"
  //   openclaw.outbound.failed          outcome=transient
  //
  // The turn ran, the model answered, the answer was persisted, and the
  // person got silence. Classified transient (correctly — that is what
  // UNAVAILABLE means on their wire), so it retried and failed identically
  // every time; a permanent input error wearing a retryable code.
  const params: Record<string, unknown> = { to: target, message: text };

  // `replyTo` is OpenClaw's own param name for a native quoted reply
  // (`dist/message-action-runner-*.js` reads `readStringParam(actionParams,
  // "replyTo")`). Only ever set when the cloud explicitly asked for one;
  // the automatic-reply path deliberately passes null so replies don't read
  // as robotic (see _deliver_local_bridge_personal_reply's comment).
  const replyTo = readString(payload.reply_to_external_message_id);
  if (replyTo) params.replyTo = replyTo;

  // Thread id (Telegram forum topic / Slack thread ts, channel-specific).
  // Passed through only when the cloud supplied one; never synthesized.
  const metadata = payload.metadata && typeof payload.metadata === "object" ? payload.metadata : {};
  const threadId = readString((metadata as Record<string, unknown>).thread_id);
  if (threadId) params.threadId = threadId;

  return {
    ok: true,
    request: {
      channel: channelId,
      action: OPENCLAW_SEND_ACTION,
      params,
      idempotencyKey,
    },
    // MEDIA IS NOT FORWARDED ON THIS LANE (same scope line inbound drew).
    // The manifest advertises `media: { text: true, images: false, ... }`,
    // so the cloud should never send any; if it does, the text still goes
    // out and the drop is REPORTED to the caller — journaled by the runtime
    // and echoed in the dispatch result the cloud stores on the outbound
    // row — rather than disappearing.
    droppedMedia,
  };
}

// ── Response classification ───────────────────────────────────────────────

/** OpenClaw's closed error-code set, transcribed from
 *  `dist/schema-*.js`'s `ErrorCodes`. Matched by CODE, never by message. */
export const OPENCLAW_ERROR_CODES = {
  NOT_LINKED: "NOT_LINKED",
  NOT_PAIRED: "NOT_PAIRED",
  AGENT_TIMEOUT: "AGENT_TIMEOUT",
  INVALID_REQUEST: "INVALID_REQUEST",
  APPROVAL_NOT_FOUND: "APPROVAL_NOT_FOUND",
  UNAVAILABLE: "UNAVAILABLE",
} as const;

export interface OpenClawResponseFrame {
  ok?: boolean;
  payload?: unknown;
  error?: {
    code?: unknown;
    message?: unknown;
    details?: unknown;
    retryable?: unknown;
    retryAfterMs?: unknown;
  };
}

export type OpenClawSendOutcome =
  | { status: "delivered"; externalMessageId?: string }
  | {
      status: "rejected" | "transient";
      code: string;
      /** OpenClaw's own human sentence. Journaled and surfaced verbatim for
       *  a human; never branched on. */
      message: string;
      retryAfterMs?: number;
    };

function readRetryAfterMs(value: unknown): number | undefined {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : undefined;
}

/**
 * Pulls a platform message id out of whatever `message.action`'s send
 * handler returned. OpenClaw's `extractToolPayload` result is adapter-shaped
 * (each channel plugin decides), so this probes a few known key spellings
 * and returns undefined rather than inventing one — the cloud treats a
 * missing external id as "delivered, id unknown", which is true, instead of
 * storing a fabricated one.
 */
export function extractOpenClawExternalMessageId(payload: unknown): string | undefined {
  if (!payload || typeof payload !== "object") return undefined;
  const record = payload as Record<string, unknown>;
  const direct =
    readString(record.messageId) ??
    readString(record.message_id) ??
    readString(record.id) ??
    readString(record.ts);
  if (direct) return direct;
  const nested = record.result ?? record.message ?? record.data;
  if (nested && typeof nested === "object" && nested !== record) {
    return extractOpenClawExternalMessageId(nested);
  }
  return undefined;
}

/**
 * Turns one OpenClaw response frame into a structured outcome.
 *
 *   delivered  ok:true. Nothing else counts as delivery.
 *   rejected   a permanent decision by OpenClaw's own policy — an
 *              unconfigured/unknown channel, an action the adapter does not
 *              support, invalid params, or a gateway that is not linked or
 *              paired. Retrying changes nothing; the cloud must see it.
 *   transient  the adapter threw, or the gateway is temporarily unavailable
 *              / timed out. Safe to retry under the SAME idempotencyKey.
 *
 * `retryable: true` on the error shape promotes an otherwise-permanent code
 * to transient, because OpenClaw is explicitly telling us so — that field
 * is the structural signal we asked for, and honouring it is how this stays
 * correct when their code changes without us re-reading their bundle.
 */
export function classifyOpenClawSendResponse(frame: OpenClawResponseFrame | undefined): OpenClawSendOutcome {
  if (frame && frame.ok === true) {
    const externalMessageId = extractOpenClawExternalMessageId(frame.payload);
    return externalMessageId ? { status: "delivered", externalMessageId } : { status: "delivered" };
  }

  const error = (frame && frame.error) || {};
  const code = readString(error.code) ?? "OPENCLAW_ERROR_CODE_MISSING";
  const message = readString(error.message) ?? "OpenClaw returned a failure with no message.";
  const retryAfterMs = readRetryAfterMs(error.retryAfterMs);

  if (error.retryable === true) {
    return { status: "transient", code, message, ...(retryAfterMs ? { retryAfterMs } : {}) };
  }
  if (error.retryable === false) {
    return { status: "rejected", code, message };
  }

  switch (code) {
    case OPENCLAW_ERROR_CODES.UNAVAILABLE:
    case OPENCLAW_ERROR_CODES.AGENT_TIMEOUT:
      return { status: "transient", code, message, ...(retryAfterMs ? { retryAfterMs } : {}) };
    case OPENCLAW_ERROR_CODES.INVALID_REQUEST:
    case OPENCLAW_ERROR_CODES.NOT_LINKED:
    case OPENCLAW_ERROR_CODES.NOT_PAIRED:
    case OPENCLAW_ERROR_CODES.APPROVAL_NOT_FOUND:
      return { status: "rejected", code, message };
    default:
      // An unrecognized code is treated as PERMANENT on purpose. Guessing
      // "transient" would make an unknown, possibly-policy failure loop
      // until the cloud's request timeout, hiding it; a rejection surfaces
      // it immediately with the code and sentence intact.
      return { status: "rejected", code, message };
  }
}

// ── Collision guard with the bridge plugin's cancel predicate ─────────────
//
// The plugin inside OpenClaw cancels OpenClaw's own credential-less failure
// reply via the `message_sending` hook, and that hook fires on EVERY
// outbound delivery — including the ones this file originates. So the two
// halves could in principle fight: if a reply Empyralis authored happened to
// contain both of the plugin's markers, OpenClaw would cancel our real
// message and the user would silently get nothing.
//
// The predicate is content-only and requires BOTH markers together
// (openclaw-bridge-plugin/src/cancel-predicate.ts), so a real reply tripping
// it is vanishingly unlikely — but "vanishingly unlikely" and "cannot
// happen" are different things, and the failure mode is a SILENT drop, the
// exact thing this step is supposed to eliminate. Hence this guard: the
// runtime refuses to hand OpenClaw text that its own suppression hook would
// swallow, turning an invisible cancellation into a loud, journaled
// rejection the cloud sees.
//
// DRIFT: these two literals are duplicated from the plugin (separate npm
// package, compiled separately, loaded inside OpenClaw's process — there is
// no sound import path between them). `openclaw-outbound.test.ts` reads the
// plugin's source file from disk and asserts both markers still appear in
// it, so a rename there fails a test here instead of silently un-guarding
// this path.
export const OPENCLAW_CANCEL_PREDICATE_MARKERS: readonly string[] = [
  "FailoverError",
  "No API key found for provider",
];

/** True when this exact text would be cancelled by the bridge plugin's
 *  `message_sending` predicate. Mirrors isSuppressedCredentiallessTurnReply:
 *  ALL markers must be present, matching its `&&`. */
export function wouldBridgePluginCancel(text: string): boolean {
  const content = String(text ?? "");
  if (content.length === 0) return false;
  return OPENCLAW_CANCEL_PREDICATE_MARKERS.every((marker) => content.includes(marker));
}
