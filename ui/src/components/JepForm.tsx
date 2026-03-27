import React, { useState } from 'react';
import {
  apiGenerateJep, apiGetLatestJep, apiListJepVersions,
  type DocResponse, type DocVersionEntry,
} from '../api/client';
import { DocViewer } from './DocViewer';

interface Props {
  customerId: string;
  onCustomerIdChange: (id: string) => void;
}

export function JepForm({ customerId, onCustomerIdChange }: Props) {
  const [customerName, setCustomerName] = useState('');
  const [diagramKey, setDiagramKey] = useState('');
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
      const resp = await apiGenerateJep(
        customerId.trim(),
        customerName.trim(),
        diagramKey.trim() || undefined,
      );
      setResult(resp);
      const vResp = await apiListJepVersions(customerId.trim());
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
      const resp = await apiGetLatestJep(customerId.trim());
      const vResp = await apiListJepVersions(customerId.trim());
      setResult({
        status: 'ok',
        agent_version: '',
        customer_id: customerId,
        doc_type: 'jep',
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
        setError(`No JEP found for customer "${customerId}". Generate one first.`);
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
      <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Joint Execution Plan (JEP)</h2>
      <p style={{ fontSize: '0.85rem', color: '#555', marginBottom: '1rem' }}>
        Generate a JEP for a POC engagement. The agent reads all uploaded notes,
        auto-generates a Bill of Materials from the notes, references the latest
        architecture diagram (if available in the bucket), and drafts the full JEP.
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

        <label style={{ display: 'block', fontWeight: 'bold', fontSize: '0.85rem', marginBottom: '0.25rem' }}>
          Diagram key (optional — OCI bucket key of the architecture diagram)
        </label>
        <input
          style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem' }}
          value={diagramKey}
          onChange={e => setDiagramKey(e.target.value)}
          placeholder="e.g. agent3/jane_street/poc_arch/LATEST.json (leave blank to auto-detect)"
        />

        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem' }}>
          <button type="submit" disabled={loading || !customerId.trim() || !customerName.trim()}>
            {loading ? 'Generating…' : 'Generate / Update JEP'}
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
          {result.bom && (result.bom.hardware as unknown[])?.length > 0 && (
            <div style={{ marginTop: '0.75rem', padding: '0.5rem', background: '#f0f4ff', border: '1px solid #aac', borderRadius: '4px', fontSize: '0.82rem' }}>
              <strong>BOM auto-generated</strong> — {(result.bom.hardware as unknown[]).length} hardware items,{' '}
              {(result.bom.software as unknown[])?.length ?? 0} software items.
              {' '}Duration: {result.bom.duration_days as number} days.
              {result.diagram_key && (
                <span style={{ marginLeft: '0.5rem', color: '#555' }}>
                  Diagram: <code style={{ fontSize: '0.8rem' }}>{result.diagram_key}</code>
                </span>
              )}
            </div>
          )}
          <DocViewer
            content={result.content}
            docType="jep"
            version={result.version}
            versionHistory={versions}
            onClose={() => setResult(null)}
          />
        </>
      )}
    </div>
  );
}
