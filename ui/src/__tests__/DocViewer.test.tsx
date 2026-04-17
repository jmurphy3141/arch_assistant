/**
 * DocViewer component tests
 *
 * D1: Label and heading per docType
 * D2: Formatted / raw toggle
 * D3: Version history table
 * D4: Close callback
 * D5: Markdown rendering
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DocViewer } from '../components/DocViewer';
import type { DocVersionEntry } from '../api/client';

const VERSIONS: DocVersionEntry[] = [
  { version: 1, key: 'doc/v1.md', timestamp: '2024-01-01T00:00:00Z', metadata: {} },
  { version: 2, key: 'doc/v2.md', timestamp: '2024-02-15T12:00:00Z', metadata: {} },
];

const CONTENT = `# Main Title

## Section One

A paragraph with **bold** and *italic* text.

- Bullet one
- Bullet two

---

> A blockquote line
`;

function renderViewer(overrides: Partial<React.ComponentProps<typeof DocViewer>> = {}) {
  return render(
    <DocViewer
      content={CONTENT}
      docType="pov"
      version={1}
      {...overrides}
    />,
  );
}

// ---------------------------------------------------------------------------
// D1: Label and heading
// ---------------------------------------------------------------------------
describe('D1: DocViewer label per docType', () => {
  it('shows "Point of View" for pov', () => {
    renderViewer({ docType: 'pov' });
    expect(screen.getByText(/Point of View/)).toBeInTheDocument();
  });

  it('shows "Joint Execution Plan" for jep', () => {
    renderViewer({ docType: 'jep' });
    expect(screen.getByText(/Joint Execution Plan/)).toBeInTheDocument();
  });

  it('shows "WAF Review" for waf', () => {
    renderViewer({ docType: 'waf' });
    expect(screen.getByText(/WAF Review/)).toBeInTheDocument();
  });

  it('includes version number in heading', () => {
    renderViewer({ version: 3 });
    expect(screen.getByText(/Version 3/)).toBeInTheDocument();
  });

  it('renders a Download .md button', () => {
    renderViewer();
    expect(screen.getByText('Download .md')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// D2: Formatted / raw toggle
// ---------------------------------------------------------------------------
describe('D2: Formatted / raw toggle', () => {
  it('shows "Raw Markdown" toggle button in default view', () => {
    renderViewer();
    expect(screen.getByText('Raw Markdown')).toBeInTheDocument();
  });

  it('switches to raw view on toggle click and shows pre element', () => {
    renderViewer();
    fireEvent.click(screen.getByText('Raw Markdown'));
    // After toggle the button label flips
    expect(screen.getByText('Formatted')).toBeInTheDocument();
    // Content should be in a <pre>
    const pre = document.querySelector('pre');
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('Main Title');
  });

  it('switches back to formatted view on second toggle', () => {
    renderViewer();
    fireEvent.click(screen.getByText('Raw Markdown'));
    fireEvent.click(screen.getByText('Formatted'));
    expect(screen.getByText('Raw Markdown')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// D3: Version history
// ---------------------------------------------------------------------------
describe('D3: Version history table', () => {
  it('shows version history when more than one version provided', () => {
    renderViewer({ versionHistory: VERSIONS });
    expect(screen.getByText(/Version history/)).toBeInTheDocument();
  });

  it('version history is hidden when only one version', () => {
    renderViewer({ versionHistory: [VERSIONS[0]] });
    expect(screen.queryByText(/Version history/)).toBeNull();
  });

  it('version history is hidden when not provided', () => {
    renderViewer();
    expect(screen.queryByText(/Version history/)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// D4: Close button
// ---------------------------------------------------------------------------
describe('D4: Close callback', () => {
  it('calls onClose when close button clicked', () => {
    const onClose = vi.fn();
    renderViewer({ onClose });
    fireEvent.click(screen.getByText('✕ Close'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('does not render close button when onClose not provided', () => {
    renderViewer({ onClose: undefined });
    expect(screen.queryByText('✕ Close')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// D5: Markdown rendering
// ---------------------------------------------------------------------------
describe('D5: Markdown rendering', () => {
  it('renders h1 heading', () => {
    renderViewer();
    expect(screen.getByRole('heading', { level: 1, name: /Main Title/ })).toBeInTheDocument();
  });

  it('renders h2 heading', () => {
    renderViewer();
    expect(screen.getByRole('heading', { level: 2, name: /Section One/ })).toBeInTheDocument();
  });

  it('renders bold text as <strong>', () => {
    renderViewer();
    // The heading also uses <strong>; find the one whose full text is just 'bold'
    const strongs = Array.from(document.querySelectorAll('strong'));
    const boldEl = strongs.find(el => el.textContent === 'bold');
    expect(boldEl).not.toBeUndefined();
  });

  it('renders italic text as <em>', () => {
    renderViewer();
    const em = document.querySelector('em');
    expect(em).not.toBeNull();
    expect(em!.textContent).toBe('italic');
  });

  it('renders bullet list items as <li>', () => {
    renderViewer();
    const items = document.querySelectorAll('li');
    expect(items.length).toBeGreaterThanOrEqual(2);
  });

  it('renders blockquote', () => {
    renderViewer();
    const bq = document.querySelector('blockquote');
    expect(bq).not.toBeNull();
    expect(bq!.textContent).toContain('blockquote');
  });

  it('renders horizontal rule', () => {
    renderViewer();
    expect(document.querySelector('hr')).not.toBeNull();
  });
});
