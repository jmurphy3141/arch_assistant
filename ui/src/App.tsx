import React, { useEffect, useMemo, useState } from 'react';
import { HealthIndicator } from './components/HealthIndicator';
import { ChatInterface } from './components/ChatInterface';
import { ChatSidebar, type SidebarHistoryItem, type SidebarProjectItem } from './components/ChatSidebar';
import { ArtifactPreviewPanel } from './components/ArtifactPreviewPanel';
import { EngagementMemoryPanel } from './components/EngagementMemoryPanel';
import { useClientId } from './hooks/useClientId';
import {
  apiGetChatProjects,
  apiGetChatHistoryIndex,
  type ChatArtifactDownload,
} from './api/client';

const TEAM_MARK_SRC = '/favicon.jpg';

function getLastCustomerId(): string {
  try { return localStorage.getItem('last_customer_id') ?? ''; } catch { return ''; }
}
function saveLastCustomerId(id: string) {
  try { localStorage.setItem('last_customer_id', id); } catch { /* ignore */ }
}
function getLastCustomerName(): string {
  try { return localStorage.getItem('last_customer_name') ?? ''; } catch { return ''; }
}
function saveLastCustomerName(name: string) {
  try { localStorage.setItem('last_customer_name', name); } catch { /* ignore */ }
}

export function App() {
  const clientId = useClientId();
  const [customerId, setCustomerId] = useState<string>(getLastCustomerId);
  const [customerName, setCustomerName] = useState<string>(getLastCustomerName);
  const [chatSessionKey, setChatSessionKey] = useState(0);
  const [sidebarLoading, setSidebarLoading] = useState(false);
  const [sidebarHistoryItems, setSidebarHistoryItems] = useState<SidebarHistoryItem[]>([]);
  const [sidebarProjectItems, setSidebarProjectItems] = useState<SidebarProjectItem[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [selectedProjectName, setSelectedProjectName] = useState('');
  const [isCompactChat, setIsCompactChat] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.innerWidth < 1024;
  });
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [chatArtifacts, setChatArtifacts] = useState<ChatArtifactDownload[]>([]);
  const [memoryRefreshTrigger, setMemoryRefreshTrigger] = useState(0);
  const [pendingPrompt, setPendingPrompt] = useState<{ text: string; seq: number } | null>(null);

  function handleCustomerIdChange(id: string) {
    setCustomerId(id);
    saveLastCustomerId(id);
  }

  function handleCustomerNameChange(name: string) {
    setCustomerName(name);
    saveLastCustomerName(name);
  }

  function handleQuickPrompt(text: string) {
    setPendingPrompt(prev => ({ text, seq: (prev?.seq ?? 0) + 1 }));
  }

  function handleSidebarSelect(nextCustomerId: string, nextCustomerName?: string) {
    const selectedItem = sidebarHistoryItems.find(item => item.customer_id === nextCustomerId);
    try {
      localStorage.setItem('chat_customer_id', nextCustomerId);
      localStorage.setItem('chat_customer_name', nextCustomerName ?? nextCustomerId);
    } catch {
      // ignore
    }
    if (selectedItem?.project_id) {
      setSelectedProjectId(selectedItem.project_id);
      setSelectedProjectName(selectedItem.project_name || selectedItem.customer_name || selectedItem.project_id);
    }
    handleCustomerIdChange(nextCustomerId);
    handleCustomerNameChange(nextCustomerName ?? nextCustomerId);
    setChatSessionKey(v => v + 1);
    setMobileSidebarOpen(false);
    setChatArtifacts([]);
    setMemoryRefreshTrigger(v => v + 1);
  }

  function handleSidebarNewChat() {
    try {
      localStorage.removeItem('chat_customer_id');
      localStorage.removeItem('chat_customer_name');
    } catch {
      // ignore
    }
    handleCustomerIdChange('');
    handleCustomerNameChange('');
    setChatSessionKey(v => v + 1);
    setMobileSidebarOpen(false);
    setChatArtifacts([]);
    setMemoryRefreshTrigger(v => v + 1);
  }

  function handleSidebarProjectSelect(projectId: string, projectName: string) {
    setSelectedProjectId(projectId);
    setSelectedProjectName(projectName);
    setMobileSidebarOpen(false);
  }

  useEffect(() => {
    if (typeof window === 'undefined') return;
    function onResize() {
      setIsCompactChat(window.innerWidth < 1024);
    }
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    if (!isCompactChat) setMobileSidebarOpen(false);
  }, [isCompactChat]);

  useEffect(() => {
    let active = true;
    setSidebarLoading(true);
    Promise.all([
      apiGetChatHistoryIndex(1, 100),
      apiGetChatProjects(1, 100),
    ])
      .then(([historyResp, projectResp]) => {
        if (!active) return;
        setSidebarHistoryItems(
          (historyResp.items ?? []).map(item => ({
            customer_id: item.customer_id || item.engagement_id || item.project_id || '',
            customer_name: item.customer_name || item.customer_id || item.engagement_id || '',
            engagement_id: item.engagement_id ?? item.customer_id,
            project_id: item.project_id,
            project_name: item.project_name,
            last_message: item.last_message_preview,
            last_timestamp: item.last_activity_timestamp,
            status: item.status,
          })),
        );
        setSidebarProjectItems(
          (projectResp.items ?? []).map(project => ({
            project_id: project.project_id,
            project_name: project.project_name,
            engagement_count: project.engagement_count,
            last_message: project.last_message_preview,
            last_timestamp: project.last_activity_timestamp,
            status: project.status,
            engagements: project.engagements.map(item => ({
              customer_id: item.customer_id || item.engagement_id || item.project_id || '',
              customer_name: item.customer_name || item.customer_id || item.engagement_id || '',
              engagement_id: item.engagement_id ?? item.customer_id,
              project_id: item.project_id,
              project_name: item.project_name,
              last_message: item.last_message_preview,
              last_timestamp: item.last_activity_timestamp,
              status: item.status,
            })),
          })),
        );
      })
      .catch(() => {
        if (!active) return;
        setSidebarHistoryItems([]);
        setSidebarProjectItems([]);
      })
      .finally(() => {
        if (!active) return;
        setSidebarLoading(false);
      });
    return () => {
      active = false;
    };
  }, [chatSessionKey]);

  const sidebarItems = useMemo<SidebarHistoryItem[]>(() => {
    const normalizedActive = customerId.trim();
    if (!normalizedActive) return sidebarHistoryItems;
    const exists = sidebarHistoryItems.some(item => item.customer_id === normalizedActive);
    if (exists) return sidebarHistoryItems;
    return [
      {
        customer_id: normalizedActive,
        customer_name: customerName || normalizedActive,
        engagement_id: normalizedActive,
        project_id: selectedProjectId || normalizedActive,
        project_name: selectedProjectName || normalizedActive,
        last_message: 'Current customer context',
        last_timestamp: new Date().toISOString(),
        status: 'In Progress',
      },
      ...sidebarHistoryItems,
    ];
  }, [customerId, customerName, selectedProjectId, selectedProjectName, sidebarHistoryItems]);

  const projectItems = useMemo<SidebarProjectItem[]>(() => {
    if (!selectedProjectId || sidebarProjectItems.some(item => item.project_id === selectedProjectId)) {
      return sidebarProjectItems;
    }
    return [
      {
        project_id: selectedProjectId,
        project_name: selectedProjectName || selectedProjectId,
        engagement_count: customerId ? 1 : 0,
        last_message: customerId ? 'Current project context' : '',
        last_timestamp: customerId ? new Date().toISOString() : '',
        status: 'In Progress',
      },
      ...sidebarProjectItems,
    ];
  }, [customerId, selectedProjectId, selectedProjectName, sidebarProjectItems]);

  const groupHeadingStyle: React.CSSProperties = {
    margin: '1rem 0 0.35rem',
    color: '#7d879a',
    fontSize: '0.72rem',
    fontWeight: 700,
  };

  const sidebar = (
    <aside
      data-testid="app-sidebar"
      style={{
        width: isCompactChat ? '100%' : 300,
        minWidth: isCompactChat ? '100%' : 300,
        height: isCompactChat ? 'auto' : '100vh',
        position: isCompactChat ? 'static' : 'sticky',
        top: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: '0.8rem',
        padding: '1rem',
        borderRight: isCompactChat ? 'none' : '1px solid #202638',
        borderBottom: isCompactChat ? '1px solid #202638' : 'none',
        background: '#0b0d13',
        overflow: 'hidden',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '0.35rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.55rem', minWidth: 0, maxWidth: '100%' }}>
          <img
            src={TEAM_MARK_SRC}
            alt=""
            aria-hidden="true"
            onError={(event) => { event.currentTarget.style.display = 'none'; }}
            style={{ width: 30, height: 30, borderRadius: 6, objectFit: 'cover', flex: '0 0 auto' }}
          />
          <h1 style={{ margin: 0, fontFamily: "'Syne', sans-serif", fontSize: '1.35rem', fontWeight: 800, color: '#f7f9ff', lineHeight: 1 }}>
            Archie<span style={{ color: '#8fb4ff' }}>.</span>
          </h1>
        </div>
        <HealthIndicator />
      </div>

      <button
        data-testid="sidebar-new-chat"
        onClick={handleSidebarNewChat}
        style={{
          width: '100%',
          padding: '0.7rem 0.8rem',
          background: '#d8e4ff',
          border: '1px solid #d8e4ff',
          borderRadius: 7,
          color: '#101624',
          cursor: 'pointer',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: '0.82rem',
          fontWeight: 800,
          textAlign: 'left',
        }}
      >
        New chat
      </button>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
        <div style={{ fontSize: '0.62rem', color: '#5a6278', letterSpacing: '0.08em', textTransform: 'uppercase' }}>Session</div>
        <input
          data-testid="chat-customer-id"
          placeholder="Customer ID"
          value={customerId}
          onChange={e => handleCustomerIdChange(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') {
              setChatSessionKey(v => v + 1);
              setChatArtifacts([]);
              setMemoryRefreshTrigger(v => v + 1);
            }
          }}
          style={{
            background: '#090b11',
            border: '1px solid #252b3d',
            borderRadius: 6,
            color: '#cdd2e0',
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: '0.78rem',
            padding: '0.42rem 0.55rem',
            width: '100%',
            boxSizing: 'border-box',
          }}
        />
        <input
          data-testid="chat-customer-name"
          placeholder="Customer Name"
          value={customerName}
          onChange={e => handleCustomerNameChange(e.target.value)}
          style={{
            background: '#090b11',
            border: '1px solid #252b3d',
            borderRadius: 6,
            color: '#cdd2e0',
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: '0.78rem',
            padding: '0.42rem 0.55rem',
            width: '100%',
            boxSizing: 'border-box',
          }}
        />
      </div>

      <div style={{ minHeight: 0, display: 'flex', flexDirection: 'column', gap: '0.45rem', flex: 1, overflow: 'hidden' }}>
        <div style={groupHeadingStyle}>Conversations</div>
        <ChatSidebar
          items={sidebarItems}
          projects={projectItems}
          loading={sidebarLoading}
          activeCustomerId={customerId}
          activeProjectId={selectedProjectId}
          compact={isCompactChat}
          showNewButton={false}
          onSelectProject={handleSidebarProjectSelect}
          onSelectCustomer={handleSidebarSelect}
          onNewChat={handleSidebarNewChat}
        />
      </div>

      <div style={{ marginTop: 'auto', fontSize: '0.65rem', color: '#6d7688', borderTop: '1px solid #202638', paddingTop: '0.65rem' }}>
        client_id:<br />
        <code data-testid="client-id-display" style={{ color: '#a9c2ff', wordBreak: 'break-all' }}>{clientId}</code>
      </div>
    </aside>
  );

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: isCompactChat ? '1fr' : '300px minmax(0, 1fr) 280px',
        fontFamily: "'JetBrains Mono', monospace",
        background: '#08090d',
        minHeight: '100vh',
        color: '#cdd2e0',
      }}
    >
      {isCompactChat && (
        <div style={{ background: '#0b0d13', borderBottom: '1px solid #202638', padding: '0.75rem 1rem' }}>
          <button
            data-testid="chat-sidebar-toggle"
            aria-controls="chat-sidebar-panel"
            aria-expanded={mobileSidebarOpen}
            onClick={() => setMobileSidebarOpen(v => !v)}
            style={{
              padding: '0.55rem 0.75rem',
              border: '1px solid #252b3d',
              background: '#10141f',
              color: '#d8e4ff',
              borderRadius: 7,
              cursor: 'pointer',
              fontSize: '0.8rem',
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            {mobileSidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
          </button>
        </div>
      )}

      {(!isCompactChat || mobileSidebarOpen) && sidebar}

      <main
        style={{
          minWidth: 0,
          padding: isCompactChat ? '1rem' : '1.4rem',
          width: '100%',
          boxSizing: 'border-box',
        }}
      >
        <header
          style={{
            borderBottom: '1px solid #202638',
            paddingBottom: '0.8rem',
            marginBottom: '1rem',
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'space-between',
            gap: '0.8rem',
            flexWrap: 'wrap',
          }}
        >
          <div>
            <div style={{ color: '#7d879a', fontSize: '0.76rem', marginBottom: '0.1rem' }}>
              {customerId ? `Customer context: ${customerId}` : 'No customer selected'}
            </div>
            <h2 style={{ margin: 0, fontSize: '1.15rem', color: '#f4f7ff', fontWeight: 800 }}>
              Chat
            </h2>
          </div>
        </header>

        <ChatInterface
          key={chatSessionKey}
          customerId={customerId}
          customerName={customerName}
          onCustomerIdChange={handleCustomerIdChange}
          onCustomerNameChange={handleCustomerNameChange}
          onArtifactsChange={setChatArtifacts}
          onConversationComplete={() => setMemoryRefreshTrigger(v => v + 1)}
          pendingPrompt={pendingPrompt}
          projectId={selectedProjectId}
          projectName={selectedProjectName}
        />
        {chatArtifacts.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <ArtifactPreviewPanel artifacts={chatArtifacts} compact={isCompactChat} onQuickPrompt={handleQuickPrompt} />
          </div>
        )}
      </main>

      {!isCompactChat && (
        <div style={{ padding: '1.4rem 1.4rem 1.4rem 0' }}>
          <EngagementMemoryPanel customerId={customerId || null} refreshTrigger={memoryRefreshTrigger} />
        </div>
      )}
      {isCompactChat && customerId.trim() && (
        <div style={{ padding: '0 1rem 1rem' }}>
          <EngagementMemoryPanel customerId={customerId} refreshTrigger={memoryRefreshTrigger} />
        </div>
      )}
    </div>
  );
}
