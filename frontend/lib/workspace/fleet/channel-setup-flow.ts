/**
 * ONE QUESTION PER SCREEN — the step model behind the panel a channel card
 * opens, and the derivation of the setup instructions that screen renders.
 *
 * No React, no CSS import, no "use client", for the same reason
 * channel-doors.ts and openclaw-channel-copy.ts are pure: channel-setup-
 * flow.test.ts runs under plain `npx tsx`, outside Next.js/webpack, and drives
 * these functions against the REAL checked-in manifest rather than a fixture
 * transcribed from them.
 *
 * WHAT WAS WRONG
 * --------------
 * The panel did two jobs on one screen, whether or not the channel was
 * connected. The founder's words: it "is just open right there… everything
 * that is editable", he gets "no choice", and "if a channel is already
 * connected it should look different — genuinely different, not just
 * everything editable."
 *
 *     BEFORE                                   AFTER
 *     ┌──────────────────────────┐             pick a door (only if >1)
 *     │ bundled·configured·on    │ status          ↓
 *     │ botToken [············]  │ connect     connect: instructions + ONE form
 *     │ ▸ Advanced (6 fields)    │ connect         ↓
 *     │ Who can DM  [allowlist]  │ settings    connected: a read-only SUMMARY
 *     │ Groups      [allowlist]  │ settings        ↓  [Edit]
 *     │ Your Telegram ID [     ] │ settings    settings: the three policies
 *     │ [Save]                   │
 *     └──────────────────────────┘             one screen answers one question
 *
 * THE THREE RULES, ENCODED HERE RATHER THAN REMEMBERED AT EACH CALL SITE
 * ----------------------------------------------------------------------
 *  1. One question per screen. The screens are a discriminated union, so
 *     "a credential field AND two allowlists AND an owner-identity field at
 *     once" is not an expressible state — it is not a rule someone has to
 *     keep, it is a shape that cannot be built.
 *  2. Settings appear only AFTER it works. `editingSettings` is an input, but
 *     it can only ever produce the `settings` screen when the channel's own
 *     remediation is `ready`. A caller cannot open the policy forms on a
 *     channel that has no identity to reply with — configuring who may talk to
 *     an agent that cannot talk is a screen with nothing to act on.
 *  3. A back affordance on any step past the first. Which is `null` exactly
 *     when the screen IS the first one, so a back arrow is never rendered
 *     pointing at nothing.
 *
 * THE DOOR COUNT STILL DECIDES THE PICKER, and this module does not re-decide
 * it: `planDoors` (channel-doors.ts) is the one rule, and its `mode` is an
 * input here. One real door means the panel opens straight into setup and
 * `back` is null; two or more mean the choice comes first and every later
 * screen can return to it.
 */

import type { ChannelDoorPlan } from "./channel-doors";
import type { ChannelConnectMethod, Remediation } from "./openclaw-channel-copy";

// ── OpenClaw's own setup instructions ───────────────────────────────────────
//
// THE INSTRUCTIONS ARE NOT OURS TO WRITE. "Open Telegram, chat with @BotFather,
// run /newbot" is knowledge about somebody else's product; authoring it here
// would be twenty-four hand-written screens that go stale the day upstream
// renames a command, and simply absent for the twenty-fifth channel. OpenClaw
// ships the text per channel as the declarative wizard its own interactive
// setup renders, and the manifest generator derives it in the same pass that
// already derives the credential SHAPE — see `_setup_wizards` in
// scripts/generate_openclaw_channel_manifest.py.
//
// The shapes below mirror that generated block exactly. They are structural:
// nothing in this file names a channel, and a channel upstream adds tomorrow
// renders its own instructions with no change here.

export type ChannelSetupHelp = {
  title: string | null;
  lines: string[];
  /** True when every line upstream was a numbered step. THE RENDERER NUMBERS
   *  THE LIST, not the text: their own ordinals are stripped in the generator,
   *  because dropping a line a customer cannot act on otherwise leaves a list
   *  that visibly starts at "2)". A block mixing steps and prose is `false`
   *  and keeps its lines exactly as written, unnumbered. */
  ordered: boolean;
};

export type ChannelSetupWizardStep = {
  kind: "credential" | "text";
  input_key: string;
  label: string | null;
  prompt: string | null;
  placeholder: string | null;
  secret: boolean;
  required: boolean;
  help: ChannelSetupHelp | null;
};

export type ChannelSetupWizard = {
  /** False = the query could not be made for this channel (its plugin is not
   *  in the pinned bundle). An honest absence, never an instruction invented
   *  on our side — and it renders exactly like `true` with no steps: the bare
   *  generated form, the same one this panel always showed. */
  resolved: boolean;
  reason?: string;
  steps: ChannelSetupWizardStep[];
  /** Their own instructions for finding the id an allowlist takes ("DM
   *  @userinfobot", "read message.from.id"), which is a genuinely hard
   *  question this product previously answered with an empty text box. */
  sender_id_help: {
    title: string | null;
    lines: string[];
    ordered: boolean;
    placeholder: string | null;
  } | null;
};

/** The instruction blocks to render above a connect form, in wizard order.
 *
 *  A step with no usable lines contributes nothing — the generator drops the
 *  lines a customer here cannot act on (the transport's own docs URLs, an
 *  env-var tip that applies to their CLI and not to a browser), so a step can
 *  legitimately arrive with an empty list. Returning it anyway would render an
 *  empty heading, which is furniture. */
export function setupInstructionsFor(wizard: ChannelSetupWizard | null | undefined): ChannelSetupHelp[] {
  if (!wizard || !Array.isArray(wizard.steps)) return [];
  return wizard.steps
    .map((step) => step.help)
    .filter((help): help is ChannelSetupHelp => Boolean(help && help.lines && help.lines.length > 0));
}

/** The one thing the customer is being asked FOR on this screen, in the
 *  transport's own words, when it has said. Used as the connect screen's own
 *  heading so the screen states its single question — the whole point of one
 *  question per screen is that the question is legible. */
export function setupQuestionFor(wizard: ChannelSetupWizard | null | undefined): string | null {
  const step = wizard?.steps?.find((candidate) => candidate.required) ?? wizard?.steps?.[0];
  return step?.label || step?.prompt || null;
}

// ── The step model ──────────────────────────────────────────────────────────

/** Every screen the panel can be on. A discriminated union rather than a bag
 *  of booleans, because "connect AND settings at once" was the bug. */
export type ChannelSetupScreenKind =
  | "pick_door"
  /** No computer bound to this agent at all. One sentence, no control — a
   *  form the customer could not possibly complete is a dead control. */
  | "needs_hardware"
  /** The box could not be asked, or its plugin has not contributed its fields
   *  yet. Whatever the remediation says, and no control. */
  | "unknown"
  /** One button that does the work on the box. */
  | "install"
  /** Instructions + exactly ONE credential form (or one code to scan). */
  | "connect"
  /** Set up but switched off. One button. */
  | "enable"
  /** Nothing to paste and no code seam either — linked by hand on the
   *  computer. Says so, renders no control. */
  | "elsewhere"
  /** It works. A READ-ONLY summary, and one Edit. */
  | "connected"
  /** Behind Edit: who may message, groups, and which identity is the owner. */
  | "settings";

export type ChannelSetupFlow = {
  screen: ChannelSetupScreenKind;
  /** Where a back affordance returns to, or null when this screen IS the
   *  first one and therefore has nothing behind it. */
  back: "doors" | "connected" | null;
  /** Only on `connect`: which of the two connect shapes to render. Carried
   *  here rather than recomputed in the component so the screen and the shape
   *  cannot disagree. */
  connect: ChannelConnectMethod | null;
  /** Whether THIS screen should carry the chosen door's own "what this is"
   *  text (its `body`, and its `consequence` at whatever tone) above the
   *  control. See `showsDoorContext` — carried on the flow, rather than
   *  recomputed per call site, for the same reason `connect` is: the screen
   *  and the decision of what rides on top of it cannot disagree. */
  showDoorContext: boolean;
};

/** Whether this screen should carry the chosen door's own "what this is"
 *  text — its `body`, and its `consequence` at whatever tone — above the
 *  control the screen renders.
 *
 *  MAN-150: pressing a single-door channel (WhatsApp, and — since
 *  full_account was deleted — Telegram too) skipped straight from the card
 *  to a connect form, an install button, or a QR code, with no idea what was
 *  about to happen. The door's own `body`/`consequence` text already exists
 *  for exactly this — CLAUDE.md: "a door states its consequence on its face"
 *  — but it was authored onto the PICKER card, so a channel with only one
 *  real door (most of them, by design: an intermediate screen offering one
 *  option is a dead click) never rendered it anywhere. Picker mode has no
 *  such gap: every door's body and consequence sit on its own card, read the
 *  instant before it is picked. So this is true ONLY in direct mode
 *  (`!picker`), and only on the three screens where a connecting action is
 *  actually about to be taken — `connect` / `install` / `enable`. The other
 *  screens either already carry their own single sentence (`elsewhere`,
 *  `needs_hardware`, `unknown`) or need none (`connected`, `settings`,
 *  `pick_door` itself) — stacking the door's body on top of those would be
 *  the wall-of-text failure mode in the other direction, never the "no
 *  context at all" one this exists to fix. */
export function showsDoorContext(picker: boolean, screen: ChannelSetupScreenKind): boolean {
  return !picker && (screen === "connect" || screen === "install" || screen === "enable");
}

export type ChannelSetupFlowInput = {
  /** From planDoors — the ONE rule that decides whether a choice is shown. */
  doorPlan: ChannelDoorPlan;
  /** Whether a door is resolved: always true in `direct` mode, true in
   *  `picker` mode only once one has been picked. */
  doorChosen: boolean;
  /** The chosen door's own row state. Null when no door is resolved yet, or
   *  when the catalog carries no row for it. */
  remediation: Remediation | null;
  /** From connectMethodFor — paste / scan / device / unknown / unknown_link. */
  connectMethod: ChannelConnectMethod | null;
  /** The customer pressed Edit on the connected summary. Cannot open the
   *  policy screens on a channel that is not `ready` — see rule 2. */
  editingSettings: boolean;
};

export function planChannelSetupFlow(input: ChannelSetupFlowInput): ChannelSetupFlow {
  const picker = input.doorPlan.mode === "picker";

  // Every return below goes through this one place, so `showDoorContext`
  // (see `showsDoorContext`) can never be decided differently from the
  // screen it rides on — the exact bug class the rest of this function
  // already guards against for `back` and `connect`.
  const flow = (
    screen: ChannelSetupScreenKind,
    back: "doors" | "connected" | null,
    connect: ChannelConnectMethod | null,
  ): ChannelSetupFlow => ({ screen, back, connect, showDoorContext: showsDoorContext(picker, screen) });

  if (picker && !input.doorChosen) {
    return flow("pick_door", null, null);
  }

  // Past the door choice, "back" means the choice — and only when there WAS
  // one. A single-door channel opened straight into this screen, so there is
  // nothing behind it and no arrow is rendered.
  const back: "doors" | null = picker ? "doors" : null;

  if (!input.remediation) {
    return flow("unknown", back, null);
  }

  switch (input.remediation.kind) {
    case "needs_hardware":
      return flow("needs_hardware", back, null);
    case "install":
      return flow("install", back, null);
    case "credential":
      return flow("connect", back, input.connectMethod ?? "paste");
    case "link":
      return flow("connect", back, input.connectMethod ?? "scan");
    case "enable":
      return flow("enable", back, null);
    case "elsewhere":
      return flow("elsewhere", back, null);
    case "ready":
      // RULE 2 LIVES HERE, AND NOWHERE ELSE. `editingSettings` is only ever
      // honoured from `ready`; every other branch above ignores it entirely,
      // so no caller can reach the policy forms on a channel with no working
      // identity to apply them to.
      return input.editingSettings ? flow("settings", "connected", null) : flow("connected", back, null);
    default:
      return flow("unknown", back, null);
  }
}

// ── The connected summary ───────────────────────────────────────────────────
//
// A summary, not a form. The founder: "if a channel is already connected it
// should look different — genuinely different, not just everything editable."
//
// THE THREE STATE FACTS ARE NOT COLLAPSED INTO "connected". OpenClaw's own
// `channels list` is the bar this surface has been held to since it shipped —
// plugin present / credential set / switched on are three independent facts,
// and a single light throws away exactly the one that says which to go fix. On
// the connected screen all three are true simultaneously, so ONE line can
// state them honestly; the moment any is false the flow above is not on this
// screen at all, so "not installed" can never hide behind "not connected".

export type ChannelSummaryRow = {
  label: string;
  /** null = this fact could not be read just now. Rendered as its own state,
   *  never as an empty value — "nobody is allowed yet" and "the read failed"
   *  are different facts and never share a line. */
  value: string | null;
};

export type ChannelSummaryInput = {
  /** The channel's own identity on the platform, when the box reports one.
   *  Never invented: no account means no line, not a guessed handle. */
  accounts: string[];
  /** null while unread / on a failed read. */
  dmAllowlistCount: number | null;
  groupAllowlistCount: number | null;
  ownerLinked: boolean | null;
};

/** The read-only body of the connected screen. Pure so the copy is testable
 *  without a DOM, and so a state that cannot be read renders as unknown rather
 *  than as a confident zero. */
export function channelSummaryRows(input: ChannelSummaryInput): ChannelSummaryRow[] {
  return [
    {
      label: "Who can message",
      value:
        input.dmAllowlistCount == null
          ? null
          : input.dmAllowlistCount === 0
            ? "Nobody yet"
            : input.dmAllowlistCount === 1
              ? "1 person"
              : `${input.dmAllowlistCount} people`,
    },
    {
      label: "Groups",
      value:
        input.groupAllowlistCount == null
          ? null
          : input.groupAllowlistCount === 0
            ? "Off"
            : input.groupAllowlistCount === 1
              ? "1 group"
              : `${input.groupAllowlistCount} groups`,
    },
    {
      label: "Owner",
      value: input.ownerLinked == null ? null : input.ownerLinked ? "Linked" : "Not linked",
    },
  ];
}

/** The account line above the summary rows, or null when the box reported no
 *  account for this channel. Their identifier verbatim — we do not decorate
 *  it, and we never substitute the channel's label for a handle we do not
 *  have. */
export function channelAccountLine(accounts: string[] | null | undefined): string | null {
  const named = (accounts || []).map((account) => String(account || "").trim()).filter(Boolean);
  return named.length > 0 ? named.join(", ") : null;
}
