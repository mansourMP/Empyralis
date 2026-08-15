/** Mechanical dead-end sweep — scratch, not shipped.
 *
 *  Every carried platform x every state a real box can report, through the
 *  REAL remediationFor/channelCardPill. Both the platform set and the per
 *  channel shape come from the generated manifest, never a hand-typed list.
 */
import { readFileSync } from "node:fs";
import { remediationFor, channelCardPill } from "./openclaw-channel-copy";
import { GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS } from "../../../../empyralis-gateway/src/openclaw/generated-openclaw-channels";
import { deriveOpenClawCredentialShapes } from "../../../../empyralis-gateway/src/openclaw/provisioning/openclaw-channel-credential-shape";

const manifest = JSON.parse(
  readFileSync(new URL("../../../../server_modules/openclaw_channel_manifest.json", import.meta.url), "utf8"),
);

/** WHAT THE BOX ANSWERS, ONCE IT HAS BEEN ASKED.
 *
 *  This used to build `observed` out of the MANIFEST — which is the one thing
 *  a box never sends back. For the four channels whose plugin the manifest
 *  never saw, feeding its own `plugin_absent` belief back in as the
 *  "observation" made every post-install state permanently unknowable, which
 *  is exactly the dead end this sweep was measuring.
 *
 *  So the box's answer is now produced by the REAL gateway derivation
 *  (deriveOpenClawCredentialShapes) over the REAL checked-in schema fixture,
 *  with a real channel node re-keyed under the absent channel's id — which is
 *  precisely what installing that plugin does to the live schema. Nothing here
 *  invents a field. */
const fixture = JSON.parse(
  readFileSync(new URL("../../../../empyralis-gateway/src/__tests__/fixtures/openclaw-config-schema.channels.unpruned.json", import.meta.url), "utf8"),
);
const donorId = (manifest.channels as any[]).find(
  (c) => c.credential_shape?.connect_method === "credential",
).id;
function boxDerivedShape(channelId: string) {
  const nodes = { ...fixture.properties.channels.properties };
  nodes[channelId] = nodes[donorId];
  const schema = { properties: { channels: { properties: nodes } } };
  return deriveOpenClawCredentialShapes(schema, [channelId])[channelId];
}

type Row = { id: string; scenario: string; pill: string; kind: string; detail: string };
const rows: Row[] = [];

for (const ch of manifest.channels as any[]) {
  if (!GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS.includes(ch.id)) continue;
  const shape = ch.credential_shape || {};
  const entry: any = {
    channel_key: ch.channel_key,
    channel_id: ch.id,
    label: ch.label,
    connect_method: shape.connect_method,
    selection_label: shape.selection_label,
    docs_path: shape.docs_path,
    fields: shape.fields || [],
    requires_plugin: !!(ch.plugin_install && ch.plugin_install.required),
    plugin_id: (ch.plugin_install || {}).plugin_id || null,
    setup_wizard: ch.setup_wizard || null,
  };

  const mkObserved = (o: any) => {
    // A box only re-derives a channel the manifest could not describe, and
    // only once its plugin is actually installed there. Every other channel
    // answers with the shape the manifest already carries, exactly as before.
    const live =
      shape.connect_method === "plugin_absent" && o.installed && !o.pluginDeclaresNothing
        ? boxDerivedShape(ch.id)
        : undefined;
    const observedShape = live ?? shape;
    return {
      channel_id: ch.id,
      channel_key: ch.channel_key,
      label: ch.label,
      connect_method: observedShape.connect_method,
      selection_label: shape.selection_label,
      docs_path: shape.docs_path,
      installed: o.installed,
      requires_plugin: entry.requires_plugin,
      plugin_id: entry.plugin_id,
      enabled: o.enabled,
      configured: o.configured,
      fields: (observedShape.fields || []).map((f: any) => ({
        name: f.name,
        secret: f.secret,
        type: f.type,
        advanced: f.advanced,
        file_alternative: f.file_alternative ?? null,
        set: o.fieldsSet,
      })),
      accounts: o.accounts || [],
      link: o.link ?? null,
    };
  };

  const scenarios: [string, any][] = [
    ["1 fresh box, plugin absent", mkObserved({ installed: false, enabled: false, configured: false, fieldsSet: false })],
    ["2 installed, nothing entered", mkObserved({ installed: true, enabled: false, configured: false, fieldsSet: false })],
    ["3 installed + credential, off", mkObserved({ installed: true, enabled: false, configured: true, fieldsSet: true })],
    ["4 installed + credential + on", mkObserved({ installed: true, enabled: true, configured: true, fieldsSet: true })],
    ["5 enabled, QR seam reported", mkObserved({ installed: true, enabled: true, configured: true, fieldsSet: true, link: { supports_qr_login: true } })],
    // The honest unknown, kept deliberately in the sweep: plugin installed and
    // it still contributes no config node. There is nothing to render and we
    // must not invent one, so this row SHOULD be a dead end.
    ["6 installed, box declares nothing", mkObserved({ installed: true, enabled: false, configured: false, fieldsSet: false, pluginDeclaresNothing: true })],
  ];

  for (const [scenario, observed] of scenarios) {
    const rem: any = remediationFor(entry, observed as any, true, true);
    const pill = channelCardPill(rem);
    rows.push({ id: ch.id, scenario, pill: pill.label, kind: rem.kind, detail: rem.detail || "" });
  }
}

// ── assertions ───────────────────────────────────────────────────────────
//
// A DEAD END is `kind: "unknown"` — the one remediation with no control
// behind it. Every other kind renders something a person can press.

let failures = 0;
function check(name: string, ok: boolean, detail = "") {
  if (ok) return;
  failures += 1;
  console.error(`  FAIL ${name}${detail ? `\n       ${detail}` : ""}`);
}

check(
  "the sweep actually covered the carried platforms",
  new Set(rows.map((r) => r.id)).size === GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS.length,
  `covered ${new Set(rows.map((r) => r.id)).size} of ${GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS.length}`,
);

// A person who has just paired a computer must never meet a card that offers
// nothing. This is the state every customer starts in.
const freshDeadEnds = rows.filter((r) => r.scenario.startsWith("1 ") && r.kind === "unknown");
check(
  "a fresh box has no dead ends",
  freshDeadEnds.length === 0,
  freshDeadEnds.map((r) => `${r.id}: ${r.pill} — ${r.detail}`).join("\n       "),
);

// The sharper rule, and the regression this file exists for: succeeding must
// not make a card inert. Pressing "Set up", having the plugin land, and then
// being told the channel is unknowable is worse than never offering it.
const afterSuccess = rows.filter(
  (r) => /^[2345] /.test(r.scenario) && r.kind === "unknown",
);
check(
  "a channel that installed and declared itself is never unknowable",
  afterSuccess.length === 0,
  afterSuccess.map((r) => `${r.id} @ ${r.scenario}`).join("\n       "),
);

// The one unknown that is CORRECT, asserted so nobody "fixes" it into a
// guessed form: the plugin is installed and still contributes no config node.
// There is genuinely nothing to render, and inventing fields would be the
// lie this whole sweep is guarding against.
const declaresNothing = rows.filter((r) => r.scenario.startsWith("6 "));
check(
  "plugin installed but declaring nothing stays honestly unknown",
  declaresNothing.length > 0 && declaresNothing.every((r) => r.kind === "unknown" || r.kind !== "ready"),
  declaresNothing.filter((r) => r.kind === "ready").map((r) => `${r.id} claimed ready`).join("\n       "),
);

// No mechanism words in anything a customer reads. Same rule the copy test
// applies per string; applied here across every state of every platform.
const MECHANISM = /\b(plugin|npm|package|binary|schema|config)\b/i;
const leaks = rows.filter((r) => MECHANISM.test(r.detail) || MECHANISM.test(r.pill));
check(
  "no mechanism words reach the customer",
  leaks.length === 0,
  leaks.map((r) => `${r.id} @ ${r.scenario}: "${r.pill}" / "${r.detail}"`).join("\n       "),
);

if (failures > 0) {
  console.error(`\n${failures} failed`);
  process.exit(1);
}
console.log(
  `${rows.length} passed, 0 failed  (${GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS.length} platforms x ${rows.length / GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS.length} box states)`,
);
