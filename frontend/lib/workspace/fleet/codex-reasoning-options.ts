// Which reasoning-effort control (if any) a cli_subscription agent's Model
// tab should render — pure, so it can be tested without a browser or a
// gateway. Same shape and same reason as agent-count-shape.ts and
// channel-doors.ts: the rule is data + one function, and the test imports
// the REAL rule rather than a copy of it.
//
// This exists because three genuinely different facts arrive on one field
// and used to collapse into one value:
//
//   GATEWAY BUILD               entry.supported…   MEANS              render
//   pre-forwarding (live fleet) null               "I don't know"     static ladder
//   forwarding, model has some  [{...}, ...]       "these levels"     those levels
//   forwarding, model has none  []                 "no levels"        NOTHING
//
// The first row is not hypothetical — a box running a gateway built before
// the fields were forwarded is the majority of the fleet today, so treating
// "absent" as "none" would silently delete the picker for most customers.
// The third row is this repo's "no dead controls" product law: a <select>
// whose every option the model does not implement is worse than no control,
// because picking one appears to do something and does not.
//
// Deliberately NOT an enum, an allowlist, or a level ladder. codex's own
// protocol types ReasoningEffort as an OPEN STRING (`{"type":"string",
// "minLength":1}`) precisely so a new level needs no client change —
// verified live 2026-08-20 against a real authenticated codex app-server,
// where gpt-5.6-terra advertises "ultra", a level in no doc and in no
// static table anywhere in this codebase. Anything here that validated
// levels against a known set would throw that away on the day it matters.

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

export type CodexReasoningPickerPlan =
  /** The model told us its own vocabulary. Render exactly these. */
  | { kind: "live"; options: ReasoningPickerOption[] }
  /** Nobody could tell us. Render the caller's static per-runtime ladder,
   *  which is the honestly-degraded last resort, not the primary path. */
  | { kind: "fallback" }
  /** The model positively reports no selectable levels. Render no control. */
  | { kind: "none" };

export type PlanCodexReasoningPickerInput = {
  /** The cli_subscription runtime the Model tab is currently showing. */
  runtime: string;
  /** Whether the live catalog fetch actually succeeded for this gateway.
   *  False covers offline, unauthenticated, non-codex and fetch failure
   *  alike — all of which mean "could not verify", never "verified empty". */
  catalogSupported: boolean;
  /** The live catalog, as fetched. */
  models: readonly CodexReasoningCatalogEntry[];
  /** The model id currently selected in the picker above. */
  selectedModel: string;
};

/** The one place the three states above become a rendering decision.
 *
 *  Only `codex` has a verified live catalog today; every other runtime is
 *  `fallback` by construction rather than by a hardcoded runtime list, so a
 *  runtime that grows one later needs no change here beyond being fetched. */
export function planCodexReasoningPicker(
  input: PlanCodexReasoningPickerInput,
): CodexReasoningPickerPlan {
  if (input.runtime !== "codex") return { kind: "fallback" };
  if (!input.catalogSupported) return { kind: "fallback" };

  const entry = input.models.find((m) => m.id === input.selectedModel);
  // A selected model absent from the live catalog is a real case (an id
  // saved before the provider retired it). Unknown, not empty.
  if (!entry) return { kind: "fallback" };

  const efforts = entry.supportedReasoningEfforts;
  if (efforts === null) return { kind: "fallback" };
  if (efforts.length === 0) return { kind: "none" };

  // The blank option is the model's OWN default, named when the model told
  // us what it is — "Model default (medium)" is a fact the customer can act
  // on; a bare "Model default" is a shrug. Never invents a value: the option
  // still submits "", so not choosing stays not choosing.
  const defaultLabel = entry.defaultReasoningEffort
    ? `Model default (${entry.defaultReasoningEffort})`
    : "Model default";
  return {
    kind: "live",
    options: [
      { value: "", label: defaultLabel },
      ...efforts.map((o) => ({
        value: o.reasoningEffort,
        // The level's own id leads, because that is the value actually sent
        // to codex; the description is the model's own prose, relayed
        // verbatim rather than restyled into a house vocabulary that would
        // have to be extended for every level a future model invents.
        label: o.description ? `${o.reasoningEffort} — ${o.description}` : o.reasoningEffort,
      })),
    ],
  };
}
