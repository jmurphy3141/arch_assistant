import React, { useMemo, useState } from 'react';

export interface SidebarHistoryItem {
  customer_id: string;
  customer_name?: string;
  engagement_id?: string;
  project_id?: string;
  project_name?: string;
  last_message?: string;
  last_timestamp?: string;
  status?: string;
}

export interface SidebarProjectItem {
  project_id: string;
  project_name: string;
  engagement_count?: number;
  last_message?: string;
  last_timestamp?: string;
  status?: string;
  engagements?: SidebarHistoryItem[];
}

interface ChatSidebarProps {
  items: SidebarHistoryItem[];
  projects?: SidebarProjectItem[];
  loading?: boolean;
  activeCustomerId?: string;
  activeProjectId?: string;
  compact?: boolean;
  showNewButton?: boolean;
  onSelectProject?: (projectId: string, projectName: string) => void;
  onSelectCustomer: (customerId: string, customerName?: string) => void;
  onNewChat?: () => void;
}

function statusColor(status?: string): string {
  if (!status) return '#454d64';
  const s = status.toLowerCase();
  if (s.includes('needs input')) return '#e8b11a';
  if (s.includes('completed'))   return '#25c26e';
  if (s.includes('in progress')) return '#00e5ff';
  return '#8b93a8';
}

function relativeTime(ts?: string): string {
  if (!ts) return '';
  const diff = Date.now() - Date.parse(ts);
  const min = Math.floor(diff / 60000);
  if (min < 2)   return 'just now';
  if (min < 60)  return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24)   return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d === 1)   return 'yesterday';
  if (d < 7)     return `${d}d ago`;
  return new Date(ts).toLocaleDateString();
}

export function ChatSidebar({
  items,
  projects = [],
  loading = false,
  activeCustomerId,
  activeProjectId,
  compact = false,
  showNewButton = true,
  onSelectProject,
  onSelectCustomer,
  onNewChat,
}: ChatSidebarProps) {
  const [query, setQuery] = useState('');

  const filteredProjects = useMemo(() => {
    const q = query.trim().toLowerCase();
    const source = projects.length > 0
      ? projects
      : items.map(item => ({
        project_id: item.project_id || item.customer_id,
        project_name: item.project_name || item.customer_name || item.customer_id,
        engagement_count: 1,
        last_message: item.last_message,
        last_timestamp: item.last_timestamp,
        status: item.status,
        engagements: [item],
      }));
    const base = [...source].sort((a, b) => {
      const at = a.last_timestamp ? Date.parse(a.last_timestamp) : 0;
      const bt = b.last_timestamp ? Date.parse(b.last_timestamp) : 0;
      return bt - at;
    });
    if (!q) return base;
    return base.filter(i =>
      (i.project_id ?? '').toLowerCase().includes(q) ||
      (i.project_name ?? '').toLowerCase().includes(q) ||
      (i.last_message ?? '').toLowerCase().includes(q) ||
      (i.engagements ?? []).some(engagement =>
        (engagement.customer_id ?? '').toLowerCase().includes(q) ||
        (engagement.customer_name ?? '').toLowerCase().includes(q) ||
        (engagement.last_message ?? '').toLowerCase().includes(q),
      ),
    );
  }, [items, projects, query]);

  const selectedProjectId = activeProjectId || '';
  const filteredChats = useMemo(() => {
    const q = query.trim().toLowerCase();
    const source = selectedProjectId
      ? items.filter(item => (item.project_id || item.customer_id) === selectedProjectId)
      : items;
    const base = [...source].sort((a, b) => {
      const at = a.last_timestamp ? Date.parse(a.last_timestamp) : 0;
      const bt = b.last_timestamp ? Date.parse(b.last_timestamp) : 0;
      return bt - at;
    });
    if (!q) return base;
    return base.filter(i =>
      (i.customer_id ?? '').toLowerCase().includes(q) ||
      (i.customer_name ?? '').toLowerCase().includes(q) ||
      (i.project_name ?? '').toLowerCase().includes(q) ||
      (i.last_message ?? '').toLowerCase().includes(q),
    );
  }, [activeProjectId, items, query, selectedProjectId]);

  return (
    <aside
      id="chat-sidebar-panel"
      role="navigation"
      aria-label="Conversation history"
      data-testid="chat-sidebar"
      style={{
        width: '100%',
        minWidth: compact ? '100%' : 0,
        minHeight: 0,
        flex: '1 1 auto',
        border: '1px solid #252b3d',
        borderRadius: 8,
        background: '#0b0e15',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div style={{ padding: '0.65rem', borderBottom: '1px solid #252b3d' }}>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <input
            data-testid="chat-sidebar-search"
            aria-label="Search conversations"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search customers..."
            style={{
              flex: 1,
              minWidth: 0,
              background: '#090b11',
              border: '1px solid #252b3d',
              borderRadius: 6,
              color: '#cdd2e0',
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: '0.78rem',
              padding: '0.5rem 0.6rem',
            }}
          />
          {showNewButton && (
            <button
              data-testid="chat-sidebar-new"
              aria-label="Start new chat"
              onClick={onNewChat}
              style={{
                background: '#d8e4ff',
                color: '#111827',
                border: 'none',
                borderRadius: 6,
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: '0.76rem',
                fontWeight: 700,
                padding: '0.5rem 0.65rem',
                cursor: 'pointer',
              }}
            >
              New
            </button>
          )}
        </div>
      </div>

      <div style={{
        minHeight: 0,
        flex: '1 1 auto',
        display: 'grid',
        gridTemplateRows: 'minmax(7rem, 0.9fr) minmax(8rem, 1.1fr)',
        gap: '0.5rem',
        padding: '0.55rem',
      }}>
        <section style={{ minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{
            color: '#5a6278',
            fontSize: '0.6rem',
            fontWeight: 700,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            marginBottom: '0.35rem',
            paddingTop: '0.2rem',
            borderTop: '1px solid #1a1f2e',
          }}>
            Projects
          </div>
          <div
            data-testid="chat-sidebar-project-list"
            style={{
              minHeight: 0,
              flex: '1 1 auto',
              overflowY: 'auto',
              overscrollBehavior: 'contain',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.4rem',
            }}
          >
            {loading && <div style={{ color: '#8b93a8', fontSize: '0.72rem' }}>Loading projects...</div>}
            {!loading && filteredProjects.length === 0 && (
              <div style={{ color: '#8b93a8', fontSize: '0.72rem' }}>No projects found.</div>
            )}
            {!loading && filteredProjects.map(project => {
              const active = Boolean(selectedProjectId) && project.project_id === selectedProjectId;
              return (
                <button
                  key={project.project_id}
                  data-testid={`chat-sidebar-project-${project.project_id}`}
                  aria-label={`Select project ${project.project_name || project.project_id}`}
                  onClick={() => onSelectProject?.(project.project_id, project.project_name)}
                  style={{
                    textAlign: 'left',
                    border: active ? '1px solid rgba(143,180,255,0.35)' : '1px solid #1c2233',
                    borderLeft: active ? '3px solid #8fb4ff' : '3px solid transparent',
                    background: active ? 'rgba(143,180,255,0.08)' : '#0e111a',
                    borderRadius: 7,
                    padding: '0.58rem 0.65rem',
                    cursor: active ? 'default' : 'pointer',
                    color: '#cdd2e0',
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem' }}>
                    <strong style={{ fontSize: '0.74rem', color: '#fff' }}>
                      {project.project_name || project.project_id}
                    </strong>
                    <span style={{ fontSize: '0.66rem', color: '#7f89a4' }}>
                      {project.engagement_count ?? project.engagements?.length ?? 0}
                    </span>
                  </div>
                  <div style={{ fontSize: '0.66rem', color: '#8b93a8', marginTop: '0.22rem' }}>
                    {project.project_id}
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section style={{ minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{
            color: '#5a6278',
            fontSize: '0.6rem',
            fontWeight: 700,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            marginBottom: '0.35rem',
            paddingTop: '0.2rem',
            borderTop: '1px solid #1a1f2e',
          }}>
            Chats
          </div>
          <div
            data-testid="chat-sidebar-chat-list"
            style={{
              minHeight: 0,
              flex: '1 1 auto',
              overflowY: 'auto',
              overscrollBehavior: 'contain',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.45rem',
            }}
          >
            {loading && <div style={{ color: '#8b93a8', fontSize: '0.72rem' }}>Loading history...</div>}
            {!loading && filteredChats.length === 0 && (
              <div style={{ color: '#8b93a8', fontSize: '0.72rem' }}>No conversations found.</div>
            )}

            {!loading && filteredChats.map(item => {
              const active = item.customer_id === activeCustomerId;
              return (
                <button
                  key={item.customer_id}
                  data-testid={`chat-sidebar-item-${item.customer_id}`}
                  aria-label={`Open conversation for ${item.customer_name || item.customer_id}`}
                  onClick={() => onSelectCustomer(item.customer_id, item.customer_name)}
                  style={{
                    textAlign: 'left',
                    border: active ? '1px solid rgba(0,229,255,0.35)' : '1px solid #1c2233',
                    borderLeft: active ? '3px solid #00e5ff' : '3px solid transparent',
                    background: active ? 'rgba(0,229,255,0.08)' : '#0e111a',
                    borderRadius: 7,
                    padding: '0.58rem 0.65rem',
                    cursor: active ? 'default' : 'pointer',
                    color: '#cdd2e0',
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem' }}>
                    <strong style={{ fontSize: '0.76rem', color: '#fff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0, flex: 1 }}>
                      {item.customer_name || item.customer_id}
                    </strong>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', flex: '0 0 auto' }}>
                      {item.status && (
                        <span
                          data-testid={`chat-sidebar-status-${item.customer_id}`}
                          style={{
                            fontSize: '0.58rem',
                            color: statusColor(item.status),
                            border: `1px solid ${statusColor(item.status)}55`,
                            borderRadius: 999,
                            padding: '0.08rem 0.35rem',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {item.status}
                        </span>
                      )}
                      <span style={{ fontSize: '0.62rem', color: '#5a6278', whiteSpace: 'nowrap' }}>
                        {relativeTime(item.last_timestamp)}
                      </span>
                    </div>
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
                </button>
              );
            })}
          </div>
        </section>
      </div>
    </aside>
  );
}
