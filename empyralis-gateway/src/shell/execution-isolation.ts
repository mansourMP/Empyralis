/**
 * WHICH ISOLATION A COMMAND ACTUALLY RAN UNDER, AND THE ONE TRUE SENTENCE
 * THAT SAYS SO.
 *
 * Founder's ruling (2026-08-22), and it inverts what this capability used to
 * do: "Docker is not something that is going to degrade what we do. It's just
 * something that is fundamentally going to be safer... not like 'this is
 * impossible to run here' or 'because you don't have Docker I don't have any
 * permission to run it'. I don't want to hear any of those things from my
 * agent."
 *
 * So Docker decides HOW a command runs. It never decides WHETHER one runs.
 *
 *   Docker ready       ─▶ sandbox      the command runs inside a throwaway
 *                                      container (docker-sandbox.ts)
 *   Docker not ready   ─▶ host         the command runs on the computer
 *                                      itself, and it WORKS — this is the
 *                                      default, not a degradation, not an
 *                                      error, and never a prompt
 *   full_access opt-in ─▶ full_access  host execution the owner deliberately
 *                                      turned on (unchanged, see below)
 *
 * `host` IS NOT `full_access`, and fusing them would have been the one
 * genuinely dangerous shortcut available here. full_access is a deliberate
 * ESCALATION with its own two-part opt-in (the box operator's local flag AND
 * a server-asserted per-call authorization — see runtime.ts's
 * resolveExecutionMode) and its own cloud-side policy semantics
 * (execution_mode_policy.py's MODE_DEFINITIONS: filesystem scope "/",
 * no per-action gating). `host` asserts none of that: it is what happens when
 * the owner attached a computer that has no sandbox on it. Reusing the
 * full_access token for it would have silently claimed an authorization
 * nobody granted, and would have made a real escalation indistinguishable
 * from an ordinary Docker-less box in every log, trace and tool result.
 *
 * WHAT STILL PROTECTS A HOST RUN, because "no sandbox" must never read as
 * "no rules": command-policy.ts's hard-blocked commands and hard-protected
 * paths (the credential vault, ~/.ssh, ~/.gnupg, /etc/empyralis, the agent's
 * own state dir) are checked in EVERY mode, before this decision is even
 * consulted, and are not bypassable by any mode, tier or caller. Host runs
 * also still start in the agent's own scoped workspace directory.
 *
 * The statements below are the customer's own words. No mechanism, no shell
 * command to copy, no "install Docker to unlock this" — a control that
 * teaches instead of working is the product law this file exists to satisfy.
 */

/** What actually contained this run. Two values, because there are two real
 *  situations — a container, or the computer itself. */
export type IsolationLevel = "sandbox" | "host";

/** The execution path taken. `host` and `full_access` share an implementation
 *  (direct execution on the machine) and differ in WHY they were chosen; see
 *  the module header for why that distinction is kept rather than collapsed. */
export type ExecutionModeId = "sandbox" | "host" | "full_access";

export interface ResolvedExecution {
  mode: ExecutionModeId;
  isolation: IsolationLevel;
  /** One plain sentence stating what is true, for a person. Never an error,
   *  never a warning, never an instruction. */
  statement: string;
  /** Internal, for logs/traces. May name mechanism; `statement` may not. */
  reason: string;
}

export const SANDBOX_STATEMENT = "Commands run isolated in a container on this computer.";
export const HOST_STATEMENT =
  "Commands run directly on this computer, because Docker isn't running here.";
export const FULL_ACCESS_STATEMENT =
  "Commands run directly on this computer, which has full access turned on.";

/**
 * The whole rule, in one pure function so both the executor and its tests
 * ask the same question of the same code.
 *
 * `authorizedMode` is what resolveExecutionMode() decided from the frame —
 * the AUTHORIZATION question, which has nothing to do with Docker.
 * `sandboxAvailable` is whether a container can actually be started right
 * now, AFTER the bounded autostart attempt has already been made and lost
 * (docker-autostart.ts). This function never probes anything itself.
 */
export function resolveExecution(
  authorizedMode: "sandbox" | "full_access",
  sandboxAvailable: boolean,
  sandboxUnavailableDetail?: string,
): ResolvedExecution {
  if (authorizedMode === "full_access") {
    return {
      mode: "full_access",
      isolation: "host",
      statement: FULL_ACCESS_STATEMENT,
      reason: "owner-enabled full_access box, authorized by the cloud control plane",
    };
  }
  if (sandboxAvailable) {
    return {
      mode: "sandbox",
      isolation: "sandbox",
      statement: SANDBOX_STATEMENT,
      reason: "Docker is ready on this box, so the command is containerized",
    };
  }
  const detail = String(sandboxUnavailableDetail ?? "").trim();
  return {
    mode: "host",
    isolation: "host",
    statement: HOST_STATEMENT,
    reason: detail
      ? `no container sandbox is available on this box: ${detail}`
      : "no container sandbox is available on this box",
  };
}
