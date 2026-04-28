import { expect, test } from '@playwright/test';

test('BOM tab supports refresh and chat flow', async ({ page }) => {
  await page.route('**/health', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        agent_version: '1.7.0',
        agent: 'oci-drawing-agent',
        pending_clarifications: [],
        idempotency_cache_size: 0,
      }),
    });
  });

  await page.route('**/api/chat/history**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', items: [], pagination: { page: 1, page_size: 100, total: 0, has_next: false } }),
    });
  });

  await page.route('**/api/bom/health', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ready: false, source: 'none', refreshed_at: null, pricing_sku_count: 0, trace_id: 'trace-health' }),
    });
  });

  await page.route('**/api/bom/config', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        default_model_id: 'bom-model',
        cache: { ready: false, source: 'none', refreshed_at: null, pricing_sku_count: 0 },
        allowed_types: ['normal', 'question', 'final'],
      }),
    });
  });

  await page.route('**/api/bom/refresh-data', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ready: true, source: 'fallback', pricing_sku_count: 9, latency_ms: 10, refreshed_at: 1710000000, trace_id: 'trace-refresh' }),
    });
  });

  await page.route('**/api/bom/chat', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        type: 'final',
        reply: 'Final BOM prepared. Review line items, then export JSON or XLSX.',
        trace_id: 'trace-chat',
        score: 1,
        bom_payload: {
          currency: 'USD',
          line_items: [
            { sku: 'B94176', description: 'Compute E4 OCPU', category: 'compute', quantity: 8, unit_price: 0.05, extended_price: 0.4, notes: 'compute' },
            { sku: 'B94177', description: 'Compute E4 Memory GB', category: 'compute', quantity: 128, unit_price: 0.01, extended_price: 1.28, notes: 'memory' },
          ],
          assumptions: ['estimate'],
          totals: { estimated_monthly_cost: 1.68 },
        },
      }),
    });
  });

  await page.goto('/');
  await page.getByTestId('sidebar-nav-bom').click();
  await page.getByRole('button', { name: 'Refresh BOM Data' }).click();
  await page.getByTestId('bom-input').fill('Generate BOM for 8 OCPU and 128 GB RAM');
  await page.getByTestId('bom-send').click();

  await expect(page.getByText('Final BOM (Editable)')).toBeVisible();
  await expect(page.getByText(/trace_id: trace-chat/)).toBeVisible();
  await expect(page.getByText(/Estimated monthly total/i)).toBeVisible();
});
