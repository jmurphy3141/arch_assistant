import React, { useState } from 'react';
import {
  apiGeneratePov, apiGetLatestPov, apiListPovVersions,
  apiApprovePov, apiGetApprovedPov,
  type DocResponse, type DocVersionEntry,
} from '../api/client';
import { DocViewer } from './DocViewer';

interface Props {
  customerId: string;
  onCustomerIdChange: (id: string) => void;
}

export function PovForm({ customerId, onCustomerIdChange }: Props) {
  const [customerName, setCustomerName] = useState('');
  const [feedback, setFeedback]         = useState('');
  const [result, setResult]             = useState<DocResponse | null>(null);
  const [versions, setVersions]         = useState<DocVersionEntry[]>([]);
  const [loading, setLoading]           = useState(false);
  const [approving, setApproving]       = useState(false);
  const [approvedExists, setApprovedExists] = useState<boolean | null>(null);
  const [error, setError]               = useState<string | null>(null);
  const [successMsg, setSuccessMsg]     = useState<string | null>(null);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId.trim() || !customerName.trim()) return;
    setLoading(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const resp = await apiGeneratePov(customerId.trim(), customerName.trim(), feedback.trim() || undefined);
      setResult(resp);
      setFeedback('');
      const vResp = await apiListPovVersions(customerId.trim());
      setVersions(vResp.versions);
      // Check whether an approved version now exists
      checkApproved(customerId.trim());
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
    setSuccessMsg(null);
    try {
      const [resp, vResp] = await Promise.all([
        apiGetLatestPov(customerId.trim()),
        apiListPovVersions(customerId.trim()),
      ]);
      setResult({
        status: 'ok', agent_version: '', customer_id: customerId, doc_type: 'pov',
        version: vResp.versions.length > 0 ? vResp.versions[vResp.versions.length - 1].version : 1,
        key: '', latest_key: '', content: resp.content, errors: [],
      });
      setVersions(vResp.versions);
      checkApproved(customerId.trim());
    } catch (err: unknown) {
      const e2 = err as { status?: number; detail?: string };
      if (e2.status === 404) setError(`No POV found for customer "${customerId}". Generate one first.`);
      else setError(`Load failed: ${e2.detail ?? String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  async function checkApproved(cid: string) {
    try {
      await apiGetApprovedPov(cid);
      setApprovedExists(true);
    } catch {
      setApprovedExists(false);
    }
  }

  async function handleApprove() {
    if (!result?.content || !customerId.trim() || !customerName.trim()) return;
    setApproving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      await apiApprovePov(customerId.trim(), customerName.trim(), result.content);
      setApprovedExists(true);
      setSuccessMsg('Approved version saved. Future generations will start from this version.');
    } catch (err: unknown) {
      const e2 = err as { detail?: string };
      setError(`Approve failed: ${e2.detail ?? String(err)}`);
    } finally {
      setApproving(false);
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
        All uploaded notes are used as context. Each generation creates a new versioned copy.
        Provide feedback to correct mistakes — it is saved permanently and applied to all future runs.
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
          Feedback / corrections <span style={{ fontWeight: 'normal', color: '#777' }}>(optional — saved permanently)</span>
        </label>
        <textarea
          style={{ ...inputStyle, resize: 'vertical', minHeight: 72, fontFamily: 'inherit' }}
          value={feedback}
          onChange={e => setFeedback(e.target.value)}
          placeholder="e.g. Change the industry to financial services. Add regulatory compliance section. Remove mention of hybrid cloud."
          rows={3}
        />

        {approvedExists === true && (
          <div style={{ marginBottom: '0.5rem', padding: '0.4rem 0.6rem', background: '#f0fff4', border: '1px solid #4c7', borderRadius: 4, fontSize: '0.8rem', color: '#2a6' }}>
            Approved version exists — next generation will start from it.
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem', flexWrap: 'wrap' }}>
          <button type="submit" disabled={loading || !customerId.trim() || !customerName.trim()}>
            {loading ? 'Generating…' : feedback.trim() ? 'Generate with Feedback' : 'Generate / Update POV'}
          </button>
          <button type="button" onClick={handleLoadLatest} disabled={loading || !customerId.trim()}>
            Load Latest
          </button>
          {result?.content && (
            <button
              type="button"
              onClick={handleApprove}
              disabled={approving || !customerName.trim()}
              style={{ background: '#e8f5e9', border: '1px solid #4c7', color: '#2a6' }}
            >
              {approving ? 'Saving…' : 'Approve This Version'}
            </button>
          )}
        </div>
      </form>

      {error && (
        <div style={{ marginTop: '0.75rem', padding: '0.5rem', background: '#fff0f0', border: '1px solid #c00', borderRadius: 4, fontSize: '0.85rem' }}>
          {error}
        </div>
      )}
      {successMsg && (
        <div style={{ marginTop: '0.75rem', padding: '0.5rem', background: '#f0fff4', border: '1px solid #4c7', borderRadius: 4, fontSize: '0.85rem', color: '#2a6' }}>
          {successMsg}
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
