/**
 * WHERE A NEW AGENT RUNS — pure, framework-free, so a plain `tsx` test drives
 * the real rule (same discipline as agent-count-shape.ts / channel-doors.ts /
 * agent-create-model.ts).
 *
 * ── Why placement is back, and why it is FIRST ───────────────────────────
 * Creation was cut to name + model. The founder measured the result against
 * the wizard that had been deleted and asked for placement back on the first
 * screen. That ordering is not a preference, it is a DEPENDENCY:
 *
 * ```
 *   step 1  where it runs   cloud only · a cloud VPS · a paired computer
 *              │
 *              ▼  decides what step 2 may honestly offer
 *   step 2  who pays        platform · your key   ALWAYS
 *                           your subscription · run locally   ONLY on a box
 * ```
 *
 * "Your subscription" and "Run locally" both route the BRAIN through a
 * Gateway on a real machine. A cloud-only agent has none, so offering them
 * there is a control that cannot be completed — the dead control the product
 * law forbids, and the exact dead end the old wizard's own comment described
 * ("surfaces later as an unrunnable 'Needs sign-in' agent"). Ask where first,
 * and the second question can only ever offer real answers.
 *
 * ── This is NOT the project question, and must never become it ───────────
 * An agent belongs to the WORKSPACE (CLAUDE.md, 2026-08-20). The old
 * wizard's Placement step carried a "Which project?" `<select>` beside these
 * options; that field is gone and does not come back. Placement answers
 * "which machine", never "which room".
 *
 * ── The two halves of a placement are separate facts ─────────────────────
 * `hardware_access` (none | vps | gateway) and `preferred_gateway_id` are
 * two different columns and are written together or not at all — a
 * non-cloud access bucket with no box is an agent that believes it has
 * hardware and can never reach any, so `planAgentCreatePlacement` refuses
 * rather than emitting half of it.
 */

export type AgentCreatePlacement = "cloud" | "vps" | "gateway";

export type AgentCreatePlacementOption = {
  id: AgentCreatePlacement;
  label: string;
  /** One line, stating the consequence — never an instruction. */
  body: string;
};

/** The same three buckets the Hardware tab's own picker already uses
 *  (none/vps/gateway), worded for someone who has not met the concept yet.
 *  Cloud is first and is the default: it needs nothing, and it is what
 *  almost every first agent should be. */
export const AGENT_CREATE_PLACEMENTS: readonly AgentCreatePlacementOption[] = [
  {
    id: "cloud",
    label: "Cloud",
    body: "Runs on Empyralis. Works on tasks and documents right away.",
  },
  {
    id: "vps",
    label: "Cloud VPS",
    body: "A server Empyralis provisions, or your own over SSH.",
  },
  {
    id: "gateway",
    label: "Paired computer",
    body: "A machine you have paired. It gets a shell there.",
  },
];

export const AGENT_CREATE_DEFAULT_PLACEMENT: AgentCreatePlacement = "cloud";

/** The shape `/api/gateway/registrations` actually returns. Deliberately all
 *  optional: this is a wire payload, not a type we own, and a field that
 *  stops being sent must degrade to "unknown" rather than to a crash. */
export type HardwareNodeLike = {
  gateway_id?: string | null;
  id?: string | null;
  display_name?: string | null;
  platform?: string | null;
  status?: string | null;
  connection_status?: string | null;
  hardware_kind?: string | null;
  hardware_label?: string | null;
};

export function hardwareNodeId(node: HardwareNodeLike): string {
  return String(node.gateway_id || node.id || "").trim();
}

/** `display_name` FIRST — the same order `gatewayLabel` (gateway-box-picker.tsx)
 *  and every inline label in HardwareSection.tsx already use, and the ONLY
 *  order that can ever show a real machine name. `hardware_label` is not an
 *  owner-chosen name; it is SERVER-COMPUTED (gateway_registry_service.
 *  _hardware_presentation) purely from `hardware_kind` — a cloud VPS gets
 *  "{Provider} · {Region}", and EVERY personal computer that isn't a cloud
 *  VPS gets the identical literal constant "This Device", regardless of its
 *  real hostname. This function used to check `hardware_label` FIRST, so two
 *  paired computers — each with a real, distinct `display_name` the gateway
 *  reports at pairing (empyralis-gateway/src/config.ts's `displayName`,
 *  which defaults to `os.hostname()`) — both rendered as the identical,
 *  meaningless "This Device" in the create wizard's Placement step. The
 *  founder's own report: two machines, one online, one offline, both
 *  labelled "This Device", in the ONE control that decides which computer an
 *  agent gets a shell on. `display_name` was present in the same wire
 *  payload the whole time (`/api/gateway/registrations`) — it was simply
 *  outranked by a hardcoded backend fallback. See `dedupeHardwareNodeLabels`
 *  below for what still happens when two nodes share even their real name
 *  (or both genuinely have none). */
export function hardwareNodeLabel(node: HardwareNodeLike): string {
  return (
    String(node.display_name || "").trim() ||
    String(node.hardware_label || "").trim() ||
    String(node.platform || "").trim() ||
    hardwareNodeId(node) ||
    "Computer"
  );
}

export function hardwareNodeOnline(node: HardwareNodeLike): boolean {
  return `${node.connection_status || ""} ${node.status || ""}`.toLowerCase().includes("online");
}

/** The label fix above still cannot save two nodes that collide for real —
 *  the same hostname paired twice, or two registrations that both genuinely
 *  carry no `display_name` and fall all the way to the shared "This Device"/
 *  platform fallback. Two entries reading identically is exactly the defect
 *  reported (the control decides which machine an agent gets a SHELL on), so
 *  every label handed to a list of nodes goes through this function rather
 *  than `hardwareNodeLabel` directly. Disambiguation is always something
 *  REAL — the platform, when it tells the colliding nodes apart, else a
 *  short slice of the node's own id — NEVER a bare ordinal ("This Device
 *  (2)"), which would imply an order that means nothing and cannot be
 *  reproduced by the person reading it next time. */
export function dedupeHardwareNodeLabels(nodes: HardwareNodeLike[]): Map<string, string> {
  const entries = (nodes || []).map((n) => ({ id: hardwareNodeId(n), label: hardwareNodeLabel(n), node: n }));
  const groups = new Map<string, typeof entries>();
  for (const entry of entries) {
    const list = groups.get(entry.label);
    if (list) list.push(entry);
    else groups.set(entry.label, [entry]);
  }
  const out = new Map<string, string>();
  for (const group of groups.values()) {
    if (group.length < 2) {
      out.set(group[0].id, group[0].label);
      continue;
    }
    // Try the platform first — a real, meaningful fact ("darwin" vs
    // "linux") when it actually differs across every colliding node.
    const platforms = group.map((e) => String(e.node.platform || "").trim());
    const platformDistinguishes = platforms.every(Boolean) && new Set(platforms).size === group.length;
    for (let i = 0; i < group.length; i++) {
      const entry = group[i];
      if (platformDistinguishes) {
        out.set(entry.id, `${entry.label} (${platforms[i]})`);
        continue;
      }
      // Fall back to a short slice of the node's own id — real, stable, and
      // unique by construction (it is the id the server already assigned).
      const suffix = entry.id.slice(-6);
      out.set(entry.id, suffix ? `${entry.label} · ${suffix}` : entry.label);
    }
  }
  return out;
}

/** VPS vs. everything else, by the SAME `hardware_kind` discriminator the
 *  Hardware page partitions on. An unknown/absent kind counts as a paired
 *  computer rather than being dropped: a node nobody can select is a node
 *  the customer paired and cannot use. */
export function partitionHardwareNodes(nodes: HardwareNodeLike[]): {
  vps: HardwareNodeLike[];
  gateway: HardwareNodeLike[];
} {
  const vps: HardwareNodeLike[] = [];
  const gateway: HardwareNodeLike[] = [];
  for (const node of nodes || []) {
    if (String(node.hardware_kind || "").trim() === "cloud_vps") vps.push(node);
    else gateway.push(node);
  }
  return { vps, gateway };
}

/** Which partition a placement picks from. `cloud` picks from neither. */
export function nodesForPlacement(
  placement: AgentCreatePlacement,
  nodes: HardwareNodeLike[],
): HardwareNodeLike[] {
  if (placement === "cloud") return [];
  const split = partitionHardwareNodes(nodes);
  return placement === "vps" ? split.vps : split.gateway;
}

export function placementNeedsNode(placement: AgentCreatePlacement): boolean {
  return placement !== "cloud";
}

export type AgentCreatePlacementPlan = {
  /** Whether step 1 may move forward on this placement. */
  ready: boolean;
  /** One FACT explaining a block, or "" when there is none. Never rendered
   *  while the node list is still loading — "you have no computers" and "I
   *  have not looked yet" are different facts (CLAUDE.md). */
  blockedReason: string;
  /** The two columns, written together or not at all. Null when this
   *  placement changes nothing from the server's own preset default, so the
   *  common case costs no extra call. */
  patch: { hardware_access: AgentCreatePlacement; preferred_gateway_id: string } | null;
};

export function planAgentCreatePlacement(state: {
  placement: AgentCreatePlacement;
  nodeId: string;
  /** False while the registrations fetch is in flight. */
  nodesKnown: boolean;
  /** How many nodes of the RIGHT kind exist for this placement. */
  availableNodeCount: number;
}): AgentCreatePlacementPlan {
  const { placement, nodeId, nodesKnown, availableNodeCount } = state;

  if (placement === "cloud") {
    // The server's "standard" capability preset already resolves
    // hardware_access to "none" (fleet_tools.fleet_create_agent), so the
    // common path needs no placement write at all — never a patch that
    // re-asserts a value nobody changed.
    return { ready: true, blockedReason: "", patch: null };
  }

  const chosen = String(nodeId || "").trim();
  if (!chosen) {
    return {
      ready: false,
      blockedReason: !nodesKnown
        ? ""
        : availableNodeCount === 0
          ? placement === "vps"
            ? "No cloud servers connected yet."
            : "No computers paired yet."
          : placement === "vps"
            ? "Pick which server this agent runs on."
            : "Pick which computer this agent runs on.",
      patch: null,
    };
  }

  return {
    ready: true,
    blockedReason: "",
    patch: { hardware_access: placement, preferred_gateway_id: chosen },
  };
}
