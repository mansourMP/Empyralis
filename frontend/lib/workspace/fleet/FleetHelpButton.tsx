"use client";

import { useEffect, useRef, useState } from "react";
import { HelpCircle } from "lucide-react";

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
 * "Shortcuts" rail row, rendered directly below Ask AI at the bottom of
 * PrimaryRail's nav (the fleet-rail-utility group in PrimaryRail.tsx). Its
 * popover is the real keyboard-shortcut reference this app already has
 * (the `g`-then-key chords, j/k, Cmd+K), not a placeholder.
 *
 * Was previously a floating "?" pinned to the bottom-right corner of the
 * content area; moved into the rail alongside Ask AI because on mobile that
 * floating pair sat directly on top of the chat composer's Send button.
 */
export function FleetHelpButton({ asControl = false }: { asControl?: boolean } = {}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={ref} className={`fleet-help-float${asControl ? " fleet-help-float--control" : ""}`}>
      {open && (
        <div className="fleet-help-popover" role="menu" aria-label="Keyboard shortcuts">
          <div className="fleet-help-popover-title">Keyboard shortcuts</div>
          {SHORTCUTS.map((s) => (
            <div key={s.label} className="fleet-help-popover-row">
              <span>{s.label}</span>
              <kbd>{s.keys}</kbd>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        className={
          asControl
            ? `fleet-rail-control-btn${open ? " is-active" : ""}`
            : `fleet-rail-item fleet-help-float-btn${open ? " fleet-rail-item--active" : ""}`
        }
        onClick={() => setOpen((v) => !v)}
        title="Keyboard shortcuts"
        aria-label="Keyboard shortcuts"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="fleet-rail-item-icon">
          <HelpCircle size={16} strokeWidth={1.75} />
        </span>
        {!asControl && <span className="fleet-rail-item-label">Shortcuts</span>}
      </button>
    </div>
  );
}
