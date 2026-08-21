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
 * THIRD PASS, 2026-08-21 — the founder kept the one-screen shape and asked
 * for the model back by name (*"I should be able to pick what model I am
 * going to use"*). That lives in agent-create-model.ts, not here.
 *
 * This module also SHRANK in that pass: it used to export a second payload
 * builder, buildAgentCreatePayload, alongside agent-quick-create.ts's
 * buildQuickCreateAgentPayload. It had ZERO production callers — its only
 * caller was its own test — i.e. CLAUDE.md's single most-documented defect
 * shape ("built, tested, and never wired") sitting in the create path, where
 * the next author would reasonably have reached for it and quietly diverged
 * from what the live request actually sends. Deleted rather than taught
 * about the model pick: there is ONE payload builder for agent creation, and
 * it is the one the network call uses.
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
