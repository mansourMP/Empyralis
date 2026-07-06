"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Filter as FilterIcon, PanelRight, SlidersHorizontal } from "lucide-react";

export type ToolbarTab = { id: string; label: string };
export type ToolbarOption = { value: string; label: string };
export type ToolbarFilter = {
  key: string;
  label: string;
  value: string;
  options: ToolbarOption[];
  onChange: (value: string) => void;
};

/**
 * The thin control row every list/detail page gets: optional view tabs on the
 * left, a quiet right-aligned icon-button cluster on the right (Filter, Sort/
 * Display, and an optional right-panel toggle). Linear's exact treatment —
 * small ghost buttons, quiet hover, accent reserved for an open menu or an
 * actually-applied filter/sort. Any prop group left out (no filters, no sort,
 * no onTogglePanel) simply doesn't render that button — no dead controls.
 */
export function FleetToolbar({
  tabs,
  activeTab,
  onTabChange,
  filters,
  sortOptions,
  sortValue,
  sortDefault = "",
  onSortChange,
  panelOpen,
  onTogglePanel,
}: {
  tabs?: ToolbarTab[];
  activeTab?: string;
  onTabChange?: (id: string) => void;
  filters?: ToolbarFilter[];
  sortOptions?: ToolbarOption[];
  sortValue?: string;
  sortDefault?: string;
  onSortChange?: (value: string) => void;
  panelOpen?: boolean;
  onTogglePanel?: () => void;
}) {
  const [openMenu, setOpenMenu] = useState<"filter" | "sort" | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!openMenu) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpenMenu(null);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenMenu(null);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [openMenu]);

  const filterActive = (filters || []).some((f) => f.value && f.value !== "all");
  const sortActive = Boolean(sortValue && sortValue !== sortDefault);
  const hasTabs = Boolean(tabs && tabs.length > 0);
  const hasActions = Boolean((filters && filters.length > 0) || (sortOptions && sortOptions.length > 0) || onTogglePanel);

  if (!hasTabs && !hasActions) return null;

  return (
    <div className="fleet-toolbar-row">
      {hasTabs ? (
        <div className="fleet-toolbar-tabs">
          {tabs!.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`fleet-toolbar-tab${activeTab === t.id ? " is-active" : ""}`}
              onClick={() => onTabChange?.(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      ) : <span />}

      <div className="fleet-toolbar-actions" ref={ref}>
        {filters && filters.length > 0 && (
          <>
            <button
              type="button"
              className={`fleet-icon-btn${filterActive || openMenu === "filter" ? " is-active" : ""}`}
              aria-label="Filter"
              title="Filter"
              onClick={() => setOpenMenu((m) => (m === "filter" ? null : "filter"))}
            >
              <FilterIcon size={16} strokeWidth={1.75} />
            </button>
            {openMenu === "filter" && (
              <div className="fleet-toolbar-popover">
                {filters.map((f) => (
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
              </div>
            )}
          </>
        )}

        {sortOptions && sortOptions.length > 0 && (
          <>
            <button
              type="button"
              className={`fleet-icon-btn${sortActive || openMenu === "sort" ? " is-active" : ""}`}
              aria-label="Sort and display"
              title="Sort and display"
              onClick={() => setOpenMenu((m) => (m === "sort" ? null : "sort"))}
            >
              <SlidersHorizontal size={16} strokeWidth={1.75} />
            </button>
            {openMenu === "sort" && (
              <div className="fleet-toolbar-popover">
                <div className="fleet-toolbar-popover-group">
                  <div className="fleet-toolbar-popover-label">Sort by</div>
                  {sortOptions.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      className={`fleet-toolbar-popover-option${sortValue === opt.value ? " is-selected" : ""}`}
                      onClick={() => {
                        onSortChange?.(opt.value);
                        setOpenMenu(null);
                      }}
                    >
                      <span className="fleet-toolbar-popover-option-check">
                        {sortValue === opt.value ? <Check size={13} strokeWidth={2} /> : null}
                      </span>
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {onTogglePanel && (
          <button
            type="button"
            className={`fleet-icon-btn${panelOpen ? " is-active" : ""}`}
            aria-label="Toggle panel"
            title="Toggle panel"
            onClick={onTogglePanel}
          >
            <PanelRight size={16} strokeWidth={1.75} />
          </button>
        )}
      </div>
    </div>
  );
}
