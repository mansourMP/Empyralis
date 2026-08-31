import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ACCOUNT_SHELL_STORAGE_KEY,
  clearAccountShellSnapshot,
  readAccountShellSnapshot,
  writeAccountShellSnapshot,
} from './account-shell-storage';
import type { AccountShellSnapshot } from './account-shell-store';

/** A minimal localStorage stand-in. The real one is a browser global this
 *  module reads defensively; driving the REAL module against a fake STORE
 *  (rather than testing a reimplementation) is what makes these assertions
 *  worth anything. */
function installStorage(seed?: string): Map<string, string> {
  const map = new Map<string, string>();
  if (seed !== undefined) map.set(ACCOUNT_SHELL_STORAGE_KEY, seed);
  (globalThis as Record<string, unknown>).window = {
    localStorage: {
      getItem: (key: string) => (map.has(key) ? map.get(key)! : null),
      setItem: (key: string, value: string) => { map.set(key, value); },
      removeItem: (key: string) => { map.delete(key); },
    },
  };
  return map;
}

/** The actual production defect: in Safari with "Block All Cookies", certain
 *  ITP states, and some private-browsing configurations, `window.localStorage`
 *  exists as an OBJECT (so `typeof window.localStorage !== 'undefined'`
 *  passes) but every real access throws `SecurityError: The operation is
 *  insecure.` A plain object whose methods throw reproduces exactly that
 *  shape -- present, but every touch throws -- which `typeof` alone cannot
 *  distinguish from working storage. */
function installThrowingStorage(): void {
  (globalThis as Record<string, unknown>).window = {
    localStorage: {
      getItem: () => { throw new DOMException('The operation is insecure.', 'SecurityError'); },
      setItem: () => { throw new DOMException('The operation is insecure.', 'SecurityError'); },
      removeItem: () => { throw new DOMException('The operation is insecure.', 'SecurityError'); },
    },
  };
}

function clearStorage(): void {
  delete (globalThis as Record<string, unknown>).window;
}

const sampleSnapshot: AccountShellSnapshot = {
  accountId: 'acc_1',
  selectedWorkspaceId: 'ws_1',
  lastVisitedWorkspaceRouteById: { ws_1: '/w/ws_1/agents' },
  workspaceRouteStateById: {},
  globalTheme: 'dark',
  globalChromePreferences: { tenantSwitcherCollapsed: true },
};

test('server-side render never touches storage and reports nothing', () => {
  clearStorage();
  assert.equal(readAccountShellSnapshot(), null);
  assert.doesNotThrow(() => writeAccountShellSnapshot(sampleSnapshot));
  assert.doesNotThrow(() => clearAccountShellSnapshot());
});

test('a written snapshot round-trips', () => {
  installStorage();
  writeAccountShellSnapshot(sampleSnapshot);
  assert.deepEqual(readAccountShellSnapshot(), sampleSnapshot);
  clearStorage();
});

test('clearing removes the snapshot', () => {
  installStorage();
  writeAccountShellSnapshot(sampleSnapshot);
  clearAccountShellSnapshot();
  assert.equal(readAccountShellSnapshot(), null);
  clearStorage();
});

test('corrupted JSON yields null, never a throw, and the bad entry is cleaned up', () => {
  const store = installStorage('not json at all');
  assert.equal(readAccountShellSnapshot(), null);
  assert.equal(store.has(ACCOUNT_SHELL_STORAGE_KEY), false, 'the unreadable entry is removed');
  clearStorage();
});

test('a non-object payload yields null, never a throw', () => {
  installStorage(JSON.stringify([1, 2, 3]));
  assert.equal(readAccountShellSnapshot(), null);
  clearStorage();
});

// ── The regression this file exists to guard ───────────────────────────────
//
// window.localStorage EXISTS as an object (typeof passes) but every access
// throws SecurityError -- Safari's "Block All Cookies", certain ITP states,
// and private browsing all produce exactly this shape. Before the fix,
// readAccountShellSnapshot's getItem() call sat outside its try block, and
// writeAccountShellSnapshot / clearAccountShellSnapshot had no try at all --
// so this reproduces a white-screen crash on app boot without the fix.

test('a storage object that throws on every access never crashes the reader', () => {
  installThrowingStorage();
  assert.doesNotThrow(() => readAccountShellSnapshot());
  assert.equal(readAccountShellSnapshot(), null);
  clearStorage();
});

test('a storage object that throws on every access never crashes the writer', () => {
  installThrowingStorage();
  assert.doesNotThrow(() => writeAccountShellSnapshot(sampleSnapshot));
  clearStorage();
});

test('a storage object that throws on every access never crashes clear', () => {
  installThrowingStorage();
  assert.doesNotThrow(() => clearAccountShellSnapshot());
  clearStorage();
});

test('a throw inside the parse-failure cleanup path is itself caught', () => {
  // getItem returns something unparseable (forcing the catch block to run),
  // and removeItem -- called FROM that catch block -- also throws. The old
  // code called window.localStorage.removeItem(...) unguarded inside its
  // own catch, so this specific shape would have escaped even a naive
  // top-level try/catch fix.
  (globalThis as Record<string, unknown>).window = {
    localStorage: {
      getItem: () => 'not json at all',
      setItem: () => { throw new DOMException('The operation is insecure.', 'SecurityError'); },
      removeItem: () => { throw new DOMException('The operation is insecure.', 'SecurityError'); },
    },
  };
  assert.doesNotThrow(() => readAccountShellSnapshot());
  assert.equal(readAccountShellSnapshot(), null);
  clearStorage();
});
