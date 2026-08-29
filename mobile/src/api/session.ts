import * as Crypto from 'expo-crypto';
import * as Linking from 'expo-linking';
import * as SecureStore from 'expo-secure-store';
import * as WebBrowser from 'expo-web-browser';

import { ApiError, apiRequest, messageFromBody } from './client';
import { NATIVE_REDIRECT_URI, WEB_ORIGIN } from './config';

/**
 * SIGN-IN IS THE WEBSITE'S JOB. The app opens the real /login in a system
 * browser sheet, the person signs in with whatever the site offers (email,
 * Google, and whatever is added next), and the site hands back a
 * single-use code on a custom scheme the app exchanges for a session.
 *
 * This is not invented here — server_modules/native_auth_service.py and
 * frontend/lib/auth/native-login-handoff.ts are the two halves that already
 * exist, and this file is the third. Read that backend module's header for
 * the full design; the properties this file must not break are:
 *
 *   NO TOKEN EVER RIDES IN THE REDIRECT. Redirect URLs land in browser
 *   history, logs and Referer headers. The sheet hands back a CODE; the
 *   session comes from a separate POST the app makes itself.
 *
 *   THE REDIRECT URI IS NEVER BUILT HERE. The caller picks a KEY ("ios")
 *   and the backend names the URL from its own hardcoded table. A
 *   parameterised redirect_uri would make the mint endpoint an open
 *   redirect on the one page an attacker can most easily get someone to
 *   open — a "log in" link.
 *
 *   THE CODE IS BURNED ON PRESENTATION, before the verifier is compared,
 *   so a wrong verifier cannot be retried against a still-live code.
 *   Nothing here may add a retry around the exchange.
 *
 * "ios" IS THE ONLY KEY THE BACKEND HAS, AND ANDROID USES IT ANYWAY.
 * NATIVE_REDIRECT_TARGETS maps exactly {"ios": "empyralis://auth"} — and
 * that value is a bare custom scheme, which this one Expo app registers
 * identically on both platforms. So Android works today without a backend
 * change. The KEY is misnamed, not the mechanism; see the report.
 */
const NATIVE_TARGET_KEY = 'ios';

const TOKEN_KEY = 'empyralis.session.token';
const ACCOUNT_KEY = 'empyralis.session.account';

export type Session = {
  token: string;
  userId: string;
  email: string;
  name: string;
  workspaceId: string;
};

export type SignInOutcome =
  | { kind: 'signed-in'; session: Session }
  /** The person closed the sheet. Not an error, and must not be reported as
   *  one — a dismissed sheet with a red banner under it reads as a failure
   *  that did not happen. */
  | { kind: 'cancelled' }
  | { kind: 'failed'; message: string };

// ── PKCE ────────────────────────────────────────────────────────────────────
// base64url, unpadded — RFC 7636 S4.2. expo-crypto returns base64 for a
// digest, so the three substitutions are ours to make; getting them wrong
// produces a challenge the backend's own regex rejects and a sheet that
// silently falls back to ordinary web login.
function toBase64Url(value: string): string {
  return value.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// base64url, encoded by hand rather than through btoa. `btoa` is a BROWSER
// global; React Native does not guarantee it, and reaching for it is the
// kind of assumption that fails on exactly one of the two platforms. Twelve
// lines beat a polyfill and a surprise.
const B64URL_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

function bytesToBase64Url(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const b0 = bytes[i];
    const b1 = i + 1 < bytes.length ? bytes[i + 1] : undefined;
    const b2 = i + 2 < bytes.length ? bytes[i + 2] : undefined;
    out += B64URL_ALPHABET[b0 >> 2];
    out += B64URL_ALPHABET[((b0 & 0x03) << 4) | ((b1 ?? 0) >> 4)];
    if (b1 === undefined) break;
    out += B64URL_ALPHABET[((b1 & 0x0f) << 2) | ((b2 ?? 0) >> 6)];
    if (b2 === undefined) break;
    out += B64URL_ALPHABET[b2 & 0x3f];
  }
  return out;
}

function randomToken(byteLength: number): string {
  return bytesToBase64Url(Crypto.getRandomBytes(byteLength));
}

async function createPkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = randomToken(64);
  const digest = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, verifier, {
    encoding: Crypto.CryptoEncoding.BASE64,
  });
  return { verifier, challenge: toBase64Url(digest) };
}

// ── Storage ─────────────────────────────────────────────────────────────────

export async function loadStoredSession(): Promise<Session | null> {
  try {
    const [token, accountRaw] = await Promise.all([
      SecureStore.getItemAsync(TOKEN_KEY),
      SecureStore.getItemAsync(ACCOUNT_KEY),
    ]);
    if (!token || !accountRaw) return null;
    const account = JSON.parse(accountRaw) as Omit<Session, 'token'>;
    if (!account?.userId || !account?.workspaceId) return null;
    return { token, ...account };
  } catch {
    // A store that cannot be read is a signed-out app, not a crashed one.
    return null;
  }
}

export async function persistSession(session: Session): Promise<void> {
  const { token, ...account } = session;
  await SecureStore.setItemAsync(TOKEN_KEY, token);
  await SecureStore.setItemAsync(ACCOUNT_KEY, JSON.stringify(account));
}

export async function clearSession(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(TOKEN_KEY),
    SecureStore.deleteItemAsync(ACCOUNT_KEY),
  ]);
}

// ── Turning a login payload into a Session ──────────────────────────────────

type LoginPayload = {
  token?: string;
  user?: { id?: string; email?: string; name?: string };
  current_workspace_id?: string;
  default_workspace_id?: string;
};

function sessionFromPayload(payload: LoginPayload): Session | null {
  const token = String(payload?.token || '').trim();
  const userId = String(payload?.user?.id || '').trim();
  const workspaceId = String(
    payload?.current_workspace_id || payload?.default_workspace_id || '',
  ).trim();
  if (!token || !userId || !workspaceId) return null;
  return {
    token,
    userId,
    workspaceId,
    email: String(payload?.user?.email || '').trim(),
    name: String(payload?.user?.name || '').trim(),
  };
}

// ── The two doors ───────────────────────────────────────────────────────────

/**
 * The primary door: the real website, in a system browser sheet.
 *
 * WHAT HAPPENS INSIDE THE SHEET IS UNREADABLE TO THIS APP, permanently.
 * It is out-of-process and system-owned. So a WRONG PASSWORD produces no
 * message here on purpose — the site leaves its own inline error on screen
 * and simply never redirects, and inventing a second message would duplicate
 * an error the person is already looking at.
 */
export async function signInThroughWebsite(): Promise<SignInOutcome> {
  let pkce: { verifier: string; challenge: string };
  let state: string;
  try {
    pkce = await createPkcePair();
    state = randomToken(16);
  } catch (error) {
    return { kind: 'failed', message: 'Could not start sign-in on this device.' };
  }

  const authorizeUrl =
    `${WEB_ORIGIN}/login?native=${encodeURIComponent(NATIVE_TARGET_KEY)}` +
    `&code_challenge=${encodeURIComponent(pkce.challenge)}` +
    `&code_challenge_method=S256` +
    `&state=${encodeURIComponent(state)}`;

  let result: WebBrowser.WebBrowserAuthSessionResult;
  try {
    result = await WebBrowser.openAuthSessionAsync(authorizeUrl, NATIVE_REDIRECT_URI);
  } catch {
    return { kind: 'failed', message: 'Could not open the sign-in page.' };
  }

  if (result.type !== 'success' || !result.url) {
    return { kind: 'cancelled' };
  }

  // Linking.parse, NOT `new URL(...).searchParams`. React Native's built-in
  // URL is a partial implementation and `searchParams` is exactly the part
  // it has historically not had — it reads as an empty query rather than
  // throwing, so the code comes back missing and sign-in fails for a reason
  // that names nothing.
  const parsed = Linking.parse(result.url);
  const code = String(parsed.queryParams?.code ?? '');
  const returnedState = String(parsed.queryParams?.state ?? '');

  // A state mismatch is a security refusal and reads as one — never folded
  // into the generic failure, because the two want different responses from
  // whoever sees them.
  if (!returnedState || returnedState !== state) {
    return { kind: 'failed', message: 'Sign-in was interrupted and could not be verified. Try again.' };
  }
  if (!code) {
    return { kind: 'failed', message: 'The sign-in page did not hand back a code.' };
  }

  return exchangeNativeCode(code, pkce.verifier);
}

/**
 * The exchange reads its own 401 body rather than mapping it to "signed
 * out", because on THIS route a 401 is how a misconfiguration reports
 * itself ("Identity token audience is invalid") and that sentence is the
 * entire diagnosis. Everywhere else, mapping 401 to "signed out" is right.
 */
async function exchangeNativeCode(code: string, verifier: string): Promise<SignInOutcome> {
  try {
    const payload = await apiRequest<LoginPayload>('/api/auth/native/exchange', {
      method: 'POST',
      body: { code, code_verifier: verifier, device_platform: 'mobile' },
      rawUnauthorized: true,
    });
    const session = sessionFromPayload(payload);
    if (!session) return { kind: 'failed', message: 'Signed in, but the session came back incomplete.' };
    await persistSession(session);
    return { kind: 'signed-in', session };
  } catch (error) {
    if (error instanceof ApiError) return { kind: 'failed', message: error.failure.message };
    return { kind: 'failed', message: 'Could not finish signing in.' };
  }
}

/**
 * The secondary door, exactly as the iOS app has it: email and password,
 * behind a quiet link. It exists because the browser sheet cannot be
 * automated — it is system-owned and out-of-process — so this is the only
 * path a test can drive, and it is also the path someone takes when the
 * sheet misbehaves.
 *
 * `channel: "mobile"` IS MANDATORY. browser_auth_session_channel() treats an
 * absent or unknown channel as "web", and the web branch STRIPS `token` from
 * the response body — producing a clean HTTP 200 that signs nobody in.
 */
export async function signInWithPassword(email: string, password: string): Promise<SignInOutcome> {
  try {
    const payload = await apiRequest<LoginPayload>('/api/v1/auth/login', {
      method: 'POST',
      body: { email: email.trim(), password, channel: 'mobile' },
      rawUnauthorized: true,
    });
    const session = sessionFromPayload(payload);
    if (!session) return { kind: 'failed', message: 'Signed in, but the session came back incomplete.' };
    await persistSession(session);
    return { kind: 'signed-in', session };
  } catch (error) {
    if (error instanceof ApiError) {
      // A rejected password and an unreachable server must never share a
      // message. "Check your connection" in front of someone who mistyped
      // their password is the exact lie this product has shipped before.
      if (error.failure.kind === 'network') {
        return { kind: 'failed', message: 'Could not reach Empyralis. Check your connection.' };
      }
      return { kind: 'failed', message: error.failure.message };
    }
    return { kind: 'failed', message: 'Could not sign in.' };
  }
}

export { messageFromBody };
