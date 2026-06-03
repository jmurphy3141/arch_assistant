import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../App';

function renderApp() {
  return render(<App />);
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  localStorage.clear();
});

describe('App chat-only workspace', () => {
  it('renders chat as the only workspace and removes form navigation', async () => {
    renderApp();

    await waitFor(() => {
      expect(screen.getByTestId('health-indicator')).toHaveTextContent('1.9.1');
    });

    expect(screen.getByTestId('chat-input')).toHaveAttribute(
      'placeholder',
      'Describe your customer or ask Archie for an artifact...',
    );
    expect(screen.queryByTestId('sidebar-nav-generate')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-bom')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-notes')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-pov')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-jep')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-terraform')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-waf')).not.toBeInTheDocument();
  });

  it('shows engagement memory when a customer is selected', async () => {
    renderApp();

    await userEvent.type(screen.getByTestId('chat-customer-id'), 'acme-discovery');

    await waitFor(() => {
      expect(screen.getByTestId('engagement-memory-panel')).toBeInTheDocument();
      expect(screen.getByTestId('memory-customer-name')).toHaveTextContent('ACME Financial Services');
    });

    expect(screen.getByTestId('memory-services')).toHaveTextContent('Autonomous Database');
    expect(screen.getByTestId('memory-artifacts')).toHaveTextContent('v2.drawio');
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
});
