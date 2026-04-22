import React, { useEffect, useMemo, useState } from 'react';
import {
  apiBomChat,
  apiBomConfig,
  apiBomGenerateXlsx,
  apiBomHealth,
  apiBomRefreshData,
  type BomChatResponse,
  type BomPayload,
  type BomPayloadLineItem,
  type ApiError,
} from '../api/client';

interface ChatLine {
  role: 'user' | 'assistant';
  text: string;
}

const panel: React.CSSProperties = {
  background: '#12151d',
  border: '1px solid #1c2030',
  borderRadius: 6,
  padding: '1rem 1.1rem',
  fontFamily: "'JetBrains Mono', monospace",
};

function formatCurrency(v: number): string {
  return `$${Number(v || 0).toFixed(2)}`;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function BomAdvisor() {
  const [health, setHealth] = useState<{ ready: boolean; source: string; pricing_sku_count: number } | null>(null);
  const [modelId, setModelId] = useState('');
  const [lines, setLines] = useState<ChatLine[]>([]);
  const [prompt, setPrompt] = useState('Generate a BOM for 16 OCPU, 256 GB RAM, 2 TB block storage, with load balancer.');
  const [pending, setPending] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<BomChatResponse | null>(null);
  const [payload, setPayload] = useState<BomPayload | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([apiBomHealth(), apiBomConfig()])
      .then(([h, c]) => {
        if (!active) return;
        setHealth({ ready: h.ready, source: h.source, pricing_sku_count: h.pricing_sku_count });
        setModelId(c.default_model_id ?? '');
      })
      .catch(() => {
        if (!active) return;
        setHealth({ ready: false, source: 'unknown', pricing_sku_count: 0 });
      });
    return () => { active = false; };
  }, []);

  const total = useMemo(() => {
    if (!payload?.line_items) return 0;
    return payload.line_items.reduce((acc, item) => acc + Number(item.quantity || 0) * Number(item.unit_price || 0), 0);
  }, [payload]);

  function updateLine(idx: number, key: keyof BomPayloadLineItem, value: string) {
    if (!payload) return;
    const next = { ...payload, line_items: payload.line_items.map((line, i) => {
      if (i !== idx) return line;
      if (key === 'quantity' || key === 'unit_price') {
        const num = Number(value);
        return { ...line, [key]: Number.isFinite(num) ? num : 0 };
      }
      return { ...line, [key]: value };
    }) };
    setPayload(next);
  }

  async function send() {
    const msg = prompt.trim();
    if (!msg || pending) return;
    setPending(true);
    setError(null);
    const convo = [...lines, { role: 'user' as const, text: msg }];
    setLines(convo);
    setPrompt('');
    try {
      const resp = await apiBomChat({
        message: msg,
        conversation: convo.map(line => ({ role: line.role, content: line.text })),
      });
      setLast(resp);
      setLines(prev => [...prev, { role: 'assistant', text: resp.reply }]);
      if (resp.bom_payload) {
        setPayload(resp.bom_payload);
      }
      const h = await apiBomHealth();
      setHealth({ ready: h.ready, source: h.source, pricing_sku_count: h.pricing_sku_count });
    } catch (e) {
      const err = e as ApiError;
      setError(`BOM chat failed (${err.status}): ${err.detail}`);
    } finally {
      setPending(false);
    }
  }

  async function refreshData() {
    setRefreshing(true);
    setError(null);
    try {
      const r = await apiBomRefreshData();
      setHealth({ ready: r.ready, source: r.source, pricing_sku_count: r.pricing_sku_count });
      setLines(prev => [...prev, { role: 'assistant', text: `BOM cache refresh complete. Source=${r.source}, SKUs=${r.pricing_sku_count}.` }]);
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 403) {
        setError('Refresh denied (403): your account is not authorized for BOM admin refresh.');
      } else {
        setError(`Refresh failed (${err.status}): ${err.detail}`);
      }
    } finally {
      setRefreshing(false);
    }
  }

  function downloadJson() {
    if (!payload) return;
    const blob = new Blob([JSON.stringify({ ...payload, totals: { estimated_monthly_cost: total } }, null, 2)], {
      type: 'application/json',
    });
    downloadBlob(blob, 'oci-bom.json');
  }

  async function exportXlsx() {
    if (!payload) return;
    setExporting(true);
    setError(null);
    try {
      const normalized: BomPayload = {
        ...payload,
        totals: { estimated_monthly_cost: total },
      };
      const blob = await apiBomGenerateXlsx(normalized);
      downloadBlob(blob, 'oci-bom.xlsx');
    } catch (e) {
      const err = e as ApiError;
      setError(`XLSX export failed (${err.status}): ${err.detail}`);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div style={{ display: 'grid', gap: '0.9rem' }}>
      <div style={panel}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.8rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <div>
            <h3 style={{ margin: 0, color: '#fff', fontFamily: "'Syne', sans-serif" }}>BOM Advisor</h3>
            <div style={{ marginTop: '0.35rem', fontSize: '0.75rem', color: '#8b93a8' }}>
              cache: {health?.ready ? 'ready' : 'not ready'} · source: {health?.source ?? 'unknown'} · skus: {health?.pricing_sku_count ?? 0}
            </div>
            {modelId && <div style={{ marginTop: '0.2rem', fontSize: '0.72rem', color: '#6f7891' }}>model: {modelId}</div>}
          </div>
          <button onClick={refreshData} disabled={refreshing} style={{
            padding: '0.45rem 0.75rem', border: '1px solid #e8571a', background: 'rgba(232,87,26,0.12)', color: '#e8571a', borderRadius: 4,
            fontSize: '0.75rem', cursor: refreshing ? 'default' : 'pointer',
          }}>
            {refreshing ? 'Refreshing…' : 'Refresh BOM Data'}
          </button>
        </div>
      </div>

      <div style={panel}>
        <div style={{ maxHeight: '15rem', overflowY: 'auto', marginBottom: '0.75rem', display: 'grid', gap: '0.45rem' }}>
          {lines.length === 0 && (
            <div style={{ color: '#6f7891', fontSize: '0.78rem' }}>
              Start with a workload sizing request. If cache is not ready, use refresh first.
            </div>
          )}
          {lines.map((line, idx) => (
            <div key={`${line.role}-${idx}`} style={{
              background: line.role === 'user' ? 'rgba(232,87,26,0.12)' : '#0e1016',
              border: `1px solid ${line.role === 'user' ? 'rgba(232,87,26,0.4)' : '#1c2030'}`,
              color: '#cdd2e0', padding: '0.55rem 0.65rem', borderRadius: 6, fontSize: '0.78rem', whiteSpace: 'pre-wrap',
            }}>
              <strong style={{ color: line.role === 'user' ? '#e8571a' : '#fff' }}>{line.role}:</strong> {line.text}
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <input
            data-testid="bom-input"
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            placeholder="Ask BOM advisor..."
            style={{
              flex: 1,
              background: '#0e1016',
              border: '1px solid #1c2030',
              borderRadius: 4,
              color: '#cdd2e0',
              padding: '0.5rem 0.6rem',
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: '0.78rem',
            }}
          />
          <button data-testid="bom-send" onClick={send} disabled={pending} style={{
            padding: '0.5rem 0.9rem', border: '1px solid #e8571a', borderRadius: 4, background: '#e8571a', color: '#fff',
            fontSize: '0.76rem', fontWeight: 700, cursor: pending ? 'default' : 'pointer',
          }}>{pending ? 'Sending…' : 'Send'}</button>
        </div>
        {last?.trace_id && (
          <div style={{ marginTop: '0.5rem', color: '#6f7891', fontSize: '0.7rem' }}>trace_id: {last.trace_id}</div>
        )}
      </div>

      {payload && (
        <div style={panel}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.8rem', flexWrap: 'wrap' }}>
            <h4 style={{ margin: 0, color: '#fff', fontFamily: "'Syne', sans-serif", fontSize: '1rem' }}>Final BOM (Editable)</h4>
            <div style={{ display: 'flex', gap: '0.45rem' }}>
              <button onClick={downloadJson} style={{ padding: '0.4rem 0.65rem', border: '1px solid #1c2030', borderRadius: 4, background: '#0e1016', color: '#cdd2e0', cursor: 'pointer', fontSize: '0.74rem' }}>Download JSON</button>
              <button onClick={exportXlsx} disabled={exporting} style={{ padding: '0.4rem 0.65rem', border: '1px solid #e8571a', borderRadius: 4, background: '#e8571a', color: '#fff', cursor: exporting ? 'default' : 'pointer', fontSize: '0.74rem' }}>{exporting ? 'Exporting…' : 'Export XLSX'}</button>
            </div>
          </div>

          <div style={{ overflowX: 'auto', marginTop: '0.7rem' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.74rem' }}>
              <thead>
                <tr>
                  {['SKU', 'Description', 'Category', 'Qty', 'Unit Price', 'Extended Price', 'Notes'].map(label => (
                    <th key={label} style={{ borderBottom: '1px solid #1c2030', textAlign: 'left', color: '#8b93a8', padding: '0.35rem' }}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {payload.line_items.map((item, idx) => {
                  const ext = Number(item.quantity || 0) * Number(item.unit_price || 0);
                  return (
                    <tr key={`${item.sku}-${idx}`}>
                      <td style={{ padding: '0.3rem' }}><input value={item.sku} onChange={e => updateLine(idx, 'sku', e.target.value)} style={{ width: '9rem', background: '#0e1016', color: '#cdd2e0', border: '1px solid #1c2030', borderRadius: 4, padding: '0.2rem 0.3rem' }} /></td>
                      <td style={{ padding: '0.3rem' }}><input value={item.description} onChange={e => updateLine(idx, 'description', e.target.value)} style={{ width: '16rem', background: '#0e1016', color: '#cdd2e0', border: '1px solid #1c2030', borderRadius: 4, padding: '0.2rem 0.3rem' }} /></td>
                      <td style={{ padding: '0.3rem' }}><input value={item.category} onChange={e => updateLine(idx, 'category', e.target.value)} style={{ width: '8rem', background: '#0e1016', color: '#cdd2e0', border: '1px solid #1c2030', borderRadius: 4, padding: '0.2rem 0.3rem' }} /></td>
                      <td style={{ padding: '0.3rem' }}><input value={String(item.quantity)} onChange={e => updateLine(idx, 'quantity', e.target.value)} style={{ width: '5rem', background: '#0e1016', color: '#cdd2e0', border: '1px solid #1c2030', borderRadius: 4, padding: '0.2rem 0.3rem' }} /></td>
                      <td style={{ padding: '0.3rem' }}><input value={String(item.unit_price)} onChange={e => updateLine(idx, 'unit_price', e.target.value)} style={{ width: '7rem', background: '#0e1016', color: '#cdd2e0', border: '1px solid #1c2030', borderRadius: 4, padding: '0.2rem 0.3rem' }} /></td>
                      <td style={{ padding: '0.3rem', color: '#fff' }}>{formatCurrency(ext)}</td>
                      <td style={{ padding: '0.3rem' }}><input value={item.notes ?? ''} onChange={e => updateLine(idx, 'notes', e.target.value)} style={{ width: '13rem', background: '#0e1016', color: '#cdd2e0', border: '1px solid #1c2030', borderRadius: 4, padding: '0.2rem 0.3rem' }} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: '0.65rem', color: '#fff', fontSize: '0.78rem' }}>
            Estimated monthly total: <strong>{formatCurrency(total)}</strong>
          </div>
        </div>
      )}

      {error && (
        <div data-testid="bom-error" style={{ ...panel, border: '1px solid rgba(232,65,90,0.4)', color: '#e8415a', background: 'rgba(232,65,90,0.08)', fontSize: '0.78rem' }}>
          {error}
        </div>
      )}
    </div>
  );
}
