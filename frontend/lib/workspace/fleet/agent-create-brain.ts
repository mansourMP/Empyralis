/**
 * WHO PAYS FOR THIS AGENT'S MODEL, AND WHAT RUNS IT — pure, framework-free,
 * so a plain `tsx` test drives the real rule (same discipline as
 * agent-create-placement.ts / channel-doors.ts / agent-create-model.ts).
 *
 * The founder measured the shipped creation card against the wizard that had
 * been deleted and found the whole question gone: one Model `<select>`, no
 * mention anywhere of who the tokens are billed to. This module is that
 * question, put back as one rule instead of four branches in a component.
 *
 * ```
 *              placement (step 1)          modes offered (step 2)
 *   cloud      no machine                  Platform credits · Your own API key
 *   vps        a server                    …plus Your subscription · Run locally
 *   gateway    a paired computer           …plus Your subscription · Run locally
 * ```
 *
 * **The gate is the PLACEMENT, never a name.** Both of the extra two route
 * the brain through a Gateway on a real machine, so on a cloud-only agent
 * they are controls that cannot be completed. They are not rendered disabled
 * and they are not rendered with an excuse: they are not rendered (CLAUDE.md,
 * "if a control cannot be used in the current state, it is not rendered"),
 * and step 2 says once, quietly, where to go to unlock them.
 *
 * ── WHERE EACH MODE'S CONFIG IS WRITTEN, and why they differ ─────────────
 *
 * ```
 * platform_credits   ─┐  POST /fleet/agents  model_choice {mode,provider,model}
 * byok_api           ─┘  ATOMIC with the create. Nothing to patch.
 *
 * cli_subscription   ─┐  POST (server seed) ─▶ PATCH model_config
 * local              ─┘  the create path accepts exactly three keys
 *                        (fleet_tools._CREATE_TIME_MODEL_CHOICE_KEYS) and
 *                        neither of these fits: both need gateway_binding
 *                        and runtime, and both need the paired box to be
 *                        checked as real+authenticated — validation that
 *                        lives in fleet_configure_agent and is REUSED here
 *                        rather than copied into a second, thinner one.
 * ```
 *
 * That second shape is a create followed by a step that can independently
 * fail, which CLAUDE.md names as a recurring defect — so the requirement it
 * carries is exact: the two outcomes must never share one message. The agent
 * EXISTS the moment the POST returns; a PATCH that then fails is reported as
 * its own fact ("created — but its brain could not be set"), never as
 * "couldn't create the agent", and never silently.
 *
 * ── A pasted key is a PREREQUISITE, not a follow-up ──────────────────────
 * `byok_api` with a provider this workspace has no credential for needs the
 * vault credential + provider profile saved BEFORE the agent is created. If
 * that fails, no agent exists yet and nothing has to be explained away. The
 * model list in that state is the static per-provider catalog on purpose:
 * with no saved credential there is no live list to ask for, and the static
 * catalog is EXACTLY what the server validates the pick against
 * (provider_profiles.model_is_known_for_provider), so the two cannot
 * disagree.
 */

import type { AgentCreatePlacement } from "./agent-create-placement";
import { runtimeForProvider } from "./fleet-provider-constants";

export type AgentCreateBrainMode = "platform" | "byok" | "subscription" | "local";

export type AgentCreateBrainOption = {
  id: AgentCreateBrainMode;
  label: string;
  body: string;
  /** True when this mode's brain runs on the placed machine — the axis the
   *  placement gate is derived from, so no call site names a mode. */
  needsMachine: boolean;
};

export const AGENT_CREATE_BRAIN_OPTIONS: readonly AgentCreateBrainOption[] = [
  {
    id: "platform",
    label: "Platform credits",
    body: "Billed to your plan. Nothing to set up.",
    needsMachine: false,
  },
  {
    id: "byok",
    label: "Your own API key",
    body: "Your key, your provider. You pay them directly.",
    needsMachine: false,
  },
  {
    id: "subscription",
    label: "Your subscription",
    body: "Claude Code, Codex, Grok or Cursor, signed in on that machine.",
    needsMachine: true,
  },
  {
    id: "local",
    label: "Run locally",
    body: "Ollama on that machine. Nothing leaves it.",
    needsMachine: true,
  },
];

export const AGENT_CREATE_DEFAULT_BRAIN_MODE: AgentCreateBrainMode = "platform";

/** DERIVED from the placement and the option's own `needsMachine`, never a
 *  hand-listed pair of modes — a fifth mode added above is gated correctly
 *  with no edit here. */
export function agentCreateBrainOptionsFor(
  placement: AgentCreatePlacement,
): AgentCreateBrainOption[] {
  const hasMachine = placement !== "cloud";
  return AGENT_CREATE_BRAIN_OPTIONS.filter((o) => hasMachine || !o.needsMachine);
}

export function agentCreateBrainModeAvailable(
  mode: AgentCreateBrainMode,
  placement: AgentCreatePlacement,
): boolean {
  return agentCreateBrainOptionsFor(placement).some((o) => o.id === mode);
}

/** The one line step 2 shows a cloud-only agent, pointing back at the
 *  decision that would unlock the missing two. Empty on any placement where
 *  nothing is missing — a note about an absence nobody is experiencing is
 *  the lecture the craft doctrine bans. */
export function agentCreateBrainPlacementNote(placement: AgentCreatePlacement): string {
  if (placement !== "cloud") return "";
  return "Your own subscription or a local model need a computer — pick one in step 1.";
}

/** What the create call and the follow-up patch each carry. `modelChoice`
 *  rides the POST; `modelConfigPatch` is the PATCH. Exactly one is ever
 *  non-null — see this file's header for why the two modes split. */
export type AgentCreateBrainPlan = {
  ready: boolean;
  blockedReason: string;
  modelChoice: { mode: string; provider: string; model: string } | null;
  modelConfigPatch: Record<string, string> | null;
  /** True when a vault credential + provider profile must be created before
   *  the agent is. */
  savesApiKey: boolean;
};

export type AgentCreateBrainState = {
  placement: AgentCreatePlacement;
  mode: AgentCreateBrainMode;
  /** The picked provider for the ACTIVE mode. */
  provider: string;
  /** The picked model id. May legitimately be "" for a CLI subscription
   *  whose own default should win. */
  model: string;
  /** The pasted key, when the byok provider has no saved credential. */
  apiKey: string;
  /** Whether the byok provider already has a usable credential in this
   *  workspace (agent-create-model-catalog's live sweep answered for it). */
  providerHasCredential: boolean;
  /** The machine chosen in step 1. Both machine-bound modes bind to it. */
  nodeId: string;
};

export function planAgentCreateBrain(state: AgentCreateBrainState): AgentCreateBrainPlan {
  const { placement, mode, nodeId } = state;
  const provider = String(state.provider || "").trim().toLowerCase();
  const model = String(state.model || "").trim();
  const apiKey = String(state.apiKey || "").trim();
  const boundNode = String(nodeId || "").trim();

  const blocked = (reason: string): AgentCreateBrainPlan => ({
    ready: false,
    blockedReason: reason,
    modelChoice: null,
    modelConfigPatch: null,
    savesApiKey: false,
  });

  // A mode the current placement does not offer can still be HELD in state
  // (pick "Run locally", walk back to step 1, choose Cloud only). Refusing
  // it here rather than silently reinterpreting it means the person is told
  // their pick no longer applies instead of quietly getting a different one.
  if (!agentCreateBrainModeAvailable(mode, placement)) {
    return blocked("That needs a computer — pick one in step 1, or choose another option.");
  }

  if (mode === "platform") {
    if (!provider || !model) return blocked("Pick a model.");
    return {
      ready: true,
      blockedReason: "",
      modelChoice: { mode: "platform_credits", provider, model },
      modelConfigPatch: null,
      savesApiKey: false,
    };
  }

  if (mode === "byok") {
    if (!provider) return blocked("Pick a provider.");
    if (!state.providerHasCredential && !apiKey) return blocked("Paste your API key.");
    if (!model) return blocked("Pick a model.");
    return {
      ready: true,
      blockedReason: "",
      modelChoice: { mode: "byok_api", provider, model },
      modelConfigPatch: null,
      savesApiKey: !state.providerHasCredential,
    };
  }

  if (!boundNode) {
    // Unreachable through the UI (step 1 already refuses a machine-bound
    // placement with no node) — kept because this function is the ONE place
    // that decides, and a binding-less machine mode must never be emitted
    // even if some future caller forgets.
    return blocked("Pick a computer in step 1 first.");
  }

  if (mode === "subscription") {
    if (!provider) return blocked("Pick which subscription to use.");
    return {
      ready: true,
      blockedReason: "",
      modelChoice: null,
      modelConfigPatch: {
        mode: "cli_subscription",
        provider,
        runtime: runtimeForProvider(provider),
        gateway_binding: boundNode,
        // Deliberately included even when blank: fleet_configure_agent
        // replaces model_config WHOLE, and an omitted key would read as
        // "keep the seed's model", which for a CLI is a provider id the CLI
        // has never heard of. Blank means "the CLI's own default".
        model,
      },
      savesApiKey: false,
    };
  }

  // local
  if (!model) return blocked("Pick which local model to run.");
  return {
    ready: true,
    blockedReason: "",
    modelChoice: null,
    modelConfigPatch: {
      mode: "local",
      provider: provider || "ollama",
      runtime: "ollama",
      gateway_binding: boundNode,
      model,
    },
    savesApiKey: false,
  };
}
