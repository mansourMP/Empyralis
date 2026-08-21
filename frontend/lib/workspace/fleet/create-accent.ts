/**
 * WHICH CREATE CONTROL OWNS A VIEW'S ONE ACCENT FILL.
 *
 * CLAUDE.md's craft doctrine, stated once and violated on every create
 * surface this product has: "One accent colour, spent on the single primary
 * action in a view. Everything else is neutral. Two accent-filled buttons in
 * one view is a bug."
 *
 * Every one of those surfaces is the SAME shape — a list of things, up to
 * three controls that make a new one, and up to two of them on screen at
 * once:
 *
 *   header       the persistent "+ New …" in the topbar
 *   empty_state  the centred "Create your first …" a fresh list shows
 *   composer     the modal that actually creates it (TaskComposer,
 *                DocumentComposer, AgentCreateCard — a card is a composer)
 *
 *   composer open  ─▶ the composer owns it. It IS the modal; everything
 *                     behind a 45%-opacity backdrop drops to the quiet
 *                     hairline variant.
 *   list is empty  ─▶ the empty state owns it. A first-run empty state is
 *                     the one moment its own big button IS the primary
 *                     action.
 *   otherwise      ─▶ the header owns it. Past first run it is the
 *                     persistent primary action used every day.
 *
 * There is always EXACTLY ONE owner, never zero — asserted directly in
 * create-accent.test.ts, because "no accent anywhere" is a different bug
 * that reads as a page with no primary action.
 *
 * ORIGINALLY agent-create-accent.ts (2026-08-21), scoped to the three
 * agent-creation controls. Its own guard test carried the note that Tasks
 * and Documents "carry the same unfixed bug against TaskComposer /
 * DocumentComposer — a separate change, deliberately not made here." This
 * IS that change, and it is a generalisation rather than a second module on
 * purpose: two modules answering one question is how the four hand-inlined
 * ternaries this replaces came to disagree in the first place.
 *
 * Pure, no React, no CSS import — same discipline as agent-count-shape.ts,
 * so a plain `tsx` test drives the real rule the components render against.
 */

export type CreateControl = "header" | "empty_state" | "composer";

export type CreateAccentState = {
  /** No items in the list this control sits on, so the empty state is what
   *  fills the pane. */
  listIsEmpty: boolean;
  /** The composer/card is mounted in front of the view. */
  composerOpen: boolean;
};

export function createAccentOwner(state: CreateAccentState): CreateControl {
  if (state.composerOpen) return "composer";
  return state.listIsEmpty ? "empty_state" : "header";
}

export function createOwnsAccent(control: CreateControl, state: CreateAccentState): boolean {
  return createAccentOwner(state) === control;
}

/** The full className for one of those controls. `fleet-btn--accent` is the
 *  quiet variant — still reachable, just not the filled one (no dead
 *  controls: a button that loses the fill is never removed or disabled by
 *  this rule).
 *
 *  It used to be an accent-coloured control too (an accent hairline and an
 *  accent label). It is fully NEUTRAL as of 2026-08-21: the founder's rule is
 *  that the accent belongs to the filled primary button and to nothing else,
 *  and this class is by definition handed out to the control that is NOT the
 *  primary action. It stays visibly the emphatic button by weight instead —
 *  a --text-primary edge and label against a plain .fleet-btn's --border /
 *  --text-secondary. See fleet-theme.css and accent-restraint.test.ts. */
export function createButtonClass(control: CreateControl, state: CreateAccentState): string {
  return `fleet-btn ${createOwnsAccent(control, state) ? "fleet-btn--accent-fill" : "fleet-btn--accent"}`;
}

/**
 * A composer only exists while it is open, so it always owns the fill — but
 * it is routed through the rule above rather than hardcoding the filled
 * class, for two reasons that are not decoration. It keeps ONE answer to
 * "who owns the accent" (a hardcoded `fleet-btn--accent-fill` here is
 * exactly what was live in TaskComposer and DocumentComposer while the
 * header button behind them stayed filled too), and it lets the source guard
 * in create-accent.test.ts see that this control obeys the rule at all.
 */
export function composerSubmitButtonClass(): string {
  return createButtonClass("composer", { listIsEmpty: false, composerOpen: true });
}
