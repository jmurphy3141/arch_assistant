import React, { useState, useRef, useCallback } from 'react';
import { apiA2AUploadBom, apiUploadBom, apiUploadToBucket, type GenerateResponse, type OrchestrationResult, type ApiError } from '../api/client';

// ── Design tokens matching the dark OCI theme ─────────────────────────────
const T = {
  bg:      '#08090d',
  surface: '#0e1016',
  card:    '#12151d',
  border:  '#1c2030',
  border2: '#242840',
  accent:  '#e8571a',
  accentG: 'rgba(232,87,26,0.15)',
  text:    '#cdd2e0',
  label:   '#8892a4',   // readable label colour — between muted and text
  muted:   '#454d64',
  green:   '#2ecc8a',
  red:     '#e8415a',
  gold:    '#f0a500',
  font:    "'JetBrains Mono', monospace",
  display: "'Syne', sans-serif",
} as const;

// ── Bucket defaults (from config.yaml) ───────────────────────────────────
const DEFAULT_NAMESPACE = 'oraclejamescalise';
const DEFAULT_BUCKET    = 'agent_assistante';
const DEFAULT_PREFIX    = 'agent3';

// ── Architecture questionnaire ────────────────────────────────────────────
interface QItem {
  id:   string;
  label: string;
  type: 'radio' | 'checkbox' | 'text';
  options?: string[];
  placeholder?: string;
}

const QUESTIONNAIRE: QItem[] = [
  {
    id: 'regions',
    label: 'Deployment scope',
    type: 'radio',
    options: ['Single region', '2 regions — active-passive', '2 regions — active-active', '3+ regions'],
  },
  {
    id: 'ha',
    label: 'High availability',
    type: 'radio',
    options: ['HA required (multiple ADs)', 'Single AD acceptable', 'Not specified'],
  },
  {
    id: 'internet',
    label: 'Internet-facing endpoints',
    type: 'radio',
    options: ['Yes — public load balancer required', 'No — internal / private only', 'Not specified'],
  },
  {
    id: 'onprem',
    label: 'On-premises connectivity',
    type: 'radio',
    options: ['IPSec VPN', 'FastConnect (dedicated circuit)', 'Both VPN + FastConnect', 'Cloud-only (none)'],
  },
  {
    id: 'security',
    label: 'Security requirements',
    type: 'checkbox',
    options: ['WAF (Web Application Firewall)', 'DDoS protection', 'Bastion host for admin access', 'Private endpoints only (no public IPs)'],
  },
  {
    id: 'workload',
    label: 'Workload type',
    type: 'checkbox',
    options: ['Traditional VMs', 'Containers / OKE (Kubernetes)', 'Functions / Serverless', 'HPC / GPU'],
  },
  {
    id: 'data',
    label: 'Data classification',
    type: 'radio',
    options: ['Public', 'Internal', 'Confidential', 'Restricted / Regulated'],
  },
  {
    id: 'compliance',
    label: 'Compliance requirements (if any)',
    type: 'text',
    placeholder: 'e.g. PCI-DSS, HIPAA, SOC 2, ISO 27001 — or leave blank',
  },
  {
    id: 'scale',
    label: 'Expected scale',
    type: 'text',
    placeholder: 'e.g. 500 concurrent users, 10 000 TPS, 50 TB data',
  },
  {
    id: 'notes',
    label: 'Additional requirements or constraints',
    type: 'text',
    placeholder: 'Anything else the architect should know',
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────
/**
 * Always emit all questionnaire questions.
 * Pre-filled answers are included as-is; blank items get "[infer from BOM or ask]"
 * so the LLM knows to try to infer rather than silently skip.
 */
function formatQuestionnaire(answers: Record<string, string | string[]>): string {
  const lines: string[] = [
    'ARCHITECTURE QUESTIONNAIRE:',
    '(Use the BOM and context to answer each item where possible. Only request clarification for items you genuinely cannot determine.)',
  ];
  for (const q of QUESTIONNAIRE) {
    const ans = answers[q.id];
    const val = Array.isArray(ans) ? ans.join(', ') : (ans || '');
    lines.push(`  ${q.label}: ${val || '[infer from BOM or ask]'}`);
  }
  return lines.join('\n');
}

// ── Static styles ─────────────────────────────────────────────────────────
const s = {
  section: {
    background: T.card,
    border: `1px solid ${T.border}`,
    borderRadius: 6,
    padding: '1rem 1.25rem',
    marginBottom: '0.75rem',
  } as React.CSSProperties,
  sectionTitle: {
    fontFamily: T.display,
    fontSize: '0.7rem',
    fontWeight: 700,
    letterSpacing: '0.15em',
    textTransform: 'uppercase' as const,
    color: T.label,
    marginBottom: '0.75rem',
  } as React.CSSProperties,
  row: {
    display: 'flex',
    gap: '0.75rem',
    marginBottom: '0.75rem',
    flexWrap: 'wrap' as const,
  } as React.CSSProperties,
  fieldWrap: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '0.3rem',
    flex: 1,
    minWidth: 180,
  } as React.CSSProperties,
  label: {
    fontSize: '0.7rem',
    fontWeight: 600,
    letterSpacing: '0.06em',
    color: T.label,
    textTransform: 'uppercase' as const,
  } as React.CSSProperties,
  input: {
    background: T.surface,
    color: T.text,
    border: `1px solid ${T.border}`,
    borderRadius: 4,
    padding: '6px 10px',
    fontFamily: T.font,
    fontSize: '0.8rem',
    outline: 'none',
    width: '100%',
  } as React.CSSProperties,
  radioGroup: {
    display: 'flex',
    flexWrap: 'wrap' as const,
    gap: '0.35rem',
  } as React.CSSProperties,
  qSection: {
    background: T.surface,
    border: `1px solid ${T.border}`,
    borderRadius: 6,
    marginBottom: '0.75rem',
    overflow: 'hidden',
  } as React.CSSProperties,
  qHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '0.6rem 1rem',
    cursor: 'pointer',
    background: T.card,
    borderBottom: `1px solid ${T.border}`,
  } as React.CSSProperties,
  qTitle: {
    fontSize: '0.72rem',
    fontWeight: 700,
    letterSpacing: '0.1em',
    textTransform: 'uppercase' as const,
    color: T.text,
  } as React.CSSProperties,
  qBody: {
    padding: '0.75rem 1rem',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '0.8rem',
  } as React.CSSProperties,
  qLabel: {
    fontSize: '0.72rem',
    color: T.label,
    fontWeight: 600,
    letterSpacing: '0.05em',
    marginBottom: '0.25rem',
  } as React.CSSProperties,
};

// ── Dynamic style functions ────────────────────────────────────────────────
function pillStyle(active: boolean): React.CSSProperties {
  return {
    padding: '4px 10px',
    borderRadius: 4,
    border: `1px solid ${active ? T.accent : T.border}`,
    background: active ? T.accentG : T.surface,
    color: active ? T.accent : T.text,
    fontSize: '0.72rem',
    cursor: 'pointer',
    userSelect: 'none',
    fontFamily: T.font,
    transition: 'all 0.15s',
  };
}

function submitBtnStyle(loading: boolean): React.CSSProperties {
  return {
    background: loading ? T.muted : T.accent,
    color: '#fff',
    border: 'none',
    borderRadius: 4,
    padding: '10px 24px',
    fontFamily: T.font,
    fontSize: '0.82rem',
    fontWeight: 700,
    letterSpacing: '0.08em',
    cursor: loading ? 'default' : 'pointer',
    opacity: loading ? 0.6 : 1,
    transition: 'all 0.15s',
  };
}

function toggleBtnStyle(active: boolean): React.CSSProperties {
  return {
    background: active ? T.accentG : 'transparent',
    color: active ? T.accent : T.muted,
    border: `1px solid ${active ? T.accent : T.border}`,
    borderRadius: 4,
    padding: '4px 12px',
    fontFamily: T.font,
    fontSize: '0.72rem',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 0.15s',
  };
}

// ── Props ─────────────────────────────────────────────────────────────────
interface Props {
  clientId:             string;
  customerId:           string;
  diagramName:          string;
  onCustomerIdChange:   (id: string) => void;
  onDiagramNameChange:  (name: string) => void;
  onResult:             (r: GenerateResponse | OrchestrationResult) => void;
  onError:              (msg: string) => void;
}

// ── Component ─────────────────────────────────────────────────────────────
export function UploadBom({
  customerId,
  diagramName,
  onCustomerIdChange,
  onDiagramNameChange,
  onResult,
  onError,
}: Props) {
  // Drag-and-drop state
  const [droppedBom,    setDroppedBom]    = useState<File | null>(null);
  const [droppedCtx,    setDroppedCtx]    = useState<File | null>(null);
  const [ctxContent,    setCtxContent]    = useState<string>('');   // actual file text
  const [dragOver,      setDragOver]      = useState<'bom' | 'ctx' | null>(null);
  const [uploadStatus,  setUploadStatus]  = useState<string>('');
  const [bomType,       setBomType]       = useState<'main' | 'poc'>('main');
  const bomInputRef = useRef<HTMLInputElement>(null);
  const ctxInputRef = useRef<HTMLInputElement>(null);

  // Bucket-path fallback (used when no file dropped)
  const [bomFile,     setBomFile]     = useState('oci_bom_priced.xlsx');
  const [ctxFile,     setCtxFile]     = useState('');

  const [buildOnPrior, setBuildOnPrior] = useState(false);
  const [priorNotes,  setPriorNotes]  = useState('');
  const [qOpen,       setQOpen]       = useState(true);
  const [qAnswers,    setQAnswers]    = useState<Record<string, string | string[]>>({});
  const [loading,     setLoading]     = useState(false);
  const [autoWaf,     setAutoWaf]     = useState(false);
  const [customerName, setCustomerName] = useState('');

  /** Read a text file in the browser and update ctxContent state. */
  function readCtxFile(file: File) {
    setDroppedCtx(file);
    setCtxFile(file.name);
    const reader = new FileReader();
    reader.onload = (ev) => setCtxContent((ev.target?.result as string) ?? '');
    reader.readAsText(file);
  }

  // ── Drag-and-drop handlers ──────────────────────────────────────────────
  const onDrop = useCallback((e: React.DragEvent, zone: 'bom' | 'ctx') => {
    e.preventDefault();
    setDragOver(null);
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    if (zone === 'bom') { setDroppedBom(file); setBomFile(file.name); }
    else                { readCtxFile(file); }
  }, []);

  // ── Questionnaire helpers ───────────────────────────────────────────────
  function setRadio(id: string, val: string) {
    setQAnswers(prev => ({ ...prev, [id]: val }));
  }

  function toggleCheckbox(id: string, val: string) {
    setQAnswers(prev => {
      const cur = (prev[id] as string[] | undefined) ?? [];
      return {
        ...prev,
        [id]: cur.includes(val) ? cur.filter(x => x !== val) : [...cur, val],
      };
    });
  }

  function setTextAnswer(id: string, val: string) {
    setQAnswers(prev => ({ ...prev, [id]: val }));
  }

  // ── Build full context string ───────────────────────────────────────────
  function buildContext(): string {
    const parts: string[] = [];

    // Include actual context file content if the file was dropped/selected in the browser.
    // If only a bucket filename was typed (no file object), pass the path reference so the
    // server can fetch it from OCI Object Storage.
    if (ctxContent.trim()) {
      parts.push(`CONTEXT DOCUMENT:\n${ctxContent.trim()}`);
    } else if (!droppedCtx && ctxFile.trim()) {
      parts.push(`CONTEXT_FILE: ${DEFAULT_PREFIX}/${customerId}/${ctxFile.trim()}`);
    }

    parts.push(formatQuestionnaire(qAnswers));

    if (buildOnPrior && priorNotes.trim()) {
      parts.push(`EXISTING DEPLOYMENT:\n  ${priorNotes.trim()}`);
    } else if (buildOnPrior) {
      parts.push('EXISTING DEPLOYMENT: true — building on prior architecture version');
    }

    return parts.join('\n\n');
  }

  // ── Submit ──────────────────────────────────────────────────────────────
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    if (!customerId.trim()) {
      onError('Customer ID is required — it defines the bucket folder.');
      return;
    }
    if (!droppedBom && !bomFile.trim()) {
      onError('Drop a BOM file or enter a BOM filename.');
      return;
    }
    if (autoWaf && !droppedBom) {
      onError('Auto WAF Review requires a BOM file to be dropped directly (not from bucket path only).');
      return;
    }

    setLoading(true);
    setUploadStatus('');
    try {
      if (autoWaf && droppedBom) {
        // ── Direct upload path (required for auto_waf) ────────────────────
        setUploadStatus('Generating diagram + WAF review loop (60–90 s)…');
        const context = buildContext();
        const fd = new FormData();
        fd.append('file', droppedBom);
        if (droppedCtx) fd.append('context_file', droppedCtx);
        fd.append('context',       context);
        fd.append('diagram_name',  diagramName || 'oci_architecture');
        fd.append('client_id',     customerId.trim());
        fd.append('customer_id',   customerId.trim());
        fd.append('customer_name', customerName.trim());
        fd.append('auto_waf',      'true');
        const result = await apiUploadBom(fd);
        setUploadStatus('');
        onResult(result);
      } else {
        // ── A2A path (default — bucket-side fetch) ────────────────────────
        if (droppedBom) {
          setUploadStatus('Uploading BOM to bucket…');
          await apiUploadToBucket(customerId.trim(), droppedBom, bomType);
        }
        if (droppedCtx) {
          setUploadStatus('Uploading context file…');
          await apiUploadToBucket(customerId.trim(), droppedCtx, bomType);
        }

        setUploadStatus('Generating diagram…');
        const bomObject = `${DEFAULT_PREFIX}/${customerId.trim()}/${droppedBom ? droppedBom.name : bomFile.trim()}`;
        const context   = buildContext();
        const result = await apiA2AUploadBom(
          customerId.trim(),
          bomObject,
          diagramName || 'oci_architecture',
          context || undefined,
          DEFAULT_NAMESPACE,
          DEFAULT_BUCKET,
        );
        setUploadStatus('');
        onResult(result);
      }
    } catch (err: unknown) {
      setUploadStatus('');
      const e = err as ApiError;
      onError(`${e.status}: ${e.detail}`);
    } finally {
      setLoading(false);
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <form onSubmit={handleSubmit} data-testid="upload-bom-form" style={{ fontFamily: T.font }}>

      {/* ── Customer + BOM location ─────────────────────────────────────── */}
      <div style={s.section}>
        <div style={s.sectionTitle}>Customer &amp; BOM Location</div>

        <div style={s.row}>
          <div style={s.fieldWrap}>
            <label style={s.label}>Customer ID</label>
            <input
              style={s.input}
              type="text"
              value={customerId}
              onChange={e => onCustomerIdChange(e.target.value)}
              placeholder="e.g. maurits, acme-corp"
              required
            />
            <span style={{ fontSize: '0.65rem', color: T.label }}>
              Bucket folder: <code style={{ color: T.accent }}>{DEFAULT_PREFIX}/{customerId || '<customer>'}/</code>
            </span>
          </div>

          <div style={s.fieldWrap}>
            <label style={s.label}>Diagram Name</label>
            <input
              style={s.input}
              type="text"
              value={diagramName}
              onChange={e => onDiagramNameChange(e.target.value)}
              placeholder="oci_architecture"
            />
          </div>
        </div>

        {/* ── BOM type + Auto WAF toggles ─────────────────────────────────── */}
        <div style={{ marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span style={s.label}>BOM type</span>
          <span
            style={pillStyle(bomType === 'main')}
            onClick={() => setBomType('main')}
          >
            Main (production)
          </span>
          <span
            style={pillStyle(bomType === 'poc')}
            onClick={() => setBomType('poc')}
          >
            POC / JEP
          </span>
          <span style={{ fontSize: '0.65rem', color: T.label, marginRight: '0.75rem' }}>
            POC uploads to <code style={{ color: T.accent }}>agent3/{customerId || '<customer>'}/poc/</code>
          </span>
          <span
            style={pillStyle(autoWaf)}
            onClick={() => setAutoWaf(v => !v)}
            title="Run WAF topology quality-gate loop after diagram generation"
          >
            {autoWaf ? '✓ Auto WAF Review' : 'Auto WAF Review'}
          </span>
          {autoWaf && (
            <span style={{ fontSize: '0.65rem', color: T.gold }}>
              requires dropped BOM file · adds ~60–90 s
            </span>
          )}
        </div>

        {/* ── Customer Name (shown when Auto WAF enabled) ──────────────────── */}
        {autoWaf && (
          <div style={{ ...s.fieldWrap, marginBottom: '0.75rem' }}>
            <label style={s.label}>Customer Name <span style={{ color: T.label }}>(for WAF document headings)</span></label>
            <input
              style={s.input}
              type="text"
              value={customerName}
              onChange={e => setCustomerName(e.target.value)}
              placeholder="e.g. Jane Street Capital"
            />
          </div>
        )}

        {/* ── Drop zones ─────────────────────────────────────────────────── */}
        <div style={s.row}>
          {/* BOM drop zone */}
          <div style={s.fieldWrap}>
            <label style={s.label}>BOM File (.xlsx)</label>
            <div
              style={{
                border: `2px dashed ${dragOver === 'bom' ? T.accent : droppedBom ? T.green : T.border}`,
                borderRadius: 6,
                padding: '1rem',
                textAlign: 'center',
                cursor: 'pointer',
                background: dragOver === 'bom' ? T.accentG : droppedBom ? 'rgba(46,204,138,0.06)' : T.surface,
                transition: 'all 0.15s',
                fontSize: '0.75rem',
                color: droppedBom ? T.green : T.label,
              }}
              onDragOver={e => { e.preventDefault(); setDragOver('bom'); }}
              onDragLeave={() => setDragOver(null)}
              onDrop={e => onDrop(e, 'bom')}
              onClick={() => bomInputRef.current?.click()}
            >
              {droppedBom
                ? `✓ ${droppedBom.name} (${(droppedBom.size / 1024).toFixed(0)} KB)`
                : 'Drop BOM here or click to browse'}
              <input
                ref={bomInputRef}
                type="file"
                accept=".xlsx,.xls"
                style={{ display: 'none' }}
                onChange={e => { const f = e.target.files?.[0]; if (f) { setDroppedBom(f); setBomFile(f.name); } }}
              />
            </div>
            <span style={{ fontSize: '0.65rem', color: T.muted, marginTop: 2 }}>
              or enter filename below if already in bucket
            </span>
            <input
              style={{ ...s.input, marginTop: 2 }}
              type="text"
              value={bomFile}
              onChange={e => setBomFile(e.target.value)}
              placeholder="oci_bom_priced.xlsx"
            />
          </div>

          {/* Context drop zone */}
          <div style={s.fieldWrap}>
            <label style={s.label}>Context / Notes <span style={{ color: T.label }}>(optional)</span></label>
            <div
              style={{
                border: `2px dashed ${dragOver === 'ctx' ? T.accent : droppedCtx ? T.green : T.border}`,
                borderRadius: 6,
                padding: '1rem',
                textAlign: 'center',
                cursor: 'pointer',
                background: dragOver === 'ctx' ? T.accentG : droppedCtx ? 'rgba(46,204,138,0.06)' : T.surface,
                transition: 'all 0.15s',
                fontSize: '0.75rem',
                color: droppedCtx ? T.green : T.label,
              }}
              onDragOver={e => { e.preventDefault(); setDragOver('ctx'); }}
              onDragLeave={() => setDragOver(null)}
              onDrop={e => onDrop(e, 'ctx')}
              onClick={() => ctxInputRef.current?.click()}
            >
              {droppedCtx
                ? `✓ ${droppedCtx.name}`
                : 'Drop context file here or click'}
              <input
                ref={ctxInputRef}
                type="file"
                style={{ display: 'none' }}
                onChange={e => { const f = e.target.files?.[0]; if (f) readCtxFile(f); }}
              />
            </div>
            <span style={{ fontSize: '0.65rem', color: T.muted, marginTop: 2 }}>
              or enter filename if already in bucket
            </span>
            <input
              style={{ ...s.input, marginTop: 2 }}
              type="text"
              value={ctxFile}
              onChange={e => setCtxFile(e.target.value)}
              placeholder="requirements.md or notes.txt"
            />
          </div>
        </div>
      </div>

      {/* ── Prior version ───────────────────────────────────────────────── */}
      <div style={s.section}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: buildOnPrior ? '0.75rem' : 0 }}>
          <button
            type="button"
            style={toggleBtnStyle(buildOnPrior)}
            onClick={() => setBuildOnPrior(v => !v)}
          >
            {buildOnPrior ? '✓ Building on existing deployment' : 'Building on existing deployment?'}
          </button>
          <span style={{ fontSize: '0.65rem', color: T.label }}>
            Toggle if extending an existing OCI architecture
          </span>
        </div>
        {buildOnPrior && (
          <div style={s.fieldWrap}>
            <label style={s.label}>Existing architecture notes or version reference</label>
            <textarea
              style={{ ...s.input, resize: 'vertical' as const, minHeight: 64 }}
              value={priorNotes}
              onChange={e => setPriorNotes(e.target.value)}
              placeholder="e.g. v2 diagram, 3-tier web with existing DB subnet 10.0.3.0/24"
              rows={3}
            />
          </div>
        )}
      </div>

      {/* ── Architecture questionnaire ───────────────────────────────────── */}
      <div style={s.qSection}>
        <div style={s.qHeader} onClick={() => setQOpen(v => !v)}>
          <span style={s.qTitle}>Architecture Questionnaire</span>
          <span style={{ fontSize: '0.65rem', color: T.label }}>
            {qOpen ? '▲ collapse' : '▼ expand'} — fill in what you know; AI infers the rest
          </span>
        </div>

        {qOpen && (
          <div style={s.qBody}>
            {QUESTIONNAIRE.map(q => (
              <div key={q.id}>
                <div style={s.qLabel}>{q.label}</div>

                {q.type === 'radio' && q.options && (
                  <div style={s.radioGroup}>
                    {q.options.map(opt => {
                      const active = qAnswers[q.id] === opt;
                      return (
                        <span
                          key={opt}
                          style={pillStyle(active)}
                          onClick={() => setRadio(q.id, active ? '' : opt)}
                        >
                          {opt}
                        </span>
                      );
                    })}
                  </div>
                )}

                {q.type === 'checkbox' && q.options && (
                  <div style={s.radioGroup}>
                    {q.options.map(opt => {
                      const cur = (qAnswers[q.id] as string[] | undefined) ?? [];
                      const active = cur.includes(opt);
                      return (
                        <span
                          key={opt}
                          style={pillStyle(active)}
                          onClick={() => toggleCheckbox(q.id, opt)}
                        >
                          {opt}
                        </span>
                      );
                    })}
                  </div>
                )}

                {q.type === 'text' && (
                  <input
                    style={s.input}
                    type="text"
                    value={(qAnswers[q.id] as string | undefined) ?? ''}
                    onChange={e => setTextAnswer(q.id, e.target.value)}
                    placeholder={q.placeholder}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Bucket path preview ──────────────────────────────────────────── */}
      <div style={{ marginBottom: '0.75rem', fontSize: '0.68rem', color: T.label, lineHeight: 1.7 }}>
        <span style={{ color: T.text }}>Source: </span>
        <code style={{ color: T.accent }}>
          oci://{DEFAULT_BUCKET}/{DEFAULT_PREFIX}/{customerId || '<customer>'}/{bomType === 'poc' ? 'poc/' : ''}{bomFile || '<bom.xlsx>'}
        </code>
        <br />
        <span style={{ color: T.text }}>Output: </span>
        <code style={{ color: T.green }}>
          oci://{DEFAULT_BUCKET}/{DEFAULT_PREFIX}/{customerId || '<customer>'}/{bomType === 'poc' ? 'poc/' : ''}diagram.drawio
        </code>
      </div>

      {uploadStatus && (
        <div style={{ marginBottom: '0.5rem', fontSize: '0.75rem', color: T.gold }}>
          ⏳ {uploadStatus}
        </div>
      )}

      <button type="submit" style={submitBtnStyle(loading)} disabled={loading}>
        {loading
          ? (uploadStatus || 'Working…')
          : autoWaf
            ? (droppedBom ? 'Upload, Generate & WAF Review' : 'Generate & WAF Review')
            : (droppedBom ? 'Upload & Generate Diagram' : 'Generate Architecture Diagram')}
      </button>
    </form>
  );
}
