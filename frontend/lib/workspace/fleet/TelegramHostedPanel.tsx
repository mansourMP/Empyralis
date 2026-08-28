"use client";

/**
 * The setup form behind Telegram's "Empyralis bot" door — the zero-friction
 * path ("no BotFather, no token") that had been built in the backend and
 * reachable from nothing since TelegramPairPanel.tsx was deleted.
 *
 * All of the judgement lives in telegram-hosted-pairing.ts (pure, tested);
 * this file is the rendering and the fetches. Read that module's header first
 * — especially the part about the hosted bot answering as SAGE rather than as
 * the agent whose tab this is rendered on.
 *
 * BACKEND CONTRACT (verified file:line, not assumed):
 *   POST   /sage/telegram-hosted/pair/start   routes_sage_telegram_hosted.py:58
 *          owner-only; 503 when the deployment has no hosted bot token.
 *   GET    /sage/telegram-hosted/pair/status  routes_sage_telegram_hosted.py:97
 *          owner-only; {configured, has_pending_code, paired, suspended,
 *          needs_reauth}.
 *   DELETE /sage/telegram-hosted/pair         routes_sage_telegram_hosted.py:486
 *          owner-only; {unpaired, removed}.
 *
 * The status poll is the ONLY way this screen learns that someone tapped the
 * link — pairing completes inside Telegram, on the person's phone, with
 * nothing to report back to this tab. So the poll is not a nicety: without it
 * the panel would sit on "waiting" forever after a successful pair.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, ExternalLink, Loader2, RefreshCw, Send, Unlink } from "lucide-react";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";
import { getErrorMessage } from "@/lib/ui/api-error";
import {
  planHostedPairing,
  hostedPairingHeadline,
  type HostedPairingStartResponse,
  type HostedPairingStatusResponse,
} from "@/lib/workspace/fleet/telegram-hosted-pairing";

/** How often to ask "has anyone tapped it yet" while a code/link is live.
 *  Only ever runs while `waiting` — never a standing poll on an idle tab. */
const WAITING_POLL_MS = 3000;

export type HostedTelegramStatus = {
  status: HostedPairingStatusResponse | null;
  statusError: string | null;
  refresh: () => Promise<void>;
};

/**
 * Live hosted-bot status for a workspace.
 *
 * Called UNCONDITIONALLY from ChannelsTab (rules of hooks) and gated by
 * `enabled` — the same shape usePersonalChannelStatus already uses there. Two
 * readers need it before the panel is ever opened: the door face (so a card
 * can say "Connected" without being clicked into) and door availability (so a
 * deployment with no hosted bot renders no dead Connect button).
 */
export function useHostedTelegramStatus(workspaceId: string, enabled: boolean): HostedTelegramStatus {
  const [status, setStatus] = useState<HostedPairingStatusResponse | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled || !workspaceId) return;
    try {
      const res = await fleetAuthorizedFetch(
        `/api/sage/telegram-hosted/pair/status?workspace_id=${encodeURIComponent(workspaceId)}`,
        { method: "GET" },
      );
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        // A 503 here would still be a real answer about the deployment, but
        // this route does not 503 — it reports `configured: false` in a 200.
        // Anything non-OK is genuinely "we could not check", which is a
        // different fact from "not connected" and gets its own screen.
        setStatusError(getErrorMessage(data, "Couldn't check the Telegram connection."));
        return;
      }
      setStatus((data || {}) as HostedPairingStatusResponse);
      setStatusError(null);
    } catch {
      setStatusError("Couldn't reach Empyralis to check the Telegram connection.");
    }
  }, [enabled, workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { status, statusError, refresh };
}

export function TelegramHostedPanel({
  workspaceId,
  status,
  statusError,
  refresh,
  onConnectedChange,
}: {
  workspaceId: string;
  status: HostedPairingStatusResponse | null;
  statusError: string | null;
  refresh: () => Promise<void>;
  /** Fired when the pairing actually flips, so the grid pill and the door face
   *  update without the customer reopening the card. */
  onConnectedChange?: () => void;
}) {
  const [start, setStart] = useState<HostedPairingStartResponse | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const state = planHostedPairing({ status, statusError, start, startedAt, now });

  // ── The poll that notices the tap ────────────────────────────────────────
  // Pairing finishes on the person's phone. Nothing pushes that back here, so
  // while a code/link is live we ask. Stops the moment it settles.
  const waiting = state.kind === "waiting";
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => {
      void refresh();
    }, WAITING_POLL_MS);
    return () => clearInterval(id);
  }, [waiting, refresh]);

  // A 1s tick ONLY while there is a countdown to run. An expiry the customer
  // watches lapse is the difference between "nobody has tapped it yet" and
  // "this can no longer be tapped" — two facts that must never share a screen.
  const hasCountdown = state.kind === "waiting" && state.codeExpiresAt !== null && !state.codeExpired;
  useEffect(() => {
    if (!hasCountdown) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [hasCountdown]);

  // Tell the grid when the pairing flips, so the card pill outside this panel
  // stops disagreeing with the panel inside it.
  const connected = state.kind === "connected";
  const wasConnected = useRef<boolean | null>(null);
  useEffect(() => {
    if (wasConnected.current !== null && wasConnected.current !== connected) {
      onConnectedChange?.();
    }
    wasConnected.current = connected;
  }, [connected, onConnectedChange]);

  async function beginPairing() {
    setBusy(true);
    setActionError(null);
    try {
      const res = await fleetAuthorizedFetch("/api/sage/telegram-hosted/pair/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        // 503 is the deployment saying it has no hosted bot — a different
        // sentence from a generic failure, because no amount of retrying
        // fixes it and the customer's real next step is the other door.
        setActionError(
          res.status === 503
            ? "The Empyralis-run bot isn't set up on this installation. Use “Chatbot” instead, with your own bot from BotFather."
            : getErrorMessage(data, "Couldn't start pairing. Try again."),
        );
        return;
      }
      setStart((data || {}) as HostedPairingStartResponse);
      setStartedAt(Date.now());
    } catch {
      setActionError("Couldn't reach Empyralis to start pairing.");
    } finally {
      setBusy(false);
    }
    // The refresh is its OWN step, deliberately outside the try above: it runs
    // only after the start is known to have succeeded, and its failure must
    // never be reported as the start having failed (CLAUDE.md's outcome-
    // honesty law — the exact shape outcome-honesty-drift.test.ts scans for).
    try {
      await refresh();
    } catch {
      /* best-effort: the poll above will catch up */
    }
  }

  async function disconnect() {
    setBusy(true);
    setActionError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/sage/telegram-hosted/pair?workspace_id=${encodeURIComponent(workspaceId)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setActionError(getErrorMessage(data, "Couldn't disconnect Telegram."));
        return;
      }
      setStart(null);
      setStartedAt(null);
    } catch {
      setActionError("Couldn't reach Empyralis to disconnect Telegram.");
    } finally {
      setBusy(false);
    }
    try {
      await refresh();
    } catch {
      /* best-effort */
    }
  }

  function copyCode(code: string) {
    void navigator.clipboard?.writeText(code).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      },
      () => setActionError("Couldn't copy the code — select it and copy manually."),
    );
  }

  const headline = hostedPairingHeadline(state);

  return (
    <div style={{ marginTop: 12 }}>
      {/* ── Connected ───────────────────────────────────────────────────── */}
      {state.kind === "connected" && (
        <>
          <div className="fleet-channel-expand-success">
            <Check size={16} strokeWidth={2} /> {headline} — message the Empyralis bot on Telegram and your
            workspace assistant answers.
          </div>
          {state.botUnreachable && (
            <p className="fleet-channel-expand-error" style={{ marginTop: 8 }}>
              Telegram is rejecting the shared bot&apos;s sign-in, so replies have stopped. The pairing itself is
              intact — nothing to redo here once it&apos;s fixed.
            </p>
          )}
          <button
            type="button"
            className="fleet-btn"
            style={{ marginTop: 12 }}
            onClick={() => void disconnect()}
            disabled={busy}
          >
            {busy ? (
              <>
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Disconnecting…
              </>
            ) : (
              <>
                <Unlink size={14} strokeWidth={2} /> Disconnect
              </>
            )}
          </button>
        </>
      )}

      {/* ── Still finding out. Never rendered as "not connected". ────────── */}
      {state.kind === "checking" && (
        <p className="fleet-channel-expand-hint">
          <Loader2 size={12} style={{ animation: "spin 1s linear infinite", verticalAlign: "-2px" }} /> {headline}
        </p>
      )}

      {/* ── The status call itself failed. Distinct from "no". ───────────── */}
      {state.kind === "status_unknown" && (
        <>
          <p className="fleet-channel-expand-hint">
            {headline}. This doesn&apos;t mean Telegram is disconnected — we couldn&apos;t find out either way.
          </p>
          <button type="button" className="fleet-btn" style={{ marginTop: 10 }} onClick={() => void refresh()}>
            <RefreshCw size={14} strokeWidth={2} /> Check again
          </button>
        </>
      )}

      {/* ── This installation has no hosted bot. No button: nothing here can
             be made to work, and the real next step is the other door. ──── */}
      {state.kind === "unsupported" && (
        <p className="fleet-channel-expand-hint">
          {headline}. Whoever runs this installation hasn&apos;t set up the Empyralis-run Telegram bot. Use
          &ldquo;Chatbot&rdquo; instead, with your own bot from BotFather.
        </p>
      )}

      {/* ── Configured, but Telegram would not answer us. ────────────────── */}
      {state.kind === "unreachable" && (
        <>
          <p className="fleet-channel-expand-hint">
            {headline}. The Empyralis bot is set up here, but Telegram rejected our sign-in just now, so we
            can&apos;t hand you a working link. Nothing you did — try again shortly, or use &ldquo;Chatbot&rdquo;
            with your own bot.
          </p>
          <button
            type="button"
            className="fleet-btn"
            style={{ marginTop: 10 }}
            onClick={() => {
              setStart(null);
              setStartedAt(null);
              void refresh();
            }}
          >
            <RefreshCw size={14} strokeWidth={2} /> Try again
          </button>
        </>
      )}

      {/* ── Ready, nothing started. ──────────────────────────────────────── */}
      {state.kind === "idle" && (
        <>
          <p className="fleet-channel-expand-hint">
            One tap opens Telegram and links it to this workspace. There&apos;s no bot to create and nothing to
            paste.
          </p>
          <button
            type="button"
            className="fleet-btn fleet-btn--accent"
            style={{ marginTop: 10 }}
            onClick={() => void beginPairing()}
            disabled={busy}
          >
            {busy ? (
              <>
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Starting…
              </>
            ) : (
              <>
                <Send size={14} strokeWidth={2} /> Connect Telegram
              </>
            )}
          </button>
        </>
      )}

      {/* ── The code ran out and there was no link to fall back to. ─────── */}
      {state.kind === "expired" && (
        <>
          <p className="fleet-channel-expand-hint">
            {headline} before anyone used it. Codes are short-lived on purpose. Get a fresh one and it&apos;ll work
            the same way.
          </p>
          <button
            type="button"
            className="fleet-btn fleet-btn--accent"
            style={{ marginTop: 10 }}
            onClick={() => void beginPairing()}
            disabled={busy}
          >
            {busy ? (
              <>
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Starting…
              </>
            ) : (
              <>
                <RefreshCw size={14} strokeWidth={2} /> Get a new code
              </>
            )}
          </button>
        </>
      )}

      {/* ── Live code/link, nobody has tapped it yet. ─────────────────────── */}
      {state.kind === "waiting" && (
        <>
          <p className="fleet-channel-expand-hint">
            {state.deepLink
              ? "Open the bot in Telegram and press Start. This page updates by itself once you do."
              : `Open ${state.botUsername ? `@${state.botUsername}` : "the bot"} in Telegram and send it the code below. This page updates by itself once you do.`}
          </p>

          <div className="fleet-pair-details">
            {state.deepLink && (
              <a
                className="fleet-btn fleet-btn--accent"
                href={state.deepLink}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink size={14} strokeWidth={2} />
                {state.botUsername ? `Open @${state.botUsername}` : "Open in Telegram"}
              </a>
            )}

            {/* Only ever the genuinely typeable code. The long deep-link token
                is NOT shown here — see hostedPairingCodeIsTypeable, which
                derives that from the response rather than from a copied
                length constant. */}
            {state.code && (
              <span className="fleet-pair-code">
                <span className="fleet-pair-code-label">CODE</span>
                <span className="fleet-pair-code-value">{state.code}</span>
                <button
                  type="button"
                  className="fleet-btn"
                  style={{ padding: "2px 8px" }}
                  onClick={() => copyCode(state.code as string)}
                  aria-label="Copy pairing code"
                >
                  {copied ? <Check size={12} strokeWidth={2} /> : <Copy size={12} strokeWidth={2} />}
                </button>
              </span>
            )}
          </div>

          <p className="fleet-channel-expand-hint" style={{ marginTop: 10 }}>
            <Loader2 size={12} style={{ animation: "spin 1s linear infinite", verticalAlign: "-2px" }} />{" "}
            {headline}
            {state.codeExpiresAt !== null && !state.codeExpired
              ? ` — the code works for another ${formatRemaining(state.codeExpiresAt - now)}.`
              : ""}
            {state.codeExpired && state.deepLink
              ? " — the typed code has expired, but the link above still works."
              : ""}
          </p>

          <button
            type="button"
            className="fleet-btn"
            style={{ marginTop: 10 }}
            onClick={() => void beginPairing()}
            disabled={busy}
          >
            <RefreshCw size={14} strokeWidth={2} /> Start over
          </button>
        </>
      )}

      {actionError && (
        <p className="fleet-channel-expand-error" style={{ marginTop: 10 }}>
          {actionError}
        </p>
      )}
    </div>
  );
}

/** "9m 58s" / "45s". Never a bare number of seconds counting into the
 *  hundreds, and never a rounded "10 minutes" that is already wrong. */
function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0 ? `${minutes}m ${String(seconds).padStart(2, "0")}s` : `${seconds}s`;
}
