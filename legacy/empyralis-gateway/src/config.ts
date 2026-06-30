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
  supervisorUrl: string;
  supervisorSecret?: string;
  supervisorTimeoutMs: number;
  pairingToken?: string;
  gatewayId?: string;
  deviceId?: string;
  gatewayToken?: string;
  displayName?: string;
  browserPythonExecutable: string;
  browserProjectRoot: string;
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
    supervisorUrl: assertCloudApiBaseUrl(
      normalizeBaseUrl(env.EMPYRALIS_SUPERVISOR_URL, "http://127.0.0.1:7788"),
      env,
    ),
    supervisorSecret: String(env.EMPYRALIS_SUPERVISOR_SECRET || "").trim() || undefined,
    supervisorTimeoutMs: normalizePositiveInt(env.EMPYRALIS_SUPERVISOR_TIMEOUT_MS, 120_000),
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
  };
}
