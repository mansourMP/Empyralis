/**
 * Drives the REAL job rules from agent-create-job.ts.
 * Run: npx tsx lib/workspace/fleet/agent-create-job.test.ts
 *
 * Half of this file is behavioural. The other half is the half that matters
 * more: a job is only a job because two PYTHON modules accept its two preset
 * ids, and nothing in TypeScript can fail when they stop. So the preset
 * assertions read `server_modules/fleet_tools.py` and
 * `server_modules/capability_presets.py` themselves — the expected set from
 * this module, the actual set from the source that enforces it, never one
 * file confirming itself (CLAUDE.md). Each scan carries a canary that fails
 * loudly if it ever stops reaching real content, because a regex that
 * silently matches nothing is a test that silently asserts nothing.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import {
  AGENT_CREATE_DEFAULT_JOB,
  AGENT_CREATE_JOBS,
  agentCreateJobAllowed,
  agentCreateJobPresets,
  agentCreateJobsForPlacement,
  capabilityPresetLocksHardware,
  instructionsArePristine,
  planAgentCreateJobInstructions,
  resolveAgentCreateJob,
  resolveSelectedAgentCreateJob,
} from "./agent-create-job";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) passed++;
  else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

const REPO_ROOT = path.resolve(__dirname, "../../../..");
function readRepoFile(rel: string): string {
  return readFileSync(path.join(REPO_ROOT, rel), "utf8");
}

// ── The shape of the list ─────────────────────────────────────────────────

assert(AGENT_CREATE_JOBS.length >= 5 && AGENT_CREATE_JOBS.length <= 6, "five or six jobs, not a marketplace");
assert(AGENT_CREATE_JOBS[0].id === "general", "General is first — the answer for somebody who does not want the question");
assert(AGENT_CREATE_DEFAULT_JOB === "general", "General is the default");
assert(
  new Set(AGENT_CREATE_JOBS.map((j) => j.id)).size === AGENT_CREATE_JOBS.length,
  "no duplicate job ids",
);

// TWO FACTS AND REFUSES A THIRD — the card face discipline agent-card-face.ts
// holds the Agents grid to, applied here. A body that has grown into a
// paragraph is a job card that has started selling itself.
assert(
  AGENT_CREATE_JOBS.every((j) => j.label.trim().length > 0 && j.body.trim().length > 0),
  "every job states a name and one line of what it does",
);
assert(
  AGENT_CREATE_JOBS.every((j) => j.body.length <= 80),
  "a job body stays one line — a professional tool labels, it does not lecture",
);
assert(
  AGENT_CREATE_JOBS.every((j) => !/\byou (should|must|need to)\b/i.test(j.body)),
  "a job body says what the agent does, never what the person should do",
);

// ── General is EXACTLY today's behaviour ──────────────────────────────────
// The regression that matters most: somebody who ignores this picker entirely
// must get the byte-for-byte agent they got before it existed. Both hardcoded
// literals are asserted by NAME here, so a change to either is a red test
// rather than a silent change to every default agent ever created.
const general = agentCreateJobPresets("general");
assert(general.purpose_preset === "internal_assistant", "General still posts purpose_preset internal_assistant");
assert(general.capability_preset === "standard", "General still posts capability_preset standard");
assert(
  resolveAgentCreateJob("general").seedInstructions === "",
  "General seeds NO instructions — a blank field is what lets the server apply its own internal_assistant line",
);

// ── The presets are real, per the Python that enforces them ───────────────

const FLEET_TOOLS_PY = readRepoFile("server_modules/fleet_tools.py");
const CAPABILITY_PRESETS_PY = readRepoFile("server_modules/capability_presets.py");

// canary: the scans must reach real content, or every assertion below is vacuous.
assert(FLEET_TOOLS_PY.includes("_VALID_PURPOSE_PRESETS"), "CANARY: fleet_tools.py was read and still declares _VALID_PURPOSE_PRESETS");
assert(CAPABILITY_PRESETS_PY.includes("CREATABLE_CAPABILITY_PRESETS"), "CANARY: capability_presets.py was read and still declares CREATABLE_CAPABILITY_PRESETS");

const validPurposeLine = /_VALID_PURPOSE_PRESETS\s*=\s*\{([^}]*)\}/.exec(FLEET_TOOLS_PY);
assert(Boolean(validPurposeLine), "CANARY: _VALID_PURPOSE_PRESETS is still a set literal this scan can parse");
const backendPurposePresets = new Set(
  (validPurposeLine?.[1] ?? "").split(",").map((s) => s.trim().replace(/^["']|["']$/g, "")).filter(Boolean),
);
assert(backendPurposePresets.size >= 3, "CANARY: parsed a real purpose-preset set, not an empty one");

for (const job of AGENT_CREATE_JOBS) {
  assert(
    backendPurposePresets.has(job.purposePreset),
    `job "${job.id}" posts a purpose_preset fleet_tools.py actually accepts (${job.purposePreset})`,
  );
}
// The one the picker must never offer: "operator" is the workspace's own
// coordinating agent, not a job somebody creates a specialist for.
assert(
  backendPurposePresets.has("operator") &&
    AGENT_CREATE_JOBS.every((j) => (j.purposePreset as string) !== "operator"),
  "the backend has an 'operator' purpose and no job offers it",
);

// Capability presets: creatable set + the hardware lock, both read from the
// module that owns them.
const creatableLine = /CREATABLE_CAPABILITY_PRESETS\s*=\s*\{([^}]*)\}/.exec(CAPABILITY_PRESETS_PY);
assert(Boolean(creatableLine), "CANARY: CREATABLE_CAPABILITY_PRESETS is still a set literal this scan can parse");
const creatableTokens = (creatableLine?.[1] ?? "").split(",").map((s) => s.trim()).filter(Boolean);
// The set is written with the module's own PRESET_* constants, so resolve each
// back to its string value rather than assuming the constant name is the id.
const backendCreatablePresets = new Set(
  creatableTokens.map((token) => {
    const m = new RegExp(`^${token}\\s*=\\s*["']([^"']+)["']`, "m").exec(CAPABILITY_PRESETS_PY);
    return m ? m[1] : token.replace(/^["']|["']$/g, "");
  }),
);
assert(backendCreatablePresets.size === 2, "CANARY: parsed both creatable capability presets");
assert(
  !backendCreatablePresets.has("operator"),
  "the reserved operator capability preset is still not creatable",
);

for (const job of AGENT_CREATE_JOBS) {
  assert(
    backendCreatablePresets.has(job.capabilityPreset),
    `job "${job.id}" posts a capability_preset the create flow accepts (${job.capabilityPreset})`,
  );
}

// THE CROSS-LANGUAGE FACT: which preset locks hardware. This module states it
// in TypeScript because a browser cannot ask Python; this asserts the two
// agree. If capability_presets.py ever flips `hardware_locked` on a preset,
// this fails here rather than shipping a job that commits an agent and then
// fails its own placement PATCH.
function backendPresetLocksHardware(presetId: string): boolean {
  // The block for one preset runs from its `"id": PRESET_<X>` line to the next
  // preset's, so slice between them and look for the lock inside.
  const constName = new RegExp(`^(PRESET_[A-Z_]+)\\s*=\\s*["']${presetId}["']`, "m").exec(CAPABILITY_PRESETS_PY);
  if (!constName) return false;
  const blockStart = CAPABILITY_PRESETS_PY.indexOf(`${constName[1]}: {`);
  if (blockStart < 0) return false;
  const rest = CAPABILITY_PRESETS_PY.slice(blockStart);
  const blockEnd = rest.indexOf("\n    },");
  const block = blockEnd > 0 ? rest.slice(0, blockEnd) : rest;
  return /"hardware_locked":\s*True/.test(block);
}
assert(backendPresetLocksHardware("knowledge"), "CANARY: the knowledge preset block is findable and still declares a lock");
for (const presetId of backendCreatablePresets) {
  assert(
    capabilityPresetLocksHardware(presetId as "standard" | "knowledge") === backendPresetLocksHardware(presetId),
    `capabilityPresetLocksHardware agrees with capability_presets.py for "${presetId}"`,
  );
}
// And the enforcement that makes the lock matter is still there — without this
// refusal the placement gate below would be protecting against nothing.
assert(
  /knowledge_agent_hardware_locked/.test(FLEET_TOOLS_PY),
  "fleet_configure_agent still refuses a hardware patch on a locked install",
);

// ── The placement gate ────────────────────────────────────────────────────

const cloudJobs = agentCreateJobsForPlacement(false);
const machineJobs = agentCreateJobsForPlacement(true);

assert(cloudJobs.length === AGENT_CREATE_JOBS.length, "a cloud placement offers every job");
assert(machineJobs.length < cloudJobs.length, "a machine placement offers fewer — the gate is not a no-op");
assert(
  machineJobs.every((j) => !capabilityPresetLocksHardware(j.capabilityPreset)),
  "no hardware-locked job survives a machine placement",
);
assert(
  cloudJobs.some((j) => capabilityPresetLocksHardware(j.capabilityPreset)),
  "the hardware-locked job IS offered on cloud — the gate narrows, it does not delete the job",
);
assert(machineJobs.some((j) => j.id === "general"), "General survives every placement");
assert(
  agentCreateJobAllowed("research", false) && !agentCreateJobAllowed("research", true),
  "the knowledge job is allowed on cloud and refused on a machine",
);

// The gate is DERIVED, not a per-job flag: every job that locks hardware is
// filtered, so a future seventh job built on `knowledge` is covered the day it
// is written. Asserted by property rather than by naming "research".
assert(
  AGENT_CREATE_JOBS.filter((j) => capabilityPresetLocksHardware(j.capabilityPreset)).every(
    (j) => !agentCreateJobAllowed(j.id, true),
  ),
  "EVERY hardware-locking job is gated, not just the one that exists today",
);

// ── Falling back never strands a selection ────────────────────────────────

assert(resolveSelectedAgentCreateJob("research", false) === "research", "a legal pick is kept");
assert(resolveSelectedAgentCreateJob("research", true) === "general", "an illegal pick falls back to General, never to nothing");
assert(resolveSelectedAgentCreateJob("support", true) === "support", "an unaffected pick survives a machine placement");
assert(resolveSelectedAgentCreateJob("nonsense-id", false) === "general", "an unknown id resolves to General");
assert(resolveAgentCreateJob("nonsense-id").id === "general", "resolve never throws and never returns undefined");

// ── The seed never overwrites a person's own words ────────────────────────

const supportSeed = resolveAgentCreateJob("support").seedInstructions;
const salesSeed = resolveAgentCreateJob("sales").seedInstructions;
assert(supportSeed.length > 0 && salesSeed.length > 0 && supportSeed !== salesSeed, "the seeds are real and distinct");

assert(planAgentCreateJobInstructions("", "support") === supportSeed, "an empty field takes the seed");
assert(planAgentCreateJobInstructions("   ", "support") === supportSeed, "whitespace-only counts as empty");
assert(
  planAgentCreateJobInstructions(supportSeed, "sales") === salesSeed,
  "a field still holding a previous seed swaps to the new one",
);
assert(
  planAgentCreateJobInstructions("Watch the warehouse inventory.", "support") === "Watch the warehouse inventory.",
  "TYPED WORDS ARE NEVER OVERWRITTEN — the whole point",
);
assert(
  planAgentCreateJobInstructions(`${supportSeed} And also watch stock.`, "sales") ===
    `${supportSeed} And also watch stock.`,
  "an EDITED seed is typed words too, and is kept",
);
assert(
  planAgentCreateJobInstructions("Watch the warehouse.", "general") === "Watch the warehouse.",
  "picking General does not wipe what somebody wrote",
);
assert(planAgentCreateJobInstructions(supportSeed, "general") === "", "a pristine field follows General back to blank");

assert(instructionsArePristine("") && instructionsArePristine(supportSeed), "pristine: blank and any job's own seed");
assert(!instructionsArePristine("anything else"), "pristine: not once a person has written something");
// Pristine is judged on the TEXT, not on a remembered job id — so it holds for
// a seed the caller has lost track of.
assert(
  AGENT_CREATE_JOBS.every((j) => instructionsArePristine(j.seedInstructions)),
  "EVERY job's seed reads as pristine, whichever job is currently selected",
);

// ── The surface actually calls all of this ────────────────────────────────
// "Built, tested, and never wired" is this codebase's most common defect, and
// a pure module with a green test is exactly what it looks like. These read
// the real call sites.

const CARD_TSX = readRepoFile("frontend/lib/workspace/fleet/AgentCreateCard.tsx");
const QUICK_CREATE_TS = readRepoFile("frontend/lib/workspace/fleet/agent-quick-create.ts");

assert(CARD_TSX.includes("agent-create-job"), "CANARY: AgentCreateCard.tsx was read and imports the job module");
assert(/agentCreateJobsForPlacement\(/.test(CARD_TSX), "the surface renders the PLACEMENT-FILTERED job list, not the raw one");
assert(/resolveSelectedAgentCreateJob\(/.test(CARD_TSX), "the surface resolves the selection through the placement gate");
assert(/planAgentCreateJobInstructions\(/.test(CARD_TSX), "the surface uses the pristine-only seed rule");
assert(/agentCreateJobPresets\(/.test(CARD_TSX + QUICK_CREATE_TS), "the presets reach the create payload");

// The two literals this whole change exists to remove. They may not come back.
assert(
  !/capability_preset:\s*["']standard["']/.test(QUICK_CREATE_TS),
  "capability_preset is no longer hardcoded in the payload builder",
);
assert(
  !/purpose_preset:\s*["']internal_assistant["']/.test(QUICK_CREATE_TS),
  "purpose_preset is no longer hardcoded in the payload builder",
);
// audience is derived server-side from purpose_preset — a copy of that map
// here would be a second opinion about one fact.
assert(
  !/audience:\s*["'](owner|external)["']/.test(QUICK_CREATE_TS),
  "audience is not hardcoded — fleet_create_agent derives it from purpose_preset",
);
// Comments are stripped before this scan, or the module's own explanation of
// WHY it does not transcribe the map would trip the tripwire watching for the
// map — the same self-tripping hazard agent-card-face.test.ts documents for
// its CSS scans.
const JOB_TS_CODE = readRepoFile("frontend/lib/workspace/fleet/agent-create-job.ts")
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/\/\/.*$/gm, "");
assert(JOB_TS_CODE.includes("AGENT_CREATE_JOBS"), "CANARY: comment-stripped job module still has real code in it");
assert(
  !/["']external["']/.test(JOB_TS_CODE) && !/_AUDIENCE_BY/.test(JOB_TS_CODE),
  "the purpose->audience map is not transcribed into TypeScript",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
