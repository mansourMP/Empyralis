/**
 * The channel setup flow: one question per screen, settings only after it
 * works, and instructions that are OpenClaw's own words rather than ours.
 *
 * DRIVEN OFF THE REAL MANIFEST AND THE REAL RULE FUNCTIONS
 * --------------------------------------------------------
 * Same discipline as channel-doors.ts / openclaw-channel-copy.test.ts: this
 * imports `planChannelSetupFlow` and reads
 * server_modules/openclaw_channel_manifest.json — the artifact the backend
 * actually serves — rather than pinning a fixture transcribed from either. A
 * check whose expected set comes from the thing it checks can only ever
 * confirm itself (CLAUDE.md), and a fixture that invents its own input cannot
 * notice the real input is shaped differently.
 *
 * Run: npx tsx lib/workspace/fleet/channel-setup-flow.test.ts
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { planDoors, type ChannelDoor } from "./channel-doors";
import type { Remediation } from "./openclaw-channel-copy";
import {
  channelAccountLine,
  channelSummaryRows,
  planChannelSetupFlow,
  setupInstructionsFor,
  setupQuestionFor,
  showsDoorContext,
  type ChannelSetupScreenKind,
  type ChannelSetupWizard,
} from "./channel-setup-flow";

let failures = 0;
function assert(condition: boolean, message: string) {
  if (!condition) {
    failures += 1;
    console.error(`FAIL: ${message}`);
  }
}

const door = (key: string): ChannelDoor => ({ key, label: key, body: "", real: true });
const ONE_DOOR = planDoors([door("only")]);
const TWO_DOORS = planDoors([door("a"), door("b")]);

const REMEDIATIONS: Remediation[] = [
  { kind: "needs_hardware", detail: "x" },
  { kind: "unknown", detail: "x" },
  { kind: "install", label: "Set up", detail: "" },
  { kind: "credential", label: "Add credential", detail: "x" },
  { kind: "link", label: "Show code", detail: "", qrKnown: true },
  { kind: "enable", label: "Turn on", detail: "" },
  { kind: "elsewhere", detail: "x" },
  { kind: "ready", detail: "x" },
];

// ── RULE 3: a back affordance on any step past the first, and never on the
//    first. A single-door channel opens straight into its one screen, so a
//    back arrow there would point at nothing. -------------------------------

for (const remediation of REMEDIATIONS) {
  const single = planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation,
    connectMethod: "paste",
    editingSettings: false,
  });
  assert(single.back === null, `single-door ${remediation.kind}: nothing behind the first screen`);

  const multi = planChannelSetupFlow({
    doorPlan: TWO_DOORS,
    doorChosen: true,
    remediation,
    connectMethod: "paste",
    editingSettings: false,
  });
  assert(multi.back === "doors", `multi-door ${remediation.kind}: back returns to the door choice`);
}

assert(
  planChannelSetupFlow({
    doorPlan: TWO_DOORS,
    doorChosen: false,
    remediation: null,
    connectMethod: null,
    editingSettings: false,
  }).screen === "pick_door",
  "two doors, none chosen: the choice comes first",
);
assert(
  planChannelSetupFlow({
    doorPlan: TWO_DOORS,
    doorChosen: false,
    remediation: null,
    connectMethod: null,
    editingSettings: false,
  }).back === null,
  "the door choice is the first screen and has no back",
);
assert(
  planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation: { kind: "credential", label: "Add credential", detail: "" },
    connectMethod: "paste",
    editingSettings: false,
  }).screen === "connect",
  "one door never shows a picker — a screen offering one option is a dead click",
);

// ── RULE 2: settings appear only AFTER it works. `editingSettings` is honoured
//    from `ready` and IGNORED everywhere else — the guarantee is structural,
//    not a condition each call site has to remember. ------------------------

for (const remediation of REMEDIATIONS) {
  const flow = planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation,
    connectMethod: "paste",
    editingSettings: true,
  });
  if (remediation.kind === "ready") {
    assert(flow.screen === "settings", "ready + Edit: the settings screen");
    assert(flow.back === "connected", "settings returns to the summary, not to the doors");
  } else {
    assert(
      flow.screen !== "settings",
      `${remediation.kind} + editingSettings must NOT reach settings — there is no working identity to configure`,
    );
  }
}

assert(
  planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation: { kind: "ready", detail: "" },
    connectMethod: null,
    editingSettings: false,
  }).screen === "connected",
  "ready without Edit: a summary, not a form",
);

// ── RULE 1: one question per screen. The screens are a union, so a screen is
//    exactly one of them — assert every remediation maps somewhere, and that
//    the connect shape travels WITH the connect screen rather than being
//    recomputed beside it. --------------------------------------------------

const EXPECTED: Record<Remediation["kind"], string> = {
  needs_hardware: "needs_hardware",
  unknown: "unknown",
  install: "install",
  credential: "connect",
  link: "connect",
  enable: "enable",
  elsewhere: "elsewhere",
  ready: "connected",
};
for (const remediation of REMEDIATIONS) {
  const flow = planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation,
    connectMethod: remediation.kind === "link" ? "scan" : "paste",
    editingSettings: false,
  });
  assert(flow.screen === EXPECTED[remediation.kind], `${remediation.kind} maps to ${EXPECTED[remediation.kind]}`);
  assert(
    (flow.connect !== null) === (flow.screen === "connect"),
    `${remediation.kind}: a connect shape exists exactly on the connect screen`,
  );
}
assert(
  planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation: { kind: "link", label: "Set up", detail: "", qrKnown: false },
    connectMethod: "unknown_link",
    editingSettings: false,
  }).connect === "unknown_link",
  "a pairing channel whose box has not reported a link shape still reaches the connect screen (it must OFFER a control — starting the link is what resolves it)",
);
assert(
  planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation: { kind: "elsewhere", detail: "" },
    connectMethod: "device",
    editingSettings: false,
  }).screen === "elsewhere",
  "an unreachable box degrades to the no-control screen rather than offering one that cannot work",
);

// ── MAN-150: the door's own "what this is" text rides on the SCREEN, not on
//    whether a picker happened to run. A single-door channel (WhatsApp today,
//    Telegram since full_account was deleted) skips the picker entirely, so
//    before this rule existed its `body`/`consequence` never reached the
//    customer at all — the connect form (or the QR) appeared with zero
//    context. Driven off the real `planChannelSetupFlow`, not a hand-rolled
//    boolean, so the screen and the decision of what rides on top of it can
//    never disagree. ------------------------------------------------------

const ALL_SCREENS: ChannelSetupScreenKind[] = [
  "pick_door",
  "needs_hardware",
  "unknown",
  "install",
  "connect",
  "enable",
  "elsewhere",
  "connected",
  "settings",
];
const CONNECTING_SCREENS: ChannelSetupScreenKind[] = ["connect", "install", "enable"];

for (const screen of ALL_SCREENS) {
  assert(
    showsDoorContext(false, screen) === CONNECTING_SCREENS.includes(screen),
    `direct mode, screen ${screen}: door context shows exactly on connect/install/enable`,
  );
  assert(
    showsDoorContext(true, screen) === false,
    `picker mode, screen ${screen}: never — the door's own card already carried this before the pick`,
  );
}

// The actual regression: a single real door (WhatsApp's shape — one
// "pairing"/QR door, no picker) reaching the connect screen must carry its
// context. Before this fix, `screen === "connect"` was true here and
// `showDoorContext` did not exist at all — a caller rendering the QR form had
// nothing telling it whether to show the door's body.
assert(
  planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation: { kind: "link", label: "Show code", detail: "", qrKnown: true },
    connectMethod: "scan",
    editingSettings: false,
  }).showDoorContext === true,
  "MAN-150: a single-door channel's QR/connect screen carries the door's own context — this is the whole fix",
);

// Two-door channels (Zalo today, once a third Telegram door lands) already
// showed the body/consequence on the picker card the instant before the
// pick — showing it again on the connect screen right after would be the
// wall-of-text failure mode in the other direction.
assert(
  planChannelSetupFlow({
    doorPlan: TWO_DOORS,
    doorChosen: true,
    remediation: { kind: "credential", label: "Add credential", detail: "" },
    connectMethod: "paste",
    editingSettings: false,
  }).showDoorContext === false,
  "a picker-mode channel already showed the door's context before the pick — not repeated on connect",
);

// Every non-connecting screen stays silent regardless of door count — the
// wall-of-text failure mode in the other direction, since these screens
// already carry their own single sentence (or need none).
for (const remediation of REMEDIATIONS) {
  if (CONNECTING_SCREENS.includes(EXPECTED[remediation.kind] as ChannelSetupScreenKind)) continue;
  const flow = planChannelSetupFlow({
    doorPlan: ONE_DOOR,
    doorChosen: true,
    remediation,
    connectMethod: remediation.kind === "link" ? "scan" : "paste",
    editingSettings: false,
  });
  assert(
    flow.showDoorContext === false,
    `${remediation.kind} (screen ${flow.screen}): not a connecting screen, so no door context — it already has its own sentence or needs none`,
  );
}

// ── THE INSTRUCTIONS ARE THEIRS. Read the checked-in manifest the backend
//    serves and assert the derivation produced usable, customer-safe text —
//    against real data, so this notices upstream changing shape. ------------

type ManifestChannel = {
  id: string;
  label: string;
  setup_wizard?: ChannelSetupWizard | null;
};
const manifest = JSON.parse(
  readFileSync(
    join(__dirname, "..", "..", "..", "..", "server_modules", "openclaw_channel_manifest.json"),
    "utf8",
  ),
) as { channels: ManifestChannel[] };

assert(manifest.channels.length > 0, "manifest loaded");

const withWizard = manifest.channels.filter((channel) => channel.setup_wizard);
assert(
  withWizard.length === manifest.channels.length,
  "every channel carries a setup_wizard block — `resolved: false` is how an unaskable channel says so, not an absent key",
);

const resolved = manifest.channels.filter((channel) => channel.setup_wizard?.resolved);
assert(resolved.length > 0, "at least one channel's wizard resolved — a manifest where none did is a broken read");

// Telegram is the channel the founder's own rig runs, and the one whose
// instructions the approved design shows verbatim. Asserted through the REAL
// derivation functions the panel calls, never against a pinned string.
const telegram = manifest.channels.find((channel) => channel.id === "telegram");
assert(Boolean(telegram), "the manifest carries telegram");
if (telegram) {
  const blocks = setupInstructionsFor(telegram.setup_wizard);
  assert(blocks.length > 0, "telegram renders at least one instruction block");
  const lines = blocks.flatMap((block) => block.lines);
  assert(
    lines.some((line) => line.includes("@BotFather")),
    "telegram's instructions still name the place a token comes from — this is the whole reason we render THEIRS",
  );
  assert(
    Boolean(setupQuestionFor(telegram.setup_wizard)),
    "the connect screen can state its single question in their words",
  );
}

// A filtered line must never leave a visibly renumbered list. The generator
// strips their ordinals and records `ordered` instead, so the renderer numbers
// the list itself — asserted here against every block in the real manifest,
// because the failure mode is a list that starts at "2)" and nothing else
// would catch it.
for (const channel of manifest.channels) {
  const blocks = [
    ...(channel.setup_wizard?.steps ?? []).map((step) => step.help),
    channel.setup_wizard?.sender_id_help ?? null,
  ].filter(Boolean) as { lines: string[]; ordered: boolean }[];
  for (const block of blocks) {
    for (const line of block.lines) {
      assert(
        !/^\s*\d+\s*[).]\s/.test(line),
        `${channel.id}: an instruction line still carries its own ordinal, which a filtered line can renumber wrongly: ${JSON.stringify(line)}`,
      );
    }
  }
}

// The two DERIVED exclusions the generator applies, asserted against every
// string that reaches a screen. Both are product law, not taste: the
// transport's own name never reaches a customer, and a tip about an
// environment variable is inapplicable in a browser form — a dead control in
// sentence form.
const ENV_VAR = /\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b/;
for (const channel of manifest.channels) {
  const wizard = channel.setup_wizard;
  if (!wizard) continue;
  const strings = [
    ...wizard.steps.flatMap((step) => [
      step.label ?? "",
      step.prompt ?? "",
      step.placeholder ?? "",
      step.help?.title ?? "",
      ...(step.help?.lines ?? []),
    ]),
    wizard.sender_id_help?.title ?? "",
    wizard.sender_id_help?.placeholder ?? "",
    ...(wizard.sender_id_help?.lines ?? []),
  ];
  for (const value of strings) {
    assert(
      !value.toLowerCase().includes("openclaw"),
      `setup_wizard string on ${channel.id} names the transport: ${JSON.stringify(value)}`,
    );
    assert(
      !ENV_VAR.test(value),
      `setup_wizard string on ${channel.id} tells the customer to set an environment variable, which a browser form has no way to do: ${JSON.stringify(value)}`,
    );
  }
}

// ── The connected summary: a fact that could not be read renders as its own
//    state, never as a confident zero. -------------------------------------

const unread = channelSummaryRows({
  accounts: [],
  dmAllowlistCount: null,
  groupAllowlistCount: null,
  ownerLinked: null,
});
assert(unread.every((row) => row.value === null), "an unread summary claims nothing");

const live = channelSummaryRows({
  accounts: ["default"],
  dmAllowlistCount: 0,
  groupAllowlistCount: 0,
  ownerLinked: false,
});
assert(live[0].value === "Nobody yet", "zero allowed senders says so plainly");
assert(live[1].value === "Off", "zero allowed groups reads as off");
assert(live[2].value === "Not linked", "no owner is a legitimate configuration, stated not nagged");
assert(
  channelSummaryRows({ accounts: [], dmAllowlistCount: 1, groupAllowlistCount: 2, ownerLinked: true })
    .map((row) => row.value)
    .join(" · ") === "1 person · 2 groups · Linked",
  "counts read as counts",
);

assert(channelAccountLine([]) === null, "no reported account means no account line — never a guessed handle");
assert(channelAccountLine(["  ", ""]) === null, "blank account ids are not an account");
assert(channelAccountLine(["default"]) === "default", "their identifier verbatim");

if (failures > 0) {
  console.error(`\n${failures} assertion(s) failed`);
  process.exit(1);
}
console.log("channel-setup-flow.test.ts: all assertions passed");
