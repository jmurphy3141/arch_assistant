import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { App } from '../App';
import { server, HEALTH_RESPONSE } from './handlers';

function renderApp() {
  return render(<App />);
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  localStorage.clear();
});

describe('App chat workspace', () => {
  it('shows health, chat input, and memory panel without form navigation', async () => {
    renderApp();

    await waitFor(() => {
      expect(screen.getByTestId('health-indicator')).toHaveTextContent(HEALTH_RESPONSE.agent_version);
    });

    expect(screen.getByTestId('chat-input')).toHaveAttribute(
      'placeholder',
      'Describe your customer or ask Archie for an artifact...',
    );
    expect(screen.getByTestId('engagement-memory-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-generate')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-bom')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-pov')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-jep')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-terraform')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-waf')).not.toBeInTheDocument();
  });

  it('client_id is stable across renders', () => {
    const { unmount } = renderApp();
    const id1 = screen.getByTestId('client-id-display').textContent ?? '';
    expect(id1).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
    unmount();

    render(<App />);
    const id2 = screen.getByTestId('client-id-display').textContent ?? '';
    expect(id2).toBe(id1);
  });

  it('filters chats when a project is selected and opens an engagement', async () => {
    renderApp();

    await waitFor(() => {
      expect(screen.getByTestId('chat-sidebar-project-acme-corp')).toBeInTheDocument();
      expect(screen.getByTestId('chat-sidebar-item-acme-discovery')).toBeInTheDocument();
      expect(screen.getByTestId('chat-sidebar-item-globex-review')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('chat-sidebar-project-globex'));

    await waitFor(() => {
      expect(screen.getByTestId('chat-sidebar-item-globex-review')).toBeInTheDocument();
      expect(screen.queryByTestId('chat-sidebar-item-acme-discovery')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('chat-sidebar-item-globex-review'));

    await waitFor(() => {
      expect(screen.getByTestId('chat-customer-id')).toHaveValue('globex-review');
    });
  });

  it('renders selected customer context in the memory panel', async () => {
    server.use(
      http.get('/api/context/acme-discovery', () => HttpResponse.json({
        status: 'ok',
        customer_id: 'acme-discovery',
        context: {
          customer_id: 'acme-discovery',
          customer_name: 'ACME Financial Services',
          customer_challenge: 'Reduce Oracle RAC licensing cost',
          oci_services_in_scope: ['Autonomous Database', 'OKE'],
          agents: {
            diagram: { diagram_key: 'diagram/acme/v2.drawio' },
          },
        },
      })),
    );

    renderApp();

    await waitFor(() => {
      expect(screen.getByTestId('chat-sidebar-item-acme-discovery')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('chat-sidebar-item-acme-discovery'));

    await waitFor(() => {
      expect(screen.getByText('ACME Financial Services')).toBeInTheDocument();
      expect(screen.getByText('Autonomous Database')).toBeInTheDocument();
      expect(screen.getByText(/v2.drawio/)).toBeInTheDocument();
    });
  });
});
