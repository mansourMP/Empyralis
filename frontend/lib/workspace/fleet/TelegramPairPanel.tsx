"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useState } from "react";
import { Check, MessageCircle } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

type PairStatus = {
  configured: boolean;
  has_pending_code: boolean;
  paired: boolean;
};

type PairStart = {
  pairing_code: string;
  deep_link: string | null;
  bot_username: string | null;
};

/**
 * First-run pairing panel: shows a single "Pair Telegram" CTA and, after
 * pairing starts, the 6-digit code + a deep link to the hosted bot. Polls
 * status every 5s until the pairing completes, then the panel unmounts.
 */
export function TelegramPairPanel({ workspaceId }: { workspaceId: string }) {
  const [status, setStatus] = useState<PairStatus | null>(null);
  const [pair, setPair] = useState<PairStart | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fleetAuthorizedFetch(
        `/api/sage/telegram-hosted/pair/status?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: "include" },
      );
      if (!res.ok) return;
      const data: PairStatus = await res.json();
      setStatus(data);
      if (data.paired) setPair(null);
    } catch {
      /* transient — will retry on next poll */
    }
  }, [workspaceId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll only while a pairing is pending — quiet otherwise.
  useEffect(() => {
    if (!status?.has_pending_code && !pair) return;
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [status?.has_pending_code, pair, refresh]);

  const startPairing = useCallback(async () => {
    setStarting(true);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch("/api/sage/telegram-hosted/pair/start", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ workspace_id: workspaceId }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const data: PairStart = await res.json();
      setPair(data);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start pairing");
    } finally {
      setStarting(false);
    }
  }, [workspaceId, refresh]);

  if (!status) return null;
  if (!status.configured) return null;
  if (status.paired) return null;

  return (
    <section className="fleet-pair-panel" aria-label="Pair Telegram">
      <div className="fleet-pair-icon">
        <MessageCircle size={18} strokeWidth={1.75} />
      </div>
      <div className="fleet-pair-body">
        <div className="fleet-pair-title">Talk to your agent on Telegram</div>
        <div className="fleet-pair-desc">
          Pair the hosted bot — no BotFather, no token. Send your first
          message and your AI replies from your workspace.
        </div>
        {pair && (
          <div className="fleet-pair-details">
            {/* The backend may return either a short, typeable numeric code
                or (when a pairing session already exists) the long deep-link
                token in the same field. Only render the CODE box when it's
                actually short — otherwise the deep-link button is the only
                usable action. */}
            {pair.pairing_code.length <= 10 && (
              <div className="fleet-pair-code">
                <span className="fleet-pair-code-label">Code</span>
                <span className="fleet-pair-code-value">{pair.pairing_code}</span>
              </div>
            )}
            {pair.deep_link && pair.bot_username && (
              <a
                className="fleet-btn fleet-btn--accent"
                href={pair.deep_link}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open @{pair.bot_username}
              </a>
            )}
          </div>
        )}
        {error && <div className="fleet-pair-error">{error}</div>}
      </div>
      {!pair && (
        <button
          type="button"
          className="fleet-btn fleet-btn--accent"
          onClick={startPairing}
          disabled={starting}
        >
          {starting ? "Starting…" : (
            <>
              <Check size={16} strokeWidth={1.75} />
              Pair Telegram
            </>
          )}
        </button>
      )}
    </section>
  );
}
