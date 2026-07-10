"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { BarChart3, Check, PanelRightClose, PanelRightOpen, SlidersHorizontal } from "lucide-react";

export type ToolbarOption = { value: string; label: string };
export type ToolbarFilter = {
  key: string;
  label: string;
  value: string;
  options: ToolbarOption[];
  onChange: (value: string) => void;
};

/**
 * The quiet right-aligned icon-button cluster every list page portals into
 * the shell topbar's action slot — see HeaderAction in Breadcrumbs.tsx.
 * Three fixed slots, in order: [usage] [filter+sort] [panel-toggle] — UI
 * Contract §6, a hard invariant, not a default. panel-toggle is ALWAYS
 * rightmost, physically adjacent to the panel edge it opens (the
 * properties drawer opens from the right — see FleetRightPanel). Filter
 * and sort share ONE icon button and ONE popover (two icons for one
 * concept was redundant) — Linear's exact treatment otherwise: small
 * ghost buttons, quiet hover, accent reserved for an open menu or an
 * actually-applied filter/sort. Any slot the caller doesn't wire up
 * simply doesn't render — no dead controls.
 */
export function FleetToolbar({
  filters,
  sortOptions,
  sortValue,
  sortDefault = "",
  onSortChange,
  panelOpen,
  onTogglePanel,
  usageHref,
}: {
  filters?: ToolbarFilter[];
  sortOptions?: ToolbarOption[];
  sortValue?: string;
  sortDefault?: string;
  onSortChange?: (value: string) => void;
  /** Renders the panel-toggle icon-button FIRST in the cluster, mirroring
   *  the caller's own FleetRightPanel `open` state. Omit on pages with no
   *  properties drawer. */
  panelOpen?: boolean;
  onTogglePanel?: () => void;
  /** Renders a Usage icon-button LAST in the cluster, linking to the
   *  workspace usage dashboard — same target, same icon, every list page. */
  usageHref?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const hasFilters = Boolean(filters && filters.length > 0);
  const hasSort = Boolean(sortOptions && sortOptions.length > 0);
  const hasControls = hasFilters || hasSort;
  const filterActive = (filters || []).some((f) => f.value && f.value !== "all");
  const sortActive = Boolean(sortValue && sortValue !== sortDefault);
  const controlsActive = filterActive || sortActive;

  if (!hasControls && !onTogglePanel && !usageHref) return null;

  return (
    <div className="fleet-toolbar-actions" ref={ref}>
      {usageHref && (
        <Link href={usageHref} className="fleet-icon-btn" aria-label="Usage" title="Usage">
          <BarChart3 size={16} strokeWidth={1.75} />
        </Link>
      )}

      {hasControls && (
        <>
          <button
            type="button"
            className={`fleet-icon-btn${controlsActive || open ? " is-active" : ""}`}
            aria-label="Filter and sort"
            title="Filter and sort"
            onClick={() => setOpen((v) => !v)}
          >
            <SlidersHorizontal size={16} strokeWidth={1.75} />
          </button>
          {open && (
            <div className="fleet-toolbar-popover">
              {hasFilters && filters!.map((f) => (
                <div key={f.key} className="fleet-toolbar-popover-group">
                  <div className="fleet-toolbar-popover-label">{f.label}</div>
                  {f.options.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      className={`fleet-toolbar-popover-option${f.value === opt.value ? " is-selected" : ""}`}
                      onClick={() => f.onChange(opt.value)}
                    >
                      <span className="fleet-toolbar-popover-option-check">
                        {f.value === opt.value ? <Check size={13} strokeWidth={2} /> : null}
                      </span>
                      {opt.label}
                    </button>
                  ))}
                </div>
              ))}
              {hasSort && (
                <div className="fleet-toolbar-popover-group">
                  <div className="fleet-toolbar-popover-label">Sort by</div>
                  {sortOptions!.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      className={`fleet-toolbar-popover-option${sortValue === opt.value ? " is-selected" : ""}`}
                      onClick={() => {
                        onSortChange?.(opt.value);
                        setOpen(false);
                      }}
                    >
                      <span className="fleet-toolbar-popover-option-check">
                        {sortValue === opt.value ? <Check size={13} strokeWidth={2} /> : null}
                      </span>
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {onTogglePanel && (
        <button
          type="button"
          className={`fleet-icon-btn${panelOpen ? " is-active" : ""}`}
          onClick={onTogglePanel}
          aria-label="Properties"
          aria-pressed={panelOpen}
          title="Properties"
        >
          {panelOpen ? <PanelRightClose size={16} strokeWidth={1.75} /> : <PanelRightOpen size={16} strokeWidth={1.75} />}
        </button>
      )}
    </div>
  );
}
