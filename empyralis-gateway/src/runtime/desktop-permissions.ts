import {
  agentComputerSystemServiceModeEnabled,
  agentComputerUserSessionBridgeEnabled,
} from "./service-mode";

export type DesktopPermissionId =
  | "screen_recording"
  | "accessibility"
  | "clipboard"
  | "automation"
  | "browser"
  | "shell_sandbox"
  | "llm_runtime"
  | "cli_setup";

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
  shell_sandbox: "EMPYRALIS_AGENT_COMPUTER_PERMISSION_SHELL_SANDBOX",
  llm_runtime: "EMPYRALIS_AGENT_COMPUTER_PERMISSION_LLM_RUNTIME",
  cli_setup: "EMPYRALIS_AGENT_COMPUTER_PERMISSION_CLI_SETUP",
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
  "shell.execute": "shell_sandbox",
  "filesystem.read_write": "shell_sandbox",
  // BYO-brain Phase 2: the on-box LLM capability is gated the same way
  // shell_sandbox is gated on Docker — only "granted" when a local model
  // runtime (Ollama) has actually been confirmed reachable.
  "llm.generate": "llm_runtime",
  // BYO-brain onboarding (Build F): install/login are real side effects
  // (a global npm install, a spawned OAuth flow) initiated remotely by the
  // control plane, so — same defense-in-depth posture as shell_sandbox and
  // llm_runtime — they need an explicit local opt-in, not a default grant.
  // Unlike those two, there's no external service to probe readiness of;
  // the "readiness" here is a one-time box-operator decision (see
  // cliSetupLocallyEnabled below), not an environment fact.
  "cli.install": "cli_setup",
  "cli.login.start": "cli_setup",
  "cli.login.input": "cli_setup",
};

// Docker readiness for the shell_sandbox permission. Unlike the other
// permissions above (OS-level, read from env vars with a "granted by
// default" fallback), shell_sandbox has NO default-granted fallback — it is
// only ever "granted" when Docker has been actively confirmed ready.
// Updated from health/service-inventory.ts right after it probes Docker, so
// this reflects the same probe result the rest of capability-readiness
// reporting uses (no separate probe, no extra race).
let shellSandboxDockerReady = false;

export function setShellSandboxDockerReady(ready: boolean): void {
  shellSandboxDockerReady = ready;
}

// Ollama (local model runtime) readiness for the llm_runtime permission.
// Same shape as shellSandboxDockerReady: NO "granted by default" fallback —
// the on-box LLM capability is only "granted" when a local Ollama endpoint has
// been actively confirmed reachable. Updated from health/service-inventory.ts
// right after it probes Ollama, so it reflects the same probe result the rest
// of capability-readiness reporting uses (no separate probe, no extra race).
let llmRuntimeOllamaReady = false;

export function setLlmRuntimeOllamaReady(ready: boolean): void {
  llmRuntimeOllamaReady = ready;
}

// cli_subscription (Phase 3): the SAME llm_runtime permission also gates
// llm.generate for the owner's own Claude Code / Codex CLI. A box with ONLY
// Claude Code ready (no Ollama at all) must still get llm.generate advertised
// — otherwise the capability never even reaches the router, and the control
// plane sees "gateway_capability_missing" instead of the far more useful
// "claude_code is not ready" it can actually act on. "Ready" here means
// installed AND authenticated (see probeClaudeCli/probeCodexCli in
// health/service-inventory.ts — status "ready", not "degraded"), same
// installed-vs-authenticated distinction Ollama's reachability check draws.
// Grok Build (xAI) / Cursor CLI addition: same shape, same "installed AND
// authenticated" bar (see probeGrokBuildCli/probeCursorCli in
// health/service-inventory.ts), just two more independent backends the
// llm_runtime OR-gate below checks.
let llmRuntimeClaudeCodeReady = false;
let llmRuntimeCodexReady = false;
let llmRuntimeGrokBuildReady = false;
let llmRuntimeCursorReady = false;

export function setLlmRuntimeClaudeCodeReady(ready: boolean): void {
  llmRuntimeClaudeCodeReady = ready;
}

export function setLlmRuntimeCodexReady(ready: boolean): void {
  llmRuntimeCodexReady = ready;
}

export function setLlmRuntimeGrokBuildReady(ready: boolean): void {
  llmRuntimeGrokBuildReady = ready;
}

export function setLlmRuntimeCursorReady(ready: boolean): void {
  llmRuntimeCursorReady = ready;
}

// cli_setup (Build F): the box operator's explicit, one-time opt-in for
// letting this Gateway install Claude Code/Codex and run their login flows
// when the control plane asks. Read once at startup from
// EMPYRALIS_GATEWAY_CLI_SETUP_ENABLED (see config.ts) — a static local
// policy choice, not a probed environment fact, so this is set once and
// never flips during the process's lifetime (unlike shellSandboxDockerReady/
// llmRuntimeOllamaReady, which track a real external dependency that could
// come and go). No default-granted fallback, same as shell_sandbox/
// llm_runtime — false until the box operator turns it on.
let cliSetupLocallyEnabled = false;

export function setCliSetupLocallyEnabled(enabled: boolean): void {
  cliSetupLocallyEnabled = enabled;
}

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
  if (permission === "shell_sandbox") {
    // No "granted by default" fallback for this one — absence of a
    // confirmed-ready Docker daemon means restricted, never granted.
    return shellSandboxDockerReady ? "granted" : "restricted";
  }
  if (permission === "llm_runtime") {
    // No "granted by default" fallback either — the on-box LLM capability is
    // only granted when AT LEAST ONE backend is confirmed ready: the local
    // Ollama runtime, or the owner's own Claude Code / Codex CLI
    // (cli_subscription, Phase 3). A box with only one of these ready must
    // still advertise llm.generate — the runtime dispatch itself (Gateway
    // side: llm/runtime.ts; control plane side:
    // _cli_subscription_readiness_reason) is what enforces WHICH specific
    // runtime a given turn actually needs.
    return (
      llmRuntimeOllamaReady || llmRuntimeClaudeCodeReady || llmRuntimeCodexReady
      || llmRuntimeGrokBuildReady || llmRuntimeCursorReady
    ) ? "granted" : "restricted";
  }
  if (permission === "cli_setup") {
    return cliSetupLocallyEnabled ? "granted" : "restricted";
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
