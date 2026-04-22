import React, { useState } from 'react';
import {
  apiGenerateJep, apiGetLatestJep, apiListJepVersions,
  apiApproveJep, apiGetApprovedJep,
  apiRequestJepRevision,
  apiJepKickoff, apiSaveJepAnswers, apiGetJepQuestions,
  type DocResponse, type DocVersionEntry, type KickoffQuestion, type JepState,
} from '../api/client';
import { DocViewer } from './DocViewer';

interface Props {
  customerId: string;
  onCustomerIdChange: (id: string) => void;
}

type Phase = 'idle' | 'kickoff_loading' | 'questions' | 'generating';

export function JepForm({ customerId, onCustomerIdChange }: Props) {
  const [customerName, setCustomerName]   = useState('');
  const [diagramKey, setDiagramKey]       = useState('');
  const [feedback, setFeedback]           = useState('');
  const [phase, setPhase]                 = useState<Phase>('idle');
  const [questions, setQuestions]         = useState<KickoffQuestion[]>([]);
  const [answers, setAnswers]             = useState<Record<string, string>>({});
  const [savingAnswers, setSavingAnswers]  = useState(false);
  const [result, setResult]               = useState<DocResponse | null>(null);
  const [versions, setVersions]           = useState<DocVersionEntry[]>([]);
  const [loading, setLoading]             = useState(false);
  const [approving, setApproving]         = useState(false);
  const [requestingRevision, setRequestingRevision] = useState(false);
  const [approvedExists, setApprovedExists] = useState<boolean | null>(null);
  const [jepState, setJepState]           = useState<JepState | null>(null);
  const [error, setError]                 = useState<string | null>(null);
  const [successMsg, setSuccessMsg]       = useState<string | null>(null);

  // ── Kickoff ────────────────────────────────────────────────────────────────

  async function handleKickoff() {
    if (!customerId.trim() || !customerName.trim()) return;
    setPhase('kickoff_loading');
    setError(null);
    setSuccessMsg(null);
    try {
      // First check if questions already exist
      try {
        const existing = await apiGetJepQuestions(customerId.trim());
        if (existing.questions && existing.questions.length > 0) {
          setQuestions(existing.questions);
          const prefilled: Record<string, string> = {};
          for (const q of existing.questions) {
            if (q.known_value) prefilled[q.id] = q.known_value;
          }
          if (existing.answers) Object.assign(prefilled, existing.answers);
          setAnswers(prefilled);
          setPhase('questions');
          return;
        }
      } catch {
        // no existing questions — run kickoff
      }
      const resp = await apiJepKickoff(customerId.trim(), customerName.trim());
      setQuestions(resp.questions);
      const prefilled: Record<string, string> = {};
      for (const q of resp.questions) {
        if (q.known_value) prefilled[q.id] = q.known_value;
      }
      setAnswers(prefilled);
      setPhase('questions');
    } catch (err: unknown) {
      const e2 = err as { detail?: string };
      setError(`Kickoff failed: ${e2.detail ?? String(err)}`);
      setPhase('idle');
    }
  }

  async function handleSaveAnswersAndGenerate() {
    if (!customerId.trim() || !customerName.trim()) return;
    setSavingAnswers(true);
    setError(null);
    try {
      await apiSaveJepAnswers(customerId.trim(), answers);
    } catch {
      // Non-fatal — proceed anyway
    } finally {
      setSavingAnswers(false);
    }
    await handleGenerate();
  }

  // ── Generate ───────────────────────────────────────────────────────────────

  async function handleGenerate() {
    setLoading(true);
    setPhase('generating');
    setError(null);
    setSuccessMsg(null);
    try {
      const resp = await apiGenerateJep(
        customerId.trim(),
        customerName.trim(),
        diagramKey.trim() || undefined,
        feedback.trim() || undefined,
      );
      setResult(resp);
      setJepState(resp.jep_state ?? null);
      setFeedback('');
      setPhase('idle');
      const vResp = await apiListJepVersions(customerId.trim());
      setVersions(vResp.versions);
      checkApproved(customerId.trim());
    } catch (err: unknown) {
      const e2 = err as { detail?: string; body?: unknown };
      const policy = e2.body as {
        status?: string;
        required_next_step?: string;
        reason_codes?: string[];
        jep_state?: JepState;
      } | undefined;
      if (policy?.status === 'policy_block') {
        setJepState(policy.jep_state ?? null);
        setError(
          `Generation blocked by policy (${(policy.reason_codes ?? []).join(', ') || 'JEP lock'}). ` +
          `Next step: ${policy.required_next_step ?? 'request_revision'}.`,
        );
      } else {
        setError(`Generation failed: ${e2.detail ?? String(err)}`);
      }
      setPhase('idle');
    } finally {
      setLoading(false);
    }
  }

  async function handleGenerateDirect(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId.trim() || !customerName.trim()) return;
    await handleGenerate();
  }

  async function handleLoadLatest() {
    if (!customerId.trim()) return;
    setLoading(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const [resp, vResp] = await Promise.all([
        apiGetLatestJep(customerId.trim()),
        apiListJepVersions(customerId.trim()),
      ]);
      setResult({
        status: 'ok', agent_version: '', customer_id: customerId, doc_type: 'jep',
        version: vResp.versions.length > 0 ? vResp.versions[vResp.versions.length - 1].version : 1,
        key: '', latest_key: '', content: resp.content, errors: [],
        jep_state: resp.jep_state,
      });
      setJepState(resp.jep_state ?? null);
      setVersions(vResp.versions);
      checkApproved(customerId.trim());
    } catch (err: unknown) {
      const e2 = err as { status?: number; detail?: string };
      if (e2.status === 404) setError(`No JEP found for customer "${customerId}". Generate one first.`);
      else setError(`Load failed: ${e2.detail ?? String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  async function checkApproved(cid: string) {
    try {
      const approved = await apiGetApprovedJep(cid);
      setApprovedExists(true);
      if (approved.jep_state) setJepState(approved.jep_state);
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
      const approved = await apiApproveJep(customerId.trim(), customerName.trim(), result.content) as { jep_state?: JepState };
      setApprovedExists(true);
      if (approved.jep_state) setJepState(approved.jep_state);
      setSuccessMsg('Approved version saved. Future generations will start from this version.');
    } catch (err: unknown) {
      const e2 = err as { detail?: string };
      setError(`Approve failed: ${e2.detail ?? String(err)}`);
    } finally {
      setApproving(false);
    }
  }

  async function handleRequestRevision() {
    if (!customerId.trim()) return;
    setRequestingRevision(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const resp = await apiRequestJepRevision(customerId.trim(), feedback.trim() || undefined);
      setJepState(resp.jep_state);
      setSuccessMsg('Revision requested. Generate is now re-enabled.');
    } catch (err: unknown) {
      const e2 = err as { detail?: string; body?: unknown };
      const policy = e2.body as { jep_state?: JepState; required_next_step?: string } | undefined;
      if (policy?.jep_state) setJepState(policy.jep_state);
      setError(`Revision request failed: ${policy?.required_next_step ?? e2.detail ?? String(err)}`);
    } finally {
      setRequestingRevision(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '0.4rem', boxSizing: 'border-box', marginBottom: '0.5rem',
  };

  const isLoading = loading || phase === 'kickoff_loading' || phase === 'generating';
  const isLocked = Boolean(jepState?.is_locked);

  return (
    <div>
      <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Joint Execution Plan (JEP)</h2>
      <p style={{ fontSize: '0.85rem', color: '#555', marginBottom: '1rem' }}>
        Generate a JEP for a POC engagement. Use <strong>Start JEP Kickoff</strong> to scan notes
        for POC signals and answer clarifying questions first, or go straight to generation.
        Provide feedback to correct mistakes — saved permanently.
      </p>

      <form onSubmit={handleGenerateDirect}>
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
          Diagram key <span style={{ fontWeight: 'normal', color: '#777' }}>(optional — auto-detects POC diagram)</span>
        </label>
        <input
          style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem' }}
          value={diagramKey}
          onChange={e => setDiagramKey(e.target.value)}
          placeholder="e.g. agent3/jane_street/poc/LATEST.json (leave blank to auto-detect)"
        />

        <label style={{ display: 'block', fontWeight: 'bold', fontSize: '0.85rem', marginBottom: '0.25rem' }}>
          Feedback / corrections <span style={{ fontWeight: 'normal', color: '#777' }}>(optional — saved permanently)</span>
        </label>
        <textarea
          style={{ ...inputStyle, resize: 'vertical', minHeight: 72, fontFamily: 'inherit' }}
          value={feedback}
          onChange={e => setFeedback(e.target.value)}
          placeholder="e.g. Duration should be 14 days. Add RDMA networking test case. Customer is in financial services."
          rows={3}
        />

        {approvedExists === true && (
          <div style={{ marginBottom: '0.5rem', padding: '0.4rem 0.6rem', background: '#f0fff4', border: '1px solid #4c7', borderRadius: 4, fontSize: '0.8rem', color: '#2a6' }}>
            Approved version exists — next generation will start from it.
          </div>
        )}
        {jepState && (
          <div style={{ marginBottom: '0.5rem', padding: '0.45rem 0.6rem', background: isLocked ? '#fff4f2' : '#f4f7ff', border: `1px solid ${isLocked ? '#e27' : '#99b'}`, borderRadius: 4, fontSize: '0.8rem' }}>
            Lifecycle: <strong>{jepState.state}</strong> · {isLocked ? 'locked' : 'unlocked'} · next: {jepState.required_next_step}
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem', flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={handleKickoff}
            disabled={isLoading || !customerId.trim() || !customerName.trim() || isLocked}
            style={{ background: '#f0f4ff', border: '1px solid #aac' }}
          >
            {phase === 'kickoff_loading' ? 'Scanning notes…' : 'Start JEP Kickoff'}
          </button>
          <button type="submit" disabled={isLoading || !customerId.trim() || !customerName.trim() || isLocked}>
            {phase === 'generating' ? 'Generating…' : feedback.trim() ? 'Generate with Feedback' : 'Generate / Update JEP'}
          </button>
          <button type="button" onClick={handleLoadLatest} disabled={isLoading || !customerId.trim()}>
            Load Latest
          </button>
          {isLocked && (
            <button
              type="button"
              onClick={handleRequestRevision}
              disabled={requestingRevision || !customerId.trim()}
              style={{ background: '#fff4e8', border: '1px solid #d9a66b' }}
            >
              {requestingRevision ? 'Requesting…' : 'Request Revision'}
            </button>
          )}
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

      {/* ── Kickoff Q&A ──────────────────────────────────────────────────── */}
      {phase === 'questions' && questions.length > 0 && (
        <div style={{ marginTop: '1rem', padding: '0.75rem', background: '#f8f9ff', border: '1px solid #aac', borderRadius: 6 }}>
          <div style={{ fontWeight: 'bold', fontSize: '0.9rem', marginBottom: '0.5rem' }}>
            POC Kickoff Questions
          </div>
          <p style={{ fontSize: '0.8rem', color: '#555', marginBottom: '0.75rem' }}>
            Pre-filled from notes where possible. Edit or complete before generating.
          </p>
          {questions.map(q => (
            <div key={q.id} style={{ marginBottom: '0.75rem' }}>
              <label style={{ display: 'block', fontSize: '0.82rem', fontWeight: 'bold', marginBottom: '0.2rem' }}>
                {q.question}
              </label>
              {q.hint && <div style={{ fontSize: '0.73rem', color: '#888', marginBottom: '0.2rem' }}>{q.hint}</div>}
              <input
                style={{ width: '100%', padding: '0.35rem', boxSizing: 'border-box', fontSize: '0.82rem' }}
                value={answers[q.id] ?? ''}
                onChange={e => setAnswers(prev => ({ ...prev, [q.id]: e.target.value }))}
                placeholder={q.known_value ?? ''}
              />
            </div>
          ))}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
            <button
              type="button"
              onClick={handleSaveAnswersAndGenerate}
              disabled={savingAnswers || loading}
              style={{ background: '#e8571a', color: '#fff', border: 'none', borderRadius: 4, padding: '8px 20px', fontWeight: 'bold' }}
            >
              {savingAnswers ? 'Saving…' : loading ? 'Generating…' : 'Save Answers & Generate JEP'}
            </button>
            <button type="button" onClick={() => setPhase('idle')} disabled={loading}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {jepState && (
        <div style={{ marginTop: '0.75rem', padding: '0.6rem', background: '#f8f9fb', border: '1px solid #ccd2de', borderRadius: 4, fontSize: '0.82rem' }}>
          <div style={{ fontWeight: 'bold', marginBottom: '0.35rem' }}>Missing required fields</div>
          {jepState.missing_fields.length === 0 ? (
            <div style={{ color: '#2a6' }}>None</div>
          ) : (
            <div style={{ color: '#b24' }}>{jepState.missing_fields.join(', ')}</div>
          )}
          <details style={{ marginTop: '0.55rem' }}>
            <summary style={{ cursor: 'pointer', fontWeight: 'bold' }}>Source context</summary>
            <pre style={{ marginTop: '0.5rem', padding: '0.5rem', background: '#fff', border: '1px solid #dde3ea', borderRadius: 4, whiteSpace: 'pre-wrap', fontSize: '0.75rem' }}>
{JSON.stringify(jepState.source_context, null, 2)}
            </pre>
          </details>
        </div>
      )}

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
        <>
          {result.bom && (result.bom.hardware as unknown[])?.length > 0 && (
            <div style={{ marginTop: '0.75rem', padding: '0.5rem', background: '#f0f4ff', border: '1px solid #aac', borderRadius: 4, fontSize: '0.82rem' }}>
              <strong>BOM auto-generated</strong> — {(result.bom.hardware as unknown[]).length} hardware items,{' '}
              {(result.bom.software as unknown[])?.length ?? 0} software items.{' '}
              Duration: {result.bom.duration_days as number} days.
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
