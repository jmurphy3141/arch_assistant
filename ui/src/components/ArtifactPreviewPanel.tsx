import React, { useEffect, useMemo, useState } from 'react';
import type { ChatArtifactDownload } from '../api/client';

interface ArtifactPreviewPanelProps {
  artifacts: ChatArtifactDownload[];
  compact?: boolean;
  onQuickPrompt?: (text: string) => void;
}

type ArtifactGroup = { label: string; icon: string; items: ChatArtifactDownload[] };

function groupArtifacts(artifacts: ChatArtifactDownload[]): ArtifactGroup[] {
  const diagrams  = artifacts.filter(a => a.type === 'diagram'   || a.tool === 'generate_diagram');
  const boms      = artifacts.filter(a => a.type === 'bom'       || a.tool === 'generate_bom');
  const terraform = artifacts.filter(a => a.type === 'terraform' || a.tool === 'generate_terraform');
  const docs      = artifacts.filter(a =>
    !diagrams.includes(a) && !boms.includes(a) && !terraform.includes(a)
  );
  const groups: ArtifactGroup[] = [];
  if (diagrams.length  > 0) groups.push({ label: 'Diagrams',   icon: '◈', items: diagrams });
  if (boms.length      > 0) groups.push({ label: 'BOMs',       icon: '$', items: boms });
  if (terraform.length > 0) groups.push({ label: 'Terraform',  icon: '{}', items: terraform });
  if (docs.length      > 0) groups.push({ label: 'Documents',  icon: '≡', items: docs });
  return groups;
}

function shortLabel(artifact: ChatArtifactDownload): string {
  if (artifact.filename) return artifact.filename;
  if (artifact.type === 'diagram')   return 'Architecture diagram';
  if (artifact.type === 'bom')       return 'Bill of Materials';
  if (artifact.type === 'terraform') return 'Terraform bundle';
  return artifact.tool ?? artifact.type ?? 'artifact';
}

const QUICK_PROMPTS = [
  { label: 'Generate a diagram', prompt: 'Generate an architecture diagram for this workload.' },
  { label: 'Build a BOM',        prompt: 'Build a priced OCI Bill of Materials for this architecture.' },
  { label: 'Write a POV',        prompt: 'Write a Point of View document for this customer engagement.' },
];

export function ArtifactPreviewPanel({ artifacts, compact = false, onQuickPrompt }: ArtifactPreviewPanelProps) {
  const [selectedUrl, setSelectedUrl] = useState<string | null>(null);
  const [selectedLabel, setSelectedLabel] = useState<string>('');
  const [previewText, setPreviewText] = useState<string>('');
  const [previewLoading, setPreviewLoading] = useState(false);

  const normalized = useMemo(
    () => artifacts.filter(a => typeof a.download_url === 'string' && a.download_url.trim().length > 0),
    [artifacts],
  );

  const groups = useMemo(() => groupArtifacts(normalized), [normalized]);

  useEffect(() => {
    if (normalized.length === 0) {
      setSelectedUrl(null);
      setSelectedLabel('');
      setPreviewText('');
      return;
    }
    const existing = selectedUrl ? normalized.find(item => item.download_url === selectedUrl) : undefined;
    if (existing) { setSelectedLabel(shortLabel(existing)); return; }
    const first = normalized[0];
    setSelectedUrl(first.download_url);
    setSelectedLabel(shortLabel(first));
  }, [normalized, selectedUrl]);

  useEffect(() => {
    if (!selectedUrl) return;
    let active = true;
    let parsedPath = '';
    try { parsedPath = new URL(selectedUrl, window.location.origin).pathname.toLowerCase(); }
    catch { parsedPath = selectedUrl.toLowerCase(); }
    if (!parsedPath.endsWith('.tf') && !parsedPath.endsWith('.md') && !parsedPath.endsWith('.txt')) {
      setPreviewText('');
      return;
    }
    setPreviewLoading(true);
    fetch(selectedUrl)
      .then(resp => resp.ok ? resp.text() : Promise.resolve('(preview unavailable)'))
      .then(text => { if (!active) return; setPreviewText(text); })
      .catch(() => { if (!active) return; setPreviewText('(preview unavailable)'); })
      .finally(() => { if (!active) return; setPreviewLoading(false); });
    return () => { active = false; };
  }, [selectedUrl]);

  const panelStyle: React.CSSProperties = {
    width:      compact ? '100%' : '320px',
    minWidth:   compact ? '100%' : '320px',
    border:     '1px solid #273149',
    borderRadius: 14,
    background: 'linear-gradient(180deg, #0d111d 0%, #0a0d16 100%)',
    boxShadow:  '0 20px 45px rgba(0,0,0,0.35)',
    padding:    '0.9rem',
    display:    'flex',
    flexDirection: 'column',
    gap:        '0.65rem',
  };

  const sectionLabelStyle: React.CSSProperties = {
    fontSize:      '0.6rem',
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color:         '#4b5470',
    fontWeight:    700,
    marginBottom:  '0.25rem',
    display:       'flex',
    alignItems:    'center',
    gap:           '0.35rem',
  };

  const itemBtnStyle = (active: boolean): React.CSSProperties => ({
    textAlign:    'left',
    border:       active ? '1px solid rgba(232,87,26,0.45)' : '1px solid #1c2233',
    background:   active ? 'rgba(232,87,26,0.12)' : '#0e111a',
    borderLeft:   active ? '3px solid #e8571a' : '1px solid #1c2233',
    borderRadius: 8,
    color:        '#cdd2e0',
    fontFamily:   "'JetBrains Mono', monospace",
    fontSize:     '0.72rem',
    padding:      '0.45rem 0.6rem',
    cursor:       'pointer',
    display:      'block',
    width:        '100%',
    overflow:     'hidden',
    textOverflow: 'ellipsis',
    whiteSpace:   'nowrap',
  });

  return (
    <aside data-testid="artifact-preview-panel" style={panelStyle}>
      <div style={{ fontSize: '0.74rem', color: '#9aa4bb', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        Artifacts
      </div>

      {normalized.length === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.55rem' }}>
          <div style={{ color: '#4b5470', fontSize: '0.72rem' }}>
            No artifacts yet. Use a quick action to get started:
          </div>
          {QUICK_PROMPTS.map(qp => (
            <button
              key={qp.label}
              onClick={() => onQuickPrompt?.(qp.prompt)}
              disabled={!onQuickPrompt}
              style={{
                textAlign:    'left',
                background:   'rgba(143,180,255,0.05)',
                border:       '1px solid #1c2233',
                borderRadius: 8,
                color:        '#8fb4ff',
                fontFamily:   "'JetBrains Mono', monospace",
                fontSize:     '0.73rem',
                padding:      '0.5rem 0.65rem',
                cursor:       onQuickPrompt ? 'pointer' : 'default',
                opacity:      onQuickPrompt ? 1 : 0.5,
              }}
            >
              {qp.label} →
            </button>
          ))}
        </div>
      )}

      {groups.map(group => (
        <div key={group.label}>
          <div style={sectionLabelStyle}>
            <span>{group.icon}</span>
            <span>{group.label}</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.28rem' }}>
            {group.items.map((artifact, idx) => {
              const active = artifact.download_url === selectedUrl;
              const label = shortLabel(artifact);
              return (
                <button
                  key={`${artifact.download_url}-${idx}`}
                  data-testid={`artifact-preview-item-${idx}`}
                  onClick={() => { setSelectedUrl(artifact.download_url); setSelectedLabel(label); }}
                  style={itemBtnStyle(active)}
                  title={label}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      ))}

      {selectedUrl && (
        <div style={{ borderTop: '1px solid #273149', paddingTop: '0.6rem', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
          <div style={{ fontSize: '0.73rem', color: '#e2e7f5', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {selectedLabel}
          </div>
          <a
            href={selectedUrl}
            target="_blank"
            rel="noreferrer"
            data-testid="artifact-preview-download-link"
            style={{ fontSize: '0.72rem', color: '#ff7a43', textDecoration: 'none' }}
          >
            Open / Download ↗
          </a>
          {(previewLoading || previewText) && (
            <pre
              data-testid="artifact-preview-text"
              style={{
                margin: 0,
                background: '#08090d',
                border: '1px solid #273149',
                borderRadius: 8,
                padding: '0.55rem',
                color: '#aeb5c8',
                fontSize: '0.68rem',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                maxHeight: '260px',
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
