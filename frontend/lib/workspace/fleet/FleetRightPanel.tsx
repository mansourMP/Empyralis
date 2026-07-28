"use client";

import { useEffect } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";

import { PANEL_WIDTH, useResizableWidth } from "./fleet-preferences";

/**
 * Properties drawer — Linear's properties-panel pattern (bordered, quiet
 * label-left/value-right rows), but as an OVERLAY layer above the content
 * sheet, never a permanent flex sibling: the sheet underneath never resizes
 * or reflows whether this is open or closed. Closed by default; the caller
 * owns `open` state via a toolbar toggle button. Dismissed by the toggle,
 * this panel's own close button, clicking the scrim, or Escape while focus
 * is inside it. Content is entirely caller-supplied via PanelSection/PanelRow.
 */
export function FleetRightPanel({
  open,
  onClose,
  children,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const panel = useResizableWidth({
    storageKey: PANEL_WIDTH.key,
    defaultWidth: PANEL_WIDTH.def,
    minWidth: PANEL_WIDTH.min,
    maxWidth: PANEL_WIDTH.max,
    cssVar: "--panel-w",
    // The handle is on this panel's LEFT edge, so dragging left widens it —
    // the drag maths runs from the panel's right edge inward.
    edge: "left",
  });

  // Escape closes. This used to hang off the drawer's own onKeyDown, which
  // only fired when focus was already inside it — opening the panel and
  // immediately pressing Escape did nothing. A window listener, live only
  // while open, closes it from anywhere; capture phase + stopPropagation
  // keeps it from also reaching the page's "Esc returns to the previous
  // view" handler and navigating away underneath the closing panel.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  // Always mounted, opened via data-open. Returning null when closed — what
  // this did before — means the element does not exist to transition FROM,
  // so a close was an instant disappearance no matter what CSS asked for.
  // Both halves are inert when closed (pointer-events, aria-hidden), so a
  // permanently-mounted scrim can't swallow clicks meant for the content.
  return (
    <>
      <div className="fleet-properties-scrim" data-open={open} onClick={onClose} aria-hidden />
      <div
        ref={panel.elRef as React.RefObject<HTMLDivElement>}
        className="fleet-properties-drawer"
        data-open={open}
        role="complementary"
        aria-label="Properties"
        aria-hidden={!open}
        inert={!open}
      >
        <div
          {...panel.separatorProps}
          tabIndex={open ? 0 : -1}
          className="fleet-properties-resizer"
          aria-label="Resize properties panel"
          title="Drag to resize"
        />
        <button type="button" className="fleet-properties-drawer-close" onClick={onClose} aria-label="Close properties">
          <X size={15} strokeWidth={1.75} />
        </button>
        <div className="fleet-properties-drawer-inner">{children}</div>
      </div>
    </>
  );
}

export function PanelSection({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div>
      <div
        className="fleet-right-panel-section-title"
        style={action ? { display: "flex", justifyContent: "space-between", alignItems: "center" } : undefined}
      >
        <span>{title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

/**
 * Placeholder rows for a PanelSection whose contents are still loading.
 * Same job as FleetListSkeleton, at this drawer's row scale: `minHeight`
 * pins each row to .fleet-panel-row's real height (6px top/bottom padding
 * + a 13px line = ~31px) so the section reserves its final depth up front.
 * MAN-89: without it a still-loading section rendered its "nothing here"
 * empty line instead — both untrue while a fetch is in flight, and a row
 * shorter than the real content, so the drawer grew as sections resolved.
 */
export function PanelRowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="fleet-panel-row" style={{ minHeight: 31, boxSizing: "border-box" }}>
          <span className="fleet-skeleton-bar" style={{ width: `${44 + (i % 3) * 14}%`, height: 12 }} />
          <span className="fleet-skeleton-bar" style={{ width: 40, height: 12 }} />
        </div>
      ))}
    </div>
  );
}

export type PanelValueTone = "default" | "online" | "offline" | "muted" | "accent";

export function PanelRow({
  label,
  value,
  icon,
  tone = "default",
  hint,
}: {
  label: string;
  value: ReactNode;
  /** Leading indicator — an icon or a colored StatusDot. */
  icon?: ReactNode;
  /** Colors the value (status green/red, spend accent, empty muted). */
  tone?: PanelValueTone;
  /** Optional native tooltip (title attribute) on the label — for a row
   *  whose meaning isn't obvious from the label alone. */
  hint?: string;
}) {
  return (
    <div className="fleet-panel-row">
      <span className="fleet-panel-row-label" title={hint}>
        {icon && <span className="fleet-panel-row-icon">{icon}</span>}
        <span>{label}</span>
      </span>
      <span className={`fleet-panel-row-value${tone !== "default" ? ` fleet-panel-row-value--${tone}` : ""}`}>
        {value}
      </span>
    </div>
  );
}
