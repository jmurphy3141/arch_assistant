/**
 * WafForm component tests
 *
 * W1: Initial render state
 * W2: Button disabled states
 * W3: Successful generate — rating badge + DocViewer
 * W4: Load Latest flow
 * W5: Error handling
 * W6: Rating badge colors
 */
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, it, expect, vi } from 'vitest';
import { WafForm } from '../components/WafForm';
import { server, WAF_GENERATE_RESPONSE, WAF_CONTENT } from './handlers';

function renderForm(customerId = '') {
  const onCustomerIdChange = vi.fn();
  render(<WafForm customerId={customerId} onCustomerIdChange={onCustomerIdChange} />);
  return { onCustomerIdChange };
}

// ---------------------------------------------------------------------------
// W1: Initial render
// ---------------------------------------------------------------------------
describe('W1: WafForm initial render', () => {
  it('shows "WAF Review" heading', () => {
    renderForm();
    expect(screen.getByText('WAF Review')).toBeInTheDocument();
  });

  it('shows Customer ID and Customer Name inputs', () => {
    renderForm();
    expect(screen.getByPlaceholderText(/jane_street/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Jane Street Capital/)).toBeInTheDocument();
  });

  it('shows Generate and Load Latest buttons', () => {
    renderForm();
    expect(screen.getByText('Generate / Update WAF Review')).toBeInTheDocument();
    expect(screen.getByText('Load Latest')).toBeInTheDocument();
  });

  it('does not show rating badge on initial render', () => {
    renderForm();
    expect(screen.queryByText(/Overall Rating/)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// W2: Button disabled states
// ---------------------------------------------------------------------------
describe('W2: Button disabled states', () => {
  it('Generate disabled when customerId empty', () => {
    renderForm('');
    expect(screen.getByText('Generate / Update WAF Review')).toBeDisabled();
  });

  it('Generate disabled when customerName empty', () => {
    renderForm('acme_corp');
    expect(screen.getByText('Generate / Update WAF Review')).toBeDisabled();
  });

  it('Load Latest disabled when customerId empty', () => {
    renderForm('');
    expect(screen.getByText('Load Latest')).toBeDisabled();
  });

  it('Load Latest enabled when customerId provided', () => {
    renderForm('acme_corp');
    expect(screen.getByText('Load Latest')).not.toBeDisabled();
  });

  it('Generate enabled after both fields filled', async () => {
    renderForm('acme_corp');
    await userEvent.type(screen.getByPlaceholderText(/Jane Street Capital/), 'Acme Corp');
    expect(screen.getByText('Generate / Update WAF Review')).not.toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// W3: Successful generate
// ---------------------------------------------------------------------------
describe('W3: Generate success', () => {
  async function generateAndWait() {
    renderForm('acme_corp');
    await userEvent.type(screen.getByPlaceholderText(/Jane Street Capital/), 'Acme Corp');
    fireEvent.click(screen.getByText('Generate / Update WAF Review'));
    // Wait for DocViewer to appear — its heading is unique
    await waitFor(() => {
      expect(screen.getByText(/WAF Review — Version/)).toBeInTheDocument();
    });
  }

  it('shows Overall Rating badge after generate', async () => {
    await generateAndWait();
    // The badge <strong> has exact text "Overall Rating:" (with colon)
    // The markdown renders "Overall Rating: ✅" as a single <strong> — different text
    expect(screen.getByText('Overall Rating:')).toBeInTheDocument();
  });

  it('shows the ✅ emoji in the rating badge', async () => {
    await generateAndWait();
    // Badge renders: <strong>Overall Rating:</strong> <span>✅</span>
    // Markdown renders: <strong>Overall Rating: ✅</strong> — different full text
    // So getByText('✅') uniquely finds the badge <span>
    expect(screen.getByText('✅')).toBeInTheDocument();
  });

  it('shows DocViewer with WAF content after generate', async () => {
    await generateAndWait();
    expect(screen.getByText(/WAF Review — Version/)).toBeInTheDocument();
  });

  it('shows WAF pillars content in the document', async () => {
    await generateAndWait();
    // "Operational Excellence" appears in both the form description and the rendered doc
    expect(screen.getAllByText(/Operational Excellence/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Security/).length).toBeGreaterThan(0);
  });

  it('DocViewer close button hides the review', async () => {
    await generateAndWait();
    fireEvent.click(screen.getByText('✕ Close'));
    expect(screen.queryByText(/WAF Review — Version/)).toBeNull();
    expect(screen.queryByText('Overall Rating:')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// W4: Load Latest
// ---------------------------------------------------------------------------
describe('W4: Load Latest', () => {
  it('displays WAF content after Load Latest succeeds', async () => {
    renderForm('acme_corp');
    fireEvent.click(screen.getByText('Load Latest'));
    await waitFor(() => {
      expect(screen.getByText(/WAF Review — Version/)).toBeInTheDocument();
    });
  });

  it('shows "Generate one first" error on 404', async () => {
    server.use(
      http.get('/api/waf/:customerId/latest', () =>
        HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
      ),
    );
    renderForm('unknown_customer');
    fireEvent.click(screen.getByText('Load Latest'));
    await waitFor(() => {
      expect(screen.getByText(/Generate one first/)).toBeInTheDocument();
    });
  });

  it('shows generic load error on 500', async () => {
    server.use(
      http.get('/api/waf/:customerId/latest', () =>
        HttpResponse.json({ detail: 'Server error' }, { status: 500 }),
      ),
    );
    renderForm('acme_corp');
    fireEvent.click(screen.getByText('Load Latest'));
    await waitFor(() => {
      expect(screen.getByText(/Load failed/)).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// W5: Generate error handling
// ---------------------------------------------------------------------------
describe('W5: Generate error handling', () => {
  it('shows error when generate fails', async () => {
    server.use(
      http.post('/api/waf/generate', () =>
        HttpResponse.json({ detail: 'Model overloaded' }, { status: 503 }),
      ),
    );
    renderForm('acme_corp');
    await userEvent.type(screen.getByPlaceholderText(/Jane Street Capital/), 'Acme Corp');
    fireEvent.click(screen.getByText('Generate / Update WAF Review'));
    await waitFor(() => {
      expect(screen.getByText(/Generation failed/)).toBeInTheDocument();
    });
  });

  it('does not show DocViewer after error', async () => {
    server.use(
      http.post('/api/waf/generate', () =>
        HttpResponse.json({ detail: 'Error' }, { status: 500 }),
      ),
    );
    renderForm('acme_corp');
    await userEvent.type(screen.getByPlaceholderText(/Jane Street Capital/), 'Acme Corp');
    fireEvent.click(screen.getByText('Generate / Update WAF Review'));
    await waitFor(() => {
      expect(screen.getByText(/Generation failed/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/WAF Review — Version/)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// W6: Rating badge colors
// ---------------------------------------------------------------------------
describe('W6: Rating badge color coding', () => {
  async function generateWithRating(rating: string) {
    server.use(
      http.post('/api/waf/generate', () =>
        HttpResponse.json({ ...WAF_GENERATE_RESPONSE, overall_rating: rating }),
    ),
    );
    renderForm('acme_corp');
    await userEvent.type(screen.getByPlaceholderText(/Jane Street Capital/), 'Acme Corp');
    fireEvent.click(screen.getByText('Generate / Update WAF Review'));
    await waitFor(() => {
      expect(screen.getByText(/WAF Review — Version/)).toBeInTheDocument();
    });
  }

  it('✅ rating badge has green color', async () => {
    await generateWithRating('✅');
    // Badge: <span style={{ color: '#006600' }}>✅</span>
    const ratingSpan = screen.getByText('✅');
    expect(ratingSpan).toHaveStyle({ color: '#006600' });
  });

  it('⚠️ rating badge has amber color', async () => {
    await generateWithRating('⚠️');
    const ratingSpan = screen.getByText('⚠️');
    expect(ratingSpan).toHaveStyle({ color: '#885500' });
  });

  it('❌ rating badge has red color', async () => {
    await generateWithRating('❌');
    const ratingSpan = screen.getByText('❌');
    expect(ratingSpan).toHaveStyle({ color: '#cc0000' });
  });
});
