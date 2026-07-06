import type { FleetAgent } from "./fleet-data";

export type AgentStatusTone = "online" | "offline" | "unknown";

export type AgentSummary = {
  id: string;
  name: string;
  role: string;
  preset: "customer_facing" | "internal_assistant" | "operator";
  runtimeTarget: string;
  hardwareStatus: string;
  lastActivity: string | null;
  tint: TintKey;
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
    lastActivity: agent.last_activity || null,
    tint: tintForAgent(agent, index),
  };
}

export function isSageAgent(agent: AgentSummary): boolean {
  const r = agent.role.toLowerCase();
  return r === "sage" || r === "operator" || agent.name.toLowerCase().includes("sage");
}

/** Status tone + label. Unknown is calm ("Not deployed"), never alarming. */
export function deriveStatus(hardwareStatus: string): { tone: AgentStatusTone; label: string } {
  if (hardwareStatus === "online") return { tone: "online", label: "Online" };
  if (hardwareStatus === "offline") return { tone: "offline", label: "Offline" };
  return { tone: "unknown", label: "Not deployed" };
}

export function statusClass(tone: AgentStatusTone): string {
  return tone === "online" ? "is-online" : tone === "offline" ? "is-offline" : "";
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
