// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

async function mockAgentComputerSettings(page) {
  const registration = {
    gateway_id: 'gw-1',
    workspace_id: 'ws-1',
    display_name: 'Mansur Mac mini',
    platform: 'macos-arm64',
    status: 'online',
    connection_status: 'online',
    device_trust_state: 'verified',
    last_seen_at: '2026-05-25T00:00:00Z',
    runtime_access_mode: 'custom',
    runtime_access_label: 'Custom',
    agent_computer_policy_id: 'agent-computer:gw-1',
    capabilities: ['file.write', 'browser.read', 'terminal.approved_script'],
  };
  let savedPolicy = null;

  await page.route('**/api/gateway/registrations?**', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }
    await route.fulfill({ json: { items: [registration] } });
  });
  await page.route('**/api/gateway/registrations/gw-1/doctor**', async (route) => {
    await route.fulfill({
      json: {
        status: 'healthy',
        checks: [],
        browser: { status: 'ready' },
        providers: { status: 'ready' },
        checkpoint: { status: 'ready' },
        quota: { status: 'ready' },
      },
    });
  });
  await page.route('**/api/gateway/registrations/gw-1/approvals', async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
  await page.route('**/api/gateway/registrations/gw-1/browser/sessions', async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
  await page.route('**/api/gateway/registrations/gw-1/events?**', async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
  await page.route('**/api/personal-channels/*/gateways/gw-1', async (route) => {
    await route.fulfill({ json: { connected: false, status: 'not_configured' } });
  });
  await page.route('**/api/agent-computers/gw-1/policy**', async (route) => {
    if (route.request().method() === 'PUT') {
      savedPolicy = route.request().postDataJSON()?.policy ?? null;
      await route.fulfill({
        json: {
          workspace_id: 'ws-1',
          computer_id: 'gw-1',
          policy_id: 'agent-computer:gw-1',
          saved: true,
          runtime_access_mode: 'custom',
          policy: savedPolicy,
          effective_policy: savedPolicy,
          emergency_stop: { active: false },
        },
      });
      return;
    }
    const policyPayload = savedPolicy ?? {
      policy_id: 'agent-computer:gw-1',
      autonomy_mode: 'ask_every_time',
      filesystem_scope: [],
      blocked_filesystem_scope: [],
      domain_allowlist: [],
      terminal_policy: 'review_required',
      network_policy: 'approval_required',
      browser_access_policy: 'approval_required',
      app_access_policy: 'approval_required',
      approval_ttl_seconds: 900,
      max_runtime_seconds: 0,
      max_budget_cents: 0,
      emergency_stop_enabled: true,
    };
    await route.fulfill({
      json: {
        workspace_id: 'ws-1',
        computer_id: 'gw-1',
        policy_id: 'agent-computer:gw-1',
        saved: Boolean(savedPolicy),
        runtime_access_mode: 'custom',
        policy: policyPayload,
        effective_policy: policyPayload,
        emergency_stop: { active: false },
      },
    });
  });

  return {
    savedPolicy: () => savedPolicy,
  };
}

test.describe('gateway surface aliases', () => {
  test('legacy My Computer route redirects to the Hardware surface', async ({ page }) => {
    await loginAsOwner(page);

    await page.goto('/w/ws-1/gateway');

    await expect(page).toHaveURL(/\/w\/ws-1\/hardware(?:[/?#]|$)/);
    await expect(page.getByRole('heading', { name: /^Hardware$/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /^My Computer$/ })).toHaveCount(0);
  });

  test('legacy gateway detail aliases redirect to the Hardware surface', async ({ page }) => {
    await loginAsOwner(page);

    await page.goto('/w/ws-1/gateway-approvals');
    await expect(page).toHaveURL(/\/w\/ws-1\/hardware(?:[/?#]|$)/);
    await expect(page.getByRole('heading', { name: /^Hardware$/ })).toBeVisible();

    await page.goto('/w/ws-1/gateway-activity');
    await expect(page).toHaveURL(/\/w\/ws-1\/hardware(?:[/?#]|$)/);
    await expect(page.getByRole('heading', { name: /^Hardware$/ })).toBeVisible();
  });
});

test.describe('Agent Computer policy settings', () => {
  test('routes Agent Computer management to the Hardware surface', async ({ page }) => {
    await mockAgentComputerSettings(page);
    await loginAsOwner(page);

    await page.goto('/w/ws-1/hardware');

    await expect(page).toHaveURL(/\/w\/ws-1\/hardware$/);
    await expect(page.getByRole('heading', { name: /^Hardware$/ })).toBeVisible();
    await expect(page.getByText('The computers Empyralis can use.')).toBeVisible();
    await expect(page.getByRole('button', { name: /^Connect$/ }).first()).toBeVisible();
  });

  test('Cloud VPS setup flow uses server regions and full-access pairing metadata', async ({ page }) => {
    await mockAgentComputerSettings(page);
    let provisionBody = null;

    await page.route('**/api/hardware/vps/oauth/digitalocean/start?**', async (route) => {
      await route.fulfill({
        json: {
          provider: 'digitalocean',
          oauth_redirect: 'https://oauth.example.test/digitalocean',
          redirect_uri: 'https://empyralis.ai/api/hardware/vps/oauth/digitalocean/callback',
          state: 'state_1',
        },
      });
    });
    await page.route('https://oauth.example.test/digitalocean', async (route) => {
      await route.fulfill({ html: '<html><body>DigitalOcean OAuth</body></html>' });
    });
    await page.route('**/api/hardware/vps/plans?provider=digitalocean&**', async (route) => {
      await route.fulfill({
        json: {
          provider: 'digitalocean',
          plans: [
            {
              id: 's-1vcpu-2gb',
              slug: 's-1vcpu-2gb',
              label: '1 CPU · 2GB · 50GB SSD',
              vcpus: 1,
              memory_mb: 2048,
              disk_gb: 50,
              price_monthly: 12,
              price_label: '$12/mo',
              recommended: false,
            },
            {
              id: 's-2vcpu-4gb',
              slug: 's-2vcpu-4gb',
              label: '2 CPU · 4GB · 80GB SSD',
              vcpus: 2,
              memory_mb: 4096,
              disk_gb: 80,
              price_monthly: 24,
              price_label: '$24/mo',
              recommended: true,
            },
          ],
        },
      });
    });
    await page.route('**/api/hardware/vps/regions?provider=digitalocean', async (route) => {
      await route.fulfill({
        json: {
          provider: 'digitalocean',
          label: 'DigitalOcean',
          default_region: 'nyc3',
          default_size: 's-1vcpu-2gb',
          regions: [
            { id: 'nyc3', label: 'New York 3' },
            { id: 'sfo3', label: 'San Francisco 3' },
          ],
        },
      });
    });
    await page.route('**/api/hardware/vps/provision', async (route) => {
      provisionBody = route.request().postDataJSON();
      await route.fulfill({
        json: {
          pairing_token: 'pair_test',
          vps_id: 'vps_1',
          provider_resource_id: 'do-1',
          public_ip: '203.0.113.10',
          status: 'provisioning',
        },
      });
    });
    await page.route('**/api/hardware/vps/vps_1/status', async (route) => {
      await route.fulfill({
        json: {
          vps_id: 'vps_1',
          provider: 'digitalocean',
          provider_resource_id: 'do-1',
          public_ip: '203.0.113.10',
          region: 'sfo3',
          size: 's-1vcpu-2gb',
          status: 'registering',
        },
      });
    });
    await loginAsOwner(page);

    await page.goto('/w/ws-1/hardware');
    await page.getByRole('button', { name: /^Connect$/ }).first().click();
    await page.getByRole('button', { name: /^Choose provider$/ }).click();
    await expect(page.getByRole('heading', { name: /^Choose your cloud provider$/ })).toBeVisible();
    await page.getByRole('button', { name: /DigitalOcean/ }).click();
    await expect(page.getByRole('button', { name: /^Log in with DigitalOcean$/ })).toBeVisible();

    const popupPromise = page.waitForEvent('popup');
    await page.getByRole('button', { name: /^Log in with DigitalOcean$/ }).click();
    await popupPromise;
    await page.evaluate(() => {
      window.postMessage(
        {
          type: 'empyralis:vps-oauth',
          provider: 'digitalocean',
          token_id: 'vps_token_do_1',
          account_email: 'owner@example.com',
        },
        '*',
      );
    });
    await expect(page.getByRole('heading', { name: /^Choose your server plan$/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /2 CPU · 4GB · 80GB SSD/ })).toBeVisible();
    await page.getByRole('button', { name: /2 CPU · 4GB · 80GB SSD/ }).click();
    await page.getByRole('button', { name: /^Continue/ }).click();
    await expect(page.getByRole('heading', { name: /^Choose region$/ })).toBeVisible();
    await expect(page.getByLabel('Region')).toHaveValue('nyc3');
    await page.getByLabel('Region').selectOption('sfo3');
    await page.getByRole('button', { name: /^Create server/ }).click();

    await expect(page.getByRole('heading', { name: /^Creating Agent Computer$/ })).toBeVisible();
    await expect(page.getByText('Installing Agent Computer...')).toBeVisible();
    expect(provisionBody).toMatchObject({
      workspace_id: 'ws-1',
      provider: 'digitalocean',
      token_id: 'vps_token_do_1',
      region: 'sfo3',
      size: 's-2vcpu-4gb',
      runtime_access_mode: 'full_access',
      autonomous_agent_setup_warning_acknowledged: true,
      metadata: {
        autonomous_agent_setup_warning_version: '2026-06-06',
      },
    });
  });

  test('Cloud VPS skips provider access when account is already connected', async ({ page }) => {
    await mockAgentComputerSettings(page);
    await page.addInitScript(() => {
      window.localStorage.setItem(
        'empyralis:vps-provider-connections:ws-1',
        JSON.stringify({
          digitalocean: {
            provider: 'digitalocean',
            tokenId: 'vps_token_do_saved',
            accountLabel: 'owner@example.com',
            connectedAt: '2026-06-09T00:00:00.000Z',
          },
        }),
      );
    });
    await page.route('**/api/hardware/vps/plans?provider=digitalocean&**', async (route) => {
      await expect(route.request().url()).toContain('token_id=vps_token_do_saved');
      await route.fulfill({
        json: {
          provider: 'digitalocean',
          plans: [
            {
              id: 's-2vcpu-4gb',
              slug: 's-2vcpu-4gb',
              label: '2 CPU · 4GB · 80GB SSD',
              vcpus: 2,
              memory_mb: 4096,
              disk_gb: 80,
              price_monthly: 24,
              price_label: '$24/mo',
              recommended: true,
            },
          ],
        },
      });
    });
    await page.route('**/api/hardware/vps/regions?provider=digitalocean', async (route) => {
      await route.fulfill({
        json: {
          provider: 'digitalocean',
          default_region: 'nyc3',
          regions: [{ id: 'nyc3', label: 'New York 3' }],
        },
      });
    });
    await loginAsOwner(page);

    await page.goto('/w/ws-1/hardware');
    await page.getByRole('button', { name: /^Connect$/ }).first().click();
    await page.getByRole('button', { name: /^Choose provider$/ }).click();

    await expect(page.getByText('connected · owner@example.com')).toBeVisible();
    await page.getByRole('button', { name: /DigitalOcean/ }).click();
    await expect(page.getByRole('heading', { name: /^Choose your server plan$/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Log in with DigitalOcean$/ })).toHaveCount(0);
  });

  test('Cloud VPS advanced providers route to the SSH setup path', async ({ page }) => {
    await mockAgentComputerSettings(page);
    await loginAsOwner(page);

    await page.goto('/w/ws-1/hardware');
    await page.getByRole('button', { name: /^Connect$/ }).first().click();
    await page.getByRole('button', { name: /^Choose provider$/ }).click();

    await expect(page.getByRole('heading', { name: /^Choose your cloud provider$/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /AWS Lightsail/ })).toBeEnabled();
    await expect(page.getByRole('button', { name: /Google Cloud/ })).toBeEnabled();
    await expect(page.getByRole('button', { name: /Custom SSH/ })).toBeEnabled();

    await page.getByRole('button', { name: /Custom SSH/ }).click();

    await expect(page.getByRole('heading', { name: /^Remote server$/ })).toBeVisible();
    await expect(page.getByLabel('Host')).toBeVisible();
  });
});
