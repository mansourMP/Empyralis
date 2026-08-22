"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useEffect, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import type { FleetAgent } from "../fleet-data";
import {
  GatewayBoxPicker,
  gatewayId as readGatewayId,
  gatewaysOfKind,
  hardwarePlacementIsBrainBound,
  resolveHardwarePlacement,
  useWorkspaceGateways,
  type FleetGateway,
  type HardwarePlacementTone,
} from "../gateway-box-picker";
import {
  dockerStatusFromGatewayPayload,
  executionIsolationStatement,
  planExecutionIsolation,
} from "../box-capability-state";

// Collapsed from a 4-way (none/gateway/vps/all) to the honest 3-way set,
// 2026-07-15 — founder ruling: once an agent has hardware, a paired computer
// or a cloud VPS, it has full run of that box by default. "Full hardware
// access" was never a distinct, separately-grantable level; it was a fourth
// button that just implied gateway/vps were partial by comparison. A
// legacy-stored "all" is normalized to "gateway" before it ever reaches this
// component (see normalizeAccess below, and fleet_tools.py server-side), so
// this array doesn't need an "all" branch to stay backward compatible.
const ACCESS_OPTIONS: { value: string; label: string; body: string }[] = [
  { value: "none", label: "Cloud", body: "Runs on Empyralis. Works on tasks and documents; add a computer only for shell and files." },
  { value: "gateway", label: "Paired computer", body: "Shell, filesystem, and browser — everything on a computer you've paired." },
  { value: "vps", label: "Cloud VPS", body: "Shell, filesystem, and browser — everything on a cloud computer Empyralis provisions for you." },
];

/** Legacy "all" (Full hardware access, retired 2026-07-15) collapses to
 *  "gateway" for display/selection purposes only — it doesn't touch
 *  preferred_gateway_id, so an agent that already had a specific box picked
 *  keeps pointing at that exact box; only the coarse bucket button that
 *  lights up changes. The backend performs the same mapping when surfacing
 *  and when persisting (see fleet_tools.py), so this is a client-side
 *  backstop for a stale cached agent record, not the primary fix. */
function normalizeAccess(raw: string | undefined | null): string {
  const value = (raw || "none").toLowerCase();
  return value === "all" ? "gateway" : value;
}

/**
 * The ONE box whose isolation we can honestly describe, or null.
 *
 * An explicit pick is the box. With no pick, the picker's own contract is
 * "whichever of these is online" — so a statement is only truthful when the
 * candidate list holds exactly one box. With several, we genuinely do not
 * know which one a command will land on, and "unknown" must render nothing
 * rather than pick a box and describe it. Same discipline as the
 * channelsLoading guard below: two different facts, never one sentence.
 */
function resolveIsolationSubject(
  access: string,
  preferredGateway: string,
  gateways: FleetGateway[],
): FleetGateway | null {
  const candidates = gatewaysOfKind(gateways, access === "vps" ? "vps" : "gateway");
  const picked = String(preferredGateway || "").trim();
  if (picked) {
    return candidates.find((g) => readGatewayId(g) === picked) ?? null;
  }
  return candidates.length === 1 ? candidates[0] : null;
}

function dotClass(tone: HardwarePlacementTone): string {
  if (tone === "online" || tone === "cloud") return "is-online";
  if (tone === "degraded") return "is-degraded";
  return "is-offline"; // offline, unpaired
}

/**
 * HARDWARE tab — the agent's real hardware placement, and the one control
 * that sets it (moved here from the Model tab, which kept a second,
 * confusable "brain runs on" picker for a different field — see ModelTab).
 *
 * Backed by hardware_access (none/gateway/vps — "all" retired 2026-07-15,
 * normalized to "gateway" wherever it's still stored, see fleet_tools.py and
 * normalizeAccess above) + preferred_gateway_id, joined against live
 * /gateway/registrations — deliberately never runtime_target /
 * hardware_status (see resolveHardwarePlacement for why that pipeline is
 * inert for real Fleet agents).
 *
 * The placement preview below is brain-aware — resolveHardwarePlacement
 * favors model_config.gateway_binding over hardware_access for
 * cli_subscription/local agents (see hardwarePlacementIsBrainBound), so it
 * can legitimately show something other than what this tab's own picker is
 * set to. The preview explains that split inline rather than leaving it a
 * silent contradiction.
 */
export function HardwareTab({
  workspaceId,
  agentId,
  agent,
  onSaved,
  reachableChannels = [],
  channelsLoading = false,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Called after a successful save so the caller can refetch — the Overview
   *  property and list rows read this same placement from a separate fetch
   *  of `agent`, and should catch up immediately rather than waiting out the
   *  next poll. */
  onSaved?: () => void;
  /** Labels of the channels this agent is actually reachable on right now.
   *  Passed down rather than fetched here: FleetAgentDetail (the only call
   *  site) already computes it through isChannelConnected, and a second
   *  opinion about "is this agent reachable" is exactly the kind of drift
   *  this codebase keeps paying for. Used for one sentence — see the
   *  hardware-reach note below. */
  reachableChannels?: string[];
  /** True while that channel list is still unknown. The note below renders
   *  NOTHING while this is true: "no channel is connected" and "I have not
   *  found out yet" are different facts and must not share one sentence. */
  channelsLoading?: boolean;
}) {
  const { gateways, loading: gatewaysLoading } = useWorkspaceGateways(workspaceId);
  const locked = !!agent?.hardware_access_locked;
  const preset = (agent?.capability_preset || "").toLowerCase();

  const [access, setAccess] = useState(normalizeAccess(agent?.hardware_access));
  const [preferredGateway, setPreferredGateway] = useState(agent?.preferred_gateway_id || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Resync when the agent record (re)loads under us — e.g. switching agents,
  // or another tab's edit landing — but never while a save is in flight.
  useEffect(() => {
    if (saving) return;
    setAccess(normalizeAccess(agent?.hardware_access));
    setPreferredGateway(agent?.preferred_gateway_id || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent?.hardware_access, agent?.preferred_gateway_id]);

  // model_config passed through even on this tab's own local edit state —
  // for a cli_subscription/local agent, brain placement wins regardless of
  // what this picker is set to (see resolveHardwarePlacement), so this
  // preview must agree with the Overview property and list card rather than
  // react to a control that doesn't actually govern brain placement.
  const placement = resolveHardwarePlacement(access, preferredGateway, gateways, agent?.model_config);
  const brainBound = hardwarePlacementIsBrainBound(agent?.model_config);

  // Which isolation this agent's commands actually run under on the computer
  // it is attached to. Rendered as ONE plain sentence, never a warning and
  // never a control — see box-capability-state.ts's planExecutionIsolation
  // for the rule and the founder's ruling behind it. `null` at any step means
  // we do not know yet, and nothing is rendered: "runs on the whole computer"
  // and "haven't found out yet" are different facts, the same reason the
  // channel-reach note below waits for channelsLoading.
  const isolationSubject = access === "none" ? null : resolveIsolationSubject(access, preferredGateway, gateways);
  const isolationStatement =
    !isolationSubject || gatewaysLoading
      ? null
      : executionIsolationStatement(
          planExecutionIsolation({
            dockerStatus: dockerStatusFromGatewayPayload(isolationSubject),
            runtimeAccessMode: isolationSubject.runtime_access_mode,
            shellFullAccessLocallyEnabled: isolationSubject.shell_full_access_locally_enabled,
          }),
        );

  async function persist(nextAccess: string, nextGateway: string) {
    setSaving(true);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`,
        {
          method: "PATCH",
          credentials: "include",
          headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
          body: JSON.stringify({
            patch: {
              // The real column, verbatim — no collapsing to a none/gateway
              // boolean the way the old Model-tab control did, which
              // silently downgraded "vps" (and legacy "all") to "gateway"
              // on save.
              hardware_access: nextAccess,
              preferred_gateway_id: nextAccess === "none" ? "" : nextGateway,
            },
          }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
      // Roll the control back to the last-known-saved values rather than
      // leaving it pointed at a selection that didn't actually persist.
      setAccess(normalizeAccess(agent?.hardware_access));
      setPreferredGateway(agent?.preferred_gateway_id || "");
    } finally {
      setSaving(false);
    }
  }

  function selectAccess(next: string) {
    if (saving || next === access) return;
    setAccess(next);
    void persist(next, preferredGateway);
  }

  function selectGateway(next: string) {
    setPreferredGateway(next);
    void persist(access, next);
  }

  return (
    <div className="fleet-detail-pad fleet-hw">
      {/* Placement preview — kept ONLY for the brainBound case, where it
          genuinely disambiguates: a cli_subscription/local agent's brain is
          bound to a computer via the Model tab, independent of this tab's
          own hardware_access control below (see hardwarePlacementIsBrainBound),
          so the two controls can legitimately disagree and this says so
          explicitly. For a plain API-model agent there's nothing to
          disambiguate — this row used to just restate whichever option is
          already highlighted in the picker directly beneath it (its own copy
          admitted as much: "placement here mirrors the hardware access you
          set below"), plus a "Loading…" flash while gateways fetch for a
          fact the picker already shows instantly. Removed for that case
          rather than kept as a redundant, briefly-wrong echo. */}
      {brainBound && (
        <>
          <div className="fleet-detail-section-title">Brain runs on</div>
          <div className="fleet-hw-card">
            <div className="fleet-hw-row">
              <span className="fleet-hw-label">Placement</span>
              <span className="fleet-hw-value">
                <span className={`fleet-detail-dot ${dotClass(placement.tone)}`} aria-hidden />
                {gatewaysLoading ? "Loading…" : placement.label}
              </span>
            </div>
          </div>
          <p className="fleet-hw-note" style={{ marginTop: 6 }}>
            Bound to this agent&apos;s subscription/local model connection, set on the Model tab — independent of the hardware access below, which only controls what its tools can reach.
          </p>
        </>
      )}

      <div className="fleet-detail-section-title" style={brainBound ? { marginTop: 20 } : undefined}>Hardware access</div>
      {/* This paragraph used to read "Owner only — customers can't trigger
          hardware, shell, or commands, no matter what they message." That
          became FALSE on 2026-08-21, when the audience tool tier was deleted
          (see server_modules/authority_mandate_service.py). Saying it now
          would be the worst version of the outcome-honesty law: a security
          claim, on the exact screen where the decision is made, describing a
          mechanism that no longer exists.

          What replaces it is ONE sentence, not a gate and not a
          confirmation — CLAUDE.md forbids approval flows, and the founder
          has settled that access to an agent is binary and gated by who can
          REACH it. So: state the consequence, name the screen that actually
          controls it, and get out of the way. Rendered only when hardware is
          actually attached (nothing to say for a cloud-only agent) and only
          once the channel list is known (see channelsLoading above). */}
      {access !== "none" && !channelsLoading && (
        <p className="fleet-hw-note" style={{ marginTop: 0 }}>
          {reachableChannels.length > 0 ? (
            <>
              Anyone who can message this agent on {reachableChannels.join(", ")} can make it
              run commands and read files on this computer. Channels is where you choose who
              that is.
            </>
          ) : (
            <>
              Anyone who can message this agent can make it run commands and read files on
              this computer. No channel is connected yet, so right now that is only you.
            </>
          )}
        </p>
      )}
      {locked ? (
        <p className="fleet-hw-note" style={{ marginTop: 0 }}>
          This is a <strong>Knowledge</strong> agent — hardware access is off and policy-locked. To
          grant hardware, change its capability preset (<span style={{ textTransform: "capitalize" }}>{preset || "knowledge"}</span>) on the
          Model tab; a knowledge agent can't be given hardware directly.
        </p>
      ) : (
        <>
          <div className="fleet-wizard-options">
            {ACCESS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`fleet-wizard-option${access === opt.value ? " is-selected" : ""}`}
                disabled={saving}
                onClick={() => selectAccess(opt.value)}
              >
                <span className="fleet-wizard-option-label">{opt.label}</span>
                <span className="fleet-wizard-option-body">{opt.body}</span>
              </button>
            ))}
          </div>
          {access !== "none" && (
            // `kind` keyed off the selected access mode: the picker then
            // lists only boxes that mode can actually use, and its empty
            // state names the right missing thing ("no cloud server" vs "no
            // paired computer") with a link to the Hardware page, where all
            // provisioning/pairing lives (founder ruling 2026-07-28). The
            // "leave unset to use whichever … is online" hint now comes from
            // the picker itself so it can match the mode too.
            <GatewayBoxPicker
              workspaceId={workspaceId}
              kind={access === "vps" ? "vps" : "gateway"}
              value={preferredGateway}
              disabled={saving}
              onChange={selectGateway}
            />
          )}
          {/* HOW commands run on the attached computer. Docker's presence
              decides this — it is not something the owner picks, and its
              absence is not an error to fix: a computer without Docker runs
              the same work directly and it WORKS. Founder, 2026-08-22:
              "Docker is not something that is going to degrade what we
              do... I don't want to hear any of those things from my agent."
              So this is a plain note in the same quiet style as the
              channel-reach sentence above — deliberately NOT an alert tone,
              NOT a banner, and with no button beside it. */}
          {isolationStatement && (
            <p className="fleet-hw-note" style={{ marginTop: 10 }}>
              {isolationStatement}
            </p>
          )}
          <div style={{ marginTop: 12, minHeight: 20 }}>
            {saving && <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>Saving…</span>}
            {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
          </div>
        </>
      )}
    </div>
  );
}
