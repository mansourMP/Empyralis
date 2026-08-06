// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

const FORBIDDEN_VISIBLE_TERMS = [
  'owner',
  'member',
  'default',
  'ws-1',
  'Personal Shell',
];

function normalizeVisibleText(input: string): string {
  return input.replace(/\s+/g, ' ').trim().toLowerCase();
}

test.describe('launch sage-first smoke', () => {
  // Was "normal login path lands in sage with clean chat-first shell" —
  // asserted the post-login URL settled on `/w/ws-1/sage` with
  // `[data-workstation-chat-composer="root"] textarea` auto-focused. Both
  // premises are gone: `/sage` now immediately redirects again, to
  // `/agents` (see frontend/app/(account)/w/[workspaceId]/sage/page.tsx —
  // "Sage is now a corner console, not a full-page route"), and
  // agents/page.tsx documents the underlying product decision directly:
  // "Phase UC: Workspace landing = Fleet Home (not Sage chat redirect)."
  // Chat is no longer where login lands or auto-focuses; it is one click
  // away behind the "Ask AI" rail launcher (SageLauncher.tsx). What's still
  // real and worth keeping: login should land in a clean shell that never
  // leaks raw internal ids/roles, and never detours through onboarding.
  test('normal login path lands in a clean shell with no leaked internal ids', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/', { waitUntil: 'networkidle' });
    await expect(page).toHaveURL(/\/w\/ws-1\/agents(?:[/?#]|$)/, { timeout: 20_000 });
    await expect(page.locator('.fleet-root')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Ask AI' })).toBeVisible();

    await expect(page.getByText(/finish workspace setup/i)).toHaveCount(0);
    await expect(page.getByText(/workspace setup/i)).toHaveCount(0);
    await expect(page).not.toHaveURL(/\/onboarding(?:[/?#]|$)/);

    // Scoped to the workspace content area (`main`), not the whole body:
    // the persistent account rail legitimately shows the signed-in
    // account's own email ("owner@example.com" — this suite's fixed e2e
    // fixture, see start-e2e-backend.sh's EMAIL constant), which contains
    // "owner" as a substring incidentally. That is the normal, desired
    // "you are signed in as ..." UI, not a raw-id/role leak — the same
    // distinction the original test's own name draws. What actually
    // matters (a raw workspace id or role token standing in for a human
    // label somewhere in the page's own content) still gets caught here.
    const visibleText = normalizeVisibleText(await page.locator('main').innerText());
    for (const term of FORBIDDEN_VISIBLE_TERMS) {
      expect(visibleText.includes(term.toLowerCase())).toBeFalsy();
    }
  });

  // Was "web chat exposes recent chats and preserves the previous chat when
  // starting a new one" — asserted an inline `.app-chat-history-pill` bar
  // on the old full-page chat. That bar is gone with the rest of the old
  // full-page chat, but the capability it tested is very much alive and
  // moved: SageLauncher.tsx's docked "Ask AI" console now owns it — a
  // "New chat" action (aria-label "New chat") and a "History" view
  // (aria-label "History") reading GET /api/threads, the same real
  // control-plane thread store AgentChat already loads a single thread
  // from (see SageConsolePanels.tsx's own header comment). Rewritten
  // against that live surface.
  test('Ask AI console exposes conversation history and preserves the previous chat when starting a new one', async ({ page }) => {
    await loginAsOwner(page);

    const accountShell = await page.request.get('/api/auth/account-shell');
    expect(accountShell.ok()).toBeTruthy();
    const ownerId = (await accountShell.json())?.account?.id;
    expect(ownerId).toBeTruthy();

    // Real backend for auth/workspace bootstrap (loginAsOwner above); mock
    // only the chat-turn endpoints AgentChat/SageConsolePanels talk to, so
    // this doesn't depend on a real model call — same convention every
    // other chat-focused spec in this suite already uses.
    await page.addInitScript((owner) => {
      const originalFetch = window.fetch.bind(window);
      const state = { threads: {} as Record<string, { title: string; turns: any[] }> };
      const jsonResponse = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), {
        status,
        headers: { 'content-type': 'application/json' },
      });

      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
        const method = (init?.method ?? 'GET').toUpperCase();

        if (url.includes('/api/sessions') && method === 'POST') {
          return jsonResponse({ session_id: 'session-e2e-history', tenant_id: 't-1', workspace_id: 'ws-1' });
        }

        if (url.includes('/api/turn') && method === 'POST') {
          const body = init?.body && typeof init.body === 'string' ? JSON.parse(init.body) : {};
          const threadId = String(body?.thread_id ?? '');
          const message = String(body?.message ?? '');
          const thread = state.threads[threadId] || (state.threads[threadId] = { title: '', turns: [] });
          if (!thread.title) thread.title = message.slice(0, 60) || 'New chat';
          const now = new Date().toISOString();
          thread.turns.push({ id: `turn-user-${thread.turns.length}`, role: 'user', status: 'completed', content: message, created_at: now, metadata: {} });
          thread.turns.push({ id: `turn-assistant-${thread.turns.length}`, role: 'assistant', status: 'completed', content: 'Got it.', created_at: now, metadata: {} });
          return jsonResponse({ status: 'completed', reply: 'Got it.', thread_id: threadId, run_id: `run-${Date.now()}` });
        }

        const singleThreadMatch = url.match(/\/api\/threads\/([^/?]+)/);
        if (singleThreadMatch && method === 'GET') {
          const id = decodeURIComponent(singleThreadMatch[1]);
          const thread = state.threads[id];
          if (!thread) return new Response('', { status: 404 });
          return jsonResponse({ id, thread_id: id, title: thread.title, turns: thread.turns });
        }

        if (/\/api\/threads(\?|$)/.test(url) && method === 'GET') {
          const items = Object.entries(state.threads).map(([id, t]) => ({
            id,
            owner_user_id: owner,
            title: t.title,
            updated_at: new Date().toISOString(),
          }));
          return jsonResponse({ items });
        }

        return originalFetch(input, init);
      };
    }, ownerId);

    await page.goto('/w/ws-1/agents', { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Ask AI' }).click();

    const askAi = page.getByRole('dialog', { name: 'Ask AI' });
    await expect(askAi).toBeVisible();

    const composer = askAi.locator('textarea');
    await composer.fill('what agents do I have?');
    await composer.press('Enter');

    await expect(askAi.locator('article[data-chat-role="user"]').filter({ hasText: 'what agents do I have?' })).toBeVisible();
    await expect(askAi.locator('article[data-chat-role="assistant"]').filter({ hasText: 'Got it.' })).toBeVisible();

    const newChatButton = askAi.getByRole('button', { name: 'New chat' });
    await expect(newChatButton).toBeVisible();
    await newChatButton.click();

    await expect(composer).toHaveValue('');
    await expect(askAi.locator('article[data-chat-role="user"]')).toHaveCount(0);

    const historyButton = askAi.getByRole('button', { name: 'History' });
    await expect(historyButton).toBeVisible();
    await historyButton.click();

    const historyRow = askAi.getByRole('listitem').filter({ hasText: 'what agents do I have?' });
    await expect(historyRow).toBeVisible();

    await historyRow.click();
    await expect(askAi.locator('article[data-chat-role="user"]').filter({ hasText: 'what agents do I have?' })).toBeVisible();
    await expect(askAi.locator('article[data-chat-role="assistant"]').filter({ hasText: 'Got it.' })).toBeVisible();
  });
});
