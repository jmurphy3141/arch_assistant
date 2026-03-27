import React, { useState } from 'react';
import {
  apiGenerateTerraform, apiGetLatestTerraform, apiListTerraformVersions,
  type TerraformLatestResponse, type TerraformVersionEntry,
} from '../api/client';

interface Props {
  customerId: string;
  onCustomerIdChange: (id: string) => void;
}

const FILE_ORDER = ['main.tf', 'variables.tf', 'outputs.tf', 'terraform.tfvars.example'];

export function TerraformForm({ customerId, onCustomerIdChange }: Props) {
  const [customerName, setCustomerName] = useState('');
  const [result, setResult] = useState<TerraformLatestResponse | null>(null);
  const [versions, setVersions] = useState<TerraformVersionEntry[]>([]);
  const [activeFile, setActiveFile] = useState<string>('main.tf');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId.trim() || !customerName.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGenerateTerraform(customerId.trim(), customerName.trim());
      // Convert generate response to latest-response shape for display
      setResult({
        status: resp.status,
        customer_id: resp.customer_id,
        doc_type: 'terraform',
        version: resp.version,
        prefix_key: resp.prefix_key,
        files: resp.files as Record<string, string>,
      });
      const vResp = await apiListTerraformVersions(customerId.trim());
      setVersions(vResp.versions);
      // Show first available file
      const available = FILE_ORDER.find(f => resp.files[f]) ?? Object.keys(resp.files)[0];
      if (available) setActiveFile(available);
    } catch (err: unknown) {
      const e2 = err as { detail?: string };
      setError(`Generation failed: ${e2.detail ?? String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleLoadLatest() {
    if (!customerId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGetLatestTerraform(customerId.trim());
      setResult(resp);
      const vResp = await apiListTerraformVersions(customerId.trim());
      setVersions(vResp.versions);
      const available = FILE_ORDER.find(f => resp.files[f]) ?? Object.keys(resp.files)[0];
      if (available) setActiveFile(available);
    } catch (err: unknown) {
      const e2 = err as { status?: number; detail?: string };
      if (e2.status === 404) {
        setError(`No Terraform found for customer "${customerId}". Generate one first.`);
      } else {
        setError(`Load failed: ${e2.detail ?? String(err)}`);
      }
    } finally {
      setLoading(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '0.4rem', boxSizing: 'border-box', marginBottom: '0.5rem',
  };

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: '0.25rem 0.6rem',
    border: active ? '2px solid #c00' : '1px solid #ccc',
    background: active ? '#fff0f0' : '#fafafa',
    cursor: active ? 'default' : 'pointer',
    fontFamily: 'monospace',
    fontSize: '0.78rem',
    borderRadius: '3px 3px 0 0',
    fontWeight: active ? 'bold' : 'normal',
  });

  const files = result?.files ?? {};
  const fileNames = FILE_ORDER.filter(f => files[f]).concat(
    Object.keys(files).filter(f => !FILE_ORDER.includes(f))
  );

  return (
    <div>
      <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Terraform Code Generator</h2>
      <p style={{ fontSize: '0.85rem', color: '#555', marginBottom: '1rem' }}>
        Generate OCI Terraform HCL for this customer's deployment. The agent reads uploaded
        notes, the architecture diagram spec (if available), and fetches oracle-quickstart
        examples to produce main.tf, variables.tf, outputs.tf, and terraform.tfvars.example.
      </p>

      <form onSubmit={handleGenerate}>
        <label style={{ display: 'block', fontWeight: 'bold', fontSize: '0.85rem', marginBottom: '0.25rem' }}>
          Customer ID *
        </label>
        <input
          style={inputStyle}
          value={customerId}
          onChange={e => onCustomerIdChange(e.target.value)}
          placeholder="e.g. jane_street"
          required
        />

        <label style={{ display: 'block', fontWeight: 'bold', fontSize: '0.85rem', marginBottom: '0.25rem' }}>
          Customer Name * (used in resource tags)
        </label>
        <input
          style={inputStyle}
          value={customerName}
          onChange={e => setCustomerName(e.target.value)}
          placeholder="e.g. Jane Street Capital"
          required
        />

        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem' }}>
          <button type="submit" disabled={loading || !customerId.trim() || !customerName.trim()}>
            {loading ? 'Generating…' : 'Generate / Update Terraform'}
          </button>
          <button
            type="button"
            onClick={handleLoadLatest}
            disabled={loading || !customerId.trim()}
          >
            Load Latest
          </button>
        </div>
      </form>

      {error && (
        <div style={{ marginTop: '0.75rem', padding: '0.5rem', background: '#fff0f0', border: '1px solid #c00', borderRadius: '4px', fontSize: '0.85rem' }}>
          {error}
        </div>
      )}

      {result && fileNames.length > 0 && (
        <div style={{ marginTop: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
            <span style={{ fontSize: '0.8rem', color: '#555' }}>
              Version {result.version} — {fileNames.length} files
              {versions.length > 0 && (
                <span style={{ marginLeft: '0.5rem', color: '#888' }}>
                  ({versions.length} version{versions.length !== 1 ? 's' : ''} total)
                </span>
              )}
            </span>
            <button
              style={{ fontSize: '0.75rem', padding: '0.2rem 0.5rem' }}
              onClick={() => {
                // Download all files as a combined text blob
                const combined = fileNames
                  .map(f => `# ${f}\n${files[f]}`)
                  .join('\n\n' + '='.repeat(60) + '\n\n');
                const blob = new Blob([combined], { type: 'text/plain' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${result.customer_id}_terraform_v${result.version}.tf`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              Download All
            </button>
          </div>

          {/* File tabs */}
          <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap', marginBottom: '-1px', position: 'relative', zIndex: 1 }}>
            {fileNames.map(fname => (
              <button
                key={fname}
                style={tabStyle(activeFile === fname)}
                onClick={() => setActiveFile(fname)}
              >
                {fname}
              </button>
            ))}
          </div>

          {/* File content */}
          <pre style={{
            background: '#1e1e1e',
            color: '#d4d4d4',
            padding: '0.75rem',
            borderRadius: '0 4px 4px 4px',
            overflowX: 'auto',
            fontSize: '0.78rem',
            lineHeight: '1.45',
            maxHeight: '500px',
            overflowY: 'auto',
            margin: 0,
          }}>
            {files[activeFile] ?? '(empty)'}
          </pre>
        </div>
      )}
    </div>
  );
}
