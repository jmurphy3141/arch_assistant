import React from 'react';
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
}

export function ResponseDisplay({ result }: Props) {
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
      </div>
    );
  }

  return null;
}
