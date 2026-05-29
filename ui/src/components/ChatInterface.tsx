/**
 * ChatInterface.tsx
 * ------------------
 * Conversational orchestrator chat UI.
 * Renders a scrollable message thread with tool chips, artifact links,
 * file attach, and block-aware markdown rendering.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  apiChat,
  apiChatBackground,
  apiChatStream,
  apiGetChatJob,
  apiGetChatHistory,
  apiClearChatHistory,
  apiUploadNote,
  apiGetLatestPov,
  apiGetLatestJep,
  apiGetLatestWaf,
  type ChatMessage,
  type ChatArtifactDownload,
  type ChatToolCall,
  type ChatArtifactManifest,
} from '../api/client';

interface QuickAction {
  command: string;
  label: string;
  tone: 'primary' | 'secondary';
}

const ARCHIE_HELLO_MESSAGES = [
  "Hi, I'm Archie. Bring me a customer, a note dump, or a rough idea and I'll help turn it into useful architecture work.",
  "Archie here. I can help sort notes, draft diagrams, prep BOMs, and keep the follow-up work moving.",
  "Hello from Archie. Drop in what you know, even if it is messy, and I will help structure the next step.",
  "Hi, I'm Archie. Ready when you are with meeting notes, architecture questions, or a half-formed plan.",
];

const ARCHIE_WORKING_MESSAGES = [
  'Archie is chewing on it...',
  'Archie is pawing it over...',
  'Archie is sniffing out the architecture trail...',
  'Archie is padding through the details...',
  'Archie is sorting the honey from the noise...',
];

function messageSeed(value: string): number {
  return Array.from(value).reduce((acc, char) => acc + char.charCodeAt(0), 0);
}

function pickArchieMessage(messages: string[], seed: string): string {
  if (messages.length === 0) return '';
  return messages[messageSeed(seed) % messages.length];
}

// ── Markdown renderer ─────────────────────────────────────────────────────────

/** Block-aware Markdown → HTML: headings, lists, hr, bold, italic, inline code, fenced code, tables. */
function mdToHtml(md: string): string {
  const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const inline = (s: string) =>
    escape(s)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code class="archie-code-inline">$1</code>');

  // Split out fenced code blocks first
  const parts: Array<{ type: 'code' | 'text'; content: string; lang?: string }> = [];
  const fence = /^```(\w*)\n?([\s\S]*?)^```/gm;
  let lastIdx = 0;
  let m: RegExpExecArray | null;
  while ((m = fence.exec(md)) !== null) {
    if (m.index > lastIdx) parts.push({ type: 'text', content: md.slice(lastIdx, m.index) });
    parts.push({ type: 'code', content: m[2] ?? '', lang: m[1] || undefined });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < md.length) parts.push({ type: 'text', content: md.slice(lastIdx) });
  if (parts.length === 0) parts.push({ type: 'text', content: md });

  const out: string[] = [];

  for (const seg of parts) {
    if (seg.type === 'code') {
      const langAttr = seg.lang ? ` data-lang="${escape(seg.lang)}"` : '';
      out.push(`<pre class="archie-code-block"${langAttr}><code>${escape(seg.content.trimEnd())}</code></pre>`);
      continue;
    }

    const lines = seg.content.split('\n');
    let inUl = false, inOl = false;
    let tableRows: string[][] = [];

    const flushTable = () => {
      if (tableRows.length === 0) return;
      out.push('<table class="archie-table">');
      let bodyStarted = false;
      tableRows.forEach((cells, idx) => {
        const isSep = cells.every(c => /^[-: ]+$/.test(c.trim()));
        if (idx === 0) {
          out.push('<thead><tr>' + cells.map(c => `<th>${inline(c.trim())}</th>`).join('') + '</tr></thead>');
        } else if (!isSep) {
          if (!bodyStarted) { out.push('<tbody>'); bodyStarted = true; }
          out.push('<tr>' + cells.map(c => `<td>${inline(c.trim())}</td>`).join('') + '</tr>');
        }
      });
      if (bodyStarted) out.push('</tbody>');
      out.push('</table>');
      tableRows = [];
    };

    const closeList = () => {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (inOl) { out.push('</ol>'); inOl = false; }
    };

    for (const raw of lines) {
      const line = raw.trimEnd();
      if (line.startsWith('|') && line.includes('|', 1)) {
        closeList();
        tableRows.push(line.replace(/^\||\|$/g, '').split('|'));
        continue;
      } else if (tableRows.length > 0) {
        flushTable();
      }
      if (/^---+$/.test(line)) { closeList(); out.push('<hr class="archie-hr"/>'); }
      else if (/^# /.test(line)) { closeList(); out.push(`<h1 class="archie-h1">${inline(line.slice(2))}</h1>`); }
      else if (/^## /.test(line)) { closeList(); out.push(`<h2 class="archie-h2">${inline(line.slice(3))}</h2>`); }
      else if (/^### /.test(line)) { closeList(); out.push(`<h3 class="archie-h3">${inline(line.slice(4))}</h3>`); }
      else if (/^[-*] /.test(line)) {
        if (inOl) { out.push('</ol>'); inOl = false; }
        if (!inUl) { out.push('<ul class="archie-ul">'); inUl = true; }
        out.push(`<li>${inline(line.slice(2))}</li>`);
      } else if (/^\d+\. /.test(line)) {
        if (inUl) { out.push('</ul>'); inUl = false; }
        if (!inOl) { out.push('<ol class="archie-ol">'); inOl = true; }
        out.push(`<li>${inline(line.replace(/^\d+\. /, ''))}</li>`);
      } else {
        closeList();
        out.push(line === '' ? '<br/>' : `<span>${inline(line)}</span><br/>`);
      }
    }
    flushTable();
    closeList();
  }
  return out.join('');
}

// ── Sub-components ────────────────────────────────────────────────────────────

const TOOL_LABELS: Record<string, string> = {
  generate_bom:       'BOM Generator',
  generate_diagram:   'Architecture Diagram',
  generate_pov:       'POV Document',
  generate_jep:       'JEP Document',
  generate_waf:       'WAF Review',
  generate_terraform: 'Terraform',
  save_notes:         'Save Notes',
  get_summary:        'Get Summary',
  get_document:       'Get Document',
};

function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool.replace(/_/g, ' ');
}

function toolRequestText(call: ChatToolCall): string {
  const a = call.args ?? {};
  for (const key of ['prompt', 'feedback', 'bom_text', 'text', 'type']) {
    const v = a[key];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return '';
}

function ToolChip({ call }: { call: ChatToolCall }) {
  const [open, setOpen] = useState(false);
  const trace    = (call.result_data?.trace ?? {}) as Record<string, unknown>;
  const governor = (trace.governor ?? {}) as Record<string, unknown>;
  const checkpoint = (trace.checkpoint ?? null) as Record<string, unknown> | null;

  const refinements  = typeof trace.refinement_count === 'number' ? trace.refinement_count : 0;
  const overallPass  = typeof trace.overall_pass === 'boolean' ? trace.overall_pass : true;
  const skills       = Array.isArray(trace.applied_skills) ? (trace.applied_skills as string[]) : [];
  const warnings     = Array.isArray(trace.warnings) ? (trace.warnings as string[]) : [];
  const modelProfile = typeof trace.model_profile === 'string' ? trace.model_profile : '';
  const govStatus    = typeof governor.overall_status === 'string' ? governor.overall_status : '';
  const checkType    = checkpoint && typeof checkpoint.type === 'string' ? checkpoint.type : '';

  const hasIssue = !overallPass || warnings.length > 0 || govStatus === 'blocked' || govStatus === 'checkpoint_required';
  const statusColor = hasIssue ? '#f0b35a' : '#52c46a';
  const statusIcon  = hasIssue ? '⚠' : '✓';

  const requestText = toolRequestText(call);
  const previewText = call.result_summary
    ? (call.result_summary.length > 120 ? call.result_summary.slice(0, 120) + '…' : call.result_summary)
    : '';

  const blockStyle: React.CSSProperties = {
    marginTop:    '0.45rem',
    border:       '1px solid #1c2030',
    borderRadius: 6,
    overflow:     'hidden',
    fontFamily:   "'JetBrains Mono', monospace",
  };
  const headerStyle: React.CSSProperties = {
    display:        'flex',
    alignItems:     'center',
    gap:            '0.45rem',
    padding:        '0.35rem 0.6rem',
    background:     'rgba(232,87,26,0.06)',
    cursor:         'pointer',
    userSelect:     'none',
    borderBottom:   open ? '1px solid #1c2030' : 'none',
  };
  const sectionLabel: React.CSSProperties = {
    fontSize:      '0.6rem',
    letterSpacing: '0.08em',
    color:         '#4b5470',
    textTransform: 'uppercase',
    marginBottom:  '0.2rem',
  };
  const codeBlock: React.CSSProperties = {
    margin:      0,
    padding:     '0.45rem 0.6rem',
    background:  '#0b0d14',
    fontSize:    '0.67rem',
    color:       '#8b93a8',
    whiteSpace:  'pre-wrap',
    wordBreak:   'break-word',
    borderRadius: 0,
  };

  return (
    <div data-testid={`tool-chip-${call.tool}`} style={blockStyle}>
      {/* ── header row ── */}
      <div style={headerStyle} onClick={() => setOpen(v => !v)}>
        <span style={{ color: statusColor, fontSize: '0.7rem' }}>{statusIcon}</span>
        <span style={{ fontSize: '0.72rem', color: '#c9d1e4', fontWeight: 600 }}>
          Used {toolLabel(call.tool)}
        </span>
        {refinements > 0 && (
          <span style={{ fontSize: '0.62rem', color: '#6b7491' }}>
            · {refinements} refinement{refinements !== 1 ? 's' : ''}
          </span>
        )}
        {checkType && (
          <span style={{ fontSize: '0.62rem', color: '#f0b35a' }}>· checkpoint</span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: '0.62rem', color: '#4b5470' }}>
          {open ? '▲' : '▼'}
        </span>
      </div>

      {/* ── result preview (always visible) ── */}
      {previewText && (
        <div style={{ padding: '0.3rem 0.6rem', fontSize: '0.7rem', color: '#6b7491', fontStyle: 'italic' }}>
          {previewText}
        </div>
      )}

      {/* ── expanded detail ── */}
      {open && (
        <div style={{ borderTop: '1px solid #1c2030' }}>
          {requestText && (
            <div style={{ padding: '0.45rem 0.6rem' }}>
              <div style={sectionLabel}>Request</div>
              <pre style={codeBlock}>{requestText.length > 600 ? requestText.slice(0, 600) + '\n…' : requestText}</pre>
            </div>
          )}

          {call.result_summary && (
            <div style={{ padding: '0.45rem 0.6rem', borderTop: '1px solid #131726' }}>
              <div style={sectionLabel}>Response</div>
              <pre style={{ ...codeBlock, color: '#aeb5c8' }}>{call.result_summary}</pre>
            </div>
          )}

          {(skills.length > 0 || modelProfile || warnings.length > 0 || govStatus) && (
            <div style={{ padding: '0.45rem 0.6rem', borderTop: '1px solid #131726' }}>
              <div style={sectionLabel}>Expert review</div>
              <div style={{ fontSize: '0.67rem', color: '#8b93a8', display: 'flex', flexDirection: 'column', gap: '0.18rem' }}>
                {skills.length > 0 && (
                  <span data-testid="tool-trace-summary">skills: {skills.join(', ')}</span>
                )}
                {modelProfile && <span>model: {modelProfile}</span>}
                {typeof trace.overall_pass === 'boolean' && (
                  <span style={{ color: overallPass ? '#52c46a' : '#f0b35a' }}>
                    verdict: {overallPass ? 'approved' : 'iterate'}
                  </span>
                )}
                {warnings.length > 0 && (
                  <span style={{ color: '#f0b35a' }}>warnings: {warnings.join(' · ')}</span>
                )}
                {govStatus && <span style={{ color: '#f0b35a' }}>governor: {govStatus}</span>}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function traceWarningsForCall(call: ChatToolCall): string[] {
  const trace = (call.result_data?.trace ?? {}) as Record<string, unknown>;
  const warnings = Array.isArray(trace.warnings) ? trace.warnings : [];
  const governor = (trace.governor ?? {}) as Record<string, unknown>;
  const cost = (governor.cost ?? {}) as Record<string, unknown>;
  const security = (governor.security ?? {}) as Record<string, unknown>;
  const derived: string[] = [];
  if (typeof governor.overall_status === 'string' && governor.overall_status === 'checkpoint_required') {
    derived.push('checkpoint_required');
  }
  if (typeof governor.overall_status === 'string' && governor.overall_status === 'blocked') {
    derived.push('governor_blocked');
  }
  if (typeof cost.variance === 'number') {
    derived.push(`budget_variance_${cost.variance}`);
  }
  if (Array.isArray(security.findings) && security.findings.length > 0) {
    derived.push('security_findings');
  }
  return [...warnings, ...derived]
    .filter((warning): warning is string => typeof warning === 'string')
    .map(warning => warning.trim())
    .filter(Boolean);
}

function ArtifactLink({ toolName, artifactKey, customerId }: {
  toolName: string; artifactKey: string; customerId: string;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const docType =
    toolName === 'generate_pov' ? 'pov' :
    toolName === 'generate_jep' ? 'jep' :
    toolName === 'generate_waf' ? 'waf' : null;

  async function handleView() {
    if (content !== null) { setContent(null); return; }
    setLoading(true);
    try {
      let text = '';
      if (docType === 'pov')      text = (await apiGetLatestPov(customerId)).content;
      else if (docType === 'jep') text = (await apiGetLatestJep(customerId)).content;
      else if (docType === 'waf') text = (await apiGetLatestWaf(customerId)).content;
      setContent(text);
    } catch { setContent('(failed to load)'); }
    finally { setLoading(false); }
  }

  const btnStyle: React.CSSProperties = {
    fontSize:   '0.7rem',
    color:      '#e8571a',
    background: 'rgba(232,87,26,0.08)',
    border:     '1px solid rgba(232,87,26,0.25)',
    borderRadius: 4,
    padding:    '0.2rem 0.5rem',
    cursor:     'pointer',
    fontFamily: "'JetBrains Mono', monospace",
  };

  if (docType) {
    const label = docType.toUpperCase();
    return (
      <div style={{ marginTop: '0.25rem' }}>
        <button onClick={handleView} disabled={loading} style={btnStyle}>
          {loading ? '…' : content ? `▲ Hide ${label}` : `📄 View ${label}`}
        </button>
        {content && (
          <pre style={{
            marginTop:    '0.35rem',
            padding:      '0.6rem',
            background:   '#0b0d14',
            border:       '1px solid #1c2030',
            borderRadius: 4,
            fontSize:     '0.68rem',
            color:        '#cdd2e0',
            whiteSpace:   'pre-wrap',
            wordBreak:    'break-word',
            maxHeight:    '16rem',
            overflowY:    'auto',
          }}>{content}</pre>
        )}
      </div>
    );
  }

  return (
    <div style={{ fontSize: '0.7rem', color: '#8b93a8', marginTop: '0.25rem' }}>
      📎 {toolName}: <code style={{ color: '#e8571a' }}>{artifactKey}</code>
    </div>
  );
}

function ArtifactManifestLinks({ manifest }: { manifest?: ChatArtifactManifest }) {
  if (!manifest || !Array.isArray(manifest.downloads) || manifest.downloads.length === 0) {
    return null;
  }
  return (
    <div style={{ marginTop: '0.3rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      {manifest.downloads.map((dl, idx) => (
        <a
          key={`${dl.type}-${dl.filename ?? dl.tool}-${idx}`}
          href={dl.download_url}
          data-testid={`artifact-link-${dl.type}-${dl.filename ?? 'artifact'}`}
          target="_blank"
          rel="noreferrer"
          style={{
            fontSize: '0.7rem',
            color: '#e8571a',
            textDecoration: 'none',
            border: '1px solid rgba(232,87,26,0.25)',
            background: 'rgba(232,87,26,0.08)',
            borderRadius: 4,
            padding: '0.2rem 0.5rem',
            width: 'fit-content',
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {dl.type === 'terraform'
            ? `Download Terraform: ${dl.filename ?? 'file'}`
            : `Download ${dl.type}: ${dl.filename ?? 'artifact'}`}
        </a>
      ))}
    </div>
  );
}

function numericValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

function bomTotalFromCall(call: ChatToolCall): number | null {
  const data = call.result_data ?? {};
  const payload = (data.bom_payload ?? data.payload) as Record<string, unknown> | undefined;
  const totals = payload?.totals as Record<string, unknown> | undefined;
  return numericValue(payload?.monthly_total)
    ?? numericValue(totals?.estimated_monthly_cost)
    ?? numericValue(data.estimated_monthly_cost);
}

function wafScoreFromCall(call: ChatToolCall): string {
  const data = call.result_data ?? {};
  const pillars = data.pillars as Record<string, unknown> | undefined;
  const overall = numericValue(data.overall_score ?? data.score);
  if (overall !== null) return `${overall.toFixed(1)}/5`;
  if (!pillars || typeof pillars !== 'object') return '';
  const scores = Object.values(pillars)
    .map(item => numericValue((item as Record<string, unknown>)?.score))
    .filter((score): score is number => score !== null);
  if (scores.length === 0) return '';
  const avg = scores.reduce((sum, score) => sum + score, 0) / scores.length;
  return `${avg.toFixed(1)}/5`;
}

function ArtifactHighlights({
  toolCalls,
  artifactManifest,
}: {
  toolCalls?: ChatToolCall[];
  artifactManifest?: ChatArtifactManifest;
}) {
  const highlights: Array<{ key: string; label: string }> = [];
  if (artifactManifest?.downloads?.some(dl => (dl.filename ?? dl.key ?? '').endsWith('.drawio'))) {
    highlights.push({ key: 'diagram', label: 'Diagram ready' });
  }
  for (const call of toolCalls ?? []) {
    const total = bomTotalFromCall(call);
    if (total !== null) {
      highlights.push({
        key: `bom-${call.tool}`,
        label: `BOM $${total.toLocaleString(undefined, { maximumFractionDigits: 0 })}/mo`,
      });
    }
    const wafScore = wafScoreFromCall(call);
    if (wafScore) {
      highlights.push({ key: `waf-${call.tool}`, label: `WAF ${wafScore}` });
    }
  }
  if (highlights.length === 0) return null;
  return (
    <div data-testid="artifact-highlights" style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', paddingLeft: '0.25rem', marginTop: '0.35rem' }}>
      {highlights.map(item => (
        <span
          key={item.key}
          style={{
            color: '#d8e4ff',
            background: '#142033',
            border: '1px solid #2d4268',
            borderRadius: 999,
            padding: '0.16rem 0.5rem',
            fontSize: '0.68rem',
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {item.label}
        </span>
      ))}
    </div>
  );
}

function extractQuickActions(content: string, toolCalls?: ChatToolCall[]): QuickAction[] {
  const dedupe = new Set<string>();
  const actions: QuickAction[] = [];

  const push = (command: string, label: string, tone: 'primary' | 'secondary') => {
    if (dedupe.has(command)) return;
    dedupe.add(command);
    actions.push({ command, label, tone });
  };

  for (const call of toolCalls ?? []) {
    const resultData = (call.result_data ?? {}) as Record<string, unknown>;
    const checkpoint = (resultData.checkpoint ?? null) as Record<string, unknown> | null;
    const options = Array.isArray(checkpoint?.options) ? checkpoint?.options : [];
    for (const option of options) {
      if (typeof option !== 'string') continue;
      const normalized = option.trim().toLowerCase();
      if (normalized === 'approve checkpoint') push('approve checkpoint', 'Approve Checkpoint', 'primary');
      if (normalized === 'revise input') push('revise input', 'Revise Input', 'secondary');
    }
  }

  const lower = (content || '').toLowerCase();
  if (lower.includes('approve checkpoint')) push('approve checkpoint', 'Approve Checkpoint', 'primary');
  if (lower.includes('revise input')) push('revise input', 'Revise Input', 'secondary');
  if (lower.includes('confirm update all')) push('confirm update all', 'Confirm Update All', 'primary');
  if (lower.includes('cancel update')) push('cancel update', 'Cancel Update', 'secondary');

  return actions;
}

function MessageBubble({
  msg,
  toolCalls,
  artifacts,
  artifactManifest,
  customerId,
  onQuickAction,
  busy,
}: {
  msg: { role: string; content?: string; timestamp: string };
  toolCalls?: ChatToolCall[];
  artifacts?: Record<string, string>;
  artifactManifest?: ChatArtifactManifest;
  customerId: string;
  onQuickAction?: (command: string) => void;
  busy?: boolean;
}) {
  const isUser = msg.role === 'user';
  const traceWarnings = !isUser && Array.isArray(toolCalls)
    ? Array.from(new Set(toolCalls.flatMap(traceWarningsForCall)))
    : [];
  const quickActions = !isUser ? extractQuickActions(msg.content ?? '', toolCalls) : [];
  const bubbleStyle: React.CSSProperties = {
    maxWidth:     '88%',
    alignSelf:    isUser ? 'flex-end' : 'flex-start',
    background:   isUser ? 'linear-gradient(180deg, rgba(232,87,26,0.18), rgba(232,87,26,0.12))' : '#101421',
    border:       `1px solid ${isUser ? 'rgba(232,87,26,0.42)' : '#273047'}`,
    borderLeft:   isUser ? undefined : '3px solid #2a3a6e',
    borderRadius: 12,
    padding:      '1rem 1.25rem',
    fontSize:     '0.86rem',
    lineHeight:   1.55,
    color:        '#dde3f3',
    fontFamily:   "'JetBrains Mono', monospace",
    whiteSpace:   'pre-wrap',
    wordBreak:    'break-word',
  };
  const content = msg.content ?? '';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignSelf: isUser ? 'flex-end' : 'flex-start', maxWidth: '88%' }}>
      <div style={bubbleStyle}>
        {isUser ? (
          <span data-testid="chat-user-message">{content}</span>
        ) : (
          <span data-testid="chat-assistant-message" className="archie-md" dangerouslySetInnerHTML={{ __html: mdToHtml(content) }} />
        )}
      </div>
      {traceWarnings.length > 0 && (
        <div
          data-testid="trace-warning-badges"
          style={{
            marginTop: '0.25rem',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.28rem',
            alignSelf: 'flex-start',
          }}
        >
          {traceWarnings.map((warning, idx) => (
            <span
              key={`${warning}-${idx}`}
              data-testid="trace-warning-badge"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.2rem',
                fontSize: '0.66rem',
                color: '#f0b35a',
                background: 'rgba(240,179,90,0.12)',
                border: '1px solid rgba(240,179,90,0.35)',
                borderRadius: 999,
                padding: '0.15rem 0.45rem',
                fontFamily: "'JetBrains Mono', monospace",
              }}
              title={warning}
            >
              ⚠ trace warning: {warning}
            </span>
          ))}
        </div>
      )}
      {toolCalls && toolCalls.length > 0 && (
        <div style={{ marginTop: '0.5rem' }}>
          <div style={{
            fontSize:      '0.65rem',
            color:         '#4b5470',
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            marginBottom:  '0.15rem',
            fontFamily:    "'JetBrains Mono', monospace",
          }}>
            {toolCalls.length} tool{toolCalls.length !== 1 ? 's' : ''} used
          </div>
          {toolCalls.map((tc, i) => <ToolChip key={i} call={tc} />)}
        </div>
      )}
      <ArtifactHighlights toolCalls={toolCalls} artifactManifest={artifactManifest} />
      {artifacts && Object.keys(artifacts).length > 0 && (
        <div style={{ paddingLeft: '0.25rem' }}>
          {Object.entries(artifacts).map(([k, v]) => (
            <ArtifactLink key={k} toolName={k} artifactKey={v} customerId={customerId} />
          ))}
        </div>
      )}
      {quickActions.length > 0 && (
        <div
          data-testid="chat-quick-actions"
          style={{ paddingLeft: '0.25rem', display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginTop: '0.3rem' }}
        >
          {quickActions.map(action => (
            <button
              key={action.command}
              type="button"
              data-testid={`quick-action-${action.command.replace(/\s+/g, '-')}`}
              disabled={busy || !onQuickAction}
              onClick={() => onQuickAction?.(action.command)}
              style={{
                fontSize: '0.72rem',
                borderRadius: 999,
                padding: '0.32rem 0.72rem',
                fontFamily: "'JetBrains Mono', monospace",
                cursor: busy ? 'not-allowed' : 'pointer',
                border: action.tone === 'primary'
                  ? '1px solid rgba(232,87,26,0.45)'
                  : '1px solid #2b344d',
                background: action.tone === 'primary'
                  ? 'rgba(232,87,26,0.14)'
                  : '#111626',
                color: action.tone === 'primary' ? '#ff9b75' : '#c9d1e4',
                opacity: busy ? 0.55 : 1,
              }}
              title={`Send: ${action.command}`}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
      <div style={{ paddingLeft: '0.25rem' }}>
        <ArtifactManifestLinks manifest={artifactManifest} />
      </div>
      <div style={{ fontSize: '0.6rem', color: '#454d64', marginTop: '0.15rem', alignSelf: isUser ? 'flex-end' : 'flex-start' }}>
        {new Date(msg.timestamp).toLocaleTimeString()}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface LocalMessage {
  role:      'user' | 'assistant';
  content:   string;
  timestamp: string;
  toolCalls?: ChatToolCall[];
  artifacts?: Record<string, string>;
  artifactManifest?: ChatArtifactManifest;
}

interface ChatInterfaceProps {
  customerId?: string;
  customerName?: string;
  onCustomerIdChange?: (id: string) => void;
  onCustomerNameChange?: (name: string) => void;
  onArtifactsChange?: (downloads: ChatArtifactDownload[]) => void;
  onConversationComplete?: () => void;
  pendingPrompt?: { text: string; seq: number } | null;
  projectId?: string;
  projectName?: string;
}

function latestManifestDownloads(messages: LocalMessage[]): ChatArtifactDownload[] {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.role !== 'assistant') continue;
    const downloads = msg.artifactManifest?.downloads;
    if (Array.isArray(downloads) && downloads.length > 0) {
      return downloads;
    }
  }
  return [];
}

function parseToolCallMessage(message: ChatMessage): ChatToolCall | null {
  let parsedContent: Partial<ChatToolCall> = {};
  const content = (message.content ?? '').trim();
  if (content.startsWith('{')) {
    try {
      parsedContent = JSON.parse(content) as Partial<ChatToolCall>;
    } catch {
      parsedContent = {};
    }
  }
  if (message.tool_call?.tool) {
    return {
      tool: message.tool_call.tool,
      args: message.tool_call.args ?? {},
      result_summary: message.result_summary ?? parsedContent.result_summary ?? '',
      result_data: parsedContent.result_data,
    };
  }
  if (typeof parsedContent.tool !== 'string') return null;
  return {
    tool: parsedContent.tool,
    args: parsedContent.args ?? {},
    result_summary: parsedContent.result_summary ?? '',
    result_data: parsedContent.result_data,
  };
}

function diagramKeyToDownload(key: string, fallbackCustomerId: string): ChatArtifactDownload | null {
  const parts = key.split('/').filter(Boolean);
  const versionIndex = parts.findIndex(part => /^v\d+$/i.test(part));
  const filename = parts.length > 0 ? parts[parts.length - 1] : 'diagram.drawio';
  const clientId = versionIndex >= 2 ? parts[versionIndex - 2] : fallbackCustomerId;
  const diagramName = versionIndex >= 1 ? parts[versionIndex - 1] : 'oci_architecture';
  if (!clientId || !diagramName) return null;
  return {
    type: 'diagram',
    tool: 'generate_diagram',
    key,
    filename,
    download_url: `/api/download/${encodeURIComponent(filename)}?client_id=${encodeURIComponent(clientId)}&diagram_name=${encodeURIComponent(diagramName)}`,
  };
}

function inferDiagramKeyFromSummary(summary: string): string {
  const match = summary.match(/Key:\s*([^\s,]+)/i);
  return match?.[1] ?? '';
}

function normalizeHistoryMessages(history: ChatMessage[], customerId: string): LocalMessage[] {
  const loaded: LocalMessage[] = [];
  let pendingToolCalls: ChatToolCall[] = [];
  let pendingDownloads: ChatArtifactDownload[] = [];
  let pendingDownloadUrls = new Set<string>();

  const pushPendingDownload = (download: ChatArtifactDownload | null) => {
    if (!download || pendingDownloadUrls.has(download.download_url)) return;
    pendingDownloadUrls.add(download.download_url);
    pendingDownloads.push(download);
  };

  const attachPending = (message: LocalMessage): LocalMessage => {
    if (pendingToolCalls.length === 0 && pendingDownloads.length === 0) return message;
    const next: LocalMessage = {
      ...message,
      ...(pendingToolCalls.length > 0 ? { toolCalls: pendingToolCalls } : {}),
      ...(pendingDownloads.length > 0 ? { artifactManifest: { downloads: pendingDownloads } } : {}),
    };
    pendingToolCalls = [];
    pendingDownloads = [];
    pendingDownloadUrls = new Set<string>();
    return next;
  };

  for (const item of history) {
    if (item.role === 'tool') {
      const summary = item.result_summary ?? '';
      if (item.tool === 'generate_diagram') {
        const key = inferDiagramKeyFromSummary(summary);
        pushPendingDownload(key ? diagramKeyToDownload(key, customerId) : null);
      }
      continue;
    }

    const toolCall = parseToolCallMessage(item);
    if (toolCall) {
      pendingToolCalls.push(toolCall);
      const key = toolCall.tool === 'generate_diagram'
        ? inferDiagramKeyFromSummary(toolCall.result_summary)
        : '';
      pushPendingDownload(key ? diagramKeyToDownload(key, customerId) : null);
      continue;
    }

    if (item.role !== 'user' && item.role !== 'assistant') continue;
    const message: LocalMessage = {
      role:      item.role,
      content:   item.content ?? '',
      timestamp: item.timestamp,
      toolCalls: item.tool_calls,
      artifacts: item.artifacts,
      artifactManifest: item.artifact_manifest,
    };
    loaded.push(item.role === 'assistant' ? attachPending(message) : message);
  }

  if (pendingDownloads.length > 0 && loaded.length > 0) {
    for (let i = loaded.length - 1; i >= 0; i -= 1) {
      if (loaded[i].role !== 'assistant') continue;
      loaded[i] = {
        ...loaded[i],
        ...(pendingToolCalls.length > 0 ? { toolCalls: pendingToolCalls } : {}),
        artifactManifest: { downloads: pendingDownloads },
      };
      break;
    }
  }

  return loaded;
}

function isNearBottom(element: HTMLElement, threshold = 96): boolean {
  const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
  return remaining <= threshold;
}

export function ChatInterface({
  customerId = '',
  customerName = '',
  onArtifactsChange,
  onConversationComplete,
  pendingPrompt,
  projectId,
  projectName,
}: ChatInterfaceProps) {
  const [messages,      setMessages]      = useState<LocalMessage[]>([]);
  const [input,         setInput]         = useState('');
  const [loading,       setLoading]       = useState(false);
  const [error,         setError]         = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [attachedFile,  setAttachedFile]  = useState<string | null>(null);
  const [attachLoading, setAttachLoading] = useState(false);
  const [streamingReply, setStreamingReply] = useState('');
  const [thinkingStatus, setThinkingStatus] = useState<string | null>(null);
  const [archieWorkingMessage, setArchieWorkingMessage] = useState(ARCHIE_WORKING_MESSAGES[0]);
  const [activeHats, setActiveHats] = useState<string[]>([]);
  const [backgroundMode, setBackgroundMode] = useState(false);
  const [backgroundJobId, setBackgroundJobId] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const bottomRef   = useRef<HTMLDivElement>(null);
  const inputRef    = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const shouldAutoScrollRef = useRef(true);

  // Load history on mount / customer change
  useEffect(() => {
    if (!customerId.trim()) return;
    let cancelled = false;
    setHistoryLoaded(false);
    setMessages([]);
    apiGetChatHistory(customerId)
      .then(resp => {
        if (cancelled) return;
        setMessages(normalizeHistoryMessages(resp.history, customerId));
        setHistoryLoaded(true);
      })
      .catch(() => { if (!cancelled) setHistoryLoaded(true); });
    return () => { cancelled = true; };
  }, [customerId]);

  function syncAutoScrollPreference() {
    const thread = threadRef.current;
    if (!thread) return;
    shouldAutoScrollRef.current = isNearBottom(thread);
  }

  // Keep the message pane pinned only when the user is already reading the latest content.
  useEffect(() => {
    const thread = threadRef.current;
    if (!thread || !shouldAutoScrollRef.current) return;
    const rafId = window.requestAnimationFrame(() => {
      if (typeof thread.scrollTo === 'function') {
        thread.scrollTo({ top: thread.scrollHeight, behavior: 'auto' });
      } else {
        thread.scrollTop = thread.scrollHeight;
      }
    });
    return () => window.cancelAnimationFrame(rafId);
  }, [messages, loading, streamingReply, thinkingStatus, historyLoaded]);

  useEffect(() => {
    onArtifactsChange?.(latestManifestDownloads(messages));
  }, [messages, onArtifactsChange]);

  useEffect(() => {
    if (!pendingPrompt) return;
    setInput(pendingPrompt.text);
    setTimeout(() => inputRef.current?.focus(), 50);
  }, [pendingPrompt]);

  useEffect(() => {
    if (!backgroundJobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const resp = await apiGetChatJob(backgroundJobId);
        if (cancelled) return;
        if ((resp as { status?: string }).status === 'pending') return;
        const complete = resp as {
          reply?: string;
          tool_calls?: ChatToolCall[];
          artifacts?: Record<string, string>;
          artifact_manifest?: ChatArtifactManifest;
        };
        const assistantMsg: LocalMessage = {
          role: 'assistant',
          content: complete.reply ?? '',
          timestamp: new Date().toISOString(),
          toolCalls: complete.tool_calls ?? [],
          artifacts: complete.artifacts ?? {},
          artifactManifest: complete.artifact_manifest,
        };
        setMessages(prev => [...prev, assistantMsg]);
        setBackgroundJobId(null);
        setBackgroundMode(false);
        onConversationComplete?.();
      } catch (err: unknown) {
        if (cancelled) return;
        const e = err as { status?: number; detail?: string };
        setError(`Background job failed: ${e.detail ?? 'unknown error'}`);
        setBackgroundJobId(null);
        setBackgroundMode(false);
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 5000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [backgroundJobId, onConversationComplete]);

  async function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !customerId.trim()) return;
    setAttachLoading(true);
    setError(null);
    try {
      await apiUploadNote(customerId, file.name, file);
      setAttachedFile(file.name);
      if (!input.trim()) {
        setInput(`I've just uploaded my meeting notes (${file.name}). Please save them.`);
      }
    } catch (err: unknown) {
      const ex = err as { status: number; detail: string };
      setError(`Upload failed: ${ex.detail}`);
    } finally {
      setAttachLoading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function sendMessage() {
    await submitMessage(input, true);
  }

  async function submitMessage(raw: string, clearComposer: boolean) {
    const text = raw.trim();
    if (!text || loading) return;
    if (!customerId.trim()) { setError('Enter a Customer ID before sending.'); return; }
    syncAutoScrollPreference();

    const userMsg: LocalMessage = {
      role: 'user', content: text, timestamp: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMsg]);
    if (clearComposer) setInput('');
    setAttachedFile(null);
    setError(null);
    setArchieWorkingMessage(
      pickArchieMessage(ARCHIE_WORKING_MESSAGES, `${customerId}:${customerName}:${text}:${messages.length}`)
    );
    setLoading(true);
    setStreamingReply('');
    setThinkingStatus(null);
    setActiveHats([]);

    try {
      let streamed = '';
      let resp;
      const effectiveCustomerName = customerName || projectName || customerId;
      const projectMeta = {
        ...(projectId ? { projectId } : {}),
        ...(projectName ? { projectName } : {}),
      };
      if (backgroundMode) {
        const job = await apiChatBackground(customerId, effectiveCustomerName, text, projectMeta);
        setBackgroundJobId(job.job_id);
        return;
      }
      try {
        resp = await apiChatStream(customerId, effectiveCustomerName, text, {
          onEvent: event => {
            const eventHat = (event as { hat?: unknown }).hat;
            if ((event.event_type as string) === 'hat_activate' && typeof eventHat === 'string' && eventHat.trim()) {
              const hat = eventHat.trim();
              setActiveHats(prev => prev.includes(hat) ? prev : [...prev, hat]);
            } else if ((event.event_type as string) === 'hat_drop' && typeof eventHat === 'string' && eventHat.trim()) {
              const hat = eventHat.trim();
              setActiveHats(prev => prev.filter(h => h !== hat));
            } else if ((event.event_type as string) === 'thinking') {
              const label = (event as { label?: unknown }).label;
              setThinkingStatus(typeof label === 'string' && label.trim() ? label : 'Thinking...');
            } else if ((event.event_type as string) === 'status' && (event as { status?: unknown }).status === 'tool_started') {
              const message = (event as { message?: unknown }).message;
              if (typeof message === 'string' && message.trim()) setThinkingStatus(message);
            } else if ((event.event_type as string) === 'completion') {
              setThinkingStatus(null);
            }
          },
          onToken: delta => {
            streamed += delta;
            setStreamingReply(prev => prev + delta);
          },
        }, projectMeta);
      } catch {
        // Fallback for environments where stream endpoint is unavailable.
        resp = await apiChat(customerId, effectiveCustomerName, text, projectMeta);
      }
      const assistantMsg: LocalMessage = {
        role:      'assistant',
        content:   resp.reply || streamed,
        timestamp: new Date().toISOString(),
        toolCalls: resp.tool_calls,
        artifacts: resp.artifacts,
        artifactManifest: resp.artifact_manifest,
      };
      setMessages(prev => [...prev, assistantMsg]);
      setStreamingReply('');
      setThinkingStatus(null);
      onConversationComplete?.();
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Error ${e.status}: ${e.detail}`);
      setStreamingReply('');
      setThinkingStatus(null);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  async function clearHistory() {
    if (!customerId.trim()) return;
    if (!confirm('Clear all conversation history for this customer?')) return;
    try {
      await apiClearChatHistory(customerId);
      setMessages([]);
      setActiveHats([]);
      setAttachedFile(null);
      setError(null);
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Clear error: ${e.detail}`);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  const inputStyle: React.CSSProperties = {
    width:        '100%',
    minHeight:    'clamp(6.5rem, 14vh, 9rem)',
    maxHeight:    'min(40vh, 18rem)',
    resize:       'vertical',
    background:   'transparent',
    border:       'none',
    borderRadius: 14,
    color:        '#e2e7f5',
    fontFamily:   "'JetBrains Mono', monospace",
    fontSize:     '1rem',
    lineHeight:   1.58,
    padding:      '0.4rem 0.5rem',
    outline:      'none',
    boxSizing:    'border-box',
  };

  const fieldStyle: React.CSSProperties = {
    background:   '#0a0d16',
    border:       '1px solid #2b3650',
    borderRadius: 10,
    color:        '#cdd2e0',
    fontFamily:   "'JetBrains Mono', monospace",
    fontSize:     '0.8rem',
    padding:      '0.55rem 0.7rem',
    outline:      'none',
    width:        '100%',
    boxSizing:    'border-box',
  };

  const btnPrimary: React.CSSProperties = {
    background:    'linear-gradient(180deg, #ff6a2f 0%, #e8571a 100%)',
    border:        'none',
    borderRadius:  10,
    color:         '#fff',
    fontFamily:    "'JetBrains Mono', monospace",
    fontSize:      '0.86rem',
    fontWeight:    700,
    padding:       '0.7rem 1.35rem',
    cursor:        loading ? 'not-allowed' : 'pointer',
    opacity:       loading ? 0.6 : 1,
    letterSpacing: '0.04em',
  };

  const btnSecondary: React.CSSProperties = {
    background:    '#111626',
    border:        '1px solid #2b344d',
    borderRadius:  10,
    color:         '#c9d1e4',
    fontFamily:    "'JetBrains Mono', monospace",
    fontSize:      '0.76rem',
    padding:       '0.5rem 0.8rem',
    cursor:        'pointer',
    letterSpacing: '0.04em',
  };

  const canSend = Boolean(customerId.trim() && input.trim()) && !loading;
  const archieHelloMessage = pickArchieMessage(
    ARCHIE_HELLO_MESSAGES,
    `${customerId}:${customerName || customerId}`
  );

  return (
    <div style={{ display: 'grid', gridTemplateRows: '1fr auto auto auto', gap: '0.75rem', minHeight: '72vh' }}>
      {/* Message thread */}
      <div style={{
        minHeight:     '50vh',
        maxHeight:     '64vh',
        overflowY:     'auto',
        background:    'radial-gradient(circle at 50% -20%, rgba(232,87,26,0.07), transparent 55%), #08090d',
        border:        '1px solid #273149',
        borderRadius:  16,
        padding:       '1.25rem',
        display:       'flex',
        flexDirection: 'column',
        gap:           '0.9rem',
      }}
      ref={threadRef}
      onScroll={syncAutoScrollPreference}>
        {!customerId.trim() && (
          <div style={{ color: '#454d64', fontSize: '0.78rem', textAlign: 'center', marginTop: '2rem' }}>
            Enter a Customer ID above to start or resume a conversation.
          </div>
        )}
        {customerId.trim() && !historyLoaded && (
          <div style={{ color: '#454d64', fontSize: '0.72rem' }}>Loading history…</div>
        )}
        {historyLoaded && messages.length === 0 && (
          <div
            data-testid="archie-hello-message"
            style={{ color: '#717b94', fontSize: '0.78rem', textAlign: 'center', marginTop: '2rem', display: 'grid', gap: '0.35rem' }}
          >
            <div style={{ color: '#dde3f3', fontSize: '0.92rem', fontWeight: 700 }}>Hi, I&apos;m Archie.</div>
            <div>{archieHelloMessage}</div>
            <div style={{ color: '#454d64' }}>No messages yet. Say hello or paste your meeting notes to get started.</div>
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            msg={msg}
            toolCalls={msg.toolCalls}
            artifacts={msg.artifacts}
            artifactManifest={msg.artifactManifest}
            customerId={customerId}
            onQuickAction={(command) => { void submitMessage(command, false); }}
            busy={loading}
          />
        ))}
        {activeHats.length > 0 && (
          <div
            data-testid="active-hat-badges"
            style={{
              display: 'flex',
              gap: '0.5rem',
              alignItems: 'center',
              flexWrap: 'wrap',
              padding: '0.25rem 0.4rem',
              fontSize: '0.72rem',
              color: '#8b93a8',
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            <span>Active:</span>
            {activeHats.map(hat => (
              <span
                key={hat}
                style={{
                  background: '#dbeafe',
                  color: '#1d4ed8',
                  padding: '0.12rem 0.55rem',
                  borderRadius: 999,
                  fontWeight: 700,
                }}
              >
                {hat.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        )}
        {thinkingStatus && (
          <div
            data-testid="chat-thinking-status"
            style={{
              color: thinkingStatus.startsWith('Running') ? '#61dafb' : '#a8b4cc',
              fontWeight: thinkingStatus.startsWith('Running') ? 600 : 400,
              fontSize: '0.78rem',
              alignSelf: 'flex-start',
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            {thinkingStatus}
          </div>
        )}
        {streamingReply && (
          <div style={{ display: 'flex', flexDirection: 'column', alignSelf: 'flex-start', maxWidth: '88%' }}>
            <div
              data-testid="chat-streaming-message"
              className="archie-md"
              style={{
                maxWidth: '88%',
                alignSelf: 'flex-start',
                background: '#101421',
                border: '1px solid #273047',
                borderLeft: '3px solid #2a3a6e',
                borderRadius: 12,
                padding: '1rem 1.25rem',
                fontSize: '0.86rem',
                lineHeight: 1.55,
                color: '#dde3f3',
                fontFamily: "'JetBrains Mono', monospace",
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
              dangerouslySetInnerHTML={{ __html: mdToHtml(streamingReply) }}
            />
          </div>
        )}
        {loading && (
          <div
            data-testid={thinkingStatus ? 'chat-thinking-status' : 'archie-working-message'}
            style={{
              color: thinkingStatus ? '#c8b8f0' : '#8b93a8',
              fontSize: '0.78rem',
              alignSelf: 'flex-start',
              fontFamily: "'JetBrains Mono', monospace",
              fontStyle: thinkingStatus ? 'normal' : 'italic',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <span className="archie-status-dot" />
            {thinkingStatus || archieWorkingMessage}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Error banner */}
        {error && (
          <div style={{
          padding:      '0.6rem 0.75rem',
          background:   'rgba(232,65,90,0.08)',
          border:       '1px solid rgba(232,65,90,0.4)',
          borderRadius: 10,
          fontSize:     '0.78rem',
          color:        '#e8415a',
            fontFamily:   "'JetBrains Mono', monospace",
          }}
          data-testid="chat-error-banner"
        >
          {error}
        </div>
      )}

      {/* Background job badge */}
      {backgroundJobId && (
        <div
          data-testid="chat-background-job-pill"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.45rem',
            background: 'rgba(97,218,251,0.08)',
            border: '1px solid rgba(97,218,251,0.3)',
            borderRadius: 10,
            padding: '0.24rem 0.55rem',
            fontSize: '0.7rem',
            color: '#9bdff2',
            alignSelf: 'flex-start',
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          Working in background... {backgroundJobId}
          <button
            type="button"
            onClick={() => setBackgroundJobId(null)}
            title="Dismiss background job status"
            style={{
              border: 'none',
              background: 'transparent',
              color: '#9bdff2',
              cursor: 'pointer',
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: '0.8rem',
              padding: 0,
            }}
          >
            x
          </button>
        </div>
      )}

      {/* Attachment badge */}
      {attachedFile && (
        <div style={{
          display:      'inline-flex',
          alignItems:   'center',
          gap:          '0.4rem',
          background:   'rgba(232,87,26,0.08)',
          border:       '1px solid rgba(232,87,26,0.25)',
          borderRadius: 10,
          padding:      '0.2rem 0.5rem',
          fontSize:     '0.7rem',
          color:        '#e8571a',
          alignSelf:    'flex-start',
          fontFamily:   "'JetBrains Mono', monospace",
        }}>
          📎 {attachedFile}
          <span
            style={{ cursor: 'pointer', opacity: 0.7 }}
            onClick={() => setAttachedFile(null)}
            title="Dismiss badge (file already uploaded)"
          >✕</span>
        </div>
      )}

      {/* Input bar */}
      <div
        style={{
          position: 'sticky',
          bottom: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: '0.65rem',
          background: 'linear-gradient(180deg, rgba(8,9,13,0.82) 0%, #0f1119 24%, #0f1119 100%)',
          border: '1px solid #282d42',
          boxShadow: '0 20px 45px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.02) inset',
          borderRadius: 20,
          padding: '0.95rem',
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".md,.txt,.docx,.pdf"
          style={{ display: 'none' }}
          onChange={handleFileSelect}
        />
        <div
          style={{
            background: '#0a0d15',
            border: '1px solid #252b3f',
            borderRadius: 16,
            padding: '0.35rem',
          }}
        >
          <textarea
            ref={inputRef}
            data-testid="chat-input"
            style={inputStyle}
            value={input}
            placeholder="Describe your customer or ask Archie for an artifact..."
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
          />
        </div>
        <div style={{ display: 'flex', gap: '0.55rem', justifyContent: 'space-between', flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: '0.45rem' }}>
            <button
              style={btnSecondary}
              onClick={() => { if (customerId.trim()) fileInputRef.current?.click(); }}
              disabled={attachLoading || !customerId.trim()}
              title="Attach a meeting notes file (.md, .txt, .docx, .pdf)"
            >
              {attachLoading ? 'Uploading…' : 'Attach Notes'}
            </button>
            <button
              style={btnSecondary}
              onClick={clearHistory}
              title="Clear conversation history"
              disabled={!customerId.trim() || loading}
            >
              Clear
            </button>
            <button
              type="button"
              data-testid="chat-background-toggle"
              style={{
                ...btnSecondary,
                border: backgroundMode ? '1px solid rgba(97,218,251,0.45)' : btnSecondary.border,
                color: backgroundMode ? '#9bdff2' : btnSecondary.color,
                background: backgroundMode ? 'rgba(97,218,251,0.1)' : btnSecondary.background,
              }}
              onClick={() => setBackgroundMode(v => !v)}
              title="Run the next chat request as a background job"
              aria-pressed={backgroundMode}
            >
              Background
            </button>
          </div>
          <button data-testid="chat-send-button" style={btnPrimary} onClick={sendMessage} disabled={!canSend}>
            {loading ? 'Sending…' : 'Send Message'}
          </button>
        </div>
      </div>

      <div style={{ fontSize: '0.65rem', color: '#454d64' }}>
        Enter = send · Shift+Enter = newline · History is saved per customer.
      </div>
    </div>
  );
}
