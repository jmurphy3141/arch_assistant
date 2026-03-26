import React, { useRef, useState } from 'react';
import { apiUploadBom, type GenerateResponse } from '../api/client';
import type { ApiError } from '../api/client';

interface Props {
  clientId: string;
  diagramName: string;
  onDiagramNameChange: (name: string) => void;
  onResult: (r: GenerateResponse) => void;
  onError: (msg: string) => void;
}

export function UploadBom({
  clientId,
  diagramName,
  onDiagramNameChange,
  onResult,
  onError,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const ctxFileRef = useRef<HTMLInputElement>(null);
  const [context, setContext] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      onError('Please select a BOM Excel file.');
      return;
    }

    const fd = new FormData();
    fd.append('file', file);
    fd.append('diagram_name', diagramName);
    fd.append('client_id', clientId);
    if (context.trim()) fd.append('context', context);
    const ctxFile = ctxFileRef.current?.files?.[0];
    if (ctxFile) fd.append('context_file', ctxFile);

    setLoading(true);
    try {
      const result = await apiUploadBom(fd);
      onResult(result);
    } catch (err: unknown) {
      const e = err as ApiError;
      onError(`${e.status}: ${e.detail}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} data-testid="upload-bom-form">
      <div style={{ marginBottom: '0.75rem' }}>
        <label>
          Diagram name:&nbsp;
          <input
            type="text"
            value={diagramName}
            onChange={(e) => onDiagramNameChange(e.target.value)}
            required
            style={{ width: '18rem' }}
          />
        </label>
      </div>

      <div style={{ marginBottom: '0.75rem' }}>
        <label>
          BOM file (.xlsx):{' '}
          <input ref={fileRef} type="file" accept=".xlsx,.xls" required />
        </label>
      </div>

      <div style={{ marginBottom: '0.75rem' }}>
        <label>
          Context file (optional):{' '}
          <input ref={ctxFileRef} type="file" />
        </label>
      </div>

      <div style={{ marginBottom: '0.75rem' }}>
        <label>
          Context text (optional):
          <br />
          <textarea
            value={context}
            onChange={(e) => setContext(e.target.value)}
            rows={3}
            style={{ width: '100%' }}
            placeholder="e.g. 6 regions, HA active-passive"
          />
        </label>
      </div>

      <button type="submit" disabled={loading}>
        {loading ? 'Uploading…' : 'Upload BOM & Generate'}
      </button>
    </form>
  );
}
