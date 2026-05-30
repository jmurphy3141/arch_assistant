// ============================================================
// Archie — main app
// ============================================================
const { useState, useRef, useEffect, useCallback } = React;

function App() {
  const [active, setActive] = useState("Chat");
  const [selectedId, setSelectedId] = useState(FOCUSED_ID);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);

  const selected = ENGAGEMENTS.find(e => e.id === selectedId) || ENGAGEMENTS[0];
  const liveCount = ENGAGEMENTS.filter(e => e.status === "live").length;

  const [messages, setMessages] = useState([
    { id: "m1", role: "user", text: "Can you put together a bill of materials for the Acme migration? ~40TB on-prem Oracle, they need HA and cross-region DR." },
    { id: "m2", role: "assistant", text: ACME_BOM_TEXT, hat: "BOM Expert", artifact: ENGAGEMENTS[0].artifacts[0] },
  ]);
  const [input, setInput] = useState("");
  const [streamingId, setStreamingId] = useState(null);
  const [isWorking, setIsWorking] = useState(true);
  const [hats] = useState(["BOM Expert"]);
  const [status, setStatus] = useState(STATUS_CYCLE[0]);
  const [toast, setToast] = useState(null);

  const scrollRef = useRef(null);
  const taRef = useRef(null);

  // Whether the focused engagement is itself live
  const focusedLive = selected.status === "live" && isWorking;

  // cycle the status line while working
  useEffect(() => {
    if (!focusedLive) return;
    let i = 0;
    const t = setInterval(() => {
      i = (i + 1) % STATUS_CYCLE.length;
      setStatus(STATUS_CYCLE[i]);
    }, 2600);
    return () => clearInterval(t);
  }, [focusedLive]);

  // autoscroll
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  });

  const stream = useCallback((id, full) => {
    let i = 0;
    setStreamingId(id);
    const step = () => {
      i += Math.max(1, Math.round(full.length / 220));
      setMessages(prev => prev.map(m => m.id === id ? { ...m, text: full.slice(0, i) } : m));
      if (i < full.length) setTimeout(step, 16);
      else {
        setMessages(prev => prev.map(m => m.id === id ? { ...m, text: full } : m));
        setStreamingId(null);
        setStatus("Ready · monitoring engagement");
      }
    };
    step();
  }, []);

  const send = useCallback(() => {
    const text = input.trim();
    if (!text || streamingId) return;
    const uid = "u" + Date.now(), aid = "a" + Date.now();
    setMessages(prev => [...prev, { id: uid, role: "user", text }]);
    setInput("");
    if (taRef.current) taRef.current.style.height = "auto";
    setIsWorking(true);
    setStatus(STATUS_CYCLE[0]);
    setTimeout(() => {
      setMessages(prev => [...prev, { id: aid, role: "assistant", text: "", hat: "BOM Expert" }]);
      stream(aid, defaultReply());
    }, 480);
  }, [input, streamingId, stream]);

  const onKey = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  const autosize = (e) => {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 130) + "px";
  };
  const onDownload = (a) => { setToast(`Downloading ${a.name}`); setTimeout(() => setToast(null), 2200); };

  // memory + artifacts reflect the focused engagement
  const memoryCustomer = { name: selected.name, challenge: selected.challenge, services: selected.services };
  const liveHats = selected.hat ? [selected.hat] : hats;
  const liveStatus = selected.id === FOCUSED_ID ? status : (selected.task || "Working…");

  return (
    <div style={S.app}>
      <Sidebar
        active={active} setActive={setActive}
        engagements={ENGAGEMENTS} selectedId={selectedId} onSelect={setSelectedId}
        collapsed={leftCollapsed} onToggleCollapse={() => setLeftCollapsed(v => !v)}
      />

      {/* Center: chat */}
      <main style={S.center}>
        <header style={S.chatHead}>
          <div style={S.chatHeadL}>
            <span style={S.tabName}>{active}</span>
            <span style={S.headDiv}>/</span>
            <span style={S.headCust}>{selected.name}</span>
            <span style={S.headFocus}>— {selected.focus}</span>
          </div>
          <div style={S.chatHeadR}>
            <span style={S.ctxChip}>context · 6 docs</span>
            <span style={S.modelChip}><span style={S.modelDot} />OCI Gen-AI</span>
            {rightCollapsed && (
              <button style={S.railToggleBtn} onClick={() => setRightCollapsed(false)} title="Show context panel">
                <PanelRightIcon size={14} />
                {liveCount > 0 && <span style={S.railToggleBadge}>{liveCount}</span>}
              </button>
            )}
          </div>
        </header>

        <div ref={scrollRef} style={S.scroll}>
          <div style={S.thread}>
            <div style={S.dayMark}><span style={S.dayLine} /><span style={S.dayText}>today · 2:47 PM</span><span style={S.dayLine} /></div>
            {messages.map(m =>
              m.role === "user"
                ? <UserMessage key={m.id} text={m.text} />
                : <AssistantMessage key={m.id} text={m.text} hat={m.hat} streaming={streamingId === m.id}>
                    {m.artifact && <ArtifactCard artifact={m.artifact} onDownload={onDownload} />}
                  </AssistantMessage>
            )}
          </div>
        </div>

        <div style={S.composerWrap}>
          <div style={S.composer}>
            <textarea ref={taRef} value={input} onChange={autosize} onKeyDown={onKey} rows={1}
              placeholder={`Ask Archie about ${selected.name}…`} style={S.textarea} />
            <button style={{ ...S.sendBtn, ...(input.trim() && !streamingId ? {} : S.sendBtnOff) }} onClick={send} aria-label="Send">
              <SendIcon size={16} />
            </button>
          </div>
          <div style={S.composerHint}>
            <span><kbd style={S.kbd}>Enter</kbd> to send · <kbd style={S.kbd}>Shift</kbd>+<kbd style={S.kbd}>Enter</kbd> for newline</span>
            <span style={S.composerNote}>Archie reads engagement memory before every reply.</span>
          </div>
        </div>
      </main>

      {/* Right: context rail (collapsible) */}
      {rightCollapsed ? (
        <div style={S.railStrip}>
          <button style={S.railStripBtn} onClick={() => setRightCollapsed(false)} title="Expand context panel">
            <ChevronLeft size={15} />
          </button>
          <div style={S.railStripLabel}>CONTEXT</div>
          {liveCount > 0 && (
            <div style={S.railStripLive}>
              <span style={S.railStripDot} />
              <span>{liveCount}</span>
            </div>
          )}
        </div>
      ) : (
        <div style={S.right}>
          <div style={S.rightHead}>
            <span style={S.rightHeadTitle}>CONTEXT</span>
            <button style={S.rightCollapseBtn} onClick={() => setRightCollapsed(true)} title="Collapse context panel">
              <ChevronRight size={14} />
            </button>
          </div>
          <LiveNowPanel engagements={ENGAGEMENTS} selectedId={selectedId} onSelect={setSelectedId} />
          <MemoryPanel customer={memoryCustomer} live={selected.status === "live"} hats={liveHats} status={liveStatus} />
          <ArtifactPanel artifacts={selected.artifacts} onDownload={onDownload} />
          <div style={S.rightFoot}>
            <div style={S.footStat}><span style={S.footNum}>{liveCount}</span><span style={S.footLbl}>live now</span></div>
            <div style={S.footStat}><span style={S.footNum}>{ENGAGEMENTS.length}</span><span style={S.footLbl}>in progress</span></div>
          </div>
        </div>
      )}

      {toast && <div style={S.toast}>{toast}</div>}
    </div>
  );
}

const S = {
  app: { display: "flex", height: "100%", width: "100%", background: "var(--bg-app)" },

  center: { flex: 1, minWidth: 0, display: "flex", flexDirection: "column", background: "var(--bg-chat)" },
  chatHead: { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "13px 22px",
    borderBottom: "1px solid var(--border)", flexShrink: 0 },
  chatHeadL: { display: "flex", alignItems: "center", gap: 9, minWidth: 0 },
  tabName: { fontSize: "0.8rem", fontWeight: 700, color: "var(--text)", letterSpacing: "0.03em" },
  headDiv: { color: "#2a3553" },
  headCust: { fontSize: "0.78rem", fontWeight: 600, color: "var(--blue)" },
  headFocus: { fontSize: "0.72rem", color: "var(--muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  chatHeadR: { display: "flex", alignItems: "center", gap: 8, flexShrink: 0 },
  ctxChip: { fontSize: "0.62rem", color: "var(--muted-2)", border: "1px solid var(--border)", borderRadius: 20, padding: "3px 10px" },
  modelChip: { display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.62rem", color: "var(--text-dim)", border: "1px solid var(--border)", borderRadius: 20, padding: "3px 10px" },
  modelDot: { width: 6, height: 6, borderRadius: "50%", background: "#46d68a", boxShadow: "0 0 7px #46d68a" },
  railToggleBtn: { position: "relative", display: "grid", placeItems: "center", width: 30, height: 26, borderRadius: 6,
    background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--blue)" },
  railToggleBadge: { position: "absolute", top: -6, right: -6, fontSize: "0.5rem", fontWeight: 700, color: "#06121f",
    background: "var(--cyan)", borderRadius: 10, padding: "0 4px", minWidth: 13, lineHeight: "13px", textAlign: "center" },

  scroll: { flex: 1, overflowY: "auto", padding: "8px 0" },
  thread: { maxWidth: 900, margin: "0 auto", padding: "18px 30px 28px", display: "flex", flexDirection: "column", gap: 24 },
  dayMark: { display: "flex", alignItems: "center", gap: 12, margin: "2px 0 4px" },
  dayLine: { flex: 1, height: 1, background: "var(--border-soft)" },
  dayText: { fontSize: "0.6rem", color: "var(--muted)", letterSpacing: "0.1em", textTransform: "uppercase" },

  composerWrap: { padding: "12px 26px 18px", flexShrink: 0 },
  composer: { maxWidth: 900, margin: "0 auto", display: "flex", alignItems: "flex-end", gap: 10,
    background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: 10, padding: "8px 8px 8px 14px" },
  textarea: { flex: 1, resize: "none", background: "transparent", border: "none", outline: "none",
    color: "var(--text)", fontSize: "0.94rem", lineHeight: 1.5, padding: "6px 0", maxHeight: 130 },
  sendBtn: { width: 36, height: 36, borderRadius: 8, border: "none", display: "grid", placeItems: "center",
    background: "var(--red)", color: "#fff", flexShrink: 0, transition: "opacity .15s, background .15s" },
  sendBtnOff: { background: "#1a2438", color: "var(--muted)", cursor: "default" },
  composerHint: { maxWidth: 900, margin: "8px auto 0", display: "flex", justifyContent: "space-between", fontSize: "0.62rem", color: "var(--muted)" },
  composerNote: { color: "var(--muted)" },
  kbd: { fontFamily: "inherit", fontSize: "0.6rem", background: "#11192c", border: "1px solid var(--border)", borderRadius: 4, padding: "1px 5px", color: "var(--muted-2)" },

  right: { width: 280, minWidth: 280, height: "100%", background: "var(--bg-sidebar)", borderLeft: "1px solid var(--border)",
    padding: 16, display: "flex", flexDirection: "column", gap: 12, overflowY: "auto" },
  rightHead: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 2 },
  rightHeadTitle: { fontSize: "0.56rem", letterSpacing: "0.18em", color: "var(--muted)" },
  rightCollapseBtn: { width: 24, height: 24, borderRadius: 5, display: "grid", placeItems: "center",
    background: "transparent", border: "1px solid var(--border)", color: "var(--muted)" },
  rightFoot: { display: "flex", gap: 10, marginTop: "auto" },
  footStat: { flex: 1, background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "var(--radius)",
    padding: "10px 12px", display: "flex", flexDirection: "column", gap: 3 },
  footNum: { fontSize: "0.92rem", fontWeight: 700, color: "var(--text)" },
  footLbl: { fontSize: "0.56rem", color: "var(--muted)", letterSpacing: "0.08em", textTransform: "uppercase" },

  // collapsed right strip
  railStrip: { width: 44, minWidth: 44, height: "100%", background: "var(--bg-sidebar)", borderLeft: "1px solid var(--border)",
    display: "flex", flexDirection: "column", alignItems: "center", padding: "14px 0", gap: 14 },
  railStripBtn: { width: 28, height: 28, borderRadius: 6, display: "grid", placeItems: "center",
    background: "transparent", border: "1px solid var(--border)", color: "var(--muted)" },
  railStripLabel: { writingMode: "vertical-rl", transform: "rotate(180deg)", fontSize: "0.58rem",
    letterSpacing: "0.22em", color: "var(--muted)", marginTop: 4 },
  railStripLive: { display: "flex", flexDirection: "column", alignItems: "center", gap: 5, marginTop: "auto",
    fontSize: "0.7rem", fontWeight: 700, color: "var(--cyan)" },
  railStripDot: { width: 7, height: 7, borderRadius: "50%", background: "var(--cyan)", boxShadow: "0 0 8px var(--cyan)", animation: "pulse-dot 1.3s infinite ease-in-out" },

  toast: { position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 80,
    background: "var(--bg-panel-hi)", border: "1px solid var(--border)", color: "var(--text)", fontSize: "0.74rem",
    padding: "10px 16px", borderRadius: 8, boxShadow: "0 12px 30px rgba(0,0,0,.6)", animation: "fade-up .2s ease forwards" },
};

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
