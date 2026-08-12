"use client";

import { useCallback, useState, type ReactNode } from "react";
import { Check, Circle, Copy, Loader2, X } from "lucide-react";

import {
  installImsgViaHomebrew,
  recheckImessagePersonalChannel,
  useGatewayPersonalChannelSurfaces,
  type ImessageInstallResult,
  type ImsgProbeStageStatus,
  type ImsgStagedProbe,
} from "./personal-channel-pairing";

const IMESSAGE_CHANNEL_KEY = "imessage_personal";

// The exact command from OpenClaw's own imsg setup docs
// (docs/channels/imessage.md, "Install and verify imsg" step) — same string
// the gateway's own auto-install action runs (see
// empyralis-gateway/src/bridges/imsg-imessage-client.ts's
// runImsgHomebrewInstall / IMSG_HOMEBREW_INSTALL_COMMAND). Always shown here
// too, so a Mac without Homebrew (or a user who'd rather run it themselves)
// has a copy-pasteable fallback regardless of whether the button below works.
const IMSG_INSTALL_COMMAND = "brew install steipete/tap/imsg";

function relativeTime(iso: string | undefined): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return null;
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return new Date(then).toLocaleDateString();
}

function StageIcon({ state }: { state: ImsgProbeStageStatus["state"] }) {
  if (state === "pass") {
    return <Check size={14} strokeWidth={2.5} style={{ color: "var(--online-text)", flexShrink: 0 }} />;
  }
  if (state === "fail") {
    return <X size={14} strokeWidth={2.5} style={{ color: "var(--offline-text)", flexShrink: 0 }} />;
  }
  return <Circle size={9} strokeWidth={2} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />;
}

/** One probe stage row. `children` is only rendered on a real failure (the
 *  inline fix); a "blocked" stage (an earlier stage failed first, so this
 *  one was never attempted — see ImsgStagedProbeResult's doc comment on the
 *  gateway) shows the gateway's own honest "not checked" text instead of
 *  fabricating either a pass or a fail. */
function StageRow({
  title,
  status,
  children,
}: {
  title: string;
  status: ImsgProbeStageStatus | undefined;
  children?: ReactNode;
}) {
  const state = status?.state ?? "blocked";
  return (
    <div className="imsg-stage-row">
      <div className="imsg-stage-row-head">
        <StageIcon state={state} />
        <span className="imsg-stage-row-title">{title}</span>
      </div>
      {state === "blocked" && status?.error && (
        <p className="fleet-channel-expand-hint imsg-stage-row-blocked">{status.error}</p>
      )}
      {state === "fail" && children && <div className="imsg-stage-row-body">{children}</div>}
    </div>
  );
}

/**
 * In-app iMessage setup panel — mirrors OpenClaw's `channels status --probe`
 * wizard, adapted to run entirely inside the platform instead of a
 * terminal. Renders the gateway's layered imsg probe (binary present? / rpc
 * responsive? / Full Disk Access ok? / private API — optional) as four
 * independent rows, each honest about pass/fail/blocked, with the exact
 * inline fix for whichever one is failing.
 *
 * HARD CONSTRAINT, stated plainly in the Full Disk Access row: macOS does
 * not let any process grant itself Full Disk Access — the user must flip
 * that toggle in System Settings themselves. Every other step (installing
 * imsg, re-checking) happens right here without a terminal.
 *
 * Data flow: useGatewayPersonalChannelSurfaces polls the same merged-surfaces
 * endpoint every other local-bridge channel uses (GET /personal-channels/
 * gateways/{gatewayId}/channels), which carries the LAST probe the gateway
 * pushed on its own connect/disconnect. "Re-check" and "Install" instead
 * dispatch a live tool-invoke round trip (recheckImessagePersonalChannel /
 * installImsgViaHomebrew in personal-channel-pairing.ts) and use that fresh
 * response directly — see this component's `liveProbe` state — so the
 * panel doesn't have to wait for the next gateway reconnect to reflect a
 * just-flipped Full Disk Access toggle or a just-finished install.
 */
export function IMessageSetupPanel({ gatewayId }: { gatewayId: string | null }) {
  const { items, loading, refresh } = useGatewayPersonalChannelSurfaces(gatewayId);
  const [liveProbe, setLiveProbe] = useState<ImsgStagedProbe | null>(null);
  const [rechecking, setRechecking] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installOutcome, setInstallOutcome] = useState<ImessageInstallResult["install"] | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const item = items.find((i) => i.channel_key === IMESSAGE_CHANNEL_KEY) || null;
  const probe = liveProbe || item?.health?.probe || null;
  const connected = !!item?.connected;

  const runRecheck = useCallback(async () => {
    if (!gatewayId) return null;
    setRechecking(true);
    setActionError(null);
    try {
      const result = await recheckImessagePersonalChannel(gatewayId);
      if (result.probe) setLiveProbe(result.probe);
      void refresh();
      return result.probe || null;
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Could not re-check right now.");
      return null;
    } finally {
      setRechecking(false);
    }
  }, [gatewayId, refresh]);

  const runInstall = useCallback(async () => {
    if (!gatewayId) return;
    setInstalling(true);
    setActionError(null);
    setInstallOutcome(null);
    try {
      const result = await installImsgViaHomebrew(gatewayId);
      setInstallOutcome(result.install || null);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Could not install imsg right now.");
    } finally {
      setInstalling(false);
    }
    // Always follow an install attempt with a live re-check, whether or not
    // the install itself reported success — the probe is the source of
    // truth, not the install command's own exit code.
    await runRecheck();
  }, [gatewayId, runRecheck]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(IMSG_INSTALL_COMMAND);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setActionError("Could not copy — select and copy the command manually.");
    }
  }, []);

  if (!gatewayId) {
    return (
      <p className="fleet-channel-expand-hint">
        This agent has no computer of its own yet — set one up on the Hardware tab first. Once it&apos;s paired,
        iMessage setup (installing imsg, checking Full Disk Access) happens right here — no separate bridge server,
        no terminal.
      </p>
    );
  }

  if (loading && !item) {
    // A bare 14px spinner reserved almost no height for what resolves into
    // a hint line plus a 4-row `.imsg-stage-list` — hundreds of px taller.
    // Reuses the real `.imsg-stage-list`/`.imsg-stage-row`/
    // `.imsg-stage-row-head`/`.imsg-stage-row-title` markup for the
    // collapsed shape every stage row renders in before it's known to have
    // failed (the only state common to all four, since a failure's own
    // expanded body is data-dependent and unknowable ahead of the fetch).
    return (
      <div className="imsg-setup-panel" aria-busy="true" aria-label="Loading">
        <p className="fleet-channel-expand-hint">
          <span className="fleet-skeleton-bar" style={{ width: "75%", height: 13 }} />
        </p>
        <div className="imsg-stage-list">
          {["imsg installed", "Full Disk Access granted", "Messages database readable", "Bridge running"].map((title) => (
            <div key={title} className="imsg-stage-row">
              <div className="imsg-stage-row-head">
                <div className="fleet-skeleton-bar" style={{ width: 14, height: 14, borderRadius: 999 }} />
                <span className="imsg-stage-row-title">{title}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const checkedAtLabel = relativeTime(probe?.checkedAt);

  return (
    <div className="imsg-setup-panel">
      {connected ? (
        <div className="fleet-channel-expand-success">
          <Check size={16} strokeWidth={2} /> Connected — imsg can read your Messages history.
        </div>
      ) : (
        <p className="fleet-channel-expand-hint">
          iMessage runs through <code>imsg</code>, a small CLI that talks to Messages.app directly on this Mac — no
          BlueBubbles Server, no separate bridge process to run yourself.
        </p>
      )}

      {!probe && (
        <p className="fleet-channel-expand-hint" style={{ color: "var(--text-tertiary)" }}>
          No health data from the Gateway yet — this fills in once it&apos;s online, or click Re-check below.
        </p>
      )}

      <div className="imsg-stage-list">
        <StageRow title="imsg installed" status={probe?.binary}>
          <p className="fleet-channel-expand-error" style={{ marginTop: 0 }}>
            {probe?.binary?.error || "imsg was not found on this Mac."}
          </p>
          <div className="imsg-stage-actions">
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void runInstall()} disabled={installing}>
              {installing ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
              {installing ? "Installing…" : "Install imsg via Homebrew"}
            </button>
          </div>
          <p className="fleet-channel-expand-hint" style={{ marginTop: 10, marginBottom: 6 }}>
            Or paste this in Terminal yourself:
          </p>
          <pre className="gw-pair-panel-command">
            <code>{IMSG_INSTALL_COMMAND}</code>
          </pre>
          <button type="button" className="fleet-btn" onClick={() => void handleCopy()} style={{ marginTop: 8 }}>
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? "Copied" : "Copy command"}
          </button>
          {installOutcome && (
            <div className="imsg-install-outcome">
              {installOutcome.ok ? (
                <p className="fleet-channel-expand-hint" style={{ color: "var(--online-text)" }}>
                  Homebrew install succeeded — re-checking above.
                </p>
              ) : installOutcome.brewFound === false ? (
                <p className="fleet-channel-expand-error">
                  Homebrew isn&apos;t installed on this Mac — use the command above instead.
                </p>
              ) : (
                <p className="fleet-channel-expand-error">{installOutcome.error || "brew install failed."}</p>
              )}
              {(installOutcome.stderr || installOutcome.stdout) && (
                <pre className="imsg-install-log">{installOutcome.stderr || installOutcome.stdout}</pre>
              )}
            </div>
          )}
        </StageRow>

        <StageRow title="imsg responds to the gateway" status={probe?.rpc}>
          <p className="fleet-channel-expand-error" style={{ marginTop: 0 }}>
            {probe?.rpc?.error || "imsg did not respond as expected."}
          </p>
          <p className="fleet-channel-expand-hint">
            This usually means an out-of-date imsg build — run <code>brew upgrade steipete/tap/imsg</code>, then
            Re-check.
          </p>
        </StageRow>

        <StageRow title="Full Disk Access (Messages history readable)" status={probe?.fullDiskAccess}>
          {probe?.fullDiskAccess?.isFullDiskAccessError ? (
            <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
              Open <strong>System Settings → Privacy &amp; Security → Full Disk Access</strong> and enable it for the
              Gateway, then Re-check. This is the one step Empyralis genuinely can&apos;t do for you — macOS only
              lets you grant Full Disk Access yourself.
            </p>
          ) : (
            <>
              <p className="fleet-channel-expand-error" style={{ marginTop: 0 }}>
                {probe?.fullDiskAccess?.error || "imsg could not read Messages history."}
              </p>
              <p className="fleet-channel-expand-hint">Make sure Messages is signed in on this Mac, then Re-check.</p>
            </>
          )}
        </StageRow>

        <StageRow
          title="Private API — optional (reactions, edit, unsend, polls)"
          status={probe?.privateApi?.checked === false ? { state: "blocked", error: probe.privateApi.error } : probe?.privateApi}
        >
          <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
            Not available yet. This is optional — basic send/receive already works without it. Enabling it requires
            disabling System Integrity Protection; see the imsg docs if you want reactions, edit, unsend, or polls.
          </p>
        </StageRow>
      </div>

      <div className="imsg-stage-footer">
        <button type="button" className="fleet-btn" onClick={() => void runRecheck()} disabled={rechecking}>
          {rechecking ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
          {rechecking ? "Checking…" : "Re-check"}
        </button>
        {checkedAtLabel && (
          <span className="fleet-channel-expand-hint imsg-stage-footer-checked">Checked {checkedAtLabel}</span>
        )}
      </div>

      {actionError && <p className="fleet-channel-expand-error">{actionError}</p>}
    </div>
  );
}
