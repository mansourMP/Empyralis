/**
 * Remembered accounts — the thing that makes the front door recognise you.
 *
 * WHAT THIS STORES, AND WHAT IT MUST NEVER STORE
 * ==============================================
 * Stored: a display identity (email, name, avatar URL) plus which METHOD
 * that person last used to get in, and when.
 *
 * NEVER stored: a password, a token, a refresh token, a session id, or a
 * workspace id. This is a CONVENIENCE HINT for the login form, not a
 * credential and not a session. The real session lives in httpOnly cookies
 * the browser will not hand to JavaScript, and that separation is the whole
 * reason it is safe to keep this in localStorage at all. If a future change
 * needs a secret here, the design is wrong — the answer is a server-side
 * session, not a bigger localStorage record.
 *
 * WHY localStorage AND NOT A COOKIE: a cookie is sent to the server on
 * every single request, which would put a list of the device's past account
 * emails into every request log for no benefit — the server has no use for
 * this data. It exists to render one screen.
 *
 * SHARED-DEVICE POSTURE: on a shared computer this list is visible to the
 * next person. That is a real, deliberate trade — it is exactly what Gmail
 * does, and it is why `forgetAccount` exists and why the UI must always
 * offer a per-account remove. Signing OUT deliberately does NOT clear the
 * list: "I left" and "this was never my computer" are different intents,
 * and wiping the list on sign-out would delete the whole feature for the
 * ordinary case (one person, their own machine, signing out for the night).
 */

export type RememberedAuthMethod = 'email' | 'google';

export interface RememberedAccount {
  email: string;
  name: string | null;
  avatarUrl: string | null;
  /** How this person last got in. Decides what the next screen ASKS for —
   *  showing a password field to someone who only ever used Google is a
   *  control they can never satisfy. */
  method: RememberedAuthMethod;
  /** Epoch ms. Ordering only — never displayed as "last seen", which would
   *  be a claim about activity this data cannot actually support (it
   *  records sign-INS, not use). */
  lastUsedAt: number;
}

const STORAGE_KEY = 'empyralis.remembered-accounts.v1';

/** Four is a chooser; twenty is a search problem wearing a chooser's
 *  clothes. The cap is on the STORE, not just the render, so an old entry
 *  is actually forgotten rather than retained invisibly. */
export const MAX_REMEMBERED_ACCOUNTS = 4;

function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function normalizeEmail(value: unknown): string {
  return String(value ?? '').trim().toLowerCase();
}

/** Avatar URLs come back from the server and are rendered into an <img>.
 *  Only http(s) is allowed through: a `javascript:` or `data:` URL in that
 *  position is a script vector on our own origin, and this value has been
 *  round-tripped through localStorage where anything could have edited it. */
function safeAvatarUrl(value: unknown): string | null {
  const raw = String(value ?? '').trim();
  if (!raw) return null;
  try {
    const parsed = new URL(raw, 'https://empyralis.ai');
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

function coerceAccount(value: unknown): RememberedAccount | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  const email = normalizeEmail(record.email);
  if (!email || !email.includes('@')) return null;
  const method: RememberedAuthMethod = record.method === 'google' ? 'google' : 'email';
  const rawName = String(record.name ?? '').trim();
  const lastUsedAt = Number(record.lastUsedAt);
  return {
    email,
    name: rawName || null,
    avatarUrl: safeAvatarUrl(record.avatarUrl),
    method,
    lastUsedAt: Number.isFinite(lastUsedAt) ? lastUsedAt : 0,
  };
}

/** Most-recent first. Anything unparseable is dropped rather than thrown —
 *  a corrupted entry must never be able to break the login screen, which is
 *  the one page a person cannot route around. */
export function listRememberedAccounts(): RememberedAccount[] {
  if (!isBrowser()) return [];
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const accounts = parsed
      .map(coerceAccount)
      .filter((item): item is RememberedAccount => item !== null);
    const deduped = new Map<string, RememberedAccount>();
    for (const account of accounts) {
      const existing = deduped.get(account.email);
      if (!existing || account.lastUsedAt > existing.lastUsedAt) {
        deduped.set(account.email, account);
      }
    }
    return Array.from(deduped.values())
      .sort((a, b) => b.lastUsedAt - a.lastUsedAt)
      .slice(0, MAX_REMEMBERED_ACCOUNTS);
  } catch {
    return [];
  }
}

function write(accounts: RememberedAccount[]): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(accounts));
  } catch {
    // Private browsing and full-quota devices both throw here. Failing to
    // remember an account is a lost convenience, never a lost sign-in — so
    // this is swallowed rather than surfaced.
  }
}

/** Called after a SUCCESSFUL sign-in only. Recording an attempt would put
 *  a typo'd address on the chooser forever. */
export function rememberAccount(input: {
  email: unknown;
  name?: unknown;
  avatarUrl?: unknown;
  method: RememberedAuthMethod;
  now?: number;
}): void {
  const email = normalizeEmail(input.email);
  if (!email || !email.includes('@')) return;
  const next: RememberedAccount = {
    email,
    name: String(input.name ?? '').trim() || null,
    avatarUrl: safeAvatarUrl(input.avatarUrl),
    method: input.method === 'google' ? 'google' : 'email',
    lastUsedAt: typeof input.now === 'number' ? input.now : Date.now(),
  };
  const rest = listRememberedAccounts().filter((account) => account.email !== email);
  write([next, ...rest].slice(0, MAX_REMEMBERED_ACCOUNTS));
}

export function forgetAccount(email: unknown): void {
  const target = normalizeEmail(email);
  if (!target) return;
  write(listRememberedAccounts().filter((account) => account.email !== target));
}

export function forgetAllAccounts(): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to do — see write() */
  }
}

/** What the login screen should render on arrival. Derived here rather than
 *  in the component so it is testable and so "which screen do I get" has
 *  exactly one answer.
 *
 *    chooser  — we recognise someone. Show accounts, not an empty form.
 *    form     — nobody recognised, or they asked for a different account. */
export function planLoginEntry(accounts: RememberedAccount[]): 'chooser' | 'form' {
  return accounts.length > 0 ? 'chooser' : 'form';
}

/** The initials fallback when there is no avatar. Derived from the NAME
 *  when we have one and the email local-part otherwise — never from the
 *  full email, which would render a "@" or a domain letter as a person's
 *  initial. */
export function accountInitials(account: RememberedAccount): string {
  const source = (account.name || account.email.split('@')[0] || '').trim();
  if (!source) return '?';
  const words = source.split(/[\s._-]+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 1).toUpperCase();
  return (words[0].slice(0, 1) + words[words.length - 1].slice(0, 1)).toUpperCase();
}
