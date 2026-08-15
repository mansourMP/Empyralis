/**
 * `POST /api/v1/empyralis/channel-login` — a gateway-authenticated door onto
 * OpenClaw's own QR-login methods, so a customer can link a channel from the
 * browser instead of a terminal.
 *
 * WHY A PLUGIN HTTP ROUTE AND NOT A WS METHOD — MEASURED, NOT ASSUMED
 * ------------------------------------------------------------------
 * OpenClaw's QR login lives behind two core gateway methods:
 *
 *     web.login.start    scope: operator.admin   advertise: false
 *     web.login.wait     scope: operator.admin   advertise: false
 *
 * (dist/core-descriptors-B2lASufG.js, `CORE_GATEWAY_METHOD_SPECS`, read from
 *  the installed openclaw@2026.6.10. Confirmed live: an operator.write client
 *  calling web.login.start is refused `INVALID_REQUEST: missing scope:
 *  operator.admin`.)
 *
 * Empyralis's own OpenClaw client connects with `operator.write` and must keep
 * doing so — `operator.admin` is the only scope under which OpenClaw honours a
 * client-asserted `senderIsOwner`, one of their own CVEs.
 *
 * The first attempt here was `api.registerGatewayMethod(..., { scope:
 * "operator.write" })` calling `dispatchGatewayMethod` inside. OpenClaw
 * refuses that, by name:
 *
 *     UNAVAILABLE: Gateway method dispatch is reserved for plugin HTTP routes
 *     that declare contracts.gatewayMethodDispatch: ["authenticated-request"].
 *
 * So this is OpenClaw's own prescribed shape, and the same one their bundled
 * `admin-http-rpc` plugin uses (docs/plugins/admin-http-rpc.md): an HTTP route
 * with `auth: "gateway"` plus the reserved manifest contract. Their doc for it:
 * *"Requests dispatch through the same Gateway method handlers and scope checks
 * as WebSocket RPC after the plugin route auth passes."*
 *
 * WHAT THIS DOES AND DOES NOT CHANGE ABOUT THE TRUST BOUNDARY — STATED PLAINLY
 * ---------------------------------------------------------------------------
 * It was never cryptographic. Both ends of `gateway.auth.token` are written by
 * Empyralis (openclaw-local-secrets.ts mints it; provisioning writes it), so
 * the Empyralis gateway process has always been able to open an admin session
 * against the OpenClaw gateway on its own box. The rule it keeps instead is
 * self-restraint about which methods it CALLS, and that restraint is intact:
 *
 *     still operator.write on the WS client         (unchanged)
 *     still never gateway.restart.request           (unchanged)
 *     still cannot assert senderIsOwner             (unchanged)
 *     one new reachable operation: start/poll a channel link
 *
 * This route is the narrow version of `admin-http-rpc`, which we deliberately
 * do NOT enable: that plugin forwards an ALLOWLIST OF 48 METHODS chosen by the
 * caller. This one takes no method name at all. `action` is `"start"` or
 * `"wait"`, mapped through a frozen table to exactly two method names, and
 * nothing else in the admin surface becomes reachable.
 *
 * NO PER-CHANNEL CODE
 * -------------------
 * `web.login.*` resolves its own provider inside OpenClaw
 * (`resolveWebLoginProvider` scans loaded channel plugins for one whose
 * `gatewayMethodDescriptors` declare these methods — WhatsApp's
 * setup-core-DVEkfMVh.js:237 does). So this handler never names a channel:
 * which channels can be linked this way is derived cloud-side from the
 * generated manifest, exactly like every other channel fact.
 *
 * SECRECY
 * -------
 * A QR payload is a credential in flight — it decodes to a pairing ref plus
 * three key blobs, and whoever scans it links THEIR account. It is passed
 * straight through, never written to disk, and never logged. The one log line
 * carries the action and whether a QR exists, never its bytes and never a
 * length that would fingerprint one.
 */

import type { IncomingMessage, ServerResponse } from "node:http";

import { dispatchGatewayMethod } from "openclaw/plugin-sdk/gateway-method-runtime";

/** The loopback path Empyralis's gateway calls. Namespaced under
 *  `/api/v1/empyralis/` so it can never collide with a core or third-party
 *  route. */
export const EMPYRALIS_CHANNEL_LOGIN_ROUTE = "/api/v1/empyralis/channel-login";

/** OpenClaw's own two methods, forwarded verbatim. The caller never supplies a
 *  method name — this table is the entire reachable surface. */
const OPENCLAW_LOGIN_METHOD_BY_ACTION = {
  start: "web.login.start",
  wait: "web.login.wait",
} as const;

export type ChannelLoginAction = keyof typeof OPENCLAW_LOGIN_METHOD_BY_ACTION;

/** Upper bound on how long one call may block. `web.login.wait` blocks until
 *  the scan lands, the QR rotates, or its own timeout fires, so the CALLER's
 *  deadline has to be the shorter one. 60s leaves room inside Empyralis's own
 *  90s capability timeout. */
const MAX_TIMEOUT_MS = 60_000;
const DEFAULT_TIMEOUT_MS = 30_000;

/** A QR data URL is ~9KB; the request body carries at most one. */
const MAX_BODY_BYTES = 64 * 1024;

function clampTimeout(raw: unknown): number {
  const value =
    typeof raw === "number" && Number.isFinite(raw) ? Math.floor(raw) : DEFAULT_TIMEOUT_MS;
  if (value <= 0) return DEFAULT_TIMEOUT_MS;
  return Math.min(value, MAX_TIMEOUT_MS);
}

function readOptionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value : undefined;
}

export function resolveChannelLoginAction(raw: unknown): ChannelLoginAction | null {
  const action = String(raw ?? "start").trim();
  return action === "start" || action === "wait" ? action : null;
}

/** Params forwarded to OpenClaw, built from the caller's request.
 *
 *  Exported and pure so a test can assert the whole translation layer without
 *  a gateway: everything else in this module is transport. */
export function buildOpenClawLoginRequest(params: Record<string, unknown>): {
  method: string;
  params: Record<string, unknown>;
  timeoutMs: number;
} | null {
  const action = resolveChannelLoginAction(params.action);
  if (!action) return null;
  const timeoutMs = clampTimeout(params.timeoutMs);
  const forwarded: Record<string, unknown> = { timeoutMs };
  const accountId = readOptionalString(params.accountId);
  if (accountId) forwarded.accountId = accountId;
  if (action === "start") {
    if (params.force === true) forwarded.force = true;
  } else {
    // The QR the browser is CURRENTLY showing. OpenClaw uses it to tell "still
    // the same code, keep waiting" from "it rotated, here is the new one" —
    // which is the entire reason a stale square never has to be shown. Passing
    // it is what makes refresh correct rather than a guess.
    const currentQrDataUrl = readOptionalString(params.currentQrDataUrl);
    if (currentQrDataUrl) forwarded.currentQrDataUrl = currentQrDataUrl;
  }
  return { method: OPENCLAW_LOGIN_METHOD_BY_ACTION[action], params: forwarded, timeoutMs };
}

/** Narrow OpenClaw's response to the three facts a link flow needs.
 *
 *  `connected`, `qr_data_url` and `message` are three DIFFERENT facts and are
 *  never collapsed: "linked", "here is a code to scan", and "nothing to scan
 *  and not linked either" are three different screens. */
export function projectLoginPayload(payload: unknown): {
  connected: boolean;
  qr_data_url: string | null;
  message: string;
} {
  const record = (payload ?? {}) as Record<string, unknown>;
  const qr = readOptionalString(record.qrDataUrl);
  return {
    connected: record.connected === true,
    qr_data_url: qr && qr.startsWith("data:image/") ? qr : null,
    message: readOptionalString(record.message) ?? "",
  };
}

async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of req) {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as string);
    total += buf.length;
    if (total > MAX_BODY_BYTES) throw new Error("request body too large");
    chunks.push(buf);
  }
  if (total === 0) return {};
  const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : {};
}

function respondJson(res: ServerResponse, status: number, body: Record<string, unknown>): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": String(Buffer.byteLength(payload)),
    // A QR is a credential in flight. Nothing between here and the browser may
    // keep a copy.
    "cache-control": "no-store",
  });
  res.end(payload);
}

export interface ChannelLoginRegistrarApi {
  registerHttpRoute(params: {
    path: string;
    handler: (req: IncomingMessage, res: ServerResponse) => Promise<boolean | void> | boolean | void;
    auth: "gateway" | "plugin";
    match?: "exact" | "prefix";
    gatewayRuntimeScopeSurface?: "write-default" | "trusted-operator";
  }): void;
}

export function registerChannelLoginRoute(
  api: ChannelLoginRegistrarApi,
  log: (message: string) => void,
): void {
  api.registerHttpRoute({
    path: EMPYRALIS_CHANNEL_LOGIN_ROUTE,
    match: "exact",
    // Gateway HTTP auth — the same shared-secret bearer the WS client already
    // presents. See this file's header for why that is not a new grant.
    auth: "gateway",
    // The route's own scope surface, and the reason the first attempt still saw
    // `missing scope: operator.admin` after the manifest contract was already
    // accepted: a plugin route defaults to `write-default`, which forwards
    // operator.write into the very same method scope check the WS client hits.
    // `trusted-operator` is what OpenClaw's own bundled `admin-http-rpc` sets
    // (dist/extensions/admin-http-rpc/index.js, verbatim) and what their doc
    // describes as "the normal full operator defaults are restored" once the
    // gateway shared-secret bearer has proved possession. It applies to THIS
    // route only — the WS client is untouched and still operator.write, and
    // this route still accepts no method name.
    gatewayRuntimeScopeSurface: "trusted-operator",
    handler: async (req, res) => {
      if ((req.method ?? "").toUpperCase() !== "POST") {
        respondJson(res, 405, { ok: false, error: { code: "INVALID_REQUEST", message: "POST only." } });
        return true;
      }
      let body: Record<string, unknown>;
      try {
        body = await readJsonBody(req);
      } catch (error) {
        respondJson(res, 400, {
          ok: false,
          error: { code: "INVALID_REQUEST", message: error instanceof Error ? error.message : "bad body" },
        });
        return true;
      }
      const request = buildOpenClawLoginRequest(body);
      if (!request) {
        respondJson(res, 400, {
          ok: false,
          error: { code: "INVALID_REQUEST", message: 'action must be "start" or "wait".' },
        });
        return true;
      }
      let response: { ok?: boolean; payload?: unknown; error?: unknown };
      try {
        response = await dispatchGatewayMethod(request.method, request.params, {
          expectFinal: true,
          // The dispatch deadline is ours to enforce and is deliberately a
          // little longer than the one handed to OpenClaw, so a method that
          // answers right at its own timeout still answers rather than racing
          // us.
          timeoutMs: request.timeoutMs + 5_000,
        });
      } catch (error) {
        respondJson(res, 503, {
          ok: false,
          error: { code: "UNAVAILABLE", message: error instanceof Error ? error.message : String(error) },
        });
        return true;
      }
      if (response.ok !== true) {
        // Forwarded verbatim: OpenClaw's own error carries a stable `code`, and
        // re-wording it here would be exactly the stale-string-matching trap
        // this codebase already has scars from.
        respondJson(res, 200, {
          ok: false,
          error: response.error ?? { code: "UNAVAILABLE", message: "OpenClaw web login did not answer." },
        });
        return true;
      }
      const projected = projectLoginPayload(response.payload);
      log(
        `channel-login: ${String(body.action ?? "start")} -> ` +
          `connected=${projected.connected} qr=${projected.qr_data_url ? "yes" : "no"}`,
      );
      respondJson(res, 200, { ok: true, result: projected });
      return true;
    },
  });
}
