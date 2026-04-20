import { expect, test, type Page } from '@playwright/test';

test.describe('UI Smoke Flows', () => {
  async function mockHealth(page: Page) {
    await page.route('**/health', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          agent_version: '1.5.0',
          agent: 'oci-drawing-agent',
          pending_clarifications: [],
          idempotency_cache_size: 0,
        }),
      });
    });
  }

  async function mockChatHistoryIndex(page: Page) {
    await page.route('**/api/chat/history**', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          items: [
            {
              customer_id: 'acme',
              customer_name: 'ACME Corp',
              last_message_preview: 'Generate Terraform from latest architecture notes',
              last_activity_timestamp: '2026-04-16T19:35:00Z',
              status: 'Completed with Terraform',
            },
            {
              customer_id: 'globex',
              customer_name: 'Globex',
              last_message_preview: 'Need clarification before Terraform generation',
              last_activity_timestamp: '2026-04-15T15:20:00Z',
              status: 'Terraform Needs Input',
            },
          ],
          pagination: {
            page: 1,
            page_size: 100,
            total: 2,
            has_next: false,
          },
        }),
      });
    });
  }

  test('chat sends message and renders artifact links without errors', async ({ page }) => {
    await mockHealth(page);
    await mockChatHistoryIndex(page);

    await page.route('**/api/chat/**/history**', async route => {
      const url = new URL(route.request().url());
      const match = url.pathname.match(/\/api\/chat\/([^/]+)\/history$/);
      if (!match) {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          customer_id: 'acme',
          history: [],
        }),
      });
    });

    await page.route('**/api/chat', async route => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          trace_id: 'trace-e2e',
          reply: 'Terraform artifacts are ready.',
          tool_calls: [],
          artifacts: {},
          artifact_manifest: {
            downloads: [
              {
                type: 'terraform',
                tool: 'generate_terraform',
                filename: 'main.tf',
                download_url: '/api/terraform/acme/download/main.tf',
              },
              {
                type: 'diagram',
                tool: 'generate_diagram',
                download_url: '/api/download/diagram.drawio',
              },
            ],
          },
          history_length: 2,
        }),
      });
    });

    await page.route('**/api/terraform/acme/download/main.tf', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: 'resource "oci_core_vcn" "main" {}',
      });
    });

    await page.goto('/');

    const historyResp = page.waitForResponse(r => r.url().includes('/api/chat/acme/history') && r.request().method() === 'GET');
    await page.getByTestId('chat-customer-id').fill('acme');
    await page.getByTestId('chat-customer-name').fill('ACME Corp');
    await historyResp;
    await page.getByTestId('chat-input').fill('Generate terraform');
    const chatResp = page.waitForResponse(r => r.url().includes('/api/chat') && r.request().method() === 'POST');
    await page.getByTestId('chat-send-button').click();
    await chatResp;

    await expect(page.getByTestId('chat-assistant-message').last()).toContainText('Terraform artifacts are ready.');
    await expect(page.getByTestId('artifact-link-terraform-main.tf')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('artifact-link-diagram-artifact')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('artifact-preview-panel')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('artifact-preview-item-0')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('artifact-preview-download-link')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('chat-error-banner')).toHaveCount(0);
  });

  test('terraform tab generates and loads downloadable files', async ({ page }) => {
    await mockHealth(page);
    await mockChatHistoryIndex(page);

    await page.route('**/api/terraform/generate', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          customer_id: 'acme',
          customer_name: 'ACME Corp',
          version: 1,
          files: ['main.tf', 'providers.tf'],
        }),
      });
    });

    await page.route('**/api/terraform/acme/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          customer_id: 'acme',
          latest: {
            version: 1,
            files: {
              'main.tf': 'terraform/acme/v1/main.tf',
              'providers.tf': 'terraform/acme/v1/providers.tf',
            },
          },
        }),
      });
    });

    await page.route('**/api/terraform/acme/versions', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          customer_id: 'acme',
          versions: [
            {
              version: 1,
              key: 'terraform/acme/v1/manifest.json',
              timestamp: '2026-04-17T00:00:00Z',
              metadata: {},
            },
          ],
        }),
      });
    });

    await page.route('**/api/terraform/acme/download/main.tf', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: 'resource "oci_core_vcn" "main" {}',
      });
    });

    await page.route('**/api/terraform/acme/download/providers.tf', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: 'terraform { required_version = ">= 1.6.0" }',
      });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Terraform' }).first().click();
    await page.getByPlaceholder('e.g. jane_street').fill('acme');
    await page.getByPlaceholder('e.g. Jane Street Capital').fill('ACME Corp');
    await page.getByRole('button', { name: 'Generate / Update Terraform' }).click();

    await expect(page.getByText('Version 1 - 2 files')).toBeVisible();
    await expect(page.getByRole('button', { name: 'main.tf' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'providers.tf' })).toBeVisible();
    await page.getByRole('button', { name: 'main.tf' }).click();
    await expect(page.locator('pre').last()).toContainText('oci_core_vcn');
  });

  test('sidebar search and customer selection loads selected thread context', async ({ page }) => {
    await mockHealth(page);
    await mockChatHistoryIndex(page);

    await page.route('**/api/chat/**/history**', async route => {
      const url = new URL(route.request().url());
      const match = url.pathname.match(/\/api\/chat\/([^/]+)\/history$/);
      if (!match) {
        await route.fallback();
        return;
      }
      const customerId = decodeURIComponent(match[1]);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          customer_id: customerId,
          history: [],
        }),
      });
    });

    await page.goto('/');
    await expect(page.getByTestId('chat-sidebar-item-acme')).toBeVisible();
    await expect(page.getByTestId('chat-sidebar-item-globex')).toBeVisible();
    await expect(page.getByTestId('chat-sidebar-status-acme')).toContainText('Completed with Terraform');
    await expect(page.getByTestId('chat-sidebar-status-globex')).toContainText('Terraform Needs Input');

    await page.getByTestId('chat-sidebar-search').fill('glob');
    await expect(page.getByTestId('chat-sidebar-item-globex')).toBeVisible();
    await expect(page.getByTestId('chat-sidebar-item-acme')).toHaveCount(0);

    const globexHistoryResp = page.waitForResponse(
      r => r.url().includes('/api/chat/globex/history') && r.request().method() === 'GET',
    );
    await page.getByTestId('chat-sidebar-item-globex').click();
    await globexHistoryResp;

    await expect(page.getByTestId('chat-customer-id')).toHaveValue('globex');
    await expect(page.getByTestId('chat-customer-name')).toHaveValue('Globex');
  });
});
