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
  | "cli.login.output";

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
}
