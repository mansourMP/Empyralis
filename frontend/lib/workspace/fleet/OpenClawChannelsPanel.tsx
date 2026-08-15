"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

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
import { runMutationWithBestEffortRefresh } from "@/lib/workspace/mutation-outcome";
import {
  remediationFor,
  splitCredentialFields,
  type OpenClawChannelCatalogEntry,
  type OpenClawCredentialField,
  type OpenClawObservedChannel,
  type Remediation,
} from "./openclaw-channel-copy";

import { QR_NO_CODE_TEXT, resolveQrPanelView } from "./channel-qr-phase";
import {
  channelAccountLine,
  channelSummaryRows,
  setupInstructionsFor,
  setupQuestionFor,
  type ChannelSetupHelp,
  type ChannelSetupWizard,
} from "./channel-setup-flow";
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
  // The STRUCTURED reason behind observed_error above (e.g.
  // "gateway_capability_missing"), when the backend recognized one — see
  // gateway_reason_messages.KNOWN_REASON_TOKENS. null for an unreachable box
  // (a genuinely unclassified failure) or an older response shape; either
  // way openclawObservedErrorBanner degrades to the existing generic
  // "could not be reached" copy, never a raw token.
  observed_error_code?: string | null;
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
        const res = await fleetAuthorizedFetch(url, { credentials: "include" });
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
        const res = await fleetAuthorizedFetch(
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
    observedErrorCode: data?.observed_error_code ?? null,
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
/** THE INSTRUCTIONS ARE NOT OURS TO WRITE — see channel-setup-flow.ts.
 *
 *  Rendered above the one field they explain, so the connect screen asks one
 *  question and answers "where do I get this?" in the same breath.
 *
 *  The NUMBERING is ours and their ordinals are stripped in the generator, for
 *  one specific reason: a line a customer here cannot act on is dropped (their
 *  own docs URLs, an env-var tip), and keeping their text's ordinals then left
 *  a list that visibly started at "2)". A block that was not a numbered
 *  sequence upstream stays unnumbered rather than being forced into one. */
export function ChannelHelpLines({ block }: { block: ChannelSetupHelp }) {
  return block.ordered ? (
    <ol className="openclaw-steps-list">
      {block.lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ol>
  ) : (
    <>
      {block.lines.map((line) => (
        <p key={line} className="openclaw-steps-line">
          {line}
        </p>
      ))}
    </>
  );
}

export function SetupInstructions({ wizard }: { wizard: ChannelSetupWizard | null | undefined }) {
  const blocks = setupInstructionsFor(wizard);
  if (blocks.length === 0) return null;
  return (
    <div className="openclaw-steps">
      {blocks.map((block, index) => (
        <div key={`${block.title ?? "help"}-${index}`} className="openclaw-steps-block">
          <ChannelHelpLines block={block} />
        </div>
      ))}
    </div>
  );
}

export function CredentialForm({
  gatewayId,
  entry,
  observed,
  onCancel,
  onSaved,
  /** "Connect" while connecting, "Save" when replacing a credential that
   *  already works. Same form, two different things it is being asked to do,
   *  and a button that says the wrong one of them is a small lie on the only
   *  control that matters. */
  submitLabel = "Connect",
  cancelLabel = "Cancel",
  /** False inside the connected screen's Replace disclosure, where the
   *  instructions were already read once and are now noise. */
  showInstructions = true,
  /** False where closing the disclosure IS the cancel — a second control that
   *  does the same thing as collapsing the section is a dead control. */
  showCancel = true,
}: {
  gatewayId: string;
  entry: OpenClawChannelCatalogEntry;
  observed: OpenClawObservedChannel | undefined;
  /** Rendered as the form's secondary button. The panel that hosts this form
   *  owns closing itself; this only says "not now". */
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
  submitLabel?: string;
  cancelLabel?: string;
  showInstructions?: boolean;
  showCancel?: boolean;
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
      // The credential is already saved on the box the moment the PUT
      // resolves ok with no refusal — a failure to refresh this panel's
      // own state afterward must not be reported as "Could not reach this
      // computer," which would read as the save itself failing.
      await runMutationWithBestEffortRefresh(async () => {
        let res: Response;
        try {
          res = await fleetAuthorizedFetch(
            `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/channels/${encodeURIComponent(entry.channel_key)}/credential`,
            {
              method: "PUT",
              credentials: "include",
              headers: buildCookieAuthHeaders("PUT", { "Content-Type": "application/json" }),
              body: JSON.stringify({ values: payload }),
            },
          );
        } catch {
          // A genuine fetch()-level failure (offline, a dropped connection)
          // carries no useful message of its own — friendly text, same as
          // this form always showed, rather than a raw "Failed to fetch."
          throw new Error("Could not reach this computer.");
        }
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(getErrorMessage(body, `Save failed (${res.status}).`));
        }
        const refusal = body?.openclaw_channel_setup?.refusal;
        if (refusal) {
          throw new Error(`${refusal.code}: ${refusal.detail}`);
        }
        // Cleared rather than retained: nothing typed here is kept in the
        // tab any longer than the request needs it.
        setValues({});
      }, () => Promise.resolve(onSaved()));
    } catch (e) {
      // Restructuring the three failure branches (fetch-level, !res.ok,
      // refusal) into `throw` so they all land in this ONE catch also fixed
      // a real stuck-spinner bug: the previous early-return shape called
      // setError and `return`ed straight out of the !res.ok and refusal
      // branches without ever calling setSaving(false), so `saving` stayed
      // true forever on either path.
      setError(e instanceof Error ? e.message : "Could not reach this computer.");
    }
    setSaving(false);
  };

  const { primary, advanced } = splitCredentialFields(entry.fields);

  const renderField = (field: OpenClawCredentialField) => {
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
  };

  return (
    <>
      {error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {showInstructions ? <SetupInstructions wizard={entry.setup_wizard} /> : null}

      <div className="openclaw-form">{primary.map(renderField)}</div>

      {/* Collapsed by default and rendered only when there is something in it —
          an empty disclosure is a dead control. Everything OpenClaw's schema
          declares is still here and still writable; it is simply not what
          connecting the channel asks of anybody. */}
      {advanced.length > 0 ? (
        <details className="openclaw-form-advanced">
          <summary>Advanced</summary>
          <div className="openclaw-form">{advanced.map(renderField)}</div>
        </details>
      ) : null}

      <p className="openclaw-form-note">
        Leave a field blank to keep what is already on the computer. Values are sent straight to it
        and are not stored here.
      </p>

      <div className="openclaw-dialog-actions">
        {showCancel ? (
          <button type="button" className="fleet-btn" onClick={onCancel} disabled={saving}>
            {cancelLabel}
          </button>
        ) : null}
        <button
          type="button"
          className="fleet-btn fleet-btn--accent-fill"
          onClick={() => void save()}
          disabled={saving || filled === 0}
        >
          {saving ? <Loader2 size={14} className="openclaw-spin" /> : null}
          {saving ? "Saving…" : submitLabel}
        </button>
      </div>
    </>
  );
}

/** Step 2, for a channel whose plugin owns OpenClaw's QR seam: a code to scan.
 *
 *  A FORM BODY, NOT A SECOND DIALOG — same rule as CredentialForm above. The
 *  panel the card opened owns the shell; this is only what goes inside it.
 *
 *  THE STATE MACHINE, AND WHY IT CANNOT SHOW A STALE SQUARE
 *  --------------------------------------------------------
 *      idle ──"Show code"──▶ starting ──▶ showing(code) ──┐
 *                                             │           │ poll: link_wait,
 *                                             │           │ carrying the code
 *                                             │           │ on screen RIGHT NOW
 *                                             ├◀──────────┘
 *                                             │  a DIFFERENT code came back
 *                                             │  -> swap it in, keep waiting
 *                                             ▼
 *                                          linked   (or) failed
 *
 *  The poll is not a timer guessing at an expiry. `link_wait` hands OpenClaw
 *  the exact code being displayed and blocks; OpenClaw answers only when that
 *  code is scanned or ROTATES, and a rotation comes back as new bytes. So the
 *  square on screen is the live one by construction. Measured against real
 *  WhatsApp on the rig: a rotation arrived after 20.2s carrying OpenClaw's own
 *  message "QR refreshed."
 *
 *  A code is a credential in flight: whoever scans it links THEIR account. It
 *  lives in this component's state for as long as it is on screen and is
 *  written nowhere else — no storage, no URL, no log. */
export function ChannelLinkForm({
  gatewayId,
  entry,
  onCancel,
  onLinked,
}: {
  gatewayId: string;
  entry: OpenClawChannelCatalogEntry;
  onCancel: () => void;
  onLinked: () => void | Promise<void>;
}) {
  const [qr, setQr] = useState<string | null>(null);
  const [requestInFlight, setRequestInFlight] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [linked, setLinked] = useState(false);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  // Survives a re-render without causing one, and is what a pending poll reads
  // to decide whether it still has a reason to exist.
  const cancelledRef = useRef(false);

  useEffect(() => {
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  // WHAT IS DRAWN IS NOT DECIDED HERE. `resolveQrPanelView` (channel-qr-phase.ts)
  // owns it, and it already exists: it was written for the first-party WhatsApp
  // panel, kept its test, and lost its only caller when that panel was deleted
  // in the 2026-08-14 cutover — "built, tested, and never wired", the shape
  // CLAUDE.md warns about, sitting one directory away. Reusing it rather than
  // re-deriving the same five states is what keeps the invariant it exists to
  // enforce: a spinner and a "start this" control are never on screen together,
  // in either direction, and that is proven by a test that drives every
  // combination of these six inputs rather than by anyone remembering it here.
  const view = resolveQrPanelView({
    qrImageReady: qr !== null,
    requestInFlight,
    errorText: error,
    codeIssued: qr !== null,
    accepted,
    // Our wait is a BLOCKING server call that returns on a scan or a rotation,
    // not a client-side timer hoping something arrives — so there is no local
    // deadline that can quietly run out. A failed hop surfaces as `errorText`.
    waitExpired: false,
  });

  const call = async (body: Record<string, unknown>) => {
    let res: Response;
    try {
      res = await fleetAuthorizedFetch(
        `/api/personal-channels/openclaw/gateways/${encodeURIComponent(gatewayId)}/channels/${encodeURIComponent(entry.channel_key)}/link`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify(body),
        },
      );
    } catch {
      throw new Error("Could not reach this computer.");
    }
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(getErrorMessage(payload, `Link failed (${res.status}).`));
    const setup = payload?.openclaw_channel_setup ?? {};
    const refusal = setup?.refusal ?? setup?.link?.refusal;
    if (refusal) throw new Error(String(refusal.detail || refusal.code));
    return setup?.link ?? {};
  };

  /** One `link_wait` hop. Recurses only while the code it was given is still
   *  the code on screen, so a Cancel — or a newer start — ends the chain
   *  instead of racing it. */
  const waitFor = async (code: string) => {
    if (cancelledRef.current) return;
    let link: { linked?: boolean; qr_data_url?: string | null; message?: string };
    try {
      link = await call({ action: "link_wait", current_qr_data_url: code });
    } catch (e) {
      if (cancelledRef.current) return;
      setError(e instanceof Error ? e.message : "Could not reach this computer.");
      setQr(null);
      return;
    }
    if (cancelledRef.current) return;
    if (link.linked) {
      setQr(null);
      setLinked(true);
      setMessage(link.message || "");
      await onLinked();
      return;
    }
    if (link.qr_data_url && link.qr_data_url !== code) {
      // Rotated. Swapping the bytes IS the expiry handling — there is never a
      // moment where the square on screen is one the platform has retired.
      setQr(link.qr_data_url);
      setMessage(link.message || "");
      void waitFor(link.qr_data_url);
      return;
    }
    // Same code still valid, or a poll that answered with nothing new: keep
    // waiting on the code already displayed.
    void waitFor(code);
  };

  const start = async (force: boolean) => {
    cancelledRef.current = false;
    setError(null);
    setRequestInFlight(true);
    try {
      const link = await call({ action: "link_start", force });
      if (cancelledRef.current) return;
      setAccepted(true);
      if (link.linked) {
        setLinked(true);
        setMessage(link.message || "");
        await onLinked();
        return;
      }
      if (!link.qr_data_url) {
        // "The box accepted the request and produced no code" is its own
        // outcome, and channel-qr-phase already owns the sentence for it
        // (QR_NO_CODE_TEXT) — reached by leaving `accepted` true with no code
        // rather than by writing a second sentence here.
        setError(link.message || QR_NO_CODE_TEXT);
        return;
      }
      setQr(link.qr_data_url);
      setMessage(link.message || "");
      void waitFor(link.qr_data_url);
    } catch (e) {
      if (cancelledRef.current) return;
      setError(e instanceof Error ? e.message : "Could not reach this computer.");
    } finally {
      if (!cancelledRef.current) setRequestInFlight(false);
    }
  };

  return (
    <>
      {error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {linked ? (
        <p className="openclaw-form-note" role="status">
          {message || "This channel is linked."}
        </p>
      ) : (
        <div className="openclaw-link-body">
          {/* The instruction line. The box's own message wins when it has one —
              OpenClaw names where to scan for the channel it is actually
              linking ("Scan this QR in WhatsApp → Linked Devices."), and its
              product, not ours, is the thing that knows. The shared hint is the
              fallback for the phases where the box has said nothing yet. */}
          <p className="openclaw-form-note" aria-live="polite">
            {view.showQrImage && message ? message : view.hint}
          </p>

          {view.showQrImage && qr ? (
            /* eslint-disable-next-line @next/next/no-img-element -- a data: URL
               held in memory only while it is on screen; next/image wants a
               real URL and a loader, for bytes that must never be cached. */
            <img className="openclaw-link-qr" src={qr} alt="" width={232} height={232} />
          ) : null}

          {view.showSpinner ? <Loader2 size={16} className="openclaw-spin" aria-hidden /> : null}

          {view.failureText ? (
            <p className="openclaw-channel-detail" role="alert">
              {view.failureText}
            </p>
          ) : null}
        </div>
      )}

      <div className="openclaw-dialog-actions">
        <button
          type="button"
          className="fleet-btn"
          onClick={() => {
            cancelledRef.current = true;
            onCancel();
          }}
        >
          {linked ? "Done" : "Cancel"}
        </button>
        {/* NO CONTROL AT ALL while something is running — `startControlLabel` is
            null in exactly those phases, which is the invariant channel-qr-phase
            exists to hold. Not a disabled button: a control that cannot be used
            is not rendered (product law). */}
        {!linked && view.startControlLabel ? (
          <button
            type="button"
            className="fleet-btn fleet-btn--accent-fill"
            onClick={() => void start(view.phase === "failed")}
          >
            {view.startControlLabel}
          </button>
        ) : null}
      </div>
    </>
  );
}

/** Who may message this agent DIRECTLY on ONE OpenClaw-transported channel.
 *
 *  The sibling of GroupAllowlistForm below, and the axis that had no screen
 *  AND no route at all until 2026-08-14 — worse than the group axis was,
 *  because its default did not merely leave a gate open, it made the whole
 *  transport refuse to run. `dm_policy` defaulted to "open", which the
 *  gateway renders as OpenClaw's own `dmPolicy: "open"`, which their
 *  mandatory `security audit` calls CRITICAL, which our lockdown treats as
 *  blocking. No customer could ever provision an OpenClaw channel on any
 *  box, and there was no control anywhere that could have changed the value
 *  responsible.
 *
 *  ONE MODE, SO NO MODE PICKER. The transport can express exactly one of
 *  Empyralis's four DM modes (allowlist); the other three all render as
 *  OpenClaw's `open` and put the box back in the state above. The backend
 *  serves that as `settable_modes` and refuses the rest with a 422, so this
 *  form saves mode: "allowlist" and offers no choice — a picker of one is a
 *  dead control, and a picker of four where three are refused is worse.
 *
 *  THE PEOPLE LIST CANNOT BE SHOWN, for the same reason the group list
 *  cannot: gate-before-model means a message from someone not on this list
 *  is refused before it is ever recorded, so no history exists to populate a
 *  picker from. A field to type an ID into is the honest form of that. */
type DmPolicyState = {
  allowlist: string[];
};

export function DmAllowlistForm({
  gatewayId,
  agentId,
  channelKey,
  channelLabel,
  /** OpenClaw's own answer to "where do I find that id?" — the hardest part
   *  of this control and the one this product previously answered with an
   *  empty text box. Their words, generated, never authored per channel; see
   *  channel-setup-flow.ts. */
  senderIdHelp,
}: {
  gatewayId: string;
  agentId: string;
  channelKey: string;
  channelLabel: string;
  senderIdHelp?: (ChannelSetupHelp & { placeholder: string | null }) | null;
}) {
  const [policy, setPolicy] = useState<DmPolicyState | null>(null);
  const [loading, setLoading] = useState(true);
  // "loaded, and nobody is allowed yet" and "the read failed" are different
  // facts and never share a message — see GroupAllowlistForm's own note.
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newSenderId, setNewSenderId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/personal-channels/${encodeURIComponent(channelKey)}/gateways/${encodeURIComponent(gatewayId)}/dm-policy?agent_id=${encodeURIComponent(agentId)}`,
        { credentials: "include", headers: buildCookieAuthHeaders("GET") },
      );
      const body = await res.json().catch(() => ({}));
      const config = body?.dm_policy;
      if (res.ok && config) {
        setPolicy({ allowlist: Array.isArray(config.allowlist) ? config.allowlist.map(String) : [] });
        setLoadFailed(false);
      } else {
        setLoadFailed(true);
      }
    } catch {
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, [channelKey, gatewayId, agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (nextAllowlist: string[]) => {
      setSaving(true);
      setError(null);
      try {
        const res = await fleetAuthorizedFetch(
          `/api/personal-channels/${encodeURIComponent(channelKey)}/gateways/${encodeURIComponent(gatewayId)}/dm-policy?agent_id=${encodeURIComponent(agentId)}`,
          {
            method: "PATCH",
            credentials: "include",
            headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
            body: JSON.stringify({ mode: "allowlist", allowlist: nextAllowlist }),
          },
        );
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          setError(getErrorMessage(body, `Could not save (${res.status}).`));
          return;
        }
        setPolicy({ allowlist: nextAllowlist });
        // Saved is saved — it is in Postgres by the time this response
        // exists. What follows is only whether the computer's own copy
        // caught up, and the three outcomes never share one message.
        const provisioning = body?.openclaw_provisioning;
        if (provisioning?.status === "agent_conflict") {
          setError(provisioning.message || "Another agent on this computer already uses this channel.");
        } else if (provisioning?.status === "unreachable") {
          setError(provisioning.message || "Saved, but this computer could not be reached to apply it yet.");
        } else if (provisioning?.status === "refused") {
          setError(dmRefusalMessage(provisioning?.refusal?.detail, nextAllowlist.length));
        }
      } catch {
        setError("Could not reach this computer.");
      } finally {
        setSaving(false);
      }
    },
    [channelKey, gatewayId, agentId],
  );

  if (loading) {
    return (
      <p className="fleet-subtitle" style={{ marginTop: "var(--space-4)" }}>
        Reading who can message this agent…
      </p>
    );
  }

  if (loadFailed) {
    return (
      <div style={{ marginTop: "var(--space-4)" }}>
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>Could not read who can message this agent just now.</span>
        </div>
        <button type="button" className="fleet-btn" onClick={() => void load()}>
          Try again
        </button>
      </div>
    );
  }

  const senders = policy?.allowlist ?? [];
  const addSender = () => {
    const trimmed = newSenderId.trim();
    if (!trimmed || senders.includes(trimmed)) return;
    setNewSenderId("");
    void save([...senders, trimmed]);
  };

  return (
    <div
      style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-4)", borderTop: "1px solid var(--border)" }}
    >
      {error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <p className="openclaw-form-note" style={{ marginTop: 0 }}>
        {senders.length === 0
          ? `Nobody can message this agent on ${channelLabel} yet — including you. Add a ${channelLabel} ID below to let that person start a conversation with it.`
          : `${senders.length === 1 ? "The person" : "The people"} below can message this agent on ${channelLabel}. Everyone else is ignored.`}
      </p>

      {senders.length > 0 ? (
        <div className="openclaw-group-list">
          {senders.map((senderId) => (
            <div key={senderId} className="openclaw-group-chip">
              <span className="openclaw-group-chip-id">{senderId}</span>
              <button
                type="button"
                className="openclaw-group-chip-remove"
                disabled={saving}
                aria-label={`Remove ${senderId}`}
                onClick={() => void save(senders.filter((id) => id !== senderId))}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : null}

      {senderIdHelp && senderIdHelp.lines.length > 0 ? (
        <details className="openclaw-form-advanced openclaw-help-disclosure">
          <summary>{senderIdHelp.title ? `Where to find a ${senderIdHelp.title}` : "Where to find this"}</summary>
          <div className="openclaw-steps">
            <ChannelHelpLines block={senderIdHelp} />
          </div>
        </details>
      ) : null}

      <div className="openclaw-group-add">
        <input
          className="fleet-wizard-input"
          type="text"
          autoComplete="off"
          spellCheck={false}
          placeholder={senderIdHelp?.placeholder || `${channelLabel} ID`}
          aria-label={`${channelLabel} ID`}
          value={newSenderId}
          disabled={saving}
          onChange={(event) => setNewSenderId(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") addSender();
          }}
        />
        <button type="button" className="fleet-btn" disabled={saving || !newSenderId.trim()} onClick={addSender}>
          Add
        </button>
      </div>
    </div>
  );
}

/** WHICH identity on this channel is the OWNER — the deliberate owner action
 *  that makes owner authority reachable from a channel at all.
 *
 *  Sits beside DmAllowlistForm and is emphatically NOT part of it. The
 *  allowlist answers "who may message this agent"; this answers "who IS the
 *  owner", and they are different facts with very different consequences —
 *  the allowlist is a list the owner grows to admit other people, so reading
 *  it as owner identity would hand shell and hardware authority to every one
 *  of them the moment they were admitted. They are two controls, on two
 *  routes, storing two things, on purpose.
 *
 *  WHY IT HAS TO BE TYPED, rather than picked or inferred. Nothing may derive
 *  the owner from an inbound message's own sender fields: the per-message
 *  state sync used to do exactly that and silently replaced the owner with
 *  whichever stranger had most recently texted (see
 *  personal_channels_service._resolve_linked_identity_for_sync). And there is
 *  no list to pick from for the same reason the allowlist has none —
 *  gate-before-model means an unadmitted message is refused before it is ever
 *  recorded, so no history exists.
 *
 *  ONE FIELD, TWO STATES, and the empty one is not an error: an agent with no
 *  owner on a channel is a legitimate configuration (an audience-facing
 *  agent), so this states what is true rather than nagging. Clearing is a
 *  first-class action — a mistaken link that could never be undone would be
 *  worse than no link at all. */
type OwnerIdentityState = {
  senderId: string | null;
};

export function OwnerIdentityForm({
  gatewayId,
  agentId,
  channelKey,
  channelLabel,
}: {
  gatewayId: string;
  agentId: string;
  channelKey: string;
  channelLabel: string;
}) {
  const [owner, setOwner] = useState<OwnerIdentityState | null>(null);
  const [loading, setLoading] = useState(true);
  // "loaded, and nobody is linked" and "the read failed" are different facts
  // and never share a message — the same rule the two allowlist forms follow.
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const url = `/api/personal-channels/${encodeURIComponent(channelKey)}/gateways/${encodeURIComponent(gatewayId)}/owner-identity?agent_id=${encodeURIComponent(agentId)}`;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(url, {
        credentials: "include",
        headers: buildCookieAuthHeaders("GET"),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        setOwner({ senderId: body?.sender_id ? String(body.sender_id) : null });
        setLoadFailed(false);
      } else {
        setLoadFailed(true);
      }
    } catch {
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (nextSenderId: string) => {
      setSaving(true);
      setError(null);
      try {
        const res = await fleetAuthorizedFetch(url, {
          method: "PUT",
          credentials: "include",
          headers: buildCookieAuthHeaders("PUT", { "Content-Type": "application/json" }),
          body: JSON.stringify({ sender_id: nextSenderId }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          setError(getErrorMessage(body, `Could not save (${res.status}).`));
          return;
        }
        // Read the SERVER's answer rather than echoing the draft: it
        // canonicalizes the id on the way in, so echoing would show the
        // owner something different from what actually decides authority.
        setOwner({ senderId: body?.sender_id ? String(body.sender_id) : null });
        setDraft("");
      } catch {
        setError("Could not save this just now — nothing changed, so it is safe to try again.");
      } finally {
        setSaving(false);
      }
    },
    [url],
  );

  if (loading) {
    return (
      <p className="fleet-subtitle" style={{ marginTop: "var(--space-4)" }}>
        Reading who this agent treats as you…
      </p>
    );
  }

  if (loadFailed) {
    return (
      <div style={{ marginTop: "var(--space-4)" }}>
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>Could not read who this agent treats as you.</span>
        </div>
        <button type="button" className="fleet-btn" onClick={() => void load()}>
          Try again
        </button>
      </div>
    );
  }

  const linked = owner?.senderId ?? null;

  return (
    <div
      style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-4)", borderTop: "1px solid var(--border)" }}
    >
      {error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <p className="openclaw-form-note" style={{ marginTop: 0 }}>
        {linked
          ? `Messages from this ${channelLabel} ID are treated as coming from you, so this agent will run commands and use your computer for them. Everyone else is served, not obeyed.`
          : `This agent does not know which ${channelLabel} ID is yours, so it treats every message as coming from someone else — it will answer, but it will not run commands or use your computer. Add your own ${channelLabel} ID to change that.`}
      </p>

      {linked ? (
        <div className="openclaw-group-list">
          <div className="openclaw-group-chip">
            <span className="openclaw-group-chip-id">{linked}</span>
            <button
              type="button"
              className="openclaw-group-chip-remove"
              disabled={saving}
              aria-label={`Remove ${linked}`}
              onClick={() => void save("")}
            >
              ×
            </button>
          </div>
        </div>
      ) : (
        <div className="openclaw-group-add">
          <input
            className="fleet-wizard-input"
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder={`Your ${channelLabel} ID`}
            aria-label={`Your ${channelLabel} ID`}
            value={draft}
            disabled={saving}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && draft.trim()) void save(draft.trim());
            }}
          />
          <button
            type="button"
            className="fleet-btn"
            disabled={saving || !draft.trim()}
            onClick={() => void save(draft.trim())}
          >
            {saving ? "Saving…" : "This is me"}
          </button>
        </div>
      )}
    </div>
  );
}

/** The one provisioning refusal an owner can hit from THIS form, translated.
 *
 *  Matched on the CHECK ID (`dm.scope_main_multiuser`), which is a stable
 *  identifier in OpenClaw's audit output, never on the sentence around it —
 *  this repo has already lost five weeks to an error bucket that matched
 *  prose someone later reworded.
 *
 *  What it actually means, measured on a real openclaw@2026.6.10: their
 *  audit warns whenever more than ONE sender may DM a channel while
 *  `session.dmScope` is still its default "main", and our generated config
 *  does not write `session.dmScope` at all. So the box accepts one allowed
 *  person and refuses two — recoverable (remove one and it provisions
 *  again), but a cliff the owner would otherwise meet as an OpenClaw check
 *  id. The real fix is one line in the gateway's config plan
 *  (`session.dmScope: "per-channel-peer"`), which is outside this file; until
 *  it lands, say the true thing rather than pass the finding through. */
export function dmRefusalMessage(detail: string | undefined, senderCount: number): string {
  if (detail && detail.includes("dm.scope_main_multiuser") && senderCount > 1) {
    return "Saved, but this computer will only run with one person allowed on this channel right now. Remove one to let it apply.";
  }
  return detail || "The computer could not apply this change.";
}

type GroupPolicyState = {
  allowlist: string[];
  requireMention: boolean;
};

/** Which groups this agent may reply in for ONE OpenClaw-transported
 *  channel, and whether it needs an @-mention there once it can. The same
 *  two facts the live group gate reads
 *  (personal_channels_service._load_agent_group_policy_config: mode /
 *  allowlist / require_mention) — this is a screen for an axis that
 *  already existed and, until now, had no screen: PATCH .../group-policy
 *  had zero frontend callers, so no owner could ever open a group by any
 *  means the product offered them, and a group stayed silent forever with
 *  nothing on screen explaining why.
 *
 *  THE GROUP LIST CANNOT BE SHOWN, ON PURPOSE — NOT A GAP TO FILL LATER.
 *  Gate-before-model means a message from a group this agent has not been
 *  cleared to answer is refused before it is ever recorded anywhere
 *  (personal_channels_service._handle_local_bridge_gateway_channel_inbound:
 *  the group gate runs BEFORE record_inbound_message), so there is no
 *  message history, however long, that could ever populate a picker here.
 *  Only the owner, from outside Empyralis, knows which groups this agent
 *  has actually been added to. A dropdown here would be exactly the dead-
 *  control defect this shell's own file doc names; the honest form of that
 *  is one sentence saying there is nothing to choose from, plus a place to
 *  type an ID once the owner has one.
 *
 *  ALWAYS SAVES mode: "allowlist" — never "open", never "disabled". This
 *  screen exists to let an owner deliberately open ONE group at a time; it
 *  has no control that widens beyond that, and the default (no groups
 *  allowed, mention required once one is) never changes on its own. */
export function GroupAllowlistForm({
  gatewayId,
  agentId,
  channelKey,
  channelLabel,
}: {
  gatewayId: string;
  agentId: string;
  channelKey: string;
  channelLabel: string;
}) {
  const [policy, setPolicy] = useState<GroupPolicyState | null>(null);
  const [loading, setLoading] = useState(true);
  // Distinct from "loaded, and there are genuinely zero groups yet" —
  // collapsing the two would tell an owner whose read failed (an expired
  // session, a network blip) that nothing they saved ever took, which is
  // the same "silence reads as absence" defect this codebase keeps
  // re-finding at other seams. A failed load renders its OWN state below,
  // never the empty-state copy.
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newGroupId, setNewGroupId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/personal-channels/${encodeURIComponent(channelKey)}/gateways/${encodeURIComponent(gatewayId)}/group-policy?agent_id=${encodeURIComponent(agentId)}`,
        { credentials: "include", headers: buildCookieAuthHeaders("GET") },
      );
      const body = await res.json().catch(() => ({}));
      const config = body?.group_policy;
      if (res.ok && config) {
        setPolicy({
          allowlist: Array.isArray(config.allowlist) ? config.allowlist.map(String) : [],
          // Absent/anything-but-false reads as the safe default (mention
          // required) — only an explicit `false` loosens it.
          requireMention: config.require_mention !== false,
        });
        setLoadFailed(false);
      } else {
        setLoadFailed(true);
      }
    } catch {
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, [channelKey, gatewayId, agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (nextAllowlist: string[], nextRequireMention: boolean) => {
      setSaving(true);
      setError(null);
      try {
        const res = await fleetAuthorizedFetch(
          `/api/personal-channels/${encodeURIComponent(channelKey)}/gateways/${encodeURIComponent(gatewayId)}/group-policy?agent_id=${encodeURIComponent(agentId)}`,
          {
            method: "PATCH",
            credentials: "include",
            headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
            body: JSON.stringify({
              mode: "allowlist",
              allowlist: nextAllowlist,
              require_mention: nextRequireMention,
            }),
          },
        );
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          setError(getErrorMessage(body, `Could not save (${res.status}).`));
          return;
        }
        setPolicy({ allowlist: nextAllowlist, requireMention: nextRequireMention });
        // The setting is saved either way (it is already in Postgres by the
        // time this response comes back) — this is only about whether the
        // computer's own copy actually caught up just now. Three distinct
        // outcomes, never collapsed: it reached the computer, another agent
        // already owns this channel, or the computer could not be reached.
        const provisioning = body?.openclaw_provisioning;
        if (provisioning?.status === "agent_conflict") {
          setError(provisioning.message || "Another agent on this computer already uses this channel.");
        } else if (provisioning?.status === "unreachable") {
          setError(provisioning.message || "Saved, but this computer could not be reached to apply it yet.");
        } else if (provisioning?.status === "refused") {
          setError(provisioning.refusal?.detail || "The computer could not apply this change.");
        }
      } catch {
        setError("Could not reach this computer.");
      } finally {
        setSaving(false);
      }
    },
    [channelKey, gatewayId, agentId],
  );

  if (loading) {
    return (
      <p className="fleet-subtitle" style={{ marginTop: "var(--space-4)" }}>
        Reading group settings…
      </p>
    );
  }

  if (loadFailed) {
    return (
      <div style={{ marginTop: "var(--space-4)" }}>
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>Could not read group settings just now.</span>
        </div>
        <button type="button" className="fleet-btn" onClick={() => void load()}>
          Try again
        </button>
      </div>
    );
  }

  const groups = policy?.allowlist ?? [];
  const requireMention = policy?.requireMention ?? true;
  const addGroup = () => {
    const trimmed = newGroupId.trim();
    if (!trimmed || groups.includes(trimmed)) return;
    setNewGroupId("");
    void save([...groups, trimmed], requireMention);
  };

  return (
    <div
      style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-4)", borderTop: "1px solid var(--border)" }}
    >
      {error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <p className="openclaw-form-note" style={{ marginTop: 0 }}>
        {groups.length === 0
          ? `This agent stays silent in every ${channelLabel} group until you add one here. There's no list to choose from — Empyralis only learns about a group once you've allowed it. Add a group's ID below to let it start replying there.`
          : `This agent may reply in the ${channelLabel} ${groups.length === 1 ? "group" : "groups"} below. Add another by its ID, or remove one to close it again.`}
      </p>

      {groups.length > 0 ? (
        <div className="openclaw-group-list">
          {groups.map((groupId) => (
            <div key={groupId} className="openclaw-group-chip">
              <span className="openclaw-group-chip-id">{groupId}</span>
              <button
                type="button"
                className="openclaw-group-chip-remove"
                disabled={saving}
                aria-label={`Remove ${groupId}`}
                onClick={() => void save(groups.filter((id) => id !== groupId), requireMention)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="openclaw-group-add">
        <input
          className="fleet-wizard-input"
          type="text"
          autoComplete="off"
          spellCheck={false}
          placeholder="Group ID"
          aria-label="Group ID"
          value={newGroupId}
          disabled={saving}
          onChange={(event) => setNewGroupId(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") addGroup();
          }}
        />
        <button type="button" className="fleet-btn" disabled={saving || !newGroupId.trim()} onClick={addGroup}>
          Add
        </button>
      </div>

      {groups.length > 0 ? (
        <label
          style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", marginTop: "var(--space-3)" }}
        >
          <button
            type="button"
            role="switch"
            aria-checked={requireMention}
            aria-label="Only reply when mentioned"
            className={`fleet-toggle${requireMention ? " is-on" : ""}`}
            disabled={saving}
            onClick={() => void save(groups, !requireMention)}
          />
          <span style={{ fontSize: 13 }}>
            Only reply when mentioned
            <span style={{ display: "block", fontSize: 12, color: "var(--text-muted)" }}>
              Off replies to every message in these groups, not just ones that mention it.
            </span>
          </span>
        </label>
      ) : null}
    </div>
  );
}

// ── THE CONNECTED SCREEN ────────────────────────────────────────────────────
//
// "if a channel is already connected it should look different — genuinely
// different, not just everything editable." Founder, 2026-08-15.
//
// A working channel gets a READ-ONLY summary and one Edit. Nothing on it is a
// control except that button, so opening a channel that already works is a
// glance rather than a form to be careful around. What the summary states is
// read from the same three routes the settings screen writes to, so it can
// never describe a configuration that is not the live one.
//
// A FACT THAT COULD NOT BE READ IS ITS OWN STATE. Three independent reads back
// three independent facts, and any of them can fail on its own; a failed read
// rendering as "Nobody yet" would tell an owner their allowlist is empty when
// it is not. channelSummaryRows takes `null` for exactly this and renders it
// as unknown — the same "empty and could-not-load are different facts" law the
// rest of this codebase is held to.

export type ChannelPolicySummary = {
  dmAllowlistCount: number | null;
  groupAllowlistCount: number | null;
  ownerLinked: boolean | null;
  loading: boolean;
  reload: () => void;
};

export function useChannelPolicySummary(
  gatewayId: string | null,
  agentId: string,
  /** null while no channel's panel is open. A hook must be called
   *  unconditionally, so "there is nothing to read" is a value here rather
   *  than a reason not to call it. */
  channelKey: string | null,
): ChannelPolicySummary {
  const [state, setState] = useState<{
    dm: number | null;
    group: number | null;
    owner: boolean | null;
    loading: boolean;
  }>({ dm: null, group: null, owner: null, loading: true });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!gatewayId || !channelKey) {
      setState({ dm: null, group: null, owner: null, loading: false });
      return;
    }
    let cancelled = false;
    setState((current) => ({ ...current, loading: true }));
    const base = `/api/personal-channels/${encodeURIComponent(channelKey)}/gateways/${encodeURIComponent(gatewayId)}`;
    const query = `?agent_id=${encodeURIComponent(agentId)}`;
    const read = async (path: string) => {
      try {
        const res = await fleetAuthorizedFetch(`${base}${path}${query}`, {
          credentials: "include",
          headers: buildCookieAuthHeaders("GET"),
        });
        const body = await res.json().catch(() => ({}));
        return res.ok ? body : null;
      } catch {
        return null;
      }
    };
    void (async () => {
      const [dm, group, owner] = await Promise.all([
        read("/dm-policy"),
        read("/group-policy"),
        read("/owner-identity"),
      ]);
      if (cancelled) return;
      const count = (value: unknown) => (Array.isArray(value) ? value.length : null);
      setState({
        dm: dm ? count(dm?.dm_policy?.allowlist) : null,
        group: group ? count(group?.group_policy?.allowlist) : null,
        // `owner` present and `sender_id` absent is a real answer ("nobody is
        // the owner", a legitimate configuration for an audience-facing
        // agent); a failed read is not, and stays null.
        owner: owner ? Boolean(owner?.sender_id) : null,
        loading: false,
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [gatewayId, agentId, channelKey, nonce]);

  return {
    dmAllowlistCount: state.dm,
    groupAllowlistCount: state.group,
    ownerLinked: state.owner,
    loading: state.loading,
    reload: () => setNonce((value) => value + 1),
  };
}

export function ConnectedChannelSummary({
  entry,
  observed,
  summary,
  onEdit,
}: {
  entry: OpenClawChannelCatalogEntry;
  observed: OpenClawObservedChannel | undefined;
  summary: ChannelPolicySummary;
  onEdit: () => void;
}) {
  const account = channelAccountLine(observed?.accounts);
  const rows = channelSummaryRows({
    accounts: observed?.accounts ?? [],
    dmAllowlistCount: summary.dmAllowlistCount,
    groupAllowlistCount: summary.groupAllowlistCount,
    ownerLinked: summary.ownerLinked,
  });
  return (
    <div className="openclaw-summary">
      <p className="openclaw-summary-state">
        <span className="openclaw-summary-dot" aria-hidden />
        {/* "Ready", never "Connected" — a connection is proven by a real
            message arriving and nothing on this screen has seen one. The
            three state facts (present / credential set / switched on) are
            all true here simultaneously, which is what lets one honest line
            stand in for three chips; the moment any is false this screen is
            not the one being rendered. */}
        Ready — set up and switched on
      </p>
      {account ? <p className="openclaw-summary-account">{account}</p> : null}

      <dl className="openclaw-summary-rows">
        {rows.map((row) => (
          <div key={row.label} className="openclaw-summary-row">
            <dt>{row.label}</dt>
            <dd className={row.value == null ? "openclaw-summary-unknown" : undefined}>
              {row.value == null ? (summary.loading ? "Reading…" : "Couldn’t read") : row.value}
            </dd>
          </div>
        ))}
      </dl>

      <div className="openclaw-dialog-actions">
        <button type="button" className="fleet-btn" onClick={onEdit}>
          Edit
        </button>
      </div>
      <p className="openclaw-form-note">
        A message arriving on {entry.label} is what proves it works.
      </p>
    </div>
  );
}

/** Behind Edit, and only ever reachable from a channel that already works
 *  (planChannelSetupFlow enforces that structurally, not by convention).
 *
 *  The credential itself sits in a COLLAPSED disclosure rather than on the
 *  screen: replacing a working credential is rare, and a token field open
 *  beside two allowlists is exactly the "everything editable at once" this
 *  rework exists to end. Nothing is lost — it was reachable before and it is
 *  reachable now, one deliberate click further from an accident. */
export function ChannelSettingsBody({
  gatewayId,
  agentId,
  entry,
  observed,
  onSaved,
}: {
  gatewayId: string;
  agentId: string;
  entry: OpenClawChannelCatalogEntry;
  observed: OpenClawObservedChannel | undefined;
  onSaved: () => void | Promise<void>;
}) {
  return (
    <div className="openclaw-settings">
      {/* Direct messages first, groups second — that is the order the gates
          themselves run in, and the DM list is the one an owner has to fill
          before the channel answers anybody at all (including them). */}
      <DmAllowlistForm
        gatewayId={gatewayId}
        agentId={agentId}
        channelKey={entry.channel_key}
        channelLabel={entry.label}
        senderIdHelp={entry.setup_wizard?.sender_id_help ?? null}
      />
      <GroupAllowlistForm
        gatewayId={gatewayId}
        agentId={agentId}
        channelKey={entry.channel_key}
        channelLabel={entry.label}
      />
      {/* Last, because it is the only one of the three that changes what an
          admitted message is ALLOWED TO DO rather than who gets admitted —
          and because it only becomes actionable once the owner has admitted
          themselves above. Deliberately not folded into the DM list it sits
          under: "may message this agent" and "IS the owner" are different
          facts, and one control for both would promote every allowed sender
          to shell and hardware authority. */}
      <OwnerIdentityForm
        gatewayId={gatewayId}
        agentId={agentId}
        channelKey={entry.channel_key}
        channelLabel={entry.label}
      />
      {entry.connect_method === "credential" && entry.fields.length > 0 ? (
        <details className="openclaw-form-advanced openclaw-replace-disclosure">
          <summary>Replace credential</summary>
          <CredentialForm
            gatewayId={gatewayId}
            entry={entry}
            observed={observed}
            showInstructions={false}
            showCancel={false}
            submitLabel="Save"
            onCancel={() => undefined}
            onSaved={onSaved}
          />
        </details>
      ) : null}
    </div>
  );
}

/** The back affordance every screen past the first carries. A LINK-shaped
 *  control at the top of the body, never a second close button in the header
 *  — the header's X still means "leave this channel", and the two must not
 *  read as the same action. */
export function PanelBackBar({ label, onBack }: { label: string; onBack: () => void }) {
  return (
    <button type="button" className="openclaw-back" onClick={onBack}>
      <span aria-hidden>←</span> {label}
    </button>
  );
}
