/**
 * Pure defaulting + planning logic for AgentCreateCard.tsx.
 *
 * FIRST PASS (2026-08-20) had this card show Name/Model/Hardware/Project,
 * pre-filled and editable — the founder's correction to zero-field instant
 * create (agent-quick-create.ts, 2026-08-19): *"when I said 'as easy as
 * possible to create agents' it doesn't mean the moment you press plus
 * everything is going to be created... What about the name?"*
 *
 * SECOND CORRECTION, same day, before the first pass shipped: the founder
 * settled a bigger positioning question that changes what belongs on this
 * card. *"messaging would never be done inside this platform. I'm strictly
 * going to prohibit that."* The web UI is for creating and configuring
 * agents and observing what they do — real conversation happens on a
 * channel (Telegram/WhatsApp/iMessage), never here. Model, hardware and
 * memory are CONFIGURATION, seen and changed on the agent's own page after
 * it exists — not questions asked at creation. And project scoping is no
 * longer a decision either: *"project and agents are completely
 * independent... let's get rid of that entirely."*
 *
 * So the card is now genuinely small: *"while I'm creating this specific
 * agent all I should do is just probably some steps — probably its name
 * and possibly some system prompt or something like this... Like the name
 * could be 'YouTube content creation agent'."* Name (pre-filled, editable)
 * plus an optional freeform system prompt. Nothing else. A project id
 * still has to reach the create request (the backend field is required
 * today), but it is resolved SILENTLY — never rendered, never a choice —
 * exactly the way agent-quick-create.ts's createAgentQuickly already
 * resolves it via resolveQuickCreateProjectId.
 *
 * Kept in its own module, framework-free, so a plain node/tsx test can
 * assert the rules without mounting React — same discipline as
 * agent-count-shape.ts / channel-doors.ts (CLAUDE.md: "a check that
 * derives its own expectations from the thing it checks is blind").
 */

/** The name the card shows. Whatever the person has typed always wins,
 *  even mid-edit; only once nothing has been typed does the server's own
 *  suggested-name pool (fleet_tools.suggest_agent_name, GET .../fleet/
 *  agents/suggested-name) fill the field — pre-filled AND editable, never
 *  a placeholder ghost the person has to first notice and then overwrite. */
export function resolveAgentCreateName(typed: string, suggested: string): string {
  const clean = typed.trim();
  return clean || suggested.trim();
}

export type AgentCreatePayload = {
  name: string;
  instructions: string;
  capability_preset: string;
  project_id: string;
  purpose_preset: string;
  audience: string;
};

/** The POST /fleet/agents body. `instructions` is the card's own optional
 *  system-prompt field, passed through verbatim (untrimmed content is
 *  preserved; only leading/trailing whitespace is trimmed) — an empty
 *  string is a legitimate, common choice and the server already treats it
 *  as "no instructions given" (fleet_create_agent falls back to a
 *  purpose-preset default in that case). Reuses the exact fixed defaults
 *  agent-quick-create.ts's buildQuickCreateAgentPayload already
 *  established for capability_preset/purpose_preset/audience — this
 *  function exists so callers building the request from this card's own
 *  resolved name/prompt/project don't have to hand-assemble the rest. */
export function buildAgentCreatePayload(opts: {
  name: string;
  instructions: string;
  projectId: string;
}): AgentCreatePayload {
  return {
    name: opts.name.trim(),
    instructions: opts.instructions.trim(),
    capability_preset: "standard",
    project_id: opts.projectId,
    purpose_preset: "internal_assistant",
    audience: "owner",
  };
}
