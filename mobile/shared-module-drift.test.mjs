/**
 * THE ONE INVARIANT THIS APP EXISTS TO HOLD: the product's rules are
 * IMPORTED from frontend/lib, never re-implemented here.
 *
 * The failure this guards against does not look like a bug. It looks like a
 * helpful local `sortInboxItems`, or a `STUCK = ['blocked','awaiting_input']`
 * typed out because the import was awkward that afternoon. From then on
 * there are two rankings, they agree today, and nobody finds out when they
 * stop. That is exactly what the Swift app had, plus a test suite whose only
 * job was noticing the drift after the fact.
 *
 * A behavioural test cannot catch this — a local copy behaves identically
 * until the web module changes. So this is a source scan, and per this
 * codebase's own rule every source scan carries a CANARY that fails loudly
 * when the scan stops reaching real files.
 *
 * Run: node --test  (from mobile/)
 */
import { strict as assert } from 'node:assert';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, 'src');
const SHARED = join(HERE, '..', 'frontend', 'lib', 'workspace', 'fleet');

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const files = walk(SRC).map((path) => ({ path, text: readFileSync(path, 'utf8') }));

test('canary: the scan reaches real app source', () => {
  // Without this, every assertion below passes vacuously the day someone
  // moves src/ or changes an extension.
  assert.ok(files.length >= 8, `expected to scan real files, found ${files.length}`);
  assert.ok(
    files.some((f) => f.text.includes('planInboxNeedsYou')),
    'the scan found no file mentioning the ranking at all — it is looking in the wrong place',
  );
});

test('canary: the shared module is where we think it is', () => {
  const shared = readFileSync(join(SHARED, 'inbox-needs-you.ts'), 'utf8');
  assert.ok(
    shared.includes('export function planInboxNeedsYou'),
    'frontend/lib/workspace/fleet/inbox-needs-you.ts no longer exports planInboxNeedsYou',
  );
});

test('the ranking is IMPORTED from the shared tree', () => {
  const importers = files.filter((f) =>
    /from ['"]@shared\/workspace\/fleet\/inbox-needs-you['"]/.test(f.text),
  );
  assert.equal(
    importers.length,
    1,
    `expected exactly one importer of the shared inbox module, found ${importers.length}`,
  );
});

test('no local re-implementation of the ranking', () => {
  // Names and literals that belong to inbox-needs-you.ts alone. Any of them
  // appearing in app source means a second copy has started.
  const OWNED_BY_SHARED = [
    'INBOX_STUCK_STATUSES',
    'isMyStuckTask',
    'isBlockedRunEvent',
    'notificationTitle',
    // The three status literals the module decides on. A local list of them
    // is the most likely shape a re-implementation takes.
    "'awaiting_input'",
    '"awaiting_input"',
    "'blocked_action'",
    '"blocked_action"',
  ];
  const offenders = [];
  for (const file of files) {
    for (const token of OWNED_BY_SHARED) {
      if (file.text.includes(token)) offenders.push(`${file.path}: ${token}`);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `these belong to the shared module and must be imported, not restated:\n${offenders.join('\n')}`,
  );
});

test('the app never re-orders what the shared module ranked', () => {
  // The Inbox is ONE FLAT LIST now — no section headers — so the group
  // boundaries are invisible and the ORDER is the only thing carrying them.
  // `planInboxNeedsYou` ranks the three groups on three deliberately
  // incompatible clocks (stuck tasks OLDEST-first, the rest newest-first),
  // so a local sort over the flattened union is both easy to reach for and
  // exactly wrong: it re-buries the seventeen-day task this surface was
  // built to surface. Flattening must stay a concatenation.
  //
  // A behavioural test cannot catch this — a re-sorted list renders
  // perfectly and looks plausible. Hence a source scan.
  const offenders = [];
  for (const file of files) {
    // Comments discuss this rule by name; stripping them first is what
    // keeps the guard from tripping over its own documentation.
    const code = file.text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    if (/\.(sort|reverse)\s*\(/.test(code)) offenders.push(file.path);
  }
  assert.deepEqual(
    offenders,
    [],
    `ordering belongs to the shared module; these re-order it locally:\n${offenders.join('\n')}`,
  );
});

test('the flatten preserves group order', () => {
  const screen = files.find((f) => f.path.endsWith('InboxScreen.tsx'));
  assert.ok(screen, 'canary: InboxScreen.tsx was not scanned');
  // Tasks first, then notifications, then runs — the order the grouped
  // rendering drew them in, so removing the headings moved no row.
  assert.match(
    screen.text.replace(/\s+/g, ' '),
    /\[ *\.\.\.data\.groups\.tasks, *\.\.\.data\.groups\.notifications, *\.\.\.data\.groups\.runs *\]/,
    'the flatten is no longer a plain concatenation in group order',
  );
});

test('this engine parses every date shape the backend emits', () => {
  // THIS ASSERTION USED TO BE A PANEL ON THE INBOX SCREEN, rendering
  // "SHARED-MODULE DATE PARSE (dev only) ✓ ✓ ✓" to whoever opened the app.
  // It is a claim about the module's contract, so it belongs here.
  //
  // The shared module ranks on Date.parse and this backend emits two
  // different shapes from one object. Swift's ISO8601DateFormatter accepts
  // neither (it wants 0 or 3 fractional digits, and rejects the space
  // outright), which is how the ported ranking silently degraded to input
  // order with nothing reporting it.
  //
  // HONEST LIMIT: node is V8, the app is Hermes. This proves the SHAPES are
  // well-formed, never that the shipping engine agrees — that half is
  // checked on the device at runtime and reported to the log (see the
  // bottom of src/api/inbox.ts).
  const SHAPES = {
    'notifications (space separator, 6 fractional digits)': '2026-08-29 06:49:50.180524+00:00',
    'activity ledger (ISO-T, 6 fractional digits)': '2026-08-29T06:51:00.518261Z',
    'tasks (space separator, offset)': '2026-08-12 09:15:00.123456+00:00',
  };
  for (const [label, sample] of Object.entries(SHAPES)) {
    assert.ok(Number.isFinite(Date.parse(sample)), `${label} did not parse: ${sample}`);
  }

  // The app must still be checking the same strings. Two hand-kept copies
  // of a wire format is the drift this file exists to stop.
  //
  // COMMENTS ARE STRIPPED FIRST, and that is not tidiness — it is the only
  // reason this half of the assertion means anything. src/api/inbox.ts
  // quotes all three shapes in its own header prose, so a whole-text
  // `includes` is satisfied by the DOCUMENTATION even after the live
  // constant has been changed underneath it. Caught by mutating the
  // constant and watching this test stay green.
  const inbox = files.find((f) => f.path.endsWith('api/inbox.ts'));
  assert.ok(inbox, 'canary: src/api/inbox.ts was not scanned');
  const inboxCode = inbox.text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  assert.ok(
    inboxCode.includes('WIRE_DATE_SHAPES'),
    'canary: comment-stripping ate the code it was supposed to leave behind',
  );
  for (const sample of Object.values(SHAPES)) {
    assert.ok(
      inboxCode.includes(sample),
      `src/api/inbox.ts no longer checks the shape this test pins: ${sample}`,
    );
  }
});

test('no engineer scaffolding is rendered on a product screen', () => {
  // A dev-only panel reporting a passing self-test shipped on the first
  // screen a person sees, and it is the single thing the founder named
  // first. The information moved into the test above; this stops it, or
  // anything like it, from moving back onto a screen.
  const offenders = [];
  for (const file of files) {
    if (!/\.tsx$/.test(file.path)) continue;
    if (/__DEV__/.test(file.text)) offenders.push(file.path);
  }
  assert.deepEqual(
    offenders,
    [],
    `dev-only rendering belongs in a test, not on a screen:\n${offenders.join('\n')}`,
  );
});

test('the shared tree is never reached by a relative path', () => {
  // `../../frontend/lib/...` would work in Metro and silently bypass the
  // alias, so the next person copying the pattern would not learn it exists.
  const offenders = files.filter((f) => /from ['"][./]+frontend\/lib/.test(f.text));
  assert.deepEqual(
    offenders.map((f) => f.path),
    [],
    'import shared modules through the @shared alias, never a relative path into frontend/',
  );
});

test('no file under mobile/src is a copy of a shared module', () => {
  // A whole-file copy would not mention any owned token if it were renamed,
  // so compare bodies directly.
  const sharedBodies = readdirSync(SHARED)
    .filter((n) => n.endsWith('.ts') && !n.endsWith('.test.ts'))
    .map((n) => ({
      name: n,
      body: readFileSync(join(SHARED, n), 'utf8').replace(/\s+/g, ' ').slice(0, 400),
    }));
  const offenders = [];
  for (const file of files) {
    const normalized = file.text.replace(/\s+/g, ' ');
    for (const shared of sharedBodies) {
      if (shared.body.length > 200 && normalized.includes(shared.body)) {
        offenders.push(`${file.path} contains the opening of ${shared.name}`);
      }
    }
  }
  assert.deepEqual(offenders, [], offenders.join('\n'));
});
