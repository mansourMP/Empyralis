import fs from "fs";
import os from "os";
import path from "path";

export interface GatewayConfig {
  apiBaseUrl: string;
  stateDir: string;
  heartbeatIntervalMs: number;
  reconnectMinDelayMs: number;
  reconnectMaxDelayMs: number;
  personalChannelsEnabled: boolean;
  // ARCHIVED (Phase U1): supervisorUrl, supervisorSecret, supervisorTimeoutMs removed.
  // The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.
  pairingToken?: string;
  gatewayId?: string;
  deviceId?: string;
  gatewayToken?: string;
  displayName?: string;
  browserPythonExecutable: string;
  browserProjectRoot: string;
  /**
   * The box operator's half of the shell_sandbox full_access opt-in (see
   * shell/runtime.ts's GatewayShellRuntimeConfig doc comment for the other,
   * server-asserted half). Defaults to false — sandbox mode is the floor;
   * full_access must be deliberately turned on for this specific box.
   */
  shellFullAccessLocallyEnabled: boolean;
  shellSandboxDockerImage?: string;
  /**
   * The box operator's opt-in for cli.install/cli.login.* (BYO-brain
   * onboarding, Build F) — see runtime/desktop-permissions.ts's cli_setup
   * permission. Defaults to false: a paired box does not let the control
   * plane install software or drive a login flow on it until the operator
   * deliberately turns this on — same "opt-in, not opt-out" posture as
   * shellFullAccessLocallyEnabled above.
   */
  cliSetupLocallyEnabled: boolean;
  /**
   * Shared secret with the OpenClaw bridge plugin running in the OpenClaw
   * gateway process on this same box (`EMPYRALIS_BRIDGE_TOKEN` — the SAME
   * name the plugin reads, deliberately, so a box is configured once).
   * Undefined means the OpenClaw inbound listener does not start at all:
   * there is no unauthenticated mode. Never log this value.
   */
  openclawBridgeToken?: string;
  /** Loopback port for that listener. Matches the plugin's own default
   *  endpoint (`http://127.0.0.1:8790/openclaw/inbound`). */
  openclawBridgePort: number;
  /**
   * The OUTBOUND leg (CHANNEL-ADOPTION-PLAN.md step 3): where the local
   * OpenClaw gateway's own WebSocket listens, and its own auth token.
   *
   * This is a DIFFERENT secret from `openclawBridgeToken` above and must
   * stay that way. `EMPYRALIS_BRIDGE_TOKEN` is a low-privilege loopback
   * secret we mint for the plugin to reach US; this is OpenClaw's own
   * gateway credential (`openclaw gateway run --auth token --token <t>`),
   * which grants `operator.write` on THEIR control plane. Never log it, and
   * never reuse one as the other.
   *
   * An undefined token means no outbound session is opened at all — the
   * OpenClaw channel runtimes are still registered so a send fails with a
   * named reason instead of a generic "unsupported personal channel key".
   * The URL is refused unless it is loopback (openclaw-gateway-client.ts's
   * assertLoopbackWebSocketUrl); there is no escape hatch.
   */
  openclawGatewayUrl: string;
  openclawGatewayToken?: string;
  /**
   * PROVISIONING (CHANNEL-ADOPTION-PLAN.md step 4).
   *
   * `openclawProfile` is the `--profile <name>` this box's OpenClaw instance
   * runs under, i.e. `~/.openclaw-<name>`. ONE ISOLATED INSTANCE PER
   * CUSTOMER, never shared — OpenClaw's own trust model is explicitly
   * single-operator ("not a hostile multi-tenant security boundary"), and
   * that isolation is the entire reason adopting their gateway is safe for a
   * multi-tenant product. The default is deliberately NOT "openclaw" or
   * "default": it must never be able to collide with `~/.openclaw`, the
   * operator's own real instance.
   */
  openclawProfile: string;
  /** Where the `openclaw` binary lives. Resolved on PATH when unset. */
  openclawBinaryPath?: string;
  /** Absolute path to the Empyralis bridge plugin directory that OpenClaw
   *  loads. Derived from this process's own entry path when unset. */
  openclawBridgePluginPath?: string;
}

/** The port half of `openclawGatewayUrl`, so provisioning tells OpenClaw to
 *  listen exactly where the outbound WS client already dials. Falls back to
 *  OpenClaw's own default (18789) for a URL with no explicit port. */
export function openClawGatewayPortFromUrl(rawUrl: string): number {
  try {
    const parsed = new URL(String(rawUrl || "").trim());
    const port = Number.parseInt(parsed.port, 10);
    return Number.isFinite(port) && port > 0 ? port : 18789;
  } catch {
    return 18789;
  }
}

function normalizeBaseUrl(value: string | undefined, fallback: string): string {
  const token = String(value ?? "").trim();
  return (token || fallback).replace(/\/+$/, "");
}

function isCloudEnvironment(env: NodeJS.ProcessEnv): boolean {
  const value = String(
    env.EMPYRALIS_DEPLOY_ENV || env.EXPO_PUBLIC_EMPYRALIS_DEPLOY_ENV || env.NODE_ENV || "",
  )
    .trim()
    .toLowerCase();
  return value === "production" || value === "prod" || value === "staging";
}

function assertCloudApiBaseUrl(value: string, env: NodeJS.ProcessEnv): string {
  if (!isCloudEnvironment(env)) {
    return value;
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("EMPYRALIS_GATEWAY_API_URL must be an absolute HTTPS URL in staging/production.");
  }
  if (["127.0.0.1", "localhost", "0.0.0.0", "::1"].includes(parsed.hostname)) {
    throw new Error("EMPYRALIS_GATEWAY_API_URL cannot point at localhost in staging/production.");
  }
  if (parsed.protocol !== "https:") {
    throw new Error("EMPYRALIS_GATEWAY_API_URL must use HTTPS in staging/production.");
  }
  return value;
}

function normalizePositiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeBoolean(value: string | undefined, fallback: boolean): boolean {
  const token = String(value ?? "").trim().toLowerCase();
  if (!token) {
    return fallback;
  }
  if (["1", "true", "yes", "on", "enabled"].includes(token)) {
    return true;
  }
  if (["0", "false", "no", "off", "disabled"].includes(token)) {
    return false;
  }
  return fallback;
}

function resolveBrowserPythonExecutable(projectRoot: string, explicitValue: string | undefined): string {
  const token = String(explicitValue || "").trim();
  if (token) {
    return token;
  }
  const candidates = [
    path.join(projectRoot, "venv", "bin", "python3"),
    path.join(projectRoot, "venv", "bin", "python"),
    path.join(projectRoot, ".venv", "bin", "python3"),
    path.join(projectRoot, ".venv", "bin", "python"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return "python3";
}

export function assertWebSocketUrl(value: string, env: NodeJS.ProcessEnv = process.env): string {
  if (!isCloudEnvironment(env)) {
    return value;
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("WebSocket URL must be an absolute URL in staging/production.");
  }
  if (parsed.protocol !== "wss:") {
    throw new Error(
      `WebSocket URL must use wss:// in staging/production. Got: ${parsed.protocol}`,
    );
  }
  if (["127.0.0.1", "localhost", "0.0.0.0", "::1"].includes(parsed.hostname)) {
    throw new Error("WebSocket URL cannot point at localhost in staging/production.");
  }
  return value;
}

export function loadGatewayConfig(env: NodeJS.ProcessEnv = process.env): GatewayConfig {
  const homeDir = env.HOME || os.homedir();
  const browserProjectRoot = path.resolve(env.EMPYRALIS_GATEWAY_BROWSER_PROJECT_ROOT || process.cwd());
  const apiBaseUrl = assertCloudApiBaseUrl(
    normalizeBaseUrl(env.EMPYRALIS_GATEWAY_API_URL, "http://127.0.0.1:8001/api"),
    env,
  );
  return {
    apiBaseUrl,
    stateDir: path.resolve(
      env.EMPYRALIS_GATEWAY_STATE_DIR || path.join(homeDir, ".empyralis", "gateway"),
    ),
    heartbeatIntervalMs: normalizePositiveInt(env.EMPYRALIS_GATEWAY_HEARTBEAT_MS, 20_000),
    reconnectMinDelayMs: normalizePositiveInt(env.EMPYRALIS_GATEWAY_RECONNECT_MIN_MS, 1_000),
    reconnectMaxDelayMs: normalizePositiveInt(env.EMPYRALIS_GATEWAY_RECONNECT_MAX_MS, 30_000),
    personalChannelsEnabled: normalizeBoolean(env.EMPYRALIS_GATEWAY_PERSONAL_CHANNELS_ENABLED, false),
    // ARCHIVED (Phase U1): supervisor URL/secret/timeout config removed.
    // The Rust empyralis-supervisor daemon is no longer part of the Empyralis product.
    pairingToken: String(env.EMPYRALIS_GATEWAY_PAIRING_TOKEN || "").trim() || undefined,
    gatewayId: String(env.EMPYRALIS_GATEWAY_ID || "").trim() || undefined,
    deviceId: String(env.EMPYRALIS_GATEWAY_DEVICE_ID || "").trim() || undefined,
    gatewayToken: String(env.EMPYRALIS_GATEWAY_TOKEN || "").trim() || undefined,
    displayName: String(env.EMPYRALIS_GATEWAY_DISPLAY_NAME || "").trim() || os.hostname(),
    browserPythonExecutable: resolveBrowserPythonExecutable(
      browserProjectRoot,
      env.EMPYRALIS_GATEWAY_BROWSER_PYTHON,
    ),
    browserProjectRoot,
    shellFullAccessLocallyEnabled: normalizeBoolean(env.EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED, false),
    shellSandboxDockerImage: String(env.EMPYRALIS_GATEWAY_SHELL_SANDBOX_IMAGE || "").trim() || undefined,
    cliSetupLocallyEnabled: normalizeBoolean(env.EMPYRALIS_GATEWAY_CLI_SETUP_ENABLED, false),
    openclawBridgeToken: String(env.EMPYRALIS_BRIDGE_TOKEN || "").trim() || undefined,
    openclawBridgePort: normalizePositiveInt(env.EMPYRALIS_BRIDGE_PORT, 8790),
    openclawGatewayUrl:
      String(env.EMPYRALIS_OPENCLAW_GATEWAY_URL || "").trim() || "ws://127.0.0.1:18789",
    openclawGatewayToken: String(env.EMPYRALIS_OPENCLAW_GATEWAY_TOKEN || "").trim() || undefined,
    openclawProfile: String(env.EMPYRALIS_OPENCLAW_PROFILE || "").trim() || "empyralis",
    openclawBinaryPath: String(env.EMPYRALIS_OPENCLAW_BINARY || "").trim() || undefined,
    openclawBridgePluginPath: String(env.EMPYRALIS_OPENCLAW_BRIDGE_PLUGIN_PATH || "").trim() || undefined,
  };
}
