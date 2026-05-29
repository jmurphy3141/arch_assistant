import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  apiGetCustomerContext,
  type EngagementContextResponse,
} from '../api/client';

interface EngagementMemoryPanelProps {
  customerId: string | null;
  refreshTrigger?: number;
}

interface MemoryView {
  customerName: string;
  challenge: string;
  services: string[];
  artifacts: string[];
}

function truncate(value: string, max = 80): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(item => String(item).trim()).filter(Boolean);
  }
  if (typeof value === 'string' && value.trim()) return [value.trim()];
  return [];
}

function artifactKeys(value: unknown): string[] {
  if (Array.isArray(value)) return stringList(value);
  if (!value || typeof value !== 'object') return [];
  return Object.values(value as Record<string, unknown>)
    .flatMap(item => {
      if (typeof item === 'string') return [item];
      if (!item || typeof item !== 'object') return [];
      const record = item as Record<string, unknown>;
      return stringList(record.key ?? record.latest_key ?? record.diagram_key ?? record.xlsx_artifact_key);
    })
    .filter(Boolean);
}

function agentArtifactKeys(context: EngagementContextResponse['context']): string[] {
  const agents = context?.agents;
  if (!agents || typeof agents !== 'object') return [];
  return Object.values(agents as Record<string, Record<string, unknown>>).flatMap(agent =>
    artifactKeys({
      key: agent.key,
      latest_key: agent.latest_key,
      diagram_key: agent.diagram_key,
      xlsx_artifact_key: agent.xlsx_artifact_key,
      artifact_ref: agent.artifact_ref,
    }),
  );
}

function extractArchieField(context: EngagementContextResponse['context'], key: string): unknown {
  const archie = context?.archie as Record<string, unknown> | undefined;
  if (!archie) return undefined;
  const clientFacts = archie.client_facts as Record<string, unknown> | undefined;
  const memory = archie.memory as Record<string, unknown> | undefined;
  const memoryFacts = memory?.client_facts as Record<string, unknown> | undefined;
  return archie[key] ?? clientFacts?.[key] ?? memoryFacts?.[key];
}

export function normalizeEngagementMemory(data: EngagementContextResponse | null): MemoryView {
  const context = data?.context;
  const challenge =
    String(data?.customer_challenge ?? context?.customer_challenge ?? extractArchieField(context, 'customer_challenge') ?? '').trim();
  const services =
    stringList(data?.oci_services_in_scope ?? context?.oci_services_in_scope ?? extractArchieField(context, 'oci_services_in_scope'));
  const artifacts = Array.from(new Set([
    ...artifactKeys(data?.artifacts),
    ...artifactKeys(context?.artifacts),
    ...agentArtifactKeys(context),
  ]));
  return {
    customerName: String(data?.customer_name ?? context?.customer_name ?? data?.customer_id ?? context?.customer_id ?? '').trim(),
    challenge,
    services,
    artifacts,
  };
}

function fileLabel(key: string): string {
  const parts = key.split('/').filter(Boolean);
  return parts[parts.length - 1] || key;
}

export function EngagementMemoryPanel({ customerId, refreshTrigger = 0 }: EngagementMemoryPanelProps) {
  const [data, setData] = useState<EngagementContextResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [error, setError] = useState('');

  const loadContext = useCallback(async () => {
    if (!customerId?.trim()) {
      setData(null);
      setLoadedOnce(false);
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      setData(await apiGetCustomerContext(customerId.trim()));
      setLoadedOnce(true);
    } catch (err) {
      const e = err as { detail?: string };
      setError(e.detail ?? 'Context unavailable');
      setLoadedOnce(true);
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => {
    void loadContext();
  }, [loadContext, refreshTrigger]);

  useEffect(() => {
    if (!customerId?.trim()) return undefined;
    const timer = window.setInterval(() => {
      void loadContext();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [customerId, loadContext]);

  const view = useMemo(() => normalizeEngagementMemory(data), [data]);
  const empty = !view.customerName && !view.challenge && view.services.length === 0 && view.artifacts.length === 0;

  return (
    <aside
      data-testid="engagement-memory-panel"
      style={{
        border: '1px solid #202638',
        borderRadius: 8,
        background: '#0d0f17',
        color: '#cdd2e0',
        padding: '0.85rem 1rem',
        boxSizing: 'border-box' as const,
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      <div style={{ fontSize: '0.62rem', color: '#5a6278', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: '0.55rem' }}>
        Engagement context
      </div>

      {!customerId?.trim() && (
        <div style={{ color: '#4a5168', fontSize: '0.76rem' }}>No customer selected</div>
      )}

      {customerId?.trim() && loading && !loadedOnce && (
        <div data-testid="memory-loading" style={{ display: 'grid', gap: '0.4rem' }}>
          <div style={{ height: 12, background: '#1a1f2e', borderRadius: 3 }} />
          <div style={{ height: 10, background: '#161b28', borderRadius: 3 }} />
          <div style={{ height: 10, width: '65%', background: '#161b28', borderRadius: 3 }} />
        </div>
      )}

      {customerId?.trim() && loadedOnce && error && (
        <div style={{ color: '#e8415a', fontSize: '0.74rem' }}>{error}</div>
      )}

      {customerId?.trim() && loadedOnce && !error && empty && (
        <div style={{ color: '#4a5168', fontSize: '0.76rem' }}>No context yet — start a conversation</div>
      )}

      {customerId?.trim() && loadedOnce && !error && !empty && (
        <div style={{ display: 'grid', gap: '0.75rem' }}>
          {view.customerName && (
            <div style={{ color: '#f4f7ff', fontSize: '0.86rem', fontWeight: 700 }}>
              {view.customerName}
            </div>
          )}
          {view.challenge && (
            <section>
              <div style={{ color: '#7d879a', fontSize: '0.66rem', marginBottom: '0.2rem' }}>Challenge</div>
              <div data-testid="memory-challenge" style={{ color: '#cdd2e0', fontSize: '0.75rem', lineHeight: 1.5 }}>
                {truncate(view.challenge)}
              </div>
            </section>
          )}
          {view.services.length > 0 && (
            <section>
              <div style={{ color: '#7d879a', fontSize: '0.66rem', marginBottom: '0.2rem' }}>Services in scope</div>
              <ul style={{ margin: 0, paddingLeft: '1rem', color: '#a9c2ff', fontSize: '0.73rem', lineHeight: 1.65 }}>
                {view.services.map(service => <li key={service}>{service}</li>)}
              </ul>
            </section>
          )}
          {view.artifacts.length > 0 && (
            <section>
              <div style={{ color: '#7d879a', fontSize: '0.66rem', marginBottom: '0.2rem' }}>Artifacts</div>
              <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: '0.25rem' }}>
                {view.artifacts.map(key => (
                  <li key={key}>
                    <a
                      href={`/download?key=${encodeURIComponent(key)}`}
                      target="_blank"
                      rel="noreferrer"
                      data-testid="memory-artifact-link"
                      style={{ color: '#8fb4ff', fontSize: '0.72rem', textDecoration: 'none', wordBreak: 'break-all' }}
                    >
                      {fileLabel(key)} ↗
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </aside>
  );
}
