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
      style={{ fontSize: '0.85rem', color: '#555', marginBottom: '0.5rem' }}
    >
      <span>{dot}</span>{' '}
      {loading && <span>Checking health…</span>}
      {!loading && error && <span>Health error: {error}</span>}
      {!loading && data && (
        <span>
          Agent: <strong>{data.agent_version}</strong> — {data.status}
        </span>
      )}
      {lastChecked && (
        <span style={{ marginLeft: '0.75rem', color: '#999' }}>
          (last checked: {fmt(lastChecked)})
        </span>
      )}
    </div>
  );
}
