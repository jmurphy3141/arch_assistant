import React, { useEffect, useMemo, useState } from 'react';
import type { ChatArtifactDownload } from '../api/client';

interface ArtifactPreviewPanelProps {
  artifacts: ChatArtifactDownload[];
  compact?: boolean;
}

function titleForArtifact(artifact: ChatArtifactDownload): string {
  if (artifact.type === 'terraform') return `Terraform: ${artifact.filename ?? 'file'}`;
  if (artifact.type === 'diagram') return `Diagram: ${artifact.filename ?? 'artifact'}`;
  return `${artifact.type}: ${artifact.filename ?? artifact.tool ?? 'artifact'}`;
}

export function ArtifactPreviewPanel({ artifacts, compact = false }: ArtifactPreviewPanelProps) {
  const [selectedUrl, setSelectedUrl] = useState<string | null>(null);
  const [selectedLabel, setSelectedLabel] = useState<string>('');
  const [previewText, setPreviewText] = useState<string>('');
  const [previewLoading, setPreviewLoading] = useState(false);

  const normalized = useMemo(
    () => artifacts.filter(a => typeof a.download_url === 'string' && a.download_url.trim().length > 0),
    [artifacts],
  );

  useEffect(() => {
    if (normalized.length === 0) {
      setSelectedUrl(null);
      setSelectedLabel('');
      setPreviewText('');
      return;
    }
    const existing = selectedUrl
      ? normalized.find(item => item.download_url === selectedUrl)
      : undefined;
    if (existing) {
      setSelectedLabel(titleForArtifact(existing));
      return;
    }
    const first = normalized[0];
    setSelectedUrl(first.download_url);
    setSelectedLabel(titleForArtifact(first));
  }, [normalized, selectedUrl]);

  useEffect(() => {
    if (!selectedUrl) return;
    let active = true;
    let parsedPath = '';
    try {
      parsedPath = new URL(selectedUrl, window.location.origin).pathname.toLowerCase();
    } catch {
      parsedPath = selectedUrl.toLowerCase();
    }
    // Best effort inline preview: terraform/text-like artifacts.
    if (!parsedPath.endsWith('.tf') && !parsedPath.endsWith('.md') && !parsedPath.endsWith('.txt')) {
      setPreviewText('');
      return;
    }
    setPreviewLoading(true);
    fetch(selectedUrl)
      .then(resp => (resp.ok ? resp.text() : Promise.resolve('(preview unavailable)')))
      .then(text => {
        if (!active) return;
        setPreviewText(text);
      })
      .catch(() => {
        if (!active) return;
        setPreviewText('(preview unavailable)');
      })
      .finally(() => {
        if (!active) return;
        setPreviewLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedUrl]);

  return (
    <aside
      data-testid="artifact-preview-panel"
      style={{
        width: compact ? '100%' : '300px',
        minWidth: compact ? '100%' : '300px',
        border: '1px solid #1c2030',
        borderRadius: 8,
        background: '#0b0d14',
        padding: '0.75rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
      }}
    >
      <div style={{ fontSize: '0.72rem', color: '#8b93a8', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        Artifacts
      </div>

      {normalized.length === 0 && (
        <div style={{ color: '#6b738a', fontSize: '0.75rem' }}>
          Generated artifacts from chat will appear here.
        </div>
      )}

      {normalized.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
          {normalized.map((artifact, idx) => {
            const label = titleForArtifact(artifact);
            const active = artifact.download_url === selectedUrl;
            return (
              <button
                key={`${artifact.download_url}-${idx}`}
                data-testid={`artifact-preview-item-${idx}`}
                onClick={() => {
                  setSelectedUrl(artifact.download_url);
                  setSelectedLabel(label);
                }}
                style={{
                  textAlign: 'left',
                  border: active ? '1px solid rgba(232,87,26,0.45)' : '1px solid #1c2030',
                  background: active ? 'rgba(232,87,26,0.08)' : '#0e1016',
                  borderRadius: 6,
                  color: '#cdd2e0',
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: '0.7rem',
                  padding: '0.4rem 0.5rem',
                  cursor: 'pointer',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}

      {selectedUrl && (
        <div style={{ borderTop: '1px solid #1c2030', paddingTop: '0.6rem', display: 'flex', flexDirection: 'column', gap: '0.45rem' }}>
          <div style={{ fontSize: '0.72rem', color: '#cdd2e0' }}>{selectedLabel}</div>
          <a
            href={selectedUrl}
            target="_blank"
            rel="noreferrer"
            data-testid="artifact-preview-download-link"
            style={{ fontSize: '0.72rem', color: '#e8571a', textDecoration: 'none' }}
          >
            Open / Download
          </a>
          {(previewLoading || previewText) && (
            <pre
              data-testid="artifact-preview-text"
              style={{
                margin: 0,
                background: '#08090d',
                border: '1px solid #1c2030',
                borderRadius: 6,
                padding: '0.55rem',
                color: '#aeb5c8',
                fontSize: '0.68rem',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                maxHeight: '280px',
                overflowY: 'auto',
              }}
            >
              {previewLoading ? 'Loading preview...' : previewText}
            </pre>
          )}
        </div>
      )}
    </aside>
  );
}
