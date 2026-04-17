/**
 * TerraformForm component tests
 *
 * T1: Initial render state
 * T2: Button disabled states
 * T3: Successful generate — file tabs + content
 * T4: Load Latest flow
 * T5: Error handling
 */
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, it, expect, vi } from 'vitest';
import { TerraformForm } from '../components/TerraformForm';
import {
  server,
  TERRAFORM_GENERATE_RESPONSE,
  TERRAFORM_LATEST_RESPONSE,
  TERRAFORM_FILES,
} from './handlers';

function renderForm(customerId = '') {
  const onCustomerIdChange = vi.fn();
  const { rerender } = render(
    <TerraformForm customerId={customerId} onCustomerIdChange={onCustomerIdChange} />,
  );
  return { onCustomerIdChange, rerender };
}

// ---------------------------------------------------------------------------
// T1: Initial render
// ---------------------------------------------------------------------------
describe('T1: TerraformForm initial render', () => {
  it('shows heading "Terraform Code Generator"', () => {
    renderForm();
    expect(screen.getByText('Terraform Code Generator')).toBeInTheDocument();
  });

  it('shows Customer ID and Customer Name inputs', () => {
    renderForm();
    expect(screen.getByPlaceholderText(/jane_street/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Jane Street Capital/)).toBeInTheDocument();
  });

  it('shows Generate and Load Latest buttons', () => {
    renderForm();
    expect(screen.getByText('Generate / Update Terraform')).toBeInTheDocument();
    expect(screen.getByText('Load Latest')).toBeInTheDocument();
  });

  it('does not show file tabs on initial render', () => {
    renderForm();
    expect(screen.queryByText('main.tf')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// T2: Button disabled states
// ---------------------------------------------------------------------------
describe('T2: Button disabled states', () => {
  it('Generate button disabled when customerId is empty', () => {
    renderForm('');
    expect(screen.getByText('Generate / Update Terraform')).toBeDisabled();
  });

  it('Generate button disabled when customerName is empty (customerId provided)', () => {
    renderForm('acme_corp');
    // Customer name is empty by default
    expect(screen.getByText('Generate / Update Terraform')).toBeDisabled();
  });

  it('Load Latest disabled when customerId is empty', () => {
    renderForm('');
    expect(screen.getByText('Load Latest')).toBeDisabled();
  });

  it('Load Latest enabled when customerId is provided', () => {
    renderForm('acme_corp');
    expect(screen.getByText('Load Latest')).not.toBeDisabled();
  });

  it('Generate button enabled after both fields filled', async () => {
    renderForm('acme_corp');
    const nameInput = screen.getByPlaceholderText(/Jane Street Capital/);
    await userEvent.type(nameInput, 'Acme Corp');
    expect(screen.getByText('Generate / Update Terraform')).not.toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// T3: Successful generate — file tabs + content
// ---------------------------------------------------------------------------
describe('T3: Generate success', () => {
  async function generateAndWait() {
    renderForm('acme_corp');
    const nameInput = screen.getByPlaceholderText(/Jane Street Capital/);
    await userEvent.type(nameInput, 'Acme Corp');
    fireEvent.click(screen.getByText('Generate / Update Terraform'));
    await waitFor(() => {
      expect(screen.getByText('main.tf')).toBeInTheDocument();
    });
  }

  it('shows all four file tabs after generate', async () => {
    await generateAndWait();
    expect(screen.getByText('main.tf')).toBeInTheDocument();
    expect(screen.getByText('variables.tf')).toBeInTheDocument();
    expect(screen.getByText('outputs.tf')).toBeInTheDocument();
    expect(screen.getByText('terraform.tfvars.example')).toBeInTheDocument();
  });

  it('shows version number after generate', async () => {
    await generateAndWait();
    expect(screen.getByText(/Version 1/)).toBeInTheDocument();
  });

  it('displays main.tf content by default', async () => {
    await generateAndWait();
    const pre = document.querySelector('pre');
    expect(pre?.textContent).toContain('oci_core_vcn');
  });

  it('switches to variables.tf content when tab clicked', async () => {
    await generateAndWait();
    fireEvent.click(screen.getByText('variables.tf'));
    const pre = document.querySelector('pre');
    expect(pre?.textContent).toContain('tenancy_ocid');
  });

  it('switches to outputs.tf content when tab clicked', async () => {
    await generateAndWait();
    fireEvent.click(screen.getByText('outputs.tf'));
    const pre = document.querySelector('pre');
    expect(pre?.textContent).toContain('vcn_id');
  });

  it('shows Download All button after generate', async () => {
    await generateAndWait();
    expect(screen.getByText('Download All')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// T4: Load Latest flow
// ---------------------------------------------------------------------------
describe('T4: Load Latest', () => {
  it('displays files after Load Latest succeeds', async () => {
    renderForm('acme_corp');
    fireEvent.click(screen.getByText('Load Latest'));
    await waitFor(() => {
      expect(screen.getByText('main.tf')).toBeInTheDocument();
    });
    const pre = document.querySelector('pre');
    expect(pre?.textContent).toContain('oci_core_vcn');
  });

  it('shows "Generate one first" error on 404', async () => {
    server.use(
      http.get('/api/terraform/:customerId/latest', () =>
        HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
      ),
    );
    renderForm('unknown_customer');
    fireEvent.click(screen.getByText('Load Latest'));
    await waitFor(() => {
      expect(screen.getByText(/Generate one first/)).toBeInTheDocument();
    });
  });

  it('shows generic error message on 500', async () => {
    server.use(
      http.get('/api/terraform/:customerId/latest', () =>
        HttpResponse.json({ detail: 'Internal error' }, { status: 500 }),
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
// T5: Error handling
// ---------------------------------------------------------------------------
describe('T5: Generate error handling', () => {
  it('shows error message when generate fails', async () => {
    server.use(
      http.post('/api/terraform/generate', () =>
        HttpResponse.json({ detail: 'LLM timeout' }, { status: 500 }),
      ),
    );
    renderForm('acme_corp');
    const nameInput = screen.getByPlaceholderText(/Jane Street Capital/);
    await userEvent.type(nameInput, 'Acme Corp');
    fireEvent.click(screen.getByText('Generate / Update Terraform'));
    await waitFor(() => {
      expect(screen.getByText(/Generation failed/)).toBeInTheDocument();
    });
  });

  it('error message contains detail text from server', async () => {
    server.use(
      http.post('/api/terraform/generate', () =>
        HttpResponse.json({ detail: 'Quota exceeded' }, { status: 429 }),
      ),
    );
    renderForm('acme_corp');
    const nameInput = screen.getByPlaceholderText(/Jane Street Capital/);
    await userEvent.type(nameInput, 'Acme Corp');
    fireEvent.click(screen.getByText('Generate / Update Terraform'));
    await waitFor(() => {
      expect(screen.getByText(/Quota exceeded/)).toBeInTheDocument();
    });
  });

  it('does not show file tabs when generate fails', async () => {
    server.use(
      http.post('/api/terraform/generate', () =>
        HttpResponse.json({ detail: 'Error' }, { status: 500 }),
      ),
    );
    renderForm('acme_corp');
    const nameInput = screen.getByPlaceholderText(/Jane Street Capital/);
    await userEvent.type(nameInput, 'Acme Corp');
    fireEvent.click(screen.getByText('Generate / Update Terraform'));
    await waitFor(() => {
      expect(screen.getByText(/Generation failed/)).toBeInTheDocument();
    });
    expect(screen.queryByText('main.tf')).toBeNull();
  });
});
