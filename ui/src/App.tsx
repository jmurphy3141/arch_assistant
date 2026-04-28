import React, { useEffect, useMemo, useState } from 'react';
import { HealthIndicator } from './components/HealthIndicator';
import { GenerateForm } from './components/GenerateForm';
import { ResponseDisplay } from './components/ResponseDisplay';
import { ClarifyForm } from './components/ClarifyForm';
import { NoteUpload } from './components/NoteUpload';
import { PovForm } from './components/PovForm';
import { JepForm } from './components/JepForm';
import { TerraformForm } from './components/TerraformForm';
import { WafForm } from './components/WafForm';
import { BomAdvisor } from './components/BomAdvisor';
import { ChatInterface } from './components/ChatInterface';
import { ChatSidebar, type SidebarHistoryItem, type SidebarProjectItem } from './components/ChatSidebar';
import { ArtifactPreviewPanel } from './components/ArtifactPreviewPanel';
import { useClientId, getLastDiagramName, saveLastDiagramName } from './hooks/useClientId';
import {
  apiClarify,
  apiGetChatProjects,
  apiGetChatHistoryIndex,
  apiRefineDiagram,
  apiWaitForJob,
  type ChatArtifactDownload,
  type GenerateResponse,
  type OrchestrationResult,
} from './api/client';

type Mode = 'chat' | 'generate' | 'bom' | 'notes' | 'pov' | 'jep' | 'terraform' | 'waf';

function getLastCustomerId(): string {
  try { return localStorage.getItem('last_customer_id') ?? ''; } catch { return ''; }
}
function saveLastCustomerId(id: string) {
  try { localStorage.setItem('last_customer_id', id); } catch { /* ignore */ }
}

export function App() {
  const clientId = useClientId();
  const [mode, setMode] = useState<Mode>('chat');
  const [diagramName, setDiagramName] = useState<string>(getLastDiagramName);
  const [customerId, setCustomerId] = useState<string>(getLastCustomerId);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [orchestrationResult, setOrchestrationResult] = useState<OrchestrationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clarifyLoading, setClarifyLoading] = useState(false);
  const [clarifyElapsed, setClarifyElapsed] = useState(0);
  const [refineLoading, setRefineLoading] = useState(false);
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
  const [documentsCollapsed, setDocumentsCollapsed] = useState(true);

  function handleDiagramNameChange(name: string) {
    setDiagramName(name);
    saveLastDiagramName(name);
  }

  function handleCustomerIdChange(id: string) {
    setCustomerId(id);
    saveLastCustomerId(id);
  }

  function handleResult(r: GenerateResponse | OrchestrationResult) {
    if (r.status === 'orchestration_complete') {
      const orch = r as OrchestrationResult;
      setOrchestrationResult(orch);
      setResult(orch.draw_result);
    } else {
      setOrchestrationResult(null);
      setResult(r as GenerateResponse);
    }
    setError(null);
  }

  function handleError(msg: string) {
    setError(msg);
    setResult(null);
  }

  async function handleClarify(
    answers: string,
    opts?: { auto_waf?: boolean; customer_id?: string; customer_name?: string },
  ) {
    setClarifyLoading(true);
    setClarifyElapsed(0);
    try {
      // Prefer stateless path: echo _clarify_context back so the server
      // doesn't need PENDING_CLARIFY (survives restarts, no client_id mismatch).
      const ctx = result?._clarify_context as {
        items_json?: string; prompt?: string; deployment_hints_json?: string;
      } | undefined;
      const pending = await apiClarify({
        answers,
        client_id:    customerId || clientId,
        diagram_name: diagramName,
        ...(ctx?.items_json && ctx?.prompt ? {
          items_json:            ctx.items_json,
          prompt:                ctx.prompt,
          deployment_hints_json: ctx.deployment_hints_json,
        } : {}),
        ...(opts?.auto_waf      ? { auto_waf:      opts.auto_waf      } : {}),
        ...(opts?.customer_id   ? { customer_id:   opts.customer_id   } : {}),
        ...(opts?.customer_name ? { customer_name: opts.customer_name } : {}),
      });
      const r = await apiWaitForJob(pending.job_id, setClarifyElapsed);
      handleResult(r);
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Clarify error ${e.status}: ${e.detail}`);
    } finally {
      setClarifyLoading(false);
      setClarifyElapsed(0);
    }
  }

  async function handleRefine(feedback: string) {
    const ctx = result?._refine_context as {
      items_json?: string; prompt?: string; prev_spec?: string; deployment_hints_json?: string;
    } | undefined;
    if (!ctx?.items_json || !ctx?.prompt) {
      setError('Refine context missing — please regenerate the diagram first.');
      return;
    }
    setRefineLoading(true);
    try {
      const r = await apiRefineDiagram({
        feedback,
        client_id:    customerId || clientId,
        diagram_name: diagramName,
        items_json:   ctx.items_json,
        prompt:       ctx.prompt,
        ...(ctx.prev_spec              ? { prev_spec:              ctx.prev_spec              } : {}),
        ...(ctx.deployment_hints_json  ? { deployment_hints_json:  ctx.deployment_hints_json  } : {}),
      });
      setResult(r);
      setError(null);
    } catch (err: unknown) {
      const e = err as { status: number; detail: string };
      setError(`Refine error ${e.status}: ${e.detail}`);
    } finally {
      setRefineLoading(false);
    }
  }

  function switchMode(m: Mode) {
    setMode(m);
    setMobileSidebarOpen(false);
    if (m === 'generate') {
      setResult(null);
      setOrchestrationResult(null);
      setError(null);
    }
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
            customer_id: item.customer_id,
            customer_name: item.customer_name,
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
              customer_id: item.customer_id,
              customer_name: item.customer_name,
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

  const workspaceTitle: Record<Mode, string> = {
    chat: 'Chat',
    generate: 'Generate Diagram',
    bom: 'BOM Advisor',
    notes: 'Notes',
    pov: 'POV',
    jep: 'JEP',
    terraform: 'Terraform',
    waf: 'WAF Review',
  };

  const mainNav: Array<{ mode: Mode; label: string }> = [
    { mode: 'chat', label: 'Chat' },
    { mode: 'generate', label: 'Generate Diagram' },
    { mode: 'bom', label: 'BOM Advisor' },
  ];

  const documentNav: Array<{ mode: Mode; label: string }> = [
    { mode: 'notes', label: 'Notes' },
    { mode: 'pov', label: 'POV' },
    { mode: 'jep', label: 'JEP' },
    { mode: 'terraform', label: 'Terraform' },
    { mode: 'waf', label: 'WAF Review' },
  ];

  const navButtonStyle = (active: boolean): React.CSSProperties => ({
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0.55rem 0.65rem',
    border: active ? '1px solid rgba(143,180,255,0.5)' : '1px solid transparent',
    background: active ? 'rgba(143,180,255,0.12)' : 'transparent',
    color: active ? '#f4f7ff' : '#c3cad8',
    cursor: active ? 'default' : 'pointer',
    fontWeight: active ? 700 : 500,
    borderRadius: 7,
    fontSize: '0.8rem',
    fontFamily: "'JetBrains Mono', monospace",
    textAlign: 'left',
    transition: 'background 0.15s, border-color 0.15s, color 0.15s',
  });

  const groupHeadingStyle: React.CSSProperties = {
    margin: '1rem 0 0.35rem',
    color: '#7d879a',
    fontSize: '0.72rem',
    fontWeight: 700,
  };

  const documentsToggleStyle: React.CSSProperties = {
    ...groupHeadingStyle,
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 0,
    border: 0,
    background: 'transparent',
    cursor: 'pointer',
    fontFamily: "'JetBrains Mono', monospace",
    textAlign: 'left',
  };

  function handleNewChatFromShell() {
    switchMode('chat');
    handleSidebarNewChat();
  }

  function renderNavButton(item: { mode: Mode; label: string }) {
    const active = mode === item.mode;
    return (
      <button
        key={item.mode}
        data-testid={`sidebar-nav-${item.mode}`}
        aria-current={active ? 'page' : undefined}
        style={navButtonStyle(active)}
        onClick={() => switchMode(item.mode)}
      >
        <span>{item.label}</span>
      </button>
    );
  }

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
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem' }}>
        <h1 style={{ margin: 0, fontFamily: "'Syne', sans-serif", fontSize: '1.35rem', fontWeight: 800, color: '#f7f9ff' }}>
          Archie<span style={{ color: '#8fb4ff' }}>.</span>
        </h1>
        <HealthIndicator />
      </div>

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

      <nav aria-label="Workspace navigation">
        <div style={groupHeadingStyle}>Workspace</div>
        <div style={{ display: 'grid', gap: '0.2rem' }}>
          {mainNav.map(renderNavButton)}
        </div>

        <button
          type="button"
          data-testid="sidebar-documents-toggle"
          aria-expanded={!documentsCollapsed}
          aria-controls="sidebar-documents-nav"
          style={documentsToggleStyle}
          onClick={() => setDocumentsCollapsed(v => !v)}
        >
          <span>Documents</span>
          <span aria-hidden="true">{documentsCollapsed ? 'Show' : 'Hide'}</span>
        </button>
        {!documentsCollapsed && (
          <div id="sidebar-documents-nav" style={{ display: 'grid', gap: '0.2rem' }}>
            {documentNav.map(renderNavButton)}
          </div>
        )}
      </nav>

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
            switchMode('chat');
            handleSidebarSelect(nextCustomerId, nextCustomerName);
          }}
          onNewChat={handleNewChatFromShell}
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
        gridTemplateColumns: isCompactChat ? '1fr' : '300px minmax(0, 1fr)',
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
          maxWidth: mode === 'chat' ? '1240px' : '980px',
          width: '100%',
          margin: mode === 'chat' ? 0 : '0 auto',
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
              {workspaceTitle[mode]}
            </h2>
          </div>
        </header>

        {/* Chat mode */}
        {mode === 'chat' && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: isCompactChat ? '1fr' : 'minmax(0, 1fr) 320px',
            gap: '0.9rem',
            alignItems: 'start',
          }}
        >
          <div style={{ minWidth: 0 }}>
            <ChatInterface
              key={chatSessionKey}
              onCustomerIdChange={handleCustomerIdChange}
              onArtifactsChange={setChatArtifacts}
              projectId={selectedProjectId}
              projectName={selectedProjectName}
            />
          </div>
          {(!isCompactChat || chatArtifacts.length > 0) && (
            <ArtifactPreviewPanel artifacts={chatArtifacts} compact={isCompactChat} />
          )}
        </div>
        )}

        {/* Diagram modes */}
        {mode === 'generate' && (
        <GenerateForm
          clientId={clientId}
          diagramName={diagramName}
          onDiagramNameChange={handleDiagramNameChange}
          onResult={handleResult}
          onError={handleError}
        />
        )}

        {mode === 'bom' && <BomAdvisor />}

        {mode === 'generate' && (
        <>
          {error && (
            <div
              data-testid="error-display"
              style={{
                marginTop: '1rem', padding: '0.75rem',
                background: 'rgba(232,65,90,0.08)',
                border: '1px solid rgba(232,65,90,0.4)',
                borderRadius: 4,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                fontSize: '0.8rem', color: '#e8415a',
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              <strong>Error:</strong> {error}
            </div>
          )}
          {result && result.status === 'ok' && (
            <ResponseDisplay
              result={result}
              orchestrationResult={orchestrationResult ?? undefined}
              onRefine={handleRefine}
              refineLoading={refineLoading}
            />
          )}
          {result && result.status === 'need_clarification' && (
            <ClarifyForm result={result} onSubmit={handleClarify} loading={clarifyLoading} elapsedSec={clarifyElapsed} />
          )}
        </>
        )}

        {/* Document modes */}
        {mode === 'notes' && (
        <NoteUpload customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
        )}

        {mode === 'pov' && (
        <PovForm customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
        )}

        {mode === 'jep' && (
        <JepForm customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
        )}

        {mode === 'terraform' && (
        <TerraformForm customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
        )}

        {mode === 'waf' && (
        <WafForm customerId={customerId} onCustomerIdChange={handleCustomerIdChange} />
        )}
      </main>
    </div>
  );
}
