/**
 * Imports the REAL rule (planCodexReasoningPicker) rather than re-deriving
 * it here — same discipline as agent-count-shape.test.ts and
 * openclaw-channel-copy.test.ts, for the reason CLAUDE.md already states:
 * "a check that derives its own expectations from the thing it checks is
 * blind, and reports 'passed'."
 *
 * The fixtures below are NOT invented. They are the real, verbatim
 * `model/list` payload observed live on 2026-08-20 by spawning a real
 * `codex app-server` (0.144.1) and driving its actual
 * initialize -> getAuthStatus -> model/list JSON-RPC exchange against a
 * real authenticated ChatGPT/Codex login — the same handshake
 * empyralis-gateway/src/llm/codex-app-server.ts performs. CLAUDE.md:
 * "a fixture that invents its own input cannot notice the real input is
 * shaped differently."
 *
 * WHAT CHANGED 2026-08-20 (founder override): these assertions used to
 * require the picker to RESTRICT itself to whatever the live catalog
 * reported, and to render NOTHING when a model reported zero levels. The
 * founder overrode that — one shared ladder, always selectable, never
 * narrowed per provider or per model. The assertions below were INVERTED
 * rather than deleted so the old, overridden behaviour cannot be
 * reinstated by accident.
 *
 * Run: npx tsx lib/workspace/fleet/codex-reasoning-options.test.ts
 */

import {
  planCodexReasoningPicker,
  type CodexReasoningCatalogEntry,
} from "./codex-reasoning-options";
import { REASONING_EFFORT_LADDER } from "./fleet-provider-constants";

/** The expected set is built from the REAL shared constant, never retyped —
 *  otherwise this file could only ever confirm itself. */
const LADDER: string[] = ["", ...(REASONING_EFFORT_LADDER as readonly string[])];

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

// --- Observed live, 2026-08-20, verbatim from a real model/list response ---
// gpt-5.6-terra is the account's own default model, and "ultra" is a real
// level on it that exists in no documentation. It is now also a rung of the
// shared ladder — which is exactly why the ladder has to stay OPEN at the
// bottom: the next such level will not be on it either.
const LIVE_TERRA: CodexReasoningCatalogEntry = {
  id: "gpt-5.6-terra",
  defaultReasoningEffort: "medium",
  supportedReasoningEfforts: [
    { reasoningEffort: "low", description: "Fast responses with lighter reasoning" },
    { reasoningEffort: "medium", description: "Balances speed and reasoning depth for everyday tasks" },
    { reasoningEffort: "high", description: "Greater reasoning depth for complex problems" },
    { reasoningEffort: "xhigh", description: "Extra high reasoning depth for complex problems" },
    { reasoningEffort: "max", description: "Maximum reasoning depth for the hardest problems" },
    { reasoningEffort: "ultra", description: "Maximum reasoning with automatic task delegation" },
  ],
};

// Same account, different model, genuinely DIFFERENT level set (4, no
// max/ultra). Under the old rule this NARROWED the picker. Under the
// founder's rule it only means there is less to annotate.
const LIVE_GPT55: CodexReasoningCatalogEntry = {
  id: "gpt-5.5",
  defaultReasoningEffort: "medium",
  supportedReasoningEfforts: [
    { reasoningEffort: "low", description: "Fast responses with lighter reasoning" },
    { reasoningEffort: "medium", description: "Balances speed and reasoning depth for everyday tasks" },
    { reasoningEffort: "high", description: "Greater reasoning depth for complex problems" },
    { reasoningEffort: "xhigh", description: "Extra high reasoning depth for complex problems" },
  ],
};

// What a box running a gateway built BEFORE the fields were forwarded
// produces: the key never arrives, so the parser yields null. This is the
// majority live state of the fleet today, not an edge case.
const OLD_GATEWAY_TERRA: CodexReasoningCatalogEntry = {
  id: "gpt-5.6-terra",
  defaultReasoningEffort: null,
  supportedReasoningEfforts: null,
};

const MODELS = [LIVE_TERRA, LIVE_GPT55];

function planFor(models: readonly CodexReasoningCatalogEntry[], selectedModel: string) {
  return planCodexReasoningPicker({ runtime: "codex", catalogSupported: true, models, selectedModel });
}

function values(plan: { options: { value: string }[] }): string {
  return plan.options.map((o) => o.value).join(" ");
}

/** The unannotated ladder — what every "we could not verify this model"
 *  input must produce, byte for byte. */
function isPlainLadder(plan: { kind: string; options: { value: string; label: string }[] }): boolean {
  return plan.kind === "fallback"
    && values(plan) === LADDER.join(" ")
    && plan.options[0].label === "Model default";
}

// --- live: the catalog ANNOTATES, it does not restrict ---

const terra = planFor(MODELS, "gpt-5.6-terra");
assert(terra.kind === "live", "a model reporting levels plans a live (annotated) picker");
assert(
  values(terra) === LADDER.join(" "),
  "the whole shared ladder is offered, in ladder order, behind a blank default",
);
assert(
  terra.options.some((o) => o.value === "ultra"),
  'a level that reached the ladder from a live catalog ("ultra") is still offered',
);
assert(
  terra.options[0].label === "Model default (medium)",
  "the blank option names the model's own reported default",
);
assert(terra.options[0].value === "", "the default option still submits an empty value, inventing nothing");
assert(
  terra.options.some((o) => o.label === "Ultra — Maximum reasoning with automatic task delegation"),
  "the model's own prose is relayed verbatim, annotating the ladder's own human label",
);

// Two models on ONE account genuinely differ in what they REPORT — and that
// must no longer change what the customer is allowed to pick.
const gpt55 = planFor(MODELS, "gpt-5.5");
assert(gpt55.kind === "live", "a second model also plans a live picker");
assert(
  values(gpt55) === LADDER.join(" "),
  "a model reporting FEWER levels than the ladder still offers the whole ladder — the founder's override",
);
assert(
  gpt55.options.some((o) => o.value === "ultra" && o.label === "Ultra"),
  "a level this model does not report stays selectable, just unannotated",
);
assert(
  !gpt55.options.some((o) => o.label.startsWith("Ultra — ")),
  "another model's prose is never leaked onto a model that did not report that level",
);
assert(
  values(gpt55) === values(terra),
  "two models on one account offer the SAME choices — the ladder is never per-model",
);

// --- fallback: every way of not knowing still renders the ladder ---

assert(
  isPlainLadder(planCodexReasoningPicker({ runtime: "codex", catalogSupported: false, models: MODELS, selectedModel: "gpt-5.6-terra" })),
  "a catalog that could not be fetched still renders the whole ladder, unannotated",
);
// The runtime no longer gates annotation — `catalogSupported` does. That is
// deliberate: the gateway can now answer llm.models.list for more than one
// runtime (cursor_cli and grok_build have their own native `models`
// subcommands), so hardcoding "codex" here would have silently discarded
// their catalogs. A runtime the gateway cannot enumerate comes back
// supported:false and lands on the plain ladder, below.
assert(
  isPlainLadder(planCodexReasoningPicker({ runtime: "claude_code", catalogSupported: false, models: [], selectedModel: "claude-opus-4-8" })),
  "a runtime the gateway cannot enumerate renders the ladder rather than borrowing another runtime's answer",
);
assert(
  isPlainLadder(planFor(MODELS, "gpt-5.4-retired")),
  "a selected model absent from the live catalog renders the ladder, unannotated",
);
assert(
  isPlainLadder(planFor([OLD_GATEWAY_TERRA], "gpt-5.6-terra")),
  "a gateway too old to forward the fields renders the ladder — the majority fleet state",
);

// --- the OVERRIDDEN "none" branch ---
// This input used to render NO control at all ("no dead controls"). The
// founder overrode it: "If it works, it works otherwise you can still
// choose it." Inverted, not deleted.

const NO_LEVELS: CodexReasoningCatalogEntry = {
  id: "some-future-model",
  defaultReasoningEffort: null,
  supportedReasoningEfforts: [],
};
assert(
  isPlainLadder(planFor([NO_LEVELS], "some-future-model")),
  "a model reporting ZERO levels STILL offers the whole ladder — never a hidden control",
);
assert(
  planFor([NO_LEVELS], "some-future-model").options.length === LADDER.length,
  "there is no input under which this planner returns an empty option list",
);
assert(
  values(planFor([OLD_GATEWAY_TERRA], "gpt-5.6-terra")) === values(planFor([NO_LEVELS], "some-future-model")),
  "absent and empty now reach the SAME rendering — there is nothing to annotate in either case",
);

// --- off-ladder levels are APPENDED, never dropped ---
// This is the half of the live catalog that is still load-bearing: the
// ladder is open at the bottom, exactly as codex's own open-string
// ReasoningEffort type intends.

const EXTRA_LEVEL: CodexReasoningCatalogEntry = {
  id: "legacy-codex-model",
  defaultReasoningEffort: "medium",
  supportedReasoningEfforts: [
    { reasoningEffort: "minimal", description: "Barely thinks" },
    { reasoningEffort: "hyperdrive", description: "A level no label map has ever seen" },
  ],
};
const extra = planFor([EXTRA_LEVEL], "legacy-codex-model");
assert(extra.kind === "live", "a model reporting only off-ladder levels is still a live plan");
assert(
  values(extra) === `${LADDER.join(" ")} minimal hyperdrive`,
  "off-ladder levels are appended after the shared ladder, in the model's own order",
);
assert(
  extra.options.some((o) => o.label === "Minimal — Barely thinks"),
  "an off-ladder level this codebase has a label for uses that label",
);
assert(
  extra.options.some((o) => o.label === "hyperdrive — A level no label map has ever seen"),
  "a level no label map knows keeps its raw id — never silently rendered as 'Model default'",
);

// --- a model that reports levels but no default ---
const NO_DEFAULT: CodexReasoningCatalogEntry = {
  id: "no-default",
  defaultReasoningEffort: null,
  supportedReasoningEfforts: [{ reasoningEffort: "high", description: "" }],
};
const noDefault = planFor([NO_DEFAULT], "no-default");
assert(noDefault.kind === "live", "levels without a stated default still plan a live picker");
assert(
  noDefault.options[0].label === "Model default",
  "an unstated default gets a bare label rather than an invented level name",
);
assert(
  noDefault.options.some((o) => o.value === "high" && o.label === "High"),
  "a level with no description keeps the ladder's own human label, never an empty-looking one",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
