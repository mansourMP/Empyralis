"use client";

import { useEffect, useRef, useState } from "react";
import { BarChart3, Check, PanelRightClose, PanelRightOpen, SlidersHorizontal } from "lucide-react";

import { UsagePopover } from "./UsagePopover";

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
 *
 * All three slots act IN PLACE. Usage used to be the odd one out — a plain
 * link that navigated the reader off the list entirely — and is now a small
 * anchored popover (UsagePopover) like its neighbours. The two popovers
 * share one open-state so they can't both be up at once, and one dismissal
 * contract: outside pointerdown, or Esc.
 */
export function FleetToolbar({
  filters,
  sortOptions,
  sortValue,
  sortDefault = "",
  onSortChange,
  panelOpen,
  onTogglePanel,
  usageWorkspaceId,
}: {
  filters?: ToolbarFilter[];
  sortOptions?: ToolbarOption[];
  sortValue?: string;
  sortDefault?: string;
  onSortChange?: (value: string) => void;
  /** Renders the panel-toggle icon-button LAST in the cluster (see the slot
   *  order above), mirroring the caller's own FleetRightPanel `open` state.
   *  Omit on pages with no properties drawer. */
  panelOpen?: boolean;
  onTogglePanel?: () => void;
  /** Renders a Usage icon-button FIRST in the cluster, opening a compact
   *  spend summary for this workspace — same popover, same icon, every list
   *  page. Omit on surfaces with no workspace context. */
  usageWorkspaceId?: string;
}) {
  const [open, setOpen] = useState<"usage" | "controls" | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(null);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(null);
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

  if (!hasControls && !onTogglePanel && !usageWorkspaceId) return null;

  return (
    <div className="fleet-toolbar-actions" ref={ref}>
      {usageWorkspaceId && (
        <>
          <button
            type="button"
            className={`fleet-icon-btn${open === "usage" ? " is-active" : ""}`}
            aria-label="Usage"
            aria-expanded={open === "usage"}
            title="Usage"
            onClick={() => setOpen((v) => (v === "usage" ? null : "usage"))}
          >
            <BarChart3 size={16} strokeWidth={1.75} />
          </button>
          {open === "usage" && <UsagePopover workspaceId={usageWorkspaceId} />}
        </>
      )}

      {hasControls && (
        <>
          <button
            type="button"
            className={`fleet-icon-btn${controlsActive || open === "controls" ? " is-active" : ""}`}
            aria-label="Filter and sort"
            aria-expanded={open === "controls"}
            title="Filter and sort"
            onClick={() => setOpen((v) => (v === "controls" ? null : "controls"))}
          >
            <SlidersHorizontal size={16} strokeWidth={1.75} />
          </button>
          {open === "controls" && (
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
                        setOpen(null);
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
