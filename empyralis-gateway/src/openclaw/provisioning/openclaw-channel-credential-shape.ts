/**
 * The setup form for a channel whose plugin was NOT installed on the machine
 * that generated the manifest — derived from THIS box's own
 * `openclaw config schema`.
 *
 * WHY THIS EXISTS, AND WHY IT COULD ONLY BE A DEVICE-SIDE READ
 * ------------------------------------------------------------
 * `scripts/generate_openclaw_channel_manifest.py` derives every channel's
 * credential shape from `openclaw config schema` at generation time, and that
 * is right for the twenty-three channels the pinned build declares a
 * `channels.<id>` node for. Four do not have one there — `openclaw-weixin`,
 * `openclaw-zaloclawbot`, `wecom`, `yuanbao` — because a channel PLUGIN
 * contributes its config node only once it is installed, and the generating
 * machine had none of them. The manifest records that honestly as
 * `connect_method: "plugin_absent"` with no fields.
 *
 * That is CORRECT BEFORE INSTALLATION AND WRONG AFTER IT. Provisioning
 * installs the plugin; from that moment the box's own `config schema` DOES
 * carry `channels.<id>` and can say exactly what the fields are. Nothing
 * asked. So all four dead-ended after a successful install: the panel said
 * "Setup fields aren't known for this one yet", forever, on a machine that
 * knew.
 *
 *     manifest   the PRE-INSTALL BELIEF, a property of the pinned version
 *     this box   the OBSERVATION, a property of what is installed here
 *                                                  ── the observation wins
 *
 * THE CLASSIFICATION IS THE GENERATOR'S, NOT A SECOND OPINION
 * -----------------------------------------------------------
 * Every rule below is a port of `_credential_fields` /
 * `_split_primary_and_advanced` / `_is_secret_ref_union` in that generator,
 * and the port is deliberate rather than a shared library: the generator runs
 * in Python, offline, against a pinned install; this runs in the gateway,
 * online, against the customer's install. It is the same cross-language
 * duplicate the local-bridge channel map already is, and it is held to the
 * same standard — `__tests__/openclaw-channel-credential-shape.test.ts`
 * asserts this implementation reproduces the checked-in manifest field-for-
 * field for every channel the pinned build DOES declare, so the two cannot
 * drift while still agreeing about the twenty-three.
 *
 * A FIELD NAME IS NEVER THE SIGNAL. `secret: true` is OpenClaw's own SecretRef
 * union — their declaration that a value is a credential — and "is this an
 * identifier" is answered by cross-channel name FREQUENCY computed from this
 * same document, never by a list of names we think look credential-ish.
 *
 * ONE AXIS OF THE GENERATOR IS DELIBERATELY NOT PORTED: source 7's
 * `_mode_gated_secrets`, which reads each plugin's `dist/*secret-contract*.js`
 * to decide that a secret is only live under some OTHER setting. That is a
 * read of files inside the OpenClaw package tree, and its absence degrades
 * exactly the way the generator's own docstring says it does for the channels
 * with no contract shipped: every secret stays PRIMARY. A field is never lost
 * and never invented by that gap — only the primary/advanced split of the form
 * is coarser than it could be.
 */

import { schemaProperties } from "./openclaw-channel-shapes";

/** Mirrors GENERIC_FIELD_NAME_CHANNEL_LIMIT in the manifest generator. A
 *  leaf-scalar property name carried by MORE than this many of the schema's
 *  channel nodes is shared boilerplate (`name`, `responsePrefix`,
 *  `historyLimit`, `webhookPath`), not a credential companion. Secrets bypass
 *  the test entirely — `botToken` is on four channels and is still a
 *  credential. */
const GENERIC_FIELD_NAME_CHANNEL_LIMIT = 5;

/** Mirrors _PATH_FIELD_SUFFIX. A `<name>File` variant is a second INPUT MODE
 *  for the same credential, never a second credential, and a browser form may
 *  not write a path on the owner's machine. */
const PATH_FIELD_SUFFIX = /(File|Path|Dir|Roots)$/;

/** Mirrors _POLICY_SURFACE_FIELDS. Provisioning generates every one of these
 *  from Empyralis's own database, so a setup form offering them would be
 *  offering to fight the next reconcile. */
const POLICY_SURFACE_FIELDS: ReadonlySet<string> = new Set([
  "dmPolicy",
  "groupPolicy",
  "requireMention",
  "groups",
  "teams",
  "rooms",
  "guilds",
  "channels",
  "dms",
  "dm",
  "allowFrom",
  "groupAllowFrom",
  "groupSenderAllowFrom",
  "autoJoinAllowlist",
  "allowlistOnly",
  "configWrites",
  "pluginHooks",
  "tools",
  "enabled",
  "threadBindings",
  "mentionPatterns",
  "mentionAliases",
  "execApprovals",
]);

export interface DerivedOpenClawCredentialField {
  readonly name: string;
  readonly secret: boolean;
  readonly type: "string" | "number" | "boolean";
  readonly file_alternative: string | null;
  readonly advanced: boolean;
}

export interface DerivedOpenClawCredentialShape {
  /** Never `plugin_absent`: this function is only ever called for a channel
   *  whose node EXISTS on this box, so the third state is not one of its
   *  possible answers. A channel with no node is simply absent from the map
   *  it returns, which keeps the manifest's honest unknown in force. */
  readonly connect_method: "credential" | "pairing";
  readonly fields: readonly DerivedOpenClawCredentialField[];
  readonly file_alternatives: readonly string[];
}

function asRecord(node: unknown): Record<string, unknown> {
  return node && typeof node === "object" ? (node as Record<string, unknown>) : {};
}

/**
 * True when a schema node is OpenClaw's own SecretRef union — THEIR
 * declaration that a value is a credential, recognised structurally:
 *
 *     anyOf: [ {type: "string"},
 *              oneOf: [ {properties: {source: {const: "env"|"file"|"exec"},
 *                                     provider: …, id: …}}, … ] ]
 *
 * A port of `_is_secret_ref_union`. No field name is ever consulted, so a
 * credential upstream adds tomorrow is found by the rule that finds
 * `botToken` today.
 */
export function isSecretRefUnion(node: unknown): boolean {
  const record = asRecord(node);
  const branches = Array.isArray(record.anyOf)
    ? record.anyOf
    : Array.isArray(record.oneOf)
      ? record.oneOf
      : undefined;
  if (!branches) return false;

  const plainString = (branch: unknown): boolean => {
    const b = asRecord(branch);
    return b.type === "string" && !Object.hasOwn(b, "enum");
  };

  const secretRef = (branch: unknown): boolean => {
    const b = asRecord(branch);
    const candidates: unknown[] = [b];
    for (const key of ["oneOf", "anyOf"] as const) {
      if (Array.isArray(b[key])) candidates.push(...(b[key] as unknown[]));
    }
    for (const candidate of candidates) {
      const props = asRecord(candidate).properties;
      if (!props || typeof props !== "object") continue;
      const propsRecord = props as Record<string, unknown>;
      const source = asRecord(propsRecord.source);
      if (typeof source.const !== "string") continue;
      if (Object.hasOwn(propsRecord, "provider") && Object.hasOwn(propsRecord, "id")) return true;
    }
    return false;
  };

  return branches.some(plainString) && branches.some(secretRef);
}

/** `"secret"` / `"string"` / `"number"` / `"boolean"`, or null for a composite
 *  no single form control can express. A port of `_leaf_scalar_type`. */
function leafScalarType(node: unknown): "secret" | "string" | "number" | "boolean" | null {
  if (!node || typeof node !== "object") return null;
  if (isSecretRefUnion(node)) return "secret";
  const type = (node as Record<string, unknown>).type;
  if (type === "string") return "string";
  if (type === "number" || type === "integer") return "number";
  if (type === "boolean") return "boolean";
  return null;
}

/** Leaf-scalar property name -> how many channel nodes carry it. The
 *  CROSS-CHANNEL axis of the identifier rule, computed from the same document
 *  being classified rather than from a list. A port of
 *  `_generic_field_names`. */
function genericFieldNames(channels: Record<string, unknown>): Map<string, number> {
  const counts = new Map<string, number>();
  for (const node of Object.values(channels)) {
    for (const [name, fieldNode] of Object.entries(schemaProperties(node))) {
      if (leafScalarType(fieldNode) === null) continue;
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
  }
  return counts;
}

/**
 * Which of a channel's form fields a customer must supply to CONNECT, as
 * opposed to mode-specific extras, network overrides and cosmetics. A port of
 * `_split_primary_and_advanced`, including its reliance on the schema's own
 * DECLARATION ORDER: a credential anchors a group, and the group is the run of
 * form fields declared around it.
 *
 * Nothing is dropped by this — an advanced field stays in the shape, stays
 * writable, and is only rendered behind a disclosure.
 */
function primaryFieldNames(
  props: Record<string, unknown>,
  candidates: ReadonlySet<string>,
  pathFields: ReadonlySet<string>,
): Set<string> {
  const fileStems = new Set(
    Object.keys(props)
      .filter((name) => name.endsWith("File"))
      .map((name) => name.slice(0, -"File".length).toLowerCase())
      .filter((stem) => stem.length > 0),
  );

  const isCredential = (name: string): boolean => {
    const node = props[name];
    if (isSecretRefUnion(node)) return true;
    if (leafScalarType(node) !== "string") return false;
    // LINE's `channelAccessToken`/`channelSecret` are plain strings whose file
    // variants are `tokenFile`/`secretFile` — the stem is a SUFFIX of the field
    // name, not the whole of it.
    const lowered = name.toLowerCase();
    for (const stem of fileStems) if (lowered.endsWith(stem)) return true;
    return false;
  };

  const closesRun = (name: string): boolean => {
    if (POLICY_SURFACE_FIELDS.has(name)) return true;
    const node = props[name];
    if (!node || typeof node !== "object") return false;
    const record = node as Record<string, unknown>;
    return (
      Object.hasOwn(record, "default") ||
      Object.hasOwn(record, "enum") ||
      Object.hasOwn(record, "const")
    );
  };

  const runs: string[][] = [];
  let current: string[] = [];
  for (const name of Object.keys(props)) {
    if (candidates.has(name)) {
      current.push(name);
      continue;
    }
    if (pathFields.has(name)) continue;
    if (closesRun(name)) {
      if (current.length > 0) runs.push(current);
      current = [];
    }
  }
  if (current.length > 0) runs.push(current);

  const primary = new Set<string>();
  for (const run of runs) {
    if (!run.some(isCredential)) continue;
    for (const name of run) primary.add(name);
  }
  return primary;
}

/** The owner-supplied connection fields for ONE channel node. A port of
 *  `_credential_fields`. */
function credentialFieldsForNode(
  node: unknown,
  nameFrequency: Map<string, number>,
): { fields: DerivedOpenClawCredentialField[]; fileAlternatives: string[] } {
  const props = schemaProperties(node);
  const pathFields = new Set(Object.keys(props).filter((name) => PATH_FIELD_SUFFIX.test(name)));

  // Does this channel take a pasted credential AT ALL? Two independent schema
  // signals, and a channel needs neither typed out: a SecretRef field (their
  // own credential type), or a `<name>File` field — OpenClaw ships a
  // file-backed variant ONLY for credentials, which is why LINE (whose
  // `channelAccessToken`/`channelSecret` are plain strings) is still caught.
  const takesPastedCredential =
    Object.values(props).some(isSecretRefUnion) ||
    Object.keys(props).some((name) => name.endsWith("File"));
  if (!takesPastedCredential) return { fields: [], fileAlternatives: [] };

  const draft: Array<Omit<DerivedOpenClawCredentialField, "advanced">> = [];
  const consumedPaths: string[] = [];
  for (const [name, fieldNode] of Object.entries(props)) {
    if (POLICY_SURFACE_FIELDS.has(name) || pathFields.has(name)) continue;
    const fieldType = leafScalarType(fieldNode);
    if (fieldType === null) continue;
    const secret = fieldType === "secret";
    if (!secret) {
      // IDENTIFIER — a plain string the owner types, kept only when the name is
      // channel-specific. Anything with a default/enum/const is a behaviour
      // knob OpenClaw can run without, and a non-string scalar is never a
      // credential.
      if (fieldType !== "string") continue;
      const record = asRecord(fieldNode);
      if (
        Object.hasOwn(record, "default") ||
        Object.hasOwn(record, "enum") ||
        Object.hasOwn(record, "const")
      ) {
        continue;
      }
      if ((nameFrequency.get(name) ?? 0) > GENERIC_FIELD_NAME_CHANNEL_LIMIT) continue;
    }
    const fileAlternative = pathFields.has(`${name}File`) ? `${name}File` : null;
    if (fileAlternative) consumedPaths.push(fileAlternative);
    draft.push({
      name,
      secret,
      type: secret ? "string" : (fieldType as "string" | "number" | "boolean"),
      file_alternative: fileAlternative,
    });
  }

  const primary = primaryFieldNames(props, new Set(draft.map((field) => field.name)), pathFields);
  const fields: DerivedOpenClawCredentialField[] = draft.map((field) => ({
    ...field,
    advanced: !primary.has(field.name),
  }));
  // The generator's own ordering: primary before advanced, secret before
  // identifier, then by name.
  fields.sort((a, b) => {
    if (a.advanced !== b.advanced) return a.advanced ? 1 : -1;
    if (a.secret !== b.secret) return a.secret ? -1 : 1;
    return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
  });

  return { fields, fileAlternatives: [...new Set(consumedPaths)].sort() };
}

/**
 * The credential shape of each requested channel, as THIS box's installed
 * OpenClaw declares it.
 *
 * A channel whose node the schema does not carry is ABSENT from the result
 * rather than present with an empty shape — "the plugin is installed and
 * contributes nothing yet" must keep the manifest's honest unknown, never be
 * smoothed into "this channel pairs".
 */
export function deriveOpenClawCredentialShapes(
  schema: unknown,
  channelIds: readonly string[],
): Record<string, DerivedOpenClawCredentialShape> {
  const channels = schemaProperties(schemaProperties(schema).channels);
  if (Object.keys(channels).length === 0) return {};
  // Computed over EVERY channel the schema declares, not just the requested
  // ones: the name-frequency test is a cross-channel measurement, and taking
  // it over four nodes would call every one of their fields channel-specific.
  const nameFrequency = genericFieldNames(channels);

  const shapes: Record<string, DerivedOpenClawCredentialShape> = {};
  for (const channelId of channelIds) {
    const node = channels[channelId];
    if (!node) continue;
    const { fields, fileAlternatives } = credentialFieldsForNode(node, nameFrequency);
    shapes[channelId] = {
      connect_method: fields.length > 0 ? "credential" : "pairing",
      fields,
      file_alternatives: fileAlternatives,
    };
  }
  return shapes;
}
