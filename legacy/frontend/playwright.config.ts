import { defineConfig } from '@playwright/test';

const frontendPort = Number(process.env.PLAYWRIGHT_FRONTEND_PORT ?? 3100);
const backendPort = Number(process.env.PLAYWRIGHT_BACKEND_PORT ?? 8101);
const frontendOrigin = `http://127.0.0.1:${frontendPort}`;
const backendOrigin = `http://127.0.0.1:${backendPort}`;
const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === '1';
const disableWebServer = process.env.PLAYWRIGHT_DISABLE_WEBSERVER === '1';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  reporter: 'list',
  use: {
    baseURL: frontendOrigin,
    headless: true,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: disableWebServer
    ? undefined
    : [
        {
          command: `PLAYWRIGHT_BACKEND_PORT=${backendPort} PLAYWRIGHT_FRONTEND_PORT=${frontendPort} sh ./scripts/start-e2e-backend.sh`,
          url: `${backendOrigin}/health`,
          reuseExistingServer,
          timeout: 120_000,
        },
        {
          command:
            `PORT=${frontendPort} NEXT_DIST_DIR='.next-e2e' EMPYRALIS_API_URL='${backendOrigin}' ORION_API_URL='${backendOrigin}' NEXT_PUBLIC_ORION_API_URL='${backendOrigin}' NEXT_PUBLIC_API_URL='${backendOrigin}' npm run dev:e2e`,
          url: `${frontendOrigin}/login`,
          reuseExistingServer,
          timeout: 120_000,
        },
      ],
});
