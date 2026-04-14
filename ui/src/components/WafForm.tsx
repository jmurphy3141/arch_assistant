import React, { useState } from 'react';
import {
  apiGenerateWaf, apiGetLatestWaf, apiListWafVersions,
  type DocVersionEntry,
} from '../api/client';
import { DocViewer } from './DocViewer';

interface Props {
  customerId: string;
  onCustomerIdChange: (id: string) => void;
}

interface WafResult {
  content: string;
  version: number;
  overall_rating?: string;
}

const RATING_COLOR: Record<string, string> = {
  '✅': '#006600',
  '⚠️': '#885500',
  '❌': '#cc0000',
};

export function WafForm({ customerId, onCustomerIdChange }: Props) {
  const [customerName, setCustomerName] = useState('');
  const [result, setResult] = useState<WafResult | null>(null);
  const [versions, setVersions] = useState<DocVersionEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId.trim() || !customerName.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGenerateWaf(customerId.trim(), customerName.trim());
      setResult({
        content: resp.content,
        version: resp.version,
        overall_rating: resp.overall_rating,
      });
      const vResp = await apiListWafVersions(customerId.trim());
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
      const resp = await apiGetLatestWaf(customerId.trim());
      const vResp = await apiListWafVersions(customerId.trim());
      setResult({
        content: resp.content,
        version: vResp.versions.length > 0
          ? vResp.versions[vResp.versions.length - 1].version
          : 1,
      });
      setVersions(vResp.versions);
    } catch (err: unknown) {
      const e2 = err as { status?: number; detail?: string };
      if (e2.status === 404) {
        setError(`No WAF review found for customer "${customerId}". Generate one first.`);
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

  const rating = result?.overall_rating;
  const ratingColor = rating ? (RATING_COLOR[rating] ?? '#333') : undefined;

  return (
    <div>
      <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>WAF Review</h2>
      <p style={{ fontSize: '0.85rem', color: '#555', marginBottom: '1rem' }}>
        Generate an OCI Well-Architected Framework review across the five pillars
        defined in the official OCI WAF document: Security and Compliance,
        Reliability and Resilience, Performance and Cost Optimization,
        Operational Efficiency, and Distributed Cloud. The agent reads the full
        engagement context (all prior agent outputs + notes) to produce a
        structured review.
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
            {loading ? 'Generating…' : 'Generate / Update WAF Review'}
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
        <>
          {rating && (
            <div style={{
              marginTop: '0.75rem',
              padding: '0.4rem 0.75rem',
              background: '#f8f8f8',
              border: '1px solid #ddd',
              borderRadius: '4px',
              fontSize: '0.85rem',
              display: 'inline-block',
            }}>
              <strong>Overall Rating:</strong>{' '}
              <span style={{ color: ratingColor, fontWeight: 'bold' }}>{rating}</span>
            </div>
          )}
          <DocViewer
            content={result.content}
            docType="waf"
            version={result.version}
            versionHistory={versions}
            onClose={() => setResult(null)}
          />
        </>
      )}
    </div>
  );
}
