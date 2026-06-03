import React, { useEffect, useMemo, useState } from 'react';
import { HealthIndicator } from './components/HealthIndicator';
import { BomAdvisor } from './components/BomAdvisor';
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
  const [mode] = useState<'chat' | 'bom'>('chat');
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
  const [chatActivity, setChatActivity] = useState<{ thinkingStatus: string | null; activeHats: string[] }>({ thinkingStatus: null, activeHats: [] });
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [memoryRefreshTrigger, setMemoryRefreshTrigger] = useState(0);

  function handleCustomerIdChange(id: string) {
    setCustomerId(id);
    saveLastCustomerId(id);
  }

  function handleCustomerNameChange(name: string) {
    setCustomerName(name);
    saveLastCustomerName(name);
  }

  const [pendingPrompt, setPendingPrompt] = useState<{ text: string; seq: number } | null>(null);
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
    if (!isCompactChat) {
      setMobileSidebarOpen(false);
    }
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
        // Keep chat usable even if aggregated history is unavailable.
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
        customer_name: normalizedActive,
        engagement_id: normalizedActive,
        project_id: selectedProjectId || normalizedActive,
        project_name: selectedProjectName || normalizedActive,
        last_message: 'Current customer context',
        last_timestamp: new Date().toISOString(),
        status: 'In Progress',
      },
      ...sidebarHistoryItems,
    ];
  }, [customerId, selectedProjectId, selectedProjectName, sidebarHistoryItems]);

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

  function handleNewChatFromShell() {
    handleSidebarNewChat();
  }

  const sidebar = (
    <aside
      data-testid="app-sidebar"
      style={{
        width: isCompactChat ? '100%' : (leftCollapsed ? 52 : 280),
        minWidth: isCompactChat ? '100%' : (leftCollapsed ? 52 : 280),
        height: isCompactChat ? 'auto' : '100vh',
        display: 'flex',
        flexDirection: 'column',
        gap: leftCollapsed ? '0.5rem' : '0.8rem',
        padding: leftCollapsed ? '0.75rem 0' : '1rem',
        borderRight: '1px solid #1a2035',
        borderBottom: isCompactChat ? '1px solid #1a2035' : 'none',
        background: '#0a0c14',
        overflow: 'hidden',
        transition: 'width 0.2s ease, min-width 0.2s ease',
        flexShrink: 0,
      }}
    >
      {/* Header row: brand + collapse toggle */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: leftCollapsed ? 'center' : 'space-between', width: '100%' }}>
        {!leftCollapsed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.55rem' }}>
            <img src="/archie-bear.jpg" alt="" aria-hidden="true"
              style={{ width: 30, height: 30, borderRadius: 6, objectFit: 'cover', flexShrink: 0 }}
              onError={(e) => { e.currentTarget.style.display = 'none'; }}
            />
            <h1 style={{ margin: 0, fontFamily: "'Syne', sans-serif", fontSize: '1.3rem', fontWeight: 800, color: '#f7f9ff', lineHeight: 1 }}>
              Archie<span style={{ color: '#e8571a' }}>.</span>
            </h1>
          </div>
        )}
        {leftCollapsed && (
          <img src="/archie-bear.jpg" alt="Archie"
            style={{ width: 32, height: 32, borderRadius: 7, objectFit: 'cover' }}
            onError={(e) => { e.currentTarget.style.display = 'none'; }}
          />
        )}
        <button
          onClick={() => setLeftCollapsed(v => !v)}
          title={leftCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          style={{
            width: 24, height: 24, borderRadius: 5, display: 'grid', placeItems: 'center',
            background: 'transparent', border: '1px solid #1a2035', color: '#6b7a94',
            cursor: 'pointer', flexShrink: 0, fontSize: '0.7rem',
          }}
        >
          {leftCollapsed ? '›' : '‹'}
        </button>
      </div>

      {!leftCollapsed && (
        <>
          <HealthIndicator />

          <button
            data-testid="sidebar-new-chat"
            onClick={handleNewChatFromShell}
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

          {/* Customer session context */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
            <div style={{ fontSize: '0.62rem', color: '#5a6278', letterSpacing: '0.08em', textTransform: 'uppercase' }}>Session</div>
            <input
              data-testid="chat-customer-id"
              placeholder="Customer ID"
              value={customerId}
              onChange={e => handleCustomerIdChange(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { setChatSessionKey(v => v + 1); setChatArtifacts([]); } }}
              style={{
                background: '#090b11',
                border: '1px solid #252b3d',
                borderRadius: 6,
                color: '#cdd2e0',
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: '0.78rem',
                padding: '0.42rem 0.55rem',
                width: '100%',
                boxSizing: 'border-box' as const,
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
                boxSizing: 'border-box' as const,
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
              onSelectCustomer={(nextCustomerId, nextCustomerName) => {
                handleSidebarSelect(nextCustomerId, nextCustomerName);
              }}
              onNewChat={handleNewChatFromShell}
            />
          </div>

          <div style={{ marginTop: 'auto', fontSize: '0.65rem', color: '#6d7688', borderTop: '1px solid #202638', paddingTop: '0.65rem' }}>
            client_id:<br />
            <code data-testid="client-id-display" style={{ color: '#a9c2ff', wordBreak: 'break-all' }}>{clientId}</code>
          </div>
        </>
      )}
    </aside>
  );

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        overflow: 'hidden',
        fontFamily: "'JetBrains Mono', monospace",
        background: '#04050a',
        color: '#d8e0f0',
      }}
    >
      {isCompactChat && (
        <div style={{ background: '#0a0c14', borderBottom: '1px solid #1a2035', padding: '0.75rem 1rem' }}>
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
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          background: '#050609',
        }}
      >
        <header
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '11px 20px', borderBottom: '1px solid #1a2035', flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{ fontSize: '0.78rem', fontWeight: 700, color: '#d8e0f0' }}>Chat</span>
            {customerId && (
              <>
                <span style={{ color: '#1a2035' }}>/</span>
                <span style={{ fontSize: '0.76rem', fontWeight: 600, color: '#8fb4ff' }}>{customerName || customerId}</span>
              </>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <span style={{ fontSize: '0.6rem', color: '#8b97b0', border: '1px solid #1a2035', borderRadius: 20, padding: '3px 10px' }}>
              {customerId ? `${customerId}` : 'no customer'}
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: '0.6rem', color: '#aeb9d0', border: '1px solid #1a2035', borderRadius: 20, padding: '3px 10px' }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#46d68a', boxShadow: '0 0 6px #46d68a', display: 'inline-block' }} />
              OCI Gen-AI
            </span>
          </div>
        </header>

        {/* Mode workspace */}
        {mode === 'bom' && <BomAdvisor />}

        {mode === 'chat' && (
        /* Chat workspace — full-height flex row: ChatInterface + right context rail */
          <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
            {/* ChatInterface column */}
            <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', padding: '1rem' }}>
              <ChatInterface
                key={chatSessionKey}
                customerId={customerId}
                customerName={customerName}
                onCustomerIdChange={handleCustomerIdChange}
                onCustomerNameChange={handleCustomerNameChange}
                onArtifactsChange={setChatArtifacts}
                onActivityChange={setChatActivity}
                onTurnComplete={() => setMemoryRefreshTrigger(v => v + 1)}
                pendingPrompt={pendingPrompt}
                projectId={selectedProjectId}
                projectName={selectedProjectName}
              />
            </div>

            {/* Right context rail — desktop only */}
            {!isCompactChat && (
              rightCollapsed ? (
                <div style={{
                  width: 44, minWidth: 44, height: '100%', background: '#0a0c14',
                  borderLeft: '1px solid #1a2035', display: 'flex', flexDirection: 'column',
                  alignItems: 'center', padding: '14px 0', gap: 14, flexShrink: 0,
                }}>
                  <button onClick={() => setRightCollapsed(false)} title="Expand context panel"
                    style={{ width: 28, height: 28, borderRadius: 6, display: 'grid', placeItems: 'center',
                      background: 'transparent', border: '1px solid #1a2035', color: '#6b7a94', cursor: 'pointer', fontSize: '0.7rem' }}>
                    ‹
                  </button>
                  <div style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', fontSize: '0.55rem',
                    letterSpacing: '0.2em', color: '#6b7a94', marginTop: 4, textTransform: 'uppercase' }}>
                    Context
                  </div>
                  {chatActivity.activeHats.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5,
                      marginTop: 'auto', fontSize: '0.7rem', fontWeight: 700, color: '#61dafb' }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#61dafb',
                        boxShadow: '0 0 8px #61dafb', display: 'block' }} />
                      {chatActivity.activeHats.length}
                    </div>
                  )}
                </div>
              ) : (
                <div style={{
                  width: 280, minWidth: 280, height: '100%', background: '#0a0c14',
                  borderLeft: '1px solid #1a2035', display: 'flex', flexDirection: 'column',
                  gap: 0, overflowY: 'auto', flexShrink: 0,
                }}>
                  {/* Rail header */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '12px 14px 8px', borderBottom: '1px solid #1a2035', flexShrink: 0 }}>
                    <span style={{ fontSize: '0.56rem', letterSpacing: '0.18em', color: '#6b7a94', textTransform: 'uppercase' }}>Context</span>
                    <button onClick={() => setRightCollapsed(true)} title="Collapse context panel"
                      style={{ width: 24, height: 24, borderRadius: 5, display: 'grid', placeItems: 'center',
                        background: 'transparent', border: '1px solid #1a2035', color: '#6b7a94', cursor: 'pointer', fontSize: '0.7rem' }}>
                      ›
                    </button>
                  </div>
                  {/* Memory + artifacts */}
                  <div style={{ flex: 1, padding: '12px', display: 'flex', flexDirection: 'column', gap: '12px', overflowY: 'auto' }}>
                    <EngagementMemoryPanel
                      customerId={customerId || null}
                      refreshTrigger={chatSessionKey + memoryRefreshTrigger}
                      activity={chatActivity}
                    />
                    {chatArtifacts.length > 0 && (
                      <ArtifactPreviewPanel artifacts={chatArtifacts} compact={true} onQuickPrompt={handleQuickPrompt} />
                    )}
                  </div>
                  {/* Footer stats */}
                  {(chatActivity.activeHats.length > 0 || chatArtifacts.length > 0) && (
                    <div style={{ borderTop: '1px solid #1a2035', padding: '10px 14px', display: 'flex', gap: 8, flexShrink: 0 }}>
                      <div style={{ flex: 1, background: '#0b0d14', border: '1px solid #1a2035', borderRadius: 6,
                        padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <span style={{ fontSize: '0.88rem', fontWeight: 700, color: '#d8e0f0' }}>{chatActivity.activeHats.length}</span>
                        <span style={{ fontSize: '0.56rem', color: '#6b7a94', letterSpacing: '0.08em', textTransform: 'uppercase' }}>hats engaged</span>
                      </div>
                      <div style={{ flex: 1, background: '#0b0d14', border: '1px solid #1a2035', borderRadius: 6,
                        padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <span style={{ fontSize: '0.88rem', fontWeight: 700, color: '#d8e0f0' }}>{chatArtifacts.length}</span>
                        <span style={{ fontSize: '0.56rem', color: '#6b7a94', letterSpacing: '0.08em', textTransform: 'uppercase' }}>artifacts</span>
                      </div>
                    </div>
                  )}
                </div>
              )
            )}

            {/* Compact: artifacts below chat */}
            {isCompactChat && chatArtifacts.length > 0 && (
              <ArtifactPreviewPanel artifacts={chatArtifacts} compact={isCompactChat} onQuickPrompt={handleQuickPrompt} />
            )}
          </div>
        )}
      </main>
    </div>
  );
}
