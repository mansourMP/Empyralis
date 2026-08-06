"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { ChevronRight } from "lucide-react";

/**
 * One category row from claude_agent_sdk's ContextUsageResponse.categories
 * (see venv/.../claude_agent_sdk/types.py — the same shape the CLI's own
 * `/context` command renders). `color` is the CLI's own swatch for this
 * category — used verbatim, never re-derived, so the rail can never drift
 * from what `/context` itself would show for the same turn. `isDeferred`
 * marks a category whose tokens are reserved but not currently loaded into
 * the window (e.g. MCP tool schemas not yet pulled in) — real usage, but
 * not counted toward `percentage`/`totalTokens`, so it renders separately
 * from the active breakdown below rather than inflating it.
 */
export interface ContextUsageCategory {
  name: string;
  tokens: number;
  color: string;
  isDeferred?: boolean;
}

/** One entry in ContextUsageResponse's memoryFiles/mcpTools/agents lists —
 *  loosely typed on the SDK's own side (`list[dict[str, Any]]`), so this
 *  reads defensively across whichever of the plausible field names a given
 *  list actually uses rather than assuming one fixed shape. */
export type ContextUsageDetailItem = Record<string, unknown>;

/**
 * The subset of claude_agent_sdk.ContextUsageResponse this rail renders.
 * Reused directly from the backend's "final" turn payload — nothing here
 * is recomputed or estimated client-side except the derived "free space"
 * remainder (maxTokens − totalTokens, both real fields). memoryFiles/
 * mcpTools/agents back the popover's expandable per-item detail rows when
 * present; gridRows rides along unread.
 */
export interface ContextUsagePayload {
  categories: ContextUsageCategory[];
  totalTokens: number;
  maxTokens: number;
  percentage: number;
  model?: string;
  memoryFiles?: ContextUsageDetailItem[];
  mcpTools?: ContextUsageDetailItem[];
  agents?: ContextUsageDetailItem[];
  [key: string]: unknown;
}

function formatTokenCount(value: unknown): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return n.toLocaleString();
}

/** "628.3k" / "1.0M" / raw integers under 1000 ("158") — matches the
 *  abbreviation style the founder's own Claude-app reference uses, and
 *  keeps every numeric column narrow enough for the category rows to stay
 *  on one line. `font-variant-numeric: tabular-nums` (fleet-theme.css)
 *  keeps digits aligned across rows regardless of magnitude. */
function formatAbbreviatedTokens(value: unknown): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(Math.round(n));
}

function isFreeSpaceCategoryName(name: string): boolean {
  return name.trim().toLowerCase() === "free space";
}

/** Best-effort label/token readers for a detail-list item (memoryFiles/
 *  mcpTools/agents) — field names aren't a fixed contract on the SDK side
 *  (`list[dict[str, Any]]`), so each reads a few plausible keys rather than
 *  assuming one. Falls back to a generic label, never blank. */
function detailItemLabel(item: ContextUsageDetailItem): string {
  const candidate = item.name ?? item.path ?? item.agentType ?? item.serverName ?? item.type;
  const text = typeof candidate === "string" ? candidate.trim() : "";
  return text || "Item";
}
function detailItemTokens(item: ContextUsageDetailItem): number {
  const candidate = item.tokens ?? item.tokenCount ?? item.token_count;
  return typeof candidate === "number" && Number.isFinite(candidate) ? candidate : 0;
}

type DetailListKey = "mcpTools" | "memoryFiles" | "agents";
const DETAIL_LIST_LABELS: Record<DetailListKey, string> = {
  mcpTools: "MCP tools",
  memoryFiles: "Memory files",
  agents: "Custom agents",
};

/**
 * The trigger's visible face: a small circular progress ring (SVG
 * stroke-dasharray fill), reading like the CLI/desktop app's own `/context`
 * indicator. Neutral stroke color, never --accent — this control is not
 * the row's primary action (Send is), per the one-accent-per-view rule.
 * Fill width is a plain CSS custom property + transition (not JS-animated),
 * so it's automatically caught by the existing `.fleet-root *` reduced-
 * motion override (fleet-theme.css) with no extra media query here.
 *
 * `percentage` is 0 both for "really at 0%" and for "no data yet" — at
 * zero the fill's stroke-dashoffset equals the full circumference, so it
 * simply doesn't draw, leaving only the neutral track. That IS the "empty"
 * state the founder asked for: a real, always-present ring that fills once
 * real data exists, never a fabricated number in between.
 */
function ContextUsageRing({ percentage }: { percentage: number }) {
  const size = 16;
  const stroke = 2;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;

  return (
    <svg
      className="fleet-context-usage-ring"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-hidden="true"
      focusable="false"
    >
      <circle
        className="fleet-context-usage-ring-track"
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        strokeWidth={stroke}
      />
      <circle
        className="fleet-context-usage-ring-fill"
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - percentage / 100)}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}

/** One expandable detail section (MCP tools / Memory files / Custom
 *  agents) — a disclosure row (chevron, label, total tokens, item count)
 *  that opens into the individual items, each with its own token count.
 *  Only rendered by the caller when the backend actually sent a non-empty
 *  list for this key — no invented sub-rows for data that isn't there. */
function ContextUsageDetailSection({
  listKey,
  items,
  expanded,
  onToggle,
}: {
  listKey: DetailListKey;
  items: ContextUsageDetailItem[];
  expanded: boolean;
  onToggle: () => void;
}) {
  const totalTokens = items.reduce((sum, item) => sum + detailItemTokens(item), 0);
  return (
    <div className="fleet-context-usage-detail">
      <button
        type="button"
        className="fleet-context-usage-detail-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <ChevronRight
          size={12}
          strokeWidth={2}
          className={`fleet-context-usage-detail-chevron${expanded ? " is-expanded" : ""}`}
        />
        <span className="fleet-context-usage-detail-label">{DETAIL_LIST_LABELS[listKey]}</span>
        <span className="fleet-context-usage-detail-tokens">{formatAbbreviatedTokens(totalTokens)}</span>
        <span className="fleet-context-usage-detail-count">{items.length}</span>
      </button>
      {expanded && (
        <ul className="fleet-context-usage-detail-items">
          {items.map((item, i) => (
            <li key={i} className="fleet-context-usage-detail-item">
              <span className="fleet-context-usage-detail-item-name">{detailItemLabel(item)}</span>
              <span className="fleet-context-usage-detail-item-tokens">{formatAbbreviatedTokens(detailItemTokens(item))}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Compact context-usage meter, seated directly in the composer's control
 * row (attach / model / reasoning effort / THIS / send). Always visible
 * (2026-08-07: previously rendered `null` while `contextUsage` was absent,
 * which made it appear and disappear between turns — the founder called
 * this out specifically, contrasting it with Claude's own context panel,
 * which is always present). The ring shows a neutral, empty track before
 * any data exists and fills once a claude_agent_sdk-engine turn completes
 * — never a fabricated percentage in between; see ContextUsageRing's own
 * docstring for how the zero state falls out of the same rendering path
 * rather than a separate branch.
 *
 * The popover CONTENT below is the founder's second, separate ask —
 * "as professional as inside Claude" — matching his own reference:
 * a header with abbreviated used/total/percentage, a segmented bar, one
 * row per category (colour square, name, tokens, percentage), muted
 * deferred rows with an em-dash instead of a percentage that wouldn't
 * mean anything yet, and — when the backend's own memoryFiles/mcpTools/
 * agents lists are non-empty — expandable per-item detail sections. Every
 * number here is real backend data or a derived remainder of real fields
 * (free space = maxTokens − totalTokens); nothing is invented to match the
 * reference's specific numbers.
 *
 * `agentInstallId`, when set, means this chat belongs to one specific Fleet
 * agent — the "Full model editor" link below points at its Model tab.
 * Omitted for Sage's own workspace-wide chat, which has no per-agent Model
 * tab to link to.
 */
export function ContextUsageRail({
  contextUsage,
  agentInstallId,
}: {
  contextUsage: ContextUsagePayload | null;
  agentInstallId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [expandedDetails, setExpandedDetails] = useState<Set<DetailListKey>>(() => new Set());
  const pathname = usePathname();
  const modelHref = agentInstallId && pathname ? pathname.replace(/\/[^/]+$/, "/model") : null;
  const ref = useRef<HTMLDivElement | null>(null);

  // Same dismissal contract as every other anchored popover in Fleet
  // (FleetToolbar's filter/sort popover, AgentModelPickerRow, etc.):
  // outside pointerdown, or Escape.
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

  function toggleDetail(key: DetailListKey) {
    setExpandedDetails((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const hasData = Boolean(contextUsage);
  const percentage = contextUsage ? Math.max(0, Math.min(100, Number(contextUsage.percentage) || 0)) : 0;
  const totalTokens = contextUsage ? Number(contextUsage.totalTokens) || 0 : 0;
  const maxTokens = contextUsage ? Number(contextUsage.maxTokens) || 0 : 0;
  const freeTokens = Math.max(0, maxTokens - totalTokens);
  const freePercentage = maxTokens > 0 ? (freeTokens / maxTokens) * 100 : 0;

  const allCategories = contextUsage && Array.isArray(contextUsage.categories) ? contextUsage.categories : [];
  const activeCategories = allCategories.filter((c) => !c.isDeferred && !isFreeSpaceCategoryName(c.name));
  const deferredCategories = allCategories.filter((c) => c.isDeferred);

  const detailLists: { key: DetailListKey; items: ContextUsageDetailItem[] }[] = contextUsage
    ? (["mcpTools", "memoryFiles", "agents"] as DetailListKey[])
      .map((key) => ({ key, items: Array.isArray(contextUsage[key]) ? (contextUsage[key] as ContextUsageDetailItem[]) : [] }))
      .filter((entry) => entry.items.length > 0)
    : [];

  return (
    <div className="fleet-context-usage-inline" ref={ref}>
      <button
        type="button"
        className={`fleet-context-usage-inline-trigger${open ? " is-active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={hasData ? `Context usage: ${percentage.toFixed(0)}% used` : "Context usage: not available yet"}
        title={hasData ? `Context used: ${percentage.toFixed(0)}%` : "Context usage — not available yet"}
      >
        <ContextUsageRing percentage={percentage} />
      </button>
      {open && (
        <div className="fleet-toolbar-popover fleet-context-usage-panel" role="dialog" aria-label="Context usage">
          {!hasData ? (
            <>
              <div className="fleet-context-usage-panel-header">
                <span className="fleet-context-usage-panel-title">Context window</span>
              </div>
              <p className="fleet-context-usage-empty">
                Not available yet — shown after this agent's next reply.
              </p>
            </>
          ) : (
            <>
              <button
                type="button"
                className="fleet-context-usage-header"
                onClick={() => setOpen(false)}
                aria-label="Collapse context window"
              >
                <span className="fleet-context-usage-header-label">Context window</span>
                <span className="fleet-context-usage-header-value">
                  {formatAbbreviatedTokens(totalTokens)} / {formatAbbreviatedTokens(maxTokens)} ({percentage.toFixed(0)}%)
                </span>
              </button>

              {contextUsage?.model && (
                <div className="fleet-context-usage-model">{contextUsage.model}</div>
              )}

              <div
                className="fleet-context-usage-segmented-bar"
                role="meter"
                aria-valuenow={Math.round(percentage)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="Context window used"
              >
                {activeCategories.map((cat) => (
                  <span
                    key={cat.name}
                    className="fleet-context-usage-segment"
                    style={{ width: `${maxTokens > 0 ? (cat.tokens / maxTokens) * 100 : 0}%`, background: cat.color || "var(--text-muted)" }}
                  />
                ))}
                <span
                  className="fleet-context-usage-segment fleet-context-usage-segment--free"
                  style={{ width: `${freePercentage}%` }}
                />
              </div>

              <ul className="fleet-context-usage-categories">
                {activeCategories.map((cat) => (
                  <li key={cat.name} className="fleet-context-usage-category">
                    <span className="fleet-context-usage-category-swatch" style={{ background: cat.color || "var(--text-muted)" }} />
                    <span className="fleet-context-usage-category-name">{cat.name}</span>
                    <span className="fleet-context-usage-category-tokens">{formatAbbreviatedTokens(cat.tokens)}</span>
                    <span className="fleet-context-usage-category-pct">
                      {maxTokens > 0 ? `${((cat.tokens / maxTokens) * 100).toFixed(1)}%` : "0.0%"}
                    </span>
                  </li>
                ))}
                <li className="fleet-context-usage-category fleet-context-usage-category--free">
                  <span className="fleet-context-usage-category-swatch fleet-context-usage-category-swatch--free" />
                  <span className="fleet-context-usage-category-name">Free space</span>
                  <span className="fleet-context-usage-category-tokens">{formatAbbreviatedTokens(freeTokens)}</span>
                  <span className="fleet-context-usage-category-pct">{freePercentage.toFixed(1)}%</span>
                </li>
                {deferredCategories.map((cat) => (
                  <li key={cat.name} className="fleet-context-usage-category fleet-context-usage-category--deferred">
                    <span className="fleet-context-usage-category-swatch fleet-context-usage-category-swatch--deferred" />
                    <span className="fleet-context-usage-category-name">{cat.name} (deferred)</span>
                    <span className="fleet-context-usage-category-tokens">{formatAbbreviatedTokens(cat.tokens)}</span>
                    <span className="fleet-context-usage-category-pct">—</span>
                  </li>
                ))}
              </ul>

              {detailLists.length > 0 && (
                <div className="fleet-context-usage-details">
                  {detailLists.map(({ key, items }) => (
                    <ContextUsageDetailSection
                      key={key}
                      listKey={key}
                      items={items}
                      expanded={expandedDetails.has(key)}
                      onToggle={() => toggleDetail(key)}
                    />
                  ))}
                </div>
              )}
            </>
          )}

          {modelHref && (
            <Link href={modelHref} className="fleet-context-usage-reasoning-link">
              Full model editor
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
