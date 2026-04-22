import React, { useMemo, useState } from 'react';

export interface SidebarHistoryItem {
  customer_id: string;
  customer_name?: string;
  last_message?: string;
  last_timestamp?: string;
  status?: string;
}

interface ChatSidebarProps {
  items: SidebarHistoryItem[];
  loading?: boolean;
  activeCustomerId?: string;
  compact?: boolean;
  onSelectCustomer: (customerId: string, customerName?: string) => void;
  onNewChat?: () => void;
}

function statusColor(status?: string): string {
  if (!status) return '#454d64';
  if (status.toLowerCase().includes('needs input')) return '#e8b11a';
  if (status.toLowerCase().includes('completed')) return '#25c26e';
  return '#8b93a8';
}

export function ChatSidebar({
  items,
  loading = false,
  activeCustomerId,
  compact = false,
  onSelectCustomer,
  onNewChat,
}: ChatSidebarProps) {
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const base = [...items].sort((a, b) => {
      const at = a.last_timestamp ? Date.parse(a.last_timestamp) : 0;
      const bt = b.last_timestamp ? Date.parse(b.last_timestamp) : 0;
      return bt - at;
    });
    if (!q) return base;
    return base.filter(i =>
      (i.customer_id ?? '').toLowerCase().includes(q) ||
      (i.customer_name ?? '').toLowerCase().includes(q) ||
      (i.last_message ?? '').toLowerCase().includes(q),
    );
  }, [items, query]);

  return (
    <aside
      id="chat-sidebar-panel"
      role="navigation"
      aria-label="Conversation history"
      data-testid="chat-sidebar"
      style={{
        width: compact ? '100%' : '300px',
        minWidth: compact ? '100%' : '300px',
        border: '1px solid #273149',
        borderRadius: 14,
        background: 'linear-gradient(180deg, #0d111d 0%, #0a0d16 100%)',
        boxShadow: '0 20px 45px rgba(0,0,0,0.35)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div style={{ padding: '0.85rem', borderBottom: '1px solid #273149' }}>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <input
            data-testid="chat-sidebar-search"
            aria-label="Search conversations"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search customers..."
            style={{
              flex: 1,
              background: '#0a0d16',
              border: '1px solid #2b3650',
              borderRadius: 10,
              color: '#cdd2e0',
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: '0.78rem',
              padding: '0.55rem 0.7rem',
            }}
          />
          <button
            data-testid="chat-sidebar-new"
            aria-label="Start new chat"
            onClick={onNewChat}
            style={{
              background: 'linear-gradient(180deg, #ff6a2f 0%, #e8571a 100%)',
              color: '#fff',
              border: 'none',
              borderRadius: 10,
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: '0.76rem',
              fontWeight: 700,
              padding: '0.55rem 0.7rem',
              cursor: 'pointer',
            }}
          >
            New
          </button>
        </div>
      </div>

      <div style={{ overflowY: 'auto', padding: '0.65rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {loading && <div style={{ color: '#8b93a8', fontSize: '0.72rem' }}>Loading history...</div>}
        {!loading && filtered.length === 0 && (
          <div style={{ color: '#8b93a8', fontSize: '0.72rem' }}>No conversations found.</div>
        )}

        {!loading && filtered.map(item => {
          const active = item.customer_id === activeCustomerId;
          return (
            <button
              key={item.customer_id}
              data-testid={`chat-sidebar-item-${item.customer_id}`}
              aria-label={`Open conversation for ${item.customer_name || item.customer_id}`}
              onClick={() => onSelectCustomer(item.customer_id, item.customer_name)}
              style={{
                textAlign: 'left',
                border: active ? '1px solid rgba(232,87,26,0.45)' : '1px solid #273149',
                background: active ? 'rgba(232,87,26,0.14)' : '#121828',
                borderRadius: 10,
                padding: '0.7rem',
                cursor: 'pointer',
                color: '#cdd2e0',
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem' }}>
                <strong style={{ fontSize: '0.76rem', color: '#fff' }}>
                  {item.customer_name || item.customer_id}
                </strong>
                <span style={{ fontSize: '0.66rem', color: '#7f89a4' }}>
                  {item.last_timestamp ? new Date(item.last_timestamp).toLocaleDateString() : ''}
                </span>
              </div>
              <div style={{ fontSize: '0.69rem', color: '#9aa4bb', marginTop: '0.24rem' }}>
                {item.customer_id}
              </div>
              <div
                style={{
                  fontSize: '0.71rem',
                  color: '#c0c8da',
                  marginTop: '0.3rem',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {item.last_message || 'No messages yet'}
              </div>
              {item.status && (
                <div
                  data-testid={`chat-sidebar-status-${item.customer_id}`}
                  style={{
                    marginTop: '0.3rem',
                    fontSize: '0.62rem',
                    color: statusColor(item.status),
                    border: `1px solid ${statusColor(item.status)}55`,
                    borderRadius: 999,
                    padding: '0.1rem 0.45rem',
                    width: 'fit-content',
                  }}
                >
                  {item.status}
                </div>
              )}
            </button>
          );
        })}
      </div>
    </aside>
  );
}
