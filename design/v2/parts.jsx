// ============================================================
// Archie — shared UI parts (icons, sidebar, panels, messages)
// ============================================================
const { useState, useRef, useEffect, useMemo } = React;

// ---- Minimal geometric icons (stroke = currentColor) --------
function Icon({ d, size = 14, fill = "none", strokeWidth = 1.6, children, vb = 24 }) {
  return (
    <svg width={size} height={size} viewBox={`0 0 ${vb} ${vb}`} fill={fill}
      stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      {d ? <path d={d} /> : children}
    </svg>
  );
}

const NavIcons = {
  Chat: (p) => <Icon {...p}><path d="M4 5h16v11H8l-4 4z" /></Icon>,
  BOM: (p) => <Icon {...p}><rect x="4" y="4" width="16" height="16" rx="1.5" /><path d="M4 9h16M9 9v11" /></Icon>,
  Diagram: (p) => <Icon {...p}><rect x="3" y="4" width="7" height="6" rx="1" /><rect x="14" y="14" width="7" height="6" rx="1" /><path d="M6.5 10v3.5h11" /></Icon>,
  Terraform: (p) => <Icon {...p}><path d="M10 4l-5 3v6l5 3 5-3" /><path d="M10 10v9" /><path d="M15 7v6" /></Icon>,
  WAF: (p) => <Icon {...p}><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z" /></Icon>,
  JEP: (p) => <Icon {...p}><path d="M5 4h14M5 4v16M19 4v16M5 20h14" /><path d="M9 9h6M9 13h6" /></Icon>,
  Notes: (p) => <Icon {...p}><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5M9 13h7M9 16.5h5" /></Icon>,
  POV: (p) => <Icon {...p}><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="2.4" /></Icon>,
};

function ChevronDown(p) { return <Icon {...p}><path d="M6 9l6 6 6-6" /></Icon>; }
function ChevronLeft(p) { return <Icon {...p}><path d="M15 6l-6 6 6 6" /></Icon>; }
function ChevronRight(p) { return <Icon {...p}><path d="M9 6l6 6-6 6" /></Icon>; }
function SearchIcon(p) { return <Icon {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></Icon>; }
function DownloadIcon(p) { return <Icon {...p}><path d="M12 4v11m0 0l-4-4m4 4l4-4M5 20h14" /></Icon>; }
function SendIcon(p) { return <Icon {...p}><path d="M5 12h13M13 6l6 6-6 6" /></Icon>; }
function PanelRightIcon(p) { return <Icon {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></Icon>; }
function SparkIcon(p) { return <Icon {...p}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18" /></Icon>; }

// Archie's bear mark. `face` zooms into the head for small avatars.
function Bear({ size = 32, radius = 8, face = false, style = {} }) {
  return (
    <span style={{
      width: size, height: size, borderRadius: radius, background: "#000",
      display: "block", flexShrink: 0, overflow: "hidden",
      backgroundImage: "url('assets/archie-bear.jpg')",
      backgroundRepeat: "no-repeat",
      backgroundSize: face ? "190%" : "94%",
      backgroundPosition: face ? "center 12%" : "center 46%",
      ...style,
    }} title="Archie" />
  );
}
function SheetIcon({ size = 26 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect x="4" y="3" width="16" height="18" rx="2" fill="#0f2a1c" stroke="#2f7d57" strokeWidth="1.3" />
      <path d="M4 9h16M4 15h16M10 9v12M16 9v12" stroke="#2f7d57" strokeWidth="1.1" />
    </svg>
  );
}

// ---- Left sidebar -------------------------------------------
const WORKSPACE_NAV = [["Chat", "Chat"], ["Generate Diagram", "Diagram"], ["BOM Advisor", "BOM", "1"]];
const DOC_NAV = [["Notes", "Notes"], ["POV", "POV"], ["JEP", "JEP"], ["Terraform", "Terraform"], ["WAF Review", "WAF"]];

function Sidebar({ active, setActive, engagements, selectedId, onSelect, collapsed, onToggleCollapse }) {
  const [open, setOpen] = useState(false);
  const [docsOpen, setDocsOpen] = useState(true);
  const [query, setQuery] = useState("");

  const selected = engagements.find(e => e.id === selectedId) || engagements[0];
  const liveCount = engagements.filter(e => e.status === "live").length;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? engagements.filter(e => e.name.toLowerCase().includes(q) || e.focus.toLowerCase().includes(q)) : engagements;
    // live first, then idle; preserve order otherwise
    return [...list].sort((a, b) => (a.status === "live" ? 0 : 1) - (b.status === "live" ? 0 : 1));
  }, [engagements, query]);

  const renderNav = ([label, iconKey, badge]) => {
    const I = NavIcons[iconKey];
    const on = active === label;
    return (
      <button key={label} title={collapsed ? label : undefined}
        style={{ ...ps.navItem, ...(collapsed ? ps.navItemCollapsed : {}), ...(on ? ps.navItemActive : {}) }}
        onClick={() => setActive(label)}>
        {on && <span style={ps.navAccent} />}
        <span style={{ color: on ? "var(--red)" : "var(--muted)", display: "flex" }}><I size={15} /></span>
        {!collapsed && <span style={{ flex: 1 }}>{label}</span>}
        {!collapsed && badge && <span style={ps.navBadge}>{badge}</span>}
      </button>
    );
  };

  // ----- collapsed icon rail -----
  if (collapsed) {
    return (
      <aside style={{ ...ps.sidebar, ...ps.sidebarCollapsed }}>
        <button style={ps.railBrand} onClick={onToggleCollapse} title="Expand sidebar">
          <Bear size={34} radius={8} />
        </button>
        <button style={ps.railSel} onClick={onToggleCollapse} title={`${selected.name} · ${selected.focus}`}>
          <span style={{ ...ps.selDot, background: "var(--cyan)" }} />
        </button>
        <div style={ps.railNav}>
          {WORKSPACE_NAV.map(renderNav)}
          <div style={ps.railDivider} />
          {DOC_NAV.map(renderNav)}
        </div>
        <button style={ps.railExpand} onClick={onToggleCollapse} title="Expand sidebar">
          <ChevronRight size={15} />
        </button>
      </aside>
    );
  }

  // ----- full sidebar -----
  return (
    <aside style={ps.sidebar}>
      <div style={ps.brandRow}>
        <Bear size={32} radius={7} style={{ border: "1px solid #3a2316" }} />
        <div style={ps.brandWord}>Archie<span style={ps.brandDot}>.</span></div>
        <button style={ps.collapseBtn} onClick={onToggleCollapse} title="Collapse sidebar">
          <ChevronLeft size={15} />
        </button>
      </div>
      <div style={ps.health}><span style={ps.healthDot} /> all systems operational</div>

      {/* Engagement switcher */}
      <div style={{ position: "relative", marginTop: 18 }}>
        <div style={ps.selLabel}>
          <span>ENGAGEMENT</span>
          <span style={ps.selCount}>{liveCount} live · {engagements.length} active</span>
        </div>
        <button style={ps.selector} onClick={() => setOpen(o => !o)}>
          <span style={{ ...ps.selDot, background: selected.status === "live" ? "var(--cyan)" : "#3a4a6e", boxShadow: selected.status === "live" ? "0 0 8px var(--cyan)" : "none" }} />
          <span style={ps.selText}>
            <span style={ps.selName}>{selected.name}</span>
            <span style={ps.selFocus}>{selected.focus}</span>
          </span>
          <span style={{ color: "var(--muted)", transform: open ? "rotate(180deg)" : "none", transition: "transform .18s" }}>
            <ChevronDown size={13} />
          </span>
        </button>
        {open && (
          <div style={ps.dropdown}>
            <div style={ps.searchRow}>
              <span style={{ color: "var(--muted)", display: "flex" }}><SearchIcon size={13} /></span>
              <input autoFocus value={query} onChange={e => setQuery(e.target.value)}
                placeholder="Search 20 engagements…" style={ps.searchInput} />
            </div>
            <div style={ps.dropList}>
              {filtered.map(c => (
                <button key={c.id} style={{ ...ps.dropItem, ...(c.id === selectedId ? ps.dropItemActive : {}) }}
                  onClick={() => { onSelect(c.id); setOpen(false); setQuery(""); }}>
                  <span style={{ ...ps.dropDot, ...(c.status === "live" ? ps.dropDotLive : {}) }} />
                  <span style={{ flex: 1, overflow: "hidden" }}>
                    <span style={ps.selName}>{c.name}</span>
                    <span style={ps.selFocus}>{c.focus}</span>
                  </span>
                  {c.status === "live"
                    ? <span style={ps.dropLive}>LIVE</span>
                    : <span style={ps.dropAgo}>{c.updated}</span>}
                </button>
              ))}
              {filtered.length === 0 && <div style={ps.noResults}>No engagements match “{query}”.</div>}
            </div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav style={ps.nav}>
        <div style={ps.navGroupLabel}>WORKSPACE</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>{WORKSPACE_NAV.map(renderNav)}</div>
        <button style={ps.docsToggle} onClick={() => setDocsOpen(v => !v)}>
          <span>DOCUMENTS</span>
          <span style={{ color: "var(--muted)", transform: docsOpen ? "none" : "rotate(-90deg)", transition: "transform .18s", display: "flex" }}>
            <ChevronDown size={12} />
          </span>
        </button>
        {docsOpen && <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>{DOC_NAV.map(renderNav)}</div>}
      </nav>

      <div style={ps.sideFoot}>
        <div style={ps.seAvatar}>JM</div>
        <div style={{ overflow: "hidden" }}>
          <div style={ps.seName}>J. Marsh</div>
          <div style={ps.seRole}>Solutions Engineer</div>
        </div>
      </div>
    </aside>
  );
}

// ---- Live Now panel (engagements Archie is working) ---------
function LiveNowPanel({ engagements, selectedId, onSelect }) {
  const live = engagements.filter(e => e.status === "live");
  if (live.length === 0) return null;
  return (
    <section style={pp.card}>
      <div style={pp.headRow}>
        <span style={pp.kicker}>LIVE NOW</span>
        <span style={pp.liveBadge}><span style={pp.liveDot} /> {live.length} WORKING</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {live.map(e => (
          <button key={e.id} style={{ ...pp.liveRow, ...(e.id === selectedId ? pp.liveRowActive : {}) }}
            onClick={() => onSelect(e.id)}>
            <span style={pp.liveRowDot} />
            <span style={{ flex: 1, overflow: "hidden", textAlign: "left" }}>
              <span style={pp.liveRowName}>{e.name}</span>
              <span style={pp.liveRowTask}><span style={pp.miniSpinner} />{e.task}</span>
            </span>
            <span style={pp.liveRowHat}>{e.hat}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

// ---- Engagement memory panel --------------------------------
function MemoryPanel({ customer, live, hats, status }) {
  if (!customer) return null;
  return (
    <section style={pp.card}>
      <div style={pp.headRow}>
        <span style={pp.kicker}>ENGAGEMENT MEMORY</span>
        {live && <span style={pp.liveBadge}><span style={pp.liveDot} /> LIVE</span>}
      </div>
      <div style={pp.custName}>{customer.name}</div>
      <div style={pp.challenge}>{customer.challenge}</div>
      <div style={pp.svcLabel}>OCI SERVICES IN SCOPE</div>
      <div style={pp.svcList}>
        {customer.services.map((s, i) => (
          <React.Fragment key={s}>
            {i > 0 && <span style={pp.svcDivider}>·</span>}
            <span style={pp.svc}>{s}</span>
          </React.Fragment>
        ))}
      </div>
      {live && (
        <div style={pp.liveZone}>
          <div style={pp.hatRow}>
            {hats.map(h => <span key={h} style={pp.hatChip}><span style={pp.hatChipDot} />{h}</span>)}
          </div>
          <div style={pp.statusRow}>
            <span style={pp.spinner} />
            <span style={pp.statusText}>{status}</span>
          </div>
        </div>
      )}
    </section>
  );
}

// ---- Artifact panel -----------------------------------------
function ArtifactPanel({ artifacts, onDownload }) {
  return (
    <section style={pp.card}>
      <div style={pp.headRow}>
        <span style={pp.kicker}>ARTIFACTS</span>
        <span style={pp.artCount}>{artifacts.length}</span>
      </div>
      {artifacts.length === 0 ? (
        <div style={pp.artEmpty}>No artifacts yet for this engagement.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {artifacts.map(a => (
            <div key={a.id} style={{ ...pp.artRow, ...(a.fresh ? pp.artRowFresh : {}) }}>
              <span style={pp.artIcon}>{a.icon}</span>
              <div style={{ overflow: "hidden", flex: 1 }}>
                <div style={pp.artName}>{a.name}</div>
                <div style={pp.artMeta}>{a.hat} · {a.time}</div>
              </div>
              <button style={pp.artDl} title="Download" onClick={() => onDownload(a)}><DownloadIcon size={13} /></button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ---- Chat messages ------------------------------------------
function UserMessage({ text }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", animation: "fade-up .25s ease forwards" }}>
      <div style={pm.userBubble}>{text}</div>
    </div>
  );
}

function AssistantMessage({ text, streaming, hat, children }) {
  return (
    <div style={{ animation: "fade-up .25s ease forwards" }}>
      <div style={pm.asstHead}>
        <span style={pm.asstMark}><Bear size={24} radius={6} face={true} /></span>
        <span style={pm.asstName}>archie</span>
        {hat && <span style={pm.asstHat}>{hat}</span>}
      </div>
      <div style={pm.asstBody}>
        <div style={pm.asstText}>
          {text}
          {streaming && <span style={pm.cursor}>▋</span>}
        </div>
        {children}
      </div>
    </div>
  );
}

function ArtifactCard({ artifact, onDownload }) {
  return (
    <div style={pm.artCard}>
      <span style={pm.artCardIcon}><SheetIcon size={30} /></span>
      <div style={{ flex: 1, overflow: "hidden" }}>
        <div style={pm.artCardName}>{artifact.name}</div>
        <div style={pm.artCardMeta}>{artifact.kind} · {artifact.size} · {artifact.rows}</div>
      </div>
      <button style={pm.dlBtn} onClick={() => onDownload(artifact)}>
        <DownloadIcon size={13} /> Download
      </button>
    </div>
  );
}

// ============================================================
// Styles
// ============================================================
const ps = {
  sidebar: { width: 220, minWidth: 220, height: "100%", background: "var(--bg-sidebar)",
    borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column",
    padding: "18px 14px 14px", transition: "width .18s ease" },
  sidebarCollapsed: { width: 60, minWidth: 60, padding: "18px 9px 14px", alignItems: "center" },

  brandRow: { display: "flex", alignItems: "center", gap: 10, padding: "0 2px 0 4px" },
  brandMark: { width: 30, height: 30, borderRadius: 7, display: "grid", placeItems: "center",
    background: "linear-gradient(150deg,#13203c,#0c1322)", border: "1px solid #243357", color: "var(--cyan)", flexShrink: 0 },
  brandWord: { fontFamily: "var(--display)", fontSize: "1.32rem", fontWeight: 800, color: "#f7f9ff",
    lineHeight: 1, letterSpacing: "0.005em", flex: 1 },
  brandDot: { color: "var(--red)" },
  collapseBtn: { width: 24, height: 24, borderRadius: 5, display: "grid", placeItems: "center",
    background: "transparent", border: "1px solid var(--border)", color: "var(--muted)", flexShrink: 0 },
  health: { display: "flex", alignItems: "center", gap: 7, marginTop: 11, padding: "0 5px",
    fontSize: "0.6rem", color: "var(--muted-2)", letterSpacing: "0.03em" },
  healthDot: { width: 6, height: 6, borderRadius: "50%", background: "#46d68a", boxShadow: "0 0 7px #46d68a" },

  // collapsed rail
  sidebarColl: {},
  railBrand: { width: 34, height: 34, borderRadius: 8, display: "grid", placeItems: "center",
    background: "transparent", border: "none", padding: 0 },
  railSel: { width: 34, height: 34, borderRadius: 8, display: "grid", placeItems: "center", marginTop: 14,
    background: "var(--bg-panel)", border: "1px solid var(--border)" },
  railNav: { display: "flex", flexDirection: "column", gap: 4, marginTop: 16, flex: 1, alignItems: "center" },
  railDivider: { width: 22, height: 1, background: "var(--border-soft)", margin: "6px 0" },
  railExpand: { width: 34, height: 30, borderRadius: 7, display: "grid", placeItems: "center",
    background: "transparent", border: "1px solid var(--border)", color: "var(--muted)" },

  selLabel: { display: "flex", alignItems: "baseline", justifyContent: "space-between",
    fontSize: "0.56rem", letterSpacing: "0.14em", color: "var(--muted)", padding: "0 4px 6px" },
  selCount: { fontSize: "0.52rem", letterSpacing: "0.06em", color: "var(--cyan)" },
  selector: { width: "100%", display: "flex", alignItems: "center", gap: 9, padding: "9px 10px",
    background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "var(--radius)",
    color: "var(--text)", textAlign: "left" },
  selDot: { width: 7, height: 7, borderRadius: "50%", background: "var(--cyan)", flexShrink: 0, boxShadow: "0 0 8px var(--cyan)" },
  selText: { display: "flex", flexDirection: "column", gap: 2, flex: 1, overflow: "hidden" },
  selName: { display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  selFocus: { display: "block", fontSize: "0.64rem", color: "var(--muted-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },

  dropdown: { position: "absolute", top: "calc(100% + 6px)", left: 0, right: 0, zIndex: 30,
    background: "var(--bg-panel-hi)", border: "1px solid var(--border)", borderRadius: "var(--radius)",
    padding: 5, boxShadow: "0 18px 40px rgba(0,0,0,.65)" },
  searchRow: { display: "flex", alignItems: "center", gap: 8, padding: "6px 9px", marginBottom: 4,
    background: "#0a0e1a", border: "1px solid var(--border)", borderRadius: 5 },
  searchInput: { flex: 1, background: "transparent", border: "none", outline: "none", color: "var(--text)", fontSize: "0.74rem" },
  dropList: { maxHeight: 296, overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 },
  dropItem: { display: "flex", alignItems: "center", gap: 9, padding: "8px 9px", borderRadius: 5,
    background: "transparent", border: "none", textAlign: "left", color: "var(--text)", width: "100%" },
  dropItemActive: { background: "#15203a" },
  dropDot: { width: 6, height: 6, borderRadius: "50%", background: "#39456b", flexShrink: 0 },
  dropDotLive: { background: "var(--cyan)", boxShadow: "0 0 7px var(--cyan)", animation: "pulse-dot 1.3s infinite ease-in-out" },
  dropLive: { fontSize: "0.52rem", letterSpacing: "0.1em", color: "var(--cyan)", fontWeight: 700, flexShrink: 0 },
  dropAgo: { fontSize: "0.58rem", color: "var(--muted)", flexShrink: 0 },
  noResults: { padding: "12px 9px", fontSize: "0.68rem", color: "var(--muted)" },

  nav: { display: "flex", flexDirection: "column", gap: 2, marginTop: 18, flex: 1 },
  navGroupLabel: { fontSize: "0.56rem", letterSpacing: "0.16em", color: "var(--muted)", padding: "0 5px 7px" },
  docsToggle: { display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%",
    background: "transparent", border: "none", color: "var(--muted)", fontSize: "0.56rem",
    letterSpacing: "0.16em", padding: "15px 5px 7px", fontFamily: "inherit" },
  navItem: { position: "relative", display: "flex", alignItems: "center", gap: 10, padding: "8px 11px",
    background: "transparent", border: "none", borderRadius: 5, color: "var(--muted-2)",
    fontSize: "0.77rem", letterSpacing: "0.01em", whiteSpace: "nowrap", transition: "background .14s, color .14s" },
  navItemCollapsed: { width: 38, height: 38, justifyContent: "center", gap: 0, padding: 0 },
  navItemActive: { background: "#11192e", color: "var(--text)", fontWeight: 600 },
  navAccent: { position: "absolute", left: 0, top: 7, bottom: 7, width: 2.5, borderRadius: 2, background: "var(--red)", boxShadow: "0 0 8px var(--red)" },
  navBadge: { marginLeft: "auto", fontSize: "0.6rem", background: "#16233f", color: "var(--blue)",
    border: "1px solid #26365c", borderRadius: 20, padding: "1px 7px", fontWeight: 600 },

  sideFoot: { display: "flex", alignItems: "center", gap: 10, padding: "12px 6px 2px",
    borderTop: "1px solid var(--border-soft)", marginTop: 8 },
  seAvatar: { width: 30, height: 30, borderRadius: 7, background: "#16203a", color: "var(--blue)",
    display: "grid", placeItems: "center", fontSize: "0.66rem", fontWeight: 700, flexShrink: 0 },
  seName: { fontSize: "0.76rem", fontWeight: 600, color: "var(--text)" },
  seRole: { fontSize: "0.62rem", color: "var(--muted)" },
};

const pp = {
  card: { background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 14 },
  headRow: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 11 },
  kicker: { fontSize: "0.58rem", letterSpacing: "0.18em", color: "var(--muted)" },
  liveBadge: { display: "inline-flex", alignItems: "center", gap: 5, fontSize: "0.56rem", letterSpacing: "0.12em",
    color: "var(--cyan)", background: "rgba(97,218,251,0.08)", border: "1px solid rgba(97,218,251,0.3)",
    borderRadius: 20, padding: "2px 8px", fontWeight: 600 },
  liveDot: { width: 6, height: 6, borderRadius: "50%", background: "var(--cyan)", boxShadow: "0 0 7px var(--cyan)", animation: "pulse-dot 1.3s infinite ease-in-out" },

  // live now rows
  liveRow: { display: "flex", alignItems: "center", gap: 9, width: "100%", padding: "8px 9px", borderRadius: 5,
    background: "var(--bg-panel-hi)", border: "1px solid var(--border-soft)", textAlign: "left" },
  liveRowActive: { border: "1px solid rgba(97,218,251,0.4)", background: "rgba(97,218,251,0.05)" },
  liveRowDot: { width: 7, height: 7, borderRadius: "50%", background: "var(--cyan)", boxShadow: "0 0 8px var(--cyan)",
    flexShrink: 0, animation: "pulse-dot 1.3s infinite ease-in-out" },
  liveRowName: { display: "block", fontSize: "0.74rem", fontWeight: 600, color: "var(--text)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  liveRowTask: { display: "flex", alignItems: "center", gap: 6, fontSize: "0.6rem", color: "var(--muted-2)", fontStyle: "italic", marginTop: 3 },
  liveRowHat: { fontSize: "0.56rem", fontWeight: 600, color: "var(--blue)", background: "rgba(143,180,255,0.08)",
    border: "1px solid rgba(143,180,255,0.26)", borderRadius: 4, padding: "2px 6px", flexShrink: 0 },
  miniSpinner: { width: 8, height: 8, borderRadius: "50%", border: "1.4px solid #1f2c49", borderTopColor: "var(--cyan)", animation: "spin .8s linear infinite", flexShrink: 0 },

  custName: { fontSize: "0.92rem", fontWeight: 700, color: "var(--text)", letterSpacing: "0.01em" },
  challenge: { fontSize: "0.72rem", color: "var(--muted-2)", lineHeight: 1.5, marginTop: 6,
    display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" },
  svcLabel: { fontSize: "0.55rem", letterSpacing: "0.16em", color: "var(--muted)", marginTop: 13, marginBottom: 6 },
  svcList: { fontSize: "0.72rem", lineHeight: 1.7, color: "var(--services)" },
  svc: { color: "var(--services)" },
  svcDivider: { color: "#3a4a6e", margin: "0 6px" },

  liveZone: { marginTop: 13, paddingTop: 12, borderTop: "1px solid var(--border-soft)" },
  hatRow: { display: "flex", flexWrap: "wrap", gap: 6 },
  hatChip: { display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.66rem", fontWeight: 600,
    color: "var(--blue)", background: "rgba(143,180,255,0.08)", border: "1px solid rgba(143,180,255,0.26)", borderRadius: 5, padding: "4px 9px" },
  hatChipDot: { width: 5, height: 5, borderRadius: "50%", background: "var(--blue)", boxShadow: "0 0 6px var(--blue)" },
  statusRow: { display: "flex", alignItems: "center", gap: 8, marginTop: 10 },
  spinner: { width: 11, height: 11, borderRadius: "50%", border: "1.6px solid #1f2c49", borderTopColor: "var(--cyan)", animation: "spin .8s linear infinite", flexShrink: 0 },
  statusText: { fontSize: "0.7rem", color: "var(--muted-2)", fontStyle: "italic" },

  artCount: { fontSize: "0.6rem", color: "var(--blue)", background: "#13203c", borderRadius: 20, padding: "1px 8px", border: "1px solid #233357" },
  artEmpty: { fontSize: "0.68rem", color: "var(--muted)", lineHeight: 1.5 },
  artRow: { display: "flex", alignItems: "center", gap: 10, padding: "8px 9px", borderRadius: 5, background: "var(--bg-panel-hi)", border: "1px solid var(--border-soft)" },
  artRowFresh: { border: "1px solid rgba(97,218,251,0.32)", background: "rgba(97,218,251,0.04)" },
  artIcon: { fontSize: "0.9rem", flexShrink: 0, display: "flex", color: "var(--blue)" },
  artName: { fontSize: "0.7rem", fontWeight: 600, color: "var(--text)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  artMeta: { fontSize: "0.6rem", color: "var(--muted)", marginTop: 2 },
  artDl: { width: 26, height: 26, borderRadius: 5, display: "grid", placeItems: "center", background: "transparent", border: "1px solid var(--border)", color: "var(--blue)", flexShrink: 0 },
};

const pm = {
  userBubble: { maxWidth: "78%", background: "var(--user-bubble)", border: "1px solid var(--user-bubble-border)",
    color: "var(--text)", padding: "11px 15px", borderRadius: "10px 10px 3px 10px", fontSize: "0.94rem", lineHeight: 1.6 },
  asstHead: { display: "flex", alignItems: "center", gap: 8, marginBottom: 8 },
  asstMark: { width: 24, height: 24, borderRadius: 6, overflow: "hidden", border: "1px solid #3a2316", display: "grid", placeItems: "center", flexShrink: 0 },
  asstName: { fontSize: "0.72rem", fontWeight: 700, color: "var(--text)", letterSpacing: "0.04em" },
  asstHat: { fontSize: "0.6rem", color: "var(--blue)", background: "rgba(143,180,255,0.08)", border: "1px solid rgba(143,180,255,0.24)", borderRadius: 4, padding: "1px 7px", fontWeight: 600 },
  asstBody: { borderLeft: "1px solid #1d2742", paddingLeft: 15, marginLeft: 9 },
  asstText: { fontSize: "0.94rem", lineHeight: 1.68, color: "var(--text-dim)", whiteSpace: "pre-wrap" },
  cursor: { color: "var(--cyan)", marginLeft: 1, animation: "blink 1s step-end infinite", fontWeight: 700 },

  artCard: { display: "flex", alignItems: "center", gap: 13, marginTop: 14, padding: "12px 13px",
    background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "var(--radius)", maxWidth: 440 },
  artCardIcon: { flexShrink: 0, display: "flex" },
  artCardName: { fontSize: "0.82rem", fontWeight: 600, color: "var(--text)" },
  artCardMeta: { fontSize: "0.66rem", color: "var(--muted)", marginTop: 3 },
  dlBtn: { display: "inline-flex", alignItems: "center", gap: 7, fontSize: "0.74rem", fontWeight: 600,
    color: "#06121f", background: "var(--blue)", border: "none", borderRadius: 5, padding: "8px 13px", flexShrink: 0 },
};

Object.assign(window, {
  Sidebar, LiveNowPanel, MemoryPanel, ArtifactPanel,
  UserMessage, AssistantMessage, ArtifactCard,
  SendIcon, SparkIcon, PanelRightIcon, ChevronRight, ChevronLeft,
});
