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
 * Run: npx tsx lib/workspace/fleet/codex-reasoning-options.test.ts
 */

import {
  planCodexReasoningPicker,
  type CodexReasoningCatalogEntry,
} from "./codex-reasoning-options";

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
// level on it that exists in no documentation and in no static table in
// this codebase. If this test ever has to be "fixed" by removing ultra,
// the fix is wrong: the whole point is that the level set is open.
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
// max/ultra) — this is the observed fact that makes a single global ladder
// wrong by construction, not a hypothetical.
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

// --- live: the model's own vocabulary wins ---

const terra = planFor(MODELS, "gpt-5.6-terra");
assert(terra.kind === "live", "a model reporting levels plans a live picker");
if (terra.kind === "live") {
  assert(
    terra.options.map((o) => o.value).join(" ") === " low medium high xhigh max ultra",
    "live options are the model's own levels, in the model's own order, behind a blank default",
  );
  assert(
    terra.options.some((o) => o.value === "ultra"),
    'an undocumented level ("ultra") survives to the picker — never filtered against a known ladder',
  );
  assert(
    terra.options[0].label === "Model default (medium)",
    "the blank option names the model's own reported default",
  );
  assert(terra.options[0].value === "", "the default option still submits an empty value, inventing nothing");
  assert(
    terra.options[6].label === "ultra — Maximum reasoning with automatic task delegation",
    "the level's own id leads the label and the model's own prose is relayed verbatim",
  );
}

// Two models on ONE account genuinely differ — the decision must be
// per-model, never per-runtime.
const gpt55 = planFor(MODELS, "gpt-5.5");
assert(gpt55.kind === "live", "a second model also plans a live picker");
if (gpt55.kind === "live" && terra.kind === "live") {
  assert(
    gpt55.options.length !== terra.options.length,
    "two models on the same account produce different option sets (per-model, not per-runtime)",
  );
  assert(
    !gpt55.options.some((o) => o.value === "ultra"),
    "a level one model offers is not leaked onto a model that does not offer it",
  );
}

// --- fallback: every way of not knowing ---

assert(
  planCodexReasoningPicker({ runtime: "codex", catalogSupported: false, models: MODELS, selectedModel: "gpt-5.6-terra" }).kind === "fallback",
  "a catalog that could not be fetched is unknown, never verified-empty",
);
assert(
  planCodexReasoningPicker({ runtime: "claude_code", catalogSupported: true, models: MODELS, selectedModel: "gpt-5.6-terra" }).kind === "fallback",
  "a runtime with no live catalog falls back rather than borrowing codex's answer",
);
assert(
  planFor(MODELS, "gpt-5.4-retired").kind === "fallback",
  "a selected model absent from the live catalog is unknown, not empty",
);
assert(
  planFor([OLD_GATEWAY_TERRA], "gpt-5.6-terra").kind === "fallback",
  "a gateway too old to forward the fields degrades to the static ladder — the majority fleet state",
);

// --- none: the no-dead-controls law ---
// THIS is the case the two halves of this chain could not see separately:
// before the null/[] split, a model positively reporting zero levels was
// byte-identical to an old gateway, so it rendered the static ladder — a
// <select> whose every option the model does not implement.
const NO_LEVELS: CodexReasoningCatalogEntry = {
  id: "some-future-model",
  defaultReasoningEffort: null,
  supportedReasoningEfforts: [],
};
assert(
  planFor([NO_LEVELS], "some-future-model").kind === "none",
  "a model positively reporting zero levels renders NO control, never a ladder it does not implement",
);
assert(
  planFor([OLD_GATEWAY_TERRA], "gpt-5.6-terra").kind !== planFor([NO_LEVELS], "some-future-model").kind,
  "absent and empty reach DIFFERENT renderings — the whole point of keeping null distinct from []",
);

// --- a model that reports levels but no default ---
const NO_DEFAULT: CodexReasoningCatalogEntry = {
  id: "no-default",
  defaultReasoningEffort: null,
  supportedReasoningEfforts: [{ reasoningEffort: "high", description: "" }],
};
const noDefault = planFor([NO_DEFAULT], "no-default");
assert(noDefault.kind === "live", "levels without a stated default still plan a live picker");
if (noDefault.kind === "live") {
  assert(
    noDefault.options[0].label === "Model default",
    "an unstated default gets a bare label rather than an invented level name",
  );
  assert(
    noDefault.options[1].label === "high",
    "a level with no description falls back to its bare id, never an empty-looking label",
  );
}

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
