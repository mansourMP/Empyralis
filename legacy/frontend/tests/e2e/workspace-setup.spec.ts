// @ts-nocheck
import { expect, test } from '@playwright/test';
import { loginAsOwner } from './support/auth';

test.describe('workspace setup', () => {
  test('fresh signup lands on onboarding and enters the configured workspace', async ({ page }) => {
    const uniqueEmail = `owner-${Date.now()}@example.com`;
    await page.goto('/signup');
    await page.getByLabel('Email').fill(uniqueEmail);
    await page.getByLabel('Password').fill('password-123');
    await page.getByLabel('Name').fill('Owner Example');
    await page.getByRole('button', { name: /sign up/i }).click();

    await expect(page).toHaveURL(/\/(?:onboarding|workspaces\/new)/);
    await expect(page.getByRole('heading', { name: /finish workspace setup|create workspace/i })).toBeVisible();
    await page.getByLabel('Workspace name').fill('Acme Deal Room');
    await page.getByLabel('Shell profile').selectOption('document_workstation_shell');
    await page.getByLabel('Default route').selectOption(/sage$/);
    await page.getByRole('button', { name: /save workspace setup|create workspace/i }).click();

    await page.waitForURL(/\/w\/[^/]+\/sage$/);
  });

  test('existing users can create a second workspace and see it in the switcher', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/workspaces/new');
    await page.getByLabel('Workspace name').fill('Second Workspace');
    await page.getByLabel('Workspace type').selectOption('team');
    await page.getByLabel('Shell profile').selectOption('operations_admin_shell');
    await page.getByLabel('Default route').selectOption('/sage');
    await page.getByRole('button', { name: /create workspace/i }).click();

    await page.waitForURL(/\/w\/[^/]+\/sage$/);
    await expect(page.getByRole('link', { name: /open second workspace/i })).toBeVisible();
  });

  test('direct navigation to an unavailable workspace falls back to the primary ready workspace', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/w/ws-incomplete/chat');
    await expect(page).toHaveURL(/\/w\/ws-1\/sage$/);
    await expect(page.locator('[data-workstation-surface="chat"]')).toBeVisible();
  });
});
