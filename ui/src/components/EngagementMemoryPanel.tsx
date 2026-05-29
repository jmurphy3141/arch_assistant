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

interface MemoryView {
  customerName: string;
  challenge: string;
  services: string[];
}

function truncate(value: string, max = 120): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(item => String(item).trim()).filter(Boolean);
  if (typeof value === 'string' && value.trim()) return [value.trim()];
  return [];
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
  const challenge = String(
    data?.customer_challenge ?? context?.customer_challenge ?? extractArchieField(context, 'customer_challenge') ?? ''
  ).trim();
  const services = stringList(
    data?.oci_services_in_scope ?? context?.oci_services_in_scope ?? extractArchieField(context, 'oci_services_in_scope')
  );
  return {
    customerName: String(data?.customer_name ?? context?.customer_name ?? data?.customer_id ?? context?.customer_id ?? '').trim(),
    challenge,
    services,
  };
}

function hatDisplayName(hat: string): string {
  return hat.replace(/^oci_/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

export function EngagementMemoryPanel({ customerId, refreshTrigger = 0, activity }: EngagementMemoryPanelProps) {
  const [data, setData] = useState<EngagementContextResponse | null>(null);
  const [loadedOnce, setLoadedOnce] = useState(false);

  const loadContext = useCallback(async () => {
    if (!customerId?.trim()) { setData(null); setLoadedOnce(false); return; }
    try {
      setData(await apiGetCustomerContext(customerId.trim()));
      setLoadedOnce(true);
    } catch {
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
  const hasContent = !!(view.customerName || view.challenge || view.services.length > 0);
  const isLive = !!(activity?.thinkingStatus || activity?.activeHats?.length);

  if (!isLive && !hasContent && !customerId?.trim()) return null;

  return (
    <aside
      data-testid="engagement-memory-panel"
      style={{
        border: '1px solid #1a2035',
        borderRadius: 6,
        background: '#0b0d14',
        color: '#cdd2e0',
        padding: '0.6rem 0.75rem',
        boxSizing: 'border-box' as const,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: '0.73rem',
      }}
    >
      {/* Live activity */}
      {isLive && (
        <div
          data-testid="live-activity"
          style={{
            marginBottom: hasContent ? '0.55rem' : 0,
            padding: '0.45rem 0.6rem',
            background: '#07111f',
            border: '1px solid #1a2d45',
            borderRadius: 5,
            display: 'grid',
            gap: '0.3rem',
          }}
        >
          <div style={{ fontSize: '0.58rem', color: '#3d6ab0', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            Live
          </div>
          {activity!.activeHats!.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
              {activity!.activeHats!.map(hat => (
                <span key={hat} data-testid="live-active-hat" style={{
                  background: 'rgba(143,180,255,0.1)',
                  border: '1px solid rgba(143,180,255,0.25)',
                  borderRadius: 3,
                  padding: '0.05rem 0.35rem',
                  fontSize: '0.65rem',
                  color: '#8fb4ff',
                }}>
                  {hatDisplayName(hat)}
                </span>
              ))}
            </div>
          )}
          {activity!.thinkingStatus && (
            <div data-testid="live-thinking-status" style={{
              fontSize: '0.7rem',
              color: activity!.thinkingStatus.startsWith('Running') ? '#61dafb' : '#6b7a94',
              fontWeight: activity!.thinkingStatus.startsWith('Running') ? 600 : 400,
            }}>
              {activity!.thinkingStatus}
            </div>
          )}
        </div>
      )}

      {/* Context — only shown once loaded and has content */}
      {loadedOnce && hasContent && (
        <div style={{ display: 'grid', gap: '0.45rem' }}>
          {view.customerName && (
            <div style={{ color: '#e8edf8', fontWeight: 700, fontSize: '0.78rem' }}>
              {view.customerName}
            </div>
          )}
          {view.challenge && (
            <div data-testid="memory-challenge" style={{ color: '#8b97b0', lineHeight: 1.45 }}>
              {truncate(view.challenge)}
            </div>
          )}
          {view.services.length > 0 && (
            <div>
              <div style={{ color: '#4a5570', fontSize: '0.62rem', marginBottom: '0.2rem' }}>in scope</div>
              <div style={{ color: '#a9c2ff', lineHeight: 1.6 }}>
                {view.services.join(' · ')}
              </div>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
