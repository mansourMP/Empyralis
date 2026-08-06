"use client";

import { useState } from "react";
import { ChevronRight, HelpCircle } from "lucide-react";

// One row per chord that actually works. The `g`-chords are driven by
// PrimaryRail's RAIL_ITEMS — if a destination isn't in the rail, there is no
// chord for it and it must not be listed here. "G H" (Hardware) went when
// Hardware moved into Settings (2026-07); "G B" (Billing) was listed but had
// never been bound at all — Billing lives in the account menu, not the rail.
const SHORTCUTS: { keys: string; label: string }[] = [
  { keys: "⌘K", label: "Command palette" },
  { keys: "G I", label: "Go to Inbox" },
  { keys: "G C", label: "Go to Conversations" },
  { keys: "G P", label: "Go to Projects" },
  { keys: "G A", label: "Go to Agents" },
  { keys: "J / K", label: "Move focus down / up" },
  { keys: "Enter", label: "Open the focused item" },
];

/**
 * "Keyboard shortcuts" row inside the account menu popover (see AccountMenu
 * in PrimaryRail.tsx) — the real shortcut reference this app already has
 * (the `g`-then-key chords, j/k, Cmd+K), not a placeholder. Expands inline,
 * beneath its own row, rather than opening a second floating popover: it
 * already lives inside one (the account popover), and a popover-on-a-popover
 * reads as a bug, not a feature.
 *
 * Previously a standalone control in the rail's permanent bottom-cluster row
 * (its own floating panel, always visible next to Theme/Activity/Bug
 * report). Folded into the account menu 2026-08, alongside the theme
 * toggle — a static shortcuts reference is exactly the kind of set-once,
 * looked-up-occasionally surface CLAUDE.md calls out as not deserving equal
 * billing with the things people look at daily.
 */
export function FleetHelpButton() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        className="fleet-rail-account-popover-row"
        role="menuitem"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title="Keyboard shortcuts"
      >
        <HelpCircle size={14} strokeWidth={1.75} />
        Keyboard shortcuts
        <ChevronRight
          size={13}
          strokeWidth={2}
          className={`fleet-rail-account-popover-chevron${open ? " is-expanded" : ""}`}
        />
      </button>
      {open && (
        <div className="fleet-rail-account-popover-subgroup" role="group" aria-label="Keyboard shortcuts">
          {SHORTCUTS.map((s) => (
            <div key={s.label} className="fleet-help-popover-row">
              <span>{s.label}</span>
              <kbd>{s.keys}</kbd>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
