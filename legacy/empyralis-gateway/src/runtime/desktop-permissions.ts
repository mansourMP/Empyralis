import {
  agentComputerSystemServiceModeEnabled,
  agentComputerUserSessionBridgeEnabled,
} from "./service-mode";

export type DesktopPermissionId =
  | "screen_recording"
  | "accessibility"
  | "clipboard"
  | "automation"
  | "browser";

export type DesktopPermissionState =
  | "granted"
  | "promptable"
  | "denied"
  | "restricted"
  | "unknown"
  | "not_applicable";

export interface CapabilityPermissionStatus {
  capability_id: string;
  permission: DesktopPermissionId | "not_applicable";
  state: DesktopPermissionState;
  prompt_available: boolean;
  summary: string;
}

const BLOCKED_PERMISSION_STATES = new Set<DesktopPermissionState>([
  "denied",
  "restricted",
  "unknown",
  "promptable",
]);

const PERMISSION_ENV_KEYS: Record<DesktopPermissionId, string> = {
  screen_recording: "EMPYRALIS_AGENT_COMPUTER_PERMISSION_SCREEN_RECORDING",
  accessibility: "EMPYRALIS_AGENT_COMPUTER_PERMISSION_ACCESSIBILITY",
  clipboard: "EMPYRALIS_AGENT_COMPUTER_PERMISSION_CLIPBOARD",
  automation: "EMPYRALIS_AGENT_COMPUTER_PERMISSION_AUTOMATION",
  browser: "EMPYRALIS_AGENT_COMPUTER_PERMISSION_BROWSER",
};

const DESKTOP_CAPABILITY_PERMISSIONS: Record<string, DesktopPermissionId> = {
  "screenshot.capture": "screen_recording",
  "computer_control.ocr": "screen_recording",
  "computer_control.move": "accessibility",
  "computer_control.click": "accessibility",
  "computer_control.type": "accessibility",
  "computer_control.key": "accessibility",
  "computer_control.clipboard_read": "clipboard",
  "computer_control.clipboard_write": "clipboard",
  "computer_control.list_windows": "automation",
  "computer_control.list_apps": "automation",
  "computer_control.launch": "automation",
  "computer_control.launch_app": "automation",
  "computer_control.notify": "automation",
  "computer_control.applescript": "automation",
  "computer_control.speak": "automation",
  "browser.session.start": "browser",
  "browser.session.action": "browser",
  "browser.session.takeover": "browser",
  "browser.session.resume": "browser",
  "browser.session.interrupt": "browser",
};

function normalizePermissionState(value: unknown): DesktopPermissionState | null {
  const token = String(value ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (token === "1" || token === "true" || token === "yes" || token === "allow" || token === "allowed") {
    return "granted";
  }
  if (token === "0" || token === "false" || token === "no" || token === "blocked") {
    return "denied";
  }
  if (
    token === "granted"
    || token === "promptable"
    || token === "denied"
    || token === "restricted"
    || token === "unknown"
    || token === "not_applicable"
  ) {
    return token;
  }
  return null;
}

function defaultDesktopPermissionState(
  permission: DesktopPermissionId,
  env: NodeJS.ProcessEnv,
): DesktopPermissionState {
  const configured = normalizePermissionState(env[PERMISSION_ENV_KEYS[permission]]);
  if (configured) {
    return configured;
  }
  if (agentComputerSystemServiceModeEnabled(env) && !agentComputerUserSessionBridgeEnabled(env)) {
    return "restricted";
  }
  return "granted";
}

export function desktopPermissionForCapability(capabilityId: unknown): DesktopPermissionId | null {
  const capability = String(capabilityId ?? "").trim();
  return DESKTOP_CAPABILITY_PERMISSIONS[capability] ?? null;
}

export function capabilityPermissionStatus(
  capabilityId: unknown,
  env: NodeJS.ProcessEnv = process.env,
): CapabilityPermissionStatus {
  const capability = String(capabilityId ?? "").trim();
  const permission = desktopPermissionForCapability(capability);
  if (!permission) {
    return {
      capability_id: capability,
      permission: "not_applicable",
      state: "not_applicable",
      prompt_available: false,
      summary: "No desktop OS permission is required for this capability.",
    };
  }
  const state = defaultDesktopPermissionState(permission, env);
  const bridgeReady = agentComputerUserSessionBridgeEnabled(env);
  const promptAvailable = state === "promptable" || (!agentComputerSystemServiceModeEnabled(env) && state !== "granted");
  return {
    capability_id: capability,
    permission,
    state,
    prompt_available: promptAvailable || bridgeReady,
    summary: state === "granted"
      ? "Desktop permission is granted."
      : state === "restricted"
        ? "Desktop permission is restricted in the current runtime session."
        : state === "promptable"
          ? "Desktop permission needs a user-session prompt before execution."
          : state === "denied"
            ? "Desktop permission was denied by the OS or user."
            : "Desktop permission state is unknown.",
  };
}

export function capabilityPermissionReady(
  capabilityId: unknown,
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  const status = capabilityPermissionStatus(capabilityId, env);
  return status.state === "granted" || status.state === "not_applicable";
}

export function filterCapabilitiesByDesktopPermission(
  capabilities: string[],
  env: NodeJS.ProcessEnv = process.env,
): string[] {
  return capabilities.filter((capability) => capabilityPermissionReady(capability, env));
}

export function blockedCapabilityPermissionStatus(
  capabilityId: unknown,
  env: NodeJS.ProcessEnv = process.env,
): CapabilityPermissionStatus | null {
  const status = capabilityPermissionStatus(capabilityId, env);
  return BLOCKED_PERMISSION_STATES.has(status.state) ? status : null;
}

export function assertCapabilityPermissionReady(
  capabilityId: unknown,
  env: NodeJS.ProcessEnv = process.env,
): void {
  const blocked = blockedCapabilityPermissionStatus(capabilityId, env);
  if (!blocked) {
    return;
  }
  throw new Error(
    `blocked/local_permission_denied:${blocked.permission}:${blocked.state}:${blocked.capability_id}`,
  );
}
