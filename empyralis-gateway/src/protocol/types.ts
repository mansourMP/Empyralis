export const PROTOCOL_VERSION = "v1alpha2";

export type GatewayRequestType =
  | "gateway.connect"
  | "gateway.heartbeat"
  | "gateway.probe"
  | "gateway.state.update"
  | "gateway.disconnect"
  | "tool.invoke"
  | "tool.interrupt"
  | "channel.outbound";

export type GatewayEventType =
  | "gateway.hello"
  | "gateway.presence"
  | "channel.inbound"
  | "cli.login.output"
  | "tool.invoke.chunk";

export type GatewayFrameKind = "request" | "response" | "event";

export interface GatewayScope {
  tenant_id: string;
  workspace_id: string;
  user_id: string;
  device_id: string;
  gateway_id: string;
}

export interface GatewayRequestEnvelope<TPayload = Record<string, unknown>> {
  kind: "request";
  protocolVersion?: string;
  id: string;
  type: GatewayRequestType;
  ts: string;
  scope?: GatewayScope;
  payload: TPayload;
}

export interface GatewayResponseEnvelope<TPayload = Record<string, unknown>> {
  kind: "response";
  protocolVersion?: string;
  id: string;
  ok: boolean;
  ts: string;
  payload?: TPayload;
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
}

export interface GatewayEventEnvelope<TPayload = Record<string, unknown>> {
  kind: "event";
  protocolVersion?: string;
  type: GatewayEventType;
  ts: string;
  scope?: GatewayScope;
  seq?: number;
  ack?: number;
  payload: TPayload;
}

export type GatewayFrame =
  | GatewayRequestEnvelope
  | GatewayResponseEnvelope
  | GatewayEventEnvelope;

export interface GatewaySessionPayload {
  session_id: string;
  gateway_id: string;
  session_token: string;
  ws_url: string;
  heartbeat_interval_seconds: number;
  scope: GatewayScope;
  gateway: Record<string, unknown>;
  created_at?: string;
  expires_at?: string;
}

export interface GatewayRegistrationPayload {
  gateway: {
    gateway_id: string;
    device_id: string;
    tenant_id: string;
    workspace_id: string;
    user_id: string;
    status: string;
    display_name?: string | null;
    platform?: string | null;
    metadata?: Record<string, unknown>;
    capabilities?: string[];
    journal_cursor?: number;
    checkpoint_cursor?: number;
    created_at?: string;
    updated_at?: string;
    last_seen_at?: string | null;
    last_heartbeat_at?: string | null;
  };
  gateway_token: string;
  scope: GatewayScope;
}

export interface GatewayToolInvokePayload {
  capability_id: string;
  arguments: Record<string, unknown>;
  run_id: string;
  trace_id: string;
  workspace_id: string;
  runtime_access_mode?: string;
  empyralis_approved?: boolean;
  agent_scope?: string;
  policy?: Record<string, unknown> | null;
}

export interface GatewayToolInterruptPayload {
  run_id: string;
  target_request_id?: string;
  trace_id: string;
  workspace_id: string;
  reason?: string;
}

/** Phase 2 (streaming): a partial-text event for an in-flight tool.invoke,
 *  correlated by request_id (the SAME id as the eventual response frame).
 *  Fire-and-forget, best-effort — the durable tool.invoke/response pair is
 *  still the authoritative delivery; losing a chunk changes nothing except
 *  how "live" the reply looks while streaming. */
export interface GatewayToolInvokeChunkPayload {
  request_id: string;
  delta: string;
}

/** Shared media-kind vocabulary for the channel.inbound / channel.outbound
 *  media contract — see GatewayChannelInboundMediaItem/OutboundMediaItem. */
export type GatewayChannelMediaKind = "image" | "voice" | "audio" | "video" | "file";

/** A single inbound media attachment. The gateway downloads the bytes and
 *  persists them locally under its own state dir (see
 *  state/db.ts's GatewayStateDb.rootDirPath()); `media_id` is the path to
 *  that file, RELATIVE to the gateway state dir root, not an opaque token
 *  or a fetchable URL. There is no HTTP media-fetch endpoint on the gateway
 *  — the contract is "gateway writes bytes to a well-known local path
 *  under its state dir, the server reads that same path directly" (the
 *  gateway and the server share a filesystem / state-dir mount on a paired
 *  Agent Computer box). See channels/telegram/runtime.ts's
 *  downloadAndStoreTelegramMedia() for the producer side of this contract. */
export interface GatewayChannelInboundMediaItem {
  kind: GatewayChannelMediaKind;
  media_id: string;
  mime_type: string;
  filename?: string;
  size_bytes: number;
  duration_sec?: number;
}

export interface GatewayChannelInboundPayload {
  channel_key: string;
  provider: string;
  message: {
    external_message_id: string;
    remote_jid: string;
    sender_jid?: string;
    push_name?: string;
    text: string;
    received_at: string;
    from_me?: boolean;
    /** Group/mention/reply metadata WhatsApp's mapper populates for its
     *  group-gating logic (see whatsapp/runtime.ts's handleMessagesUpsert) —
     *  optional since other channel mappers (e.g. Telegram's) don't set them. */
    is_self_chat?: boolean;
    is_group?: boolean;
    is_mentioned?: boolean;
    is_reply_to_sage?: boolean;
    quoted_stanza_id?: string;
    /** Human-readable chat/group name (e.g. a WhatsApp group's subject, a
     *  Telegram group/channel's title) — best-effort, present only when the
     *  channel adapter resolved one for a group chat. The server folds this
     *  into the owner-unified activity-feed's mirrored "[sent to X · Y]"
     *  entries (see personal_channel_sage_bridge_service.py); never a trust
     *  boundary, since any group member/admin can set it. Absent for 1:1
     *  chats and for channels that haven't resolved a title. */
    chat_title?: string;
    /** Present only when the inbound message carried one or more media
     *  attachments (photo/voice/audio/video/document/sticker on Telegram).
     *  Absent (not an empty array) for text-only messages. */
    media?: GatewayChannelInboundMediaItem[];
  };
}

/** cli.login.output (Build F): pushed by the Gateway, out of band from the
 *  cli.login.start request/response, as a login session produces output or
 *  finishes. Only ever carries a URL, a "paste code" prompt, or a final
 *  done/ok/error — never raw CLI stdout. See llm/cli-login-session.ts's
 *  module doc comment for why. */
export interface GatewayCliLoginOutputPayload {
  run_id: string;
  runtime: "claude_code" | "codex";
  event: "output" | "done";
  kind?: "url" | "code_prompt";
  text?: string;
  ok?: boolean;
  error?: string;
  error_kind?: string;
}

/** A single outbound media attachment the server wants delivered. Exactly
 *  one of source_path/source_url must be set — source_path is a local
 *  filesystem path the gateway process can read directly (shared state-dir
 *  mount on a paired Agent Computer box, same assumption as
 *  GatewayChannelInboundMediaItem.media_id), source_url is a direct URL the
 *  channel's own client library downloads/streams itself. */
export interface GatewayChannelOutboundMediaItem {
  kind: GatewayChannelMediaKind;
  source_path?: string;
  source_url?: string;
  mime_type?: string;
  caption?: string;
  as_voice?: boolean;
}

export interface GatewayChannelOutboundPayload {
  channel_key: string;
  provider: string;
  remote_jid: string;
  text: string;
  idempotency_key: string;
  operation?: "draft_start" | "draft_delta" | "draft_final" | "send_final";
  draft_id?: string;
  sequence?: number;
  delta?: string;
  reply_to_external_message_id?: string;
  metadata?: Record<string, unknown>;
  /** Present when the server wants one or more media attachments sent
   *  alongside (or instead of) `text`. See GatewayChannelOutboundMediaItem. */
  media?: GatewayChannelOutboundMediaItem[];
}
