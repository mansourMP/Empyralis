/**
 * Drives the REAL rules from agent-create-model.ts — including the real
 * PLATFORM_CREDITS_* constants it derives the included tiers from, so the
 * expected set and the actual set come from different places (CLAUDE.md:
 * "a check that derives its own expectations from the thing it checks is
 * blind, and reports 'passed'").
 *
 * Run: npx tsx lib/workspace/fleet/agent-create-model.test.ts
 */

import {
  PLATFORM_CREDITS_MODEL_BY_TIER,
  PLATFORM_CREDITS_PROVIDER,
  PLATFORM_CREDITS_TIER_OPTIONS,
} from "./fleet-model-config";
import { credentialedProvidersFromProfiles } from "./agent-create-model-catalog";
import {
  AGENT_CREATE_DEFAULT_MODEL_ID,
  AGENT_CREATE_INCLUDED_GROUP,
  agentCreateModelChoicePayload,
  buildAgentCreateModelChoices,
  byokModelChoices,
  findAgentCreateModelChoice,
  groupAgentCreateModelChoices,
  platformCreditModelChoices,
  resolveAgentCreateModelId,
} from "./agent-create-model";

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

// ── Canary: the sources this file measures against must be real ───────────
// Without these, every assertion below could pass against an empty catalog.
assert(PLATFORM_CREDITS_TIER_OPTIONS.length >= 2, "CANARY: the platform-credit tier catalog is non-empty");
assert(PLATFORM_CREDITS_PROVIDER.trim().length > 0, "CANARY: the platform-credit provider id is non-empty");

// ── The included pair is DERIVED, not transcribed ─────────────────────────

{
  const included = platformCreditModelChoices();
  assert(
    included.length === PLATFORM_CREDITS_TIER_OPTIONS.length,
    "every platform-credit tier becomes exactly one choice — no curation",
  );
  for (const opt of PLATFORM_CREDITS_TIER_OPTIONS) {
    const model = PLATFORM_CREDITS_MODEL_BY_TIER[opt.tier];
    const match = included.find((c) => c.model === model);
    assert(Boolean(match), `tier "${opt.tier}" reaches the picker`);
    assert(match?.label === opt.label, `tier "${opt.tier}" keeps the catalog's own label`);
    assert(match?.detail === opt.subtitle, `tier "${opt.tier}" keeps the catalog's own subtitle`);
    assert(match?.provider === PLATFORM_CREDITS_PROVIDER, `tier "${opt.tier}" carries the catalog's provider`);
    assert(match?.mode === "platform_credits", `tier "${opt.tier}" is a platform-credits pick`);
    assert(match?.group === AGENT_CREATE_INCLUDED_GROUP, `tier "${opt.tier}" sits in the included group`);
  }
}

// ── The default is the one the server already seeds ───────────────────────
// The cross-language half of this (that the server really does seed Pro)
// lives in server_modules/tests/test_agent_create_model_choice.py, which
// reads agent-create-model.ts off disk. This half only asserts the id is
// internally coherent and actually selectable.

{
  const choices = buildAgentCreateModelChoices();
  const chosen = findAgentCreateModelChoice(choices, AGENT_CREATE_DEFAULT_MODEL_ID);
  assert(Boolean(chosen), "the default id names a choice that is actually in the list");
  assert(chosen?.model === PLATFORM_CREDITS_MODEL_BY_TIER.pro, "the default is the Pro tier, matching the server's own seed");
  assert(resolveAgentCreateModelId(choices, "") === AGENT_CREATE_DEFAULT_MODEL_ID, "nothing requested opens on the default");
}

// ── BYOK groups: real credentials only, live ids only ─────────────────────

{
  const choices = byokModelChoices([{ provider: "openai", label: "OpenAI", models: ["gpt-5.6", "gpt-5.6-mini"] }]);
  assert(choices.length === 2, "each live model id becomes one choice");
  assert(choices.every((c) => c.mode === "byok_api"), "a BYOK pick carries mode byok_api");
  assert(choices.every((c) => c.group === "OpenAI"), "a BYOK pick is grouped under its provider's label");
  assert(choices[0].label === "gpt-5.6", "a BYOK option reads as the real model id, never a renamed tier");
}

{
  assert(
    byokModelChoices([{ provider: "openai", label: "OpenAI", models: [] }]).length === 0,
    "a provider whose live list could not be read contributes NOTHING — never an empty group, never a static fallback",
  );
  assert(
    byokModelChoices([{ provider: "  ", label: "Ghost", models: ["x"] }]).length === 0,
    "a blank provider id is dropped rather than producing an unusable choice",
  );
  assert(
    byokModelChoices([{ provider: "openai", label: "OpenAI", models: ["gpt-5.6", " ", "gpt-5.6"] }]).length === 1,
    "blank and duplicate model ids collapse to one real choice",
  );
}

{
  // The always-available pair must survive a total BYOK failure — that is
  // the whole reason the picker can degrade silently instead of lecturing.
  const degraded = buildAgentCreateModelChoices([]);
  assert(
    degraded.length === PLATFORM_CREDITS_TIER_OPTIONS.length,
    "with no credentialed provider at all, the picker is still fully usable",
  );
  assert(
    resolveAgentCreateModelId(degraded, "") === AGENT_CREATE_DEFAULT_MODEL_ID,
    "and still opens on the server's own default",
  );
}

// ── Nothing that needs a computer can be expressed ────────────────────────

{
  const all = buildAgentCreateModelChoices([{ provider: "openai", label: "OpenAI", models: ["gpt-5.6"] }]);
  assert(
    all.every((c) => c.mode === "platform_credits" || c.mode === "byok_api"),
    "no creation-time choice can carry cli_subscription or local — both need a paired computer this surface never asks about",
  );
  for (const c of all) {
    const payload = agentCreateModelChoicePayload(c);
    assert(payload !== null, "every choice yields a payload");
    assert(
      Object.keys(payload || {}).sort().join(",") === "mode,model,provider".split(",").sort().join(","),
      "the payload carries exactly mode/provider/model — no gateway binding, runtime, engine or reasoning effort",
    );
  }
  assert(agentCreateModelChoicePayload(null) === null, "no choice yields no payload, never a fabricated one");
}

// ── Stale / unknown selections resolve to something real ──────────────────

{
  const choices = buildAgentCreateModelChoices();
  assert(
    resolveAgentCreateModelId(choices, "byok_api:openai:gone") === AGENT_CREATE_DEFAULT_MODEL_ID,
    "a selection that left the list falls back to the default rather than a blank select",
  );
  assert(resolveAgentCreateModelId([], "anything") === "", "an empty catalog is an honest empty string, never a fabricated id");
}

{
  // A catalog that somehow lacks the default still selects something real.
  const onlyByok = byokModelChoices([{ provider: "openai", label: "OpenAI", models: ["gpt-5.6"] }]);
  assert(resolveAgentCreateModelId(onlyByok, "") === onlyByok[0].id, "with no default present, the first real choice is selected");
}

// ── Grouping preserves render order ───────────────────────────────────────

{
  const groups = groupAgentCreateModelChoices(
    buildAgentCreateModelChoices([
      { provider: "openai", label: "OpenAI", models: ["gpt-5.6"] },
      { provider: "anthropic", label: "Anthropic", models: ["claude-sonnet-4-6"] },
    ]),
  );
  assert(groups[0].group === AGENT_CREATE_INCLUDED_GROUP, "the included pair is always first — it is what most new agents use");
  assert(groups.map((g) => g.group).join("|") === `${AGENT_CREATE_INCLUDED_GROUP}|OpenAI|Anthropic`, "group order follows first appearance");
  assert(groups.reduce((n, g) => n + g.choices.length, 0) === 4, "grouping loses nothing");
}

// ── Ids are unique and unambiguous across providers ───────────────────────

{
  const all = buildAgentCreateModelChoices([
    { provider: "deepseek", label: "DeepSeek", models: [PLATFORM_CREDITS_MODEL_BY_TIER.pro] },
  ]);
  const ids = all.map((c) => c.id);
  assert(new Set(ids).size === ids.length, "the same model id on platform credits and on a customer's own key are two distinct choices");
}


// ── The live sweep's own parsing rule ─────────────────────────────────────
// Imported from the catalog module so the REAL parser is under test, not a
// re-derivation of it here.

{
  const parsed = credentialedProvidersFromProfiles({
    items: [
      { provider: "OpenAI", enabled: true },
      { provider: "openai", enabled: true },
      { provider: "anthropic" },
      { provider: "groq", enabled: false },
      { provider: "azure_openai", enabled: true },
      { provider: "custom_openai_compatible", enabled: true },
      { provider: "   ", enabled: true },
      null,
      "not-an-object",
    ],
  });
  assert(parsed.join(",") === "openai,anthropic", "only distinct, enabled, model-discoverable providers survive the sweep");
  assert(!parsed.includes("groq"), "a DISABLED profile never contributes a group");
  assert(
    !parsed.includes("azure_openai") && !parsed.includes("custom_openai_compatible"),
    "freeform providers are excluded — their model is a deployment name, so there is nothing to discover",
  );
}

assert(credentialedProvidersFromProfiles({}).length === 0, "a body with no items is no providers, never a throw");
assert(credentialedProvidersFromProfiles(null).length === 0, "an unreadable body is no providers, never a throw");
assert(credentialedProvidersFromProfiles({ items: "nope" }).length === 0, "a malformed items field is no providers, never a throw");

// ── Summary ───────────────────────────────────────────────────────────────

console.log(`${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
