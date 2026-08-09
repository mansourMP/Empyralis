"use client";

/**
 * Data + shared UI for the OpenClaw-transported channels inside the ONE
 * unified channel list on the Channels tab (FleetAgentDetail.tsx's
 * ChannelsTab). No terminal, no SSH.
 *
 * FORMERLY A STANDALONE PANEL, NOW A HOOK + A DIALOG
 * ---------------------------------------------------
 * Until 2026-08-09 this file exported a full `<OpenClawChannelsPanel>`
 * section, rendered by ChannelsTab as its OWN block underneath a separate
 * `fleet-channel-grid` of the first-party channel cards — two visually
 * distinct components stacked in one tab, which is exactly the "old vs new"
 * split the founder called out: two components stacked, even visually
 * similar, is not one user interface. ChannelsTab now owns ONE merged list
 * and ONE render loop over every channel (first-party + OpenClaw), so the
 * data-fetching (`useOpenClawChannelSetup`) and the generated-credential-form
 * dialog (`CredentialDialog`) are exported here for it to use directly; the
 * section chrome (heading, toolbar, list container) moved to ChannelsTab
 * itself since it is no longer this file's own section.
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
 * Three independent facts and the specific next action, on one line. A single
 * "Connected / Not connected" light throws away exactly the information that
 * says which of the three to go fix. So each row reads its three states
 * separately, and each non-working state carries its own remediation — a
 * BUTTON here rather than a command, because the cloud can already drive the
 * device. Where the action genuinely cannot happen in a browser (a QR scan on
 * a phone), the row says so instead of rendering a control that does nothing.
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

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
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
 *  computer) is a valid, common state — OpenClaw is structurally box-only, so
 *  this resolves immediately with an empty row set and no fetch, rather than
 *  spinning forever on a request that can never succeed. */
export function useOpenClawChannelSetup(gatewayId: string | null, agentId: string) {
  const [data, setData] = useState<SetupResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(gatewayId));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!gatewayId) {
        setData(null);
        setError(null);
        setLoading(false);
        return;
      }
      if (!opts?.silent) setLoading(true);
      try {
        const res = await fetch(
          `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/setup`,
          { credentials: "include" },
        );
        if (!res.ok) {
          setError(`Could not load channels (${res.status}).`);
          return;
        }
        setError(null);
        setData((await res.json()) as SetupResponse);
      } catch {
        setError("Could not load channels.");
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
          setError(String(body?.detail || `Setup failed (${res.status}).`));
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
        return { entry, observed, remediation: remediationFor(entry, observed, reachable) };
      }),
    [catalog, observedById, reachable],
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
    observedById,
  };
}

/** The generated form. Every control below is driven by `entry.fields`; there
 *  is no branch on a channel id anywhere in it. */
export function CredentialDialog({
  gatewayId,
  entry,
  observed,
  onClose,
  onSaved,
}: {
  gatewayId: string;
  entry: OpenClawChannelCatalogEntry;
  observed: OpenClawObservedChannel | undefined;
  onClose: () => void;
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
        setError(String(body?.detail || `Save failed (${res.status}).`));
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
    <div className="fleet-detail-backdrop" onClick={onClose}>
      {/* .fleet-channel-banner: the exact shape every row in the unified list
          opens into (header + icon + title + close, then a scrolling body) —
          this dialog reuses it rather than a bespoke, differently-sized
          panel. */}
      <div
        className="fleet-channel-banner"
        role="dialog"
        aria-modal="true"
        aria-labelledby="openclaw-dialog-heading"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="fleet-channel-banner-header">
          <span className="fleet-channel-banner-icon">{entry.label.charAt(0)}</span>
          <span className="fleet-channel-banner-title" id="openclaw-dialog-heading">
            {entry.label}
          </span>
          <button type="button" className="fleet-detail-close fleet-detail-close--inline" onClick={onClose} aria-label="Close">
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        <div className="fleet-channel-banner-body">
          <p className="fleet-subtitle" style={{ marginTop: 0 }}>
            {entry.selection_label}. These are the fields this channel needs to connect.
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
            <button type="button" className="fleet-btn" onClick={onClose} disabled={saving}>
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
        </div>
      </div>
    </div>
  );
}
