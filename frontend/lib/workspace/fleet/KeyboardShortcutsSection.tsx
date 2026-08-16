"use client";

import { RAIL_ITEMS } from "./primary-rail-nav";

/**
 * Keyboard shortcuts — a real routed Settings page (/settings/shortcuts,
 * 2026-08-16). It used to be an accordion inside the account-menu popover
 * (FleetHelpButton.tsx, deleted with this); the account-menu row is a plain
 * link here now.
 *
 * ONE ROW PER CHORD THAT ACTUALLY WORKS, and the `g`-chord rows are DERIVED
 * from the same RAIL_ITEMS the keydown handler itself matches against
 * (PrimaryRail.tsx) — never a hand-kept list. The old hand-kept one still
 * advertised chords for Conversations (G C) and Agents (G A) long after
 * both surfaces left navigation: neither key was bound to anything, and the
 * list had no way to notice. Deriving makes that class of lie impossible;
 * primary-rail-nav.test.ts asserts the derivation stays.
 */
const SHORTCUT_ROWS: { keys: string; label: string }[] = [
  { keys: "⌘K", label: "Command palette" },
  ...RAIL_ITEMS.map((item) => ({
    keys: `G ${item.chord.toUpperCase()}`,
    label: `Go to ${item.label}`,
  })),
  { keys: "J / K", label: "Move focus down / up" },
  { keys: "Enter", label: "Open the focused item" },
];

export function KeyboardShortcutsSection() {
  return (
    <>
      <h2 className="fleet-detail-section-title">Keyboard shortcuts</h2>
      <div className="fleet-shortcuts-list">
        {SHORTCUT_ROWS.map((s) => (
          <div key={s.label} className="fleet-shortcuts-row">
            <span>{s.label}</span>
            <kbd>{s.keys}</kbd>
          </div>
        ))}
      </div>
    </>
  );
}
