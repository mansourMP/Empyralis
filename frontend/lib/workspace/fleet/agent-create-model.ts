/**
 * WHICH MODELS A BRAND-NEW AGENT MAY BE CREATED WITH — pure, framework-free,
 * so a plain `tsx` test drives the real rule (same discipline as
 * agent-count-shape.ts / channel-doors.ts / codex-reasoning-options.ts).
 *
 * The founder, 2026-08-21, on the creation surface: *"it must be something
 * that is real... **I should be able to pick what model I am going to use**
 * and others."* Creation stays LIGHT — name, model, optional prompt — so the
 * question this module answers is narrow and it is not "what models exist":
 *
 *   WHICH MODELS CAN THIS WORKSPACE ACTUALLY CHOOSE, RIGHT NOW,
 *   WITH NOTHING ELSE SET UP FIRST?
 *
 * ```
 * ALWAYS       Flash · Pro        platform credits. No key, no hardware,
 *                                 nothing to configure. DERIVED from
 *                                 fleet-model-config's PLATFORM_CREDITS_*
 *                                 constants, never re-typed here.
 * IF SAVED     <provider>'s own   one group per BYOK provider this workspace
 *              live model ids     already holds a credential for, and only
 *                                 the ids that provider's OWN API answered
 *                                 with (GET /api/providers/{id}/models).
 * NEVER        anything needing   an API-key field, a provider with no saved
 *              a step first       credential, cli_subscription / local (both
 *                                 need a paired computer). Those live on the
 *                                 agent's own Model tab, one click later.
 * ```
 *
 * Two rules this file exists to keep.
 *
 * **A model id is never transcribed here.** CLAUDE.md: "derive the
 * capability set from the thing that owns it, never transcribe it." The
 * platform tiers come from fleet-model-config.ts (which mirrors
 * provider_profiles.py's own deepseek catalogue and is the single source the
 * Model tab already renders from); the BYOK ids come from a live call
 * against the customer's own credential. There is no hand-typed model list
 * in this module, and adding one would be the bug.
 *
 * **A provider whose live list could not be read contributes NOTHING**, and
 * that is a deliberate divergence from ModelTab's own fallback. ModelTab has
 * an existing agent whose saved model must stay visible even when discovery
 * fails, so it falls back to the static catalog with an honest "may be
 * stale" note. A brand-new agent has no saved model to preserve, so the same
 * fallback would only ever put an UNVERIFIED id in front of someone at the
 * exact moment they cannot tell — while Flash/Pro, which are never affected,
 * are sitting right there. Silence is the honest answer; Configure is one
 * click away.
 */

import {
  PLATFORM_CREDITS_MODEL_BY_TIER,
  PLATFORM_CREDITS_PROVIDER,
  PLATFORM_CREDITS_TIER_OPTIONS,
} from "./fleet-model-config";

/** The only two modes a creation-time pick can carry. cli_subscription and
 *  local are absent BY CONSTRUCTION, not by omission — both require a paired
 *  computer, which a workspace creating its first agent does not have, and a
 *  control that cannot be completed is a dead control. */
export type AgentCreateModelMode = "platform_credits" | "byok_api";

export type AgentCreateModelChoice = {
  /** Stable `<select>` value — `${mode}:${provider}:${model}`. Composite
   *  because a model id alone is not unique across providers (the same id
   *  can be reachable both on platform credits and on a customer's own
   *  key). */
  id: string;
  /** What the option reads as. */
  label: string;
  /** The second half of the option line — what it actually is. Never
   *  hidden behind a tooltip; a model pick is a spend decision. */
  detail: string;
  /** The optgroup this belongs under. */
  group: string;
  mode: AgentCreateModelMode;
  provider: string;
  model: string;
};

/** One BYOK provider that already has a usable credential in this
 *  workspace, plus the model ids its own API answered with. */
export type AgentCreateByokProvider = {
  provider: string;
  label: string;
  models: string[];
};

export const AGENT_CREATE_INCLUDED_GROUP = "Included with your plan";

export function agentCreateModelChoiceId(mode: string, provider: string, model: string): string {
  return `${mode}:${provider}:${model}`;
}

/** The always-available pair, derived from the same PLATFORM_CREDITS_*
 *  constants the Model tab's own tier picker renders from — so a change to
 *  the platform's tier vocabulary reaches this surface with no edit here. */
export function platformCreditModelChoices(): AgentCreateModelChoice[] {
  return PLATFORM_CREDITS_TIER_OPTIONS.map((opt) => {
    const model = PLATFORM_CREDITS_MODEL_BY_TIER[opt.tier];
    return {
      id: agentCreateModelChoiceId("platform_credits", PLATFORM_CREDITS_PROVIDER, model),
      label: opt.label,
      detail: opt.subtitle,
      group: AGENT_CREATE_INCLUDED_GROUP,
      mode: "platform_credits" as const,
      provider: PLATFORM_CREDITS_PROVIDER,
      model,
    };
  });
}

/** One group per credentialed provider. A provider carrying no models
 *  (nothing saved, or a live call that could not answer) contributes no
 *  group at all rather than an empty one — see this file's header. */
export function byokModelChoices(providers: AgentCreateByokProvider[]): AgentCreateModelChoice[] {
  const out: AgentCreateModelChoice[] = [];
  for (const p of providers) {
    const provider = String(p.provider || "").trim();
    if (!provider) continue;
    const group = String(p.label || "").trim() || provider;
    for (const raw of p.models || []) {
      const model = String(raw || "").trim();
      if (!model) continue;
      const id = agentCreateModelChoiceId("byok_api", provider, model);
      if (out.some((c) => c.id === id)) continue;
      out.push({ id, label: model, detail: `Your own ${group} key`, group, mode: "byok_api", provider, model });
    }
  }
  return out;
}

/** The whole picker, in render order: the included pair first (it is what
 *  almost every new agent will use), then any credentialed providers. */
export function buildAgentCreateModelChoices(byok: AgentCreateByokProvider[] = []): AgentCreateModelChoice[] {
  return [...platformCreditModelChoices(), ...byokModelChoices(byok)];
}

/**
 * The pick the card opens on, and it is NOT an invented "sensible default":
 * it is the exact model_config the server already stamps on every new agent
 * (fleet_tools.seed_specialist_metadata → {"mode": "platform_credits",
 * "model": "deepseek-v4-pro"}). Pre-selecting anything else would make the
 * picker lie about what happens if nobody touches it.
 *
 * Held to that server value by a real two-source drift test —
 * server_modules/tests/test_agent_create_model_choice.py reads THIS FILE off
 * disk and compares the id below against seed_specialist_metadata()'s own
 * return value. Cross-language, so neither side can be edited alone.
 */
export const AGENT_CREATE_DEFAULT_MODEL_ID = agentCreateModelChoiceId(
  "platform_credits",
  PLATFORM_CREDITS_PROVIDER,
  PLATFORM_CREDITS_MODEL_BY_TIER.pro,
);

export function findAgentCreateModelChoice(
  choices: AgentCreateModelChoice[],
  id: string,
): AgentCreateModelChoice | null {
  return choices.find((c) => c.id === id) || null;
}

/** Which id the `<select>` should actually be showing. A requested id that
 *  is no longer in the list (the live catalog resolved and dropped it, or a
 *  provider's credential went away mid-open) falls back to the default, then
 *  to whatever is first — never to "" , which renders as a blank select the
 *  person has to notice is blank. */
export function resolveAgentCreateModelId(choices: AgentCreateModelChoice[], requested: string): string {
  if (choices.length === 0) return "";
  if (requested && choices.some((c) => c.id === requested)) return requested;
  if (choices.some((c) => c.id === AGENT_CREATE_DEFAULT_MODEL_ID)) return AGENT_CREATE_DEFAULT_MODEL_ID;
  return choices[0].id;
}

/** The `model_choice` object POST /fleet/agents accepts. Exactly three keys,
 *  matching fleet_tools._CREATE_TIME_MODEL_CHOICE_KEYS — the create path
 *  deliberately accepts a NARROWER model_config than PATCH does (no gateway
 *  binding, no runtime, no engine, no reasoning effort), so nothing that
 *  needs a paired computer can arrive through a surface that never asked
 *  about one. */
export function agentCreateModelChoicePayload(
  choice: AgentCreateModelChoice | null,
): { mode: string; provider: string; model: string } | null {
  if (!choice) return null;
  return { mode: choice.mode, provider: choice.provider, model: choice.model };
}

/** Choices grouped for `<optgroup>`, preserving first-seen group order. */
export function groupAgentCreateModelChoices(
  choices: AgentCreateModelChoice[],
): { group: string; choices: AgentCreateModelChoice[] }[] {
  const groups: { group: string; choices: AgentCreateModelChoice[] }[] = [];
  for (const choice of choices) {
    const existing = groups.find((g) => g.group === choice.group);
    if (existing) existing.choices.push(choice);
    else groups.push({ group: choice.group, choices: [choice] });
  }
  return groups;
}
