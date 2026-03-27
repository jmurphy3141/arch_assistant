import React, { useState } from 'react';
import type { DocVersionEntry } from '../api/client';

interface Props {
  content: string;
  docType: 'pov' | 'jep';
  version: number;
  versionHistory?: DocVersionEntry[];
  onClose?: () => void;
}

export function DocViewer({ content, docType, version, versionHistory = [], onClose }: Props) {
  const [showRaw, setShowRaw] = useState(false);

  const label = docType === 'pov' ? 'Point of View' : 'Joint Execution Plan';

  return (
    <div style={{ marginTop: '1rem' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        borderBottom: '2px solid #e00', paddingBottom: '0.4rem', marginBottom: '0.75rem',
      }}>
        <strong style={{ fontSize: '1rem' }}>
          {label} — Version {version}
        </strong>
        <span style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            style={{ fontSize: '0.8rem', padding: '0.2rem 0.5rem' }}
            onClick={() => setShowRaw(!showRaw)}
          >
            {showRaw ? 'Formatted' : 'Raw Markdown'}
          </button>
          <button
            style={{ fontSize: '0.8rem', padding: '0.2rem 0.5rem' }}
            onClick={() => {
              const blob = new Blob([content], { type: 'text/markdown' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `${docType}_v${version}.md`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            Download .md
          </button>
          {onClose && (
            <button style={{ fontSize: '0.8rem', padding: '0.2rem 0.5rem' }} onClick={onClose}>
              ✕ Close
            </button>
          )}
        </span>
      </div>

      {showRaw ? (
        <pre style={{
          background: '#f8f8f8', border: '1px solid #ddd', borderRadius: '4px',
          padding: '1rem', overflow: 'auto', maxHeight: '60vh',
          fontSize: '0.78rem', lineHeight: '1.5', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>
          {content}
        </pre>
      ) : (
        <div style={{
          background: '#fff', border: '1px solid #ddd', borderRadius: '4px',
          padding: '1.25rem', overflow: 'auto', maxHeight: '60vh',
          fontSize: '0.88rem', lineHeight: '1.7',
        }}>
          <MarkdownRenderer content={content} />
        </div>
      )}

      {versionHistory.length > 1 && (
        <details style={{ marginTop: '0.75rem' }}>
          <summary style={{ cursor: 'pointer', fontSize: '0.82rem', color: '#666' }}>
            Version history ({versionHistory.length} versions)
          </summary>
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '0.5rem', fontSize: '0.8rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #ddd' }}>
                <th style={{ textAlign: 'left', padding: '0.2rem 0.4rem' }}>Version</th>
                <th style={{ textAlign: 'left', padding: '0.2rem 0.4rem' }}>Generated</th>
              </tr>
            </thead>
            <tbody>
              {versionHistory.map(v => (
                <tr key={v.version} style={{ borderBottom: '1px solid #eee' }}>
                  <td style={{ padding: '0.2rem 0.4rem' }}>v{v.version}</td>
                  <td style={{ padding: '0.2rem 0.4rem', color: '#666' }}>
                    {new Date(v.timestamp).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

/** Minimal Markdown renderer — headings, bold, bullets, blockquotes, horizontal rules. */
function MarkdownRenderer({ content }: { content: string }) {
  const lines = content.split('\n');
  const elements: React.ReactNode[] = [];
  let key = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.startsWith('### ')) {
      elements.push(<h3 key={key++} style={{ margin: '1rem 0 0.3rem', fontSize: '0.95rem' }}>{inlineMarkdown(line.slice(4))}</h3>);
    } else if (line.startsWith('## ')) {
      elements.push(<h2 key={key++} style={{ margin: '1.2rem 0 0.4rem', fontSize: '1.05rem', borderBottom: '1px solid #eee', paddingBottom: '0.2rem' }}>{inlineMarkdown(line.slice(3))}</h2>);
    } else if (line.startsWith('# ')) {
      elements.push(<h1 key={key++} style={{ margin: '0 0 0.6rem', fontSize: '1.2rem' }}>{inlineMarkdown(line.slice(2))}</h1>);
    } else if (line.startsWith('> ')) {
      elements.push(
        <blockquote key={key++} style={{ margin: '0.3rem 0', paddingLeft: '0.75rem', borderLeft: '3px solid #aaa', color: '#555', fontStyle: 'italic' }}>
          {inlineMarkdown(line.slice(2))}
        </blockquote>
      );
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      elements.push(<li key={key++} style={{ marginLeft: '1.5rem', marginBottom: '0.15rem' }}>{inlineMarkdown(line.slice(2))}</li>);
    } else if (line.startsWith('| ')) {
      // Table row — collect until non-table line
      const tableLines: string[] = [line];
      while (i + 1 < lines.length && lines[i + 1].startsWith('|')) {
        i++;
        tableLines.push(lines[i]);
      }
      elements.push(<TableRenderer key={key++} rows={tableLines} />);
    } else if (line.match(/^---+$/) || line.match(/^\*\*\*+$/)) {
      elements.push(<hr key={key++} style={{ border: 'none', borderTop: '1px solid #ddd', margin: '0.75rem 0' }} />);
    } else if (line.trim() === '') {
      elements.push(<div key={key++} style={{ height: '0.4rem' }} />);
    } else {
      elements.push(<p key={key++} style={{ margin: '0 0 0.3rem' }}>{inlineMarkdown(line)}</p>);
    }
  }

  return <div>{elements}</div>;
}

function TableRenderer({ rows }: { rows: string[] }) {
  const parsed = rows.map(r => r.split('|').slice(1, -1).map(cell => cell.trim()));
  const [header, _sep, ...body] = parsed;
  return (
    <table style={{ borderCollapse: 'collapse', width: '100%', marginBottom: '0.5rem', fontSize: '0.82rem' }}>
      {header && (
        <thead>
          <tr style={{ background: '#f2f2f2' }}>
            {header.map((h, i) => (
              <th key={i} style={{ border: '1px solid #ddd', padding: '0.3rem 0.5rem', textAlign: 'left' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
      )}
      <tbody>
        {body.map((row, ri) => (
          <tr key={ri}>
            {row.map((cell, ci) => (
              <td key={ci} style={{ border: '1px solid #ddd', padding: '0.3rem 0.5rem' }}>
                {inlineMarkdown(cell)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function inlineMarkdown(text: string): React.ReactNode {
  // Handle **bold** and *italic* and `code`
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={i} style={{ background: '#f0f0f0', padding: '0 0.2rem', borderRadius: '2px', fontSize: '0.88em' }}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}
