import React, { useState } from 'react';
import {
  apiGenerate,
  apiInputsResolve,
  type GenerateRequest,
  type GenerateResponse,
  type ObjectRef,
  type ApiError,
  type ResolveResponse,
} from '../api/client';

type InputSource = 'manual' | 'bucket';

interface Props {
  clientId: string;
  diagramName: string;
  onDiagramNameChange: (name: string) => void;
  onResult: (r: GenerateResponse) => void;
  onError: (msg: string) => void;
}

function parseObjectRef(val: string, field: string): ObjectRef | null {
  if (!val.trim()) return null;
  try {
    const parsed = JSON.parse(val);
    if (parsed && typeof parsed === 'object' && 'bucket' in parsed && 'object' in parsed) {
      return parsed as ObjectRef;
    }
  } catch {
    // Not JSON — try simple "bucket/object" shorthand
    const parts = val.trim().split('/');
    if (parts.length >= 2) {
      return { bucket: parts[0], object: parts.slice(1).join('/') };
    }
  }
  return null;
}

export function GenerateForm({
  clientId,
  diagramName,
  onDiagramNameChange,
  onResult,
  onError,
}: Props) {
  const [inputSource, setInputSource] = useState<InputSource>('manual');
  const [loading, setLoading] = useState(false);
  const [resolveResult, setResolveResult] = useState<ResolveResponse | null>(null);

  // Manual mode
  const [resourcesJson, setResourcesJson] = useState('[]');
  const [context, setContext] = useState('');
  const [questionnaire, setQuestionnaire] = useState('');
  const [notes, setNotes] = useState('');

  // Bucket mode
  const [resourcesBucket, setResourcesBucket] = useState('');
  const [contextBucket, setContextBucket] = useState('');
  const [questionnaireBucket, setQuestionnaireBucket] = useState('');
  const [notesBucket, setNotesBucket] = useState('');
  const [deploymentHintsBucket, setDeploymentHintsBucket] = useState('');

  async function handleValidatePreview() {
    const body: Record<string, ObjectRef> = {};
    const rb = parseObjectRef(resourcesBucket, 'resources');
    if (rb) body.resources_from_bucket = rb;
    const cb = parseObjectRef(contextBucket, 'context');
    if (cb) body.context_from_bucket = cb;
    const qb = parseObjectRef(questionnaireBucket, 'questionnaire');
    if (qb) body.questionnaire_from_bucket = qb;
    const nb = parseObjectRef(notesBucket, 'notes');
    if (nb) body.notes_from_bucket = nb;
    const db = parseObjectRef(deploymentHintsBucket, 'deployment_hints');
    if (db) body.deployment_hints_from_bucket = db;

    setLoading(true);
    try {
      const res = await apiInputsResolve(body);
      setResolveResult(res);
    } catch (err: unknown) {
      const e = err as ApiError;
      onError(`Resolve error ${e.status}: ${e.detail}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const req: GenerateRequest = {
      diagram_name: diagramName,
      client_id: clientId,
    };

    if (inputSource === 'manual') {
      let resources: Record<string, unknown>[];
      try {
        resources = JSON.parse(resourcesJson);
        if (!Array.isArray(resources)) throw new Error('must be array');
      } catch (err) {
        onError(`resources JSON error: ${String(err)}`);
        return;
      }
      req.resources = resources;
      if (context.trim()) req.context = context;
      if (questionnaire.trim()) req.questionnaire = questionnaire;
      if (notes.trim()) req.notes = notes;
    } else {
      // Bucket mode
      const rb = parseObjectRef(resourcesBucket, 'resources');
      if (!rb) {
        onError('resources_from_bucket is required in bucket mode');
        return;
      }
      req.resources_from_bucket = rb;
      const cb = parseObjectRef(contextBucket, 'context');
      if (cb) req.context_from_bucket = cb;
      const qb = parseObjectRef(questionnaireBucket, 'questionnaire');
      if (qb) req.questionnaire_from_bucket = qb;
      const nb = parseObjectRef(notesBucket, 'notes');
      if (nb) req.notes_from_bucket = nb;
      const db = parseObjectRef(deploymentHintsBucket, 'deployment_hints');
      if (db) req.deployment_hints_from_bucket = db;
    }

    setLoading(true);
    try {
      const result = await apiGenerate(req);
      onResult(result);
    } catch (err: unknown) {
      const e = err as ApiError;
      onError(`${e.status}: ${e.detail}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} data-testid="generate-form">
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
        <strong>Input source:</strong>&nbsp;
        <label>
          <input
            type="radio"
            name="inputSource"
            value="manual"
            checked={inputSource === 'manual'}
            onChange={() => setInputSource('manual')}
          />
          &nbsp;Manual JSON
        </label>
        &nbsp;&nbsp;
        <label>
          <input
            type="radio"
            name="inputSource"
            value="bucket"
            checked={inputSource === 'bucket'}
            onChange={() => setInputSource('bucket')}
          />
          &nbsp;OCI Bucket Refs
        </label>
      </div>

      {inputSource === 'manual' && (
        <>
          <div style={{ marginBottom: '0.75rem' }}>
            <label>
              resources[] (JSON array):
              <br />
              <textarea
                data-testid="resources-json"
                value={resourcesJson}
                onChange={(e) => setResourcesJson(e.target.value)}
                rows={6}
                style={{ width: '100%', fontFamily: 'monospace', fontSize: '0.85rem' }}
              />
            </label>
          </div>
          <div style={{ marginBottom: '0.75rem' }}>
            <label>
              context (optional):
              <br />
              <textarea
                value={context}
                onChange={(e) => setContext(e.target.value)}
                rows={2}
                style={{ width: '100%' }}
              />
            </label>
          </div>
          <div style={{ marginBottom: '0.75rem' }}>
            <label>
              questionnaire answers (optional):
              <br />
              <textarea
                value={questionnaire}
                onChange={(e) => setQuestionnaire(e.target.value)}
                rows={2}
                style={{ width: '100%' }}
              />
            </label>
          </div>
          <div style={{ marginBottom: '0.75rem' }}>
            <label>
              notes (optional):
              <br />
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                style={{ width: '100%' }}
              />
            </label>
          </div>
        </>
      )}

      {inputSource === 'bucket' && (
        <>
          <p style={{ fontSize: '0.85rem', color: '#555' }}>
            Enter bucket refs as <code>bucket-name/object-name</code> or JSON{' '}
            <code>{'{"bucket":"b","object":"k"}'}</code>. The server fetches objects
            directly — your browser never accesses OCI.
          </p>
          {(
            [
              { label: 'resources_from_bucket (required)', value: resourcesBucket, set: setResourcesBucket },
              { label: 'context_from_bucket', value: contextBucket, set: setContextBucket },
              { label: 'questionnaire_from_bucket', value: questionnaireBucket, set: setQuestionnaireBucket },
              { label: 'notes_from_bucket', value: notesBucket, set: setNotesBucket },
              { label: 'deployment_hints_from_bucket', value: deploymentHintsBucket, set: setDeploymentHintsBucket },
            ] as { label: string; value: string; set: React.Dispatch<React.SetStateAction<string>> }[]
          ).map(({ label, value, set }) => (
            <div key={label} style={{ marginBottom: '0.5rem' }}>
              <label>
                {label}:
                <br />
                <input
                  type="text"
                  value={value}
                  onChange={(e) => set(e.target.value)}
                  style={{ width: '100%' }}
                  placeholder="my-bucket/path/to/object.json"
                />
              </label>
            </div>
          ))}
          <button
            type="button"
            onClick={handleValidatePreview}
            disabled={loading}
            style={{ marginBottom: '0.75rem', marginRight: '0.5rem' }}
          >
            {loading ? 'Validating…' : 'Validate & Preview'}
          </button>
          {resolveResult && (
            <pre style={{ fontSize: '0.8rem', background: '#f4f4f4', padding: '0.5rem' }}>
              {JSON.stringify(resolveResult, null, 2)}
            </pre>
          )}
        </>
      )}

      <button type="submit" disabled={loading}>
        {loading ? 'Generating…' : 'Generate Diagram'}
      </button>
    </form>
  );
}
