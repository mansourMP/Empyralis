/**
 * WHAT A NEW AGENT IS FOR — pure, framework-free, so a plain `tsx` test drives
 * the real rule (same discipline as agent-create-placement.ts /
 * agent-create-brain.ts / channel-doors.ts).
 *
 * ── The shaping system already existed. The UI threw it away ─────────────
 * Founder, 2026-08-28: *"reasoning is a commodity… I want this specific agent
 * to be great at specific work, so we could say this is the agent you need for
 * financial things — not because of the AI model, but because of tools and
 * others. People don't know what they need until you tell them."*
 *
 * Two backend preset axes have shipped, are validated, and were reachable from
 * no screen — `buildQuickCreateAgentPayload` typed both as literals:
 *
 * ```
 *   purpose_preset      fleet_tools.py        seeds instructions, DERIVES audience
 *     customer_facing   ─▶ audience "external"
 *     internal_assistant─▶ audience "owner"        <-- was hardcoded, forever
 *     operator          ─▶ the workspace's OWN agent. never offered here.
 *
 *   capability_preset   capability_presets.py  seeds hardware/model/context
 *     standard          ─▶ the normal baseline    <-- was hardcoded, forever
 *     knowledge         ─▶ hardware "none", POLICY-LOCKED
 *     operator          ─▶ reserved, `is_creatable` refuses it
 * ```
 *
 * A JOB is those two presets plus the words. Nothing here is a new backend
 * concept: every id below is a literal the two Python modules already accept,
 * and `agent-create-job.test.ts` reads both files to prove it rather than
 * trusting this comment.
 *
 * ── SIX JOBS, NOT A MARKETPLACE ──────────────────────────────────────────
 * "Best, not most." A list long enough to need scanning is a list that has
 * stopped telling anyone what they need — which is the whole reason the
 * picker exists. Jobs share a preset pair where the honest answer is that
 * they share one; what differs between them is the PROSE, and the prose is
 * not decoration — `purpose_preset` seeds instructions all by itself, so a
 * job that seeds better ones is doing that system's own job better.
 *
 * ── WHY THE PICKER SITS BELOW PLACEMENT ──────────────────────────────────
 * `knowledge` policy-locks hardware to "none", and `fleet_configure_agent`
 * REFUSES a hardware patch on such an install ("knowledge_agent_hardware_
 * locked", fleet_tools.py). So on a machine placement a knowledge job commits
 * an agent and then fails the placement PATCH — "created, but where it runs
 * couldn't be saved". That is a dead control that bills you for the privilege.
 *
 * It is not fixed by hiding the job somewhere clever. It is fixed by ASKING IN
 * THE ORDER THE DEPENDENCY RUNS, which is the identical argument that already
 * put placement first in the sequence:
 *
 * ```
 *   name             what it is called
 *      │
 *   where it runs    ─▶ decides which jobs can honestly be offered
 *      │
 *   what it is for   ─▶ decides the two presets, and seeds the words
 *      │
 *   what it does     the words, editable
 * ```
 *
 * A picker whose options can be invalidated by a control BELOW it is the
 * dishonesty the placement-first rule exists to prevent; putting the job
 * above placement would recreate it one screen down. `agentCreateJobsForPlacement`
 * is the gate, and it derives from the capability preset rather than a
 * hand-set per-job flag — so a seventh job built on `knowledge` is covered on
 * the day it is written, not on the day someone remembers.
 *
 * ── THE SEED NEVER OVERWRITES A PERSON'S OWN WORDS ───────────────────────
 * `planAgentCreateJobInstructions` fills the field only while it is still
 * PRISTINE — empty, or holding a previous job's seed verbatim. One character
 * typed and the field is theirs; changing jobs after that changes the presets
 * and leaves the prose alone. There is no "restore" affordance and no warning
 * line: silently keeping what someone typed needs neither.
 */

/** Every capability preset a normal create may request —
 *  `capability_presets.CREATABLE_CAPABILITY_PRESETS`. "operator" is reserved
 *  (Sage-class) and `is_creatable` refuses it, so it is absent here. */
export type AgentCreateCapabilityPreset = "standard" | "knowledge";

/** The purpose presets this surface offers — `fleet_tools._VALID_PURPOSE_PRESETS`
 *  minus "operator", which names the workspace's OWN coordinating agent and is
 *  not a job a person creates a specialist for. */
export type AgentCreatePurposePreset = "internal_assistant" | "customer_facing";

export type AgentCreateJobId =
  | "general"
  | "support"
  | "sales"
  | "bookkeeping"
  | "research"
  | "operations";

export type AgentCreateJob = {
  id: AgentCreateJobId;
  /** The card's first fact. A job title, never an instruction. */
  label: string;
  /** The card's second fact, and there is no third — the same two-facts-and-
   *  refuses-a-third discipline agent-card-face.ts holds the Agents grid to.
   *  One line, what the agent does, in the words of someone who has not met
   *  the product. */
  body: string;
  purposePreset: AgentCreatePurposePreset;
  capabilityPreset: AgentCreateCapabilityPreset;
  /** Pre-fills "What this agent does" — and, because the create path only
   *  falls back to `_PURPOSE_PRESET_INSTRUCTIONS` when instructions are
   *  blank, this is what actually becomes the agent's instructions.
   *
   *  EMPTY for "general" on purpose: an empty field lets the server seed its
   *  own `internal_assistant` line, which is byte-for-byte what creating an
   *  agent did before this picker existed. */
  seedInstructions: string;
};

/**
 * The jobs, in the order they are rendered. General is first and is the
 * default: it asks nothing and changes nothing, so somebody who does not want
 * to answer this question has already answered it.
 *
 * `operator` appears as neither a purpose nor a capability preset here, and
 * both absences are deliberate rather than an oversight — see the type
 * declarations above.
 */
export const AGENT_CREATE_JOBS: readonly AgentCreateJob[] = [
  {
    id: "general",
    label: "General",
    body: "A capable assistant with nothing narrowed down.",
    purposePreset: "internal_assistant",
    capabilityPreset: "standard",
    seedInstructions: "",
  },
  {
    id: "support",
    label: "Customer support",
    body: "Answers your customers and handles what they ask for.",
    purposePreset: "customer_facing",
    capabilityPreset: "standard",
    seedInstructions:
      "You answer this business's customers directly. Be professional, accurate and " +
      "warm — a customer judges the business by how you speak to them. Answer from " +
      "what you actually know; when you do not know, say so and find out rather than " +
      "guessing. Keep a record of what each person asked for so nobody has to repeat " +
      "themselves.",
  },
  {
    id: "sales",
    label: "Sales and outreach",
    body: "Follows up with people who showed interest, and keeps deals moving.",
    purposePreset: "customer_facing",
    capabilityPreset: "standard",
    seedInstructions:
      "You talk to prospective customers on behalf of this business. Reply quickly, " +
      "answer the question that was actually asked, and be straight about price and " +
      "what the product does and does not do. Track where each conversation stands " +
      "and follow up on the ones that have gone quiet. Never promise something the " +
      "business has not agreed to.",
  },
  {
    id: "bookkeeping",
    label: "Bookkeeping",
    body: "Tracks invoices, expenses and what is still owed.",
    purposePreset: "internal_assistant",
    capabilityPreset: "standard",
    seedInstructions:
      "You keep this business's books. Record invoices, expenses and payments as they " +
      "come in, and keep a current picture of what is owed and what is overdue. " +
      "Numbers are either checked or flagged — never estimated quietly. When " +
      "something does not reconcile, say which two figures disagree and by how much " +
      "rather than picking one.",
  },
  {
    id: "research",
    label: "Research and answers",
    body: "Reads what you give it and answers from it. Never gets a shell.",
    purposePreset: "internal_assistant",
    // The one job that is not "standard", and the reason the picker is gated
    // on placement at all. `knowledge` policy-locks hardware to "none" — a
    // real, enforced guarantee, not a default, so it is worth stating on the
    // face as the second half of that one line.
    capabilityPreset: "knowledge",
    seedInstructions:
      "You read the documents and notes this team gives you and answer questions from " +
      "them. Cite which document an answer came from. When the material does not " +
      "cover something, say that plainly instead of filling the gap from general " +
      "knowledge — an answer nobody can trace is worse here than no answer.",
  },
  {
    id: "operations",
    label: "Operations",
    body: "Runs the routine work and keeps track of what is done.",
    purposePreset: "internal_assistant",
    capabilityPreset: "standard",
    seedInstructions:
      "You run this team's recurring work. Keep the tasks that repeat moving, notice " +
      "when something that should have happened has not, and raise it early rather " +
      "than at the deadline. Record what you did so the next person picking it up can " +
      "see the state without asking.",
  },
];

export const AGENT_CREATE_DEFAULT_JOB: AgentCreateJobId = "general";

const JOBS_BY_ID: ReadonlyMap<string, AgentCreateJob> = new Map(
  AGENT_CREATE_JOBS.map((j) => [j.id, j]),
);

/** Never throws and never returns undefined — an unrecognised id resolves to
 *  General, which is the "nothing narrowed down" answer and therefore the only
 *  safe thing an unknown value can mean. */
export function resolveAgentCreateJob(id: string): AgentCreateJob {
  return JOBS_BY_ID.get(id) ?? JOBS_BY_ID.get(AGENT_CREATE_DEFAULT_JOB)!;
}

/**
 * Whether this capability preset policy-locks hardware access to "none".
 *
 * The LOCK ITSELF is the backend's — `capability_presets.CAPABILITY_PRESETS
 * [<id>]["hardware_locked"]`, enforced by `fleet_configure_agent`, which
 * refuses any `hardware_access != "none"` patch on such an install. This is a
 * genuine cross-language duplicate of one fact (a browser cannot ask Python),
 * so it is stated ONCE here and `agent-create-job.test.ts` reads the Python
 * source to prove the two agree — the expected set and the actual set from
 * different places, never one file confirming itself.
 */
export function capabilityPresetLocksHardware(preset: AgentCreateCapabilityPreset): boolean {
  return preset === "knowledge";
}

/**
 * The jobs that can honestly be created on this placement.
 *
 * A hardware-locked job on a machine placement is not a slower path or a
 * warning — it is an agent that commits and then fails its own placement
 * PATCH. So it is not offered, which is what "no dead controls" means when
 * the control would have taken your money first.
 *
 * `needsMachine` rather than a placement id, so this module never learns the
 * placement vocabulary: agent-create-placement.ts owns which buckets need a
 * box, and a fourth bucket added there needs no edit here.
 */
export function agentCreateJobsForPlacement(needsMachine: boolean): readonly AgentCreateJob[] {
  if (!needsMachine) return AGENT_CREATE_JOBS;
  return AGENT_CREATE_JOBS.filter((j) => !capabilityPresetLocksHardware(j.capabilityPreset));
}

/** Whether this job survives the placement above it. */
export function agentCreateJobAllowed(id: string, needsMachine: boolean): boolean {
  return agentCreateJobsForPlacement(needsMachine).some((j) => j.id === resolveAgentCreateJob(id).id);
}

/**
 * The job that is actually selected, once the placement has had its say.
 *
 * A job the placement no longer allows falls back to General rather than
 * leaving a selection nobody can see — and General is the honest landing
 * spot, because it is the one job that narrows nothing. The person's typed
 * prose is NOT touched by this; see `planAgentCreateJobInstructions`.
 */
export function resolveSelectedAgentCreateJob(id: string, needsMachine: boolean): AgentCreateJobId {
  const job = resolveAgentCreateJob(id);
  return agentCreateJobAllowed(job.id, needsMachine) ? job.id : AGENT_CREATE_DEFAULT_JOB;
}

/** The two preset fields a job contributes to the create request. Named for
 *  the wire, not for this module, because they go straight onto the POST body
 *  and a rename here must be a rename there.
 *
 *  `audience` is deliberately ABSENT: `fleet_create_agent` derives it from
 *  `purpose_preset` via its own `_AUDIENCE_BY_PURPOSE_PRESET`, and copying
 *  that map into TypeScript would be a second opinion about one fact — the
 *  exact drift this codebase has been bitten by with channel lists four
 *  times. A customer-facing job gets `audience: "external"` because the
 *  server says so, not because this file remembered to. */
export type AgentCreateJobPresets = {
  purpose_preset: AgentCreatePurposePreset;
  capability_preset: AgentCreateCapabilityPreset;
};

export function agentCreateJobPresets(id: string): AgentCreateJobPresets {
  const job = resolveAgentCreateJob(id);
  return { purpose_preset: job.purposePreset, capability_preset: job.capabilityPreset };
}

/**
 * What "What this agent does" should hold after a job is picked.
 *
 * ```
 *   field is empty                      ─▶ the new job's seed
 *   field still holds a job seed        ─▶ the new job's seed  (swap)
 *   field holds anything else           ─▶ UNCHANGED. it is theirs.
 * ```
 *
 * "Still holds a job seed" is tested against EVERY job's seed, not just the
 * previously-selected one, so the rule survives a re-render that lost track of
 * which job filled it — the pristine check is about the TEXT, which is the
 * thing that can actually be observed, never about a flag that can drift out
 * of sync with it.
 *
 * Whitespace-only counts as empty: a field holding two spaces is not work
 * anybody would miss, and treating it as precious would leave the picker
 * looking broken for the rest of the session.
 */
export function planAgentCreateJobInstructions(current: string, nextJobId: string): string {
  const seed = resolveAgentCreateJob(nextJobId).seedInstructions;
  return instructionsArePristine(current) ? seed : current;
}

/** True while the field holds nothing a person would be sorry to lose —
 *  blank, or a seed this module itself put there. */
export function instructionsArePristine(current: string): boolean {
  const text = (current || "").trim();
  if (!text) return true;
  return AGENT_CREATE_JOBS.some((j) => j.seedInstructions.trim() === text);
}
