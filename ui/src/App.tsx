import React, { useState } from 'react';
import { HealthIndicator } from './components/HealthIndicator';
import { UploadBom } from './components/UploadBom';
import { GenerateForm } from './components/GenerateForm';
import { ResponseDisplay } from './components/ResponseDisplay';
import { ClarifyForm } from './components/ClarifyForm';
import { NoteUpload } from './components/NoteUpload';
import { PovForm } from './components/PovForm';
import { JepForm } from './components/JepForm';
import { useClientId, getLastDiagramName, saveLastDiagramName } from './hooks/useClientId';
import { apiClarify, type GenerateResponse } from './api/client';

type Mode = 'upload' | 'generate' | 'notes' | 'pov' | 'jep';

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
  const [error, setError] = useState<string | null>(null);
  const [clarifyLoading, setClarifyLoading] = useState(false);

  function handleDiagramNameChange(name: string) {
    setDiagramName(name);
    saveLastDiagramName(name);
  }

  function handleCustomerIdChange(id: string) {
    setCustomerId(id);
    saveLastCustomerId(id);
  }

  function handleResult(r: GenerateResponse) {
    setResult(r);
    setError(null);
  }

  function handleError(msg: string) {
    setError(msg);
    setResult(null);
  }

  async function handleClarify(answers: string) {
    setClarifyLoading(true);
    try {
      const r = await apiClarify({
        answers,
        client_id: clientId,
        diagram_name: diagramName,
      });
      setResult(r);
      setError(null);
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Clarify error ${e.status}: ${e.detail}`);
    } finally {
      setClarifyLoading(false);
    }
  }

  function switchMode(m: Mode) {
    setMode(m);
    if (m === 'upload' || m === 'generate') {
      setResult(null);
      setError(null);
    }
  }

  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '0.3rem 0.75rem',
    border: active ? '2px solid #c00' : '1px solid #ccc',
    background: active ? '#fff0f0' : '#fff',
    cursor: active ? 'default' : 'pointer',
    fontWeight: active ? 'bold' : 'normal',
    borderRadius: '3px',
    fontSize: '0.85rem',
  });

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '1rem', fontFamily: 'sans-serif' }}>
      <header style={{ borderBottom: '2px solid #e00', paddingBottom: '0.5rem', marginBottom: '1rem' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem' }}>
          OCI Agent Fleet{' '}
          <small style={{ fontWeight: 'normal', fontSize: '0.82rem', color: '#666' }}>
            Drawing · POV · JEP
          </small>
        </h1>
        <HealthIndicator />
      </header>

      {/* Tab bar */}
      <div style={{ marginBottom: '1.25rem', display: 'flex', gap: '0.3rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: '0.78rem', color: '#888', marginRight: '0.1rem' }}>Diagrams:</span>
        <button style={btnStyle(mode === 'upload')}   onClick={() => switchMode('upload')}>Upload BOM</button>
        <button style={btnStyle(mode === 'generate')} onClick={() => switchMode('generate')}>Generate</button>
        <span style={{ fontSize: '0.78rem', color: '#888', margin: '0 0.1rem 0 0.75rem' }}>Documents:</span>
        <button style={btnStyle(mode === 'notes')} onClick={() => switchMode('notes')}>Notes</button>
        <button style={btnStyle(mode === 'pov')}   onClick={() => switchMode('pov')}>POV</button>
        <button style={btnStyle(mode === 'jep')}   onClick={() => switchMode('jep')}>JEP</button>
      </div>

      {/* Diagram modes */}
      {mode === 'upload' && (
        <UploadBom
          clientId={clientId}
          diagramName={diagramName}
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
                marginTop: '1rem', padding: '0.75rem', background: '#fff0f0',
                border: '1px solid #c00', borderRadius: '4px',
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}
            >
              <strong>Error:</strong> {error}
            </div>
          )}
          {result && result.status === 'ok' && <ResponseDisplay result={result} />}
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

      <footer style={{ marginTop: '2rem', fontSize: '0.75rem', color: '#999', borderTop: '1px solid #eee', paddingTop: '0.5rem' }}>
        client_id: <code data-testid="client-id-display">{clientId}</code>
      </footer>
    </div>
  );
}
