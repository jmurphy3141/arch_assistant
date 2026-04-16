/**
 * ChatInterface.tsx
 * ------------------
 * Conversational orchestrator chat UI.
 * Renders a scrollable message thread with tool chips and artifact links.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  apiChat,
  apiGetChatHistory,
  apiClearChatHistory,
  type ChatMessage,
  type ChatToolCall,
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

function MessageBubble({
  msg,
  toolCalls,
  artifacts,
}: {
  msg: { role: string; content?: string; timestamp: string };
  toolCalls?: ChatToolCall[];
  artifacts?: Record<string, string>;
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
        {isUser ? content : (
          <span dangerouslySetInnerHTML={{ __html: mdToHtml(content) }} />
        )}
      </div>
      {toolCalls && toolCalls.length > 0 && (
        <div style={{ paddingLeft: '0.25rem' }}>
          {toolCalls.map((tc, i) => <ToolChip key={i} call={tc} />)}
        </div>
      )}
      {artifacts && Object.keys(artifacts).length > 0 && (
        <div style={{ paddingLeft: '0.25rem', marginTop: '0.25rem', fontSize: '0.7rem', color: '#8b93a8' }}>
          {Object.entries(artifacts).map(([k, v]) => (
            <div key={k}>📎 {k}: <code style={{ color: '#e8571a' }}>{v}</code></div>
          ))}
        </div>
      )}
      <div style={{ fontSize: '0.6rem', color: '#454d64', marginTop: '0.15rem', alignSelf: isUser ? 'flex-end' : 'flex-start' }}>
        {new Date(msg.timestamp).toLocaleTimeString()}
      </div>
    </div>
  );
}

/** Minimal Markdown → HTML (bold, inline code, line breaks). */
function mdToHtml(md: string): string {
  return md
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code style="color:#e8571a;background:rgba(232,87,26,0.1);padding:0 2px;border-radius:2px">$1</code>')
    .replace(/\n/g, '<br/>');
}

// ── Main component ────────────────────────────────────────────────────────────

interface LocalMessage {
  role:      'user' | 'assistant';
  content:   string;
  timestamp: string;
  toolCalls?: ChatToolCall[];
  artifacts?: Record<string, string>;
}

interface ChatInterfaceProps {
  onCustomerIdChange?: (id: string) => void;
}

export function ChatInterface({ onCustomerIdChange }: ChatInterfaceProps) {
  const stored = getStoredCustomer();
  const [customerId,   setCustomerId]   = useState(stored.id);
  const [customerName, setCustomerName] = useState(stored.name);
  const [messages,     setMessages]     = useState<LocalMessage[]>([]);
  const [input,        setInput]        = useState('');
  const [loading,      setLoading]      = useState(false);
  const [error,        setError]        = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef  = useRef<HTMLTextAreaElement>(null);

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
        setMessages(loaded);
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

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading) return;
    if (!customerId.trim()) { setError('Enter a Customer ID before sending.'); return; }

    const userMsg: LocalMessage = {
      role: 'user', content: text, timestamp: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
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
    background:   '#e8571a',
    border:       'none',
    borderRadius: 4,
    color:        '#fff',
    fontFamily:   "'JetBrains Mono', monospace",
    fontSize:     '0.78rem',
    fontWeight:   700,
    padding:      '0.45rem 1rem',
    cursor:       loading ? 'not-allowed' : 'pointer',
    opacity:      loading ? 0.6 : 1,
    letterSpacing: '0.04em',
  };

  const btnSecondary: React.CSSProperties = {
    background:   'transparent',
    border:       '1px solid #1c2030',
    borderRadius: 4,
    color:        '#8b93a8',
    fontFamily:   "'JetBrains Mono', monospace",
    fontSize:     '0.72rem',
    padding:      '0.3rem 0.7rem',
    cursor:       'pointer',
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
            style={fieldStyle}
            value={customerName}
            placeholder="e.g. ACME Corp"
            onChange={e => handleCustomerNameChange(e.target.value)}
          />
        </div>
      </div>

      {/* Message thread */}
      <div style={{
        height:       '480px',
        overflowY:    'auto',
        background:   '#08090d',
        border:       '1px solid #1c2030',
        borderRadius: 6,
        padding:      '1rem',
        display:      'flex',
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
        }}>
          {error}
        </div>
      )}

      {/* Input bar */}
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-end' }}>
        <textarea
          ref={inputRef}
          style={inputStyle}
          value={input}
          placeholder="Type a message… (Enter to send, Shift+Enter for newline)"
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
          <button style={btnPrimary} onClick={sendMessage} disabled={loading}>
            Send
          </button>
          <button style={btnSecondary} onClick={clearHistory} title="Clear conversation history">
            Clear
          </button>
        </div>
      </div>

      <div style={{ fontSize: '0.65rem', color: '#454d64' }}>
        Enter = send · Shift+Enter = newline · History is persisted per customer in OCI Object Storage.
      </div>
    </div>
  );
}
