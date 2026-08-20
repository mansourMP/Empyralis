// Which reasoning-effort options a cli_subscription agent's Model tab
// renders — pure, so it can be tested without a browser or a gateway. Same
// shape and same reason as agent-count-shape.ts and channel-doors.ts: the
// rule is data + one function, and the test imports the REAL rule rather
// than a copy of it.
//
// ── THE LADDER IS ALWAYS OFFERED (founder's rule, 2026-08-20) ─────────────
//
// An earlier version of this module resolved three ways — live levels, a
// static ladder, and NOTHING at all when a model positively reported zero
// levels. The founder overrode the third branch directly:
//
//   "I'm not going to change this effort level based on like separated for
//    each one provider... low medium high, extra high max and ultra. If it
//    works, it works otherwise you can still choose it — for example that's
//    how it works inside this Claude Code even if I use it with DeepSeek,
//    it doesn't have any effort level."
//
// So the live catalog changed ROLE. It no longer decides WHETHER a control
// exists, or WHICH levels are legal. It only ENRICHES a ladder that is
// always there:
//
//   BEFORE                              AFTER
//     null  → static ladder               null  → the ladder, plain
//     [...] → exactly those levels        [...] → the ladder, annotated with
//     []    → no control at all                   the model's own default and
//              ↑ the overridden branch             its own prose, plus any
//                                                  level it reports that the
//                                                  ladder does not carry
//                                        []    → the ladder, plain
//
// This is NOT the "no dead controls" law being waived. A chosen level always
// reaches something real: on the cloud provider path an unsupported level
// degrades to a strong system instruction (openai_compat_adapter's
// _apply_reasoning_effort), and on the cli_subscription path it is clamped
// into the bound CLI's own vocabulary before its own flag is built. The one
// runtime where it genuinely goes nowhere is cursor_cli, whose CLI publishes
// no reasoning control at all — that is reported honestly rather than fixed
// by inventing a control Cursor does not provide.
//
// Still deliberately NOT an enum, an allowlist, or a filter over the live
// levels. codex's own protocol types ReasoningEffort as an OPEN STRING
// (`{"type":"string","minLength":1}`) precisely so a new level needs no
// client change — verified live 2026-08-20 against a real authenticated
// codex app-server, where gpt-5.6-terra advertises "ultra", a level in no
// doc and in no static table anywhere in this codebase. A model-reported
// level the shared ladder does not carry is APPENDED, never dropped.

import { REASONING_EFFORT_OPTIONS, reasoningEffortLabel } from "./fleet-provider-constants";

export type CodexReasoningEffortOption = {
  reasoningEffort: string;
  description: string;
};

/** Just the parts of a live catalog entry this decision reads. Structural,
 *  so fleet-model-config.ts's CodexModelCatalogEntry satisfies it without
 *  this module importing (or coupling to) the fetch layer. */
export type CodexReasoningCatalogEntry = {
  id: string;
  supportedReasoningEfforts: CodexReasoningEffortOption[] | null;
  defaultReasoningEffort: string | null;
};

export type ReasoningPickerOption = { value: string; label: string };

export type CodexReasoningPickerPlan = {
  /** "live" — a live catalog entry annotated the ladder below.
   *  "fallback" — nobody could tell us anything about this exact model, so
   *  the ladder renders plain. Both render the SAME control; this only
   *  decides which one-line hint sits under it. */
  kind: "live" | "fallback";
  options: ReasoningPickerOption[];
};

export type PlanCodexReasoningPickerInput = {
  /** The cli_subscription runtime the Model tab is currently showing. */
  runtime: string;
  /** Whether the live catalog fetch actually succeeded for this gateway.
   *  False covers offline, unauthenticated, no-live-catalog and fetch
   *  failure alike — all of which mean "could not verify", and none of
   *  which may remove the ladder. */
  catalogSupported: boolean;
  /** The live catalog, as fetched. */
  models: readonly CodexReasoningCatalogEntry[];
  /** The model id currently selected in the picker above. */
  selectedModel: string;
};

/** The shared ladder, as picker options. Rebuilt per call rather than shared
 *  by reference so a caller can never mutate the constant. */
function ladderOptions(): ReasoningPickerOption[] {
  return REASONING_EFFORT_OPTIONS.map((o) => ({ value: o.value, label: o.label }));
}

/** The one place the live catalog becomes a rendering decision. Always
 *  returns a usable option list — every runtime, every model, every gateway
 *  build. */
export function planCodexReasoningPicker(
  input: PlanCodexReasoningPickerInput,
): CodexReasoningPickerPlan {
  const plain: CodexReasoningPickerPlan = { kind: "fallback", options: ladderOptions() };
  if (!input.catalogSupported) return plain;

  const entry = input.models.find((m) => m.id === input.selectedModel);
  // A selected model absent from the live catalog is a real case (an id
  // saved before the provider retired it). Unknown, not empty.
  if (!entry) return plain;

  const efforts = entry.supportedReasoningEfforts;
  // null = "this gateway did not tell us". [] = "this model reports none".
  // Both now render the same ladder — the distinction survives only in what
  // there is to annotate it WITH, which is nothing in either case.
  if (!efforts || efforts.length === 0) return plain;

  const described = new Map(efforts.map((o) => [o.reasoningEffort, o.description || ""]));

  // The blank option is the model's OWN default, named when the model told
  // us what it is — "Model default (medium)" is a fact the customer can act
  // on; a bare "Model default" is a shrug. Never invents a value: the option
  // still submits "", so not choosing stays not choosing.
  const options = ladderOptions().map((o) => {
    if (o.value === "") {
      return entry.defaultReasoningEffort
        ? { value: "", label: `Model default (${entry.defaultReasoningEffort})` }
        : o;
    }
    const description = described.get(o.value);
    // The model's own prose is relayed verbatim rather than restyled into a
    // house vocabulary that would have to be extended for every level a
    // future model invents. A ladder level the model did NOT report keeps
    // its plain label and stays selectable — that is the founder's rule.
    return description ? { value: o.value, label: `${o.label} — ${description}` } : o;
  });

  // A level the model reports that the shared ladder does not carry is
  // APPENDED, never dropped — this is the half of the live catalog that is
  // still load-bearing. reasoningEffortLabel falls back to the raw id for a
  // level no label map has ever seen, which is exactly the open-string case.
  const known = new Set(options.map((o) => o.value));
  for (const effort of efforts) {
    if (known.has(effort.reasoningEffort)) continue;
    known.add(effort.reasoningEffort);
    const base = reasoningEffortLabel(effort.reasoningEffort);
    const label = base === "Model default" ? effort.reasoningEffort : base;
    options.push({
      value: effort.reasoningEffort,
      label: effort.description ? `${label} — ${effort.description}` : label,
    });
  }

  return { kind: "live", options };
}
