import type { FleetAgent, StoppedState } from "./fleet-data";

export type AgentStatusTone = "online" | "ready" | "offline" | "unknown" | "error" | "stopped";

export type AgentSummary = {
  id: string;
  name: string;
  role: string;
  preset: "customer_facing" | "internal_assistant" | "operator";
  runtimeTarget: string;
  hardwareStatus: string;
  // Real placement source (see resolveHardwarePlacement in gateway-box-picker.tsx)
  // — runtimeTarget/hardwareStatus above are display-legacy and must not be
  // used for "where does this agent run", only for online/offline status.
  hardwareAccess: string;
  preferredGatewayId: string;
  lastActivity: string | null;
  tint: TintKey;
  stopped?: StoppedState;
};

export type TintKey = "blue" | "purple" | "amber" | "teal" | "coral";

/* Muted per-agent identity tints (~13% opacity fill + colored glyph).
   These are identity colors, NOT the brand accent. */
export const TINTS: Record<TintKey, { bg: string; fg: string }> = {
  blue: { bg: "rgba(12, 68, 124, 0.16)", fg: "#85B7EB" },
  purple: { bg: "rgba(60, 52, 137, 0.16)", fg: "#AFA9EC" },
  amber: { bg: "rgba(133, 79, 11, 0.16)", fg: "#EF9F27" },
  teal: { bg: "rgba(15, 110, 86, 0.16)", fg: "#5DCAA5" },
  coral: { bg: "rgba(153, 60, 29, 0.16)", fg: "#F0997B" },
};

const TINT_ORDER: TintKey[] = ["blue", "teal", "amber", "coral", "purple"];

/** Spread-out identity tint purely from a list position — used where there's
 *  no FleetAgent record to key off (e.g. the cost-by-agent panel pips). */
export function tintKeyForIndex(index: number): TintKey {
  return TINT_ORDER[((index % TINT_ORDER.length) + TINT_ORDER.length) % TINT_ORDER.length];
}

export function tintForAgent(agent: FleetAgent, index: number): TintKey {
  const r = (agent.role || "").toLowerCase();
  if (r === "customer_facing") return "teal";
  // Stable, spread-out tints for everything else.
  return TINT_ORDER[index % TINT_ORDER.length];
}

function presetForRole(role: string): AgentSummary["preset"] {
  const r = (role || "").toLowerCase();
  if (r === "sage" || r === "operator") return "operator";
  if (r === "customer_facing") return "customer_facing";
  return "internal_assistant";
}

export function toAgentSummary(agent: FleetAgent, index: number): AgentSummary {
  return {
    id: agent.agent_id,
    name: agent.label || "Unnamed agent",
    role: agent.role,
    // purpose_preset is set at creation time by the create-agent wizard
    // (step 2) and returned by fleet_list_agents. Older agents created
    // before that field existed fall back to a role-based guess.
    preset: agent.purpose_preset || presetForRole(agent.role),
    runtimeTarget: agent.runtime_target || "unknown",
    hardwareStatus: agent.hardware_status || "unknown",
    hardwareAccess: agent.hardware_access || "none",
    preferredGatewayId: agent.preferred_gateway_id || "",
    lastActivity: agent.last_activity || null,
    tint: tintForAgent(agent, index),
    stopped: agent.stopped,
  };
}

export function isSageAgent(agent: AgentSummary): boolean {
  const r = agent.role.toLowerCase();
  return r === "sage" || r === "operator" || agent.name.toLowerCase().includes("sage");
}

/** Same match as isSageAgent, over the raw FleetAgent list — used anywhere
 *  that needs the actual agent record (id, project_id, …) rather than the
 *  display-only AgentSummary. */
export function findSageAgent(agents: FleetAgent[]): FleetAgent | null {
  const r = (a: FleetAgent) => (a.role || "").toLowerCase();
  return (
    agents.find((a) => r(a) === "sage" || r(a) === "operator") ||
    agents.find((a) => (a.label || "").toLowerCase().includes("sage")) ||
    null
  );
}

/** Status tone + label — the ONE status vocabulary (contract), used by the
 *  agents list, the Overview tab, and the Now strip alike so an agent never
 *  reads differently in two places:
 *  active (green, has real activity) · ready (calm, hardware-reachable but
 *  never run — a fresh agent's honest first state) · offline (red) ·
 *  not deployed (neutral) · error (red) · stopped (owner-initiated, distinct
 *  from offline — the agent isn't down, it's deliberately paused).
 *  `stopped` wins over every other signal: an agent that's hardware-online
 *  but owner-stopped must still read Stopped everywhere. `hasActivity`
 *  (pass `Boolean(agent.last_activity)`) is what separates Active from
 *  Ready — hardware/deployment reachability alone (e.g. a Cloud agent is
 *  trivially always "online") is not evidence the agent has ever done
 *  anything, and must not read as if it has. */
export function deriveStatus(
  hardwareStatus: string,
  stopped?: boolean,
  hasActivity?: boolean,
): { tone: AgentStatusTone; label: string } {
  if (stopped) return { tone: "stopped", label: "Stopped" };
  if (hardwareStatus === "online") {
    return hasActivity ? { tone: "online", label: "Active" } : { tone: "ready", label: "Ready" };
  }
  if (hardwareStatus === "offline") return { tone: "offline", label: "Offline" };
  if (hardwareStatus === "error") return { tone: "error", label: "Error" };
  return { tone: "unknown", label: "Not deployed" };
}

export function statusClass(tone: AgentStatusTone): string {
  return tone === "online" ? "is-online" : tone === "ready" ? "is-ready" : tone === "offline" ? "is-offline" : tone === "stopped" ? "is-stopped" : "";
}

/** Placement/meta line. Never prints raw "unknown". */
export function derivePlacement(runtimeTarget: string, deployed: boolean): string {
  if (!deployed || !runtimeTarget || runtimeTarget === "unknown") {
    return "Ready to configure";
  }
  if (runtimeTarget === "cloud") return "Cloud";
  return runtimeTarget.replace(/:/g, " · ");
}

/** Compact relative time for list rows ("2h ago", "3d ago"). "—" when unknown. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  if (diffMs < 0) return "just now";
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
