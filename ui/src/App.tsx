import React, { useState } from 'react';
import { HealthIndicator } from './components/HealthIndicator';
import { UploadBom } from './components/UploadBom';
import { GenerateForm } from './components/GenerateForm';
import { ResponseDisplay } from './components/ResponseDisplay';
import { ClarifyForm } from './components/ClarifyForm';
import { useClientId, getLastDiagramName, saveLastDiagramName } from './hooks/useClientId';
import { apiClarify, type GenerateResponse } from './api/client';

type Mode = 'upload' | 'generate';

export function App() {
  const clientId = useClientId();
  const [mode, setMode] = useState<Mode>('upload');
  const [diagramName, setDiagramName] = useState<string>(getLastDiagramName);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clarifyLoading, setClarifyLoading] = useState(false);

  function handleDiagramNameChange(name: string) {
    setDiagramName(name);
    saveLastDiagramName(name);
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

  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', padding: '1rem', fontFamily: 'sans-serif' }}>
      <header style={{ borderBottom: '2px solid #e00', paddingBottom: '0.5rem', marginBottom: '1rem' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem' }}>
          OCI Drawing Agent <small style={{ fontWeight: 'normal', fontSize: '0.85rem' }}>Agent 3 v1.3.2</small>
        </h1>
        <HealthIndicator />
      </header>

      <div style={{ marginBottom: '1rem' }}>
        <strong>Mode:</strong>&nbsp;
        <button
          onClick={() => { setMode('upload'); setResult(null); setError(null); }}
          disabled={mode === 'upload'}
          style={{ marginRight: '0.5rem' }}
        >
          Upload BOM
        </button>
        <button
          onClick={() => { setMode('generate'); setResult(null); setError(null); }}
          disabled={mode === 'generate'}
        >
          Generate from Resources
        </button>
      </div>

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

      {error && (
        <div
          data-testid="error-display"
          style={{
            marginTop: '1rem',
            padding: '0.75rem',
            background: '#fff0f0',
            border: '1px solid #c00',
            borderRadius: '4px',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          <strong>Error:</strong> {error}
        </div>
      )}

      {result && result.status === 'ok' && <ResponseDisplay result={result} />}

      {result && result.status === 'need_clarification' && (
        <ClarifyForm
          result={result}
          onSubmit={handleClarify}
          loading={clarifyLoading}
        />
      )}

      <footer style={{ marginTop: '2rem', fontSize: '0.75rem', color: '#999', borderTop: '1px solid #eee', paddingTop: '0.5rem' }}>
        client_id: <code data-testid="client-id-display">{clientId}</code>
      </footer>
    </div>
  );
}
