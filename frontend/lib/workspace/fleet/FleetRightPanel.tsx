"use client";

import { useEffect, useState, type ReactNode } from "react";

/** Open/closed, remembered per browser session (sessionStorage) — shared by
 *  every page that renders a FleetRightPanel, keyed so each page remembers
 *  its own state independently. Default closed. */
export function usePanelOpenState(storageKey: string): [boolean, () => void] {
  const key = `fleet:panel:${storageKey}`;
  const [open, setOpen] = useState(false);

  useEffect(() => {
    try {
      setOpen(window.sessionStorage.getItem(key) === "1");
    } catch {
      // sessionStorage unavailable (private mode, etc.) — stay closed.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const toggle = () => {
    setOpen((v) => {
      const next = !v;
      try {
        window.sessionStorage.setItem(key, next ? "1" : "0");
      } catch {
        // ignore
      }
      return next;
    });
  };

  return [open, toggle];
}

/**
 * The reusable collapsible right panel — Linear's properties-panel pattern.
 * Bordered, quiet label-left/value-right rows, 150ms width slide (the
 * existing --motion-base token). Used by project detail now, agent detail
 * later. Content is entirely caller-supplied via PanelSection/PanelRow.
 */
export function FleetRightPanel({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <div className={`fleet-right-panel${open ? " is-open" : ""}`} aria-hidden={!open}>
      <div className="fleet-right-panel-inner">{children}</div>
    </div>
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
}: {
  label: string;
  value: ReactNode;
  /** Leading indicator — an icon or a colored StatusDot. */
  icon?: ReactNode;
  /** Colors the value (status green/red, spend accent, empty muted). */
  tone?: PanelValueTone;
}) {
  return (
    <div className="fleet-panel-row">
      <span className="fleet-panel-row-label">
        {icon && <span className="fleet-panel-row-icon">{icon}</span>}
        <span>{label}</span>
      </span>
      <span className={`fleet-panel-row-value${tone !== "default" ? ` fleet-panel-row-value--${tone}` : ""}`}>
        {value}
      </span>
    </div>
  );
}
