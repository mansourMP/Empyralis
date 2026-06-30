import { chromium } from '@playwright/test';

const baseUrl = (process.env.API_WALL_SMOKE_BASE_URL || 'http://127.0.0.1:3000').replace(/\/+$/, '');
const workspaceId = process.env.API_WALL_SMOKE_WORKSPACE_ID || 'ws-1';
const thresholds = {
  sage: Number(process.env.API_WALL_SMOKE_SAGE_MAX || 6),
  applications: Number(process.env.API_WALL_SMOKE_APPLICATIONS_MAX || 6),
  hardware: Number(process.env.API_WALL_SMOKE_HARDWARE_MAX || 6),
  studio: Number(process.env.API_WALL_SMOKE_STUDIO_MAX || 8),
  activity: Number(process.env.API_WALL_SMOKE_ACTIVITY_MAX || 6),
};

const routes = [
  ['sage', `/w/${workspaceId}/sage`],
  ['applications', `/w/${workspaceId}/applications`],
  ['hardware', `/w/${workspaceId}/hardware`],
  ['studio', `/w/${workspaceId}/agents`],
  ['activity', `/w/${workspaceId}/activity`],
];

async function login(page) {
  const response = await page.request.post(`${baseUrl}/api/auth/login`, {
    data: {
      email: process.env.API_WALL_SMOKE_EMAIL || 'owner@example.com',
      password: process.env.API_WALL_SMOKE_PASSWORD || 'password-123',
      channel: 'web',
    },
  });
  if (!response.ok()) {
    throw new Error(`login failed: ${response.status()}`);
  }
}

function countedRequestKey(url, method) {
  try {
    const parsed = new URL(url);
    if (parsed.origin !== baseUrl || !parsed.pathname.startsWith('/api/') || method !== 'GET') {
      return null;
    }
    return `${method} ${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}

const browser = await chromium.launch();
try {
  const context = await browser.newContext({ baseURL: baseUrl });
  const page = await context.newPage();
  await login(page);
  const results = [];
  for (const [name, path] of routes) {
    const seen = new Set();
    const listener = (request) => {
      const key = countedRequestKey(request.url(), request.method());
      if (key) {
        seen.add(key);
      }
    };
    page.on('request', listener);
    await page.goto(path, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(Number(process.env.API_WALL_SMOKE_SETTLE_MS || 1500));
    page.off('request', listener);
    const count = seen.size;
    const max = thresholds[name] ?? 6;
    results.push({ name, count, max, passed: count <= max });
  }
  const failed = results.filter((item) => !item.passed);
  console.log(JSON.stringify({ baseUrl, workspaceId, results }, null, 2));
  if (failed.length > 0) {
    process.exitCode = 1;
  }
} finally {
  await browser.close();
}
