"use client";

import type { ReactNode } from "react";
import { X } from "lucide-react";

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
  if (!open) return null;
  return (
    <>
      <div className="fleet-properties-scrim" onClick={onClose} />
      <div
        className="fleet-properties-drawer"
        role="complementary"
        aria-label="Properties"
        onKeyDown={(e) => {
          // Own Escape here so it closes just this drawer — otherwise it
          // bubbles to the page's own "Esc returns to the previous view"
          // handler and navigates away instead.
          if (e.key === "Escape") {
            e.stopPropagation();
            onClose();
          }
        }}
      >
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
