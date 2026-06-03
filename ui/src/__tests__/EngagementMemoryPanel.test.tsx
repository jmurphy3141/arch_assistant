import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EngagementMemoryPanel } from '../components/EngagementMemoryPanel';
import { server } from './handlers';

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('EngagementMemoryPanel', () => {
  it('renders "No context yet" when API returns empty context', async () => {
    server.use(
      http.get('/api/context/:customerId', ({ params }) => HttpResponse.json({
        status: 'ok',
        customer_id: params.customerId,
        context: {},
      })),
    );

    render(<EngagementMemoryPanel customerId="empty" />);

    await waitFor(() => {
      expect(screen.getByTestId('memory-empty')).toHaveTextContent('No context yet - start a conversation');
    });
  });

  it('renders customer_name in the header when present', async () => {
    server.use(
      http.get('/api/context/:customerId', () => HttpResponse.json({
        status: 'ok',
        customer_id: 'acme',
        customer_name: 'ACME Financial Services',
      })),
    );

    render(<EngagementMemoryPanel customerId="acme" />);

    await waitFor(() => {
      expect(screen.getByTestId('memory-customer-name')).toHaveTextContent('ACME Financial Services');
    });
  });

  it('renders artifact links with correct href', async () => {
    server.use(
      http.get('/api/context/:customerId', () => HttpResponse.json({
        status: 'ok',
        customer_id: 'acme',
        context: {
          artifacts: ['diagram/acme/v2.drawio', 'waf/acme/v1.md'],
        },
      })),
    );

    render(<EngagementMemoryPanel customerId="acme" />);

    await waitFor(() => {
      expect(screen.getByTestId('memory-artifacts')).toBeInTheDocument();
    });

    const diagram = screen.getByRole('link', { name: /v2\.drawio/ });
    expect(diagram).toHaveAttribute('href', '/download?key=diagram%2Facme%2Fv2.drawio');
    expect(diagram).toHaveAttribute('target', '_blank');
  });

  it('truncates customer_challenge at 80 chars with ellipsis', async () => {
    const challenge = 'A'.repeat(90);
    server.use(
      http.get('/api/context/:customerId', () => HttpResponse.json({
        status: 'ok',
        customer_id: 'acme',
        customer_challenge: challenge,
      })),
    );

    render(<EngagementMemoryPanel customerId="acme" />);

    await waitFor(() => {
      expect(screen.getByTestId('memory-challenge')).toHaveTextContent(`${'A'.repeat(80)}…`);
    });
  });
});
