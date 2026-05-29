import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const clientMocks = vi.hoisted(() => ({
  apiGetCustomerContext: vi.fn(),
}));

vi.mock('../api/client', () => ({
  apiGetCustomerContext: clientMocks.apiGetCustomerContext,
}));

import { EngagementMemoryPanel } from '../components/EngagementMemoryPanel';

describe('EngagementMemoryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clientMocks.apiGetCustomerContext.mockResolvedValue({ status: 'ok', context: {} });
  });

  it('renders "No context yet" when API returns an empty object', async () => {
    render(<EngagementMemoryPanel customerId="acme" />);

    await waitFor(() => {
      expect(screen.getByText('No context yet — start a conversation')).toBeInTheDocument();
    });
  });

  it('renders customer_name in the header when present', async () => {
    clientMocks.apiGetCustomerContext.mockResolvedValue({
      status: 'ok',
      context: { customer_name: 'ACME Financial Services' },
    });

    render(<EngagementMemoryPanel customerId="acme" />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'ACME Financial Services' })).toBeInTheDocument();
    });
  });

  it('renders artifact links with download hrefs', async () => {
    clientMocks.apiGetCustomerContext.mockResolvedValue({
      status: 'ok',
      context: {
        customer_name: 'ACME',
        artifacts: ['diagram/acme/v2.drawio'],
      },
    });

    render(<EngagementMemoryPanel customerId="acme" />);

    await waitFor(() => {
      const link = screen.getByTestId('memory-artifact-link');
      expect(link).toHaveTextContent('v2.drawio ↗');
      expect(link).toHaveAttribute('href', '/download?key=diagram%2Facme%2Fv2.drawio');
      expect(link).toHaveAttribute('target', '_blank');
    });
  });

  it('truncates customer_challenge at 80 characters with ellipsis', async () => {
    const challenge = 'A'.repeat(90);
    clientMocks.apiGetCustomerContext.mockResolvedValue({
      status: 'ok',
      context: {
        customer_name: 'ACME',
        customer_challenge: challenge,
      },
    });

    render(<EngagementMemoryPanel customerId="acme" />);

    await waitFor(() => {
      expect(screen.getByTestId('memory-challenge')).toHaveTextContent(`${'A'.repeat(80)}…`);
    });
  });
});
