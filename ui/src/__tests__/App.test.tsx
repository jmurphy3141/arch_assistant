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

function openSidebarMode(mode: string) {
  fireEvent.click(screen.getByTestId(`sidebar-nav-${mode}`));
}

function submitGenerateForm() {
  const form = screen.getByTestId('generate-form');
  const submit = form.querySelector('button[type="submit"]');
  if (!(submit instanceof HTMLElement)) {
    throw new Error('Generate form submit button not found');
  }
  fireEvent.click(submit);
}

// Silence console.error for expected test errors
beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// C1: Generate success renders request_id/input_hash + correct download URLs
// ---------------------------------------------------------------------------
describe('C1: Generate success', () => {
  it('renders request_id and input_hash after generate', async () => {
    renderApp();

    // Switch to Generate mode
    openSidebarMode('generate');

    // Submit the form (default resources is [])
    submitGenerateForm();

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
    openSidebarMode('generate');
    submitGenerateForm();

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
    openSidebarMode('generate');
    submitGenerateForm();

    await waitFor(() => {
      expect(screen.getByTestId('clarify-form')).toBeInTheDocument();
    });

    expect(screen.getByText(/How many ADs per region/)).toBeInTheDocument();
  });

  it('submits answers and shows ok result after clarify', async () => {
    server.use(
      http.post('/api/generate', () => HttpResponse.json(CLARIFY_RESPONSE)),
      http.post('/api/clarify', () => HttpResponse.json({ status: 'pending', job_id: 'job-clarify-test' })),
      http.get('/api/job/:jobId', () => HttpResponse.json(GENERATE_OK_RESPONSE)),
    );

    renderApp();
    openSidebarMode('generate');
    submitGenerateForm();

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
    }, { timeout: 5000 });
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
    openSidebarMode('generate');
    submitGenerateForm();

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
    openSidebarMode('generate');
    submitGenerateForm();

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
    openSidebarMode('generate');

    // Should not throw
    expect(() => submitGenerateForm()).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// C4: Health polling displays agent_version
// ---------------------------------------------------------------------------
describe('C4: Health indicator', () => {
  it('shows agent_version from health response', async () => {
    renderApp();

    await waitFor(() => {
      expect(screen.getByTestId('health-indicator')).toHaveTextContent('1.9.1');
    });
  });

  it('shows error state when health check fails', async () => {
    server.use(
      http.get('/health', () =>
        HttpResponse.json({ detail: 'Server error' }, { status: 503 }),
      ),
    );

    renderApp();

    await waitFor(() => {
      const indicator = screen.getByTestId('health-indicator');
      expect(indicator).toHaveTextContent(/health error/i);
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
    openSidebarMode('generate');
    submitGenerateForm();

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

describe('C6: BOM tab', () => {
  it('renders BOM advisor and accepts a chat submission', async () => {
    renderApp();
    openSidebarMode('bom');

    await waitFor(() => {
      expect(screen.getByTestId('bom-input')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId('bom-input'), {
      target: { value: 'Generate BOM for 4 OCPU and 64 GB RAM' },
    });
    fireEvent.click(screen.getByTestId('bom-send'));

    await waitFor(() => {
      expect(screen.getByText(/BOM data is not ready/i)).toBeInTheDocument();
    });
  });
});

describe('C7: Project sidebar', () => {
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
});

describe('C8: Documents sidebar section', () => {
  it('is collapsed by default while conversations remain visible', async () => {
    renderApp();

    const toggle = screen.getByTestId('sidebar-documents-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('sidebar-nav-notes')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-pov')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-jep')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-terraform')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-waf')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId('chat-sidebar-project-acme-corp')).toBeInTheDocument();
      expect(screen.getByTestId('chat-sidebar-item-acme-discovery')).toBeInTheDocument();
    });
  });

  it('expands and collapses document navigation', () => {
    renderApp();

    const toggle = screen.getByTestId('sidebar-documents-toggle');
    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('sidebar-nav-notes')).toHaveTextContent('Notes');
    expect(screen.getByTestId('sidebar-nav-pov')).toHaveTextContent('POV');
    expect(screen.getByTestId('sidebar-nav-jep')).toHaveTextContent('JEP');
    expect(screen.getByTestId('sidebar-nav-terraform')).toHaveTextContent('Terraform');
    expect(screen.getByTestId('sidebar-nav-waf')).toHaveTextContent('WAF Review');

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('sidebar-nav-notes')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-pov')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-jep')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-terraform')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-nav-waf')).not.toBeInTheDocument();
  });

  it('switches workspace when a document mode is selected', () => {
    renderApp();

    fireEvent.click(screen.getByTestId('sidebar-documents-toggle'));
    fireEvent.click(screen.getByTestId('sidebar-nav-notes'));

    expect(screen.getByRole('heading', { name: 'Notes' })).toBeInTheDocument();
    expect(screen.getByTestId('sidebar-nav-notes')).toHaveAttribute('aria-current', 'page');
  });
});
