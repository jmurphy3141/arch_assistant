import React, { useState } from 'react';
import { HealthIndicator } from './components/HealthIndicator';
import { UploadBom } from './components/UploadBom';
import { GenerateForm } from './components/GenerateForm';
import { ResponseDisplay } from './components/ResponseDisplay';
import { ClarifyForm } from './components/ClarifyForm';
import { NoteUpload } from './components/NoteUpload';
import { PovForm } from './components/PovForm';
import { JepForm } from './components/JepForm';
import { TerraformForm } from './components/TerraformForm';
import { WafForm } from './components/WafForm';
import { useClientId, getLastDiagramName, saveLastDiagramName } from './hooks/useClientId';
import { apiClarify, apiRefineDiagram, type GenerateResponse, type OrchestrationResult } from './api/client';

type Mode = 'upload' | 'generate' | 'notes' | 'pov' | 'jep' | 'terraform' | 'waf';

function getLastCustomerId(): string {
  try { return localStorage.getItem('last_customer_id') ?? ''; } catch { return ''; }
}
function saveLastCustomerId(id: string) {
  try { localStorage.setItem('last_customer_id', id); } catch { /* ignore */ }
}

export function App() {
  const clientId = useClientId();
  const [mode, setMode] = useState<Mode>('upload');
  const [diagramName, setDiagramName] = useState<string>(getLastDiagramName);
  const [customerId, setCustomerId] = useState<string>(getLastCustomerId);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [orchestrationResult, setOrchestrationResult] = useState<OrchestrationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clarifyLoading, setClarifyLoading] = useState(false);
  const [refineLoading, setRefineLoading] = useState(false);

  function handleDiagramNameChange(name: string) {
    setDiagramName(name);
    saveLastDiagramName(name);
  }

  function handleCustomerIdChange(id: string) {
    setCustomerId(id);
    saveLastCustomerId(id);
  }

  function handleResult(r: GenerateResponse | OrchestrationResult) {
    if (r.status === 'orchestration_complete') {
      const orch = r as OrchestrationResult;
      setOrchestrationResult(orch);
      setResult(orch.draw_result);
    } else {
      setOrchestrationResult(null);
      setResult(r as GenerateResponse);
    }
    setError(null);
  }

  function handleError(msg: string) {
    setError(msg);
    setResult(null);
  }

  async function handleClarify(
    answers: string,
    opts?: { auto_waf?: boolean; customer_id?: string; customer_name?: string },
  ) {
    setClarifyLoading(true);
    try {
      // Prefer stateless path: echo _clarify_context back so the server
      // doesn't need PENDING_CLARIFY (survives restarts, no client_id mismatch).
      const ctx = result?._clarify_context as {
        items_json?: string; prompt?: string; deployment_hints_json?: string;
      } | undefined;
      const r = await apiClarify({
        answers,
        client_id:    customerId || clientId,
        diagram_name: diagramName,
        ...(ctx?.items_json && ctx?.prompt ? {
          items_json:            ctx.items_json,
          prompt:                ctx.prompt,
          deployment_hints_json: ctx.deployment_hints_json,
        } : {}),
        ...(opts?.auto_waf    ? { auto_waf:      opts.auto_waf    } : {}),
        ...(opts?.customer_id ? { customer_id:   opts.customer_id } : {}),
        ...(opts?.customer_name ? { customer_name: opts.customer_name } : {}),
      });
      handleResult(r);
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Clarify error ${e.status}: ${e.detail}`);
    } finally {
      setClarifyLoading(false);
    }
  }

  async function handleRefine(feedback: string) {
    const ctx = result?._refine_context as {
      items_json?: string; prompt?: string; prev_spec?: string; deployment_hints_json?: string;
    } | undefined;
    if (!ctx?.items_json || !ctx?.prompt) {
      setError('Refine context missing — please regenerate the diagram first.');
      return;
    }
    setRefineLoading(true);
    try {
      const r = await apiRefineDiagram({
        feedback,
        client_id:    customerId || clientId,
        diagram_name: diagramName,
        items_json:   ctx.items_json,
        prompt:       ctx.prompt,
        ...(ctx.prev_spec              ? { prev_spec:              ctx.prev_spec              } : {}),
        ...(ctx.deployment_hints_json  ? { deployment_hints_json:  ctx.deployment_hints_json  } : {}),
      });
      setResult(r);
      setError(null);
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Refine error ${e.status}: ${e.detail}`);
    } finally {
      setRefineLoading(false);
    }
  }

  function switchMode(m: Mode) {
    setMode(m);
    if (m === 'upload' || m === 'generate') {
      setResult(null);
      setOrchestrationResult(null);
      setError(null);
    }
  }

  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '0.3rem 0.75rem',
    border: active ? '1px solid #e8571a' : '1px solid #1c2030',
    background: active ? 'rgba(232,87,26,0.15)' : '#0e1016',
    color: active ? '#e8571a' : '#cdd2e0',
    cursor: active ? 'default' : 'pointer',
    fontWeight: active ? 700 : 400,
    borderRadius: 4,
    fontSize: '0.75rem',
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.04em',
    transition: 'all 0.15s',
  });

  return (
    <div style={{ maxWidth: '960px', margin: '0 auto', padding: '1.25rem', fontFamily: "'JetBrains Mono', monospace", background: '#08090d', minHeight: '100vh', color: '#cdd2e0' }}>
      <header style={{ borderBottom: '1px solid #1c2030', paddingBottom: '0.75rem', marginBottom: '1.25rem' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem' }}>
          <h1 style={{ margin: 0, fontFamily: "'Syne', sans-serif", fontSize: '1.4rem', fontWeight: 800, letterSpacing: '-0.03em', color: '#fff' }}>
            OCI<span style={{ color: '#e8571a' }}>.</span>Agent Fleet
            <small style={{ fontWeight: 400, fontSize: '0.68rem', color: '#454d64', marginLeft: '0.75rem', letterSpacing: '0.12em', textTransform: 'uppercase', fontFamily: "'JetBrains Mono', monospace" }}>
              Drawing · POV · JEP · Terraform · WAF
            </small>
          </h1>
          <HealthIndicator />
        </div>
      </header>

      {/* Tab bar */}
      <div style={{ marginBottom: '1.25rem', display: 'flex', gap: '0.3rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: '0.62rem', color: '#454d64', marginRight: '0.2rem', letterSpacing: '0.1em', textTransform: 'uppercase' }}>Diagrams:</span>
        <button style={btnStyle(mode === 'upload')}   onClick={() => switchMode('upload')}>Upload BOM</button>
        <button style={btnStyle(mode === 'generate')} onClick={() => switchMode('generate')}>Generate</button>
        <span style={{ fontSize: '0.62rem', color: '#454d64', margin: '0 0.2rem 0 0.75rem', letterSpacing: '0.1em', textTransform: 'uppercase' }}>Documents:</span>
        <button style={btnStyle(mode === 'notes')}     onClick={() => switchMode('notes')}>Notes</button>
        <button style={btnStyle(mode === 'pov')}       onClick={() => switchMode('pov')}>POV</button>
        <button style={btnStyle(mode === 'jep')}       onClick={() => switchMode('jep')}>JEP</button>
        <button style={btnStyle(mode === 'terraform')} onClick={() => switchMode('terraform')}>Terraform</button>
        <button style={btnStyle(mode === 'waf')}       onClick={() => switchMode('waf')}>WAF Review</button>
      </div>

      {/* Diagram modes */}
      {mode === 'upload' && (
        <UploadBom
          clientId={clientId}
          customerId={customerId}
          diagramName={diagramName}
          onCustomerIdChange={handleCustomerIdChange}
          onDiagramNameChange={handleDiagramNameChange}
          onResult={handleResult}
          onError={handleError}
        />
      )}

      {mode === 'generate' && (
        <GenerateForm
          clientId={clientId}
          diagramName={diagramName}
          onDiagramNameChange={handleDiagramNameChange}
          onResult={handleResult}
          onError={handleError}
        />
      )}

      {(mode === 'upload' || mode === 'generate') && (
        <>
          {error && (
            <div
              data-testid="error-display"
              style={{
                marginTop: '1rem', padding: '0.75rem',
                background: 'rgba(232,65,90,0.08)',
                border: '1px solid rgba(232,65,90,0.4)',
                borderRadius: 4,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                fontSize: '0.8rem', color: '#e8415a',
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              <strong>Error:</strong> {error}
            </div>
          )}
          {result && result.status === 'ok' && (
            <ResponseDisplay
              result={result}
              orchestrationResult={orchestrationResult ?? undefined}
              onRefine={handleRefine}
              refineLoading={refineLoading}
            />
          )}
          {result && result.status === 'need_clarification' && (
            <ClarifyForm result={result} onSubmit={handleClarify} loading={clarifyLoading} />
          )}
        </>
      )}

      {/* Document modes */}
      {mode === 'notes' && (
        <NoteUpload customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
      )}

      {mode === 'pov' && (
        <PovForm customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
      )}

      {mode === 'jep' && (
        <JepForm customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
      )}

      {mode === 'terraform' && (
        <TerraformForm customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
      )}

      {mode === 'waf' && (
        <WafForm customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
      )}

      <footer style={{ marginTop: '2rem', fontSize: '0.65rem', color: '#454d64', borderTop: '1px solid #1c2030', paddingTop: '0.5rem' }}>
        client_id: <code data-testid="client-id-display" style={{ color: '#e8571a' }}>{clientId}</code>
      </footer>
    </div>
  );
}
