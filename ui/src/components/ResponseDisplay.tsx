import React, { useState } from 'react';
import { downloadUrl } from '../api/client';
import type { GenerateResponse } from '../api/client';

const ARTIFACT_FILES = [
  'diagram.drawio',
  'spec.json',
  'draw_dict.json',
  'render_manifest.json',
  'node_to_resource_map.json',
] as const;

interface Props {
  result: GenerateResponse;
  onRefine?: (feedback: string) => void;
  refineLoading?: boolean;
}

const S = {
  box: {
    marginTop: '1.5rem',
    padding: '0.75rem',
    background: 'rgba(232,87,26,0.05)',
    border: '1px solid rgba(232,87,26,0.25)',
    borderRadius: 4,
  } as React.CSSProperties,
  label: {
    fontSize: '0.72rem',
    color: '#e8571a',
    letterSpacing: '0.08em',
    textTransform: 'uppercase' as const,
    marginBottom: '0.4rem',
    display: 'block',
  },
  textarea: {
    width: '100%',
    minHeight: '70px',
    background: '#0e1016',
    border: '1px solid #1c2030',
    borderRadius: 4,
    color: '#cdd2e0',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: '0.78rem',
    padding: '0.5rem',
    resize: 'vertical' as const,
    boxSizing: 'border-box' as const,
  },
  btn: (disabled: boolean): React.CSSProperties => ({
    marginTop: '0.5rem',
    padding: '0.3rem 0.9rem',
    background: disabled ? '#1c2030' : 'rgba(232,87,26,0.15)',
    border: '1px solid ' + (disabled ? '#1c2030' : '#e8571a'),
    color: disabled ? '#454d64' : '#e8571a',
    cursor: disabled ? 'default' : 'pointer',
    borderRadius: 4,
    fontSize: '0.75rem',
    fontFamily: "'JetBrains Mono', monospace",
  }),
};

export function ResponseDisplay({ result, onRefine, refineLoading }: Props) {
  const [feedback, setFeedback] = useState('');

  if (result.status === 'ok') {
    return (
      <div data-testid="response-ok">
        <h3>Diagram Generated</h3>
        <table style={{ borderCollapse: 'collapse', marginBottom: '1rem' }}>
          <tbody>
            <tr>
              <td style={{ paddingRight: '1rem', fontWeight: 'bold' }}>
                request_id
              </td>
              <td data-testid="request-id">{result.request_id}</td>
            </tr>
            <tr>
              <td style={{ paddingRight: '1rem', fontWeight: 'bold' }}>
                input_hash
              </td>
              <td data-testid="input-hash">
                <code>{result.input_hash}</code>
              </td>
            </tr>
          </tbody>
        </table>

        {result.render_manifest && (
          <details open>
            <summary>render_manifest</summary>
            <pre style={{ background: '#f4f4f4', padding: '0.5rem', fontSize: '0.8rem' }}>
              {JSON.stringify(result.render_manifest, null, 2)}
            </pre>
          </details>
        )}

        <h4>Downloads</h4>
        <ul data-testid="download-list">
          {ARTIFACT_FILES.map((f) => (
            <li key={f}>
              <a
                href={downloadUrl(f, result.client_id, result.diagram_name)}
                download={f}
                data-testid={`download-${f}`}
              >
                {f}
              </a>
            </li>
          ))}
        </ul>

        {onRefine && (
          <div style={S.box}>
            <span style={S.label}>Refine Diagram</span>
            <textarea
              data-testid="refine-feedback"
              style={S.textarea}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="Describe changes, e.g. &quot;Add a DB subnet to each compartment&quot; or &quot;Show the DR region as a full duplicate&quot;"
            />
            <button
              data-testid="refine-submit"
              style={S.btn(refineLoading === true || !feedback.trim())}
              disabled={refineLoading === true || !feedback.trim()}
              onClick={() => { onRefine(feedback); setFeedback(''); }}
            >
              {refineLoading ? 'Regenerating…' : 'Refine Diagram'}
            </button>
          </div>
        )}
      </div>
    );
  }

  return null;
}
