"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { Gauge } from "lucide-react";

/**
 * One category row from claude_agent_sdk's ContextUsageResponse.categories
 * (see venv/.../claude_agent_sdk/types.py — the same shape the CLI's own
 * `/context` command renders). `color` is the CLI's own swatch for this
 * category — used verbatim, never re-derived, so the rail can never drift
 * from what `/context` itself would show for the same turn.
 */
export interface ContextUsageCategory {
  name: string;
  tokens: number;
  color: string;
  isDeferred?: boolean;
}

/**
 * The subset of claude_agent_sdk.ContextUsageResponse this rail renders.
 * Reused directly from the backend's "final" turn payload — nothing here
 * is recomputed or estimated client-side. Extra fields (memoryFiles,
 * mcpTools, agents, gridRows, ...) ride along on the object but are not
 * read by this component.
 */
export interface ContextUsagePayload {
  categories: ContextUsageCategory[];
  totalTokens: number;
  maxTokens: number;
  percentage: number;
  model?: string;
  [key: string]: unknown;
}

function formatTokenCount(value: unknown): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return n.toLocaleString();
}

/**
 * Compact context-usage meter, seated directly in the composer's control
 * row (attach / model / reasoning effort / THIS / send) — re-seated here
 * from a detached right-edge column it used to occupy on its own, per the
 * founder's composer-consolidation call: everything about the turn you're
 * about to send lives in one control row under the input, not scattered
 * around the message list. Same data source as before (the last completed
 * claude_agent_sdk turn's context_usage), same "not available" honesty for
 * every other engine/mode or before any turn has completed — this only
 * changes WHERE it renders and how it opens (an anchored popover here,
 * instead of a slide-out panel next to a permanent toggle strip).
 *
 * `agentInstallId`, when set, means this chat belongs to one specific Fleet
 * agent, which already has its own reasoning-effort control in this same
 * composer row (see AgentChat.tsx) — the "Change reasoning effort" link
 * below still points at the Model tab as a fallback for anything that
 * control doesn't cover (byok_api/cli_subscription's fuller editor).
 * Omitted for Sage's own workspace-wide chat, which has no per-agent Model
 * tab to link to.
 *
 * No dead controls (CLAUDE.md): renders nothing at all until `contextUsage`
 * is real data. A bare gauge icon with no percentage next to it — the
 * previous behavior — reads as a broken control, indistinguishable from a
 * rendering bug, not as "no data yet." This is a genuinely common state:
 * context_usage is never persisted with the turn (get_context_usage() rides
 * only on that one turn's live SSE "final" event — see AgentChat.tsx's own
 * contextUsage docstring), so it starts null on every fresh page load/thread
 * open and stays null until a claude_agent_sdk-engine turn completes in
 * *this* render. Once one has, the meter appears with a real number and
 * keeps showing the last completed turn's reading (never resets to null on
 * a new send) — same as before, just no longer visible while there is
 * nothing to show.
 */
export function ContextUsageRail({
  contextUsage,
  agentInstallId,
}: {
  contextUsage: ContextUsagePayload | null;
  agentInstallId?: string;
}) {
  const [open, setOpen] = useState(false);
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

  if (!contextUsage) return null;

  const categories = Array.isArray(contextUsage.categories) ? contextUsage.categories : [];
  const percentage = Math.max(0, Math.min(100, Number(contextUsage.percentage) || 0));

  return (
    <div className="fleet-context-usage-inline" ref={ref}>
      <button
        type="button"
        className={`fleet-context-usage-inline-trigger${open ? " is-active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Context usage"
        title={`Context used: ${percentage.toFixed(0)}%`}
      >
        <Gauge size={13} strokeWidth={1.75} />
        <span className="fleet-context-usage-inline-pct">{Math.round(percentage)}%</span>
      </button>
      {open && (
        <div className="fleet-toolbar-popover fleet-context-usage-panel" role="dialog" aria-label="Context usage">
          <div className="fleet-context-usage-panel-header">
            <span className="fleet-context-usage-panel-title">Context usage</span>
          </div>

          {contextUsage.model && (
            <div className="fleet-context-usage-model">{contextUsage.model}</div>
          )}
          <div
            className="fleet-context-usage-meter"
            role="meter"
            aria-valuenow={Math.round(percentage)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Context window used"
          >
            <div className="fleet-context-usage-meter-fill" style={{ width: `${percentage}%` }} />
          </div>
          <div className="fleet-context-usage-totals">
            {formatTokenCount(contextUsage.totalTokens)} / {formatTokenCount(contextUsage.maxTokens)} tokens
            <span className="fleet-context-usage-pct"> · {percentage.toFixed(1)}%</span>
          </div>

          {categories.length > 0 && (
            <ul className="fleet-context-usage-categories">
              {categories.map((cat) => (
                <li key={cat.name} className="fleet-context-usage-category">
                  <span
                    className="fleet-context-usage-category-swatch"
                    style={{ background: cat.color || "var(--text-muted)" }}
                  />
                  <span className="fleet-context-usage-category-name">{cat.name}</span>
                  <span className="fleet-context-usage-category-tokens">
                    {formatTokenCount(cat.tokens)}
                  </span>
                </li>
              ))}
            </ul>
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
