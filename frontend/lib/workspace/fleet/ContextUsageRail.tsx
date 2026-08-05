"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { Gauge, X } from "lucide-react";

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
 * Collapsible right-side rail showing the current model and a context-
 * window usage breakdown for THIS conversation's last claude_agent_sdk
 * turn. Closed by default — a single toggle button is the only thing
 * rendered until a person opens it, so it never competes with the compose
 * area for attention (this product's target user is explicitly a
 * non-developer who has no reason to see token accounting by default).
 *
 * `contextUsage` is null for every turn that didn't run on the
 * claude_agent_sdk engine (the legacy tool loop, gateway_brain local/
 * cli_subscription modes) or before any turn has completed yet — the panel
 * says so in plain language rather than rendering a zeroed-out fake chart.
 *
 * `agentInstallId`, when set, means this chat belongs to one specific Fleet
 * agent, which already has its own reasoning-effort picker on its Model tab
 * (FleetAgentDetail.tsx's renderCliReasoningEffortPicker /
 * renderReasoningEffortPicker). Rather than building a second control here,
 * the rail links there — the same modelHref pattern the Chat tab's own
 * toolbar already uses for its read-only model chip. Omitted for Sage's own
 * workspace-wide chat, which has no per-agent Model tab to link to.
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

  const categories = Array.isArray(contextUsage?.categories) ? contextUsage!.categories : [];
  const percentage = Math.max(0, Math.min(100, Number(contextUsage?.percentage) || 0));

  return (
    <div className={`fleet-context-usage-rail${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="fleet-context-usage-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={open ? "Hide context usage" : "Show context usage"}
        title={open ? "Hide context usage" : "Show context usage"}
      >
        <Gauge size={15} strokeWidth={1.75} />
      </button>
      {open && (
        <div className="fleet-context-usage-panel" role="region" aria-label="Context usage">
          <div className="fleet-context-usage-panel-header">
            <span className="fleet-context-usage-panel-title">Context usage</span>
            <button
              type="button"
              className="fleet-context-usage-close"
              onClick={() => setOpen(false)}
              aria-label="Close context usage"
            >
              <X size={14} strokeWidth={1.75} />
            </button>
          </div>

          {!contextUsage ? (
            <p className="fleet-context-usage-empty">
              Not available for this conversation's engine yet.
            </p>
          ) : (
            <>
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
                  Change reasoning effort
                </Link>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
