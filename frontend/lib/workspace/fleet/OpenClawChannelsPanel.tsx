"use client";

/**
 * Channel setup for the OpenClaw transport — every channel, one screen, from
 * the browser. No terminal, no SSH.
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
import {
  AlertTriangle,
  Check,
  Loader2,
  Plug,
  RefreshCw,
  Smartphone,
  X,
} from "lucide-react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import {
  formatChannelList,
  remediationFor,
  type OpenClawChannelCatalogEntry,
  type OpenClawObservedChannel,
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
  // that decides which channels THIS panel lists, never hand-typed here.
  // See channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS.
  already_available_channels?: string[];
};

// .fleet-badge is the shared status-chip primitive (Members, Hardware,
// Connections all use it) — this only adds the ok/off colour, never a new
// shape, so a chip here reads as the same control everywhere else it appears.
function StateChip({ ok, on, off }: { ok: boolean; on: string; off: string }) {
  return (
    <span
      className={`fleet-badge openclaw-chip ${ok ? "openclaw-chip--ok" : "openclaw-chip--off"}`}
      style={{ marginLeft: 0 }}
    >
      {ok ? on : off}
    </span>
  );
}

export function OpenClawChannelsPanel({
  workspaceId,
  gatewayId,
  agentId,
}: {
  workspaceId: string;
  gatewayId: string;
  agentId: string;
}) {
  const [data, setData] = useState<SetupResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<OpenClawChannelCatalogEntry | null>(null);

  const load = useCallback(
    async (opts?: { silent?: boolean }) => {
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

  const rows = useMemo(
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

  return (
    <section aria-labelledby="openclaw-channels-heading">
      <h2 className="fleet-detail-section-title" id="openclaw-channels-heading">
        Channels
      </h2>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        Messaging channels connected through this computer.
      </p>
      {alreadyAvailable.length > 0 ? (
        // Makes the exclusion legible instead of silent: the founder read a
        // missing Telegram card as "did you turn this off?" with nothing on
        // screen to answer that. These channels are correct to omit here —
        // they already work through the setup above — but the omission
        // needs a reason, in one line, not a per-card explanation.
        <p className="fleet-subtitle openclaw-elsewhere-note" style={{ marginTop: 0 }}>
          {formatChannelList(alreadyAvailable)} already connect through the setup above. They will move
          into this list once each is verified here individually.
        </p>
      ) : null}

      {error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {data?.observed_error ? (
        <div className="openclaw-banner" role="status">
          <AlertTriangle size={14} aria-hidden />
          <span>
            This computer could not be reached, so the states below are unknown. The setup fields below
            are still accurate.
          </span>
        </div>
      ) : null}

      <div className="openclaw-toolbar">
        {/* The single accent action in this view. Everything in a row is
            neutral, so there is exactly one primary action on screen. */}
        <button
          type="button"
          className={`fleet-btn ${repairable.length > 0 ? "fleet-btn--accent-fill" : ""}`}
          onClick={() => void provision(repairable)}
          disabled={busy !== null || loading}
        >
          {busy === "__all__" ? <Loader2 size={14} className="openclaw-spin" /> : <Plug size={14} />}
          {repairable.length > 0 ? `Set up ${repairable.length} channel${repairable.length === 1 ? "" : "s"}` : "Re-check this computer"}
        </button>
        <button
          type="button"
          className="fleet-btn"
          onClick={() => void load()}
          disabled={busy !== null || loading}
          aria-label="Refresh channel state"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {loading ? (
        <p className="fleet-subtitle">Reading this computer…</p>
      ) : rows.length === 0 ? (
        <div className="fleet-empty">
          <div className="fleet-empty-title">No channels</div>
          <div className="fleet-empty-desc">
            This computer reported no channels, which should be impossible. Contact support.
          </div>
        </div>
      ) : (
        // .fleet-list / .fleet-list-row: the same flat-hairline-row primitive
        // Members, Hardware and Connections already use — not a bespoke
        // bordered card floating inside the section. That card-within-a-card
        // treatment was exactly what read as bolted on.
        <div className="fleet-list openclaw-channel-list">
          {rows.map(({ entry, observed, remediation }) => (
            <div key={entry.channel_key} className="fleet-list-row openclaw-channel-row">
              <div className="fleet-list-row-main">
                <div className="fleet-list-row-title">{entry.label}</div>
                <div className="openclaw-channel-selection">{entry.selection_label}</div>
                <div className="openclaw-chips">
                  {entry.requires_plugin ? (
                    <StateChip ok={Boolean(observed?.installed)} on="installed" off="not installed" />
                  ) : (
                    <span className="fleet-badge openclaw-chip openclaw-chip--ok" style={{ marginLeft: 0 }}>
                      bundled
                    </span>
                  )}
                  {entry.connect_method === "credential" ? (
                    <StateChip ok={Boolean(observed?.configured)} on="credential set" off="no credential" />
                  ) : null}
                  <StateChip ok={Boolean(observed?.enabled)} on="on" off="off" />
                </div>
                <div className="openclaw-channel-detail">{remediation.detail}</div>
              </div>
              <div className="openclaw-channel-actions">
                {remediation.kind === "install" || remediation.kind === "enable" ? (
                  <button
                    type="button"
                    className="fleet-btn"
                    onClick={() => void provision([entry.channel_key])}
                    disabled={busy !== null}
                  >
                    {busy === entry.channel_key ? (
                      <Loader2 size={14} className="openclaw-spin" />
                    ) : null}
                    {remediation.label}
                  </button>
                ) : null}
                {remediation.kind === "credential" ? (
                  <button
                    type="button"
                    className="fleet-btn"
                    onClick={() => setOpen(entry)}
                    disabled={busy !== null}
                  >
                    {remediation.label}
                  </button>
                ) : null}
                {remediation.kind === "ready" ? (
                  <>
                    <span className="openclaw-ready">
                      <Check size={14} aria-hidden /> Ready
                    </span>
                    <button type="button" className="fleet-btn" onClick={() => setOpen(entry)}>
                      Replace credential
                    </button>
                  </>
                ) : null}
                {remediation.kind === "elsewhere" ? (
                  <span className="openclaw-ready openclaw-ready--muted">
                    <Smartphone size={14} aria-hidden /> Links on the device
                  </span>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}

      {open ? (
        <CredentialDialog
          gatewayId={gatewayId}
          entry={open}
          observed={observedById.get(open.channel_id)}
          onClose={() => setOpen(null)}
          onSaved={async () => {
            setOpen(null);
            await load({ silent: true });
          }}
        />
      ) : null}
    </section>
  );
}

/** The generated form. Every control below is driven by `entry.fields`; there
 *  is no branch on a channel id anywhere in it. */
function CredentialDialog({
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
      {/* .fleet-channel-banner: the exact shape the Telegram/WhatsApp/Discord/
          Signal/iMessage/Slack/WeChat cards above already open into (header +
          icon + title + close, then a scrolling body) — this dialog reuses it
          rather than the bespoke, differently-sized panel it used to be. */}
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
