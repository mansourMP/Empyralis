"use client";

/**
 * Data + shared UI for the OpenClaw-transported channels inside the ONE
 * unified channel list on the Channels tab (FleetAgentDetail.tsx's
 * ChannelsTab). No terminal, no SSH.
 *
 * FORMERLY A STANDALONE PANEL, NOW A HOOK + A FORM BODY
 * -----------------------------------------------------
 * Until 2026-08-09 this file exported a full `<OpenClawChannelsPanel>`
 * section, rendered by ChannelsTab as its OWN block underneath a separate
 * `fleet-channel-grid` of the first-party channel cards — two visually
 * distinct components stacked in one tab, which is exactly the "old vs new"
 * split the founder called out: two components stacked, even visually
 * similar, is not one user interface. ChannelsTab now owns ONE
 * `.fleet-channel-grid` of square `.fleet-channel-card`s over EVERY channel
 * (first-party + transported), so the data-fetching
 * (`useOpenClawChannelSetup`) and the generated credential form
 * (`CredentialForm`) are exported here for it to use directly; the section
 * chrome (heading, toolbar, grid container) and the panel a card opens into
 * both live in ChannelsTab, since neither is this file's own section any more.
 *
 * A CARD FACE HOLDS ICON + LABEL + ONE PILL, AND NOTHING ELSE
 * ----------------------------------------------------------
 * The three states below are not shown on the grid. They are shown, all
 * three, in the panel a card opens — which is also where the remediation
 * sentence and its button live. A 2026-08-09 pass put all of it on the face
 * of every row (subtitle + three chips + a full sentence + a button, ~26
 * times) and the screen became a wall of text; that is the thing this file's
 * `channelCardPill` sibling in openclaw-channel-copy.ts exists to prevent.
 *
 * THE FORM IS GENERATED, NOT WRITTEN
 * ---------------------------------
 * There is no per-channel component in this file and there must never be one.
 * Every control comes from `channels[].fields`, which the backend derives from
 * OpenClaw's own config schema at manifest-generation time. A channel OpenClaw
 * adds upstream grows its own form here with no change to this file — the same
 * rule that already governs the channel LIST governs the channel FORMS, for
 * the same reason: a hand-maintained per-channel surface is pure curation and
 * pure defect.
 *
 * THREE STATES, NEVER COLLAPSED
 * -----------------------------
 * OpenClaw's own `channels list` is the bar:
 *
 *     - Feishu: not installed, configured, disabled, run openclaw plugins …
 *
 * Three independent facts and the specific next action. A single
 * "Connected / Not connected" light throws away exactly the information that
 * says which of the three to go fix. So the panel a channel opens reads its
 * three states separately, and each non-working state carries its own
 * remediation — a BUTTON there rather than a command, because the cloud can
 * already drive the device. Where the action genuinely cannot happen in a
 * browser (a QR scan on a phone), the panel says so instead of rendering a
 * control that does nothing.
 *
 * OBSERVED, NEVER DECLARED
 * ------------------------
 * No state on this screen comes from "we saved a credential, so it must be
 * connected". `installed` is OpenClaw's registry, `set` is a read-back of the
 * effective config, `enabled` is the config switch. When the box cannot be
 * reached we show that as its own state — never as "not connected", which
 * would be a claim about the channel made from a fact about the network.
 *
 * NOTHING SECRET COMES BACK
 * -------------------------
 * A secret input is always empty on load and after a save. There is no
 * "current value" to show: OpenClaw redacts secret values before they leave
 * the customer's machine, so the browser only ever learns `set: true`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import {
  remediationFor,
  type OpenClawChannelCatalogEntry,
  type OpenClawObservedChannel,
  type Remediation,
} from "./openclaw-channel-copy";

import "./openclaw-channels.css";

export type {
  OpenClawCredentialField,
  OpenClawChannelCatalogEntry,
  OpenClawObservedField,
  OpenClawObservedChannel,
} from "./openclaw-channel-copy";

type SetupResponse = {
  openclaw_version?: string;
  channels?: OpenClawChannelCatalogEntry[];
  observed?: { status?: string; channels?: OpenClawObservedChannel[]; refusal?: { detail?: string } | null } | null;
  observed_error?: string | null;
  // Channels a first-party Empyralis runtime already carries (Telegram,
  // WhatsApp, ...) — computed on the backend from the same overlap logic
  // that decides which channels this hook's catalog carries, never
  // hand-typed here. See channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS.
  // ChannelsTab uses this only to render a real row for each one (via the
  // first-party grid data) instead of the old "these connect elsewhere" note.
  already_available_channels?: string[];
};

export type OpenClawChannelRow = {
  entry: OpenClawChannelCatalogEntry;
  observed: OpenClawObservedChannel | undefined;
  remediation: Remediation;
};

/** .fleet-badge is the shared status-chip primitive (Members, Hardware,
 *  Connections all use it) — this only adds the ok/off colour, never a new
 *  shape, so a chip here reads as the same control everywhere else it
 *  appears, including the first-party rows in the same unified list. */
export function StateChip({ ok, on, off }: { ok: boolean; on: string; off: string }) {
  return (
    <span
      className={`fleet-badge openclaw-chip ${ok ? "openclaw-chip--ok" : "openclaw-chip--off"}`}
      style={{ marginLeft: 0 }}
    >
      {ok ? on : off}
    </span>
  );
}

/** Every OpenClaw channel this gateway carries, joined with its observed
 *  state, plus the provisioning action. `gatewayId: null` (no paired
 *  computer) is a valid, common state — OpenClaw is structurally box-only —
 *  but it is NOT "nothing to show": the CATALOG (which channels exist and
 *  what their setup form looks like) is a property of the pinned transport,
 *  not of any one box, so it still loads from
 *  `GET /personal-channels/openclaw/catalog` with no gateway_id in the URL.
 *  Only the OBSERVED half (installed/configured/enabled on a real machine)
 *  is genuinely box-only and stays absent. Before this, `gatewayId: null`
 *  resolved to an empty row set and the whole transported half of the
 *  channel grid silently vanished for a cloud-only agent — the "built,
 *  tested, and never wired" shape, since the catalog was reachable, just not
 *  through any route that didn't require a gateway_id. */
const SETUP_VERIFY_POLL_MS = 3_000;
const SETUP_VERIFY_TIMEOUT_MS = 120_000;

export function useOpenClawChannelSetup(gatewayId: string | null, agentId: string) {
  const [data, setData] = useState<SetupResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  /** `[active, ...waiting]`. See `requestSetup` — installs are serialized for
   *  the same reason `useInstallQueue` serializes CLI installs on the Hardware
   *  page: several fired at once are several installer processes on ONE box. */
  const [setupQueue, setSetupQueue] = useState<string[]>([]);
  const runningRef = useRef<string | null>(null);

  const load = useCallback(
    async (opts?: { silent?: boolean }): Promise<SetupResponse | null> => {
      if (!opts?.silent) setLoading(true);
      try {
        // With a gateway bound, join the catalog against this box's live
        // state. With none, the catalog alone — no `observed` key at all,
        // which is exactly what makes `reachable` (below) false and every
        // row's remediation `needs_hardware` rather than a stale/invented
        // "not installed" read from a device that was never asked.
        const url = gatewayId
          ? `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/setup`
          : "/api/personal-channels/openclaw/catalog";
        const res = await fetch(url, { credentials: "include" });
        if (!res.ok) {
          setError(`Could not load channels (${res.status}).`);
          return null;
        }
        setError(null);
        const body = (await res.json()) as SetupResponse;
        setData(body);
        return body;
      } catch {
        setError("Could not load channels.");
        return null;
      } finally {
        setLoading(false);
      }
    },
    [gatewayId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  /** The `doctor --fix` equivalent: provisioning. It installs the plugins it
   *  was asked for and re-asserts policy — and it HIDES NOTHING, because every
   *  row re-reads its own three states straight afterwards. */
  const provision = useCallback(
    async (installChannels: string[]) => {
      if (!gatewayId) return;
      setBusy(installChannels[0] ?? "__all__");
      try {
        const res = await fetch(
          `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/provision?agent_id=${encodeURIComponent(agentId)}`,
          {
            method: "POST",
            credentials: "include",
            headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
            body: JSON.stringify({ install_channels: installChannels }),
          },
        );
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          setError(getErrorMessage(body, `Setup failed (${res.status}).`));
        } else {
          const refusal = body?.openclaw_provisioning?.refusal;
          setError(refusal ? `${refusal.code}: ${refusal.detail}` : null);
        }
      } catch {
        setError("Could not reach this computer.");
      } finally {
        setBusy(null);
        await load({ silent: true });
      }
    },
    [agentId, gatewayId, load],
  );

  /** Ask for ONE channel to be set up. The caller never runs the work itself:
   *  it joins the queue, and the effect below starts it the instant it is this
   *  channel's turn. Two rows clicked in quick succession therefore run one
   *  after the other rather than launching two package installs onto the same
   *  machine at once — the defect `useInstallQueue` exists for on the Hardware
   *  page, in a surface that can trigger the same thing. */
  const requestSetup = useCallback((channelKey: string) => {
    setSetupQueue((queue) => (queue.includes(channelKey) ? queue : [...queue, channelKey]));
  }, []);

  const activeSetup = setupQueue[0] ?? null;

  useEffect(() => {
    if (!activeSetup || !gatewayId) return;
    if (runningRef.current === activeSetup) return;
    runningRef.current = activeSetup;
    let cancelled = false;
    void (async () => {
      try {
        await provision([activeSetup]);
        // VERIFY, don't declare. The provisioning call returning is not the
        // same fact as the box reporting the channel installed, so this keeps
        // reading the device's own state until it catches up (or gives up
        // loudly) — the pattern CliSetupControl uses for exactly this reason.
        const startedAt = Date.now();
        while (!cancelled && Date.now() - startedAt < SETUP_VERIFY_TIMEOUT_MS) {
          const body = await load({ silent: true });
          const observedRow = (body?.observed?.channels ?? []).find(
            (row) => row.channel_key === activeSetup,
          );
          if (observedRow?.installed) break;
          await new Promise((resolve) => setTimeout(resolve, SETUP_VERIFY_POLL_MS));
        }
      } finally {
        runningRef.current = null;
        setSetupQueue((queue) => queue.filter((key) => key !== activeSetup));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeSetup, gatewayId, provision, load]);

  /** What this one channel's setup control should show right now. `working`
   *  means something is genuinely running on the box for it; `queued` means
   *  nothing is, it is only waiting its turn. */
  const setupStateFor = useCallback(
    (channelKey: string): "idle" | "queued" | "working" =>
      activeSetup === channelKey ? "working" : setupQueue.includes(channelKey) ? "queued" : "idle",
    [activeSetup, setupQueue],
  );

  const catalog = data?.channels ?? [];
  const alreadyAvailable = data?.already_available_channels ?? [];
  const observedList = data?.observed?.channels ?? [];
  const reachable = Boolean(data?.observed) && !data?.observed_error;
  const observedById = useMemo(
    () => new Map(observedList.map((entry) => [entry.channel_id, entry])),
    [observedList],
  );

  const rows: OpenClawChannelRow[] = useMemo(
    () =>
      catalog.map((entry) => {
        const observed = observedById.get(entry.channel_id);
        // `Boolean(gatewayId)` — not `reachable` — is the "is there a
        // computer at all" fact. With no gateway bound, every row reads
        // `needs_hardware` regardless of what a PREVIOUS gateway's observed
        // data might still be sitting in `data` from a stale response.
        return { entry, observed, remediation: remediationFor(entry, observed, reachable, Boolean(gatewayId)) };
      }),
    [catalog, observedById, reachable, gatewayId],
  );

  const repairable = rows
    .filter((row) => row.remediation.kind === "install" || row.remediation.kind === "enable")
    .map((row) => row.entry.channel_key);

  return {
    loading,
    error,
    observedError: data?.observed_error ?? null,
    busy,
    rows,
    alreadyAvailable,
    repairable,
    refresh: load,
    provision,
    requestSetup,
    setupStateFor,
    observedById,
  };
}

/** The generated form. Every control below is driven by `entry.fields`; there
 *  is no branch on a channel id anywhere in it.
 *
 *  A FORM BODY, NOT A DIALOG. Until 2026-08-10 this was `CredentialDialog`,
 *  which carried its own `.fleet-detail-backdrop` + `.fleet-channel-banner`
 *  shell. The unified channel GRID opens one panel per card — the same panel
 *  a first-party card opens — so the shell belongs to that panel and this is
 *  only what goes inside it. Two stacked dialogs (a card's detail panel, then
 *  a second modal on top of it for the credential) is the "two components
 *  stacked is not one interface" defect one level down. */
export function CredentialForm({
  gatewayId,
  entry,
  observed,
  onCancel,
  onSaved,
}: {
  gatewayId: string;
  entry: OpenClawChannelCatalogEntry;
  observed: OpenClawObservedChannel | undefined;
  /** Rendered as the form's secondary button. The panel that hosts this form
   *  owns closing itself; this only says "not now". */
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setFields = useMemo(
    () => new Set((observed?.fields ?? []).filter((field) => field.set).map((field) => field.name)),
    [observed],
  );
  const filled = Object.values(values).filter((value) => value.trim().length > 0).length;

  const save = async () => {
    const payload = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value.trim().length > 0),
    );
    if (Object.keys(payload).length === 0) {
      setError("Fill in at least one field.");
      return;
    }
    setSaving(true);
    try {
      const res = await fetch(
        `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/channels/${encodeURIComponent(entry.channel_key)}/credential`,
        {
          method: "PUT",
          credentials: "include",
          headers: buildCookieAuthHeaders("PUT", { "Content-Type": "application/json" }),
          body: JSON.stringify({ values: payload }),
        },
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(getErrorMessage(body, `Save failed (${res.status}).`));
        return;
      }
      const refusal = body?.openclaw_channel_setup?.refusal;
      if (refusal) {
        setError(`${refusal.code}: ${refusal.detail}`);
        return;
      }
      // Cleared rather than retained: nothing typed here is kept in the tab
      // any longer than the request needs it.
      setValues({});
      await onSaved();
    } catch {
      setError("Could not reach this computer.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
        These are the fields this channel needs to connect.
      </p>

      {error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="openclaw-form">
        {entry.fields.map((field) => {
          const inputId = `openclaw-${entry.channel_id}-${field.name}`;
          const already = setFields.has(field.name);
          return (
            <div key={field.name} className="openclaw-form-field">
              <label htmlFor={inputId}>
                <span className="openclaw-form-name">{field.name}</span>
                {field.secret ? (
                  <span className="fleet-badge openclaw-chip openclaw-chip--secret" style={{ marginLeft: 0 }}>
                    secret
                  </span>
                ) : null}
                {already ? (
                  <span className="fleet-badge openclaw-chip openclaw-chip--ok" style={{ marginLeft: 0 }}>
                    set
                  </span>
                ) : null}
              </label>
              <input
                id={inputId}
                className="fleet-wizard-input"
                /* A secret is a password input and is ALWAYS empty on open:
                   there is no stored value to prefill — this computer
                   redacts it before it ever leaves the machine. */
                type={field.secret ? "password" : "text"}
                autoComplete="off"
                spellCheck={false}
                value={values[field.name] ?? ""}
                placeholder={already ? "Set — type to replace" : ""}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [field.name]: event.target.value }))
                }
              />
            </div>
          );
        })}
      </div>

      <p className="openclaw-form-note">
        Leave a field blank to keep what is already on the computer. Values are sent straight to it
        and are not stored here.
      </p>

      <div className="openclaw-dialog-actions">
        <button type="button" className="fleet-btn" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        <button
          type="button"
          className="fleet-btn fleet-btn--accent-fill"
          onClick={() => void save()}
          disabled={saving || filled === 0}
        >
          {saving ? <Loader2 size={14} className="openclaw-spin" /> : null}
          {saving ? "Saving…" : "Save credential"}
        </button>
      </div>
    </>
  );
}
