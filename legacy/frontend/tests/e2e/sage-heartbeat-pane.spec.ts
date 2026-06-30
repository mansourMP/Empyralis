// @ts-nocheck
import { expect, test } from '@playwright/test';

import { loginAsOwner } from './support/auth';

test.describe('sage tasks pane', () => {
  test('shows product queue lanes and quiet-hours truth', async ({ page }) => {
    await page.route('**/api/sage-heartbeat?workspace_id=ws-1', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          workspace_id: 'ws-1',
          tenant_id: 'tenant-1',
          profile: {
            recurring_responsibility: 'Keep my inbox triaged.',
          },
          bootstrap: {
            complete: true,
            progress_label: '5/5',
          },
          quiet_hours: {
            start_hour: 22,
            end_hour: 7,
            label: '22:00–07:00',
          },
          reminders: {
            count: 2,
            items: [
              {
                id: 'sched-1',
                name: 'Morning follow-up',
                enabled: true,
                schedule_kind: 'cron',
                next_run_at: '2026-05-06T09:00:00Z',
                wake_mode: 'next-heartbeat',
                delivery: 'announce',
                pending_heartbeat: true,
                timezone: 'local',
              },
              {
                id: 'sched-2',
                name: 'Evening recap',
                enabled: true,
                schedule_kind: 'cron',
                next_run_at: '2026-05-06T18:00:00Z',
                wake_mode: 'now',
                delivery: 'announce',
                pending_heartbeat: false,
                timezone: 'local',
              },
            ],
          },
          next_scheduled_action: {
            id: 'sched-1',
            name: 'Morning follow-up',
            schedule_kind: 'cron',
            next_run_at: '2026-05-06T09:00:00Z',
            wake_mode: 'next-heartbeat',
            delivery: 'announce',
            pending_heartbeat: true,
            timezone: 'local',
          },
          wake_queue: {
            pending_count: 1,
            claimed_count: 0,
            pending: [],
            claimed: [],
          },
          lane_queue: {
            running: true,
            accepting_new_work: true,
            draining: false,
            pending_count: 2,
            active_count: 1,
            max_total_concurrency: 4,
            lanes: {},
          },
          queue_overview: {
            queued_count: 1,
            running_now_count: 1,
            blocked_on_approval_count: 1,
            done_count: 1,
            quiet_hours: {
              active: true,
              label: 'Quiet hours active until 07:00',
              next_allowed_at: '2026-05-06T07:00:00Z',
            },
            lanes: {
              now: [
                {
                  id: 'run-1',
                  label: 'Morning review',
                  lane: 'cron',
                  status: 'running',
                  status_label: 'Running now',
                  summary: 'Reviewing the day plan now.',
                  run_id: 'run-1',
                },
              ],
              waiting: [
                {
                  id: 'queued-1',
                  label: 'Follow-up queue',
                  lane: 'cron',
                  status: 'queued',
                  status_label: 'Waiting',
                  summary: 'Follow up on the morning plan.',
                },
              ],
              scheduled: [
                {
                  id: 'sched-2',
                  label: 'Evening recap',
                  lane: 'cron',
                  status: 'scheduled',
                  status_label: 'Scheduled',
                  summary: 'cron · now · announce',
                  scheduled_for: '2026-05-06T18:00:00Z',
                },
              ],
              needs_ok: [
                {
                  id: 'approval-1',
                  label: 'Need confirmation before sending',
                  lane: 'cron',
                  status: 'waiting_approval',
                  status_label: 'Needs your OK',
                  summary: 'Waiting for approval before messaging Alex.',
                },
              ],
              done: [
                {
                  id: 'done-1',
                  label: 'Finished recap',
                  lane: 'main',
                  status: 'completed',
                  status_label: 'Done',
                  summary: 'Inbox triage summary delivered.',
                },
              ],
            },
          },
          policy: {
            plan_tier: 'pro',
            require_network_online: false,
            minimum_battery_percent: 20,
            max_self_proposed_per_hour: 2,
          },
        }),
      });
    });

    await loginAsOwner(page);
    await page.goto('/w/ws-1/tasks');

    await expect(page).toHaveURL(/\/w\/ws-1\/tasks$/);
    await expect(page.getByText(/Tasks is where Sage shows heartbeat, reminders, recurring responsibilities, quiet hours, scheduled jobs, and governed work lanes/i)).toBeVisible();
    await expect(page.getByText(/task lanes/i)).toBeVisible();
    await expect(page.getByText(/Now, Waiting, Scheduled, Needs your OK, and Done show what Sage is doing now and what happens later/i)).toBeVisible();
    await expect(page.getByText(/Reminders and recurring responsibilities/i)).toBeVisible();
    await expect(page.locator('.app-surface-stat').filter({ hasText: 'Running now' })).toContainText('1');
    await expect(page.locator('.app-surface-stat').filter({ hasText: 'Waiting' })).toContainText('1');
    await expect(page.locator('.app-surface-stat').filter({ hasText: 'Quiet hours' })).toContainText('Active');
    await expect(page.getByText(/quiet hours active until 07:00/i).first()).toBeVisible();
    await expect(page.getByText(/^Now$/i).first()).toBeVisible();
    await expect(page.getByText(/^Waiting$/i).first()).toBeVisible();
    await expect(page.getByText(/^Scheduled$/i).first()).toBeVisible();
    await expect(page.getByText(/^Needs your OK$/i).first()).toBeVisible();
    await expect(page.getByText(/^Done$/i).first()).toBeVisible();
    await expect(page.getByText(/Need confirmation before sending/i)).toBeVisible();
    await expect(page.getByText(/Inbox triage summary delivered/i)).toBeVisible();
  });

});
