import type { GatewayRequestEnvelope, GatewayToolInvokePayload } from "../protocol/types";

export const EXTERNAL_AGENT_PROXY_CAPABILITY = "external_agent_proxy";

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_BYTES = 256 * 1024;
const ALLOWED_HEADER_NAMES = new Set(["accept", "authorization", "content-type", "x-api-key"]);

function requireObject(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
  return value as Record<string, unknown>;
}

function token(value: unknown): string {
  return String(value ?? "").trim();
}

function isPrivateHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  if (host === "localhost" || host === "localhost.localdomain" || host.endsWith(".local")) {
    return true;
  }
  if (host === "::1" || host.startsWith("127.")) {
    return true;
  }
  if (host.startsWith("10.") || host.startsWith("192.168.")) {
    return true;
  }
  const match = /^172\.(\d{1,2})\./.exec(host);
  if (match) {
    const second = Number(match[1]);
    return second >= 16 && second <= 31;
  }
  return false;
}

function normalizeUrl(value: unknown): URL {
  const raw = token(value);
  if (!raw) {
    throw new Error("url is required.");
  }
  const parsed = new URL(raw);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("external_agent_proxy only supports HTTP and HTTPS targets.");
  }
  if (!isPrivateHost(parsed.hostname)) {
    throw new Error("external_agent_proxy can only reach localhost or private-network targets on the Agent Computer.");
  }
  return parsed;
}

function normalizeMethod(value: unknown): "GET" | "POST" {
  const method = token(value || "GET").toUpperCase();
  if (method === "GET" || method === "POST") {
    return method;
  }
  throw new Error("external_agent_proxy only supports GET and POST.");
}

function normalizeHeaders(value: unknown): Record<string, string> {
  const payload = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  const headers: Record<string, string> = {};
  for (const [name, rawValue] of Object.entries(payload)) {
    const key = token(name).toLowerCase();
    const value = token(rawValue);
    if (!value || !ALLOWED_HEADER_NAMES.has(key)) {
      continue;
    }
    headers[name] = value;
  }
  return headers;
}

async function readResponseBody(response: Response, maxBytes: number): Promise<{ body_json?: unknown; body_text?: string }> {
  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > maxBytes) {
    throw new Error(`external_agent_proxy response exceeded ${maxBytes} bytes.`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json") || text.trim().startsWith("{") || text.trim().startsWith("[")) {
    try {
      return { body_json: JSON.parse(text) as unknown };
    } catch {
      return { body_text: text };
    }
  }
  return { body_text: text };
}

export class ExternalAgentProxyRuntime {
  requestedCapabilities(): string[] {
    return [EXTERNAL_AGENT_PROXY_CAPABILITY];
  }

  supportsCapability(capabilityId: string): boolean {
    return capabilityId === EXTERNAL_AGENT_PROXY_CAPABILITY;
  }

  async handleCapabilityInvoke(
    frame: GatewayRequestEnvelope<GatewayToolInvokePayload>,
  ): Promise<Record<string, unknown>> {
    const payload = requireObject(frame.payload, "tool.invoke payload must be an object.");
    const argumentsPayload = requireObject(payload.arguments ?? {}, "arguments must be an object.");
    const method = normalizeMethod(argumentsPayload.method);
    const url = normalizeUrl(argumentsPayload.url);
    const maxBytes = Math.min(Math.max(Number(argumentsPayload.max_bytes || DEFAULT_MAX_BYTES), 1024), DEFAULT_MAX_BYTES);
    const headers = normalizeHeaders(argumentsPayload.headers);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
    timeout.unref?.();
    try {
      const response = await fetch(url, {
        method,
        headers: method === "POST" ? { "Content-Type": "application/json", ...headers } : headers,
        body: method === "POST" ? JSON.stringify(requireObject(argumentsPayload.json ?? {}, "json must be an object.")) : undefined,
        redirect: "manual",
        signal: controller.signal,
      });
      const body = await readResponseBody(response, maxBytes);
      return {
        ok: response.ok,
        status: response.status,
        content_type: response.headers.get("content-type") || null,
        url: url.toString(),
        observed_at: new Date().toISOString(),
        ...body,
      };
    } finally {
      clearTimeout(timeout);
    }
  }
}
