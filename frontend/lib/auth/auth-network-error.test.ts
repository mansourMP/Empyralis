/**
 * MAN-343, second half: signup must never report a definitive failure for a
 * request whose outcome it does not actually know.
 *
 * The first half (a hiccup in the post-signup readiness poll reported as
 * signup failure) was fixed by splitting that poll into its own try/catch.
 * But `signup()` itself can still reject AFTER the server has created the
 * account — a dropped connection, or the auth client's own 30s
 * AbortController firing, with only the RESPONSE lost on the way back. Both
 * used to arrive at the signup page as a plain Error, indistinguishable
 * from a real 409/422, and rendered "Couldn't create the account" on the
 * very first screen a customer touches.
 *
 * This asserts the DISCRIMINATION that makes an honest message possible:
 *
 *   fetch rejected (no response at all)  -> AuthNetworkError  (unknown)
 *   a real non-ok HTTP response          -> plain Error       (definitive)
 *
 * The second case is the one most likely to be broken by a careless later
 * edit ("just throw AuthNetworkError everywhere"), which would turn every
 * genuine "that email is already registered" into a vague "we couldn't
 * confirm" — the same law broken in the other direction. It is asserted
 * explicitly below.
 *
 * Run: npx tsx lib/auth/auth-network-error.test.ts
 */

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

// auth-client reaches for window.setTimeout/clearTimeout (its abort timer)
// and, through csrf.ts, document.cookie. Stub both BEFORE importing it.
// signup() also reads window.location.search (its channel-attribution
// token) and sessionStorage, so the stub has to be a plausible browser,
// not just a timer host.
const memoryStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};
const g = globalThis as unknown as Record<string, unknown>;
g.window = {
  setTimeout: (fn: () => void, ms: number) => setTimeout(fn, ms),
  clearTimeout: (h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>),
  location: { search: "", href: "http://localhost/signup", origin: "http://localhost" },
  sessionStorage: memoryStorage,
  localStorage: memoryStorage,
};
g.document = { cookie: "" };
g.sessionStorage = memoryStorage;
g.localStorage = memoryStorage;

type FetchImpl = () => Promise<unknown>;
function setFetch(impl: FetchImpl): void {
  g.fetch = impl;
}

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k: string) => (k.toLowerCase() === "content-type" ? "application/json" : null) },
    json: async () => body,
  };
}

async function main(): Promise<void> {
  const { signup, AuthNetworkError } = await import("./auth-client");

  // 1. A dropped connection — fetch rejects outright. Outcome UNKNOWN.
  setFetch(() => Promise.reject(new TypeError("Failed to fetch")));
  let caught: unknown = null;
  try {
    await signup("a@example.com", "pw");
  } catch (e) {
    caught = e;
  }
  assert(caught instanceof AuthNetworkError, "a dropped connection throws AuthNetworkError");

  // 2. Our own 30s abort — also no response, so also UNKNOWN.
  setFetch(() => {
    const err = new Error("The operation was aborted.");
    err.name = "AbortError";
    return Promise.reject(err);
  });
  caught = null;
  try {
    await signup("a@example.com", "pw");
  } catch (e) {
    caught = e;
  }
  assert(caught instanceof AuthNetworkError, "an aborted request throws AuthNetworkError");

  // 3. A REAL server rejection. The server answered; nothing was created.
  //    This must stay an ordinary Error so the page keeps rendering a real
  //    failure ("That email is already registered") rather than downgrading
  //    every definitive answer into "we couldn't confirm".
  setFetch(() => Promise.resolve(jsonResponse(409, { detail: "already registered" })));
  caught = null;
  try {
    await signup("a@example.com", "pw");
  } catch (e) {
    caught = e;
  }
  assert(caught instanceof Error, "a 409 still throws");
  assert(
    !(caught instanceof AuthNetworkError),
    "a 409 is NOT AuthNetworkError — the server gave a definitive answer",
  );
  assert(
    String((caught as Error).message).toLowerCase().includes("already"),
    "the definitive 409 keeps its own server-supplied wording",
  );

  // 4. A clean success still resolves normally.
  setFetch(() => Promise.resolve(jsonResponse(200, { ok: true })));
  let ok = false;
  try {
    await signup("a@example.com", "pw");
    ok = true;
  } catch {
    ok = false;
  }
  assert(ok, "a 200 signup resolves");

  console.log(`auth-network-error.test.ts: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

void main();
