import React, { useState } from 'react';
import {
  apiGeneratePov, apiGetLatestPov, apiListPovVersions,
  type DocResponse, type DocVersionEntry,
} from '../api/client';
import { DocViewer } from './DocViewer';

interface Props {
  customerId: string;
  onCustomerIdChange: (id: string) => void;
}

export function PovForm({ customerId, onCustomerIdChange }: Props) {
  const [customerName, setCustomerName] = useState('');
  const [result, setResult] = useState<DocResponse | null>(null);
  const [versions, setVersions] = useState<DocVersionEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId.trim() || !customerName.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGeneratePov(customerId.trim(), customerName.trim());
      setResult(resp);
      const vResp = await apiListPovVersions(customerId.trim());
      setVersions(vResp.versions);
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
      const resp = await apiGetLatestPov(customerId.trim());
      const vResp = await apiListPovVersions(customerId.trim());
      // Cast to DocResponse shape for viewer
      setResult({
        status: 'ok',
        agent_version: '',
        customer_id: customerId,
        doc_type: 'pov',
        version: vResp.versions.length > 0 ? vResp.versions[vResp.versions.length - 1].version : 1,
        key: '',
        latest_key: '',
        content: resp.content,
        errors: [],
      });
      setVersions(vResp.versions);
    } catch (err: unknown) {
      const e2 = err as { status?: number; detail?: string };
      if (e2.status === 404) {
        setError(`No POV found for customer "${customerId}". Generate one first.`);
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

  return (
    <div>
      <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Point of View (POV)</h2>
      <p style={{ fontSize: '0.85rem', color: '#555', marginBottom: '1rem' }}>
        Generate or update an internal Oracle POV document for a customer.
        All notes previously uploaded for this customer will be used as context.
        Each generation creates a new versioned copy in the bucket.
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
          Customer Name * (used in document headings)
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
            {loading ? 'Generating…' : 'Generate / Update POV'}
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

      {result && result.content && (
        <DocViewer
          content={result.content}
          docType="pov"
          version={result.version}
          versionHistory={versions}
          onClose={() => setResult(null)}
        />
      )}
    </div>
  );
}
