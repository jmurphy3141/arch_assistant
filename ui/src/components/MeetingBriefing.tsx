import React, { useCallback, useEffect, useState } from 'react';
import {
  apiGetMeetingPrep,
  apiGetBriefing,
  type BriefingResponse,
  type DebriefResult,
} from '../api/client';

interface Props {
  customerId: string | null;
  customerName?: string;
  refreshTrigger?: number;
}

const SECTION: React.CSSProperties = {
  marginBottom: '1rem',
  borderBottom: '1px solid rgba(0, 229, 255, 0.1)',
  paddingBottom: '0.75rem',
};

const LABEL: React.CSSProperties = {
  fontSize: '0.6rem',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.12em',
  color: 'rgba(0, 229, 255, 0.5)',
  marginBottom: '0.35rem',
};

const PILL: React.CSSProperties = {
  display: 'inline-block',
  fontSize: '0.68rem',
  padding: '0.1rem 0.45rem',
  borderRadius: '9999px',
  marginRight: '0.3rem',
  marginBottom: '0.2rem',
};

function phasePill(phase: string) {
  const colors: Record<string, { bg: string; color: string; glow: string }> = {
    Discover:  { bg: 'rgba(0,229,255,0.08)',   color: '#00e5ff',  glow: '0 0 8px rgba(0,229,255,0.3)' },
    Design:    { bg: 'rgba(139,92,246,0.1)',   color: '#a78bfa',  glow: '0 0 8px rgba(139,92,246,0.3)' },
    Develop:   { bg: 'rgba(240,165,0,0.08)',   color: '#f0a500',  glow: '0 0 8px rgba(240,165,0,0.3)' },
    Deliver:   { bg: 'rgba(46,204,138,0.08)',  color: '#2ecc8a',  glow: '0 0 8px rgba(46,204,138,0.3)' },
  };
  const style = colors[phase] ?? { bg: 'rgba(255,255,255,0.05)', color: '#a8c4d8', glow: 'none' };
  return (
    <span style={{ ...PILL, background: style.bg, color: style.color, border: `1px solid ${style.color}55`, boxShadow: style.glow }}>{phase}</span>
  );
}

function DebriefPanel({ debrief }: { debrief: DebriefResult }) {
  const total = debrief.fact_count;
  if (total === 0) return null;
  return (
    <div style={{ background: 'rgba(240, 165, 0, 0.06)', border: '1px solid rgba(240, 165, 0, 0.4)', borderRadius: 6, padding: '0.75rem', marginBottom: '1rem', boxShadow: '0 0 10px rgba(240,165,0,0.08)' }}>
      <div style={{ ...LABEL, color: '#f0a500' }}>Pending Debrief — {total} item{total !== 1 ? 's' : ''} to confirm</div>
      {debrief.stakeholders.length > 0 && (
        <div style={{ marginBottom: '0.4rem' }}>
          <strong style={{ fontSize: '0.75rem' }}>New stakeholders</strong>
          {debrief.stakeholders.map((s, i) => (
            <div key={i} style={{ fontSize: '0.78rem', paddingLeft: '0.5rem' }}>
              {s.name} — {s.role}{s.disposition ? ` (${s.disposition})` : ''}
            </div>
          ))}
        </div>
      )}
      {debrief.action_items.length > 0 && (
        <div style={{ marginBottom: '0.4rem' }}>
          <strong style={{ fontSize: '0.75rem' }}>Action items</strong>
          {debrief.action_items.map((a, i) => (
            <div key={i} style={{ fontSize: '0.78rem', paddingLeft: '0.5rem' }}>
              [{a.owner}] {a.task}{a.due ? ` (due: ${a.due})` : ''}
            </div>
          ))}
        </div>
      )}
      {debrief.objections.length > 0 && (
        <div style={{ marginBottom: '0.4rem' }}>
          <strong style={{ fontSize: '0.75rem' }}>Objections raised</strong>
          {debrief.objections.map((o, i) => (
            <div key={i} style={{ fontSize: '0.78rem', paddingLeft: '0.5rem' }}>
              {o.concern}{o.raised_by ? ` — ${o.raised_by}` : ''}
            </div>
          ))}
        </div>
      )}
      {debrief.commitments.length > 0 && (
        <div>
          <strong style={{ fontSize: '0.75rem' }}>Commitments</strong>
          {debrief.commitments.map((c, i) => (
            <div key={i} style={{ fontSize: '0.78rem', paddingLeft: '0.5rem' }}>
              [{c.who}] {c.what}{c.due ? ` (due: ${c.due})` : ''}
            </div>
          ))}
        </div>
      )}
      <div style={{ fontSize: '0.72rem', color: 'rgba(240,165,0,0.7)', marginTop: '0.5rem' }}>
        Tell Archie "confirm debrief" to save these to engagement context.
      </div>
    </div>
  );
}

export function MeetingBriefing({ customerId, customerName = '', refreshTrigger = 0 }: Props) {
  const [briefing, setBriefing] = useState<BriefingResponse | null>(null);
  const [prep, setPrep] = useState<string>('');
  const [showPrep, setShowPrep] = useState(false);
  const [loading, setLoading] = useState(false);
  const [prepLoading, setPrepLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!customerId) return;
    setLoading(true);
    setError(null);
    try {
      const b = await apiGetBriefing(customerId, customerName);
      setBriefing(b);
    } catch (e: unknown) {
      const err = e as { detail?: string };
      setError(err.detail ?? 'Failed to load briefing');
    } finally {
      setLoading(false);
    }
  }, [customerId, customerName]);

  useEffect(() => { load(); }, [load, refreshTrigger]);

  async function loadPrep() {
    if (!customerId) return;
    setPrepLoading(true);
    try {
      const r = await apiGetMeetingPrep(customerId, customerName);
      setPrep(r.prep);
      setShowPrep(true);
    } finally {
      setPrepLoading(false);
    }
  }

  if (!customerId) {
    return <div style={{ padding: '1rem', color: '#9ca3af', fontSize: '0.85rem' }}>Select a customer to see briefing.</div>;
  }

  if (loading) {
    return <div style={{ padding: '1rem', color: 'rgba(0, 229, 255, 0.45)', fontSize: '0.85rem' }}>Loading briefing…</div>;
  }

  if (error) {
    return <div style={{ padding: '1rem', color: '#ef4444', fontSize: '0.85rem' }}>{error}</div>;
  }

  if (!briefing) return null;

  const { mission, stakeholders, open_objections, open_commitments, open_action_items, pending_debrief } = briefing;
  const phase = mission?.phase ?? '';

  return (
    <div style={{ padding: '0.75rem 1rem', fontSize: '0.82rem', color: '#a8c4d8', fontFamily: "'JetBrains Mono', monospace" }}>

      {/* Phase badge */}
      {phase && (
        <div style={{ marginBottom: '0.75rem' }}>
          <div style={LABEL}>C3E Phase</div>
          {phasePill(phase)}
          {(mission.next_required ?? []).length > 0 && (
            <div style={{ fontSize: '0.72rem', color: 'rgba(0, 229, 255, 0.45)', marginTop: '0.25rem' }}>
              Next: {(mission.next_required ?? []).join(', ')}
            </div>
          )}
          {(mission.blockers ?? []).length > 0 && (
            <div style={{ fontSize: '0.72rem', color: '#dc2626', marginTop: '0.15rem' }}>
              Blocked: {(mission.blockers ?? []).join('; ')}
            </div>
          )}
        </div>
      )}

      {/* Pending debrief */}
      {pending_debrief && pending_debrief.fact_count > 0 && (
        <DebriefPanel debrief={pending_debrief} />
      )}

      {/* Stakeholders */}
      {stakeholders.length > 0 && (
        <div style={SECTION}>
          <div style={LABEL}>Stakeholders</div>
          {stakeholders.map((s, i) => (
            <div key={i} style={{ marginBottom: '0.2rem' }}>
              <strong>{s.name}</strong> <span style={{ color: 'rgba(0, 229, 255, 0.45)' }}>{s.role}</span>
              {s.disposition && (
                <span style={{
                  ...PILL,
                  marginLeft: '0.35rem',
                  background: s.disposition === 'champion' ? 'rgba(46,204,138,0.1)' : s.disposition === 'blocker' ? 'rgba(232,65,90,0.1)' : 'rgba(0,229,255,0.06)',
                  border: `1px solid ${s.disposition === 'champion' ? 'rgba(46,204,138,0.35)' : s.disposition === 'blocker' ? 'rgba(232,65,90,0.35)' : 'rgba(0,229,255,0.2)'}`,
                  color: s.disposition === 'champion' ? '#2ecc8a' : s.disposition === 'blocker' ? '#e8415a' : 'rgba(0,229,255,0.6)',
                }}>
                  {s.disposition}
                </span>
              )}
              {s.notes && <div style={{ fontSize: '0.72rem', color: 'rgba(0, 229, 255, 0.45)', paddingLeft: '0.5rem' }}>{s.notes}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Open objections */}
      {open_objections.length > 0 && (
        <div style={SECTION}>
          <div style={LABEL}>Open Objections</div>
          {open_objections.map((o, i) => (
            <div key={i} style={{ marginBottom: '0.35rem' }}>
              <div>{o.concern}{o.raised_by ? <span style={{ color: 'rgba(0, 229, 255, 0.45)' }}> — {o.raised_by}</span> : ''}</div>
              {o.response && <div style={{ fontSize: '0.72rem', color: 'rgba(0, 229, 255, 0.45)', paddingLeft: '0.5rem' }}>→ {o.response}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Open commitments */}
      {open_commitments.length > 0 && (
        <div style={SECTION}>
          <div style={LABEL}>Commitments</div>
          {open_commitments.map((c, i) => (
            <div key={i} style={{ marginBottom: '0.2rem' }}>
              <span style={{ color: 'rgba(0, 229, 255, 0.45)' }}>[{c.who}]</span> {c.what}
              {c.due && <span style={{ color: '#f0a500', marginLeft: '0.3rem' }}>due {c.due}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Open action items */}
      {open_action_items.length > 0 && (
        <div style={SECTION}>
          <div style={LABEL}>Action Items</div>
          {open_action_items.map((a, i) => (
            <div key={i} style={{ marginBottom: '0.2rem' }}>
              <span style={{ color: 'rgba(0, 229, 255, 0.45)' }}>[{a.owner}]</span> {a.task}
              {a.due && <span style={{ color: '#f0a500', marginLeft: '0.3rem' }}>due {a.due}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Pre-call prep */}
      <div style={{ marginTop: '0.5rem' }}>
        <button
          onClick={showPrep ? () => setShowPrep(false) : loadPrep}
          disabled={prepLoading}
          style={{
            fontSize: '0.78rem',
            padding: '0.3rem 0.75rem',
            background: 'rgba(0, 229, 255, 0.1)',
            color: '#00e5ff',
            border: '1px solid rgba(0, 229, 255, 0.4)',
            borderRadius: 4,
            cursor: 'pointer',
            boxShadow: '0 0 8px rgba(0, 229, 255, 0.2)',
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {prepLoading ? 'Loading…' : showPrep ? 'Hide prep brief' : 'Get pre-call brief'}
        </button>
        {showPrep && prep && (
          <pre style={{
            marginTop: '0.75rem',
            fontSize: '0.75rem',
            background: '#05070d',
            border: '1px solid rgba(0, 229, 255, 0.15)',
            borderRadius: 4,
            padding: '0.75rem',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            color: '#a8c4d8',
            boxShadow: '0 0 12px rgba(0, 229, 255, 0.05)',
          }}>
            {prep}
          </pre>
        )}
      </div>
    </div>
  );
}
