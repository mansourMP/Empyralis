"use client";

import { useEffect, useRef, useState } from "react";
import { Activity } from "lucide-react";

import {
  connectionPresentation,
  gatewayId,
  gatewayLabel,
  useWorkspaceGateways,
} from "./gateway-box-picker";
import { StatusDot } from "./fleet-indicators";
import type { AgentStatusTone } from "./fleet-presentation";

/** Real snapshot of the platform's shared hosted-Telegram-bot 401 circuit
 *  breaker (server_modules/sage_telegram_hosted_service.py — `_trip_circuit_
 *  breaker`/`_clear_circuit_breaker`/`hosted_bot_auth_status()`), surfaced
 *  through the existing, already-shipped `GET /sage/telegram-hosted/pair/
 *  status` endpoint (owner-role gated, workspace-scoped). This is the one
 *  self-healing mechanism in the codebase today (per docs/design/reliability
 *  -audit-*.md) with a queryable CURRENT-state signal — not a history log,
 *  just "is the breaker open right now." See the note rendered below the
 *  list for why nothing here claims a "last N hours" count. */
type TelegramBreakerStatus = {
  configured?: boolean;
  paired?: boolean;
  suspended?: boolean;
  needs_reauth?: boolean;
};

function useTelegramBreakerStatus(workspaceId: string, enabled: boolean): TelegramBreakerStatus | null {
  const [status, setStatus] = useState<TelegramBreakerStatus | null>(null);
  useEffect(() => {
    if (!enabled || !workspaceId) return;
    let cancelled = false;
    fetch(`/api/sage/telegram-hosted/pair/status?workspace_id=${encodeURIComponent(workspaceId)}`, {
      credentials: "include",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) setStatus(data);
      })
      .catch(() => {
        // Silent — this row is additive context, not load-bearing; the
        // computers list above is the primary content and renders regardless.
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, enabled]);
  return status;
}

function relativeHeartbeatAge(seconds: number | null | undefined): string {
  if (seconds == null) return "no heartbeat yet";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

/**
 * System health — MAN-107. One reachable place showing (1) whether paired
 * computers are actually up, (2) per-computer status + last heartbeat, and
 * (3) the one self-healing signal the backend can currently answer honestly
 * (the hosted Telegram bot's 401 circuit breaker — real, queryable, current-
 * state only). Deliberately does NOT show a "self-heal events in the last
 * 24h" count or an invented health score: no part of the backend persists a
 * self-heal event history today (BYO key-rotation cooldowns and the CLI
 * auth-expired recovery retry are both real and live — see cli-runner.ts and
 * credential_rotation_service.py — but neither emits anything queryable
 * beyond current in-memory state). The note at the bottom says so instead of
 * faking a number.
 *
 * Placed in .fleet-rail-controls next to Bug report — the rail's remaining
 * home for a control that has to stay visible at all times, unlike the
 * theme toggle and Shortcuts, which moved into the account menu popover
 * (2026-08) as a set-once/looked-up-occasionally pair.
 */
export function SystemHealthButton({ workspaceId }: { workspaceId: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const { gateways, loading } = useWorkspaceGateways(workspaceId);
  const telegramBreaker = useTelegramBreakerStatus(workspaceId, open);

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

  const total = gateways.length;
  let problems = 0;
  for (const g of gateways) {
    const tone = connectionPresentation(g).tone;
    if (tone !== "online") problems += 1;
  }
  const overallTone: AgentStatusTone = total === 0 ? "unknown" : problems === 0 ? "online" : "degraded";
  const overallLabel =
    total === 0
      ? "No paired computers yet"
      : problems === 0
        ? `All ${total} computer${total === 1 ? "" : "s"} online`
        : `${problems} of ${total} computer${total === 1 ? "" : "s"} need attention`;

  const showTelegramBreaker = Boolean(telegramBreaker?.configured && telegramBreaker?.paired);

  return (
    <div ref={ref} className="fleet-health-float">
      {open && (
        <div className="fleet-health-popover" role="dialog" aria-label="System health">
          <div className="fleet-health-popover-title">
            <StatusDot tone={overallTone} size={7} />
            <span>{overallLabel}</span>
          </div>

          <div className="fleet-health-section-label">Computers</div>
          {loading ? (
            <p className="fleet-health-note">Loading…</p>
          ) : total === 0 ? (
            <p className="fleet-health-note">Connect a computer on the Hardware tab to see its status here.</p>
          ) : (
            <div className="fleet-health-rows">
              {gateways.map((g) => {
                const presentation = connectionPresentation(g);
                return (
                  <div key={gatewayId(g)} className="fleet-health-row">
                    <StatusDot tone={presentation.tone} size={7} />
                    <span className="fleet-health-row-label">{gatewayLabel(g)}</span>
                    <span className="fleet-health-row-meta">
                      {presentation.label} · {relativeHeartbeatAge(g.heartbeat_age_seconds)}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          <div className="fleet-health-section-label">Self-healing</div>
          <div className="fleet-health-rows">
            {showTelegramBreaker && (
              <div className="fleet-health-row">
                <StatusDot tone={telegramBreaker!.suspended ? "offline" : "online"} size={7} />
                <span className="fleet-health-row-label">Telegram bot</span>
                <span className="fleet-health-row-meta">
                  {telegramBreaker!.suspended
                    ? "Circuit breaker tripped — needs a valid token"
                    : "Circuit breaker OK"}
                </span>
              </div>
            )}
            <p className="fleet-health-note">
              Reconnect backoff, BYO-key rotation on rate-limit, and CLI subscription auth-recovery
              all run automatically in the background.{" "}
              {showTelegramBreaker
                ? "Only the Telegram breaker's current state is shown"
                : "No live breaker is relevant to this workspace right now"}
              — a historical self-heal event log isn&apos;t tracked yet, so this can&apos;t show a
              count over time.
            </p>
          </div>
        </div>
      )}
      <button
        type="button"
        className={`fleet-rail-control-btn${open ? " is-active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="System health"
        aria-label="System health"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <Activity size={16} strokeWidth={1.75} />
      </button>
    </div>
  );
}
