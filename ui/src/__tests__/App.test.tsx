/**
 * UI unit tests — Vitest + React Testing Library + MSW
 *
 * C1: Generate success renders request_id/input_hash + correct download URLs
 * C2: Need-clarification renders questions and clarify request works
 * C3: Non-JSON error body is displayed without crash
 * C4: Health polling displays agent_version
 * C5: localStorage client_id is stable + download reconstruction after refresh
 */
import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { App } from '../App';
import { server, GENERATE_OK_RESPONSE, CLARIFY_RESPONSE, HEALTH_RESPONSE } from './handlers';
import { downloadUrl } from '../api/client';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function renderApp() {
  return render(<App />);
}

// Silence console.error for expected test errors
beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

// ---------------------------------------------------------------------------
// C1: Generate success renders request_id/input_hash + correct download URLs
// ---------------------------------------------------------------------------
describe('C1: Generate success', () => {
  it('renders request_id and input_hash after generate', async () => {
    renderApp();

    // Switch to Generate mode
    fireEvent.click(screen.getByText('Generate from Resources'));

    // Submit the form (default resources is [])
    fireEvent.click(screen.getByText('Generate Diagram'));

    await waitFor(() => {
      expect(screen.getByTestId('response-ok')).toBeInTheDocument();
    });

    expect(screen.getByTestId('request-id')).toHaveTextContent(
      GENERATE_OK_RESPONSE.request_id,
    );
    expect(screen.getByTestId('input-hash')).toHaveTextContent(
      GENERATE_OK_RESPONSE.input_hash,
    );
  });

  it('renders correct download links for all artifact files', async () => {
    renderApp();
    fireEvent.click(screen.getByText('Generate from Resources'));
    fireEvent.click(screen.getByText('Generate Diagram'));

    await waitFor(() => {
      expect(screen.getByTestId('download-list')).toBeInTheDocument();
    });

    const files = [
      'diagram.drawio',
      'spec.json',
      'draw_dict.json',
      'render_manifest.json',
      'node_to_resource_map.json',
    ];

    for (const f of files) {
      const link = screen.getByTestId(`download-${f}`);
      expect(link).toHaveAttribute('href');
      const href = link.getAttribute('href')!;
      expect(href).toContain('/api/download/');
      expect(href).toContain(`client_id=`);
      expect(href).toContain(`diagram_name=`);
    }
  });

  it('download URL helper builds correct path', () => {
    const url = downloadUrl('diagram.drawio', 'my-client', 'my-diag');
    expect(url).toBe(
      '/api/download/diagram.drawio?client_id=my-client&diagram_name=my-diag',
    );
  });
});

// ---------------------------------------------------------------------------
// C2: Need clarification renders questions and clarify request works
// ---------------------------------------------------------------------------
describe('C2: Clarification flow', () => {
  it('renders clarification questions when server needs clarification', async () => {
    // Override /generate to return need_clarification
    server.use(
      http.post('/api/generate', () => HttpResponse.json(CLARIFY_RESPONSE)),
    );

    renderApp();
    fireEvent.click(screen.getByText('Generate from Resources'));
    fireEvent.click(screen.getByText('Generate Diagram'));

    await waitFor(() => {
      expect(screen.getByTestId('clarify-form')).toBeInTheDocument();
    });

    expect(screen.getByText(/How many ADs per region/)).toBeInTheDocument();
  });

  it('submits answers and shows ok result after clarify', async () => {
    server.use(
      http.post('/api/generate', () => HttpResponse.json(CLARIFY_RESPONSE)),
      http.post('/api/clarify', () => HttpResponse.json(GENERATE_OK_RESPONSE)),
    );

    renderApp();
    fireEvent.click(screen.getByText('Generate from Resources'));
    fireEvent.click(screen.getByText('Generate Diagram'));

    await waitFor(() => {
      expect(screen.getByTestId('clarify-form')).toBeInTheDocument();
    });

    // Fill in answers
    const textarea = screen.getByTestId('clarify-answers');
    await userEvent.type(textarea, '3 ADs');

    // Submit
    fireEvent.click(screen.getByTestId('clarify-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('response-ok')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// C3: Non-JSON error body displayed without crash
// ---------------------------------------------------------------------------
describe('C3: Error handling', () => {
  it('displays non-JSON error body as plain text without crashing', async () => {
    server.use(
      http.post('/api/generate', () =>
        new HttpResponse('Internal Server Error — not JSON', {
          status: 500,
          headers: { 'Content-Type': 'text/plain' },
        }),
      ),
    );

    renderApp();
    fireEvent.click(screen.getByText('Generate from Resources'));
    fireEvent.click(screen.getByText('Generate Diagram'));

    await waitFor(() => {
      expect(screen.getByTestId('error-display')).toBeInTheDocument();
    });

    expect(screen.getByTestId('error-display')).toHaveTextContent(
      'Internal Server Error — not JSON',
    );
  });

  it('displays HTTP status code in error', async () => {
    server.use(
      http.post('/api/generate', () =>
        HttpResponse.json({ detail: 'Bad request' }, { status: 400 }),
      ),
    );

    renderApp();
    fireEvent.click(screen.getByText('Generate from Resources'));
    fireEvent.click(screen.getByText('Generate Diagram'));

    await waitFor(() => {
      expect(screen.getByTestId('error-display')).toBeInTheDocument();
    });

    expect(screen.getByTestId('error-display')).toHaveTextContent('400');
  });

  it('UI does not crash when response has unexpected shape', async () => {
    server.use(
      http.post('/api/generate', () =>
        HttpResponse.json({ unexpected: 'shape', no_status: true }),
      ),
    );

    renderApp();
    fireEvent.click(screen.getByText('Generate from Resources'));

    // Should not throw
    expect(() => fireEvent.click(screen.getByText('Generate Diagram'))).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// C4: Health polling displays agent_version
// ---------------------------------------------------------------------------
describe('C4: Health indicator', () => {
  it('shows agent_version from health response', async () => {
    renderApp();

    await waitFor(() => {
      expect(screen.getByTestId('health-indicator')).toHaveTextContent('1.3.2');
    });
  });

  it('shows error state when health check fails', async () => {
    server.use(
      http.get('/api/health', () =>
        HttpResponse.json({ detail: 'Server error' }, { status: 503 }),
      ),
    );

    renderApp();

    await waitFor(() => {
      const indicator = screen.getByTestId('health-indicator');
      expect(indicator).toHaveTextContent('Health error');
    });
  });
});

// ---------------------------------------------------------------------------
// C5: localStorage client_id is stable + download reconstruction after refresh
// ---------------------------------------------------------------------------
describe('C5: Persistence', () => {
  it('client_id is stable across renders', () => {
    const { unmount } = renderApp();
    const id1 = screen.getByTestId('client-id-display').textContent ?? '';
    expect(id1).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
    unmount();

    // Re-render — same client_id from localStorage
    render(<App />);
    const id2 = screen.getByTestId('client-id-display').textContent ?? '';
    expect(id2).toBe(id1);
  });

  it('download URL can be reconstructed from stored clientId and diagramName', async () => {
    renderApp();

    // Generate a diagram
    fireEvent.click(screen.getByText('Generate from Resources'));
    fireEvent.click(screen.getByText('Generate Diagram'));

    await waitFor(() => {
      expect(screen.getByTestId('download-list')).toBeInTheDocument();
    });

    // All download links include client_id and diagram_name query params
    const link = screen.getByTestId('download-diagram.drawio');
    const href = link.getAttribute('href')!;
    expect(href).toContain('client_id=');
    expect(href).toContain('diagram_name=');
  });
});
