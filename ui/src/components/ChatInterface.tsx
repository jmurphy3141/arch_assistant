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
  apiGetChatHistory,
  apiClearChatHistory,
  apiUploadNote,
  apiGetLatestPov,
  apiGetLatestJep,
  apiGetLatestWaf,
  type ChatMessage,
  type ChatToolCall,
  type ChatArtifactManifest,
} from '../api/client';

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
      <span style={chipStyle} onClick={() => setOpen(v => !v)}>
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
        </pre>
      )}
    </div>
  );
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

function MessageBubble({
  msg,
  toolCalls,
  artifacts,
  artifactManifest,
  customerId,
}: {
  msg: { role: string; content?: string; timestamp: string };
  toolCalls?: ChatToolCall[];
  artifacts?: Record<string, string>;
  artifactManifest?: ChatArtifactManifest;
  customerId: string;
}) {
  const isUser = msg.role === 'user';
  const bubbleStyle: React.CSSProperties = {
    maxWidth:     '80%',
    alignSelf:    isUser ? 'flex-end' : 'flex-start',
    background:   isUser ? 'rgba(232,87,26,0.12)' : '#0e1016',
    border:       `1px solid ${isUser ? 'rgba(232,87,26,0.35)' : '#1c2030'}`,
    borderRadius: 6,
    padding:      '0.6rem 0.85rem',
    fontSize:     '0.8rem',
    color:        '#cdd2e0',
    fontFamily:   "'JetBrains Mono', monospace",
    whiteSpace:   'pre-wrap',
    wordBreak:    'break-word',
  };
  const content = msg.content ?? '';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignSelf: isUser ? 'flex-end' : 'flex-start', maxWidth: '80%' }}>
      <div style={bubbleStyle}>
        {isUser ? (
          <span data-testid="chat-user-message">{content}</span>
        ) : (
          <span data-testid="chat-assistant-message" dangerouslySetInnerHTML={{ __html: mdToHtml(content) }} />
        )}
      </div>
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
}

export function ChatInterface({ onCustomerIdChange }: ChatInterfaceProps) {
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
  const bottomRef   = useRef<HTMLDivElement>(null);
  const inputRef    = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

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
    const text = input.trim();
    if (!text || loading) return;
    if (!customerId.trim()) { setError('Enter a Customer ID before sending.'); return; }

    const userMsg: LocalMessage = {
      role: 'user', content: text, timestamp: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setAttachedFile(null);
    setError(null);
    setLoading(true);

    try {
      const resp = await apiChat(customerId, customerName || customerId, text);
      const assistantMsg: LocalMessage = {
        role:      'assistant',
        content:   resp.reply,
        timestamp: new Date().toISOString(),
        toolCalls: resp.tool_calls,
        artifacts: resp.artifacts,
        artifactManifest: resp.artifact_manifest,
      };
      setMessages(prev => [...prev, assistantMsg]);
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Error ${e.status}: ${e.detail}`);
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
    minHeight:    '3.5rem',
    maxHeight:    '10rem',
    resize:       'vertical',
    background:   '#0b0d14',
    border:       '1px solid #1c2030',
    borderRadius: 4,
    color:        '#cdd2e0',
    fontFamily:   "'JetBrains Mono', monospace",
    fontSize:     '0.82rem',
    padding:      '0.6rem 0.75rem',
    outline:      'none',
    boxSizing:    'border-box',
  };

  const fieldStyle: React.CSSProperties = {
    background:   '#0b0d14',
    border:       '1px solid #1c2030',
    borderRadius: 4,
    color:        '#cdd2e0',
    fontFamily:   "'JetBrains Mono', monospace",
    fontSize:     '0.78rem',
    padding:      '0.35rem 0.6rem',
    outline:      'none',
    width:        '100%',
    boxSizing:    'border-box',
  };

  const btnPrimary: React.CSSProperties = {
    background:    '#e8571a',
    border:        'none',
    borderRadius:  4,
    color:         '#fff',
    fontFamily:    "'JetBrains Mono', monospace",
    fontSize:      '0.78rem',
    fontWeight:    700,
    padding:       '0.45rem 1rem',
    cursor:        loading ? 'not-allowed' : 'pointer',
    opacity:       loading ? 0.6 : 1,
    letterSpacing: '0.04em',
  };

  const btnSecondary: React.CSSProperties = {
    background:    'transparent',
    border:        '1px solid #1c2030',
    borderRadius:  4,
    color:         '#8b93a8',
    fontFamily:    "'JetBrains Mono', monospace",
    fontSize:      '0.72rem',
    padding:       '0.3rem 0.7rem',
    cursor:        'pointer',
    letterSpacing: '0.04em',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {/* Customer identity fields */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
        <div>
          <label style={{ fontSize: '0.68rem', color: '#8b93a8', display: 'block', marginBottom: '0.2rem' }}>
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
          <label style={{ fontSize: '0.68rem', color: '#8b93a8', display: 'block', marginBottom: '0.2rem' }}>
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
        height:        '480px',
        overflowY:     'auto',
        background:    '#08090d',
        border:        '1px solid #1c2030',
        borderRadius:  6,
        padding:       '1rem',
        display:       'flex',
        flexDirection: 'column',
        gap:           '0.75rem',
      }}>
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
          />
        ))}
        {loading && (
          <div style={{ color: '#8b93a8', fontSize: '0.75rem', alignSelf: 'flex-start' }}>
            ⏳ thinking…
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
          borderRadius: 4,
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
          borderRadius: 4,
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
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-end' }}>
        <input
          ref={fileInputRef}
          type="file"
          accept=".md,.txt,.docx,.pdf"
          style={{ display: 'none' }}
          onChange={handleFileSelect}
        />
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          style={inputStyle}
          value={input}
          placeholder="Type a message… (Enter to send, Shift+Enter for newline)"
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
          <button data-testid="chat-send-button" style={btnPrimary} onClick={sendMessage} disabled={loading}>
            Send
          </button>
          <button
            style={btnSecondary}
            onClick={() => { if (customerId.trim()) fileInputRef.current?.click(); }}
            disabled={attachLoading || !customerId.trim()}
            title="Attach a meeting notes file (.md, .txt, .docx, .pdf)"
          >
            {attachLoading ? '…' : '📎'}
          </button>
          <button style={btnSecondary} onClick={clearHistory} title="Clear conversation history">
            Clear
          </button>
        </div>
      </div>

      <div style={{ fontSize: '0.65rem', color: '#454d64' }}>
        Enter = send · Shift+Enter = newline · 📎 = attach notes file · History persisted per customer.
      </div>
    </div>
  );
}
