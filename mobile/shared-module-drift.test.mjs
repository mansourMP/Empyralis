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
