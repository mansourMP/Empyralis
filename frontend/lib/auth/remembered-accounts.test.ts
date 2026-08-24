import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  MAX_REMEMBERED_ACCOUNTS,
  accountInitials,
  forgetAccount,
  forgetAllAccounts,
  listRememberedAccounts,
  planLoginEntry,
  rememberAccount,
  type RememberedAccount,
} from './remembered-accounts';

/** A minimal localStorage stand-in. The real one is a browser global this
 *  module reads defensively; driving the REAL module against a fake STORE
 *  (rather than testing a reimplementation) is what makes these assertions
 *  worth anything. */
function installStorage(seed?: string): Map<string, string> {
  const map = new Map<string, string>();
  if (seed !== undefined) map.set('empyralis.remembered-accounts.v1', seed);
  (globalThis as Record<string, unknown>).window = {
    localStorage: {
      getItem: (key: string) => (map.has(key) ? map.get(key)! : null),
      setItem: (key: string, value: string) => { map.set(key, value); },
      removeItem: (key: string) => { map.delete(key); },
    },
  };
  return map;
}

function clearStorage(): void {
  delete (globalThis as Record<string, unknown>).window;
}

test('server-side render never touches storage and reports nothing', () => {
  clearStorage();
  assert.deepEqual(listRememberedAccounts(), []);
  assert.equal(planLoginEntry([]), 'form');
});

test('a remembered account comes back, and decides the chooser', () => {
  installStorage();
  rememberAccount({ email: 'Mansur@Example.com', name: 'Mansur', method: 'email', now: 100 });
  const accounts = listRememberedAccounts();
  assert.equal(accounts.length, 1);
  assert.equal(accounts[0].email, 'mansur@example.com', 'email is normalised to lowercase');
  assert.equal(accounts[0].method, 'email');
  assert.equal(planLoginEntry(accounts), 'chooser');
  clearStorage();
});

test('most recently used sorts first', () => {
  installStorage();
  rememberAccount({ email: 'a@example.com', method: 'email', now: 100 });
  rememberAccount({ email: 'b@example.com', method: 'google', now: 200 });
  assert.deepEqual(listRememberedAccounts().map((a) => a.email), ['b@example.com', 'a@example.com']);
  clearStorage();
});

test('signing in again updates in place rather than duplicating', () => {
  installStorage();
  rememberAccount({ email: 'a@example.com', name: 'Old', method: 'email', now: 100 });
  rememberAccount({ email: 'a@example.com', name: 'New', method: 'google', now: 300 });
  const accounts = listRememberedAccounts();
  assert.equal(accounts.length, 1, 'one identity is one row');
  assert.equal(accounts[0].name, 'New');
  assert.equal(accounts[0].method, 'google', 'the method must follow the LAST successful sign-in');
  clearStorage();
});

test('the list is capped, and the oldest is genuinely dropped from storage', () => {
  const store = installStorage();
  for (let i = 0; i < MAX_REMEMBERED_ACCOUNTS + 3; i += 1) {
    rememberAccount({ email: `user${i}@example.com`, method: 'email', now: i });
  }
  assert.equal(listRememberedAccounts().length, MAX_REMEMBERED_ACCOUNTS);
  const persisted = JSON.parse(store.get('empyralis.remembered-accounts.v1')!);
  assert.equal(persisted.length, MAX_REMEMBERED_ACCOUNTS, 'capped on write, not merely on read');
  clearStorage();
});

test('forgetting removes exactly one account', () => {
  installStorage();
  rememberAccount({ email: 'a@example.com', method: 'email', now: 100 });
  rememberAccount({ email: 'b@example.com', method: 'email', now: 200 });
  forgetAccount('A@Example.com');
  assert.deepEqual(listRememberedAccounts().map((a) => a.email), ['b@example.com']);
  clearStorage();
});

test('forgetting everything leaves the form, not a broken chooser', () => {
  installStorage();
  rememberAccount({ email: 'a@example.com', method: 'email', now: 100 });
  forgetAllAccounts();
  assert.deepEqual(listRememberedAccounts(), []);
  assert.equal(planLoginEntry(listRememberedAccounts()), 'form');
  clearStorage();
});

// ── The security-relevant cases ────────────────────────────────────────────

test('a javascript: avatar URL is refused', () => {
  installStorage();
  rememberAccount({
    email: 'a@example.com',
    avatarUrl: 'javascript:alert(1)',
    method: 'email',
    now: 100,
  });
  assert.equal(listRememberedAccounts()[0].avatarUrl, null);
  clearStorage();
});

test('a hostile avatar URL injected straight into storage is still refused on READ', () => {
  // The write path is not the only way in — anything can edit localStorage,
  // so the read path has to sanitise too.
  installStorage(JSON.stringify([
    { email: 'a@example.com', avatarUrl: 'javascript:alert(1)', method: 'email', lastUsedAt: 1 },
  ]));
  assert.equal(listRememberedAccounts()[0].avatarUrl, null);
  clearStorage();
});

test('an https avatar URL survives', () => {
  installStorage();
  rememberAccount({
    email: 'a@example.com',
    avatarUrl: 'https://cdn.example.com/a.png',
    method: 'email',
    now: 100,
  });
  assert.equal(listRememberedAccounts()[0].avatarUrl, 'https://cdn.example.com/a.png');
  clearStorage();
});

test('corrupted storage yields an empty list, never a throw', () => {
  installStorage('not json at all');
  assert.deepEqual(listRememberedAccounts(), []);
  clearStorage();

  installStorage(JSON.stringify({ not: 'an array' }));
  assert.deepEqual(listRememberedAccounts(), []);
  clearStorage();

  installStorage(JSON.stringify([{ garbage: true }, null, 5, 'x']));
  assert.deepEqual(listRememberedAccounts(), [], 'unparseable entries are dropped individually');
  clearStorage();
});

test('an entry with no usable email is never stored', () => {
  installStorage();
  rememberAccount({ email: '   ', method: 'email', now: 1 });
  rememberAccount({ email: 'not-an-email', method: 'email', now: 2 });
  assert.deepEqual(listRememberedAccounts(), []);
  clearStorage();
});

test('a storage that throws does not break sign-in', () => {
  (globalThis as Record<string, unknown>).window = {
    localStorage: {
      getItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('denied'); },
      removeItem: () => { throw new Error('denied'); },
    },
  };
  assert.deepEqual(listRememberedAccounts(), []);
  assert.doesNotThrow(() => rememberAccount({ email: 'a@example.com', method: 'email' }));
  clearStorage();
});

// ── Initials ───────────────────────────────────────────────────────────────

test('initials come from the name, or the email local-part', () => {
  const make = (over: Partial<RememberedAccount>): RememberedAccount => ({
    email: 'someone@example.com',
    name: null,
    avatarUrl: null,
    method: 'email',
    lastUsedAt: 0,
    ...over,
  });
  assert.equal(accountInitials(make({ name: 'Mansur Aliyev' })), 'MA');
  assert.equal(accountInitials(make({ name: 'Mansur' })), 'M');
  // Never the domain, and never "@" — the whole reason this is derived
  // from the local-part rather than the raw address.
  assert.equal(accountInitials(make({ email: 'mansur.aliyev@example.com' })), 'MA');
  assert.equal(accountInitials(make({ email: 'mansur@example.com' })), 'M');
});
