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
  borderBottom: '1px solid #e5e7eb',
  paddingBottom: '0.75rem',
};

const LABEL: React.CSSProperties = {
  fontSize: '0.7rem',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  color: '#6b7280',
  marginBottom: '0.35rem',
};

const PILL: React.CSSProperties = {
  display: 'inline-block',
  fontSize: '0.7rem',
  padding: '0.1rem 0.45rem',
  borderRadius: '9999px',
  marginRight: '0.3rem',
  marginBottom: '0.2rem',
};

function phasePill(phase: string) {
  const colors: Record<string, { bg: string; color: string }> = {
    Discover:  { bg: '#dbeafe', color: '#1d4ed8' },
    Design:    { bg: '#ede9fe', color: '#6d28d9' },
    Develop:   { bg: '#fef9c3', color: '#92400e' },
    Deliver:   { bg: '#dcfce7', color: '#166534' },
  };
  const style = colors[phase] ?? { bg: '#f3f4f6', color: '#374151' };
  return (
    <span style={{ ...PILL, background: style.bg, color: style.color }}>{phase}</span>
  );
}

function DebriefPanel({ debrief }: { debrief: DebriefResult }) {
  const total = debrief.fact_count;
  if (total === 0) return null;
  return (
    <div style={{ background: '#fffbeb', border: '1px solid #fcd34d', borderRadius: 6, padding: '0.75rem', marginBottom: '1rem' }}>
      <div style={{ ...LABEL, color: '#92400e' }}>Pending Debrief — {total} item{total !== 1 ? 's' : ''} to confirm</div>
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
      <div style={{ fontSize: '0.72rem', color: '#92400e', marginTop: '0.5rem' }}>
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
    return <div style={{ padding: '1rem', color: '#6b7280', fontSize: '0.85rem' }}>Loading briefing…</div>;
  }

  if (error) {
    return <div style={{ padding: '1rem', color: '#ef4444', fontSize: '0.85rem' }}>{error}</div>;
  }

  if (!briefing) return null;

  const { mission, stakeholders, open_objections, open_commitments, open_action_items, pending_debrief } = briefing;
  const phase = mission?.phase ?? '';

  return (
    <div style={{ padding: '0.75rem 1rem', fontSize: '0.82rem', color: '#111827' }}>

      {/* Phase badge */}
      {phase && (
        <div style={{ marginBottom: '0.75rem' }}>
          <div style={LABEL}>C3E Phase</div>
          {phasePill(phase)}
          {(mission.next_required ?? []).length > 0 && (
            <div style={{ fontSize: '0.72rem', color: '#6b7280', marginTop: '0.25rem' }}>
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
              <strong>{s.name}</strong> <span style={{ color: '#6b7280' }}>{s.role}</span>
              {s.disposition && (
                <span style={{
                  ...PILL,
                  marginLeft: '0.35rem',
                  background: s.disposition === 'champion' ? '#dcfce7' : s.disposition === 'blocker' ? '#fee2e2' : '#f3f4f6',
                  color: s.disposition === 'champion' ? '#166534' : s.disposition === 'blocker' ? '#991b1b' : '#374151',
                }}>
                  {s.disposition}
                </span>
              )}
              {s.notes && <div style={{ fontSize: '0.72rem', color: '#6b7280', paddingLeft: '0.5rem' }}>{s.notes}</div>}
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
              <div>{o.concern}{o.raised_by ? <span style={{ color: '#6b7280' }}> — {o.raised_by}</span> : ''}</div>
              {o.response && <div style={{ fontSize: '0.72rem', color: '#6b7280', paddingLeft: '0.5rem' }}>→ {o.response}</div>}
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
              <span style={{ color: '#6b7280' }}>[{c.who}]</span> {c.what}
              {c.due && <span style={{ color: '#d97706', marginLeft: '0.3rem' }}>due {c.due}</span>}
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
              <span style={{ color: '#6b7280' }}>[{a.owner}]</span> {a.task}
              {a.due && <span style={{ color: '#d97706', marginLeft: '0.3rem' }}>due {a.due}</span>}
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
            background: '#1e40af',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            cursor: 'pointer',
          }}
        >
          {prepLoading ? 'Loading…' : showPrep ? 'Hide prep brief' : 'Get pre-call brief'}
        </button>
        {showPrep && prep && (
          <pre style={{
            marginTop: '0.75rem',
            fontSize: '0.75rem',
            background: '#f9fafb',
            border: '1px solid #e5e7eb',
            borderRadius: 4,
            padding: '0.75rem',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}>
            {prep}
          </pre>
        )}
      </div>
    </div>
  );
}
