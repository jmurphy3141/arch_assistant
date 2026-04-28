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
  apiChatStream,
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

// ── Helpers ───────────────────────────────────────────────────────────────────

function getStoredCustomer(): { id: string; name: string } {
  try {
    return {
      id:   localStorage.getItem('chat_customer_id')   ?? '',
      name: localStorage.getItem('chat_customer_name') ?? '',
    };
  } catch { return { id: '', name: '' }; }
}
function saveStoredCustomer(id: string, name: string) {
  try {
    localStorage.setItem('chat_customer_id',   id);
    localStorage.setItem('chat_customer_name', name);
  } catch { /* ignore */ }
}

// ── Markdown renderer ─────────────────────────────────────────────────────────

/** Block-aware Markdown → HTML: headings, lists, hr, bold, inline code. */
function mdToHtml(md: string): string {
  const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const inline = (s: string) =>
    escape(s)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g,
        '<code style="color:#e8571a;background:rgba(232,87,26,0.1);padding:0 2px;border-radius:2px">$1</code>');

  const lines = md.split('\n');
  const out: string[] = [];
  let inUl = false, inOl = false;

  const closeList = () => {
    if (inUl) { out.push('</ul>'); inUl = false; }
    if (inOl) { out.push('</ol>'); inOl = false; }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^---+$/.test(line)) {
      closeList();
      out.push('<hr style="border:none;border-top:1px solid #1c2030;margin:0.5rem 0"/>');
    } else if (/^### /.test(line)) {
      closeList();
      out.push(`<h3 style="margin:0.5rem 0 0.2rem;font-size:0.85rem;color:#e8571a">${inline(line.slice(4))}</h3>`);
    } else if (/^## /.test(line)) {
      closeList();
      out.push(`<h2 style="margin:0.6rem 0 0.25rem;font-size:0.95rem;color:#fff">${inline(line.slice(3))}</h2>`);
    } else if (/^[-*] /.test(line)) {
      if (inOl) { out.push('</ol>'); inOl = false; }
      if (!inUl) { out.push('<ul style="margin:0.2rem 0;padding-left:1.2rem">'); inUl = true; }
      out.push(`<li style="margin:0.1rem 0">${inline(line.slice(2))}</li>`);
    } else if (/^\d+\. /.test(line)) {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (!inOl) { out.push('<ol style="margin:0.2rem 0;padding-left:1.2rem">'); inOl = true; }
      out.push(`<li style="margin:0.1rem 0">${inline(line.replace(/^\d+\. /, ''))}</li>`);
    } else {
      closeList();
      out.push(line === '' ? '<br/>' : `<span>${inline(line)}</span><br/>`);
    }
  }
  closeList();
  return out.join('');
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ToolChip({ call }: { call: ChatToolCall }) {
  const [open, setOpen] = useState(false);
  const trace = (call.result_data?.trace ?? {}) as Record<string, unknown>;
  const governor = (trace.governor ?? {}) as Record<string, unknown>;
  const checkpoint = (trace.checkpoint ?? null) as Record<string, unknown> | null;
  const decisionContext = (trace.decision_context ?? {}) as Record<string, unknown>;
  const traceSummary = {
    path_id: typeof trace.path_id === 'string' ? trace.path_id : '',
    applied_skills: Array.isArray(trace.applied_skills) ? trace.applied_skills : [],
    model_profile: typeof trace.model_profile === 'string' ? trace.model_profile : '',
    refinement_count: typeof trace.refinement_count === 'number' ? trace.refinement_count : 0,
    max_refinements: typeof trace.max_refinements === 'number' ? trace.max_refinements : 0,
    overall_pass: typeof trace.overall_pass === 'boolean' ? trace.overall_pass : true,
    warnings: Array.isArray(trace.warnings) ? trace.warnings : [],
    constraint_tags: Array.isArray(trace.constraint_tags) ? trace.constraint_tags : [],
    assumption_count: typeof trace.assumption_count === 'number' ? trace.assumption_count : 0,
    governor_status: typeof governor.overall_status === 'string' ? governor.overall_status : '',
    checkpoint_type: checkpoint && typeof checkpoint.type === 'string' ? checkpoint.type : '',
    decision_context_goal: typeof decisionContext.goal === 'string' ? decisionContext.goal : '',
  };
  const chipStyle: React.CSSProperties = {
    display:      'inline-flex',
    alignItems:   'center',
    gap:          '0.3rem',
    background:   'rgba(232,87,26,0.08)',
    border:       '1px solid rgba(232,87,26,0.25)',
    borderRadius: 4,
    padding:      '0.2rem 0.5rem',
    fontSize:     '0.7rem',
    color:        '#e8571a',
    cursor:       'pointer',
    fontFamily:   "'JetBrains Mono', monospace",
    marginTop:    '0.3rem',
    userSelect:   'none',
  };
  return (
    <div>
      <span data-testid={`tool-chip-${call.tool}`} style={chipStyle} onClick={() => setOpen(v => !v)}>
        ⚙ {call.tool} {open ? '▲' : '▼'}
      </span>
      {open && (
        <pre style={{
          marginTop:    '0.3rem',
          padding:      '0.5rem',
          background:   '#0b0d14',
          border:       '1px solid #1c2030',
          borderRadius: 4,
          fontSize:     '0.68rem',
          color:        '#8b93a8',
          whiteSpace:   'pre-wrap',
          wordBreak:    'break-all',
        }}>
          {`args: ${JSON.stringify(call.args, null, 2)}\nresult: ${call.result_summary}`}
          {(traceSummary.applied_skills.length > 0 || traceSummary.max_refinements > 0) && (
            <span
              data-testid="tool-trace-summary"
              style={{ display: 'block', marginTop: '0.45rem', color: '#aeb5c8' }}
            >
              {`\ntrace: ${JSON.stringify(traceSummary, null, 2)}`}
            </span>
          )}
        </pre>
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
    borderRadius: 12,
    padding:      '0.78rem 0.95rem',
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
          <span data-testid="chat-assistant-message" dangerouslySetInnerHTML={{ __html: mdToHtml(content) }} />
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
        <div style={{ paddingLeft: '0.25rem' }}>
          {toolCalls.map((tc, i) => <ToolChip key={i} call={tc} />)}
        </div>
      )}
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
  onCustomerIdChange?: (id: string) => void;
  onArtifactsChange?: (downloads: ChatArtifactDownload[]) => void;
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

function isNearBottom(element: HTMLElement, threshold = 96): boolean {
  const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
  return remaining <= threshold;
}

export function ChatInterface({ onCustomerIdChange, onArtifactsChange }: ChatInterfaceProps) {
  const stored = getStoredCustomer();
  const [customerId,    setCustomerId]    = useState(stored.id);
  const [customerName,  setCustomerName]  = useState(stored.name);
  const [messages,      setMessages]      = useState<LocalMessage[]>([]);
  const [input,         setInput]         = useState('');
  const [loading,       setLoading]       = useState(false);
  const [error,         setError]         = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [attachedFile,  setAttachedFile]  = useState<string | null>(null);
  const [attachLoading, setAttachLoading] = useState(false);
  const [streamingReply, setStreamingReply] = useState('');
  const threadRef = useRef<HTMLDivElement>(null);
  const bottomRef   = useRef<HTMLDivElement>(null);
  const inputRef    = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const shouldAutoScrollRef = useRef(true);

  // Load history on mount / customer change
  useEffect(() => {
    if (!customerId.trim()) return;
    setHistoryLoaded(false);
    apiGetChatHistory(customerId)
      .then(resp => {
        const loaded: LocalMessage[] = resp.history
          .filter(m => m.role === 'user' || m.role === 'assistant')
          .map(m => ({
            role:      m.role as 'user' | 'assistant',
            content:   m.content ?? '',
            timestamp: m.timestamp,
          }));
        // Avoid clobbering newly-sent local messages if history returns late.
        setMessages(prev => (prev.length > 0 ? prev : loaded));
        setHistoryLoaded(true);
      })
      .catch(() => setHistoryLoaded(true));
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
  }, [messages, loading, streamingReply, historyLoaded]);

  useEffect(() => {
    onArtifactsChange?.(latestManifestDownloads(messages));
  }, [messages, onArtifactsChange]);

  function handleCustomerIdChange(id: string) {
    setCustomerId(id);
    saveStoredCustomer(id, customerName);
    onCustomerIdChange?.(id);
  }
  function handleCustomerNameChange(name: string) {
    setCustomerName(name);
    saveStoredCustomer(customerId, name);
  }

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
    setLoading(true);
    setStreamingReply('');

    try {
      let streamed = '';
      let resp;
      try {
        resp = await apiChatStream(customerId, customerName || customerId, text, {
          onToken: delta => {
            streamed += delta;
            setStreamingReply(prev => prev + delta);
          },
        });
      } catch {
        // Fallback for environments where stream endpoint is unavailable.
        resp = await apiChat(customerId, customerName || customerId, text);
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
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Error ${e.status}: ${e.detail}`);
      setStreamingReply('');
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

  return (
    <div style={{ display: 'grid', gridTemplateRows: 'auto 1fr auto auto auto', gap: '0.75rem', minHeight: '72vh' }}>
      {/* Customer identity fields */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.6rem' }}>
        <div>
          <label style={{ fontSize: '0.7rem', color: '#9aa4bb', display: 'block', marginBottom: '0.28rem', letterSpacing: '0.06em' }}>
            CUSTOMER ID
          </label>
          <input
            data-testid="chat-customer-id"
            style={fieldStyle}
            value={customerId}
            placeholder="e.g. acme001"
            onChange={e => handleCustomerIdChange(e.target.value)}
          />
        </div>
        <div>
          <label style={{ fontSize: '0.7rem', color: '#9aa4bb', display: 'block', marginBottom: '0.28rem', letterSpacing: '0.06em' }}>
            CUSTOMER NAME
          </label>
          <input
            data-testid="chat-customer-name"
            style={fieldStyle}
            value={customerName}
            placeholder="e.g. ACME Corp"
            onChange={e => handleCustomerNameChange(e.target.value)}
          />
        </div>
      </div>

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
          <div style={{ color: '#454d64', fontSize: '0.78rem', textAlign: 'center', marginTop: '2rem' }}>
            No messages yet. Say hello or paste your meeting notes to get started.
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
        {streamingReply && (
          <div style={{ display: 'flex', flexDirection: 'column', alignSelf: 'flex-start', maxWidth: '88%' }}>
            <div
              data-testid="chat-streaming-message"
              style={{
                maxWidth: '88%',
                alignSelf: 'flex-start',
                background: '#101421',
                border: '1px solid #273047',
                borderRadius: 12,
                padding: '0.78rem 0.95rem',
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
          <div style={{ color: '#8b93a8', fontSize: '0.75rem', alignSelf: 'flex-start' }}>
            Streaming response...
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
            placeholder="Message OCI Agent… (Enter to send, Shift+Enter for newline)"
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
