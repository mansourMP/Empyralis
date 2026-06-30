const BASE = "https://empyralis-web.onrender.com";
const MOBILE_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1";

const TURN_COUNT = Number.parseInt(process.env.PHASE3_TURN_COUNT || "12", 10);
const cookieJar = new Map();
const results = [];

function splitSetCookie(value) {
  if (!value) return [];
  return String(value)
    .split(/,(?=[^;,]+=)/g)
    .map((item) => item.trim())
    .filter(Boolean);
}

function storeCookies(headers) {
  const values =
    typeof headers.getSetCookie === "function"
      ? headers.getSetCookie()
      : splitSetCookie(headers.get("set-cookie") || "");
  for (const line of values) {
    const pair = String(line).split(";")[0] || "";
    const index = pair.indexOf("=");
    if (index <= 0) continue;
    cookieJar.set(pair.slice(0, index), pair.slice(index + 1));
  }
}

function cookieHeader() {
  return Array.from(cookieJar.entries())
    .map(([key, value]) => `${key}=${value}`)
    .join("; ");
}

function csrfToken() {
  return cookieJar.get("empyralis_csrf_token") || "";
}

function nowMs() {
  return Date.now();
}

function record(name, ok, detail = "", data = undefined) {
  results.push({ name, ok, detail, data });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}: ${detail}`);
  if (!ok && data !== undefined) {
    const text = typeof data === "string" ? data : JSON.stringify(data);
    console.log(text.slice(0, 1400));
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function request(
  method,
  pathOrUrl,
  { payload, headers = {}, mobile = false, redirect = "follow", timeoutMs = 45000 } = {},
) {
  const url = pathOrUrl.startsWith("http") ? pathOrUrl : `${BASE}${pathOrUrl}`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const requestHeaders = {
    accept: "application/json, text/event-stream;q=0.9, text/html;q=0.8, */*;q=0.7",
    "user-agent": mobile ? MOBILE_UA : "EmpyralisPhase3Cert/1.0",
    ...headers,
  };
  const cookies = cookieHeader();
  if (cookies) requestHeaders.cookie = cookies;
  if (payload !== undefined) requestHeaders["content-type"] = "application/json";
  const csrf = csrfToken();
  if (csrf && !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
    requestHeaders["x-csrf-token"] = csrf;
  }

  try {
    const response = await fetch(url, {
      method,
      headers: requestHeaders,
      body: payload === undefined ? undefined : JSON.stringify(payload),
      redirect,
      signal: controller.signal,
    });
    storeCookies(response.headers);
    const text = await response.text();
    return { status: response.status, headers: response.headers, text, url: response.url };
  } catch (error) {
    return {
      status: 0,
      headers: new Headers(),
      text: `${error?.name || "Error"}: ${error?.message || error}`,
      url,
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function jsonRequest(method, pathOrUrl, options = {}) {
  const response = await request(method, pathOrUrl, options);
  let json = null;
  try {
    json = JSON.parse(response.text);
  } catch {}
  return { ...response, json };
}

function findWorkspace(payload) {
  if (!payload || typeof payload !== "object") return { workspaceId: null, tenantId: null };
  const candidates = [];
  if (payload.workspace && typeof payload.workspace === "object") {
    candidates.push({
      workspace: payload.workspace,
      tenantId:
        payload.workspace.tenantId ||
        payload.workspace.tenant_id ||
        payload.tenantId ||
        payload.tenant_id,
    });
  }
  for (const key of ["workspaceMemberships", "memberships"]) {
    if (!Array.isArray(payload[key])) continue;
    for (const item of payload[key]) {
      if (!item || typeof item !== "object") continue;
      if (item.workspace && typeof item.workspace === "object") {
        candidates.push({
          workspace: item.workspace,
          tenantId: item.tenantId || item.tenant_id || item.workspace.tenantId || item.workspace.tenant_id,
        });
      } else {
        candidates.push({
          workspace: item,
          tenantId: item.tenantId || item.tenant_id,
        });
      }
    }
  }
  for (const candidate of candidates) {
    const item = candidate.workspace || {};
    const workspaceId = String(item.id || item.workspace_id || "").trim();
    const tenantId = String(candidate.tenantId || item.tenantId || item.tenant_id || "").trim();
    if (workspaceId) return { workspaceId, tenantId: tenantId || null };
  }
  const match = JSON.stringify(payload).match(/ws_[A-Za-z0-9]+/);
  return { workspaceId: match?.[0] || null, tenantId: null };
}

function summarizeProviders(payload) {
  let providers = [];
  if (Array.isArray(payload?.providers)) providers = payload.providers;
  if (payload?.providers && !Array.isArray(payload.providers) && typeof payload.providers === "object") {
    providers = Object.values(payload.providers);
  }
  const selected = [];
  for (const item of providers) {
    const id = String(item?.id || item?.provider || "").toLowerCase();
    if (["deepseek", "gemini", "openai", "anthropic", "ollama"].includes(id)) {
      selected.push({
        id,
        configured: item.configured,
        usable: item.usable,
        status: item.status || item.reason || item.ui_status || null,
      });
    }
  }
  return { count: providers.length, selected };
}

function parseSseEvents(text) {
  const lines = String(text || "").split(/\r?\n/);
  const out = [];
  let currentEvent = "message";
  let currentData = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      currentEvent = line.slice(6).trim() || "message";
      continue;
    }
    if (line.startsWith("data:")) {
      currentData.push(line.slice(5).trim());
      continue;
    }
    if (line === "") {
      if (currentData.length) {
        const joined = currentData.join("\n");
        let parsed = null;
        try {
          parsed = JSON.parse(joined);
        } catch {}
        out.push({ event: currentEvent, data: joined, parsed });
      }
      currentEvent = "message";
      currentData = [];
    }
  }
  if (currentData.length) {
    const joined = currentData.join("\n");
    let parsed = null;
    try {
      parsed = JSON.parse(joined);
    } catch {}
    out.push({ event: currentEvent, data: joined, parsed });
  }
  return out;
}

function hasRawErrorText(text) {
  const markers = [
    "traceback",
    "stack trace",
    "runtimeerror:",
    "valueerror:",
    "keyerror:",
    "internal server error",
    "chat stream was interrupted while the runtime was unavailable",
    "bootstrap returned",
    "\"error_code\":",
    "\"kind\":\"terminal_error\"",
  ];
  const normalized = String(text || "").toLowerCase();
  return markers.some((marker) => normalized.includes(marker));
}

function providerFromEvents(parsedEvents) {
  for (let i = parsedEvents.length - 1; i >= 0; i -= 1) {
    const item = parsedEvents[i]?.parsed;
    if (!item || typeof item !== "object") continue;
    const direct =
      String(item.provider || item.provider_id || item.providerId || item.model_provider || "").trim().toLowerCase();
    if (direct) return direct;
    const metadata = item.metadata;
    if (metadata && typeof metadata === "object") {
      const nested = String(
        metadata.provider || metadata.provider_id || metadata.providerId || metadata.model_provider || "",
      )
        .trim()
        .toLowerCase();
      if (nested) return nested;
    }
  }
  return null;
}

function threadTurnCount(payload) {
  const turns = Array.isArray(payload?.turns) ? payload.turns : [];
  return turns.length;
}

function threadContainsUserPrompt(payload, prompt) {
  if (!payload || !Array.isArray(payload.turns)) return false;
  for (let i = payload.turns.length - 1; i >= 0; i -= 1) {
    const turn = payload.turns[i];
    const role = String(turn?.role || turn?.actor_role || "").toLowerCase();
    const text = String(turn?.content || turn?.text || turn?.message || "").trim();
    if (role === "user" && text.includes(prompt)) return true;
  }
  return false;
}

async function waitForThreadState({ threadId, expectedMinimumTurns, userPrompt, attempts = 8, waitMs = 1000 }) {
  let last = null;
  for (let i = 0; i < attempts; i += 1) {
    const response = await jsonRequest("GET", `/api/threads/${encodeURIComponent(threadId)}`, { timeoutMs: 30000 });
    if (response.status === 200 && response.json && threadTurnCount(response.json) >= expectedMinimumTurns) {
      if (!userPrompt || threadContainsUserPrompt(response.json, userPrompt)) return response;
      last = response;
    } else {
      last = response;
    }
    await delay(waitMs);
  }
  return last;
}

async function main() {
  const startedAt = nowMs();
  const indexResponse = await request("GET", "/", { timeoutMs: 30000 });
  record("prod_web_http", indexResponse.status === 200, `HTTP ${indexResponse.status}`);
  if (indexResponse.status !== 200) return;

  const email = `phase3-cert-${Date.now()}-${crypto.randomUUID().slice(0, 6)}@example.com`;
  const signup = await jsonRequest("POST", "/api/auth/signup", {
    timeoutMs: 45000,
    payload: {
      email,
      password: `P3Cert-${crypto.randomUUID()}-1a!`,
      name: "Phase3 Cert",
      channel: "web",
    },
  });
  record("fresh_signup", signup.status === 200 && Boolean(signup.json), `HTTP ${signup.status}; email=${email}`, signup.status === 200 ? undefined : signup.text);
  if (signup.status !== 200) return;

  const shell = await jsonRequest("GET", "/api/auth/account-shell", { timeoutMs: 45000 });
  const { workspaceId, tenantId } = findWorkspace(shell.json);
  record(
    "account_shell",
    shell.status === 200 && Boolean(workspaceId),
    `HTTP ${shell.status}; workspace=${workspaceId}; tenant=${tenantId || "default"}`,
    shell.status === 200 ? undefined : shell.text,
  );
  if (!workspaceId) return;

  const providers = await jsonRequest("GET", `/api/providers/catalog?workspace_id=${workspaceId}`, { timeoutMs: 45000 });
  const providerSummary = summarizeProviders(providers.json);
  const preferredProvider = providerSummary.selected.find(
    (item) => ["deepseek", "gemini", "openai"].includes(item.id) && item.usable === true,
  );
  record(
    "provider_catalog",
    providers.status === 200 && Boolean(preferredProvider?.id),
    `HTTP ${providers.status}; preferred=${preferredProvider?.id || "none"}; selected=${JSON.stringify(providerSummary.selected)}`,
    providers.status === 200 ? undefined : providers.text,
  );
  if (!preferredProvider?.id) return;

  const threadId = `thread-phase3-${crypto.randomUUID().slice(0, 10)}`;
  const resolvedTenantId = String(tenantId || "default").trim() || "default";
  const actor = { type: "user", id: "phase3-cert", display_name: "Phase 3 Cert" };
  const session = await jsonRequest("POST", "/api/sessions", {
    payload: {
      tenant_id: resolvedTenantId,
      workspace_id: workspaceId,
      channel: "web",
      actor,
      metadata: { thread_id: threadId, source: "phase3_cert" },
    },
  });
  const sessionId = String(session.json?.session_id || session.json?.id || "").trim();
  record(
    "sage_session_create",
    session.status === 200 && Boolean(sessionId),
    `HTTP ${session.status}; session=${sessionId.slice(0, 12)}; thread=${threadId}`,
    session.status === 200 ? undefined : session.text,
  );
  if (!sessionId) return;

  let streamedTurns = 0;
  let persistencePasses = 0;
  let providerMetadataPasses = 0;
  let noRawErrorPasses = 0;
  let noTimeoutBannerPasses = 0;
  let sawThinkingTurns = 0;

  for (let turnIndex = 1; turnIndex <= TURN_COUNT; turnIndex += 1) {
    const prompt = `phase3 turn ${turnIndex}: reply with exactly "phase3-ok-${turnIndex}"`;
    let turn = null;
    let turnAttempt = 0;
    while (turnAttempt < 3) {
      turnAttempt += 1;
      turn = await request("POST", "/api/turn", {
        headers: { accept: "text/event-stream" },
        timeoutMs: 120000,
        payload: {
          tenant_id: resolvedTenantId,
          workspace_id: workspaceId,
          thread_id: threadId,
          session_id: sessionId,
          client_request_id: `phase3-${crypto.randomUUID()}`,
          channel: "web",
          actor,
          message: prompt,
          provider: preferredProvider.id,
          attachments: [],
          context_hints: {
            source: "phase3_cert",
            thread_id: threadId,
            force_direct_chat: true,
          },
          execution_mode: "sync",
          response_mode: "stream",
          policy_context: {},
        },
      });
      const transient = [0, 429, 502, 503, 504].includes(Number(turn.status));
      if (!transient || turnAttempt >= 3) break;
      await delay(1200 * turnAttempt);
    }

    const parsedEvents = parseSseEvents(turn.text);
    const eventNames = parsedEvents.map((item) => item.event);
    const hasFinal = eventNames.includes("final");
    const hasChunk = eventNames.includes("chunk");
    const hasThinking = eventNames.includes("step") || eventNames.includes("trace");
    const timedOutBanner = String(turn.text || "").toLowerCase().includes("took too long");
    const rawError = hasRawErrorText(turn.text);
    const providerSeen = providerFromEvents(parsedEvents);
    const providerMatch = !providerSeen || providerSeen === preferredProvider.id;

    const streamOk = turn.status === 200 && hasFinal && (hasChunk || turn.text.includes("phase3-ok-"));
    record(
      `sage_stream_turn_${turnIndex}`,
      streamOk,
      `HTTP ${turn.status}; attempt=${turnAttempt}; events=${[...new Set(eventNames)].sort().join(",")}; bytes=${turn.text.length}; provider_seen=${providerSeen || "n/a"}`,
      streamOk ? undefined : turn.text,
    );
    if (!streamOk) break;
    streamedTurns += 1;
    if (hasThinking) sawThinkingTurns += 1;
    if (!timedOutBanner) noTimeoutBannerPasses += 1;
    if (!rawError) noRawErrorPasses += 1;
    if (providerMatch) providerMetadataPasses += 1;

    const thread = await waitForThreadState({
      threadId,
      expectedMinimumTurns: streamedTurns,
      userPrompt: `phase3 turn ${turnIndex}`,
      attempts: 8,
      waitMs: 900,
    });
    const turnCount = threadTurnCount(thread?.json);
    const persisted = thread?.status === 200 && turnCount >= streamedTurns;
    record(
      `thread_persist_turn_${turnIndex}`,
      persisted,
      `HTTP ${thread?.status}; turns=${turnCount}; expected>=${streamedTurns}`,
      persisted ? undefined : thread?.text,
    );
    if (persisted) persistencePasses += 1;
  }

  record(
    "phase3_rollup",
    streamedTurns === TURN_COUNT &&
      persistencePasses === TURN_COUNT &&
      providerMetadataPasses === TURN_COUNT &&
      noRawErrorPasses === TURN_COUNT &&
      noTimeoutBannerPasses === TURN_COUNT &&
      sawThinkingTurns > 0,
    [
      `turns=${streamedTurns}/${TURN_COUNT}`,
      `persisted=${persistencePasses}/${TURN_COUNT}`,
      `provider_meta=${providerMetadataPasses}/${TURN_COUNT}`,
      `no_raw_error=${noRawErrorPasses}/${TURN_COUNT}`,
      `no_timeout_banner=${noTimeoutBannerPasses}/${TURN_COUNT}`,
      `thinking_turns=${sawThinkingTurns}`,
      `duration_ms=${nowMs() - startedAt}`,
    ].join("; "),
  );

  console.log("\nJSON_SUMMARY");
  console.log(JSON.stringify(results, null, 2));
}

await main();
process.exit(results.every((item) => item.ok) ? 0 : 1);
