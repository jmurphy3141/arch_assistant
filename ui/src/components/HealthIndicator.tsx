import React from 'react';
import { useHealth } from '../hooks/useHealth';

function fmt(d: Date | null): string {
  if (!d) return '—';
  return d.toLocaleTimeString();
}

export function HealthIndicator() {
  const { data, error, lastChecked, loading } = useHealth();

  const dot = loading
    ? '⏳'
    : error
    ? '🔴'
    : data?.status === 'ok'
    ? '🟢'
    : '🟡';

  return (
    <div
      data-testid="health-indicator"
      style={{
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: '0.2rem 0.55rem',
        minWidth: 0,
        fontSize: '0.72rem',
        lineHeight: 1.35,
        color: '#8892a4',
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      <span>{dot}</span>{' '}
      {loading && <span>checking…</span>}
      {!loading && error && <span style={{ color: '#e8415a' }}>health error: {error}</span>}
      {!loading && data && (
        <span>
          <strong style={{ color: '#cdd2e0' }}>{data.agent_version}</strong>
          <span style={{ color: '#454d64' }}> · {data.status}</span>
        </span>
      )}
      {lastChecked && (
        <span style={{ color: '#454d64' }}>
          checked {fmt(lastChecked)}
        </span>
      )}
    </div>
  );
}
