export type AgentComputerDesktopSession = "user_session" | "system_service";

function envFlag(env: NodeJS.ProcessEnv, key: string): boolean {
  const value = String(env[key] ?? "").trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes" || value === "on";
}

export function agentComputerSystemServiceModeEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return envFlag(env, "EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE")
    || String(env.EMPYRALIS_AGENT_COMPUTER_SERVICE_MODE ?? "").trim().toLowerCase() === "system";
}

export function agentComputerUserSessionBridgeEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return envFlag(env, "EMPYRALIS_AGENT_COMPUTER_USER_SESSION_BRIDGE")
    || envFlag(env, "EMPYRALIS_AGENT_COMPUTER_USER_SESSION_BRIDGE_READY");
}

export function agentComputerDesktopSession(env: NodeJS.ProcessEnv = process.env): AgentComputerDesktopSession {
  return agentComputerSystemServiceModeEnabled(env) ? "system_service" : "user_session";
}
