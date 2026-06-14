import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  apiGetCustomerContext,
  type EngagementContextResponse,
} from '../api/client';

interface ActivityState {
  thinkingStatus: string | null;
  activeHats: string[];
}

interface EngagementMemoryPanelProps {
  customerId: string | null;
  refreshTrigger?: number;
  activity?: ActivityState;
}

interface ArtifactLink {
  key: string;
  label: string;
}

interface MemoryView {
  customerName: string;
  challenge: string;
  services: string[];
  artifacts: ArtifactLink[];
}

function truncate(value: string, max = 80): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function lastPathSegment(key: string): string {
  const parts = key.split('/').filter(Boolean);
  return parts[parts.length - 1] || key;
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(item => String(item).trim()).filter(Boolean);
  if (typeof value === 'string' && value.trim()) return [value.trim()];
  return [];
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function extractArchieField(context: Record<string, unknown> | undefined, key: string): unknown {
  const archie = asRecord(context?.archie);
  if (!archie) return undefined;
  const clientFacts = asRecord(archie.client_facts);
  const memory = asRecord(archie.memory);
  const memoryFacts = asRecord(memory?.client_facts);
  return archie[key] ?? clientFacts?.[key] ?? memoryFacts?.[key];
}

function extractServices(data: EngagementContextResponse | null): string[] {
  const context = asRecord(data?.context);
  const direct = stringList(data?.oci_services_in_scope ?? context?.oci_services_in_scope ?? extractArchieField(context, 'oci_services_in_scope'));
  if (direct.length > 0) return direct;

  const archie = asRecord(context?.archie);
  const memory = asRecord(archie?.memory);
  const architecture = asRecord(memory?.architecture_state);
  return stringList(architecture?.components);
}

function pushArtifact(artifacts: ArtifactLink[], seen: Set<string>, value: unknown) {
  const key = typeof value === 'string' ? value.trim() : '';
  if (!key || seen.has(key)) return;
  seen.add(key);
  artifacts.push({ key, label: lastPathSegment(key) });
}

function extractArtifacts(data: EngagementContextResponse | null): ArtifactLink[] {
  const artifacts: ArtifactLink[] = [];
  const seen = new Set<string>();
  const context = asRecord(data?.context);
  const candidates = data?.artifacts ?? context?.artifacts;

  if (Array.isArray(candidates)) {
    for (const item of candidates) {
      if (typeof item === 'string') {
        pushArtifact(artifacts, seen, item);
      } else {
        const record = asRecord(item);
        pushArtifact(artifacts, seen, record?.key ?? record?.artifact_key ?? record?.url);
      }
    }
  } else {
    const record = asRecord(candidates);
    if (record) {
      for (const value of Object.values(record)) pushArtifact(artifacts, seen, value);
    }
  }

  const agents = asRecord(context?.agents);
  if (agents) {
    for (const agentState of Object.values(agents)) {
      const state = asRecord(agentState);
      if (!state) continue;
      for (const [key, value] of Object.entries(state)) {
        if (key === 'key' || key.endsWith('_key') || key.endsWith('Key')) {
          pushArtifact(artifacts, seen, value);
        }
      }
    }
  }

  const archie = asRecord(context?.archie);
  const memory = asRecord(archie?.memory);
  const workProducts = asRecord(memory?.work_products);
  if (workProducts) {
    for (const product of Object.values(workProducts)) {
      const state = asRecord(product);
      if (!state) continue;
      for (const [key, value] of Object.entries(state)) {
        if (key === 'key' || key.endsWith('_key') || key.endsWith('Key')) {
          pushArtifact(artifacts, seen, value);
        }
      }
    }
  }

  return artifacts;
}

export function normalizeEngagementMemory(data: EngagementContextResponse | null): MemoryView {
  const context = asRecord(data?.context);
  const challenge = String(
    data?.customer_challenge
    ?? context?.customer_challenge
    ?? extractArchieField(context, 'customer_challenge')
    ?? extractArchieField(context, 'engagement_summary')
    ?? extractArchieField(context, 'latest_notes_summary')
    ?? ''
  ).trim();

  return {
    customerName: String(data?.customer_name ?? context?.customer_name ?? data?.customer_id ?? context?.customer_id ?? '').trim(),
    challenge,
    services: extractServices(data),
    artifacts: extractArtifacts(data),
  };
}

function hatDisplayName(hat: string): string {
  return hat.replace(/^oci_/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

export function EngagementMemoryPanel({ customerId, refreshTrigger = 0, activity }: EngagementMemoryPanelProps) {
  const [data, setData] = useState<EngagementContextResponse | null>(null);
  const [loadedOnce, setLoadedOnce] = useState(false);

  const loadContext = useCallback(async () => {
    if (!customerId?.trim()) {
      setData(null);
      setLoadedOnce(false);
      return;
    }
    try {
      setData(await apiGetCustomerContext(customerId.trim()));
    } catch {
      setData(null);
    } finally {
      setLoadedOnce(true);
    }
  }, [customerId]);

  useEffect(() => { void loadContext(); }, [loadContext, refreshTrigger]);

  useEffect(() => {
    if (!customerId?.trim()) return undefined;
    const timer = window.setInterval(() => { void loadContext(); }, 10000);
    return () => window.clearInterval(timer);
  }, [customerId, loadContext]);

  const view = useMemo(() => normalizeEngagementMemory(data), [data]);
  const context = asRecord(data?.context);
  const hasExplicitCustomerName = !!String(data?.customer_name ?? context?.customer_name ?? '').trim();
  const hasContent = !!(hasExplicitCustomerName || view.challenge || view.services.length > 0 || view.artifacts.length > 0);
  const isLive = !!(activity?.thinkingStatus || activity?.activeHats?.length);

  if (!customerId?.trim()) return null;

  return (
    <aside
      data-testid="engagement-memory-panel"
      style={{
        width: '100%',
        minHeight: 220,
        border: '1px solid rgba(0, 229, 255, 0.18)',
        borderRadius: 8,
        background: '#07090f',
        color: '#a8c4d8',
        padding: '0.85rem',
        boxSizing: 'border-box' as const,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: '0.74rem',
        boxShadow: '0 0 12px rgba(0, 229, 255, 0.06)',
      }}
    >
      <div style={{ borderBottom: '1px solid rgba(0, 229, 255, 0.12)', paddingBottom: '0.55rem', marginBottom: '0.7rem' }}>
        <div style={{ fontSize: '0.6rem', letterSpacing: '0.14em', color: 'rgba(0, 229, 255, 0.5)', textTransform: 'uppercase', marginBottom: '0.3rem' }}>
          Engagement Memory
        </div>
        <div data-testid="memory-customer-name" style={{ color: '#00e5ff', fontWeight: 700, fontSize: '0.88rem', lineHeight: 1.3, textShadow: '0 0 8px rgba(0,229,255,0.4)' }}>
          {view.customerName || customerId}
        </div>
      </div>

      {isLive && (
        <div
          data-testid="live-activity"
          style={{
            marginBottom: '0.7rem',
            padding: '0.45rem 0.55rem',
            background: 'rgba(0, 229, 255, 0.05)',
            border: '1px solid rgba(0, 229, 255, 0.25)',
            borderRadius: 6,
            display: 'grid',
            gap: '0.35rem',
            boxShadow: '0 0 8px rgba(0, 229, 255, 0.1)',
          }}
        >
          {activity!.activeHats!.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
              {activity!.activeHats!.map(hat => (
                <span key={hat} data-testid="live-active-hat" className="hat-glow-in" style={{
                  background: 'rgba(0, 229, 255, 0.08)',
                  border: '1px solid rgba(0, 229, 255, 0.3)',
                  borderRadius: 4,
                  padding: '0.05rem 0.4rem',
                  fontSize: '0.64rem',
                  color: '#00e5ff',
                  fontWeight: 700,
                  display: 'inline-block',
                }}>
                  {hatDisplayName(hat)}
                </span>
              ))}
            </div>
          )}
          {activity!.thinkingStatus && (
            <div data-testid="live-thinking-status" style={{ fontSize: '0.68rem', color: 'rgba(0, 229, 255, 0.7)' }}>
              {activity!.thinkingStatus}
            </div>
          )}
        </div>
      )}

      {!loadedOnce && (
        <div data-testid="memory-loading" style={{ display: 'grid', gap: '0.45rem' }}>
          <div style={{ height: 12, width: '75%', background: 'rgba(0, 229, 255, 0.06)', borderRadius: 4 }} />
          <div style={{ height: 12, width: '55%', background: 'rgba(0, 229, 255, 0.06)', borderRadius: 4 }} />
          <div style={{ height: 12, width: '68%', background: 'rgba(0, 229, 255, 0.06)', borderRadius: 4 }} />
        </div>
      )}

      {loadedOnce && !hasContent && (
        <div data-testid="memory-empty" style={{ color: 'rgba(0, 229, 255, 0.35)', lineHeight: 1.45 }}>
          No context yet — start a conversation
        </div>
      )}

      {loadedOnce && hasContent && (
        <div style={{ display: 'grid', gap: '0.75rem' }}>
          {view.challenge && (
            <section>
              <div style={{ color: 'rgba(0, 229, 255, 0.5)', fontSize: '0.6rem', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '0.24rem', fontWeight: 700 }}>
                Challenge
              </div>
              <div data-testid="memory-challenge" style={{ lineHeight: 1.45, color: '#c8d8e8' }}>
                {truncate(view.challenge)}
              </div>
            </section>
          )}

          {view.services.length > 0 && (
            <section>
              <div style={{ color: 'rgba(0, 229, 255, 0.5)', fontSize: '0.6rem', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '0.24rem', fontWeight: 700 }}>
                Services in scope
              </div>
              <ul data-testid="memory-services" style={{ margin: 0, paddingLeft: '1rem', lineHeight: 1.55, color: '#c8d8e8' }}>
                {view.services.map(service => <li key={service}>{service}</li>)}
              </ul>
            </section>
          )}

          {view.artifacts.length > 0 && (
            <section>
              <div style={{ color: 'rgba(0, 229, 255, 0.5)', fontSize: '0.6rem', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '0.24rem', fontWeight: 700 }}>
                Artifacts
              </div>
              <ul data-testid="memory-artifacts" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: '0.3rem' }}>
                {view.artifacts.map(artifact => (
                  <li key={artifact.key}>
                    <a
                      href={`/download?key=${encodeURIComponent(artifact.key)}`}
                      target="_blank"
                      rel="noreferrer"
                      style={{ color: '#00e5ff', textDecoration: 'none', overflowWrap: 'anywhere', textShadow: '0 0 6px rgba(0,229,255,0.3)' }}
                    >
                      {artifact.label} ↗
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
